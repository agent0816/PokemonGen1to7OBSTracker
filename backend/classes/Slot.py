import enum
from classes.Pokemon import Pokemon

class Extra(enum.Enum):
    Normal = 0
    Mega = 1
    Alola = 2
    Galar = 3
    Hisui = 4


class Slot:
    def __init__(self, id: int, pokemon: Pokemon, extra: Extra = Extra.Normal):
        self.id: int = id
        self.pokemon: Pokemon = pokemon
        self.extra: Extra = extra
        if self.pokemon.dexnr == -1:
            self.egg: bool = True
        else:
            self.egg: bool = False

    def isEgg(self) -> bool:
        return self.egg

    def getId(self) -> int:
        return self.id

    def getDexnr(self) -> int:
        return self.pokemon.dexnr

    def getShiny(self) -> bool:
        return self.pokemon.shiny

    def getExtra(self) -> Extra:
        return self.extra

    def setNickname(self, nickname: str):
        self.nickname: str = nickname

    def __repr__(self):
        return "Slot: " + str(self.id) + " | Pokemon: " + str(self.pokemon) + " Extra: " + str(self.extra)

    def __eq__(self, other):
        return self.id == other.id and self.pokemon == other.pokemon