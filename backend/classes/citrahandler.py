import asyncio
import logging
import sys
import traceback
import yaml
from backend.classes.citra import Citra
from backend.classes.munchlax import Munchlax
import backend.pokedecoder as pokedecoder

BLOCK_SIZE = 56
SLOT_OFFSET = 484
SLOT_DATA_SIZE = (8 + (4 * BLOCK_SIZE)) # 232
STAT_DATA_OFFSET = 112
STAT_DATA_SIZE = 22
BATTLE_STAT_OFFSET = 580

class CitraHandler:
    def __init__(self):
        self.citra_instance = Citra()
        self.is_connected = False
        self.player_number = None

        with open("backend/data/pointer_xy.yml") as file:
            pointer_xy = yaml.safe_load(file)
        with open("backend/data/pointer_oras.yml") as file:
            pointer_oras = yaml.safe_load(file)
        with open("backend/data/pointer_sm.yml") as file:
            pointer_sm = yaml.safe_load(file)
        with open("backend/data/pointer_usum.yml") as file:
            pointer_usum = yaml.safe_load(file)

        self.edition_lut = {
            "X": 61,
            "Y": 62,
            "Omega Rubin": 63,
            "Alpha Saphir": 64,
            "Sonne": 71,
            "Mond": 72,
            "Ultra Sonne": 73,
            "Ultra Mond": 74
        }
        self.pointer_lut = {
            61: pointer_xy,
            62: pointer_xy,
            63: pointer_oras,
            64: pointer_oras,
            71: pointer_sm,
            72: pointer_sm,
            73: pointer_usum,
            74: pointer_usum
        }

        self.logger = self.init_logging()

    def init_logging(self):
        logger = logging.getLogger(__name__)
        logger.setLevel(logging.INFO)

        logging_formatter = logging.Formatter('[%(asctime)s] %(levelname)s: %(message)s')

        file_handler = logging.FileHandler('./logs/citra.log', 'w')
        file_handler.setFormatter(logging_formatter)
        logger.addHandler(file_handler)

        stream_handler = logging.StreamHandler(sys.stdout)
        stream_handler.setFormatter(logging_formatter)
        logger.addHandler(stream_handler)

        return logger
    
    def check_connection(self):
        try:
            self.citra_instance.read_memory(0,1)
            self.is_connected = True
        except ConnectionResetError:
            self.is_connected = False

    async def handle_citra(self):
        while self.started and self.is_connected:
            try:
                await asyncio.sleep(1)
            except ConnectionResetError:
                self.is_connected = False
            except Exception:
                pass

        self.logger.info("Citra getrennt.")
        if self.started:
            self.start_button.trigger_action(0)

        self.player_number = None

    def update_teams(self, team):
        team = pokedecoder.team(team, self.edition)
        teams = self.munchlax.bizhawk_teams
        if self.player_number in teams:
            if teams[self.player_number] == team:
                return
        teams[self.player_number] = team
        self.munchlax.unsorted_teams[self.player_number] = team

    def update_stats(self, stats):
        team = self.munchlax.bizhawk_teams[self.player_number]
        # TODO: data structure for stats
        self.munchlax.unsorted_teams[self.player_number] = team

    def read_team(self):
        result = b''
        # Teamreihenfolge auslesen:
        team_pointer = self.citra_instance.read_memory(self.pointer["team_reihenfolge"], 25)
        number_of_team_pokemon = int.from_bytes(team_pointer[24:],'little')
        for i in range(6):
            read_address = int.from_bytes(team_pointer[i*4:i*4 + 4], 'little') + 0x40
            pokemon = self.citra_instance.read_memory(read_address, SLOT_DATA_SIZE)
            battle_stats = self.citra_instance.read_memory(read_address + SLOT_DATA_SIZE + STAT_DATA_OFFSET, STAT_DATA_SIZE)
            result += pokemon + battle_stats
        return result
    
    def read_stats(self):
        # TODO: implementing data structure 
        return {}
    
    def read_badges(self):
        return self.citra_instance.read_memory(self.pointer["badges"], 1)

    async def start(self, munchlax, button):
        self.munchlax: Munchlax = munchlax
        self.logger.info("Citra verbunden.")
        self.started = True

        self.start_button = button

        self.set_pointer()
        self.set_player_number()
        if not self.player_number:
            self.is_connected = False
        asyncio.create_task(self.handle_citra())

    def set_pointer(self):
        self.edition = self.edition_lut.get(self.munchlax.pl["session_game"], 0)

        self.pointer = self.pointer_lut[self.edition]

    def set_player_number(self):
        for i in range(1, self.munchlax.pl['player_count'] + 1):
            if not self.munchlax.pl[f"remote_{i}"]:
                self.player_number = i
        if self.player_number:
            self.munchlax.editions[self.player_number] = self.edition

    async def stop(self):
        self.started = False
        
if __name__ == "__main__":
    pass