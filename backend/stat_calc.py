"""Statuswert-Berechnung aus Basiswerten, IV/DV, EV/Stat-Exp, Wesen und Level.

Reine Funktionen ohne Kivy. Genutzt vom Pokemon-Editor, um nach einer
Level-Änderung die Party-Statuswerte (die das Spiel nur beim Level-Up oder
beim Ablegen/Holen aus der Box neu berechnet) selbst neu zu schreiben.

Formeln nach Bulbapedia ("Stat"):
- Gen 3+: HP = ⌊(2B + IV + ⌊EV/4⌋)·L/100⌋ + L + 10,
  sonst ⌊(⌊(2B + IV + ⌊EV/4⌋)·L/100⌋ + 5)·Wesen⌋ (Wesen 1.1/1.0/0.9).
  Ausnahme Ninjatask (#292): HP immer 1.
- Gen 1/2: S = min(255, ⌈√StatExp⌉) (Deckel 255 laut pokered-Disassembly
  CalcStat, Schleife bricht bei b == $FF ab; nicht live gegengeprüft), HP = ⌊((B+DV)·2 + ⌊S/4⌋)·L/100⌋ + L + 10,
  sonst ⌊((B+DV)·2 + ⌊S/4⌋)·L/100⌋ + 5. KP-DV wird aus den Low-Bits der
  übrigen DVs gebildet (Atk·8 + Def·4 + Init·2 + Spez).

Ausgabe-Schlüssel wie die Decoder-``battle_stats`` plus ``hp``:
hp, attack, defense, speed, special_attack, special_defense.
"""
from __future__ import annotations

import math

from backend.pokemon_detail_view import NATURE_AMPS

SHEDINJA = 292

# Reihenfolge der NATURE_AMPS-Spalten
_NATURE_STAT_ORDER = ["attack", "defense", "special_attack", "special_defense", "speed"]

# Rando-Log-Schlüssel (RandoPokemon.stats) → Personal-YAML-Schlüssel
_RANDO_TO_PERSONAL = {
    "hp": "hp", "atk": "attack", "def": "defense",
    "satk": "special_attack", "sdef": "special_defense", "spd": "speed",
}


def normalize_base_stats(stats: dict) -> dict[str, int]:
    """Bringt Basiswerte (Personal-YAML- oder Rando-Log-Schlüssel) auf
    Personal-Schlüssel (hp/attack/defense/special_attack/special_defense/speed)."""
    out: dict[str, int] = {}
    for key, value in (stats or {}).items():
        out[_RANDO_TO_PERSONAL.get(key, key)] = int(value)
    return out


def hp_dv_gen12(dvs: dict) -> int:
    """KP-DV aus den Low-Bits von Atk/Def/Init/Spez (Gen 1/2)."""
    return (((dvs.get("attack", 0) & 1) << 3) | ((dvs.get("defense", 0) & 1) << 2)
            | ((dvs.get("speed", 0) & 1) << 1) | (dvs.get("special", 0) & 1))


def _stat_exp_term(stat_exp: int) -> int:
    root = min(255, math.isqrt(max(0, stat_exp)))
    if root * root < stat_exp and root < 255:
        root += 1
    return root // 4


def calc_stats_gen12(base: dict, dvs: dict, stat_exp: dict, level: int) -> dict[str, int]:
    """Statuswerte Gen 1/2.

    ``base``: Personal-Schlüssel (Gen 1: special_attack == Spezial).
    ``dvs``: attack/defense/speed/special (0–15); ``hp`` wird ignoriert und
    aus den übrigen DVs abgeleitet.
    ``stat_exp``: hp/attack/defense/speed/special (0–65535).
    """
    b = normalize_base_stats(base)
    hp_dv = hp_dv_gen12(dvs)

    def _core(base_val: int, dv: int, sexp: int) -> int:
        return ((base_val + dv) * 2 + _stat_exp_term(sexp)) * level // 100

    special_exp = stat_exp.get("special", 0)
    special_dv = dvs.get("special", 0)
    return {
        "hp": _core(b.get("hp", 0), hp_dv, stat_exp.get("hp", 0)) + level + 10,
        "attack": _core(b.get("attack", 0), dvs.get("attack", 0), stat_exp.get("attack", 0)) + 5,
        "defense": _core(b.get("defense", 0), dvs.get("defense", 0), stat_exp.get("defense", 0)) + 5,
        "speed": _core(b.get("speed", 0), dvs.get("speed", 0), stat_exp.get("speed", 0)) + 5,
        "special_attack": _core(b.get("special_attack", 0), special_dv, special_exp) + 5,
        # Gen 2: SpVert nutzt Spezial-DV und Spezial-Stat-Exp, aber eigenen Basiswert
        "special_defense": _core(b.get("special_defense", b.get("special_attack", 0)), special_dv, special_exp) + 5,
    }


def calc_stats_gen3plus(base: dict, ivs: dict, evs: dict, nature: int, level: int,
                        dexnr: int | None = None) -> dict[str, int]:
    """Statuswerte Gen 3–7. ``ivs``/``evs`` mit Decoder-Schlüsseln."""
    b = normalize_base_stats(base)

    def _core(key: str) -> int:
        return (2 * b.get(key, 0) + ivs.get(key, 0) + evs.get(key, 0) // 4) * level // 100

    if dexnr == SHEDINJA:
        hp = 1
    else:
        hp = _core("hp") + level + 10
    out = {"hp": hp}
    amps = NATURE_AMPS[nature] if 0 <= nature < len(NATURE_AMPS) else [0] * 5
    for key, amp in zip(_NATURE_STAT_ORDER, amps):
        out[key] = (_core(key) + 5) * (100 + 10 * amp) // 100
    return out


def scale_cur_hp(old_cur: int, old_max: int, new_max: int) -> int:
    """Neue aktuelle KP nach Max-KP-Änderung: Schaden bleibt erhalten.

    Ein lebendes Pokemon bleibt lebendig (≥1), ein besiegtes bleibt bei 0 —
    sonst schlägt die Death-/Wipe-Erkennung falsch an.
    """
    if old_cur <= 0:
        return 0
    damage = max(0, old_max - old_cur)
    return max(1, min(new_max, new_max - damage))
