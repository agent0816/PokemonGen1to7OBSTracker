import asyncio
import pickle
import time
import traceback
from backend.logging_setup import get_logger
from backend.team_id import slug_team_id
from backend.type_lookup import first_type, type_name

# Modi mit aktiver Soullink-Logik (Links, Ersttyp-Clause, Trade-Detection).
# Alles andere ("off" legacy, "nuzlocke", "disabled", "versus_ffa") bedeutet:
# keine Cross-Owner-Checks auf dem Server.
SOULLINK_MODES = ("coop", "versus")
# Modi mit Versus-Scoreboard (badges/alive/deaths). "versus" aggregiert pro Team,
# "versus_ffa" (jeder gegen jeden) pro Spieler — ohne Soullink-Links.
VERSUS_MODES = ("versus", "versus_ffa")
# Modi, deren Config beim Connect an neue Clients ausgeliefert wird.
BROADCAST_MODES = ("coop", "versus", "versus_ffa")

# Grace-Period für Owner im _team_owner_cache: nach Disconnect noch so lange
# als "known" führen, damit Heartbeat-Blips das Team-Overlay nicht kippen.
# Danach: Cache-Eintrag entfernen, Owner verschwindet aus dem Team-Bucket.
TEAM_OWNER_CACHE_GRACE_SECONDS = 300

class Arceus:
    def __init__(self, host, port, rem):
        super().__init__()
        self.host = host
        self.port = port
        self.munchlaxes = {}
        self.munchlax_names = {}
        self.munchlax_status = {}
        self.munchlax_heartbeats = {}
        self.heartbeat_counts = {}
        self.client_player_ids: dict[str, set[int]] = {}
        self.teams = {}
        # Box-Cache analog zu self.teams: dict[player_id, list[list[Pokemon|None]]].
        # Wird vom besitzenden Munchlax per "boxes_update" gefüllt und bei
        # neuen Verbindungen einmalig an den Client gepusht.
        self.boxes = {}
        # Encounter-Cache: (personality, owner) → enc_dict.
        # Wird bei encounter_sync gefüllt, bei encounter_outcome aktualisiert
        # und beim Connect eines neuen Clients einmalig ausgeliefert.
        self.encounters: dict[tuple[int, str], dict] = {}
        # Bag-Cache: (owner, edition) → pockets_dict.
        self.bags: dict[tuple[str, str], dict] = {}
        # Soullink-State (rein server-authoritativ, keine DB-Persistenz auf Server-Seite).
        # Config wird vom Host-Client per "soullink_config" gesetzt und beim Connect
        # neuer Clients einmalig ausgeliefert.
        # config: mode, player_count, link_strategy, team_membership, expected_owners
        self.soullink_config: dict = {
            "mode": "off",
            "player_count": 2,
            "link_strategy": "full_chain",
            "team_membership": {},
            "expected_owners": [],
        }
        # link_id → {link_id, route, edition_gen, link_index, state, members: {owner: {...}}}
        self.soullink_links: dict[int, dict] = {}
        self.soullink_next_id: int = 1
        # Death-Cache: (personality, owner) → death_dict, wird bei Reconnect gepusht.
        self.soullink_deaths: dict[tuple[int, str], dict] = {}
        # Rule-Violation-Cache: (rule_type, subject) → violation_dict, wird bei
        # Reconnect neuer Clients gepusht. Beispiele: type='trade', type='first_type_clash'.
        self.soullink_rule_violations: dict[tuple[str, str], dict] = {}
        # Token-State pro Owner: {owner: {earned: int, used: int, active_route: int|None,
        #                                 active_edition: int|None}}
        # `active_route` = Route für die als nächstes ein Extra-Encounter erlaubt ist.
        self.soullink_tokens: dict[str, dict] = {}
        # Versus-State: aggregierte Team-Stats (badges_min, deaths, alive_count).
        self.soullink_versus_state: dict[str, dict] = {}
        # Team-State fürs Overlay (modus-unabhängig). Basiert auf
        # _effective_team_membership() + je Owner Rohdaten (badges/edition/pids),
        # das Overlay aggregiert selbst (AND, Region-Gruppierung).
        self.soullink_team_state: dict[str, dict] = {}
        # Owner-Cache last-seen: {owner: {"badges", "edition", "last_seen"}}.
        # Puffert kurzzeitige Disconnects/Heartbeat-Blips, damit die AND-Aggregation
        # im Overlay nicht sofort auf 0 fällt und alle Regionen als leer anzeigt.
        # Wird nur mit echten Werten aktualisiert, nie mit None überschrieben.
        # Prune nach TEAM_OWNER_CACHE_GRACE_SECONDS ohne Sichtung (siehe
        # _prune_team_owner_cache), damit Session-Wechsel/dauerhafter Disconnect
        # keine Ghost-Owner im Team-State hinterlassen.
        self._team_owner_cache: dict[str, dict] = {}
        # History manueller PvP-Runden zwischen Teams.
        self.soullink_versus_battles: list[dict] = []
        # Race-Timer (server-authoritativ). Ticks werden im Background-Task
        # broadcastet, State-Änderungen (start/pause/resume/split) zusätzlich
        # bei jedem Handler.
        self.timer_state: dict = {
            "running": False,
            "start_ts": None,          # Unix-Wall-Time des Timer-Starts
            "pause_start_ts": None,    # aktueller Pause-Beginn oder None
            "pause_total_seconds": 0.0,# summierte Pausen bis jetzt
            "splits": [],              # list[{label, elapsed, ts, source}]
            "pause_consent": [],       # list[str] Client-IDs, die pause bestätigt haben
            "resume_consent": [],      # list[str] Client-IDs, die resume bestätigt haben
        }
        # Zuletzt gesehene Badge-Werte pro (client_id, player_id) für Auto-Splits.
        self._timer_last_badges: dict[tuple[str, int], int] = {}
        self.timer_task = None
        # Countdown-Timer (unabhängig vom Race-Timer). Für z.B. YouTube-Aufnahme:
        # Zeit setzen, Server broadcastet countdown_finished wenn abgelaufen,
        # Clients spielen dann einen Ton oder zeigen ein Popup.
        self.countdown_state: dict = {
            "running": False,
            "start_ts": None,             # Unix-Zeit des Countdown-Starts (aktueller Lauf)
            "duration_seconds": 0.0,
            "remaining_at_pause": 0.0,    # verbleibende Sekunden zum Pausen-Zeitpunkt
            "label": "",
            "finished": False,
        }
        # Guard, damit ein einmal ausgelöstes countdown_finished nicht in jedem
        # tick-Zyklus erneut broadcastet wird.
        self._countdown_finished_signaled = False
        # Pro Client ein Lock, damit gleichzeitige Sender (update_all_clients +
        # broadcast_boxes_update) sich nicht in den chunked-Stream funken.
        self.writer_locks = {}
        self.server = None
        self.is_connected = False
        self.disconnect_lock = asyncio.Lock()
        self.rem = rem

        self.logger = get_logger(__name__, './logs/arceus.log')
    
    async def handle_munchlax(self, reader, writer):

        raw = await self.receive_message(reader)
        client_name, client_id = raw.rsplit("_", 1)
        self.munchlaxes[client_id] = writer
        self.munchlax_names[client_id] = client_name
        self.munchlax_status[client_id] = 'connected'
        self.heartbeat_counts[client_id] = 0
        self.client_player_ids[client_id] = set()
        self.writer_locks[client_id] = asyncio.Lock()
        self.logger.info(f"Client {client_id} connected and registered.")

        asyncio.create_task(self.update_all_clients(client_id))
        asyncio.create_task(self.broadcast_connection_status())
        asyncio.create_task(self.broadcast_player_names())

        while True:
            try:
                data = await self.receive_message(reader)
                msg_desc = data.get("type") if isinstance(data, dict) else (data if isinstance(data, str) else f"{len(data)} Spieler")
                self.logger.debug(f"Empfangen von {client_id}: {msg_desc}")
                if type(data) == str and data.startswith("disconnect"): # or not data:
                    break
                if data == 'heartbeat':
                    self.munchlax_heartbeats[client_id] = time.time()
                elif isinstance(data, dict) and data.get("type") == "boxes_update":
                    player_id = data.get("player_id")
                    boxes = data.get("boxes")
                    if player_id is None or boxes is None:
                        self.logger.warning(f"boxes_update ohne player_id/boxes von {client_id}: {data!r}")
                    else:
                        self.boxes[player_id] = boxes
                        asyncio.create_task(self.broadcast_boxes_update(client_id, player_id, boxes))
                elif isinstance(data, dict) and data.get("type") == "encounter_sync":
                    encounters = data.get("encounters", [])
                    changed_links: list[dict] = []
                    trade_warnings: list[dict] = []
                    tokens_changed = False
                    for enc in encounters:
                        trade = self._detect_trade(enc)
                        if trade is not None:
                            trade_warnings.append(trade)
                        key = (enc["personality"], enc["owner"])
                        self.encounters[key] = enc
                        # Auto-Consume Token-Slot wenn dieser Encounter auf einer
                        # aktiven Token-Route passiert (Task #12).
                        if self._token_consume_if_matches(
                            enc.get("owner", ""), enc.get("edition"), enc.get("route")
                        ):
                            tokens_changed = True
                        link = self._assign_link_group(enc)
                        if link is not None:
                            changed_links.append(link)
                    asyncio.create_task(self.broadcast_encounter_sync(client_id, encounters))
                    for link in changed_links:
                        asyncio.create_task(self.broadcast_soullink_link_state(link))
                        clash = self._check_first_type_clash(link)
                        if link.pop("_compensation_broadcast_pending", False):
                            asyncio.create_task(self.broadcast_soullink_tokens())
                        if clash is not None:
                            asyncio.create_task(self.broadcast_soullink_rule_violation(clash))
                    for warning in trade_warnings:
                        asyncio.create_task(self.broadcast_soullink_rule_violation(warning))
                    if tokens_changed:
                        asyncio.create_task(self.broadcast_soullink_tokens())
                elif isinstance(data, dict) and data.get("type") == "encounter_outcome":
                    pv = data["personality"]
                    owner = data["owner"]
                    outcome = data["outcome"]
                    key = (pv, owner)
                    if key in self.encounters:
                        self.encounters[key]["outcome"] = outcome
                    link = self._update_link_member_outcome(pv, owner, outcome)
                    asyncio.create_task(self.broadcast_encounter_outcome(client_id, pv, owner, outcome))
                    if link is not None:
                        asyncio.create_task(self.broadcast_soullink_link_state(link))
                elif isinstance(data, dict) and data.get("type") == "soullink_config":
                    cfg = data.get("config", {})
                    self.soullink_config.update(cfg)
                    self.logger.info(
                        f"Soullink-Config aktualisiert: mode={self.soullink_config.get('mode')}, "
                        f"players={self.soullink_config.get('expected_owners')}"
                    )
                    asyncio.create_task(self.broadcast_soullink_config())
                    if self._recompute_team_state():
                        asyncio.create_task(self.broadcast_soullink_team_state())
                elif isinstance(data, dict) and data.get("type") == "soullink_death":
                    payload = self._process_death(data)
                    if payload is not None:
                        asyncio.create_task(self.broadcast_soullink_death(payload))
                    if self._recompute_versus_state():
                        asyncio.create_task(self.broadcast_soullink_versus_state())
                elif isinstance(data, dict) and data.get("type") == "soullink_versus_battle":
                    battle = {
                        "winner": data.get("winner"),
                        "loser": data.get("loser"),
                        "label": data.get("label", ""),
                        "timestamp": time.time(),
                    }
                    self.soullink_versus_battles.append(battle)
                    self.logger.info(
                        f"Versus-Battle-Result: {battle['winner']} vs {battle['loser']} "
                        f"({battle['label']})"
                    )
                    asyncio.create_task(self.broadcast_soullink_versus_battle(battle))
                elif isinstance(data, dict) and data.get("type") == "timer_start":
                    self._timer_start()
                    asyncio.create_task(self.broadcast_timer_state())
                elif isinstance(data, dict) and data.get("type") == "timer_reset":
                    self._timer_reset()
                    asyncio.create_task(self.broadcast_timer_state())
                elif isinstance(data, dict) and data.get("type") == "timer_split":
                    label = data.get("label", "split")
                    split = self._timer_split(label, source=data.get("source", "manual"))
                    if split is not None:
                        asyncio.create_task(self.broadcast_timer_state())
                elif isinstance(data, dict) and data.get("type") == "timer_pause_request":
                    if self._timer_add_consent("pause", client_id):
                        asyncio.create_task(self.broadcast_timer_state())
                elif isinstance(data, dict) and data.get("type") == "timer_resume_request":
                    if self._timer_add_consent("resume", client_id):
                        asyncio.create_task(self.broadcast_timer_state())
                elif isinstance(data, dict) and data.get("type") == "countdown_start":
                    duration = float(data.get("duration_seconds", 0.0) or 0.0)
                    label = data.get("label", "")
                    self._countdown_start(duration, label)
                    asyncio.create_task(self.broadcast_countdown_state())
                elif isinstance(data, dict) and data.get("type") == "countdown_pause":
                    if self._countdown_pause():
                        asyncio.create_task(self.broadcast_countdown_state())
                elif isinstance(data, dict) and data.get("type") == "countdown_resume":
                    if self._countdown_resume():
                        asyncio.create_task(self.broadcast_countdown_state())
                elif isinstance(data, dict) and data.get("type") == "countdown_cancel":
                    self._countdown_reset()
                    asyncio.create_task(self.broadcast_countdown_state())
                elif isinstance(data, dict) and data.get("type") == "soullink_rule_violation":
                    # Client-initiierter Violation-Broadcast (z.B. total_wipe).
                    payload = data.get("violation") or {}
                    if payload:
                        asyncio.create_task(self.broadcast_soullink_rule_violation(payload))
                elif isinstance(data, dict) and data.get("type") == "soullink_token_earned":
                    owner = data.get("owner") or ""
                    if self._token_earn(owner):
                        asyncio.create_task(self.broadcast_soullink_tokens())
                elif isinstance(data, dict) and data.get("type") == "soullink_token_redeem":
                    owner = data.get("owner") or ""
                    route = data.get("route")
                    edition = data.get("edition")
                    if self._token_redeem(owner, edition, route):
                        asyncio.create_task(self.broadcast_soullink_tokens())
                elif isinstance(data, dict) and data.get("type") == "bag_sync":
                    bag_owner = data.get("owner", "")
                    bag_edition = data.get("edition", "")
                    self.bags[(bag_owner, bag_edition)] = data.get("pockets", {})
                    asyncio.create_task(self.broadcast_bag_sync(client_id, data))
                else:
                    for player, team in data.items(): #type: ignore
                        if player not in self.teams or self.teams[player] != team:
                            self.teams[player] = team
                    new_player_ids = set(data.keys())
                    if new_player_ids != self.client_player_ids.get(client_id, set()):
                        self.client_player_ids[client_id] = new_player_ids
                        asyncio.create_task(self.broadcast_player_names())
                    if self._recompute_versus_state():
                        asyncio.create_task(self.broadcast_soullink_versus_state())
                    if self._recompute_team_state():
                        asyncio.create_task(self.broadcast_soullink_team_state())
                    auto_splits = self._detect_badge_splits(client_id, data)
                    if auto_splits:
                        for split in auto_splits:
                            self.logger.info(f"Auto-Split (Badge): {split['label']}")
                        asyncio.create_task(self.broadcast_timer_state())
            except ConnectionResetError:
                # Reader ist tot, weitere receive_message-Aufrufe wuerden nur
                # noch denselben Fehler in Busy-Loop werfen. Schleife verlassen,
                # damit der regulaere disconnect_client-Pfad greift.
                break
            except pickle.UnpicklingError as exc:
                self.logger.error(f"Fehler beim Entpacken der Daten: {type(exc)},{exc}")
                self.logger.error(f"{traceback.format_exc()}")
            except Exception as exc:
                self.logger.error(f"handle_munchlax abgebrochen:{type(exc)},{exc}")
                self.logger.error(f"{traceback.format_exc()}")
                break
        
        await self.disconnect_client(client_id)

    async def update_all_clients(self, client_id):
        old_teams = self.teams.copy()
        # Onboarding-Sequenz komplett in try/except gehuellt: Task laeuft ohne
        # Referenz + ohne add_done_callback (siehe start-Aufruf), eine Exception
        # in einem der send_to_client-Aufrufe wuerde den Task sonst lautlos
        # sterben lassen und der Client bekaeme unvollstaendiges Onboarding
        # (z.B. nie sein team_state) ohne Spur im Log. Sync-Review Runde 6.
        try:
            self.logger.debug(f"Initiales Team-Update an Client {client_id}: {len(self.teams)} Spieler")
            await self.send_to_client(client_id, self.teams)

            # Box-Stand einmal an den frisch verbundenen Client schicken, damit
            # remote betrachtete BoxMenüs sofort den letzten bekannten Cache haben.
            for player_id, boxes in list(self.boxes.items()):
                await self.send_to_client(client_id, {
                    "type": "boxes_update",
                    "player_id": player_id,
                    "boxes": boxes,
                })

            if self.encounters:
                await self.send_to_client(client_id, {
                    "type": "encounter_sync",
                    "encounters": list(self.encounters.values()),
                })

            if self.soullink_config.get("mode", "off") in BROADCAST_MODES:
                await self.send_to_client(client_id, {
                    "type": "soullink_config",
                    "config": dict(self.soullink_config),
                })
            for link in list(self.soullink_links.values()):
                await self.send_to_client(client_id, {
                    "type": "soullink_link_state",
                    "link": link,
                })
            for death in list(self.soullink_deaths.values()):
                await self.send_to_client(client_id, {
                    "type": "soullink_death",
                    "death": death,
                })
            for violation in list(self.soullink_rule_violations.values()):
                await self.send_to_client(client_id, {
                    "type": "soullink_rule_violation",
                    "violation": violation,
                })
            if self.soullink_tokens:
                await self.send_to_client(client_id, {
                    "type": "soullink_tokens",
                    "tokens": dict(self.soullink_tokens),
                })
            if self.soullink_versus_state:
                await self.send_to_client(client_id, {
                    "type": "soullink_versus_state",
                    "state": self.soullink_versus_state,
                    "battles": list(self.soullink_versus_battles),
                })
            # Team-State (modus-unabhängig, siehe _compute_team_state) — beim Connect
            # einmal ausliefern. Entweder-oder-Muster gegen Duplikat:
            # - Aenderung durch den Connect → Broadcast an alle (inkl. neuer Client)
            #   als create_task (fire-and-forget), damit ein langsamer/haengender
            #   Fremd-Client die restlichen Onboarding-Sends nicht blockiert.
            # - State stable → gezielter direct-send an neuen Client.
            if self._recompute_team_state():
                asyncio.create_task(self.broadcast_soullink_team_state())
            elif self.soullink_team_state:
                await self.send_to_client(client_id, {
                    "type": "soullink_team_state",
                    "state": self.soullink_team_state,
                })
            if self.timer_state.get("start_ts") is not None:
                await self.send_to_client(client_id, {
                    "type": "timer_state",
                    "timer": self._timer_snapshot(),
                })
            if self.countdown_state.get("duration_seconds", 0.0) > 0:
                await self.send_to_client(client_id, {
                    "type": "countdown_state",
                    "countdown": self._countdown_snapshot(),
                })

            for (bag_owner, bag_edition), pockets in list(self.bags.items()):
                await self.send_to_client(client_id, {
                    "type": "bag_sync",
                    "owner": bag_owner,
                    "edition": bag_edition,
                    "pockets": pockets,
                })
        except Exception as exc:
            self.logger.error(f"update_all_clients Onboarding an {client_id} abgebrochen: {type(exc)},{exc}")
            self.logger.error(f"{traceback.format_exc()}")
            return

        while True:
            try:
                if old_teams != self.teams:
                    old_teams = self.teams.copy()
                    await self.send_to_client(client_id, self.teams)
                await asyncio.sleep(1)
            except Exception as exc:
                self.logger.error(f"update_all_clients abgebrochen:{type(exc)},{exc}")
                self.logger.error(f"{traceback.format_exc()}")
                break

    async def send_to_client(self, client_id, message):
        """Sendet eine Nachricht an genau einen Client; serialisiert pro Writer."""
        writer = self.munchlaxes.get(client_id)
        lock = self.writer_locks.get(client_id)
        if writer is None or lock is None:
            return
        async with lock:
            await self.send_message(writer, message)

    async def broadcast_boxes_update(self, sender_id, player_id, boxes):
        """Verteilt einen Box-Update an alle Clients außer dem Absender."""
        message = {"type": "boxes_update", "player_id": player_id, "boxes": boxes}
        for client_id in list(self.munchlaxes.keys()):
            if client_id == sender_id:
                continue
            try:
                await self.send_to_client(client_id, message)
            except Exception as exc:
                self.logger.error(f"broadcast_boxes_update an {client_id} failed: {type(exc)},{exc}")
                self.logger.error(f"{traceback.format_exc()}")
    
    async def broadcast_encounter_sync(self, sender_id, encounters):
        message = {"type": "encounter_sync", "encounters": encounters}
        for client_id in list(self.munchlaxes.keys()):
            if client_id == sender_id:
                continue
            try:
                await self.send_to_client(client_id, message)
            except Exception as exc:
                self.logger.error(f"broadcast_encounter_sync an {client_id} failed: {type(exc)},{exc}")
                self.logger.error(f"{traceback.format_exc()}")

    async def broadcast_encounter_outcome(self, sender_id, personality, owner, outcome):
        message = {"type": "encounter_outcome", "personality": personality, "owner": owner, "outcome": outcome}
        for client_id in list(self.munchlaxes.keys()):
            if client_id == sender_id:
                continue
            try:
                await self.send_to_client(client_id, message)
            except Exception as exc:
                self.logger.error(f"broadcast_encounter_outcome an {client_id} failed: {type(exc)},{exc}")
                self.logger.error(f"{traceback.format_exc()}")

    @staticmethod
    def _edition_to_gen(edition) -> int:
        try:
            e = int(edition)
        except (TypeError, ValueError):
            return 0
        if 1 <= e <= 5:
            return 1
        if 11 <= e <= 15:
            return 2
        if 31 <= e <= 35:
            return 3
        if 41 <= e <= 45:
            return 4
        if 51 <= e <= 54:
            return 5
        if 61 <= e <= 64:
            return 6
        if 71 <= e <= 74:
            return 7
        return 0

    def _linked_owners_for(self, owner: str) -> list[str]:
        """Owner-Liste, mit denen dieser Owner verlinkt ist (unter Berücksichtigung von Mode/Team)."""
        cfg = self.soullink_config
        mode = cfg.get("mode", "off")
        if mode not in SOULLINK_MODES:
            return []
        expected = list(cfg.get("expected_owners", []))
        if owner not in expected:
            return []
        if mode == "versus":
            team_map = cfg.get("team_membership", {}) or {}
            my_team = team_map.get(owner)
            if my_team is None:
                return []
            return [o for o in expected if team_map.get(o) == my_team]
        # coop: full_chain = alle expected owners werden gelinkt.
        return list(expected)

    def _compute_link_state(self, link: dict) -> str:
        """Berechnet state (pending/complete/failed) aus Member-Outcomes."""
        members = link.get("members", {})
        outcomes = [m.get("outcome", "unknown") for m in members.values()]
        # Warten bis alle erwarteten Members drin sind
        if len(members) < len(link.get("expected_owners", [])):
            return "pending"
        if any(o == "unknown" for o in outcomes):
            return "pending"
        # min. einer nicht gefangen → failed (alle boxen)
        caught_like = {"caught", "obtained"}
        if all(o in caught_like for o in outcomes):
            return "complete"
        return "failed"

    def _find_or_create_link(self, route: int, edition_gen: int,
                              link_index: int, expected: list[str]) -> dict:
        for link in self.soullink_links.values():
            if (link["route"] == route and link["edition_gen"] == edition_gen
                    and link["link_index"] == link_index
                    and link["expected_owners"] == expected):
                return link
        link_id = self.soullink_next_id
        self.soullink_next_id += 1
        link = {
            "link_id": link_id,
            "route": route,
            "edition_gen": edition_gen,
            "link_index": link_index,
            "state": "pending",
            "expected_owners": expected,
            "members": {},
        }
        self.soullink_links[link_id] = link
        return link

    def _assign_link_group(self, enc: dict) -> dict | None:
        """Weist einem neuen Encounter eine Link-Group zu (falls Soullink aktiv + is_first)."""
        if self.soullink_config.get("mode", "off") not in SOULLINK_MODES:
            return None
        if not enc.get("is_first"):
            return None
        owner = enc.get("owner")
        if not owner:
            return None
        expected = self._linked_owners_for(owner)
        if len(expected) < 2:
            return None
        edition_gen = self._edition_to_gen(enc.get("edition"))
        if edition_gen == 0:
            return None
        route = enc.get("route", 0)
        # link_index = wie oft dieser Owner bereits auf (route, edition_gen) gelinkt wurde + 1
        prior = 0
        for link in self.soullink_links.values():
            if (link["route"] == route and link["edition_gen"] == edition_gen
                    and owner in link["members"]):
                prior += 1
        link_index = prior + 1
        link = self._find_or_create_link(route, edition_gen, link_index, expected)
        if owner in link["members"]:
            return None
        link["members"][owner] = {
            "personality": enc.get("personality"),
            "edition": enc.get("edition"),
            "outcome": enc.get("outcome", "unknown"),
            "dexnr": enc.get("dexnr"),
            "method": enc.get("method", "wild"),
        }
        link["state"] = self._compute_link_state(link)
        self.logger.info(
            f"Soullink-Group {link['link_id']} route={route} gen={edition_gen} "
            f"idx={link_index}: +{owner} ({len(link['members'])}/{len(expected)}) state={link['state']}"
        )
        return link

    def _update_link_member_outcome(self, personality, owner, outcome) -> dict | None:
        """Sucht Link-Group, in der (personality, owner) Mitglied ist, und aktualisiert outcome."""
        for link in self.soullink_links.values():
            member = link["members"].get(owner)
            if member is None:
                continue
            if member.get("personality") != personality:
                continue
            if member.get("outcome") == outcome:
                return None
            member["outcome"] = outcome
            new_state = self._compute_link_state(link)
            if new_state != link["state"]:
                link["state"] = new_state
                self.logger.info(
                    f"Soullink-Group {link['link_id']} state={new_state} "
                    f"(outcome-update {owner}={outcome})"
                )
            return link
        return None

    async def broadcast_soullink_config(self):
        message = {"type": "soullink_config", "config": dict(self.soullink_config)}
        for client_id in list(self.munchlaxes.keys()):
            try:
                await self.send_to_client(client_id, message)
            except Exception as exc:
                self.logger.error(f"broadcast_soullink_config an {client_id} failed: {exc}")

    async def broadcast_soullink_link_state(self, link: dict):
        message = {"type": "soullink_link_state", "link": link}
        for client_id in list(self.munchlaxes.keys()):
            try:
                await self.send_to_client(client_id, message)
            except Exception as exc:
                self.logger.error(f"broadcast_soullink_link_state an {client_id} failed: {exc}")

    def _process_death(self, data: dict) -> dict | None:
        """Verarbeitet eine Todesmeldung: markiert Link-Members als dead und sammelt Partner."""
        personality = data.get("personality")
        owner = data.get("owner")
        if personality is None or not owner:
            return None
        if self.soullink_config.get("mode", "off") not in SOULLINK_MODES:
            # Ohne Soullink trotzdem Broadcast (Overlay/UI-Update), aber keine Partner-Logik.
            death = {
                "personality": personality,
                "owner": owner,
                "edition": data.get("edition"),
                "cause": data.get("cause", "faint"),
                "timestamp": time.time(),
                "partners": [],
                "link_id": None,
            }
            self.soullink_deaths[(personality, owner)] = death
            return death
        target_link = None
        for link in self.soullink_links.values():
            member = link["members"].get(owner)
            if member and member.get("personality") == personality:
                target_link = link
                break
        partners: list[dict] = []
        link_id = None
        if target_link is not None:
            link_id = target_link["link_id"]
            for other_owner, member in target_link["members"].items():
                if other_owner == owner:
                    member["outcome"] = "dead"
                    continue
                partners.append({
                    "owner": other_owner,
                    "personality": member.get("personality"),
                    "edition": member.get("edition"),
                    "dexnr": member.get("dexnr"),
                })
            target_link["state"] = "failed"
        death = {
            "personality": personality,
            "owner": owner,
            "edition": data.get("edition"),
            "cause": data.get("cause", "faint"),
            "timestamp": time.time(),
            "partners": partners,
            "link_id": link_id,
        }
        self.soullink_deaths[(personality, owner)] = death
        self.logger.info(
            f"Soullink-Death: owner={owner} pv={personality} "
            f"link={link_id} partners={[p['owner'] for p in partners]}"
        )
        return death

    # ----- Timer -----

    def _timer_elapsed(self) -> float:
        ts = self.timer_state
        start = ts.get("start_ts")
        if start is None:
            return 0.0
        pause_total = ts.get("pause_total_seconds", 0.0)
        if ts.get("running"):
            return max(0.0, time.time() - start - pause_total)
        pause_start = ts.get("pause_start_ts")
        if pause_start is not None:
            return max(0.0, pause_start - start - pause_total)
        return max(0.0, time.time() - start - pause_total)

    def _timer_snapshot(self) -> dict:
        ts = self.timer_state
        return {
            "running": ts.get("running", False),
            "start_ts": ts.get("start_ts"),
            "pause_start_ts": ts.get("pause_start_ts"),
            "pause_total_seconds": ts.get("pause_total_seconds", 0.0),
            "elapsed": self._timer_elapsed(),
            "server_ts": time.time(),
            "splits": list(ts.get("splits", [])),
            "pause_consent": list(ts.get("pause_consent", [])),
            "resume_consent": list(ts.get("resume_consent", [])),
            "consent_required": len(self.munchlaxes),
        }

    def _timer_start(self):
        ts = self.timer_state
        if ts.get("start_ts") is None:
            ts["start_ts"] = time.time()
            ts["pause_total_seconds"] = 0.0
            ts["pause_start_ts"] = None
            ts["splits"] = []
        elif not ts.get("running") and ts.get("pause_start_ts") is not None:
            # Resume ohne Konsens (Force-Start): pause_total um vergangene Pause erhöhen
            ts["pause_total_seconds"] = ts.get("pause_total_seconds", 0.0) + (time.time() - ts["pause_start_ts"])
            ts["pause_start_ts"] = None
        ts["running"] = True
        ts["pause_consent"] = []
        ts["resume_consent"] = []
        self.logger.info(f"Timer gestartet/resumed: start_ts={ts['start_ts']}")

    def _timer_reset(self):
        self.timer_state = {
            "running": False,
            "start_ts": None,
            "pause_start_ts": None,
            "pause_total_seconds": 0.0,
            "splits": [],
            "pause_consent": [],
            "resume_consent": [],
        }
        self._timer_last_badges = {}
        self.logger.info("Timer resettet")

    def _timer_split(self, label: str, source: str = "manual") -> dict | None:
        ts = self.timer_state
        if ts.get("start_ts") is None:
            return None
        split = {
            "label": label,
            "elapsed": self._timer_elapsed(),
            "ts": time.time(),
            "source": source,
        }
        ts.setdefault("splits", []).append(split)
        return split

    def _timer_add_consent(self, kind: str, client_id: str) -> bool:
        ts = self.timer_state
        if ts.get("start_ts") is None:
            return False
        key = "pause_consent" if kind == "pause" else "resume_consent"
        other_key = "resume_consent" if kind == "pause" else "pause_consent"
        consent = ts.setdefault(key, [])
        if client_id not in consent:
            consent.append(client_id)
        ts[other_key] = []
        required = len(self.munchlaxes)
        if required > 0 and len(consent) >= required:
            if kind == "pause" and ts.get("running"):
                ts["running"] = False
                ts["pause_start_ts"] = time.time()
                ts["pause_consent"] = []
                self.logger.info("Timer pausiert (Konsens erreicht)")
            elif kind == "resume" and not ts.get("running") and ts.get("pause_start_ts") is not None:
                ts["pause_total_seconds"] = ts.get("pause_total_seconds", 0.0) + (time.time() - ts["pause_start_ts"])
                ts["pause_start_ts"] = None
                ts["running"] = True
                ts["resume_consent"] = []
                self.logger.info("Timer resumed (Konsens erreicht)")
        return True

    def _detect_badge_splits(self, client_id: str, teams_data) -> list[dict]:
        """Prüft Team-Update auf Badge-Count-Erhöhung → automatischer Split pro betroffenem Player."""
        if self.timer_state.get("start_ts") is None:
            return []
        splits: list[dict] = []
        if not isinstance(teams_data, dict):
            return splits
        for player_id, team in teams_data.items():
            if not team or len(team) <= 6:
                continue
            badges_raw = team[6]
            new_count = self._badge_count(badges_raw)
            if new_count is None:
                continue
            key = (client_id, player_id)
            old_count = self._timer_last_badges.get(key)
            self._timer_last_badges[key] = new_count
            if old_count is None:
                continue
            if new_count > old_count:
                owner = self._owner_for_client(client_id)
                label = f"{owner} Gym {new_count}"
                split = self._timer_split(label, source="badge")
                if split is not None:
                    splits.append(split)
        return splits

    @staticmethod
    def _badge_count(badges) -> int | None:
        if badges is None:
            return None
        if isinstance(badges, int):
            return bin(badges).count("1") if badges >= 0 else None
        if isinstance(badges, (list, tuple)):
            return sum(1 for b in badges if b)
        if isinstance(badges, str):
            try:
                return sum(1 for c in badges if c in "1TtXx✓")
            except Exception:
                return None
        return None

    async def broadcast_timer_state(self):
        message = {"type": "timer_state", "timer": self._timer_snapshot()}
        for client_id in list(self.munchlaxes.keys()):
            try:
                await self.send_to_client(client_id, message)
            except Exception as exc:
                self.logger.error(f"broadcast_timer_state an {client_id} failed: {exc}")

    async def broadcast_timer_tick(self):
        message = {
            "type": "timer_tick",
            "elapsed": self._timer_elapsed(),
            "running": self.timer_state.get("running", False),
            "server_ts": time.time(),
        }
        for client_id in list(self.munchlaxes.keys()):
            try:
                await self.send_to_client(client_id, message)
            except Exception as exc:
                self.logger.debug(f"broadcast_timer_tick an {client_id} failed: {exc}")

    async def timer_tick_loop(self):
        while True:
            try:
                if self.timer_state.get("start_ts") is not None:
                    await self.broadcast_timer_tick()
                if self.countdown_state.get("running"):
                    remaining = self._countdown_remaining()
                    await self.broadcast_countdown_tick(remaining)
                    if remaining <= 0.0 and not self._countdown_finished_signaled:
                        self._countdown_finished_signaled = True
                        self.countdown_state["running"] = False
                        self.countdown_state["finished"] = True
                        self.logger.info(
                            f"Countdown abgelaufen: label='{self.countdown_state.get('label','')}'"
                        )
                        await self.broadcast_countdown_finished()
                        await self.broadcast_countdown_state()
                await asyncio.sleep(1.0)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                self.logger.error(f"timer_tick_loop error: {exc}")
                await asyncio.sleep(1.0)

    # ----- Countdown -----

    def _countdown_remaining(self) -> float:
        cs = self.countdown_state
        if cs.get("finished"):
            return 0.0
        if not cs.get("running"):
            return max(0.0, float(cs.get("remaining_at_pause", 0.0)))
        start = cs.get("start_ts")
        base = float(cs.get("remaining_at_pause", cs.get("duration_seconds", 0.0)))
        if start is None:
            return base
        return max(0.0, base - (time.time() - start))

    def _countdown_snapshot(self) -> dict:
        cs = self.countdown_state
        return {
            "running": cs.get("running", False),
            "start_ts": cs.get("start_ts"),
            "duration_seconds": cs.get("duration_seconds", 0.0),
            "remaining_seconds": self._countdown_remaining(),
            "label": cs.get("label", ""),
            "finished": cs.get("finished", False),
            "server_ts": time.time(),
        }

    def _countdown_start(self, duration_seconds: float, label: str = ""):
        if duration_seconds <= 0:
            self._countdown_reset()
            return
        self.countdown_state = {
            "running": True,
            "start_ts": time.time(),
            "duration_seconds": float(duration_seconds),
            "remaining_at_pause": float(duration_seconds),
            "label": label or "",
            "finished": False,
        }
        self._countdown_finished_signaled = False
        self.logger.info(f"Countdown gestartet: {duration_seconds}s label='{label}'")

    def _countdown_pause(self) -> bool:
        cs = self.countdown_state
        if not cs.get("running"):
            return False
        cs["remaining_at_pause"] = self._countdown_remaining()
        cs["running"] = False
        cs["start_ts"] = None
        return True

    def _countdown_resume(self) -> bool:
        cs = self.countdown_state
        if cs.get("running") or cs.get("finished"):
            return False
        remaining = float(cs.get("remaining_at_pause", 0.0))
        if remaining <= 0:
            return False
        cs["running"] = True
        cs["start_ts"] = time.time()
        return True

    def _countdown_reset(self):
        self.countdown_state = {
            "running": False,
            "start_ts": None,
            "duration_seconds": 0.0,
            "remaining_at_pause": 0.0,
            "label": "",
            "finished": False,
        }
        self._countdown_finished_signaled = False

    async def broadcast_countdown_state(self):
        message = {"type": "countdown_state", "countdown": self._countdown_snapshot()}
        for client_id in list(self.munchlaxes.keys()):
            try:
                await self.send_to_client(client_id, message)
            except Exception as exc:
                self.logger.error(f"broadcast_countdown_state an {client_id} failed: {exc}")

    async def broadcast_countdown_tick(self, remaining: float):
        message = {
            "type": "countdown_tick",
            "remaining_seconds": remaining,
            "running": self.countdown_state.get("running", False),
            "server_ts": time.time(),
        }
        for client_id in list(self.munchlaxes.keys()):
            try:
                await self.send_to_client(client_id, message)
            except Exception as exc:
                self.logger.debug(f"broadcast_countdown_tick an {client_id} failed: {exc}")

    async def broadcast_countdown_finished(self):
        message = {
            "type": "countdown_finished",
            "label": self.countdown_state.get("label", ""),
            "duration_seconds": self.countdown_state.get("duration_seconds", 0.0),
            "server_ts": time.time(),
        }
        for client_id in list(self.munchlaxes.keys()):
            try:
                await self.send_to_client(client_id, message)
            except Exception as exc:
                self.logger.error(f"broadcast_countdown_finished an {client_id} failed: {exc}")

    def _owner_for_client(self, client_id: str) -> str:
        name = self.munchlax_names.get(client_id, "")
        return f"{name}_{client_id}"

    def _find_client_for_owner(self, owner: str) -> str | None:
        for cid in self.munchlaxes.keys():
            if self._owner_for_client(cid) == owner:
                return cid
        return None

    def _team_stats_for_owner(self, owner: str) -> dict:
        """Aggregiert badges + alive_count aus self.teams für einen Owner."""
        cid = self._find_client_for_owner(owner)
        if cid is None:
            return {"badges": None, "alive_count": None}
        player_ids = self.client_player_ids.get(cid, set())
        badges = None
        alive_count = 0
        found_team = False
        for pid in player_ids:
            team = self.teams.get(pid)
            if not team:
                continue
            found_team = True
            # Team-Array: Index 0-5 = Slots, Index 6 = badges, Index 7 = edition
            if len(team) > 6 and team[6] is not None:
                b = team[6]
                if badges is None or (isinstance(b, int) and b > badges):
                    badges = b
            for slot in team[:6]:
                if slot is None:
                    continue
                dex = getattr(slot, "dexnr", None) or (slot.get("dexnr") if isinstance(slot, dict) else None)
                if dex:
                    alive_count += 1
        if not found_team:
            return {"badges": None, "alive_count": None}
        return {"badges": badges, "alive_count": alive_count}

    def _recompute_versus_state(self) -> bool:
        """Rechnet Versus-Aggregate neu. Gibt True zurück wenn sich was geändert hat.

        "versus": Bucket pro Team (team_membership). "versus_ffa": Bucket pro
        Spieler — jeder Owner ist sein eigenes "Team"."""
        cfg = self.soullink_config
        mode = cfg.get("mode")
        if mode not in VERSUS_MODES:
            if self.soullink_versus_state:
                self.soullink_versus_state = {}
                return True
            return False
        team_map = cfg.get("team_membership", {}) or {}
        expected = cfg.get("expected_owners", []) or []
        new_state: dict[str, dict] = {}
        for owner in expected:
            team = owner if mode == "versus_ffa" else team_map.get(owner)
            if not team:
                continue
            bucket = new_state.setdefault(team, {
                "badges_min": None,
                "badges_by_owner": {},
                "alive_count": 0,
                "alive_by_owner": {},
                "deaths": 0,
                "deaths_by_owner": {},
                "owners": [],
            })
            stats = self._team_stats_for_owner(owner)
            bucket["owners"].append(owner)
            bucket["badges_by_owner"][owner] = stats["badges"]
            if stats["badges"] is not None:
                if bucket["badges_min"] is None or stats["badges"] < bucket["badges_min"]:
                    bucket["badges_min"] = stats["badges"]
            if stats["alive_count"] is not None:
                bucket["alive_by_owner"][owner] = stats["alive_count"]
                bucket["alive_count"] += stats["alive_count"]
            # Deaths pro Owner aus soullink_deaths ableiten
            own_deaths = sum(1 for (_pv, o) in self.soullink_deaths.keys() if o == owner)
            bucket["deaths_by_owner"][owner] = own_deaths
            bucket["deaths"] += own_deaths
        if new_state == self.soullink_versus_state:
            return False
        self.soullink_versus_state = new_state
        return True

    async def broadcast_soullink_versus_state(self):
        message = {
            "type": "soullink_versus_state",
            "state": self.soullink_versus_state,
            "battles": list(self.soullink_versus_battles),
        }
        for client_id in list(self.munchlaxes.keys()):
            try:
                await self.send_to_client(client_id, message)
            except Exception as exc:
                self.logger.error(f"broadcast_soullink_versus_state an {client_id} failed: {exc}")

    def _effective_team_membership(self) -> dict[str, str]:
        """owner → team_id. Bei aktivem Soullink aus config; sonst jeder Owner
        sein eigenes Team. Für Standard-Nuzlocke/Off ergibt sich {owner: owner}
        aus verbundenen Clients + expected_owners."""
        cfg = self.soullink_config
        mode = cfg.get("mode", "off")
        if mode == "coop":
            raw = cfg.get("team_membership", {}) or {}
            if raw:
                return {o: slug_team_id(t) for o, t in raw.items()}
            # Coop-Fallback: NuzlockeMenu fuellt team_membership derzeit nur im
            # versus-Zweig. Ohne diese Zuordnung waere das gesamte Team-Feature
            # fuer den Coop-Kern-Anwendungsfall wirkungslos. Alle expected_owners
            # in einen gemeinsamen Bucket 'coop' schuetten.
            return {o: "coop" for o in (cfg.get("expected_owners", []) or []) if o}
        if mode == "versus":
            raw = cfg.get("team_membership", {}) or {}
            return {o: slug_team_id(t) for o, t in raw.items()}
        if mode == "versus_ffa":
            return {o: slug_team_id(o) for o in (cfg.get("expected_owners", []) or [])}
        owners: set[str] = set()
        for cid in self.munchlaxes.keys():
            try:
                o = self._owner_for_client(cid)
                if o:
                    owners.add(o)
            except Exception:
                pass
        for o in (cfg.get("expected_owners", []) or []):
            if o:
                owners.add(o)
        # Zusätzlich alle jemals gesehenen Owner aus dem Team-Owner-Cache. Sonst
        # verschwindet bei kurzem Disconnect eines Owners im off-Modus der
        # gesamte Team-Bucket aus dem State — für ein Regie-/Spectator-Overlay
        # sieht das wie "Team weg" aus statt "Werte unverändert".
        for o in self._team_owner_cache.keys():
            if o:
                owners.add(o)
        return {o: slug_team_id(o) for o in owners}

    def _prune_team_owner_cache(self):
        """Entfernt Cache-Einträge deren Grace-Period abgelaufen ist. Owner die
        aktuell verbunden oder in expected_owners sind bekommen frisches
        last_seen; alle anderen sterben nach TEAM_OWNER_CACHE_GRACE_SECONDS.
        """
        now = time.time()
        connected_owners: set[str] = set()
        for cid in list(self.munchlaxes.keys()):
            try:
                o = self._owner_for_client(cid)
                if o:
                    connected_owners.add(o)
            except Exception:
                pass
        expected = set(self.soullink_config.get("expected_owners", []) or [])
        alive = connected_owners | expected
        for owner in list(self._team_owner_cache.keys()):
            entry = self._team_owner_cache[owner]
            if owner in alive:
                entry["last_seen"] = now
                continue
            if now - entry.get("last_seen", now) > TEAM_OWNER_CACHE_GRACE_SECONDS:
                del self._team_owner_cache[owner]

    def _compute_team_state(self) -> dict[str, dict]:
        """Baut team_state aus effektiver Membership + self.teams. Owner-Rohdaten,
        Aggregation (AND, Region) macht das Overlay.

        Fällt für disconnected/blip-artige Owner auf `self._team_owner_cache`
        zurück, damit das Team-Badge-Overlay nicht bei jedem Heartbeat-Aussetzer
        alle Regionen auf 0 kippt.
        """
        self._prune_team_owner_cache()
        membership = self._effective_team_membership()
        new_state: dict[str, dict] = {}
        now = time.time()
        for owner, team_id in membership.items():
            bucket = new_state.setdefault(team_id, {
                "team_id": team_id,
                "owners": [],
                "badges_by_owner": {},
                "editions_by_owner": {},
                "player_ids_by_owner": {},
            })
            if owner in bucket["owners"]:
                continue
            bucket["owners"].append(owner)
            cid = self._find_client_for_owner(owner)
            player_ids = sorted(self.client_player_ids.get(cid, set())) if cid else []
            bucket["player_ids_by_owner"][owner] = player_ids
            badges_val = None
            edition_val = None
            for pid in player_ids:
                team = self.teams.get(pid)
                if not team:
                    continue
                if len(team) > 6 and team[6] is not None and isinstance(team[6], int):
                    if badges_val is None or team[6] > badges_val:
                        badges_val = team[6]
                if len(team) > 7 and team[7] is not None:
                    edition_val = team[7]
            cached = self._team_owner_cache.setdefault(owner, {"badges": None, "edition": None, "last_seen": now})
            if badges_val is not None:
                cached["badges"] = badges_val
            if edition_val is not None:
                cached["edition"] = edition_val
            if cid is not None:
                cached["last_seen"] = now
            bucket["badges_by_owner"][owner] = badges_val if badges_val is not None else cached["badges"]
            bucket["editions_by_owner"][owner] = edition_val if edition_val is not None else cached["edition"]
        return new_state

    def _recompute_team_state(self) -> bool:
        """True wenn sich state geändert hat."""
        new_state = self._compute_team_state()
        if new_state == self.soullink_team_state:
            return False
        self.soullink_team_state = new_state
        return True

    async def broadcast_soullink_team_state(self):
        message = {
            "type": "soullink_team_state",
            "state": self.soullink_team_state,
        }
        for client_id in list(self.munchlaxes.keys()):
            try:
                await self.send_to_client(client_id, message)
            except Exception as exc:
                self.logger.error(f"broadcast_soullink_team_state an {client_id} failed: {exc}")

    async def broadcast_soullink_versus_battle(self, battle: dict):
        message = {"type": "soullink_versus_battle", "battle": battle}
        for client_id in list(self.munchlaxes.keys()):
            try:
                await self.send_to_client(client_id, message)
            except Exception as exc:
                self.logger.error(f"broadcast_soullink_versus_battle an {client_id} failed: {exc}")

    def _check_first_type_clash(self, link: dict) -> dict | None:
        """Prüft Ersttyp-Kollision zwischen Link-Members. Nur bei state=complete.

        - Config: rule_first_type_clause_linked (default off) muss aktiv sein
        - rule_first_type_clause_static_exception: Members mit method ∈ {static, gift,
          fossil, egg} sind vom Regel-Violation-Broadcast befreit, ABER: erleiden sie
          durch die Zwangsmechanik einen Ersttyp-Match mit einem anderen Member,
          bekommen sie als Kompensation einen Token gutgeschrieben (Task #12-Regel).
        - Kollision: mindestens 2 Members mit gleichem Typ1
        - Guard: link["compensation_paid"] verhindert doppelte Token-Gutschrift
        """
        if not self._rule("rule_first_type_clause_linked", False):
            return None
        if link.get("state") != "complete":
            return None
        static_exempt = self._rule("rule_first_type_clause_static_exception", True)
        exempt_methods = {"static", "gift", "fossil", "egg"} if static_exempt else set()

        # Alle Members mit Typen + Methods sammeln
        member_types: dict[str, tuple[int, str]] = {}
        for owner, member in link.get("members", {}).items():
            t = first_type(member.get("dexnr"))
            if t is None:
                continue
            member_types[owner] = (t, member.get("method", "wild"))

        # Typ-Buckets (alle Owner, unabhängig von Method)
        buckets: dict[int, list[str]] = {}
        for owner, (t, _m) in member_types.items():
            buckets.setdefault(t, []).append(owner)
        colliding_buckets = {t: owners for t, owners in buckets.items() if len(owners) >= 2}
        if not colliding_buckets:
            return None

        # Token-Kompensation: Für jeden Owner in einer Kollision, dessen method
        # exempt ist, gib einen Token (nur einmal pro Link).
        if not link.get("compensation_paid"):
            compensated: list[str] = []
            for t, owners in colliding_buckets.items():
                for owner in owners:
                    _t, method = member_types.get(owner, (0, "wild"))
                    if method in exempt_methods:
                        self._token_earn(owner)
                        compensated.append(owner)
            if compensated:
                link["compensation_paid"] = True
                self.logger.info(
                    f"Ersttyp-Kollision durch geschenkte Pokemon → Token-Kompensation "
                    f"für {compensated} in Link {link['link_id']}"
                )
                # Token-State broadcast asynchron aus dem Aufrufer-Kontext getriggert:
                # setze Flag, damit dieser Aufruf-Rahmen die Broadcast-Task erstellt.
                link["_compensation_broadcast_pending"] = True

        # Violation-Broadcast: Kollisionen zwischen NICHT-exempt Members zählen als Regelbruch.
        clashing_non_exempt: dict[int, list[str]] = {}
        for t, owners in colliding_buckets.items():
            non_exempt = [o for o in owners if member_types.get(o, (0, "wild"))[1] not in exempt_methods]
            if len(non_exempt) >= 2:
                clashing_non_exempt[t] = non_exempt
        if not clashing_non_exempt:
            return None

        parts = [
            f"{type_name(t)}: {', '.join(owners)}"
            for t, owners in clashing_non_exempt.items()
        ]
        subject = f"link_{link['link_id']}"
        return {
            "type": "first_type_clash",
            "subject": subject,
            "link_id": link["link_id"],
            "clashes": clashing_non_exempt,
            "timestamp": time.time(),
            "message": f"Ersttyp-Kollision im Link {link['link_id']}: " + " | ".join(parts),
        }

    def _rule(self, key: str, default):
        """Kurzform: liest rule_* aus soullink_config['rules'] oder Fallback default.

        Server-Config speichert Rules nicht separat — sie liegen im Client-Config
        (nuz-dict). Client sollte sie als Teil von soullink_config['rules'] mitsenden;
        Fallback default wenn Feld fehlt.
        """
        rules = self.soullink_config.get("rules") if isinstance(self.soullink_config, dict) else None
        if isinstance(rules, dict) and key in rules:
            return rules[key]
        return default

    def _token_state_for(self, owner: str) -> dict:
        return self.soullink_tokens.setdefault(owner, {
            "earned": 0, "used": 0, "active_route": None, "active_edition": None,
        })

    def _token_earn(self, owner: str) -> bool:
        if not owner:
            return False
        st = self._token_state_for(owner)
        st["earned"] = int(st.get("earned", 0)) + 1
        self.logger.info(f"Token-Earn: {owner} → earned={st['earned']}")
        return True

    def _token_redeem(self, owner: str, edition, route) -> bool:
        if not owner or route is None:
            return False
        st = self._token_state_for(owner)
        available = int(st.get("earned", 0)) - int(st.get("used", 0))
        if available <= 0:
            self.logger.warning(f"Token-Redeem abgelehnt: {owner} keine freien Tokens")
            return False
        st["used"] = int(st.get("used", 0)) + 1
        st["active_route"] = int(route)
        st["active_edition"] = edition
        self.logger.info(
            f"Token-Redeem: {owner} edition={edition} route={route} "
            f"(remaining={int(st['earned']) - int(st['used'])})"
        )
        return True

    def _token_consume_if_matches(self, owner: str, edition, route) -> bool:
        """Wird beim Verarbeiten eines Encounters aufgerufen: passt Route+Edition
        zum aktiven Token-Credit, verbrauche den Credit und gebe True zurück.
        """
        if not owner or route is None:
            return False
        st = self.soullink_tokens.get(owner)
        if not st:
            return False
        active_route = st.get("active_route")
        active_edition = st.get("active_edition")
        if active_route is None:
            return False
        if int(active_route) != int(route):
            return False
        if active_edition is not None and edition is not None:
            try:
                if int(active_edition) != int(edition):
                    return False
            except (ValueError, TypeError):
                pass
        st["active_route"] = None
        st["active_edition"] = None
        self.logger.info(f"Token-Credit eingelöst: {owner} route={route}")
        return True

    async def broadcast_soullink_tokens(self):
        message = {"type": "soullink_tokens", "tokens": dict(self.soullink_tokens)}
        for client_id in list(self.munchlaxes.keys()):
            try:
                await self.send_to_client(client_id, message)
            except Exception as exc:
                self.logger.error(f"broadcast_soullink_tokens an {client_id} failed: {exc}")

    def _detect_trade(self, enc: dict) -> dict | None:
        """Erkennt Owner-Wechsel eines PID → wahrscheinlicher Trade (bei Soullink verboten).

        Regel: Wenn (personality, some_owner) im Encounter-Cache existiert und der neue
        Encounter denselben personality-Wert unter einem anderen Owner meldet, ist das
        Owner-Umzug. Cross-owner PID-Kollisionen sind selten genug, dass wir das als
        Trade-Indikator behandeln.
        """
        if self.soullink_config.get("mode", "off") not in SOULLINK_MODES:
            return None
        pv = enc.get("personality")
        new_owner = enc.get("owner")
        if pv is None or not new_owner:
            return None
        for (existing_pv, existing_owner), existing in self.encounters.items():
            if existing_pv != pv or existing_owner == new_owner:
                continue
            # gleicher PID unter anderem Owner
            violation = {
                "type": "trade",
                "subject": f"{pv}",
                "old_owner": existing_owner,
                "new_owner": new_owner,
                "dexnr": enc.get("dexnr"),
                "timestamp": time.time(),
                "message": (
                    f"PID {pv} taucht bei {new_owner} auf, war zuvor bei {existing_owner}. "
                    f"Trades bei aktiver Soullink-Session sind verboten."
                ),
            }
            key = ("trade", str(pv))
            if key in self.soullink_rule_violations:
                return None
            self.soullink_rule_violations[key] = violation
            self.logger.warning(
                f"Soullink-Trade-Warnung: PID={pv} {existing_owner} -> {new_owner}"
            )
            return violation
        return None

    async def broadcast_soullink_rule_violation(self, violation: dict):
        key = (violation.get("type", "unknown"), str(violation.get("subject", "")))
        self.soullink_rule_violations[key] = violation
        message = {"type": "soullink_rule_violation", "violation": violation}
        for client_id in list(self.munchlaxes.keys()):
            try:
                await self.send_to_client(client_id, message)
            except Exception as exc:
                self.logger.error(f"broadcast_soullink_rule_violation an {client_id} failed: {exc}")

    async def broadcast_soullink_death(self, death: dict):
        message = {"type": "soullink_death", "death": death}
        for client_id in list(self.munchlaxes.keys()):
            try:
                await self.send_to_client(client_id, message)
            except Exception as exc:
                self.logger.error(f"broadcast_soullink_death an {client_id} failed: {exc}")
        # Falls Death eine Link-Group auf failed gesetzt hat: Link-State ebenfalls broadcasten.
        link_id = death.get("link_id")
        if link_id is not None and link_id in self.soullink_links:
            await self.broadcast_soullink_link_state(self.soullink_links[link_id])

    async def broadcast_bag_sync(self, sender_id, data):
        for client_id in list(self.munchlaxes.keys()):
            if client_id == sender_id:
                continue
            try:
                await self.send_to_client(client_id, data)
            except Exception as exc:
                self.logger.error(f"broadcast_bag_sync an {client_id} failed: {type(exc)},{exc}")
                self.logger.error(f"{traceback.format_exc()}")

    async def disconnect_client(self, client_id):
        async with self.disconnect_lock:
            if client_id in self.munchlaxes:
                writer = self.munchlaxes[client_id]
                writer.close()
                await writer.wait_closed()
                self.logger.info(f"Client {client_id} disconnected.")
                del self.munchlaxes[client_id]
                del self.munchlax_names[client_id]
                del self.munchlax_status[client_id]
                del self.munchlax_heartbeats[client_id]
                del self.heartbeat_counts[client_id]
                self.writer_locks.pop(client_id, None)
                self.client_player_ids.pop(client_id, None)
        asyncio.create_task(self.broadcast_connection_status())
        asyncio.create_task(self.broadcast_player_names())
        if self._recompute_team_state():
            asyncio.create_task(self.broadcast_soullink_team_state())
        if self._recompute_versus_state():
            asyncio.create_task(self.broadcast_soullink_versus_state())

    # async def send_message(self, writer, message):
    #     serialized_message = pickle.dumps(message)
    #     length = len(serialized_message).to_bytes(4, 'big')
    #     writer.write(length)
    #     await writer.drain()
    #     writer.write(serialized_message)
    #     await writer.drain()
    
    async def send_message(self, writer, message):
        serialized_message = pickle.dumps(message)
        CHUNK_SIZE = 500  # Die Größe jedes Chunks in Bytes

        # Gesamtlänge der Nachricht senden
        msg_type = message.get("type", "teams") if isinstance(message, dict) else type(message).__name__
        chunk_count = (len(serialized_message) + CHUNK_SIZE - 1) // CHUNK_SIZE
        self.logger.debug(f"Sende Nachricht: type={msg_type}, {len(serialized_message)} Bytes, {chunk_count} Chunks")
        length = len(serialized_message).to_bytes(4, 'big')
        writer.write(length)
        await writer.drain()

        # Nachricht in Chunks senden
        for i in range(0, len(serialized_message), CHUNK_SIZE):
            chunk = serialized_message[i:i+CHUNK_SIZE]
            # Größe des aktuellen Chunks senden
            chunk_length = len(chunk).to_bytes(4, 'big')
            writer.write(chunk_length)
            await writer.drain()
            # Chunk senden
            writer.write(chunk)
            await writer.drain()


    # async def receive_message(self, reader):
    #     message_length = int.from_bytes(await reader.read(4), 'big')
    #     message = await reader.read(message_length)

    #     return pickle.loads(message)

    async def receive_message(self, reader):
        # readexactly statt read: read(N) liefert nur "bis zu" N Bytes — bei
        # grossen Pickles (z.B. Gen 6/7 Boxes-Update ~210 KB in 500-Byte-Chunks)
        # werden TCP-Pakete fragmentiert, der Stream desynchronisiert sich und
        # das Pickle bricht mit "invalid load key". readexactly garantiert
        # genau N Bytes oder wirft IncompleteReadError (vom Caller gefangen).
        total_length = int.from_bytes(await reader.readexactly(4), 'big')
        message = b''

        while len(message) < total_length:
            chunk_length = int.from_bytes(await reader.readexactly(4), 'big')
            chunk = await reader.readexactly(chunk_length)
            message += chunk

        return pickle.loads(message)
    
    async def broadcast_connection_status(self):
        status = {cid: self.munchlax_status.get(cid, "connected") for cid in self.munchlaxes}
        names = dict(self.munchlax_names)
        message = {"type": "connection_status", "status": status, "names": names}
        for client_id in list(self.munchlaxes.keys()):
            try:
                await self.send_to_client(client_id, message)
            except Exception as exc:
                self.logger.error(f"broadcast_connection_status an {client_id} failed: {exc}")

    async def broadcast_player_names(self):
        names: dict[int, str] = {}
        for cid, player_ids in self.client_player_ids.items():
            client_name = self.munchlax_names.get(cid, "")
            for pid in player_ids:
                names[pid] = client_name
        message = {"type": "player_names", "names": names}
        for client_id in list(self.munchlaxes.keys()):
            try:
                await self.send_to_client(client_id, message)
            except Exception as exc:
                self.logger.error(f"broadcast_player_names an {client_id} failed: {exc}")

    async def check_heartbeats(self):
        while True:
            # Try/except um den gesamten Iterations-Body, weil disconnect_client
            # synchrone _recompute_team_state/_recompute_versus_state aufruft.
            # Eine Exception dort darf den Heartbeat-Monitor nicht dauerhaft killen
            # (sonst laufen tote Clients ewig weiter, siehe Sync-Review Runde 6).
            try:
                now = time.time()
                to_disconnect = []
                for client_id, last_heartbeat in list(self.munchlax_heartbeats.items()):
                    if now - last_heartbeat > 5.1:
                        self.logger.warning(f"Client {client_id} hat seit {now - last_heartbeat} Sekunden keinen Heartbeat gesendet!")
                        self.heartbeat_counts[client_id] += 1
                        if self.heartbeat_counts[client_id] > 3:
                            to_disconnect.append(client_id)
                        else:
                            self.munchlax_status[client_id] = "warning"
                    else:
                        self.heartbeat_counts[client_id] = 0
                        self.munchlax_status[client_id] = "connected"
                for client_id in to_disconnect:
                    await self.disconnect_client(client_id)
                await self.broadcast_connection_status()
            except Exception as exc:
                self.logger.error(f"check_heartbeats Iteration abgebrochen: {type(exc)},{exc}")
                self.logger.error(f"{traceback.format_exc()}")
            await asyncio.sleep(5)
    
    async def start(self):
        self.server = await asyncio.start_server(
            self.handle_munchlax, self.host, self.port)
        
        self.logger.info(f"Arceus auf Port {self.port} gestartet")
        self.heartbeattask = asyncio.create_task(self.check_heartbeats())
        self.timer_task = asyncio.create_task(self.timer_tick_loop())
        self.is_connected = 'connected'

        async with self.server:
            await self.server.serve_forever()

    async def start_helper_listener(self, sp: dict, configsave, save_callback=None):
        helper_port = int(self.rem.get('helper_port', int(self.port) + 1))
        self.helper_server = await asyncio.start_server(
            lambda r, w: self._handle_helper(r, w, sp, configsave, save_callback),
            self.host, helper_port)
        self.logger.info(f"Helper-Listener auf Port {helper_port} gestartet")

    async def _handle_helper(self, reader, writer, sp, configsave, save_callback):
        try:
            data = await self.receive_message(reader)
            if isinstance(data, dict) and data.get("type") == "sprite_path_obs":
                for key, value in data.items():
                    if key != "type":
                        sp[key] = value
                if save_callback:
                    save_callback()
                await self.send_message(writer, "ok")
                self.logger.info(f"OBS-Sprite-Pfade vom Helper empfangen und gespeichert.")
            else:
                await self.send_message(writer, "error")
                self.logger.warning(f"Unbekannte Helper-Nachricht: {data}")
        except Exception as err:
            self.logger.error(f"Fehler im Helper-Handler: {err}")
        finally:
            writer.close()
            await writer.wait_closed()

    async def stop_helper_listener(self):
        if hasattr(self, 'helper_server') and self.helper_server:
            self.helper_server.close()
            await self.helper_server.wait_closed()
            self.helper_server = None
            self.logger.info("Helper-Listener gestoppt.")

    async def stop(self):
        await self.stop_helper_listener()
        if self.server:
            self.heartbeattask.cancel()
            if self.timer_task is not None:
                self.timer_task.cancel()
                self.timer_task = None
            self.server.close()
            await self.server.wait_closed()
            self.server = None
            self.is_connected = False
            self.logger.info("Arceus has been stopped.")
            self.port = self.rem['client_port']
