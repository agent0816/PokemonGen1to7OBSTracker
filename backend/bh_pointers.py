"""Lädt die BizHawk-Pointer-YAMLs pro Generation und stellt sie keyed by gameversion bereit.

Die Lua-Seite (obsautomation.lua) erfragt den passenden Pointer-Satz während der
Handshake-Phase, damit neue ROMs oder Lokalisierungen rein auf Python-Seite
gepflegt werden können.
"""
import yaml

from backend.logging_setup import get_logger

_logger = get_logger("bh_pointers")


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


# map_id -> met_location (= mapLabelTextID = Encounter-Route-Key) für Gen 4.
# Keyed by gameversion (41-45), Inhalt {map_id: section}. Quelle + Beleg:
# backend/data/mapid_section_gen4.yml (aus pret/pokeplatinum). Gen 4 liest live
# nur die map_id (mapidoffset) — die Section fürs Kartenfeature kommt aus dieser
# statischen LUT (anders als Gen 3, wo regionMapSectionId direkt im RAM liegt).
_MAPID_SECTION_FILE = "backend/data/mapid_section_gen4.yml"


def _load_mapid_sections() -> dict[int, dict[int, int]]:
    try:
        with open(_MAPID_SECTION_FILE) as file:
            return yaml.safe_load(file) or {}
    except FileNotFoundError:
        # Fehlt die LUT (z. B. nicht als Data-File ins Nuitka-Onefile gepackt),
        # liefert der Getter überall None → Gen 4 zeigt keinen Karten-Marker.
        # Laut loggen, damit der stille Ausfall in der Fehlersuche auffällt.
        _logger.error(
            f"Gen-4-Section-LUT nicht gefunden: {_MAPID_SECTION_FILE} — "
            f"Kartenfeature ohne Section/Marker für Gen 4"
        )
        return {}


_MAPID_SECTIONS: dict[int, dict[int, int]] = _load_mapid_sections()


def has_map_sections(gameversion: int) -> bool:
    """True, wenn für die ``gameversion`` eine map_id→Section-Tabelle geladen ist."""
    return bool(_MAPID_SECTIONS.get(gameversion))


def get_map_section(gameversion: int, map_id: int) -> int | None:
    """Übersetzt eine Gen-4-``map_id`` in die Region-Map-Section (met_location).

    Gibt ``None`` zurück, wenn für die ``gameversion`` keine Tabelle existiert
    oder die ``map_id`` unbekannt ist (dann kein Marker/Highlight).
    """
    table = _MAPID_SECTIONS.get(gameversion)
    if not table:
        return None
    return table.get(map_id)


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
