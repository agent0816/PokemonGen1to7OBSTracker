import asyncio
import os
import hashlib
import logging
import sys
import pickle
from pickle import UnpicklingError
import traceback
from backend.classes.obs import OBS

class Munchlax:
    def __init__(self, host, port, rem, sp, pl):
        self.client_id = rem.get("client_id")
        if self.client_id == 0:
            self.client_id = self.generate_hashed_id()
            rem["client_id"] = self.client_id
        self.bizhawk_teams = {}
        self.sorted_teams = {}
        self.unsorted_teams = {}
        self.badges = {}
        self.editions = {}
        self.rem = rem
        self.sp = sp
        self.pl = pl
        self.host = host
        self.port = port
        self.is_connected = False
        self.obs: OBS | None = None
        self.writer_lock = asyncio.Lock()
        self.disconnect_lock = asyncio.Lock()

        self.logger = self.init_logging()

    def init_logging(self):
        logger = logging.getLogger(__name__)
        logger.setLevel(logging.INFO)

        logging_formatter = logging.Formatter('[%(asctime)s] %(levelname)s: %(message)s')

        file_handler = logging.FileHandler('./logs/munchlax.log', 'w')
        file_handler.setFormatter(logging_formatter)
        logger.addHandler(file_handler)

        stream_handler = logging.StreamHandler(sys.stdout)
        stream_handler.setFormatter(logging_formatter)
        logger.addHandler(stream_handler)

        return logger

    async def alter_teams(self):
        while True:
            try:
                # length = int.from_bytes(await reader.read(4), 'big')
                # msg = await reader.read(length)
                # self.unsorted_teams = pickle.loads(msg)
                self.unsorted_teams = await self.receive_message()
                self.logger.info(self.unsorted_teams)
                new_teams = self.unsorted_teams.copy()
                self.logger.info(f"{new_teams=}")
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
                if new_teams != self.sorted_teams:
                    for player in new_teams:
                        if player not in self.sorted_teams:
                            if self.obs and self.obs.is_connected: 
                                await self.obs.changeSource(player, range(6), new_teams[player], self.editions[player]) 
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
                    self.sorted_teams = new_teams.copy()
            except UnicodeEncodeError as err:
                self.logger.error(f"Unicode error:{type(err)},{err}")
                self.logger.error(f"{traceback.format_exc()}")
            except UnpicklingError as err:
                self.logger.error(f"Pickle Data error:{type(err)},{err}")
                self.logger.error(f"{traceback.format_exc()}")
            except EOFError as err:
                self.logger.error(f"{traceback.format_exc()}")
            except Exception as err:
                self.logger.error(f"alter_teams abgebrochen: {type(err)},{err}")
                self.logger.error(f"{traceback.format_exc()}")
                break

        await self.disconnect()

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
                async with self.writer_lock:
                    await self.send_message('heartbeat')
                await asyncio.sleep(5)
            except Exception as err:
                self.logger.warning(f"Heartbeat failed: {type(err)},{err}")
                self.logger.error(f"{traceback.format_exc()}")
                break
        
        await self.disconnect() #type: ignore

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
        
        await self.disconnect() # type:ignore
    
    async def connect(self):
        self.logger.info(f"trying to connect munchlax to ({self.host}, {self.port})")
        self.reader, self.writer = await asyncio.open_connection(self.host, self.port)
        self.logger.info(f"Munchlax {self.client_id} bei Arceus({self.host},{self.port}) registriert")
        
        name = self.pl.get('your_name', '')

        async with self.writer_lock:
            await self.send_message(f"{name}_{self.client_id}")
        self.is_connected = 'connected'

        self.heartbeat_task = asyncio.create_task(self.send_heartbeat())
        self.send_teams_task = asyncio.create_task(self.send_teams())
        self.alter_teams_task = asyncio.create_task(self.alter_teams())

    async def disconnect(self):
        async with self.disconnect_lock:
            if self.is_connected:
                try:
                    async with self.writer_lock:
                        await self.send_message(f"disconnect {self.client_id}")
                except Exception as err:
                    self.logger.warning(f"Disconnect Nachricht versenden failed: {err}")
                
                self.is_connected = False

                self.alter_teams_task.cancel()
                self.heartbeat_task.cancel()
                self.send_teams_task.cancel()

                self.writer.close()
                await self.writer.wait_closed()
                self.logger.info(f"Client {self.client_id} hat sich disconnectet.")

                self.host = '127.0.0.1' if self.rem["start_server"] else self.rem["server_ip_adresse"]
                self.port = self.rem["client_port"] if self.rem["start_server"] else self.rem["server_port"]

    # async def send_message(self, message):
    #     serialized_message = pickle.dumps(message)
    #     length = len(serialized_message).to_bytes(4, 'big')
    #     self.writer.write(length)
    #     await self.writer.drain()
    #     self.writer.write(serialized_message)
    #     await self.writer.drain()

    async def send_message(self, message):
        serialized_message = pickle.dumps(message)
        CHUNK_SIZE = 500  # Die Größe jedes Chunks in Bytes

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
        total_length = int.from_bytes(await reader.read(4), 'big')
        message = b''

        while len(message) < total_length:
            chunk_length = int.from_bytes(await reader.read(4), 'big')
            chunk = await reader.read(chunk_length)
            message += chunk

        return pickle.loads(message)

    # async def receive_message(self):
    #     reader = self.reader
    #     message_length = int.from_bytes(await reader.read(4), 'big')
    #     self.logger.info(f"Munchlax: {message_length=}")
    #     message = await reader.read(message_length)
    #     self.logger.info(f"Munchlax: {message=}")

    #     return pickle.loads(message)
    
    def generate_hashed_id(self):
        random_id = os.urandom(16)
        
        hashed = hashlib.sha256(random_id).hexdigest()

        client_id = hashed
        return client_id