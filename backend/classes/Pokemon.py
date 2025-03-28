from typing import Literal
import logging
import traceback
import sys

def init_logging():
    logger = logging.getLogger(__name__)
    logger.setLevel(logging.INFO)

    logging_formatter = logging.Formatter('[%(asctime)s] %(levelname)s: %(message)s')

    file_handler = logging.FileHandler('./logs/PokemonObjects.log', 'w')
    file_handler.setFormatter(logging_formatter)
    logger.addHandler(file_handler)

    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(logging_formatter)
    logger.addHandler(stream_handler)

    return logger

logger = init_logging()

class Pokemon:
    def __init__(self, dexnr: int, shiny: bool = False, female = False, form = '', **kwargs):
        self.dexnr: int | Literal['egg'] = dexnr
        self.nickname: str = kwargs.get('nickname', '')
        self.lvl = kwargs.get('lvl')
        self.shiny: bool = shiny
        self.female = female
        self.form = form
        if 'route' not in kwargs:
            self.route = 0
        if 'item' not in kwargs:
            self.item = 0
        if 'cur_hp' not in kwargs:
            self.cur_hp = 1
        if 'max_hp' not in kwargs:
            self.max_hp = 1
        if 'checksum_given' not in kwargs:
            self.checksum_given = -1
        if 'checksum_calculated' not in kwargs:
            self.checksum_calculated = -1
        for key in kwargs.keys():
            self.__dict__[key] = kwargs.get(key)

        self.logger = logger

    def __repr__(self):
        try:
            representation: str = f'<{self.dexnr}, {self.nickname}, lvl={self.lvl}, item={self.item}, hp={self.cur_hp}/{self.max_hp}'
            if self.checksum_given >= 0 and self.checksum_calculated >= 0:
                representation += f', checksum={self.checksum_given}, checksum berechnet={self.checksum_calculated}'
            representation += ">"
        except AttributeError as err:
            self.logger.error(f"Pokemon.__repr__ failed: {type(err)},{err}")
            self.logger.error(f"{traceback.format_exc()}")
            representation = "there was an error."
        
        return representation

    def obs_property_changed(self, other, sp):
        if sp["show_items"] and self.item != other.item:
            return False
        if sp["show_nicknames"] and self.nickname != other.nickname:
            return False
        if self.dexnr != other.dexnr:
            return False
        if self.shiny != other.shiny:
            return False
        return True

    def __eq__(self, other) -> bool:
        if self.dexnr != other.dexnr:
            return False
        if self.nickname != other.nickname:
            return False
        if self.shiny != other.shiny:
            return False
        if self.item != other.item:
            return False
        if self.lvl != other.lvl:
            return False
        if self.cur_hp != other.cur_hp:
            return False
        return True

    def __lt__(self, other):
        if self.dexnr == 0:
            return False
        if other.dexnr == 0:
            return True
        if self.dexnr == 'egg':
            return False
        if other.dexnr == 'egg':
            return True
        return self.dexnr < other.dexnr
