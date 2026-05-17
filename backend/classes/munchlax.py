import asyncio
import os
import hashlib
import pickle
import time
from pathlib import Path
from pickle import UnpicklingError
import traceback
from backend.classes.obs import OBS
from backend.classes.pokedex_db import PokedexDB
from backend.logging_setup import get_logger

# Mindestabstand zwischen automatischen Box-Refreshs pro Spieler.
# Verhindert Box-Read-Stürme z.B. bei Team-Reorder-Spam oder Evolutionen.
BOX_REFRESH_THROTTLE_SECONDS = 5.0

class Munchlax:
    def __init__(self, host, port, rem, sp, pl, configsave=None):
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
                        if self.obs and self.obs.is_connected:
                            await self.obs.changeSource(player, diff, team, self.editions[player])
                        self.sorted_teams[player] = team
                        if self.overlay_server and self.overlay_server.is_connected:
                            await self.overlay_server.notify_update(player, "team")
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
        except Exception as err:
            self.logger.error(f"update_bag failed: {type(err)},{err}")
            self.logger.error(f"{traceback.format_exc()}")

    async def _persist_teams(self, teams):
        try:
            self._ensure_pokedex_db()
            if self.pokedex_db is None or self.pokedex_db.connection is None:
                return
            loop = asyncio.get_event_loop()
            for player, team_data in teams.items():
                # team_data hat die Form [p1..p6, badges, edition]
                pokemons = team_data[:6]
                edition = team_data[7] if len(team_data) > 7 else self.editions.get(player)
                owner = str(player)
                await loop.run_in_executor(
                    None,
                    self.pokedex_db.upsert_team,
                    owner,
                    edition,
                    pokemons,
                )
        except Exception as err:
            self.logger.error(f"_persist_teams failed: {type(err)},{err}")
            self.logger.error(f"{traceback.format_exc()}")

    def logging_teams(self, teams: dict, dictname: str):
        self.logger.debug(dictname)
        for player, team in teams.items():
            self.logger.debug(f"logging_teams: player {player}: {[str(p) for p in team[:6]]}")
    
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