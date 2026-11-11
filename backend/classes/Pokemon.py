from typing import Literal
import traceback
from backend.logging_setup import get_logger

logger = get_logger(__name__, './logs/PokemonObjects.log')


def party_has_valid_pokemon(team, edition) -> bool:
    """True, wenn die Party (Slots 0-5) mindestens ein echtes Pokemon enthaelt.

    Readiness-Signal fuer den Bag-Refresh: vor dem Starter gibt es in keiner
    Gen Baelle, am Titelbild/Intro steht im RAM Muell. Leere Slots haben
    dexnr 0; Gen 3+ verlangt zusaetzlich eine passende Checksumme (ein
    komplett genullter Gen-3-Slot hat Checksumme 0 == 0, faellt aber ueber
    dexnr 0 raus). Eier zaehlen als gueltig.
    """
    try:
        edition_int = int(edition)
    except (TypeError, ValueError):
        edition_int = 0
    for pokemon in list(team or [])[:6]:
        if not isinstance(pokemon, Pokemon):
            continue
        if pokemon.dexnr in (0, None, '', '0'):
            continue
        if edition_int > 30 and (
                pokemon.checksum_given < 0
                or pokemon.checksum_given != pokemon.checksum_calculated):
            continue
        return True
    return False

class Pokemon:
    def __init__(self, dexnr: int, shiny: bool = False, female = False, form = '', **kwargs):
        self.dexnr: int | Literal['egg'] = dexnr
        self.nickname: str = kwargs.get('nickname', '')
        self.lvl = kwargs.get('lvl')
        self.shiny: bool = shiny
        self.female = female
        # form-Normalisierung: get_form() (pokedecoder.py:249) liefert bereits
        # str, aber alte Pickles/DB-Rows koennen int enthalten. Konkatenation
        # `str + int` in Sprite-Pfad-Builds (obs.get_sprite, overlay_server
        # _sprite_path) wirft sonst TypeError und killt silent den Redraw-Task.
        if isinstance(form, int):
            form = ""
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

    @property
    def identity_key(self) -> str | None:
        if self.dexnr == 0 or self.dexnr == 'egg':
            return None
        pv = getattr(self, 'personality', None)
        if pv is not None:
            return f"pid_{pv}"
        return None

    def obs_property_changed(self, other, sp):
        if sp["show_items"] and self.item != other.item:
            return False
        if sp["show_nicknames"] and self.nickname != other.nickname:
            return False
        if self.dexnr != other.dexnr:
            return False
        if self.shiny != other.shiny:
            return False
        if sp.get("show_hp_bars") and (self.cur_hp != other.cur_hp or self.max_hp != other.max_hp):
            return False
        if sp.get("show_status_effects"):
            if getattr(self, 'status', {}) != getattr(other, 'status', {}):
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
        if 'moves' in self.__dict__ and self.moves != other.moves:
            return False
        if 'evs' in self.__dict__ and self.evs != other.evs:
            return False
        if 'battle_stats' in self.__dict__ and self.battle_stats != other.battle_stats:
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
