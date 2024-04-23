import asyncio
import logging
import sys
import traceback
from backend.classes.citra import Citra
from backend.classes.munchlax import Munchlax
import backend.pokedecoder as pokedecoder

class CitraHandler:
    def __init__(self):
        self.citra_instance = Citra()

        # self.logger = self.init_logging()

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
    
    def is_connected(self):
        try:
            self.citra_instance.read_memory(0,1)
            return True
        except ConnectionResetError:
            return False

    async def start(self, munchlax, button):
        self.munchlax: Munchlax = munchlax
        
if __name__ == "__main__":
    pass