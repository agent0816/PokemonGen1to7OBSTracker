"""Reine Anzeige-Logik für den Encounter-Screen (KivyMD-Migration B2).

Trennt die Aufbereitung der Encounter-Daten (flache Routen-Liste + Soullink-
Gruppierung) von der GUI, damit der MD-Screen (``frontend/widgets/encountermenumd.py``)
nur noch rendert. Kein Kivy-Import.

Wiederverwendung aus ``backend.pokedex_view``: ``species_name``, ``location_name``,
``edition_to_gen``, ``row_slot`` — damit Routen-/Artnamen und Slot-Extraktion
überall identisch aufgelöst werden (siehe Memory ``feedback_owner_slot_extract``).

Soullink-Gruppierung greift nur in den Linking-Modi ``coop``/``versus``
(``SOULLINK_MODES`` in ``arceus.py``); sonst flache Liste wie in der Klassik.
"""
from __future__ import annotations

import traceback

from backend.classes.pokedex_db import LEGACY_GIFT_ROUTE_MARKER, PokedexDB
from backend.encounter_edit import parse_locks
from backend.logging_setup import get_logger
from backend.pokedex_view import (
    edition_to_gen,
    location_name,
    row_slot,
    species_name,
    _load_locations,
)

logger = get_logger(__name__, 'logs/encountermenu.log')

# Nur diese Modi erzeugen Link-Groups (Spiegel von arceus.SOULLINK_MODES).
# Bewusst hier dupliziert, damit das reine View-Modul keine Server-Klasse
# (arceus) importieren muss.
SOULLINK_MODES = ("coop", "versus")

_EDITION_NAME = {
    31: "Rubin", 32: "Saphir", 33: "Smaragd", 34: "Feuerrot", 35: "Blattgrün",
    41: "Diamant", 42: "Perl", 43: "Platin", 44: "HeartGold", 45: "SoulSilver",
    51: "Schwarz", 52: "Weiß", 53: "Schwarz 2", 54: "Weiß 2",
    61: "X", 62: "Y", 63: "Omega Rubin", 64: "Alpha Saphir",
    71: "Sonne", 72: "Mond", 73: "Ultra Sonne", 74: "Ultra Mond",
}

STATUS_TEXT = {
    "caught":     "gefangen",
    "obtained":   "erhalten",
    "not_caught": "verpasst",
    "unknown":    "offen",
    "dupes":      "dupe",
    "open":       "offen",
    "dead":       "tot",
}

# Aggregat-Zustände einer Soullink-Gruppe (analog arceus._compute_link_state).
LINK_STATE_TEXT = {
    "complete": "komplett",
    "pending":  "offen",
    "failed":   "fehlgeschlagen",
}

_CAUGHT_LIKE = frozenset({"caught", "obtained"})


def is_soullink(nuz: dict | None) -> bool:
    """True, wenn die Session in einem Linking-Modus (coop/versus) läuft."""
    if not nuz:
        return False
    return (nuz.get("run_mode") or "nuzlocke") in SOULLINK_MODES


def edition_label(edition: int) -> str:
    """'<id> (<Name>)' bzw. '<id>' für den Edition-Filter."""
    try:
        ed = int(edition)
    except (TypeError, ValueError):
        return str(edition)
    name = _EDITION_NAME.get(ed)
    return f"{ed} ({name})" if name else str(ed)


def determine_status(enc: dict) -> str:
    """Status-Schlüssel eines Encounters (tot > dupes > outcome > unknown)."""
    if enc.get("dead", 0):
        return "dead"
    if enc.get("is_dupes_skip", 0):
        return "dupes"
    outcome = enc.get("outcome", "unknown")
    return outcome if outcome in STATUS_TEXT else "unknown"


def locations_for_edition(edition: int) -> list[int]:
    """Alle Routen-IDs, die in dieser Edition vorkommen (für offene Routen)."""
    gen = edition_to_gen(edition)
    locs = _load_locations().get(gen, {})
    result: list[int] = []
    for loc_id, loc_data in locs.items():
        if not isinstance(loc_data, dict):
            continue
        if edition in loc_data.get("games", []):
            try:
                result.append(int(loc_id))
            except (TypeError, ValueError):
                continue
    return sorted(result)


def _route_sort_key(route: int, is_open: bool) -> tuple[int, int, int]:
    """Starter (-1) oben, reguläre Routen (>=0) mittig, Sondermarker (<-1) unten.

    Port von encountermenu._sort_key: LEGACY_GIFT_ROUTE_MARKER und künftige
    negative Marker dürfen nicht als kleinste Zahlen vor die regulären Routen
    rutschen.
    """
    if route == -1:
        bucket = 0
    elif route >= 0:
        bucket = 1
    else:
        bucket = 2
    return (bucket, route, 1 if is_open else 0)


def build_encounter_rows(
    encounters: list[dict],
    species_de: dict,
    edition_filter: int | None = None,
    player_filter: str = "alle",
    query: str = "",
    show_deleted: bool = False,
) -> tuple[list[dict], int, int]:
    """Flache Routen-/Encounter-Liste (wie Klassik).

    Gibt ``(rows, enc_count, open_count)`` zurück. ``rows`` ist nach
    Starter/Route/Marker-Buckets sortiert. Bei gesetztem Edition-Filter werden
    noch nicht getroffene Routen als ``is_open``-Platzhalter ergänzt.
    Gelöschte Encounter (``deleted=1``) werden nur mit ``show_deleted`` gezeigt
    und zählen sonst nicht als getroffene Route.
    """
    query = (query or "").strip().lower()
    encountered_routes: dict[int, list[dict]] = {}
    for enc in encounters:
        if enc.get("deleted", 0) and not show_deleted:
            continue
        if player_filter and player_filter != "alle":
            if str(enc.get("owner", "") or "") != player_filter:
                continue
        if edition_filter is not None:
            try:
                if int(enc.get("edition", 0)) != edition_filter:
                    continue
            except (TypeError, ValueError):
                continue
        try:
            route = int(enc.get("route", 0))
        except (TypeError, ValueError):
            continue
        encountered_routes.setdefault(route, []).append(enc)

    rows: list[dict] = []
    for route, encs in encountered_routes.items():
        for enc in encs:
            try:
                enc_edition = int(enc.get("edition", 0))
            except (TypeError, ValueError):
                enc_edition = 0
            rows.append({
                "route": route,
                "location": location_name(route, enc_edition),
                "species": species_name(enc.get("dexnr", 0), species_de),
                "dexnr": enc.get("dexnr", 0),
                "edition": enc_edition,
                "lvl": enc.get("lvl", ""),
                "shiny": bool(enc.get("shiny", 0)),
                "female": bool(enc.get("female", 0)),
                "form": enc.get("form") or "",
                "method": enc.get("method", "wild"),
                "owner": str(enc.get("owner", "") or ""),
                "status": determine_status(enc),
                "is_open": False,
                "personality": enc.get("personality"),
                "manual_fields": str(enc.get("manual_fields") or ""),
                "locks": parse_locks(enc.get("manual_fields")),
                "deleted": bool(enc.get("deleted", 0)),
                "is_first": enc.get("is_first", 0),
                "is_dupes_skip": enc.get("is_dupes_skip", 0),
                "is_shiny_override": enc.get("is_shiny_override", 0),
                "has_balls": enc.get("has_balls", 0),
                "outcome": enc.get("outcome") or "unknown",
                "dead": bool(enc.get("dead", 0)),
                # Rohwert (None = unbekannt) für den Bearbeiten-Dialog
                "female_raw": enc.get("female"),
            })

    if edition_filter is not None:
        for route_id in locations_for_edition(edition_filter):
            if route_id not in encountered_routes:
                rows.append({
                    "route": route_id,
                    "location": location_name(route_id, edition_filter),
                    "species": "—",
                    "dexnr": 0,
                    "edition": edition_filter,
                    "lvl": "",
                    "shiny": False,
                    "female": False,
                    "form": "",
                    "method": "—",
                    "owner": "",
                    "status": "open",
                    "is_open": True,
                    "personality": None,
                    "manual_fields": "",
                    "locks": set(),
                    "deleted": False,
                    "is_first": 0,
                    "is_dupes_skip": 0,
                    "is_shiny_override": 0,
                    "has_balls": 0,
                    "outcome": "unknown",
                    "dead": False,
                    "female_raw": None,
                })

    if query:
        rows = [
            r for r in rows
            if query in f"{r['route']} {r['location']} {r['species']}".lower()
        ]

    rows.sort(key=lambda r: _route_sort_key(r["route"], r["is_open"]))

    enc_count = sum(1 for r in rows if not r["is_open"])
    open_count = sum(1 for r in rows if r["is_open"])
    return rows, enc_count, open_count


def aggregate_link_status(statuses: list[str], expected_count: int) -> str:
    """Aggregat-Status einer Soullink-Gruppe (Spiegel von arceus._compute_link_state).

    pending, solange nicht alle erwarteten Mitglieder da sind oder eines noch
    offen ist; complete, wenn alle gefangen/erhalten; sonst failed.
    """
    if len(statuses) < max(expected_count, 1):
        return "pending"
    if any(s in ("unknown", "open") for s in statuses):
        return "pending"
    if all(s in _CAUGHT_LIKE for s in statuses):
        return "complete"
    return "failed"


def _as_int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


# Gültige Link-Member-Outcomes (Server-Werte, ohne Anzeige-Sonderfälle)
_MEMBER_OUTCOMES = frozenset({"caught", "obtained", "not_caught", "unknown"})


def _find_member_encounter(encounters: list[dict], root: str, personality) -> dict | None:
    """Passender (nicht gelöschter) Encounter: gleiche PV und Owner-Root."""
    if personality is None:
        # Ohne PV kein eindeutiger Treffer (sonst matcht None == None den
        # erstbesten Encounter des Spielers).
        return None
    for enc in encounters:
        if enc.get("deleted", 0):
            continue
        if enc.get("personality") != personality:
            continue
        if PokedexDB.owner_root(str(enc.get("owner", "") or "")) == root:
            return enc
    return None


def group_soullink(
    links: dict[int, dict],
    encounters: list[dict],
    species_de: dict,
    expected_count: int = 2,
    edition_filter: int | None = None,
    query: str = "",
) -> list[dict]:
    """Soullink-Gruppen aus den Server-Links (``Arceus.soullink_links``) aufbauen.

    Jedes Mitglied wird mit seinem Encounter (gleiche PV + Owner-Root) angereichert;
    fehlt der Encounter, zeigen die Link-Daten das Mitglied trotzdem. Nach
    Route-Buckets sortiert, Mitglieder nach Slot.
    """
    query = (query or "").strip().lower()
    result: list[dict] = []
    for key, link in (links or {}).items():
        if not isinstance(link, dict):
            continue
        link_id = link.get("link_id", key)
        route = _as_int(link.get("route", 0))
        members: list[dict] = []
        for root, mem in (link.get("members") or {}).items():
            mem = mem if isinstance(mem, dict) else {}
            personality = mem.get("personality")
            enc = _find_member_encounter(encounters, str(root), personality)
            dexnr = enc.get("dexnr", 0) if enc else mem.get("dexnr", 0)
            edition = _as_int(enc.get("edition") if enc else mem.get("edition", 0))
            outcome = mem.get("outcome")
            if outcome == "dead":
                status = "dead"
            elif enc:
                status = determine_status(enc)
            else:
                status = outcome if outcome in _MEMBER_OUTCOMES else "unknown"
            members.append({
                "slot": row_slot(enc) if enc else None,
                "owner": str(enc.get("owner", "") or "") if enc else str(root),
                "root_owner": str(root),
                "personality": personality,
                "species": species_name(dexnr, species_de),
                "dexnr": dexnr,
                "edition": edition,
                "lvl": enc.get("lvl", "") if enc else "",
                "shiny": bool(enc.get("shiny", 0)) if enc else False,
                "female": bool(enc.get("female", 0)) if enc else False,
                "form": (enc.get("form") or "") if enc else "",
                "status": status,
                "manual_fields": str(enc.get("manual_fields") or "") if enc else "",
                "locks": parse_locks(enc.get("manual_fields")) if enc else set(),
                "member_manual": bool(mem.get("manual")),
            })
        if edition_filter is not None and not any(
                m["edition"] == edition_filter for m in members):
            continue
        # Mitglieder nach Slot ordnen (None ans Ende), damit P1/P2/... stabil stehen
        members.sort(key=lambda m: (m["slot"] is None, m["slot"] if m["slot"] is not None else 0))

        edition = next((m["edition"] for m in members if m["edition"]), 0)
        state = link.get("state")
        if state in LINK_STATE_TEXT:
            agg = state
        else:
            agg = aggregate_link_status([m["status"] for m in members], expected_count)
        result.append({
            "link_id": link_id,
            "link_group_id": link_id,
            "route": route,
            "location": location_name(route, edition),
            "edition": edition,
            "members": members,
            "agg_status": agg,
            "manual": bool(link.get("manual")),
            "expected_owners": list(link.get("expected_owners") or []),
        })

    if query:
        def _match(group: dict) -> bool:
            hay = f"{group['route']} {group['location']}".lower()
            if query in hay:
                return True
            return any(query in m["species"].lower() for m in group["members"])
        result = [g for g in result if _match(g)]

    result.sort(key=lambda g: _route_sort_key(g["route"], False))
    return result


def unlinked_encounters(links: dict[int, dict], encounters: list[dict]) -> list[dict]:
    """Nicht gelöschte Encounter, die in keinem Link-Member stecken.

    Ein Encounter gilt als gruppiert, wenn ein Link-Member unter seinem Owner-Root
    dieselbe PV trägt (wie ``_find_member_encounter``). ``is_first`` ist egal.
    """
    linked: set[tuple] = set()
    for link in (links or {}).values():
        if not isinstance(link, dict):
            continue
        for root, mem in (link.get("members") or {}).items():
            pv = mem.get("personality") if isinstance(mem, dict) else None
            if pv is not None:
                linked.add((pv, str(root)))
    result: list[dict] = []
    for enc in encounters:
        if enc.get("deleted", 0):
            continue
        root = PokedexDB.owner_root(str(enc.get("owner", "") or ""))
        if (enc.get("personality"), root) in linked:
            continue
        result.append(enc)
    return result


def group_title(group: dict) -> str:
    """'Ort (Route N)' bzw. 'Route N' einer Gruppe."""
    loc = group.get("location", "")
    route = group.get("route", 0)
    return loc if loc == f"Route {route}" else f"{loc} (Route {route})"


def link_targets(groups: list[dict], root_owner: str,
                 exclude_link_id=None) -> list[tuple[str, int]]:
    """Zielgruppen fuer 'verschieben/hinzufuegen' eines Pokemon des Spielers ``root_owner``.

    Nur Gruppen, deren ``expected_owners`` den Spieler enthalten und die noch kein
    Mitglied dieses Spielers haben (Server lehnt sonst ab). Anzeige
    'Route X - Mitglieder'. Rueckgabe ``[(Text, link_id)]``.
    """
    result: list[tuple[str, int]] = []
    for group in groups:
        if exclude_link_id is not None and group.get("link_id") == exclude_link_id:
            continue
        if root_owner not in (group.get("expected_owners") or []):
            continue
        members = group.get("members") or []
        if any(m.get("root_owner") == root_owner for m in members):
            continue
        names = ", ".join(f"{m.get('root_owner')}: {m.get('species')}" for m in members)
        result.append((f"{group_title(group)} - {names or 'leer'}", group["link_id"]))
    return result
