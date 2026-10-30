"""Kivy-freie Logik für den Pokemon-Editor-Dialog (Level + Attacken).

Pool-Berechnung, Validierung, Stat-Vorschau und Aufbau des ``PokemonEdit``
liegen hier, damit sie ohne GUI testbar sind. Der Dialog selbst:
``frontend/widgets/pokemon_editor_md.py``.
"""
from __future__ import annotations

from backend import learnsets
from backend.pokemon_detail_view import get_species_personal
from backend.pokemon_encoder import PokemonEdit, is_supported
from backend.stat_calc import calc_stats_gen3plus

LEVEL_MIN = 1
LEVEL_MAX = 100
MOVE_SLOTS = 4

STAT_PREVIEW_ORDER = [
    ("hp", "KP"), ("attack", "Ang"), ("defense", "Ver"),
    ("special_attack", "SpAng"), ("special_defense", "SpVer"), ("speed", "Init"),
]


def clamp_level(value) -> int | None:
    """Text/Zahl → Level 1–100 (geklemmt); None bei unlesbarer Eingabe."""
    try:
        level = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return max(LEVEL_MIN, min(LEVEL_MAX, level))


def current_move_ids(pokemon) -> list[int]:
    """Die 4 aktuellen Attacken-IDs (0 = leer) aus ``pokemon.moves`` (Dict-Liste)."""
    ids: list[int] = []
    for move in (getattr(pokemon, "moves", None) or [])[:MOVE_SLOTS]:
        ids.append(int(move.get("id", 0) or 0) if isinstance(move, dict) else 0)
    ids += [0] * (MOVE_SLOTS - len(ids))
    return ids


def move_choices(edition: int, dexnr: int, level: int, selected_moves: list[int],
                 rando_data=None) -> list[int]:
    """Wählbare Attacken für das gewählte Level. Aktuell gewählte Attacken bleiben
    enthalten (known_moves), auch wenn sie bei niedrigerem Level nicht mehr im
    Level-Up-Pool liegen."""
    known = [m for m in selected_moves if m]
    return learnsets.move_pool(edition, dexnr, level, known, rando_data)


def validate_moves(moves: list[int]) -> str | None:
    """Fehlertext oder None. Keine Duplikate, mindestens eine Attacke."""
    chosen = [m for m in moves if m]
    if not chosen:
        return "Mindestens eine Attacke wählen."
    if len(set(chosen)) != len(chosen):
        return "Attacken dürfen nicht doppelt vorkommen."
    return None


def build_edit(level: int, moves: list[int]) -> PokemonEdit:
    """``PokemonEdit`` mit genau 4 Attacken-IDs."""
    padded = (list(moves) + [0] * MOVE_SLOTS)[:MOVE_SLOTS]
    return PokemonEdit(new_level=int(level), moves=[int(m) for m in padded])


def preview_stats(edition: int, dexnr: int, level: int, ivs: dict, evs: dict,
                  nature: int, rando_data=None) -> dict[str, int] | None:
    """Neue Statuswerte (Gen 3+). None, wenn Basiswerte/Daten fehlen."""
    try:
        stats = None
        rando_pokemon = rando_data.pokemon.get(dexnr) if rando_data is not None else None
        if rando_pokemon is not None and rando_pokemon.stats:
            stats = rando_pokemon.stats
        if not stats:
            stats = (get_species_personal(edition).get(dexnr) or {}).get("stats")
        if not stats or not isinstance(ivs, dict) or not isinstance(evs, dict):
            return None
        return calc_stats_gen3plus(stats, ivs, evs, int(nature or 0), int(level), dexnr)
    except (TypeError, ValueError, KeyError, AttributeError):
        return None


def format_stats(stats: dict[str, int]) -> str:
    """Einzeiler 'KP 45 · Ang 30 · …' für die Vorschau."""
    return " · ".join(f"{label} {stats.get(key, 0)}" for key, label in STAT_PREVIEW_ORDER)


def find_party_slot(team, personality) -> int | None:
    """RAM-Party-Slot (Index in ``unsorted_teams``) zur ``personality``."""
    for index, pokemon in enumerate(team or []):
        if pokemon is not None and getattr(pokemon, "personality", None) == personality:
            return index
    return None


def edit_allowed(edition, is_boxed: bool, dexnr, remote: bool, has_bizhawk: bool) -> bool:
    """Ob der Bearbeiten-Button aktiv sein darf."""
    if remote or not has_bizhawk or dexnr in ("egg", 0, "", None):
        return False
    return is_supported(edition, is_boxed)
