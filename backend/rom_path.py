"""Auflösung des ROM-Pfads des aktiven Runs für die eigenen (lokalen) Slots.

Kivy-frei, damit testbar. Nutzt ``RunManager.get_active_run`` /
``RunManager.get_run_paths``.
"""
from __future__ import annotations

from pathlib import Path


def resolve_slot_rom_path(run_manager, slot: int) -> str | None:
    """ROM-Pfad des aktiven Runs für genau einen Slot (BizHawk-Start pro Spieler).

    ``None`` ohne RunManager, ohne aktiven Run oder ohne ROM-Datei für den Slot.
    """
    return resolve_own_rom_path(run_manager, [slot])


def resolve_own_rom_path(run_manager, slots: list[int]) -> str | None:
    """Liefert den ROM-Pfad des ersten lokalen Slots mit existierender ROM-Datei.

    ``None`` bei fehlendem RunManager, ohne aktiven Run oder ohne ROM für die Slots.
    """
    if run_manager is None:
        return None
    active = run_manager.get_active_run()
    if not active:
        return None
    run_id = active.get("run_id")
    if not run_id:
        return None
    roms = (run_manager.get_run_paths(run_id) or {}).get("roms", {}) or {}
    for slot in slots:
        entry = roms.get(slot)
        if not entry:
            continue
        rom = entry.get("rom")
        if not rom:
            continue
        path = Path(rom)
        if path.is_file():
            return str(path.resolve())
    return None
