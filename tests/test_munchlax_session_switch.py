"""Session-Wechsel leert Munchlax-Session-State (Testrunde 2026-11-02)."""
import collections
import logging
import threading

from backend.classes.munchlax import Munchlax


def test_clear_for_session_switch():
    m = Munchlax.__new__(Munchlax)
    m.logger = logging.getLogger("test_switch")
    m.state_lock = threading.Lock()
    m._outbound_buffer = collections.deque([1, 2])
    m.sorted_teams = {1: ["alt"]}
    m.boxes = {1: [[]]}
    m.positions = {1: {}}
    m._wipe_signaled = {2: True}
    m.soullink_config = {"mode": "coop"}
    m.timer_state = {"running": True}
    m.player_names = {1: "Ash"}
    m.client_names = {"c": "Ash"}
    m.clear_for_session_switch()
    assert m.sorted_teams == {} and m.boxes == {} and m.positions == {}
    assert m._wipe_signaled == {}
    assert m.soullink_config == {} and m.timer_state == {}
    assert len(m._outbound_buffer) == 0
    assert m.player_names == {1: "Ash"}  # Namen laufender Emulatoren bleiben
