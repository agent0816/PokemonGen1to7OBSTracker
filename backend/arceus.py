import asyncio
import sys
import pickle
import logging
import time
import pickle
import traceback
from kivy.event import EventDispatcher
from kivy.properties import DictProperty

class Arceus(EventDispatcher):
    def __init__(self, host, port):
        super().__init__()
        self.host = host
        self.port = port
        self.munchlaxes = {}
        self.status_dict = {}
        self.munchlax_status = DictProperty()
        self.munchlax_heartbeats = {}
        self.heartbeat_counts = {}
        self.teams = {}
        self.server = None
        self.is_connected = False

        self.logger = self.init_logging()

    def init_logging(self):
        logger = logging.getLogger(__name__)

        logging_formatter = logging.Formatter('[%(asctime)s] %(levelname)s: %(message)s')

        file_handler = logging.FileHandler('./logs/arceus.log', 'w')
        file_handler.setFormatter(logging_formatter)
        logger.addHandler(file_handler)

        stream_handler = logging.StreamHandler(sys.stdout)
        stream_handler.setFormatter(logging_formatter)
        logger.addHandler(stream_handler)

        return logger
    
    async def handle_munchlax(self, reader, writer):

        self.logger.warning("handle_munchlax")
        client_id = await self.receive_message(reader)
        self.logger.warning(client_id)
        self.munchlaxes[client_id] = writer
        self.status_dict[client_id] = 'connected'
        self.munchlax_status.set(self, self.status_dict)
        self.logger.warning("nach Veränderung des Dictionaries")
        self.heartbeat_counts[client_id] = 0
        self.logger.info(f"Client {client_id} connected and registered.")

        asyncio.create_task(self.update_all_clients(writer))

        while True:
            try:
                data = await self.receive_message(reader)
                if type(data) == str and data.startswith("disconnect"): # or not data:
                    break
                if data == 'heartbeat':
                    self.munchlax_heartbeats[client_id] = time.time()
                else:
                    for player, team in data.items(): #type: ignore
                        if player not in self.teams or self.teams[player] != team:
                            self.teams[player] = team
            except ConnectionResetError:
                pass
            except Exception as exc:
                self.logger.error(f"handle_munchlax abgebrochen:{type(exc)},{exc}")
                self.logger.error(f"{traceback.format_exc()}")
                break
        
        await self.disconnect_client(client_id)

    async def update_all_clients(self, writer):
        old_teams = self.teams.copy()
        await self.send_message(writer, self.teams)

        while True:
            try:
                if old_teams != self.teams:
                    old_teams = self.teams.copy()
                    await self.send_message(writer, self.teams)
                await asyncio.sleep(1)
            except Exception as exc:
                self.logger.error(f"update_all_clients abgebrochen:{exc}")
                break
    
    async def disconnect_client(self, client_id):
        if client_id in self.munchlaxes:
            writer = self.munchlaxes[client_id]
            writer.close()
            await writer.wait_closed()
            self.logger.info(f"Client {client_id} disconnected.")
            del self.munchlaxes[client_id]
            del self.status_dict[client_id]
            self.munchlax_status.set(self, self.status_dict)
            del self.munchlax_heartbeats[client_id]
            del self.heartbeat_counts[client_id]

    async def send_message(self, writer, message):
        serialized_message = pickle.dumps(message)
        length = len(serialized_message).to_bytes(4, 'big')
        writer.write(length)
        await writer.drain()
        writer.write(serialized_message)
        await writer.drain()
    
    async def receive_message(self, reader):
        message_length = int.from_bytes(await reader.read(4), 'big')
        message = await reader.read(message_length)

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

    async def stop(self):
        if self.server:
            self.heartbeattask.cancel()
            self.server.close()
            await self.server.wait_closed()
            self.server = None
            self.is_connected = False
            self.logger.info("Arceus has been stopped.")
