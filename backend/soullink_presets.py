"""Loader für Soullink-Regel-Presets aus backend/data/soullink_presets.yml.

Anwendung:
    presets = load_presets()
    apply_preset(nuz_dict, presets["versus_2v2"])
    # nuz_dict enthält nun soullink_mode + alle rule_* Keys des Presets.
"""

from pathlib import Path

import yaml

from backend.logging_setup import get_logger

logger = get_logger(__name__, './logs/soullink_presets.log')

_PRESET_PATH = Path("backend/data/soullink_presets.yml")


def load_presets() -> dict:
    """Läd Presets-YAML. Bei Fehler leeres Dict + Log-Warning."""
    if not _PRESET_PATH.exists():
        logger.warning(f"Preset-Datei fehlt: {_PRESET_PATH}")
        return {}
    try:
        with open(_PRESET_PATH, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        if not isinstance(data, dict):
            logger.warning(f"Preset-Datei enthält kein Dict: {type(data)}")
            return {}
        return data
    except Exception as err:
        logger.error(f"Preset-Datei laden fehlgeschlagen: {type(err)},{err}")
        return {}


def apply_preset(nuz: dict, preset: dict) -> dict:
    """Wendet ein Preset auf ein nuzlocke.yml-Dict an (in-place).

    Setzt soullink_mode/player_count/link_strategy falls im Preset gesetzt.
    Überträgt alle rule_* Keys aus preset["rules"].
    Regel-Keys, die im Preset NICHT vorkommen, bleiben unverändert.
    """
    if not preset:
        return nuz
    for key in ("soullink_mode", "soullink_player_count", "soullink_link_strategy"):
        val = preset.get(key)
        if val is not None:
            nuz[key] = val
    rules = preset.get("rules") or {}
    for rule_key, rule_val in rules.items():
        if not rule_key.startswith("rule_"):
            logger.warning(f"Preset-Regel ohne rule_ Prefix ignoriert: {rule_key}")
            continue
        nuz[rule_key] = rule_val
    return nuz


def preset_choices(presets: dict) -> list[tuple[str, str]]:
    """Gibt [(preset_id, label)] für UI-Dropdown zurück."""
    out = []
    for pid, entry in presets.items():
        label = entry.get("label", pid) if isinstance(entry, dict) else pid
        out.append((pid, label))
    return out
