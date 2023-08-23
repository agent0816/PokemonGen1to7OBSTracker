import asyncio
import os
import hashlib
import logging
import sys
import pickle

class Munchlax:
    def __init__(self, host, port, conf):
        self.client_id = self.generate_hashed_id()
        self.bizhawk_teams = {}
        self.sorted_teams = {}
        self.unsorted_teams = {}
        self.badges = {}
        self.editions = {}
        self.conf = conf
        self.host = host
        self.port = port
        self.is_connected = False
        self.obs = None
        self.writer_lock = asyncio.Lock()
        self.disconnect_lock = asyncio.Lock()

        self.logger = self.init_logging()

    def init_logging(self):
        logger = logging.getLogger(__name__)

        logging_formatter = logging.Formatter('[%(asctime)s] %(levelname)s: %(message)s')

        file_handler = logging.FileHandler('./logs/munchlax.log', 'w')
        file_handler.setFormatter(logging_formatter)
        logger.addHandler(file_handler)

        stream_handler = logging.StreamHandler(sys.stdout)
        stream_handler.setFormatter(logging_formatter)
        logger.addHandler(stream_handler)

        return logger

    async def alter_teams(self):
        reader = self.reader

        while True:
            try:
                length = int.from_bytes(await reader.read(4), 'big')
                msg = await reader.read(length)
                self.unsorted_teams = pickle.loads(msg)
                self.logger.info("alter_teams")
                self.logger.info(self.unsorted_teams)
                new_teams = self.unsorted_teams.copy()
                for player in new_teams:
                    team = new_teams[player]
                    self.logger.info(team)
                    new_teams[player] = self.sort(team[:6], self.conf['order'])
                    if player not in self.badges or self.unsorted_teams[player][6] != self.badges[player]:
                        self.badges[player] = self.unsorted_teams[player][6]
                    if player not in self.editions or self.unsorted_teams[player][7] != self.editions[player]:
                        self.editions[player] = self.unsorted_teams[player][7]
                    if self.obs:
                        await self.obs.change_badges(player) #type: ignore
                if new_teams != self.sorted_teams:
                    for player in new_teams:
                        if player not in self.sorted_teams:
                            if self.obs: 
                                await self.obs.changeSource(player, range(6), new_teams[player], self.editions[player]) #type: ignore
                            continue

                        diff = []
                        team = new_teams[player]
                        old_team = self.sorted_teams[player]
                        for i in range(6):
                            if team[i] != old_team[i]:
                                self.logger.debug(f"{i=},{team[i]=}")
                                diff.append(i)
                        if self.obs:
                            await self.obs.changeSource(player, diff, team, self.editions[player]) #type: ignore
                            await self.obs.change_badges(player) #type: ignore
                    self.sorted_teams = new_teams.copy()

            except Exception as err:
                self.logger.error(f"alter_teams abgebrochen: {err}")
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
            self.sorted_teams[team] = self.sort(self.unsorted_teams[team][:6], self.conf['order'])

    async def send_heartbeat(self):
        while True:
            try:
                await asyncio.sleep(5)
                async with self.writer_lock:
                    await self.send_message('heartbeat')
            except Exception as err:
                self.logger.warning(f"Heartbeat failed: {err}")
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
                    self.logger.warning(f"Teams senden failed: {err}")
                    break
            await asyncio.sleep(1)
        
        await self.disconnect() # type:ignore
    
    async def connect(self):
        self.logger.info(f"trying to connect munchlax to ({self.host}, {self.port})")
        self.reader, self.writer = await asyncio.open_connection(self.host, self.port)
        self.logger.info(f"Munchlax {self.client_id} bei Arceus({self.host},{self.port}) registriert")
        
        async with self.writer_lock:
            await self.send_message(self.client_id)
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

    async def send_message(self, message):
        serialized_message = pickle.dumps(message)
        length = len(serialized_message).to_bytes(4, 'big')
        self.writer.write(length)
        await self.writer.drain()
        self.writer.write(serialized_message)
        await self.writer.drain()

    async def receive_message(self):
        reader = self.reader
        message_length = int.from_bytes(await reader.read(4), 'big')
        message = await reader.read(message_length)

        return pickle.loads(message)
    
    def generate_hashed_id(self):
        random_id = os.urandom(16)
        
        hashed = hashlib.sha256(random_id).hexdigest()

        client_id = hashed
        return client_id