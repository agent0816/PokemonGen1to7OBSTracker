"""Anzeigenamen fuer Spieler-Slots (Testrunde 2026-11-03).

Statt "Spieler 1"/"P1"/rohem Owner ("Stephan_1") zeigen die Screens den Namen
des verbundenen Clients — wie das Teamfenster im Hauptmenue. Nur wenn derselbe
Name mehrere Slots hat (mehrere lokale BizHawks eines Clients), kommt die
Slot-Nummer dahinter: "Stephan (1)", "Stephan (2)".

Quelle: ``munchlax.player_names`` ({slot: Clientname}, vom Server bzw. lokal
von bizhawk.py gesetzt). Fallback: Name aus dem Owner ("Stephan_1" → "Stephan"),
zuletzt "Spieler N". Kein Kivy-Import.
"""
from __future__ import annotations

from collections import Counter
from typing import Iterable, Mapping


def _slot(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def owner_slot(owner: str | None) -> int | None:
    """Slot aus dem Owner-Format 'name_<slot>' (auch rein numerisch)."""
    if not owner:
        return None
    text = str(owner)
    tail = text.rsplit("_", 1)[-1]
    return int(tail) if tail.isdigit() else None


def owner_name(owner: str | None) -> str:
    """Namensteil aus 'name_<slot>' ('' wenn keiner)."""
    if not owner:
        return ""
    text = str(owner)
    head, sep, tail = text.rpartition("_")
    if sep and tail.isdigit():
        return head
    return "" if text.isdigit() else text


def _normalized(names: Mapping | None) -> dict[int, str]:
    result: dict[int, str] = {}
    for key, value in (names or {}).items():
        slot = _slot(key)
        if slot is not None and value:
            result[slot] = str(value)
    return result


def _with_numbers(bases: dict, slots: dict) -> dict:
    """Haengt ' (slot)' an, wenn ein Basisname mehrfach vorkommt."""
    counts = Counter(bases.values())
    return {key: (f"{base} ({slots[key]})" if counts[base] > 1 and slots[key] is not None
                  else base)
            for key, base in bases.items()}


def labels_for_slots(slots: Iterable, names: Mapping | None) -> dict[int, str]:
    """{slot: Anzeigename} fuer die angegebenen Slots."""
    known = _normalized(names)
    wanted = [s for s in (_slot(x) for x in slots) if s is not None]
    # Duplikate auch gegen bekannte, aber hier nicht angefragte Slots pruefen
    all_slots = sorted(set(wanted) | set(known))
    bases = {s: known.get(s) or f"Spieler {s}" for s in all_slots}
    labels = _with_numbers(bases, {s: s for s in all_slots})
    return {s: labels[s] for s in wanted}


def slot_label(slot, names: Mapping | None) -> str:
    """Anzeigename fuer einen Slot."""
    s = _slot(slot)
    if s is None:
        return str(slot)
    return labels_for_slots([s], names)[s]


def labels_for_owners(owners: Iterable[str], names: Mapping | None) -> dict[str, str]:
    """{owner: Anzeigename} fuer DB-Owner ('name_<slot>')."""
    known = _normalized(names)
    owners = [str(o) for o in owners]
    slots = {o: owner_slot(o) for o in owners}
    bases = {}
    for o in owners:
        slot = slots[o]
        bases[o] = (known.get(slot) if slot is not None else None) or owner_name(o) \
            or (f"Spieler {slot}" if slot is not None else o)
    # Bekannte Clientnamen anderer Slots fuer die Duplikatpruefung mitzaehlen
    extra = {f"__slot_{s}": n for s, n in known.items()
             if s not in {v for v in slots.values() if v is not None}}
    labels = _with_numbers({**bases, **extra},
                           {**slots, **{k: None for k in extra}})
    return {o: labels[o] for o in owners}


def owner_label(owner: str, names: Mapping | None) -> str:
    return labels_for_owners([owner], names)[str(owner)]


def button_text(label: str) -> str:
    """Text fuer Spieler-Auswahlknoepfe: 'Spieler: Stephan', aber nicht
    'Spieler: Spieler 3' (Fallback-Label steht dann allein)."""
    return label if label.startswith("Spieler ") else f"Spieler: {label}"
