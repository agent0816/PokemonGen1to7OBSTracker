import asyncio
import pickle
import time
import traceback
from backend.logging_setup import get_logger

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
                    for enc in encounters:
                        key = (enc["personality"], enc["owner"])
                        self.encounters[key] = enc
                    asyncio.create_task(self.broadcast_encounter_sync(client_id, encounters))
                elif isinstance(data, dict) and data.get("type") == "encounter_outcome":
                    pv = data["personality"]
                    owner = data["owner"]
                    outcome = data["outcome"]
                    key = (pv, owner)
                    if key in self.encounters:
                        self.encounters[key]["outcome"] = outcome
                    asyncio.create_task(self.broadcast_encounter_outcome(client_id, pv, owner, outcome))
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
            except ConnectionResetError:
                pass
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

        for (bag_owner, bag_edition), pockets in list(self.bags.items()):
            await self.send_to_client(client_id, {
                "type": "bag_sync",
                "owner": bag_owner,
                "edition": bag_edition,
                "pockets": pockets,
            })

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
            await asyncio.sleep(5)
    
    async def start(self):
        self.server = await asyncio.start_server(
            self.handle_munchlax, self.host, self.port)
        
        self.logger.info(f"Arceus auf Port {self.port} gestartet")
        self.heartbeattask = asyncio.create_task(self.check_heartbeats())
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
            self.server.close()
            await self.server.wait_closed()
            self.server = None
            self.is_connected = False
            self.logger.info("Arceus has been stopped.")
            self.port = self.rem['client_port']
