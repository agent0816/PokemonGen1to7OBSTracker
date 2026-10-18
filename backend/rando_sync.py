"""Gemeinsame Rando→Munchlax-Sync-Logik (TM/HM-Zuordnung + Ability-LUT).

Reine Glue zwischen Randomizer-Ergebnis, Munchlax (cross-thread Push) und
``pokedecoder`` — ohne Kivy-Import, damit klassische UI (``mainmenu.py``) und
KivyMD-UI (``mainmenumd.py``) dieselbe Quelle nutzen statt den Code zu
duplizieren (Phase C des KivyMD-Umzugs).

Historie: ursprünglich als ``MainMenu._sync_rando_to_munchlax`` /
``_schedule_rando_sync`` / ``_sync_rando_abilities`` / ``_local_player_slots``
in ``frontend/widgets/mainmenu.py``.

**Sync-sensibel:** ``send_rando_moves_sync`` wird bewusst OHNE
``is_connected``-Guard dispatcht — ``Munchlax.send_rando_moves_sync`` queued den
Payload intern (``_pending_rando_moves``) wenn offline und flusht beim nächsten
Connect. Ein Guard würde den Offline-Puffer umgehen (Phänomen 3, Logs
2026-09-30: ohne Push zeigte das Overlay anderer Hosts Vanilla-TM-Icons).
"""
from __future__ import annotations

from contextlib import nullcontext

from backend import pokedecoder


def _state_lock(munchlax):
    """state_lock des Munchlax, oder nullcontext (Test-Fakes ohne Lock).

    Schützt die rando_*_moves-Writes gegen konkurrierende snapshot_rando_*-Reads
    des Munchlax-Loops (iteriert unter state_lock → sonst "dict changed size
    during iteration")."""
    lock = getattr(munchlax, "state_lock", None)
    return lock if lock is not None else nullcontext()


def local_player_slots(pl: dict, logger=None) -> list[int]:
    """Lokale Spieler-Slots aus ``pl``: 1..player_count, exkl. ``remote_i=True``.

    Fällt auf ``[1]`` zurück, wenn alle Slots remote sind (Host erzeugt trotzdem
    mindestens ein ROM und kann später verteilen) oder ``player_count`` unlesbar
    ist.
    """
    try:
        count = int(pl.get("player_count", 1) or 1)
    except (TypeError, ValueError):
        count = 1
    slots: list[int] = []
    for slot in range(1, count + 1):
        if pl.get(f"remote_{slot}", False):
            continue
        slots.append(slot)
    if not slots:
        if logger is not None:
            logger.info("Alle player_count-Slots sind remote — nutze Slot 1 als Fallback")
        slots = [1]
    return slots


def _dispatch_rando_sync(munchlax, slot: int, tm_moves: dict, hm_moves: dict) -> None:
    """Pusht eine TM/HM-Zuordnung cross-thread an den Munchlax-Loop.

    Munchlax läuft seit der Loop-Isolation (Phase 3) auf einem eigenen Thread;
    ``dispatch_cross_thread`` hält das Future über ``submit_coro_logged`` selbst
    stark (Thread-interne Queue) — eine zusätzliche Strong-Ref ist nicht nötig.
    """
    munchlax.dispatch_cross_thread(
        munchlax.send_rando_moves_sync(slot, tm_moves, hm_moves),
        name="send_rando_moves_sync",
    )


def sync_rando_abilities(rando_data) -> None:
    """Baut die Gen-3-Ability-LUT (dexnr → [ability_id]) aus dem Rando-Log und
    übergibt sie an ``pokedecoder``. Leere LUT → ``None`` (Vanilla-Abilities)."""
    lut: dict[int, list[int]] = {}
    for dexnr, poke in rando_data.pokemon.items():
        ids = []
        for name in poke.abilities:
            aid = pokedecoder.resolve_ability_name(name)
            if aid is not None:
                ids.append(aid)
        if ids:
            lut[dexnr] = ids
    pokedecoder.set_gen3_abilities(lut if lut else None)


def apply_rando_to_munchlax(munchlax, randomizer, pl: dict, logger) -> None:
    """Schreibt die TM/HM-Zuordnung des Randomizers in Munchlax und pusht sie an
    den Server. Jedes lokale Player-Slot bekommt denselben rando_data-Snapshot
    (gleiche Settings → gleiche Seeds → selbe TM-Zuordnung pro ROM).

    Ohne rando_data werden die Slots geleert und die Ability-LUT zurückgesetzt.
    """
    rando_data = randomizer.get_log_data()
    local_slots = local_player_slots(pl, logger)
    if rando_data:
        tm_moves = rando_data.tm_moves or {}
        hm_moves = rando_data.hm_moves or {}
        # Dict-Writes unter state_lock (Dispatch bewusst ausserhalb, sonst Lock
        # über cross-thread-submit gehalten).
        with _state_lock(munchlax):
            for slot in local_slots:
                munchlax.rando_tm_moves[slot] = dict(tm_moves)
                munchlax.rando_hm_moves[slot] = dict(hm_moves)
        for slot in local_slots:
            _dispatch_rando_sync(munchlax, slot, tm_moves, hm_moves)
        sync_rando_abilities(rando_data)
        logger.info(
            f"apply_rando_to_munchlax: slots={local_slots}, "
            f"tms={len(tm_moves)}, hms={len(hm_moves)}, "
            f"connected={munchlax.is_connected}"
        )
    else:
        with _state_lock(munchlax):
            for slot in local_slots:
                munchlax.rando_tm_moves.pop(slot, None)
                munchlax.rando_hm_moves.pop(slot, None)
        for slot in local_slots:
            _dispatch_rando_sync(munchlax, slot, {}, {})
        pokedecoder.set_gen3_abilities(None)
        logger.info(
            f"apply_rando_to_munchlax: keine rando_data, slots geleert={local_slots}"
        )
