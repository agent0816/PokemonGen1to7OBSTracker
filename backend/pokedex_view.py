"""Reine Darstellungs-/Filterlogik für den Pokedex-Screen (ohne Kivy)."""
from __future__ import annotations

import os
import traceback

import yaml

from backend.classes.pokedex_db import LEGACY_GIFT_ROUTE_MARKER, PokedexDB
from backend.logging_setup import get_logger

logger = get_logger(__name__, 'logs/pokedexmenu.log')

_SPECIES_PATH = "backend/data/Species_de.yaml"
_species_cache: dict | None = None


def load_species_de() -> dict:
    """Lädt die deutsche Species-LUT (einmal, danach aus dem Cache)."""
    global _species_cache
    if _species_cache is not None:
        return _species_cache
    try:
        with open(_SPECIES_PATH, encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
        _species_cache = data if isinstance(data, dict) else {}
    except Exception as err:
        logger.error(f"Species_de.yaml laden fehlgeschlagen: {err}")
        logger.error(traceback.format_exc())
        _species_cache = {}
    return _species_cache


def species_name(dexnr_text: str, species_de: dict) -> str:
    """Artname zur Dex-Nummer (Text); 'egg' -> Ei, unbekannt -> '#<nr>'."""
    if dexnr_text == "egg":
        return species_de.get(0, "Ei")
    try:
        return species_de.get(int(dexnr_text), f"#{dexnr_text}")
    except (ValueError, TypeError):
        return f"#{dexnr_text}"


def row_slot(row: dict) -> int | None:
    """Spieler-Slot (1-basiert) einer DB-Zeile, oder None.

    Der ``owner`` liegt je nach Herkunft als ``name_<slot>`` (build_owner),
    als reiner Slot-Int (Legacy-Team-DB) oder als numerischer String vor.
    Reuse von ``PokedexDB.owner_slot`` für das build_owner-Format
    (siehe Memory ``feedback_owner_slot_extract``: kein ``int(owner)``).
    """
    owner = row.get("owner")
    if isinstance(owner, int):
        return owner
    if owner is None:
        return None
    text = str(owner)
    slot = PokedexDB.owner_slot(text)
    if slot is not None:
        return slot
    return int(text) if text.isdigit() else None


def filter_rows(rows: list[dict], query: str, slot: str, shiny_only: bool, species_de: dict) -> list[dict]:
    """Filtert Pokedex-Zeilen. ``query`` lowercased/getrimmt, ``slot`` getrimmt.

    ``slot`` ist ein Spieler-Slot als String ("1".."4"); verglichen wird gegen
    ``row_slot`` (deckt ``name_<slot>`` und Legacy-numerische Owner ab). Ein
    nicht-numerischer ``slot`` deaktiviert den Slot-Filter.
    """
    slot_num: int | None = int(slot) if slot and slot.isdigit() else None
    result: list[dict] = []
    for row in rows:
        if shiny_only and not row.get("shiny"):
            continue
        if slot_num is not None and row_slot(row) != slot_num:
            continue
        if query:
            haystack = " ".join(str(row.get(field, "")) for field in ("nickname", "edition", "dexnr")).lower()
            haystack += " " + species_name(str(row.get("dexnr", "")), species_de).lower()
            if query not in haystack:
                continue
        result.append(row)
    return result


def format_row(row: dict, species_de: dict) -> list[str]:
    """Die 11 Spaltentexte (DexNr, Name, Spitzname, Lv., S, Edition, Slot, Item, Route, PV, Zuletzt)."""
    dexnr = str(row.get("dexnr", ""))
    raw_route = row.get("route")
    # STARTER_ROUTE (-1) als "Starter" anzeigen
    route_text = "Starter" if raw_route == -1 else str(raw_route or "")
    pv = row.get("personality")
    pv_text = f"{pv:08X}" if isinstance(pv, int) else ""
    last_seen = str(row.get("last_seen") or "")
    # ISO-Timestamp kürzen: "2026-04-17T13:23:35+00:00" -> "2026-04-17 13:23"
    pretty = last_seen.replace("T", " ")[:16]
    return [
        dexnr,
        species_name(dexnr, species_de),
        str(row.get("nickname") or ""),
        str(row.get("lvl") or ""),
        # ASCII statt Unicode-Stern (Roboto hat kein Glyph)
        "*" if row.get("shiny") else "",
        str(row.get("edition") or ""),
        str(row.get("owner") or ""),
        str(row.get("item") or ""),
        route_text,
        pv_text,
        pretty,
    ]


# --- Item-/Routen-Auflösung ---------------------------------------------------

_items_cache: dict[int, dict] = {}
_locations_cache: dict[int, dict] | None = None


def edition_to_gen(edition: int) -> int:
    """Generation zur Edition-ID (11–15→1 … 71–74→7), sonst 0.

    Gen 1/2 ergänzt (im Gegensatz zu encountermenu._edition_to_gen), damit
    item_name die richtige items{gen}.yml-LUT trifft statt still auf items.yml
    (andere ID-Welt) zu fallen.
    """
    if 11 <= edition <= 15:
        return 1
    if 21 <= edition <= 25:
        return 2
    if 31 <= edition <= 35:
        return 3
    if 41 <= edition <= 45:
        return 4
    if 51 <= edition <= 54:
        return 5
    if 61 <= edition <= 64:
        return 6
    if 71 <= edition <= 74:
        return 7
    return 0


def _load_items(gen: int) -> dict:
    """Item-LUT (id -> slug) der Generation; Fallback auf items.yml. Gecacht pro gen."""
    if gen in _items_cache:
        return _items_cache[gen]
    data: dict = {}
    for path in (f"backend/data/items{gen}.yml", "backend/data/items.yml"):
        if not os.path.exists(path):
            continue
        try:
            with open(path, encoding="utf-8") as fh:
                loaded = yaml.safe_load(fh)
            data = loaded if isinstance(loaded, dict) else {}
        except Exception as err:
            logger.error(f"{path} laden fehlgeschlagen: {err}")
            logger.error(traceback.format_exc())
        break
    _items_cache[gen] = data
    return data


def item_name(item_id: object, edition: int) -> str:
    """Lesbarer Item-Name (englischer Slug aufgehübscht); '' = kein Item, '#<id>' = unbekannt."""
    if item_id in (None, "", 0, "0"):
        return ""
    try:
        key = int(item_id)  # type: ignore[call-overload]
        if key == 0:
            return ""
        slug = _load_items(edition_to_gen(edition)).get(key)
        if not slug:
            return f"#{item_id}"
        return str(slug).replace("-", " ").title()
    except Exception as err:
        logger.debug(f"item_name fehlgeschlagen (item_id={item_id!r}, edition={edition}): {err}")
        return ""


def _load_locations() -> dict[int, dict]:
    global _locations_cache
    if _locations_cache is not None:
        return _locations_cache
    result: dict[int, dict] = {}
    for gen in (3, 4, 5, 6, 7):
        filename = f"encounter_locations_gen{gen}.yml"
        try:
            with open(f"backend/data/{filename}", encoding="utf-8") as fh:
                result[gen] = yaml.safe_load(fh) or {}
        except Exception as err:
            logger.error(f"{filename} laden fehlgeschlagen: {err}")
            logger.error(traceback.format_exc())
            result[gen] = {}
    _locations_cache = result
    return result


def location_name(route: object, edition: int) -> str:
    """Routen-/Ortsname zur Route-ID; Port von encountermenu._location_name."""
    try:
        if route == -1:
            return "Starter"
        if route == LEGACY_GIFT_ROUTE_MARKER:
            return "Gift (Legacy)"
        # Kein Cross-Gen-Fallback: MAPSEC-IDs kollidieren zwischen Generationen.
        loc = _load_locations().get(edition_to_gen(edition), {}).get(route)
        if isinstance(loc, dict):
            return loc.get("name", f"Route {route}")
    except Exception as err:
        logger.debug(f"location_name fehlgeschlagen: {err}")
    return f"Route {route}"
