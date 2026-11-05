"""Reine Merge-/Lock-Funktionen für manuelle Encounter-Korrekturen.

Kivy-frei. Manuell geänderte Felder landen in ``manual_fields`` (kommagetrennt)
und werden von der Automatik nicht mehr überschrieben.
"""
import random
from datetime import datetime, timezone

# Felder, die der Spieler direkt ändern darf
EDITABLE_FIELDS: tuple[str, ...] = (
    "outcome", "dexnr", "lvl", "female", "form", "shiny", "route", "method",
    "is_first", "is_dupes_skip", "is_shiny_override", "has_balls", "dead",
)
# Felder, die nur als Sperre existieren (Wert liegt woanders bzw. Soft-Delete)
LOCK_ONLY_FIELDS: tuple[str, ...] = ("link", "deleted")
ALL_LOCKABLE: tuple[str, ...] = EDITABLE_FIELDS + LOCK_ONLY_FIELDS


def parse_locks(manual_fields: str | None) -> set[str]:
    """Zerlegt den ``manual_fields``-String in eine Menge bekannter Felder."""
    if not manual_fields:
        return set()
    return {
        part.strip() for part in str(manual_fields).split(",")
        if part.strip() in ALL_LOCKABLE
    }


def format_locks(locks: set[str]) -> str:
    """Sortierter, kommagetrennter String (nur bekannte Felder)."""
    return ",".join(sorted(f for f in locks if f in ALL_LOCKABLE))


def is_locked(enc: dict, field: str) -> bool:
    return field in parse_locks(enc.get("manual_fields"))


def merge_encounter_edit(enc: dict, changes: dict,
                         unlock: list[str] | None = None,
                         delete: bool | None = None) -> dict:
    """Wendet eine manuelle Änderung auf eine Kopie von ``enc`` an.

    - geänderte Felder (nur EDITABLE_FIELDS) werden gesetzt und gesperrt
    - ``delete=True``: deleted=1 + Sperre; ``delete=False``: deleted=0, Sperre weg
    - ``unlock``: Sperren entfernen, Wert bleibt (Automatik darf wieder schreiben)
    """
    result = dict(enc)
    locks = parse_locks(enc.get("manual_fields"))
    for field, value in (changes or {}).items():
        if field not in EDITABLE_FIELDS:
            continue
        result[field] = value
        locks.add(field)
    if delete is True:
        result["deleted"] = 1
        locks.add("deleted")
    elif delete is False:
        result["deleted"] = 0
        locks.discard("deleted")
    for field in unlock or []:
        locks.discard(field)
    result["manual_fields"] = format_locks(locks)
    return result


def keep_locked(old: dict, new: dict) -> dict:
    """Für automatische Updates: gesperrte Felder aus ``old`` behalten."""
    result = dict(new)
    locks = parse_locks(old.get("manual_fields"))
    for field in locks:
        if field in old:
            result[field] = old[field]
    result["manual_fields"] = old.get("manual_fields", "") or ""
    return result


def new_manual_personality() -> int:
    """Synthetische negative PV für Freitext-Encounter (kollidiert nie mit u32-PVs)."""
    return -random.randint(1, 2 ** 31)


def find_rebind_candidate(rows: list[dict], dexnr: int,
                          family: list[int] | None = None) -> dict | None:
    """Manuell angelegten Encounter (Pseudo-PV < 0) zu einem neu aufgetauchten
    echten Pokemon finden (Auto-Bindung, Testrunde 2026-11-02).

    ``rows``: Encounter EINES Owners/einer Edition. Kandidaten: negative PV,
    nicht gelöscht, Art = ``dexnr`` oder aus derselben Familie. Eindeutig
    gleiche Art gewinnt; sonst genau ein Familien-Treffer. Mehrdeutig → None
    (lieber nicht binden als falsch binden).
    """
    try:
        dex = int(dexnr)
    except (TypeError, ValueError):
        return None
    fam = {int(d) for d in (family or [dex])} | {dex}
    pool = [r for r in rows
            if isinstance(r.get("personality"), int) and r["personality"] < 0
            and not r.get("deleted") and r.get("dexnr") in fam]
    exact = [r for r in pool if r.get("dexnr") == dex]
    if len(exact) == 1:
        return exact[0]
    if not exact and len(pool) == 1:
        return pool[0]
    return None


def new_encounter(personality: int, owner: str, create: dict) -> dict:
    """Manuell angelegter Encounter: Defaults + ``create``-Felder (gesperrt)."""
    base = {
        "personality": personality, "owner": owner, "edition": create.get("edition"),
        "route": 0, "dexnr": 0, "lvl": 0, "shiny": 0, "female": None,
        "form": 0, "is_first": 1, "is_shiny_override": 0,
        "is_dupes_skip": 0, "has_balls": 1, "method": "wild",
        "outcome": "caught", "deleted": 0, "dead": 0, "manual_fields": "",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    return merge_encounter_edit(
        base, {k: v for k, v in create.items() if k in EDITABLE_FIELDS})


def opponent_pokemon(entry: dict | None):
    """Snapshot-Eintrag aus ``Munchlax.last_opponents`` als Pokemon-aehnliches
    Objekt (dexnr, lvl, personality, shiny, female, form) fuer den Anlegen-
    Dialog; None ohne gueltige Spezies."""
    from types import SimpleNamespace
    if not entry or int(entry.get("dexnr") or 0) <= 0:
        return None
    return SimpleNamespace(
        dexnr=int(entry["dexnr"]), lvl=int(entry.get("lvl") or 0),
        personality=entry.get("personality"), shiny=bool(entry.get("shiny")),
        female=entry.get("female"), form=str(entry.get("form") or ""), nickname="")


def pokemon_gender_form(pokemon) -> tuple[bool | None, str]:
    """Geschlecht (None = unbekannt) und Form (gestrippt, "" = keine) eines
    dekodierten Pokemon — gemeinsam fuer Anlegen-Dialog und Import."""
    female = getattr(pokemon, "female", None)
    form = getattr(pokemon, "form", "")
    return (None if female is None else bool(female),
            form.strip() if isinstance(form, str) else "")
