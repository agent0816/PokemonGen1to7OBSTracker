import asyncio
import logging
import sys
import traceback
import yaml
from backend.classes.citra import Citra
from backend.classes.munchlax import Munchlax
import backend.pokedecoder as pokedecoder

class CitraHandler:
    def __init__(self):
        self.citra_instance = Citra()
        self.is_connected = False

        pointer_xy = yaml.safe_load("backen/data/pointer_xy.yml")
        pointer_oras = yaml.safe_load("backen/data/pointer_oras.yml")
        pointer_sm = yaml.safe_load("backen/data/pointer_sm.yml")
        pointer_usum = yaml.safe_load("backen/data/pointer_usum.yml")

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
            
            await asyncio.sleep(1)

        self.logger.info("Citra getrennt.")

    async def start(self, munchlax, button):
        self.munchlax: Munchlax = munchlax
        self.started = True
        self.logger.info("Citra verbunden.")

        self.get_pointer()
        
        asyncio.create_task(self.handle_citra())

    def get_pointer(self):
        edition = self.edition_lut.get(self.munchlax.pl["session_game"], 0)

        self.pointer = self.pointer_lut[edition]

    async def stop(self):
        self.started = False
        
if __name__ == "__main__":
    pass