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

class CitraHandler:
    def __init__(self):
        self.citra_instance = Citra()
        self.is_connected = False
        self.player_number = None
        self.started = False

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
                new_data = b''
                new_data += self.read_team()
                new_data += self.read_badges()

                update_data = self.read_in_battle_stats()

                self.update_teams(new_data)
                self.update_stats(update_data)
                await asyncio.sleep(1)
            except ConnectionResetError:
                self.is_connected = False
            except Exception as err:
                self.logger.error(f"handle_citra abgebrochen: {type(err)},{err}")
                self.logger.error(f"{traceback.format_exc()}")
                self.is_connected = False

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

    def update_stats(self, stats: dict):
        if stats:
            team = self.munchlax.bizhawk_teams[self.player_number]
            for slot, statlist in stats.items():
                for key, value in statlist.items():
                    old_value_dict = team[slot].__dict__
                    old_value_dict[key] = value if old_value_dict[key] != value else old_value_dict[key]
            self.munchlax.unsorted_teams[self.player_number] = team

    def read_team(self):
        result = b''
        # Teamreihenfolge auslesen:
        team_pointer = self.citra_instance.read_memory(self.pointer["team_reihenfolge"], 25)
        self.number_of_team_pokemon = int.from_bytes(team_pointer[24:],'little')
        for i in range(6):
            read_address = int.from_bytes(team_pointer[i*4:i*4 + 4], 'little') + 0x40
            pokemon = self.citra_instance.read_memory(read_address, SLOT_DATA_SIZE)
            battle_stats = self.citra_instance.read_memory(read_address + SLOT_DATA_SIZE + STAT_DATA_OFFSET, STAT_DATA_SIZE)
            result += pokemon + battle_stats
        return result
    
    def read_in_battle_stats(self):
        result = {}
        battle_kind = int.from_bytes(self.citra_instance.read_memory(self.pointer["battle_kind"], 2), 'little')
        if battle_kind == self.pointer["trainer_value"]:
            read_address = self.pointer["kampf_trainer"]
        elif battle_kind == self.pointer["wild_value"]:
            read_address = self.pointer["kampf_wild"]
        else:
            read_address = None

        if read_address:
            offset = self.pointer["in_battle_stat_offset"]
            for index in range(self.number_of_team_pokemon):
                stats_dict = {}
                stat_bytes = self.citra_instance.read_memory(read_address + index * offset, 20)
                stats_dict['lvl'] = int.from_bytes(stat_bytes[16:17], 'little')
                stats_dict['max_hp'] = int.from_bytes(stat_bytes[6:8], 'little')
                stats_dict['cur_hp'] = int.from_bytes(stat_bytes[8:10], 'little')
                stats_dict['item'] = int.from_bytes(stat_bytes[10:12], 'little')
                result[index] = stats_dict

        return result
    
    def read_badges(self):
        result = b'\x00'
        read_address = self.pointer["badges"]
        if read_address:
            result = self.citra_instance.read_memory(read_address, 1)
        return result

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
        self.is_connected = False
        
if __name__ == "__main__":
    pass