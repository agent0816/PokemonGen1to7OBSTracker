"""Party-Team-Regeln (Frontend + Backend Consumer gemeinsam).

Reine Funktionen ueber Pokemon-Objekte + nuz-Config-Dict. Keine Klassen,
keine Kivy-/OBS-Abhaengigkeit. Wird von frontend/widgets/trainerbox.py
sowie backend/classes/overlay_server.py und backend/classes/obs.py
importiert, damit der Filter-Kern nur einmal existiert.
"""
from backend.type_lookup import first_type


def first_type_dupe_slots(team, nuz: dict | None) -> set[int]:
    """Slots deren Typ1 im Party-Team mehrfach vorkommt.

    Regel ``rule_single_type_per_team``: alle beteiligten Slots landen im
    Set (kein Sieger, keine chronologische Wahl). Eier und leere Slots
    (dexnr in {0, 'egg'}) sind ausgenommen. Wenn Regel deaktiviert oder
    ``nuz`` leer/None ist, wird ein leeres Set zurueckgegeben.
    """
    if not nuz or not nuz.get("rule_single_type_per_team", False):
        return set()
    slot_type: dict[int, int] = {}
    for slot, pokemon in enumerate(team):
        dex = getattr(pokemon, "dexnr", 0)
        if not dex or dex == "egg":
            continue
        t = first_type(dex)
        if t is None:
            continue
        slot_type[slot] = t
    if not slot_type:
        return set()
    counts: dict[int, int] = {}
    for t in slot_type.values():
        counts[t] = counts.get(t, 0) + 1
    return {slot for slot, t in slot_type.items() if counts.get(t, 0) >= 2}
