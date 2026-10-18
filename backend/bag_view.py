"""Reine Anzeige-Logik für den Bag-Screen (KivyMD-Migration B).

Trennt Item-Namensauflösung, Filterung, Gruppierung und Pocket-Auswahl von der
GUI (``frontend/widgets/bagmenu.py`` klassisch / ``bagmenumd.py`` MD). Kein
Kivy-Import. Portiert die puren Teile aus der klassischen BagMenu 1:1, damit
beide UIs identisch rechnen und die Logik testbar ist.

Nicht hier (bleibt im Widget, weil an Munchlax/Emulator gekoppelt): DB-Query,
Rando-TM/HM-Lookup pro Spieler, Sonderbonbon-Schreiben, Sprite-Pfad-Bau.
"""
from __future__ import annotations

from pathlib import Path

import yaml

from backend.bag_decoder import POCKET_MAX_SLOTS
from backend.logging_setup import get_logger

logger = get_logger(__name__, 'logs/bagmenu.log')


# Generation -> Item-LUT-Datei (identisch zur Klassik; Gen 6/7 teilen items.yml).
_GEN_ITEM_LUT_FILE = {
    1: "items1.yml", 2: "items2.yml", 3: "items3.yml",
    4: "items4.yml", 5: "items5.yml", 6: "items.yml", 7: "items.yml",
}


def _load_item_luts() -> dict[int, dict]:
    luts: dict[int, dict] = {}
    for gen, fname in _GEN_ITEM_LUT_FILE.items():
        path = Path("backend/data") / fname
        try:
            with path.open(encoding="utf-8") as f:
                luts[gen] = yaml.safe_load(f) or {}
        except Exception as err:
            logger.warning(f"Item-LUT {fname} laden fehlgeschlagen: {err}")
            luts[gen] = {}
    return luts


_ITEM_LUTS = _load_item_luts()


POCKET_LABEL = {
    "tasche": "Tasche", "items": "Items", "keyitems": "Schlüssel-Items",
    "tmvm": "TM/VM", "medizin": "Medizin", "beeren": "Beeren",
    "baelle": "Bälle", "kampfitems": "Kampf-Items", "mailitems": "Mail-Items",
    "zkristalle": "Z-Kristalle", "pc": "PC",
}

# Pocket-Reihenfolge wie im Spielmenü.
_POCKET_ORDER = [
    "tasche", "items", "medizin", "baelle", "tmvm", "beeren",
    "keyitems", "kampfitems", "mailitems", "zkristalle", "pc",
]
_POCKET_INDEX = {p: i for i, p in enumerate(_POCKET_ORDER)}

_EDITION_NAME = {
    11: "Rot", 12: "Blau", 13: "Gelb",
    21: "Gold", 22: "Silber", 23: "Kristall",
    31: "Rubin", 32: "Saphir", 33: "Smaragd", 34: "Feuerrot", 35: "Blattgrün",
    41: "Diamant", 42: "Perl", 43: "Platin", 44: "Herzgold", 45: "Seelensilber",
    51: "Schwarz", 52: "Weiß", 53: "Schwarz 2", 54: "Weiß 2",
    61: "X", 62: "Y", 63: "Omega Rubin", 64: "Alpha Saphir",
    71: "Sonne", 72: "Mond", 73: "Ultra Sonne", 74: "Ultra Mond",
}


def safe_int(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def edition_name(edition) -> str | None:
    return _EDITION_NAME.get(safe_int(edition))


def edition_to_gen(edition_str) -> int | None:
    try:
        return int(edition_str) // 10
    except (TypeError, ValueError):
        return None


def item_slug(edition_str, item_id: int) -> str | None:
    """Roher Slug aus der items*.yml ('master-ball') oder None."""
    gen = edition_to_gen(edition_str)
    if gen is None:
        return None
    lut = _ITEM_LUTS.get(gen) or {}
    slug = lut.get(item_id)
    return str(slug) if slug else None


def item_display_name(edition_str, item_id: int) -> str:
    """'master-ball' -> 'Master Ball'; Fallback '#<id>'."""
    slug = item_slug(edition_str, item_id)
    if not slug:
        return f"#{item_id}"
    return " ".join(part.capitalize() for part in slug.split("-"))


def is_ball(edition_str, item_id: int) -> bool:
    """True, wenn der Item-Slug auf '-ball' endet (Nuzlocke-Ball-Regel)."""
    slug = item_slug(edition_str, item_id)
    return bool(slug and slug.endswith("-ball"))


def pretty_iso(ts: str | None) -> str:
    if not ts:
        return ""
    return str(ts).replace("T", " ")[:16]


def pocket_key_from_label(label: str) -> str | None:
    """Pocket-Anzeigename zurück auf den Pocket-Key ('Bälle' -> 'baelle')."""
    for key, value in POCKET_LABEL.items():
        if value == label:
            return key
    return None


def editions_for_player(rows: list[dict], player_filter: str) -> set[int]:
    editions: set[int] = set()
    for row in rows:
        if player_filter != "alle" and str(row.get("owner", "")) != player_filter:
            continue
        try:
            editions.add(int(row.get("edition")))
        except (TypeError, ValueError):
            continue
    return editions


def pocket_labels_for(rows: list[dict], player_filter: str) -> list[str]:
    """Pocket-Spinner-Werte: nur Pockets, die die aktiven Editionen laut
    POCKET_MAX_SLOTS (Decoder-Wahrheit) überhaupt unterstützen. Immer mit 'alle'.
    """
    editions = editions_for_player(rows, player_filter)
    if not editions:
        return ["alle"]
    keys: set[str] = set()
    for ed in editions:
        keys.update(POCKET_MAX_SLOTS.get(ed, {}).keys())
    ordered = sorted(keys, key=lambda k: _POCKET_INDEX.get(k, 99))
    return ["alle"] + [POCKET_LABEL.get(k, k) for k in ordered]


def filter_bag_rows(rows: list[dict], player_filter: str = "alle",
                    pocket_key: str | None = None, balls_only: bool = False,
                    query: str = "") -> list[dict]:
    """Filtert + sortiert die Bag-Zeilen (Port von BagMenu._apply_filters)."""
    query = (query or "").strip().lower()
    filtered: list[dict] = []
    for row in rows:
        if player_filter != "alle" and str(row.get("owner", "")) != player_filter:
            continue
        if pocket_key and row.get("pocket") != pocket_key:
            continue
        if balls_only and not is_ball(row.get("edition") or "", int(row.get("item_id") or 0)):
            continue
        if query:
            edition_str = row.get("edition") or ""
            item_id = row.get("item_id") or 0
            name = item_display_name(edition_str, item_id).lower()
            haystack = f"{name} {item_id} {edition_str}".lower()
            if query not in haystack:
                continue
        filtered.append(row)

    filtered.sort(key=lambda r: (
        str(r.get("owner") or ""),
        str(r.get("edition") or ""),
        _POCKET_INDEX.get(r.get("pocket"), 99),
        int(r.get("item_id") or 0),
    ))
    return filtered


def group_bag(rows: list[dict]) -> list[dict]:
    """Gruppiert gefilterte Zeilen zu Sektionen pro (owner, edition).

    Rückgabe: Liste von Sektionen (nach owner/edition sortiert), jede als
    ``{owner, edition, edition_name, pockets: [{key, label, items: [...]}, ...]}``
    mit Pockets in Spielmenü-Reihenfolge. Fertig für den Renderer.
    """
    groups: dict[tuple[str, str], dict[str, list[dict]]] = {}
    for row in rows:
        key = (str(row.get("owner") or ""), str(row.get("edition") or ""))
        pocket = str(row.get("pocket") or "")
        groups.setdefault(key, {}).setdefault(pocket, []).append(row)

    sections: list[dict] = []
    for owner, edition in sorted(groups.keys()):
        pockets = groups[(owner, edition)]
        pocket_keys = sorted(pockets.keys(), key=lambda k: _POCKET_INDEX.get(k, 99))
        sections.append({
            "owner": owner,
            "edition": edition,
            "edition_name": edition_name(edition),
            "pockets": [
                {"key": pk, "label": POCKET_LABEL.get(pk, pk), "items": pockets[pk]}
                for pk in pocket_keys
            ],
        })
    return sections
