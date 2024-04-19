from typing import Literal

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
        for key in kwargs.keys():
            self.__dict__[key] = kwargs.get(key)

    def __repr__(self):
        return f'<{self.dexnr}, {self.nickname}, lvl={self.lvl}, item={self.item}, hp={self.cur_hp}/{self.max_hp}>'

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
