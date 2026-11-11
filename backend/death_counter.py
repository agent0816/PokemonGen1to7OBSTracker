"""Todescounter fürs Overlay (Browser-Source + OBS-Textquellen), Kivy-frei.

Zahlen kommen aus dem lokalen Friedhof (``Munchlax.snapshot_death_counts``,
pro Session + Spielername). Hier nur Aufbereitung: Payload für ``/deaths``
und Texte/Input-Namen für OBS. Jede Instanz hat ihren eigenen Friedhof → kein
Server-Broadcast nötig.
"""
from __future__ import annotations

from backend.team_id import slug_team_id

OBS_TOTAL_INPUT = "deaths_total"
OBS_PLAYER_PREFIX = "deaths_"


def obs_input_name(player_name: str) -> str:
    """OBS-Textquelle pro Spieler: ``deaths_<slug>`` (gleicher Slug wie URLs)."""
    return f"{OBS_PLAYER_PREFIX}{slug_team_id(player_name)}"


def build_death_payload(counts: dict[str, int], player_names: dict | None) -> dict:
    """``{"players": [{"name", "slot", "count", "obs_input"}], "total": N}``.

    Spieler aus ``player_names`` ({slot: name}) erscheinen auch mit 0; Namen,
    die nur im Friedhof stehen (z.B. Spieler gerade offline), hinten dran.
    Sortierung: nach Slot, dann Name.
    """
    counts = dict(counts or {})
    players: list[dict] = []
    by_root: dict[str, dict] = {}
    for slot, name in sorted((player_names or {}).items(), key=lambda kv: _slot_key(kv[0])):
        if not name:
            continue
        # Friedhof speichert owner_root(build_owner(your_name, slot)) → gleiche
        # Root-Bildung, sonst landet z.B. "Ash_2" als Phantom-Zeile "Ash"
        root = owner_root(name)
        entry = by_root.get(root)
        if entry is not None:
            # Zweiter Slot desselben Spielers (Multi-BizHawk pro Client):
            # ein Zähler pro Name, /deaths/player/<slot> findet ihn trotzdem
            if _slot_int(slot) is not None:
                entry["slots"].append(_slot_int(slot))
            continue
        entry = {"name": name, "slot": _slot_int(slot),
                 "slots": [s for s in (_slot_int(slot),) if s is not None],
                 "count": int(counts.get(root, 0))}
        by_root[root] = entry
        players.append(entry)
    for name in sorted(counts):
        if name not in by_root:
            players.append({"name": name, "slot": None, "slots": [],
                            "count": int(counts[name])})
    for p in players:
        p["obs_input"] = obs_input_name(p["name"])
    return {"players": players, "total": sum(int(v) for v in counts.values())}


def obs_texts(payload: dict) -> dict[str, str]:
    """{input_name: text} für OBS (ohne Symbol — GDI+-Fonts zeigen ☠ nicht sicher)."""
    out = {OBS_TOTAL_INPUT: f"Gesamt: {payload.get('total', 0)}"}
    for p in payload.get("players", []):
        out[p["obs_input"]] = f"{p['name']}: {p['count']}"
    return out


def player_from_payload(payload: dict, slot: int) -> dict | None:
    for p in payload.get("players", []):
        if slot in (p.get("slots") or []):
            return p
    return None


def owner_root(name: str) -> str:
    """Wie ``PokedexDB.owner_root``: letzten ``_<int>``-Suffix abschneiden."""
    if not name:
        return name
    head, sep, tail = str(name).rpartition("_")
    if sep and head and tail.isdigit():
        return head
    return str(name)


def _slot_int(slot) -> int | None:
    try:
        return int(slot)
    except (TypeError, ValueError):
        return None


def _slot_key(slot) -> tuple:
    s = _slot_int(slot)
    return (0, s) if s is not None else (1, 0)
