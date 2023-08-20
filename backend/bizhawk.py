import logging
import sys
import asyncio
from backend.munchlax import Munchlax
import backend.pokedecoder as pokedecoder
from kivy.event import EventDispatcher
from kivy.properties import DictProperty

class Bizhawk(EventDispatcher):
    def __init__(self, host, port):
        super().__init__()
        self.host = host
        self.port = port
        self.bizhawks = {}
        self.bizhawks_status = DictProperty()
        self.server = None
        self.is_connected = False

        self.logger = self.init_logging()

    def init_logging(self):
        logger = logging.getLogger(__name__)

        logging_formatter = logging.Formatter('[%(asctime)s] %(levelname)s: %(message)s')

        file_handler = logging.FileHandler('./logs/bizhawk.log', 'w')
        file_handler.setFormatter(logging_formatter)
        logger.addHandler(file_handler)

        stream_handler = logging.StreamHandler(sys.stdout)
        stream_handler.setFormatter(logging_formatter)
        logger.addHandler(stream_handler)

        return logger
    
    async def handle_bizhawk(self, reader, writer):
        client_id = None
        try:
            id_length = int((await reader.read(2)).decode())
            client_id = await reader.read(id_length)
            self.bizhawks[client_id.decode()] = writer
            self.bizhawks_status[client_id.decode()] = 'connected'
            self.logger.info(f"Emulator {client_id.decode()} connected.")

            def get_length():
                if edition < 20:
                    return 331
                elif edition < 30:
                    return 362
                elif edition < 40:
                    return 601
                elif edition < 50:
                    return 1418
                elif edition < 60:
                    return 1321

            def update_teams(team):
                team = pokedecoder.team(team, edition)
                teams = self.munchlax.bizhawk_teams
                if player in teams:
                    if teams[player] == team:
                        return
                teams[player] = team
                self.munchlax.unsorted_teams[player] = team

            edition_length = int((await reader.read(2)).decode())
            edition = int((await reader.read(edition_length)).decode())
            player = int(client_id.decode()[7:])

            length = get_length()
            msg = await reader.read(length)
            update_teams(msg)
            while True:
                try:
                    header = await reader.read(2)
                    if len(header) < 2:
                        continue
                    edition = header[0]
                    player = header[1]
                    length = get_length()
                    msg = await reader.read(length)
                    update_teams(msg)
                except Exception as err:
                    self.logger.error(f"handle_bizhawk abgebrochen: {err}")
                    self.logger.error(sys.exc_info())
                    break
        except Exception as err:
            self.logger.error(f"handle_bizhawk abgebrochen: {err}")
            self.logger.error(sys.exc_info())

        if client_id:
            del self.bizhawks[client_id.decode()]
            del self.bizhawks_status[client_id.decode()]

    async def start(self, munchlax):
        self.server = await asyncio.start_server(
            self.handle_bizhawk, self.host, self.port)

        self.munchlax: Munchlax = munchlax

        async with self.server:
            await self.server.serve_forever()
    
    async def stop(self):
        if self.server:
            self.server.close()
            await self.server.wait_closed()
            self.server = None
            self.connected = False
            self.logger.info("Bizhawk has been stopped.")

