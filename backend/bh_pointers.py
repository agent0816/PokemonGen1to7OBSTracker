"""Lädt die BizHawk-Pointer-YAMLs pro Generation und stellt sie keyed by gameversion bereit.

Die Lua-Seite (obsautomation.lua) erfragt den passenden Pointer-Satz während der
Handshake-Phase, damit neue ROMs oder Lokalisierungen rein auf Python-Seite
gepflegt werden können.
"""
import yaml


_POINTER_FILES = {
    1: "backend/data/pointer_gen1.yml",
    2: "backend/data/pointer_gen2.yml",
    3: "backend/data/pointer_gen3.yml",
    4: "backend/data/pointer_gen4.yml",
    5: "backend/data/pointer_gen5.yml",
}


def _load_all() -> dict[int, dict]:
    """Lädt alle Pointer-YAMLs und mergt sie zu einem Dict keyed by gameversion."""
    merged: dict[int, dict] = {}
    for path in _POINTER_FILES.values():
        with open(path) as file:
            data = yaml.safe_load(file) or {}
        merged.update(data)
    return merged


POINTERS: dict[int, dict] = _load_all()


def get_pointers(gameversion: int, language: int | None = None) -> dict:
    """Gibt den flachen Pointer-Satz für eine gameversion zurück.

    Sprachspezifische Überschreibungen (für Gen 3) werden aus dem optionalen
    ``languages``-Unterdict gemergt, sofern der passende language-Key existiert.
    """
    entry = POINTERS.get(gameversion)
    if entry is None:
        return {}

    result = {k: v for k, v in entry.items() if k != "languages"}
    lang_overrides = entry.get("languages", {})
    if language is not None and language in lang_overrides:
        result.update(lang_overrides[language])
    return result
