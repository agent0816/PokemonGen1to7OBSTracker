import asyncio
import hashlib
import os
import pickle
import time
from pathlib import Path
from pickle import UnpicklingError
import traceback
from backend.bag_decoder import BagItem
from backend.classes.obs import OBS
from backend.classes.pokedex_db import PokedexDB
from backend.logging_setup import get_logger

# Mindestabstand zwischen automatischen Box-Refreshs pro Spieler.
# Verhindert Box-Read-Stürme z.B. bei Team-Reorder-Spam oder Evolutionen.
BOX_REFRESH_THROTTLE_SECONDS = 5.0

# Wipe-Detection HP-Consistency: erst nach N consecutive Zero-Reads pro Pokemon
# gilt es als tot. Gefährdungen: RAM-Fluktuationen, Battle-RAM-Ausreißer in
# Gen 6/7 wo Kampfstatistiken temporär an anderer Adresse liegen können.
WIPE_ZERO_READ_THRESHOLD = 5

class Munchlax:
    def __init__(self, host, port, rem, sp, pl, configsave=None, nuz=None):
        self.client_id = rem.get("client_id", 0)
        if self.client_id == 0:
            self.client_id = self.generate_hashed_id()
            rem["client_id"] = self.client_id
        self.bizhawk_teams = {}
        self.sorted_teams = {}
        self.unsorted_teams = {}
        self.badges = {}
        self.editions = {}
        self.player_names: dict[int, str] = {}
        self.remote_connection_status: dict[str, str] = {}
        self.remote_connection_names: dict[str, str] = {}
        # PC-Boxen pro Spieler: dict[player_id, list[list[Pokemon|None]]].
        # Auto-Refresh läuft, wenn sich das Team eines lokal bedienten
        # Spielers ändert — gedrosselt via BOX_REFRESH_THROTTLE_SECONDS,
        # sonst weiter on-demand vom BoxMenu.
        self.boxes: dict[int, list] = {}
        # Letzter erfolgreicher (oder gestarteter) Auto-Refresh-Zeitpunkt
        # pro Spieler. Wird vor dem Box-Read gesetzt — fehlgeschlagene Reads
        # sperren den Trigger ebenfalls für die Throttle-Dauer, das ist hier
        # bewusst, um Spam bei dauerhaftem Fehler zu verhindern.
        self.last_box_refresh_at: dict[int, float] = {}
        self.initialized = False
        self.rem = rem
        self.sp = sp
        self.pl = pl
        self.configsave = configsave
        self.nuz = nuz or {}
        self.pokedex_db: PokedexDB | None = None
        self.host = host
        self.port = port
        self.is_connected = False
        self.obs: OBS | None = None
        self.overlay_server = None
        self.rando_tm_moves: dict[int, str] | None = None
        self.rando_hm_moves: dict[int, str] | None = None
        self.rando_abilities_gen3: dict[int, list[int]] | None = None
        self.writer_lock = asyncio.Lock()
        self.disconnect_lock = asyncio.Lock()
        self._last_bag_hash: dict[str, str] = {}
        # Soullink-State, gespiegelt vom Server. Persistenz auf DB kommt in
        # späteren Tasks (Frontend/Persistierung); hier reine In-Memory-Ablage.
        self.soullink_config: dict = {}
        self.soullink_links: dict[int, dict] = {}
        self.soullink_deaths: dict[tuple[int, str], dict] = {}
        self.soullink_versus_state: dict[str, dict] = {}
        self.soullink_versus_battles: list[dict] = []
        self.soullink_rule_violations: dict[tuple[str, str], dict] = {}
        # Token-State pro Owner (Server-authoritativ, Cache).
        # {owner: {earned, used, active_route, active_edition}}
        self.soullink_tokens: dict[str, dict] = {}
        self._wipe_signaled: dict = {}
        self.on_total_wipe_callback = None
        # HP-Consistency: pro (player_id, personality) → {last_max_hp, last_lvl, zero_reads}.
        # Filtert kurze RAM-Fluktuationen und Gen-6/7-Battle-RAM-Ausreißer, bevor ein
        # Pokemon als "tot" gilt.
        self._pokemon_hp_state: dict[tuple, dict] = {}
        # Race-Timer, gespiegelt vom Server. `timer_state` = kompletter Snapshot,
        # `timer_last_tick` = (elapsed, server_ts, local_ts_at_receive) für lokale
        # Drift-Korrektur zwischen Ticks (Client rechnet elapsed lokal weiter).
        self.timer_state: dict = {}
        self.timer_last_tick: dict | None = None
        # Countdown (z.B. YouTube-Aufnahme). `countdown_finished_callback` wird
        # vom Frontend gesetzt und beim Ablauf einmal aufgerufen (Ton/Popup).
        self.countdown_state: dict = {}
        self.countdown_last_tick: dict | None = None
        self.countdown_finished_callback = None

        self.logger = get_logger(__name__, './logs/munchlax.log')

    def clear_everything(self):
        self.bizhawk_teams = {}
        self.sorted_teams = {}
        self.unsorted_teams = {}
        self.badges = {}
        self.editions = {}
        self.boxes = {}
        self.player_names.clear()
        self.remote_connection_status.clear()
        self.remote_connection_names.clear()
        self.initialized = False
        self.soullink_config = {}
        self.soullink_links = {}
        self.soullink_deaths = {}
        self.soullink_versus_state = {}
        self.soullink_versus_battles = []
        self.soullink_rule_violations = {}
        self.soullink_tokens = {}
        self.timer_state = {}
        self.timer_last_tick = None
        self.countdown_state = {}
        self.countdown_last_tick = None

    def should_auto_refresh_boxes(self, player_id: int) -> bool:
        """True wenn die Throttle-Drosselung einen automatischen Refresh erlaubt."""
        last = self.last_box_refresh_at.get(player_id, 0.0)
        return (time.time() - last) >= BOX_REFRESH_THROTTLE_SECONDS

    def mark_box_refresh(self, player_id: int):
        """Throttle-Zeitstempel setzen (vor dem eigentlichen Box-Read aufrufen)."""
        self.last_box_refresh_at[player_id] = time.time()

    async def update_boxes(self, player_id: int, boxes: list):
        """Lokal cachen + (falls verbunden) an Arceus pushen.

        Arceus verteilt das Update an alle anderen Munchlaxes, sodass
        BoxMenüs auf Remote-Clients automatisch frische Daten bekommen.
        """
        await self._enrich_box_levels(boxes)
        self.boxes[player_id] = boxes
        if not self.is_connected:
            return
        try:
            async with self.writer_lock:
                await self.send_message({
                    "type": "boxes_update",
                    "player_id": player_id,
                    "boxes": boxes,
                })
        except Exception as err:
            self.logger.warning(f"boxes_update an Arceus senden failed: {type(err)},{err}")
            self.logger.warning(f"{traceback.format_exc()}")

    async def _enrich_box_levels(self, boxes: list):
        """Ersetzt berechnete Box-Pokemon-Level durch DB-Werte (per PV).

        Box-Pokemon haben ab Gen 3 kein gespeichertes Level — der pokedecoder
        nutzt deshalb eine Medium-Fast-Approximation, die bei Fast/Slow/
        Erratic/Fluctuating-Spezies bis zu mehrere Level daneben liegen kann.
        Wenn dasselbe Pokemon (per PV) schon mal im Team war, kennen wir das
        echte Level aus pokemon.db und nehmen es bevorzugt.

        Gen 1/2 wird automatisch übersprungen: dort kommen Box-Pokemon ohne
        Personality-Attribut, das Memory-Level steht direkt im Slot.
        """
        self._ensure_pokedex_db()
        if self.pokedex_db is None or self.pokedex_db.connection is None:
            return
        slots_by_pv: dict[int, list] = {}
        for box in boxes:
            for slot in box:
                if slot is None:
                    continue
                pv = getattr(slot, "personality", None)
                if pv is None:
                    continue
                slots_by_pv.setdefault(int(pv), []).append(slot)
        if not slots_by_pv:
            return
        try:
            loop = asyncio.get_event_loop()
            levels = await loop.run_in_executor(
                None,
                self.pokedex_db.get_lvls_by_personalities,
                list(slots_by_pv.keys()),
            )
            for pv, lvl in levels.items():
                for slot in slots_by_pv.get(pv, []):
                    slot.lvl = lvl
        except Exception as err:
            self.logger.warning(f"Box-Level-Enrichment failed: {type(err)},{err}")
            self.logger.warning(f"{traceback.format_exc()}")

    async def alter_teams(self):
        while True:
            try:
                data = await self.receive_message()
                # Getypte Server-Push-Nachrichten: separater Pfad, damit die
                # Teams-Logik darunter nicht versehentlich ein {"type": ...}-
                # Dict als Player-Map behandelt.
                if isinstance(data, dict) and "type" in data:
                    msg_type = data.get("type")
                    if msg_type == "boxes_update":
                        player_id = data.get("player_id")
                        boxes = data.get("boxes")
                        if player_id is not None and boxes is not None:
                            self.boxes[player_id] = boxes
                            self.logger.info(
                                f"boxes_update empfangen: player={player_id}, "
                                f"box_count={len(boxes)}"
                            )
                    elif msg_type == "player_names":
                        self.player_names = data.get("names", {})
                        self.logger.info(f"player_names empfangen: {self.player_names}")
                    elif msg_type == "connection_status":
                        self.remote_connection_status.clear()
                        self.remote_connection_status.update(data.get("status", {}))
                        self.remote_connection_names.clear()
                        self.remote_connection_names.update(data.get("names", {}))
                    elif msg_type == "encounter_sync":
                        self._handle_remote_encounters(data.get("encounters", []))
                    elif msg_type == "encounter_outcome":
                        self._handle_remote_outcome(data)
                    elif msg_type == "bag_sync":
                        self._handle_remote_bag(data)
                    elif msg_type == "soullink_config":
                        self.soullink_config = data.get("config", {}) or {}
                        self.logger.info(
                            f"soullink_config empfangen: mode={self.soullink_config.get('mode')}, "
                            f"players={self.soullink_config.get('expected_owners')}"
                        )
                        await self._notify_overlay_session("soullink_config", self.soullink_config)
                    elif msg_type == "soullink_link_state":
                        link = data.get("link") or {}
                        if link.get("link_id") is not None:
                            self.soullink_links[link["link_id"]] = link
                            self.logger.info(
                                f"soullink_link_state empfangen: link={link.get('link_id')} "
                                f"state={link.get('state')} members={list(link.get('members', {}).keys())}"
                            )
                            await self._notify_overlay_session("soullink_link_state", link)
                    elif msg_type == "soullink_death":
                        death = data.get("death") or {}
                        self._handle_remote_death(death)
                        await self._notify_overlay_session("soullink_death", death)
                    elif msg_type == "soullink_versus_state":
                        self.soullink_versus_state = data.get("state", {}) or {}
                        self.soullink_versus_battles = data.get("battles", []) or []
                        self.logger.info(
                            f"soullink_versus_state empfangen: teams={list(self.soullink_versus_state.keys())}"
                        )
                        await self._notify_overlay_session("soullink_versus_state", {
                            "state": self.soullink_versus_state,
                            "battles": self.soullink_versus_battles,
                        })
                    elif msg_type == "soullink_versus_battle":
                        battle = data.get("battle") or {}
                        self.soullink_versus_battles.append(battle)
                        self.logger.info(
                            f"soullink_versus_battle empfangen: {battle.get('winner')} vs {battle.get('loser')}"
                        )
                        await self._notify_overlay_session("soullink_versus_battle", battle)
                    elif msg_type == "soullink_rule_violation":
                        violation = data.get("violation") or {}
                        key = (violation.get("type", "unknown"), str(violation.get("subject", "")))
                        self.soullink_rule_violations[key] = violation
                        self.logger.warning(
                            f"soullink_rule_violation empfangen: type={violation.get('type')} "
                            f"subject={violation.get('subject')} msg={violation.get('message')}"
                        )
                        await self._notify_overlay_session("soullink_rule_violation", violation)
                        # Frontend-Trigger für total_wipe
                        if violation.get("type") == "total_wipe" and callable(getattr(self, "on_total_wipe_callback", None)):
                            try:
                                self.on_total_wipe_callback(violation)
                            except Exception as err:
                                self.logger.warning(f"on_total_wipe_callback: {err}")
                    elif msg_type == "soullink_tokens":
                        self.soullink_tokens = data.get("tokens", {}) or {}
                        self.logger.info(
                            f"soullink_tokens empfangen: {[(o, s.get('earned'), s.get('used'), s.get('active_route')) for o, s in self.soullink_tokens.items()]}"
                        )
                        await self._notify_overlay_session("soullink_tokens", self.soullink_tokens)
                    elif msg_type == "timer_state":
                        self._handle_timer_state(data.get("timer") or {})
                        await self._notify_overlay_session("timer_state", self.timer_state)
                        await self._push_timer_text_to_obs()
                    elif msg_type == "timer_tick":
                        self._handle_timer_tick(data)
                        await self._notify_overlay_session("timer_tick", {
                            "elapsed": data.get("elapsed", 0.0),
                            "running": data.get("running", False),
                            "server_ts": data.get("server_ts"),
                        })
                        await self._push_timer_text_to_obs()
                    elif msg_type == "countdown_state":
                        self._handle_countdown_state(data.get("countdown") or {})
                        await self._notify_overlay_session("countdown_state", self.countdown_state)
                    elif msg_type == "countdown_tick":
                        self._handle_countdown_tick(data)
                        await self._notify_overlay_session("countdown_tick", {
                            "remaining_seconds": data.get("remaining_seconds", 0.0),
                            "running": data.get("running", False),
                            "server_ts": data.get("server_ts"),
                        })
                    elif msg_type == "countdown_finished":
                        self._handle_countdown_finished(data)
                        await self._notify_overlay_session("countdown_finished", {
                            "label": data.get("label", ""),
                            "duration_seconds": data.get("duration_seconds", 0.0),
                        })
                    else:
                        self.logger.warning(f"Unbekannter Message-Typ vom Server: {msg_type}")
                    continue
                self.unsorted_teams = data
                new_teams = self.unsorted_teams.copy()
                self.logging_teams(self.unsorted_teams, "unsorted teams received")
                for player in new_teams:
                    team = new_teams[player]
                    new_teams[player] = self.sort(team[:6], self.sp['order'])
                    if player not in self.editions or self.unsorted_teams[player][7] != self.editions[player]:
                        self.editions[player] = self.unsorted_teams[player][7]
                    if player not in self.badges or self.unsorted_teams[player][6] != self.badges[player]:
                        self.badges[player] = self.unsorted_teams[player][6]
                        self.logger.info(f"{self.badges[player]=}")
                        if self.obs and self.obs.is_connected:
                            await self.obs.change_badges(player)
                        if self.overlay_server and self.overlay_server.is_connected:
                            await self.overlay_server.notify_update(player, "badges")
                await self._persist_teams(self.unsorted_teams)
                if new_teams != self.sorted_teams or not self.initialized:
                    for player in new_teams:
                        if player not in self.sorted_teams or not self.initialized:
                            if self.obs and self.obs.is_connected:
                                await self.obs.changeSource(player, range(6), new_teams[player], self.editions[player])
                                self.initialized = True
                            self.sorted_teams[player] = new_teams[player]
                            if self.overlay_server and self.overlay_server.is_connected:
                                await self.overlay_server.notify_update(player, "team")
                            continue

                        diff = []
                        team = new_teams[player]
                        old_team = self.sorted_teams[player]
                        for i in range(6):
                            if not team[i].obs_property_changed(old_team[i], self.sp):
                                self.logger.debug(f"{i=},{team[i]=}")
                                diff.append(i)
                        slot_mapping = self.compute_slot_mapping(old_team, team)
                        if self.obs and self.obs.is_connected:
                            await self.obs.changeSource(player, diff, team, self.editions[player], slot_mapping=slot_mapping)
                        self.sorted_teams[player] = team
                        if self.overlay_server and self.overlay_server.is_connected:
                            await self.overlay_server.notify_update(player, "team", slot_mapping=slot_mapping)
            except (UnicodeEncodeError, UnicodeDecodeError) as err:
                self.logger.warning(f"Unicode error:{type(err)},{err}")
                self.logger.warning(f"{traceback.format_exc()}")
            except (UnpicklingError, AttributeError) as err:
                self.logger.warning(f"Pickle Data error:{type(err)},{err}")
                self.logger.warning(f"{traceback.format_exc()}")
            except EOFError as err:
                self.logger.warning(f"{traceback.format_exc()}")
            except Exception as err:
                self.logger.error(f"alter_teams abgebrochen: {type(err)},{err}")
                self.logger.error(f"{traceback.format_exc()}")
                break

        await self.disconnect(intentional=False)

    def _ensure_pokedex_db(self):
        if self.configsave is None:
            return
        try:
            current_path = Path(str(self.configsave)).resolve()
            if self.pokedex_db is None or self.pokedex_db.session_path.resolve() != current_path:
                if self.pokedex_db is not None:
                    self.pokedex_db.close()
                self.pokedex_db = PokedexDB(current_path)
                self.pokedex_db.connect()
        except Exception as err:
            self.logger.error(f"_ensure_pokedex_db failed: {type(err)},{err}")
            self.logger.error(f"{traceback.format_exc()}")

    async def update_bag(self, player, edition, pockets):
        """Persistiert die ausgelesenen Bag-Pockets pro Player in die PokedexDB.

        ``pockets`` ist ein Dict {pocket_key: list[BagItem]}. Pro Pocket
        ueberschreibt die DB den bisherigen Bestand fuer (owner, edition, pocket)
        komplett — verlorene Items verschwinden also auch wieder. Parallel
        pflegt upsert_bag_pocket die bag_first_seen-Tabelle (Nuzlocke-Marker),
        die NIE ueberschrieben wird.

        Anders als _persist_teams wird hier nicht aus self.unsorted_teams gelesen,
        sondern direkt vom Bizhawk-Reader uebergeben — der Bag laeuft auf einem
        eigenen Polling-Intervall und ist von der Team-Tick-Logik entkoppelt.
        """
        try:
            self._ensure_pokedex_db()
            if self.pokedex_db is None or self.pokedex_db.connection is None:
                return
            owner = str(player)
            loop = asyncio.get_event_loop()
            for pocket_key, items in pockets.items():
                await loop.run_in_executor(
                    None,
                    self.pokedex_db.upsert_bag_pocket,
                    owner,
                    edition,
                    pocket_key,
                    items,
                )
            bag_key = f"{owner}_{edition}"
            current_hash = self._bag_hash(pockets)
            if current_hash != self._last_bag_hash.get(bag_key):
                self._last_bag_hash[bag_key] = current_hash
                await self.send_bag_sync(owner, edition, pockets)
        except Exception as err:
            self.logger.error(f"update_bag failed: {type(err)},{err}")
            self.logger.error(f"{traceback.format_exc()}")

    async def send_encounter_sync(self, encounter_dict: dict):
        """Sendet einen Encounter-Record an Arceus zur Weiterverteilung."""
        if not self.is_connected:
            return
        try:
            async with self.writer_lock:
                await self.send_message({
                    "type": "encounter_sync",
                    "encounters": [encounter_dict],
                })
        except Exception as err:
            self.logger.warning(f"send_encounter_sync failed: {err}")

    async def send_encounter_outcome(self, personality: int, owner: str, outcome: str):
        """Sendet ein Outcome-Update an Arceus zur Weiterverteilung."""
        if not self.is_connected:
            return
        try:
            async with self.writer_lock:
                await self.send_message({
                    "type": "encounter_outcome",
                    "personality": personality,
                    "owner": owner,
                    "outcome": outcome,
                })
        except Exception as err:
            self.logger.warning(f"send_encounter_outcome failed: {err}")

    async def send_soullink_death(self, personality: int, owner: str,
                                    edition=None, cause: str = "faint"):
        """Meldet dem Server, dass ein Pokemon gestorben ist."""
        if not self.is_connected:
            return
        try:
            async with self.writer_lock:
                await self.send_message({
                    "type": "soullink_death",
                    "personality": personality,
                    "owner": owner,
                    "edition": edition,
                    "cause": cause,
                })
        except Exception as err:
            self.logger.warning(f"send_soullink_death failed: {err}")

    async def send_soullink_config(self, config: dict):
        """Setzt/aktualisiert die Soullink-Config auf dem Server."""
        if not self.is_connected:
            return
        try:
            async with self.writer_lock:
                await self.send_message({
                    "type": "soullink_config",
                    "config": config,
                })
        except Exception as err:
            self.logger.warning(f"send_soullink_config failed: {err}")

    async def send_soullink_versus_battle(self, winner: str, loser: str, label: str = ""):
        """Meldet manuelles PvP-Battle-Result zwischen zwei Teams."""
        if not self.is_connected:
            return
        try:
            async with self.writer_lock:
                await self.send_message({
                    "type": "soullink_versus_battle",
                    "winner": winner,
                    "loser": loser,
                    "label": label,
                })
        except Exception as err:
            self.logger.warning(f"send_soullink_versus_battle failed: {err}")

    async def send_soullink_token_earned(self, owner: str):
        await self._send_timer_message({
            "type": "soullink_token_earned",
            "owner": owner,
        })

    async def send_soullink_token_redeem(self, owner: str, edition, route: int):
        await self._send_timer_message({
            "type": "soullink_token_redeem",
            "owner": owner,
            "edition": edition,
            "route": int(route),
        })

    async def _send_timer_message(self, msg: dict):
        if not self.is_connected:
            return
        try:
            async with self.writer_lock:
                await self.send_message(msg)
        except Exception as err:
            self.logger.warning(f"send timer msg failed: {err}")

    async def send_timer_start(self):
        await self._send_timer_message({"type": "timer_start"})

    async def send_timer_reset(self):
        await self._send_timer_message({"type": "timer_reset"})

    async def send_timer_split(self, label: str, source: str = "manual"):
        await self._send_timer_message({"type": "timer_split", "label": label, "source": source})

    async def send_timer_pause_request(self):
        await self._send_timer_message({"type": "timer_pause_request"})

    async def send_timer_resume_request(self):
        await self._send_timer_message({"type": "timer_resume_request"})

    def _handle_timer_state(self, timer: dict):
        self.timer_state = timer
        self.timer_last_tick = {
            "elapsed": timer.get("elapsed", 0.0),
            "server_ts": timer.get("server_ts", time.time()),
            "local_ts": time.time(),
            "running": timer.get("running", False),
        }
        self.logger.info(
            f"timer_state empfangen: running={timer.get('running')} "
            f"elapsed={timer.get('elapsed'):.1f}s splits={len(timer.get('splits', []))} "
            f"pause_consent={len(timer.get('pause_consent', []))}/{timer.get('consent_required')}"
        )

    def _handle_timer_tick(self, data: dict):
        self.timer_last_tick = {
            "elapsed": data.get("elapsed", 0.0),
            "server_ts": data.get("server_ts", time.time()),
            "local_ts": time.time(),
            "running": data.get("running", False),
        }

    def current_timer_elapsed(self) -> float:
        """Lokal gerechneter Timer-Wert. Verwendet letzten Tick als Anker."""
        if not self.timer_last_tick:
            return 0.0
        base = self.timer_last_tick.get("elapsed", 0.0)
        if not self.timer_last_tick.get("running"):
            return base
        return base + (time.time() - self.timer_last_tick.get("local_ts", time.time()))

    async def send_countdown_start(self, duration_seconds: float, label: str = ""):
        await self._send_timer_message({
            "type": "countdown_start",
            "duration_seconds": float(duration_seconds),
            "label": label,
        })

    async def send_countdown_pause(self):
        await self._send_timer_message({"type": "countdown_pause"})

    async def send_countdown_resume(self):
        await self._send_timer_message({"type": "countdown_resume"})

    async def send_countdown_cancel(self):
        await self._send_timer_message({"type": "countdown_cancel"})

    def _handle_countdown_state(self, cd: dict):
        self.countdown_state = cd
        self.countdown_last_tick = {
            "remaining": cd.get("remaining_seconds", 0.0),
            "server_ts": cd.get("server_ts", time.time()),
            "local_ts": time.time(),
            "running": cd.get("running", False),
        }
        self.logger.info(
            f"countdown_state empfangen: running={cd.get('running')} "
            f"remaining={cd.get('remaining_seconds'):.1f}s label='{cd.get('label')}'"
        )

    def _handle_countdown_tick(self, data: dict):
        self.countdown_last_tick = {
            "remaining": data.get("remaining_seconds", 0.0),
            "server_ts": data.get("server_ts", time.time()),
            "local_ts": time.time(),
            "running": data.get("running", False),
        }

    def _handle_countdown_finished(self, data: dict):
        label = data.get("label", "")
        duration = data.get("duration_seconds", 0.0)
        self.logger.info(f"countdown_finished empfangen: label='{label}' dauer={duration}s")
        if self.countdown_finished_callback is not None:
            try:
                self.countdown_finished_callback(label, duration)
            except Exception as err:
                self.logger.error(f"countdown_finished_callback failed: {type(err)},{err}")

    async def _notify_overlay_session(self, event_type: str, payload):
        srv = self.overlay_server
        if srv is None or not srv.is_connected:
            return
        try:
            await srv.notify_session_event(event_type, payload)
        except Exception as err:
            self.logger.debug(f"notify_overlay_session {event_type} failed: {err}")

    async def check_total_wipe(self, player_id, team) -> bool:
        """Prüft rule_restart_on_total_wipe: alle Team-Slots tot → Broadcast Warning.

        Debounce: nach einem erkannten Wipe wird `_wipe_signaled[player_id]=True`
        gesetzt und erst wieder gelöscht sobald ein Pokemon wieder lebt. So
        entsteht keine Broadcast-Schleife bei jedem Team-Tick.

        HP-Consistency (`_is_pokemon_dead`): filtert kurze RAM-Fluktuationen
        (Threshold: WIPE_ZERO_READ_THRESHOLD consecutive Zero-Reads) und
        Gen-6/7-Battle-RAM-Ausreißer (max_hp-Wechsel ohne plausibles Level-Up).
        """
        if not self.nuz.get("rule_restart_on_total_wipe", False):
            return False
        alive = 0
        any_pokemon = False
        for slot in team[:6] if team else []:
            if slot is None:
                continue
            if isinstance(slot, dict):
                dex = slot.get("dexnr")
            else:
                dex = getattr(slot, "dexnr", None)
            if not dex:
                continue
            any_pokemon = True
            if not self._is_pokemon_dead(player_id, slot):
                alive += 1
        if not any_pokemon:
            # Team leer (z.B. vor ROM-Load) — kein Wipe, aber auch nicht als Wipe zaehlen.
            return False
        if alive > 0:
            # Debounce zuruecksetzen sobald Team wieder lebt.
            if getattr(self, "_wipe_signaled", None) is None:
                self._wipe_signaled = {}
            self._wipe_signaled.pop(player_id, None)
            return False
        # alive_count = 0 UND any_pokemon vorhanden → Wipe
        if getattr(self, "_wipe_signaled", None) is None:
            self._wipe_signaled = {}
        if self._wipe_signaled.get(player_id):
            return True  # bereits gemeldet
        self._wipe_signaled[player_id] = True
        self.logger.warning(f"Total Wipe erkannt für player_id={player_id}")
        if self.is_connected:
            try:
                async with self.writer_lock:
                    await self.send_message({
                        "type": "soullink_rule_violation",
                        "violation": {
                            "type": "total_wipe",
                            "subject": str(player_id),
                            "player_id": player_id,
                            "message": "Alle Pokemon gefallen — Run-Neustart erforderlich",
                            "timestamp": time.time(),
                        },
                    })
            except Exception as err:
                self.logger.warning(f"total_wipe send failed: {err}")
        return True

    def archive_active_run(self) -> str | None:
        """Archiviert die aktive encounters-Tabelle unter encounters_archived_<ts>.

        Wird von Frontend-Wipe-Banner "Bei Null starten" gerufen. Legt eine
        Kopie an und leert die aktive Tabelle.
        """
        self._ensure_pokedex_db()
        if self.pokedex_db is None or self.pokedex_db.connection is None:
            return None
        try:
            from datetime import datetime, timezone
            ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            archive_name = f"encounters_archived_{ts}"
            conn = self.pokedex_db.connection
            conn.execute(f"CREATE TABLE IF NOT EXISTS {archive_name} AS SELECT * FROM encounters")
            conn.execute("DELETE FROM encounters")
            conn.commit()
            self.logger.info(f"Aktive encounters archiviert als {archive_name}")
            # Debounce zurücksetzen
            if getattr(self, "_wipe_signaled", None) is not None:
                self._wipe_signaled.clear()
            return archive_name
        except Exception as err:
            self.logger.error(f"archive_active_run failed: {type(err)},{err}")
            self.logger.error(f"{traceback.format_exc()}")
            return None

    def _is_pokemon_dead(self, player_id, slot) -> bool:
        """Consistency-Check für HP=0.

        Ein Pokemon gilt erst als tot, wenn:
        1. cur_hp konsistent 0 ist über WIPE_ZERO_READ_THRESHOLD aufeinanderfolgende Reads
        2. Der Read plausibel ist: max_hp identisch zum letzten Read ODER Level-Diff in {0,1}
           (letzteres deckt in-battle Level-Up ab).

        max_hp-Wechsel mit unplausiblem Level-Diff = wahrscheinlich Battle-RAM-Read
        an falscher Adresse (Gen 6/7): Read verwerfen, Debounce-Zähler nicht erhöhen.
        """
        if isinstance(slot, dict):
            pv = slot.get("personality")
            cur_hp = slot.get("cur_hp")
            max_hp = slot.get("max_hp")
            lvl = slot.get("lvl")
        else:
            pv = getattr(slot, "personality", None)
            cur_hp = getattr(slot, "cur_hp", None)
            max_hp = getattr(slot, "max_hp", None)
            lvl = getattr(slot, "lvl", None)

        if pv is None:
            # Ohne PID kein Consistency-Tracking möglich — direkter Check.
            return cur_hp is not None and cur_hp == 0

        key = (player_id, pv)
        state = self._pokemon_hp_state.get(key)
        if state is None:
            state = {"last_max_hp": max_hp, "last_lvl": lvl, "zero_reads": 0}
            self._pokemon_hp_state[key] = state
            # Beim allerersten Read kein Zero-Zaehler-Increment (keine Baseline für
            # Consistency vorhanden). Erst ab dem zweiten Read greift der Filter.
            return False

        # Consistency: max_hp-Wechsel ohne plausibles Level-Up → Read verwerfen.
        if (state["last_max_hp"] is not None and max_hp is not None
                and max_hp != state["last_max_hp"]):
            last_lvl = state["last_lvl"]
            plausible = False
            if last_lvl is not None and lvl is not None:
                lvl_diff = lvl - last_lvl
                if lvl_diff in (0, 1):
                    plausible = True
            if not plausible:
                self.logger.debug(
                    f"HP-Read verworfen (Battle-RAM?): player={player_id} pv={pv} "
                    f"max_hp {state['last_max_hp']}->{max_hp} lvl {last_lvl}->{lvl}"
                )
                # Zero-Reads NICHT hochzählen, state NICHT aktualisieren
                return state["zero_reads"] >= WIPE_ZERO_READ_THRESHOLD

        # Read akzeptieren + State updaten
        state["last_max_hp"] = max_hp
        state["last_lvl"] = lvl

        if cur_hp is None:
            state["zero_reads"] = 0
            return False
        if cur_hp == 0:
            state["zero_reads"] += 1
            if state["zero_reads"] >= WIPE_ZERO_READ_THRESHOLD:
                return True
            return False
        state["zero_reads"] = 0
        return False

    def check_nickname_required(self, pokemon, edition, default_species_name) -> bool:
        """rule_nickname_required: prüft ob Nickname vom Default-Species-Namen abweicht."""
        if not self.nuz.get("rule_nickname_required", True):
            return True
        nickname = getattr(pokemon, "nickname", "") or ""
        default = default_species_name or ""
        # Wenn Nickname leer oder gleich Species-Default → Verstoss (return False)
        return bool(nickname and nickname.strip() and nickname.strip().upper() != default.strip().upper())

    async def _push_timer_text_to_obs(self):
        """Setzt eine OBS-Text-Source 'RaceTimer' auf den aktuellen Timer-Wert.

        Optional: Nur aktiv wenn OBS-WebSocket verbunden UND eine Input mit
        exakt diesem Namen existiert. Bei Fehler: still ignorieren, damit
        andere Overlay-Wege (Browser-Source) unabhängig weiterlaufen.
        """
        if self.obs is None or not self.obs.is_connected:
            return
        elapsed = self.current_timer_elapsed()
        s = int(max(0, elapsed))
        text = f"{s // 3600:02d}:{(s // 60) % 60:02d}:{s % 60:02d}"
        try:
            import simpleobsws
            await self.obs.ws.call(simpleobsws.Request(
                "SetInputSettings",
                {
                    "inputName": "RaceTimer",
                    "inputSettings": {"text": text},
                    "overlay": True,
                },
            ))
        except Exception as err:
            # OBS-Text-Source ist optional — kein Grund für Log-Spam.
            self.logger.debug(f"OBS RaceTimer-Text set failed: {err}")

    def current_countdown_remaining(self) -> float:
        """Lokal gerechneter Rest-Countdown. Anker = letzter Server-Tick."""
        if not self.countdown_last_tick:
            return 0.0
        base = float(self.countdown_last_tick.get("remaining", 0.0))
        if not self.countdown_last_tick.get("running"):
            return base
        return max(0.0, base - (time.time() - self.countdown_last_tick.get("local_ts", time.time())))

    def _handle_remote_death(self, death: dict):
        pv = death.get("personality")
        owner = death.get("owner")
        if pv is None or not owner:
            return
        self.soullink_deaths[(pv, owner)] = death
        partners = death.get("partners", [])
        # Ist dieser Client Owner eines Partners? Dann lokale UI/DB-Reaktion nötig.
        my_partner = None
        for partner in partners:
            for pid, edition in self.editions.items():
                partner_owner = str(pid)
                if partner_owner == partner.get("owner"):
                    my_partner = partner
                    break
        self.logger.info(
            f"soullink_death empfangen: owner={owner} pv={pv} "
            f"link={death.get('link_id')} own_partner={my_partner}"
        )
        # Falls Server auch Link-Group als failed markiert hat, kommt separater
        # soullink_link_state-Broadcast; hier keine doppelte Verarbeitung.

    async def send_bag_sync(self, owner: str, edition, pockets: dict):
        """Sendet Bag-Inhalt an Arceus. BagItem → (id, qty) Tupel."""
        if not self.is_connected:
            return
        try:
            wire_pockets = {}
            for pocket_key, items in pockets.items():
                wire_pockets[pocket_key] = [(it.id, it.qty) for it in items if it.id != 0 and it.qty != 0]
            async with self.writer_lock:
                await self.send_message({
                    "type": "bag_sync",
                    "owner": owner,
                    "edition": str(edition) if edition is not None else "",
                    "pockets": wire_pockets,
                })
        except Exception as err:
            self.logger.warning(f"send_bag_sync failed: {err}")

    def _handle_remote_encounters(self, encounters: list[dict]):
        self._ensure_pokedex_db()
        if self.pokedex_db is None or self.pokedex_db.connection is None:
            return
        count = 0
        for enc in encounters:
            if self.pokedex_db.sync_encounter(enc):
                count += 1
        if count:
            self.logger.info(f"Remote-Encounters empfangen: {count} von {len(encounters)} eingefügt/aktualisiert")

    def _handle_remote_outcome(self, data: dict):
        self._ensure_pokedex_db()
        if self.pokedex_db is None or self.pokedex_db.connection is None:
            return
        self.pokedex_db.update_encounter_outcome(
            data["personality"], data["owner"], data["outcome"]
        )

    def _handle_remote_bag(self, data: dict):
        self._ensure_pokedex_db()
        if self.pokedex_db is None or self.pokedex_db.connection is None:
            return
        owner = data.get("owner", "")
        edition = data.get("edition", "")
        pockets = data.get("pockets", {})
        for pocket_key, items_raw in pockets.items():
            bag_items = [BagItem(id=item_id, qty=qty) for item_id, qty in items_raw]
            self.pokedex_db.upsert_bag_pocket(owner, edition, pocket_key, bag_items)
        self.logger.info(
            f"Remote-Bag empfangen: owner={owner} edition={edition} "
            f"pockets={list(pockets.keys())}"
        )

    @staticmethod
    def _bag_hash(pockets: dict) -> str:
        parts = []
        for key in sorted(pockets.keys()):
            items = pockets[key]
            parts.append(f"{key}:" + ",".join(f"{it.id}:{it.qty}" for it in items))
        return hashlib.md5("|".join(parts).encode()).hexdigest()

    async def _persist_teams(self, teams):
        try:
            self._ensure_pokedex_db()
            if self.pokedex_db is None or self.pokedex_db.connection is None:
                return
            loop = asyncio.get_event_loop()
            for player, team_data in teams.items():
                pokemons = team_data[:6]
                edition = team_data[7] if len(team_data) > 7 else self.editions.get(player)
                owner = str(player)
                written, new_pvs = await loop.run_in_executor(
                    None,
                    self.pokedex_db.upsert_team,
                    owner,
                    edition,
                    pokemons,
                )
                if new_pvs:
                    self._on_new_pokemon_detected(player, edition, pokemons, new_pvs)
                await self.check_total_wipe(player, pokemons)
        except Exception as err:
            self.logger.error(f"_persist_teams failed: {type(err)},{err}")
            self.logger.error(f"{traceback.format_exc()}")

    def _on_new_pokemon_detected(self, player: int, edition, pokemons, new_pvs: list[int]):
        """Callback wenn neue Pokemon in der DB auftauchen. Wird von bizhawk
        überschrieben um Gift-Encounter-Erkennung zu triggern."""
        pass

    def logging_teams(self, teams: dict, dictname: str):
        self.logger.debug(dictname)
        for player, team in teams.items():
            self.logger.debug(f"logging_teams: player {player}: {[str(p) for p in team[:6]]}")
    
    def compute_slot_mapping(self, old_team: list, new_team: list) -> dict:
        old_keys = {}
        new_keys = {}
        for slot in range(min(6, len(old_team))):
            key = old_team[slot].identity_key
            if key is not None:
                old_keys[slot] = key
        for slot in range(min(6, len(new_team))):
            key = new_team[slot].identity_key
            if key is not None:
                new_keys[slot] = key

        new_key_to_slot = {key: slot for slot, key in new_keys.items()}
        old_key_set = set(old_keys.values())

        mapping = {}
        for old_slot, key in old_keys.items():
            mapping[old_slot] = new_key_to_slot.get(key)

        new_slots = [slot for slot, key in new_keys.items() if key not in old_key_set]
        removed_slots = [old_slot for old_slot, key in old_keys.items() if key not in new_keys.values()]

        mapping['new_slots'] = new_slots
        mapping['removed_slots'] = removed_slots
        return mapping

    def sort(self, liste, key):
        key = key.lower().replace('.', '')
        if key == 'dexnr':
            return sorted(sorted(liste), key=lambda a: a)
        if key == 'team':
            return liste
        if key == 'lvl':
            return sorted(sorted(liste), key=lambda a: - a.lvl if a.dexnr != 0 else 999999)
        if key == 'route':
            return sorted(sorted(liste), key=lambda a: a.route if a.dexnr != 0 else 999999)

    def change_order(self, *args):
        for team in self.sorted_teams:
            self.sorted_teams[team] = self.sort(self.unsorted_teams[team][:6], self.sp['order'])

    async def send_heartbeat(self):
        while True:
            try:
                self.logger.debug("Heartbeat gesendet")
                async with self.writer_lock:
                    await self.send_message('heartbeat')
                await asyncio.sleep(5)
            except Exception as err:
                self.logger.warning(f"Heartbeat failed: {type(err)},{err}")
                self.logger.error(f"{traceback.format_exc()}")
                break

        await self.disconnect(intentional=False)

    async def send_teams(self):
        while True:
            if self.sorted_teams == {}:
                self.sorted_teams = self.bizhawk_teams.copy()
                self.change_order()
            if self.bizhawk_teams != {}:
                try:
                    async with self.writer_lock:
                        await self.send_message(self.bizhawk_teams)
                except Exception as err:
                    self.logger.warning(f"Teams senden failed: {type(err)},{err}")
                    self.logger.error(f"{traceback.format_exc()}")
                    break
            await asyncio.sleep(1)

        await self.disconnect(intentional=False)
    
    async def connect(self):
        self.logger.info(f"Verbinde Munchlax zu ({self.host}, {self.port})")
        self.reader, self.writer = await asyncio.open_connection(self.host, self.port)
        self.logger.info(f"Munchlax {self.client_id} bei Arceus({self.host},{self.port}) registriert")
        self.logger.debug(f"Client-ID: {self.client_id}, Start-Server: {self.rem.get('start_server')}")
        
        name = self.pl.get('your_name', '')

        async with self.writer_lock:
            await self.send_message(f"{name}_{self.client_id}")
        self.is_connected = 'connected'

        self.heartbeat_task = asyncio.create_task(self.send_heartbeat())
        self.send_teams_task = asyncio.create_task(self.send_teams())
        self.alter_teams_task = asyncio.create_task(self.alter_teams())

    async def disconnect(self, intentional=True):
        self.initialized = False
        should_reconnect = False
        async with self.disconnect_lock:
            if self.is_connected:
                if intentional:
                    try:
                        async with self.writer_lock:
                            await self.send_message(f"disconnect {self.client_id}")
                    except Exception as err:
                        self.logger.warning(f"Disconnect Nachricht versenden failed: {err}")
                else:
                    should_reconnect = True

                self.is_connected = False
                self.remote_connection_status.clear()
                self.remote_connection_names.clear()

                self.alter_teams_task.cancel()
                self.heartbeat_task.cancel()
                self.send_teams_task.cancel()

                try:
                    self.writer.close()
                    await self.writer.wait_closed()
                except Exception:
                    pass
                self.logger.info(f"Client {self.client_id} hat sich disconnectet.")

                if self.pokedex_db is not None:
                    self.pokedex_db.close()
                    self.pokedex_db = None

                self.host = '127.0.0.1' if self.rem["start_server"] else self.rem["server_ip_adresse"]
                self.port = self.rem["client_port"] if self.rem["start_server"] else self.rem["server_port"]

        if should_reconnect:
            asyncio.create_task(self._auto_reconnect())

    async def _auto_reconnect(self):
        delays = [5, 10, 20]
        for attempt, delay in enumerate(delays, 1):
            self.logger.info(f"Auto-Reconnect Versuch {attempt}/{len(delays)} in {delay}s...")
            await asyncio.sleep(delay)
            if self.is_connected:
                return
            try:
                await self.connect()
                self.logger.info(f"Auto-Reconnect erfolgreich nach Versuch {attempt}.")
                return
            except Exception as err:
                self.logger.warning(f"Auto-Reconnect Versuch {attempt} fehlgeschlagen: {err}")
        self.logger.error("Auto-Reconnect aufgegeben nach 3 Versuchen.")

    async def send_message(self, message):
        serialized_message = pickle.dumps(message)
        CHUNK_SIZE = 500  # Die Größe jedes Chunks in Bytes

        msg_type = message.get("type", "teams") if isinstance(message, dict) else (message if isinstance(message, str) else type(message).__name__)
        self.logger.debug(f"Sende: type={msg_type}, {len(serialized_message)} Bytes")

        # Gesamtlänge der Nachricht senden
        length = len(serialized_message).to_bytes(4, 'big')
        self.writer.write(length)
        await self.writer.drain()

        # Nachricht in Chunks senden
        for i in range(0, len(serialized_message), CHUNK_SIZE):
            chunk = serialized_message[i:i+CHUNK_SIZE]
            # Größe des aktuellen Chunks senden
            chunk_length = len(chunk).to_bytes(4, 'big')
            self.writer.write(chunk_length)
            await self.writer.drain()
            # Chunk senden
            self.writer.write(chunk)
            await self.writer.drain()

    async def receive_message(self):
        reader = self.reader
        # readexactly statt read — siehe Begruendung in arceus.receive_message:
        # read(N) ist nicht-deterministisch bei fragmentierten TCP-Paketen,
        # was bei den ~210 KB Gen 6/7 Boxes-Updates zu UnpicklingError und
        # Stream-Desynchronisation fuehrt.
        total_length = int.from_bytes(await reader.readexactly(4), 'big')
        message = b''

        while len(message) < total_length:
            chunk_length = int.from_bytes(await reader.readexactly(4), 'big')
            chunk = await reader.readexactly(chunk_length)
            message += chunk

        result = pickle.loads(message)
        msg_desc = result.get("type", f"{len(result)} Spieler") if isinstance(result, dict) else type(result).__name__
        self.logger.debug(f"Empfangen: {msg_desc}, {total_length} Bytes")
        return result
    
    def generate_hashed_id(self):
        random_id = os.urandom(16)
        
        hashed = hashlib.sha256(random_id).hexdigest()

        client_id = hashed
        return client_id