"""View-Logik für die Friedhof-Ansicht (Kivy-frei, testbar).

Daten: ``GraveyardDB.list_graves`` (über ``Munchlax.snapshot_graves``).
Hier: Filter, Soullink-Gruppierung, Anzeige-Texte und CSV-Export für das
Ende eines YouTube-Projekts.
"""
from __future__ import annotations

import csv
import io
import time
from collections import OrderedDict
from pathlib import Path

from backend.bag_view import edition_name
from backend.pokedex_view import location_name, species_name

CAUSE_LABELS = {
    "faint": "Kampf",
    "manual": "manuell markiert",
    "soullink_partner": "Soullink-Partner",
}

CSV_COLUMNS = [
    ("datum", "Datum"),
    ("session", "Projekt (Session)"),
    ("run_id", "Run"),
    ("owner_name", "Spieler"),
    ("owner_slot", "Slot"),
    ("team", "Team"),
    ("link_id", "Soullink-Gruppe"),
    ("partners", "Partner"),
    ("dexnr", "Dex-Nr."),
    ("species", "Pokémon"),
    ("nickname", "Spitzname"),
    ("form", "Form"),
    ("shiny", "Shiny"),
    ("female", "Weiblich"),
    ("level", "Level"),
    ("edition", "Edition"),
    ("route", "Fundort"),
    ("method", "Methode"),
    ("cause", "Ursache"),
]


def cause_label(cause: str | None) -> str:
    if not cause:
        return "unbekannt"
    return CAUSE_LABELS.get(cause, cause)


def format_ts(ts) -> str:
    try:
        return time.strftime("%d.%m.%Y %H:%M", time.localtime(float(ts)))
    except (TypeError, ValueError, OverflowError, OSError):
        return ""


def filter_graves(graves: list[dict], owner: str | None = None,
                  edition: int | None = None) -> list[dict]:
    """``None`` = kein Filter."""
    out = []
    for g in graves or []:
        if owner is not None and g.get("owner_name") != owner:
            continue
        if edition is not None and g.get("edition") != edition:
            continue
        out.append(g)
    return out


def filter_options(graves: list[dict]) -> dict:
    """Werte für die Filter-Dropdowns: Spieler (sortiert), Editionen (sortiert)."""
    owners = sorted({g.get("owner_name") for g in graves or [] if g.get("owner_name")})
    editions = sorted({g.get("edition") for g in graves or [] if g.get("edition") is not None})
    return {"owners": owners, "editions": editions}


def group_graves(graves: list[dict]) -> list[dict]:
    """Soullink-Gruppen zusammenfassen: gleiche (session, link_id) = eine Gruppe.

    Rückgabe in Todesreihenfolge (früheste Grab-Zeit der Gruppe zuerst):
    ``[{"link_id": int|None, "graves": [...], "first_ts": float}]``.
    Gräber ohne link_id sind Einzelgruppen.
    """
    groups: "OrderedDict[tuple, dict]" = OrderedDict()
    for g in sorted(graves or [], key=lambda x: (x.get("ts") or 0, x.get("id") or 0)):
        link_id = g.get("link_id")
        key = (g.get("session"), link_id) if link_id is not None else ("_single", g.get("id"), id(g))
        bucket = groups.get(key)
        if bucket is None:
            bucket = {"link_id": link_id, "graves": [], "first_ts": g.get("ts") or 0}
            groups[key] = bucket
        bucket["graves"].append(g)
    return list(groups.values())


def grave_row(g: dict, species_de: dict) -> dict:
    """Anzeige-Texte für eine Zeile/Karte."""
    dexnr = g.get("dexnr")
    species = species_name(str(dexnr), species_de) if dexnr not in (None, "") else "?"
    nickname = g.get("nickname") or ""
    edition = g.get("edition")
    route = g.get("route")
    place = location_name(route, edition) if route is not None and edition is not None else ""
    level = g.get("level")
    return {
        "title": f"{nickname} ({species})" if nickname and nickname != species else species,
        "species": species,
        "owner": g.get("owner_name") or "",
        "level": f"Lv. {level}" if level else "",
        "place": place,
        "cause": cause_label(g.get("cause")),
        "date": format_ts(g.get("ts")),
        "edition": edition_name(edition) or (str(edition) if edition is not None else ""),
        "shiny": bool(g.get("shiny")),
        "counted": g.get("cause") != "soullink_partner",
    }


def graves_to_csv_rows(graves: list[dict], species_de: dict) -> list[list[str]]:
    rows = [[label for _key, label in CSV_COLUMNS]]
    for g in sorted(graves or [], key=lambda x: (x.get("ts") or 0, x.get("id") or 0)):
        r = grave_row(g, species_de)
        values = {
            "datum": r["date"],
            "species": r["species"],
            "route": r["place"],
            "edition": r["edition"],
            "cause": r["cause"],
            "partners": ", ".join(g.get("partners") or []),
            "shiny": "ja" if g.get("shiny") else "",
            "female": "ja" if g.get("female") else "",
        }
        rows.append([str(values.get(key, g.get(key)) if values.get(key, g.get(key)) is not None
                         else "") for key, _label in CSV_COLUMNS])
    return rows


def export_csv(graves: list[dict], path: str | Path, species_de: dict) -> Path:
    """CSV mit ``;`` und UTF-8-BOM → öffnet in deutschem Excel direkt korrekt."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=";", lineterminator="\r\n")
    writer.writerows(graves_to_csv_rows(graves, species_de))
    # newline="" — sonst macht Windows aus \r\n ein \r\r\n (Leerzeilen in Excel)
    path.write_text(buf.getvalue(), encoding="utf-8-sig", newline="")
    return path


def default_export_name(session: str | None) -> str:
    stamp = time.strftime("%Y%m%d_%H%M")
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in (session or "alle"))
    return f"friedhof_{safe}_{stamp}.csv"
