"""Listener-Liste fuer Encounter-/Link-Aenderungen (Karte live, Live-Test 2026-11-04)."""
import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.classes.munchlax import Munchlax  # noqa: E402


def _mx():
    m = Munchlax.__new__(Munchlax)
    m.logger = logging.getLogger("test")
    m._state_listeners = []
    m.on_soullink_state_changed_callback = None
    m.is_connected = False
    return m


def test_listener_bei_link_und_encounter():
    m = _mx()
    got = []
    m.add_state_listener(got.append)
    m.add_state_listener(got.append)  # doppelt registrieren → einmal
    single = []
    m.on_soullink_state_changed_callback = lambda: single.append(1)
    m._notify_soullink_state_changed()
    asyncio.run(m.send_encounter_sync({"personality": 1}))
    asyncio.run(m.send_encounter_outcome(1, "Ash_1", "caught"))
    assert got == ["soullink", "encounter", "encounter"]
    assert single == [1]  # Einzel-Slot (Encounter-Menue) weiter bedient


def test_fehlerhafter_listener_stoppt_andere_nicht():
    m = _mx()
    got = []

    def boom(_reason):
        raise RuntimeError("x")
    m.add_state_listener(boom)
    m.add_state_listener(got.append)
    m._notify_state_listeners("encounter")
    assert got == ["encounter"]


def test_listener_entfernen():
    m = _mx()
    got = []
    m.add_state_listener(got.append)
    m.remove_state_listener(got.append)
    m.remove_state_listener(got.append)  # idempotent
    m._notify_state_listeners("encounter")
    assert got == []


def test_last_opponent_snapshot():
    import threading
    m = _mx()
    m.state_lock = threading.Lock()
    m.last_opponents = {}
    m.set_last_opponent(2, 34, 111, {"dexnr": 302, "lvl": 5, "personality": 7,
                                     "shiny": 0, "female": True, "form": ""})
    snap = m.snapshot_last_opponents()
    assert snap[2]["dexnr"] == 302 and snap[2]["route"] == 111 and snap[2]["female"] is True
    snap[2]["dexnr"] = 1
    assert m.snapshot_last_opponents()[2]["dexnr"] == 302  # Kopie


def test_opponent_pokemon_helper():
    from backend.encounter_edit import opponent_pokemon
    assert opponent_pokemon(None) is None
    assert opponent_pokemon({"dexnr": 0}) is None
    p = opponent_pokemon({"dexnr": 10, "lvl": 3, "personality": 5, "female": None})
    assert p.dexnr == 10 and p.lvl == 3 and p.personality == 5 and p.female is None
