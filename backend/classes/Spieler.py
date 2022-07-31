from classes.Game import Game
from classes.Slot import Slot
from classes.Pokemon import Pokemon

class Spieler:
    def __init__(self, id: int, game: Game, slots: list = [Slot(i, Pokemon(0)) for i in range(1, 7)]):
        self.id: int = id
        self.game: Game = game
        self.slots: list = slots

    def getSlot(self, slotId) -> Slot:
        return self.slots[slotId - 1]

    def setSlot(self, slot: Slot):
        self.slots[slot.getId() - 1] = slot

    def getId(self) -> int:
        return self.id

    def __repr__(self):
        return "Spieler: " + str(self.id) + " | Game: " + str(self.game.edition) + " | Slots: " + str(self.slots)

    def __eq__(self, other):
        return self.id == other.id