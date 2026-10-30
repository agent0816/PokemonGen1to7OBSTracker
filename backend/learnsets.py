"""Attacken-Pool für den Pokemon-Editor (ohne Kivy).

Pool = Level-Up-Attacken der Spezies UND ihrer Vorstufen mit Lern-Level ≤
aktuellem Level, plus die bereits bekannten Attacken. Datenquelle:
- Vanilla: backend/data/learnsets_<versiongroup>.yml, move_pp.yml,
  pre_evolutions.yml (generiert von tools/gen_learnsets.py aus PokeAPI)
- Randomizer-Run: UPR-Log-Abschnitte "Pokemon Movesets", "Randomized
  Evolutions" und "Move Updates" (``RandomizerLogData``), falls vorhanden

Formen-spezifische Learnsets (Deoxys, Wormadam, Alola …) sind nicht abgebildet:
es gilt immer das Learnset der Standardform.
"""
from __future__ import annotations

import re

import yaml

from backend.logging_setup import get_logger

logger = get_logger(__name__, './logs/pokemon_editor.log')

# Tracker-Edition -> Version-Group-Kürzel (Dateiname learnsets_<kürzel>.yml)
EDITION_VERSION_GROUP: dict[int, str] = {
    11: "rb", 12: "rb", 13: "y",
    21: "gs", 22: "gs", 23: "c",
    31: "rs", 32: "rs", 33: "e", 34: "frlg", 35: "frlg",
    41: "dp", 42: "dp", 43: "pt", 44: "hgss", 45: "hgss",
    51: "bw", 52: "bw", 53: "b2w2", 54: "b2w2",
    61: "xy", 62: "xy", 63: "oras", 64: "oras",
    71: "sm", 72: "sm", 73: "usum", 74: "usum",
}
# Fallback für unbekannte Editionen (ROM-Hacks o.ä.) pro Generation
_GEN_FALLBACK_VG = {1: "y", 2: "c", 3: "e", 4: "pt", 5: "b2w2", 6: "oras", 7: "usum"}

# Alte deutsche Attackennamen aus Gen-1/2-ROMs, die von moves_de.yml (aktuelle
# Namen) abweichen. Gefunden in UPR-Logs deutscher Blau-/Silber-ROMs.
_OLD_DE_MOVE_NAMES = {
    "PFUND": 1,         # Klaps
    "SCHNABEL": 64,     # Pikser
    "KONFUSTRAHL": 109,  # Konfusstrahl
    "GIFTBLICK": 137,   # Schlangenblick
    "STAFFETTE": 226,   # Stafette
    "GESICHTE": 248,    # Seher
}

_learnsets: dict[str, dict[int, list[list[int]]]] = {}
_move_pp: dict[int, dict[int, int]] | None = None
_pre_evolutions: dict[int, int] | None = None
_move_name_map: dict[str, int] | None = None


def _load_yaml(path: str) -> dict:
    loader = getattr(yaml, "CSafeLoader", yaml.SafeLoader)
    try:
        with open(path, encoding="utf-8") as f:
            return yaml.load(f, Loader=loader) or {}
    except Exception as err:
        logger.error(f"{path} laden fehlgeschlagen: {err}")
        return {}


def generation(edition: int) -> int:
    return int(edition) // 10


def version_group(edition: int) -> str | None:
    vg = EDITION_VERSION_GROUP.get(int(edition))
    if vg is None:
        vg = _GEN_FALLBACK_VG.get(generation(edition))
    return vg


def _vanilla_learnset(edition: int, dexnr: int) -> list[tuple[int, int]]:
    vg = version_group(edition)
    if vg is None:
        return []
    if vg not in _learnsets:
        _learnsets[vg] = {int(k): v for k, v in _load_yaml(f"backend/data/learnsets_{vg}.yml").items()}
    return [(int(lvl), int(move)) for lvl, move in _learnsets[vg].get(int(dexnr), [])]


def _normalize_name(name: str) -> str:
    return re.sub(r"[^A-Z0-9ÄÖÜ]", "", name.upper().replace("ß", "SS"))


def resolve_move_name(name: str) -> int | None:
    """Attacken-ID zu einem Namen aus dem Rando-Log (DE aktuell/alt oder EN)."""
    global _move_name_map
    if _move_name_map is None:
        mapping: dict[str, int] = {}
        for move_id, de_name in _load_yaml("backend/data/moves_de.yml").items():
            if int(move_id) > 0:
                mapping[_normalize_name(str(de_name))] = int(move_id)
        for en_name, move_id in _load_yaml("backend/data/moves_en_reverse.yml").items():
            mapping[_normalize_name(str(en_name))] = int(move_id)
        for old_name, move_id in _OLD_DE_MOVE_NAMES.items():
            mapping[_normalize_name(old_name)] = move_id
        _move_name_map = mapping
    return _move_name_map.get(_normalize_name(name))


def levelup_moves(edition: int, dexnr: int, rando_data=None) -> list[tuple[int, int]]:
    """[(lern_level, move_id)] — Rando-Moveset, falls im Log, sonst Vanilla."""
    rando_pokemon = rando_data.pokemon.get(dexnr) if rando_data is not None else None
    if rando_pokemon is not None and rando_pokemon.levelup_moves:
        out = []
        for lvl, name in rando_pokemon.levelup_moves:
            move_id = resolve_move_name(name)
            if move_id is None:
                logger.warning(f"Rando-Moveset #{dexnr}: Attacke '{name}' unbekannt, übersprungen")
                continue
            out.append((lvl, move_id))
        return out
    return _vanilla_learnset(edition, dexnr)


def pre_evolutions(dexnr: int, rando_data=None) -> list[int]:
    """Alle Vorstufen (nächste zuerst). Bei randomisierten Entwicklungen aus dem Log."""
    global _pre_evolutions
    parent_of: dict[int, int]
    if rando_data is not None and getattr(rando_data, "evolutions_randomized", False):
        parent_of = {}
        for parent_id, evo_names in rando_data.evolutions.items():
            for evo_name in evo_names:
                child_id = rando_data.pokemon_name_to_id.get(evo_name.lower())
                if child_id is not None:
                    parent_of.setdefault(child_id, parent_id)
    else:
        if _pre_evolutions is None:
            _pre_evolutions = {int(k): int(v) for k, v in _load_yaml("backend/data/pre_evolutions.yml").items()}
        parent_of = _pre_evolutions

    chain: list[int] = []
    current = int(dexnr)
    while current in parent_of and parent_of[current] not in chain and parent_of[current] != int(dexnr):
        current = parent_of[current]
        chain.append(current)
    return chain


def move_pool(edition: int, dexnr: int, level: int, known_moves=(), rando_data=None) -> list[int]:
    """Wählbare Attacken-IDs: Level-Up ≤ ``level`` von Spezies + Vorstufen,
    plus ``known_moves``. Sortiert nach Lern-Level, dann ID; ohne Duplikate."""
    best_level: dict[int, int] = {}
    for species in [int(dexnr)] + pre_evolutions(dexnr, rando_data):
        for lvl, move_id in levelup_moves(edition, species, rando_data):
            if lvl <= level and move_id > 0:
                best_level[move_id] = min(lvl, best_level.get(move_id, lvl))
    for move_id in known_moves:
        if move_id:
            best_level.setdefault(int(move_id), 0)
    return sorted(best_level, key=lambda m: (best_level[m], m))


def base_pp(edition: int, move_id: int, rando_data=None) -> int:
    """Basis-AP einer Attacke in der Generation der Edition (Rando-"Move Updates" zuerst)."""
    global _move_pp
    if rando_data is not None:
        for update in getattr(rando_data, "move_updates", []):
            if "pp" in update.changes and resolve_move_name(update.name) == int(move_id):
                return int(update.changes["pp"])
    if _move_pp is None:
        _move_pp = {int(g): {int(m): int(pp) for m, pp in moves.items()}
                    for g, moves in _load_yaml("backend/data/move_pp.yml").items()}
    pp = _move_pp.get(generation(edition), {}).get(int(move_id))
    if pp is None:
        logger.warning(f"Basis-AP für Attacke {move_id} (Gen {generation(edition)}) unbekannt, nutze 5")
        return 5
    return pp
