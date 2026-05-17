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
        self.teams = {}
        # Box-Cache analog zu self.teams: dict[player_id, list[list[Pokemon|None]]].
        # Wird vom besitzenden Munchlax per "boxes_update" gefüllt und bei
        # neuen Verbindungen einmalig an den Client gepusht.
        self.boxes = {}
        # Pro Client ein Lock, damit gleichzeitige Sender (update_all_clients +
        # broadcast_boxes_update) sich nicht in den chunked-Stream funken.
        self.writer_locks = {}
        self.server = None
        self.is_connected = False
        self.disconnect_lock = asyncio.Lock()
        self.rem = rem

        self.logger = get_logger(__name__, './logs/arceus.log')
    
    async def handle_munchlax(self, reader, writer):

        client_id_with_name = tuple((await self.receive_message(reader)).split("_"))
        client_name = client_id_with_name[0]
        client_id = client_id_with_name[1]
        self.munchlaxes[client_id] = writer
        self.munchlax_names[client_id] = client_name
        self.munchlax_status[client_id] = 'connected'
        self.heartbeat_counts[client_id] = 0
        self.writer_locks[client_id] = asyncio.Lock()
        self.logger.info(f"Client {client_id} connected and registered.")

        asyncio.create_task(self.update_all_clients(client_id))

        while True:
            try:
                data = await self.receive_message(reader)
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
                else:
                    for player, team in data.items(): #type: ignore
                        if player not in self.teams or self.teams[player] != team:
                            self.teams[player] = team
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
        self.logger.info(f"Arceus: {self.teams}")
        await self.send_to_client(client_id, self.teams)

        # Box-Stand einmal an den frisch verbundenen Client schicken, damit
        # remote betrachtete BoxMenüs sofort den letzten bekannten Cache haben.
        for player_id, boxes in list(self.boxes.items()):
            await self.send_to_client(client_id, {
                "type": "boxes_update",
                "player_id": player_id,
                "boxes": boxes,
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
        self.logger.info(f"{serialized_message=}")
        length = len(serialized_message).to_bytes(4, 'big')
        writer.write(length)
        await writer.drain()

        # Nachricht in Chunks senden
        for i in range(0, len(serialized_message), CHUNK_SIZE):
            chunk = serialized_message[i:i+CHUNK_SIZE]
            self.logger.info(f"Arceus: {chunk=}")
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
    
    async def check_heartbeats(self):
        while True:
            now = time.time()
            for client_id, last_heartbeat in self.munchlax_heartbeats.items():
                if now - last_heartbeat > 5.1:
                    self.logger.warning(f"Client {client_id} hat seit {now - last_heartbeat} Sekunden keinen Heartbeat gesendet!")
                    self.heartbeat_counts[client_id] += 1
                    if self.heartbeat_counts[client_id] > 3:
                        await self.disconnect_client(client_id)
                else:
                    self.heartbeat_counts[client_id] = 0
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
