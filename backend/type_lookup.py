"""Lookup: Species-Dexnr → Typ1/Typ2.

Verwendet die species_personal_*.yml. Für Regular-Runs decken gen2to5 + gen6 + gen7
alle relevanten Dexnrn ab. Randomizer-Runs: Types werden hier NICHT aus dem
Randomizer-Log gelesen — dafür müsste dieser Lookup pro Session ausgetauscht
werden (offen, [[project-nuzlocke-encounter-tracking]]).
"""

from pathlib import Path

import yaml

from backend.logging_setup import get_logger

logger = get_logger(__name__, './logs/type_lookup.log')

_PERSONAL_FILES = (
    "backend/data/species_personal_gen1.yml",
    "backend/data/species_personal_gen2to5.yml",
    "backend/data/species_personal_gen6.yml",
    "backend/data/species_personal_gen7.yml",
)

_dex_to_types: dict[int, list[int]] | None = None


def _load() -> dict[int, list[int]]:
    global _dex_to_types
    if _dex_to_types is not None:
        return _dex_to_types
    merged: dict[int, list[int]] = {}
    for path in _PERSONAL_FILES:
        p = Path(path)
        if not p.exists():
            logger.debug(f"personal-file fehlt: {path}")
            continue
        try:
            with open(p, encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
        except Exception as err:
            logger.warning(f"personal-file laden failed {path}: {err}")
            continue
        for dexnr, entry in data.items():
            if not isinstance(entry, dict):
                continue
            types = entry.get("types")
            if not isinstance(types, list) or len(types) < 1:
                continue
            # Erstladung gewinnt (gen2to5 vor gen6/7-Override); hier reicht das,
            # weil Typ1/Typ2-Basis für die aktuelle Prüf-Ebene gleich ist.
            if dexnr not in merged:
                merged[dexnr] = types
    _dex_to_types = merged
    logger.info(f"Type-Lookup geladen: {len(merged)} Species")
    return merged


def first_type(dexnr) -> int | None:
    """Gibt Typ 1 als int-ID zurück, None wenn unbekannt."""
    try:
        d = int(dexnr)
    except (TypeError, ValueError):
        return None
    types = _load().get(d)
    if not types:
        return None
    return types[0]


def secondary_type(dexnr) -> int | None:
    try:
        d = int(dexnr)
    except (TypeError, ValueError):
        return None
    types = _load().get(d)
    if not types or len(types) < 2:
        return None
    return types[1]


_type_names_de: dict[int, str] | None = None


def type_name(type_id) -> str:
    global _type_names_de
    if _type_names_de is None:
        try:
            with open("backend/data/types_de.yml", encoding="utf-8") as f:
                _type_names_de = yaml.safe_load(f) or {}
        except Exception as err:
            logger.warning(f"types_de.yml laden failed: {err}")
            _type_names_de = {}
    if type_id is None:
        return "?"
    return _type_names_de.get(int(type_id), f"Typ{type_id}")
