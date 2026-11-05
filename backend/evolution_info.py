"""Entwicklungs-Info (deutsche Einzeiler) für den Pokemon-Editor (ohne Kivy).

Daten: ``backend/data/evolutions_gen{1..7}.yml`` (generiert von
``tools/gen_evolutions.py``). Struktur: Quell-Dex -> Liste von Regeln.
"""
from __future__ import annotations

import os
import traceback

import yaml

from backend.logging_setup import get_logger

logger = get_logger(__name__, 'logs/pokemon_detail.log')

NO_EVOLUTION_TEXT = "Entwickelt sich nicht weiter"
_PREFIX = "Entwickelt sich "
_MAX_DEX = {1: 151, 2: 251, 3: 386, 4: 493, 5: 649, 6: 721, 7: 807}

# PokeAPI-Typ-IDs (nicht die In-Game-IDs)
_TYPES_DE = {
    1: "Normal", 2: "Kampf", 3: "Flug", 4: "Gift", 5: "Boden", 6: "Gestein", 7: "Käfer",
    8: "Geist", 9: "Stahl", 10: "Feuer", 11: "Wasser", 12: "Pflanze", 13: "Elektro",
    14: "Psycho", 15: "Eis", 16: "Drache", 17: "Unlicht", 18: "Fee",
}
_STATS_CMP = {1: "Angriff > Verteidigung", -1: "Angriff < Verteidigung", 0: "Angriff = Verteidigung"}

_cache: dict[int, dict[int, list[dict]]] = {}
_moves_cache: dict | None = None


def load_evolutions(gen: int) -> dict[int, list[dict]]:
    """Entwicklungs-LUT der Generation (gecacht); unbekannte Gen oder Fehler -> {}."""
    if gen in _cache:
        return _cache[gen]
    data: dict[int, list[dict]] = {}
    path = f"backend/data/evolutions_gen{gen}.yml"
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as fh:
                loaded = yaml.safe_load(fh)
            data = loaded if isinstance(loaded, dict) else {}
        except Exception as err:
            logger.error(f"{path} laden fehlgeschlagen: {err}")
            logger.error(traceback.format_exc())
    elif 1 <= gen <= 7:
        # Fehlt im Build sonst still ("Entwickelt sich nicht weiter")
        logger.warning(f"{path} fehlt — Entwicklungs-Info nicht verfuegbar")
    _cache[gen] = data
    return data


def _move_name(move_id: int) -> str:
    global _moves_cache
    if _moves_cache is None:
        try:
            with open("backend/data/moves_de.yml", encoding="utf-8") as fh:
                loaded = yaml.safe_load(fh)
            _moves_cache = loaded if isinstance(loaded, dict) else {}
        except Exception as err:
            logger.error(f"moves_de.yml laden fehlgeschlagen: {err}")
            _moves_cache = {}
    return str(_moves_cache.get(move_id, f"#{move_id}"))


def _species(dex: int, species_de: dict) -> str:
    return str(species_de.get(dex, f"#{dex}"))


def method_text(rule: dict, species_de: dict) -> str:
    """Methodenteil einer Regel, z.B. 'ab Lv. 16' oder 'mit Feuerstein' (ohne Ziel)."""
    trigger = rule.get("trigger")
    parts: list[str] = []
    level = rule.get("level")
    if trigger == "level" and level:
        base = f"ab Lv. {level}"
    elif trigger == "item":
        base = f"mit {rule.get('item', 'Item')}"
    elif trigger == "trade":
        base = "durch Tausch"
        if rule.get("trade_species"):
            parts.append(f"gegen {_species(int(rule['trade_species']), species_de)}")
    elif trigger == "happiness":
        base = "durch Freundschaft"
    elif trigger == "level" and rule.get("beauty"):
        base = "durch Schönheit"
    elif rule.get("location"):
        base = "durch Level-Up an besonderem Ort"
    elif rule.get("known_move"):
        base = f"durch Level-Up mit {_move_name(int(rule['known_move']))}"
    elif rule.get("party_species"):
        base = f"durch Level-Up mit {_species(int(rule['party_species']), species_de)} im Team"
    elif rule.get("party_type"):
        base = f"durch Level-Up mit {_TYPES_DE.get(int(rule['party_type']), 'Typ')}-Pokemon im Team"
    elif rule.get("move_type"):
        base = f"durch Level-Up mit Attacke vom Typ {_TYPES_DE.get(int(rule['move_type']), '?')}"
        if rule.get("affection"):
            parts.append("hohe Zuneigung")
    elif rule.get("rain"):
        base = "durch Level-Up bei Regen"
    elif rule.get("upside_down"):
        base = "durch Level-Up mit auf den Kopf gedrehtem Gerät"
    else:
        base = "durch Level-Up unter besonderen Bedingungen"
    if trigger == "level" and rule.get("beauty") and level:
        parts.append("hohe Schönheit")
    if rule.get("time"):
        parts.append("Tag" if rule["time"] == "day" else "Nacht")
    if rule.get("gender"):
        parts.append("weiblich" if rule["gender"] == "female" else "männlich")
    if rule.get("held_item"):
        parts.append(f"mit {rule['held_item']}")
    if "stats_cmp" in rule and rule["stats_cmp"] in _STATS_CMP:
        parts.append(_STATS_CMP[rule["stats_cmp"]])
    return f"{base} ({', '.join(parts)})" if parts else base


def evolution_text(dexnr: int, gen: int, species_de: dict,
                   rando_targets: list[int] | None = None) -> str:
    """Deutscher Einzeiler zur Entwicklung; '' bei unbekannter Gen/Dex-Nr.

    ``rando_targets``: Ziel-Dex-Nummern aus randomisierten Entwicklungen.
    """
    try:
        dex = int(dexnr)
    except (TypeError, ValueError):
        return ""
    max_dex = _MAX_DEX.get(gen)
    if max_dex is None or dex < 1 or dex > max_dex:
        return ""
    vanilla = load_evolutions(gen).get(dex, [])
    if rando_targets is None:
        if not vanilla:
            return NO_EVOLUTION_TEXT
        entries = [f"{method_text(rule, species_de)} zu {_species(int(rule['to']), species_de)}"
                   for rule in vanilla]
        return _PREFIX + "; ".join(entries)
    targets = [int(t) for t in rando_targets]
    if not targets:
        return NO_EVOLUTION_TEXT
    if vanilla and len(vanilla) == len(targets):
        entries = [f"{method_text(rule, species_de)} zu {_species(t, species_de)}"
                   for rule, t in zip(vanilla, targets)]
        return _PREFIX + "; ".join(entries)
    names = ", ".join(_species(t, species_de) for t in targets)
    if vanilla:
        return f"{_PREFIX}zu {names} (Methode wie Original: {method_text(vanilla[0], species_de)})"
    return f"{_PREFIX}zu {names}"
