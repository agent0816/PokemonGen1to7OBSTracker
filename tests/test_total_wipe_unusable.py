"""Total-Wipe: unbrauchbare Pokemon zählen als tot (Testrunde 2026-11-02)."""
import asyncio
import logging
import threading

from backend.classes.munchlax import Munchlax


class _FakeDB:
    connection = object()

    def __init__(self, rows):
        self.rows = rows

    def get_encounters(self, owner, edition=None):
        return [r for r in self.rows if r["owner"] == owner]


def _munchlax(mode="off", links=None, rows=None):
    m = Munchlax.__new__(Munchlax)
    m.logger = logging.getLogger("test_wipe")
    m.nuz = {"run_mode": "nuzlocke", "rule_restart_on_total_wipe": True}
    m.pl = {"your_name": "Ash"}
    m.soullink_config = {"mode": mode}
    m.soullink_links = links or {}
    m.state_lock = threading.Lock()
    m.pokedex_db = _FakeDB(rows or [])
    m.editions = {1: 32}
    m.is_connected = False
    m._wipe_signaled = {}
    return m


def _run(m, dead_infos):
    return asyncio.run(m.check_total_wipe(1, [], dead_infos))


def test_soullink_failed_gruppe_zaehlt_als_tot():
    links = {3: {"state": "failed", "members": {"Ash": {"personality": 222}}}}
    m = _munchlax(mode="coop", links=links)
    assert _run(m, [(111, 25, True), (222, 180, False)]) is True


def test_soullink_complete_gruppe_bleibt_brauchbar():
    links = {3: {"state": "complete", "members": {"Ash": {"personality": 222}}}}
    m = _munchlax(mode="coop", links=links)
    assert _run(m, [(111, 25, True), (222, 180, False)]) is False


def test_nuzlocke_verpasster_encounter_im_team_zaehlt_als_tot():
    rows = [{"owner": "Ash_1", "personality": 222, "outcome": "not_caught"}]
    m = _munchlax(rows=rows)
    assert _run(m, [(111, 25, True), (222, 180, False)]) is True


def test_kein_wipe_ohne_echten_tod():
    rows = [{"owner": "Ash_1", "personality": 222, "outcome": "not_caught"}]
    m = _munchlax(rows=rows)
    assert _run(m, [(222, 180, False)]) is False


def test_debounce_marker_wird_bei_lebendem_team_geloest():
    m = _munchlax()
    m._wipe_signaled = {1: True}
    assert _run(m, [(111, 25, False)]) is False
    assert 1 not in m._wipe_signaled


def test_soullink_dead_gruppe_zaehlt_pending_nicht():
    links = {
        3: {"state": "dead", "members": {"Ash": {"personality": 222}}},
        4: {"state": "pending", "members": {"Ash": {"personality": 333}}},
    }
    m = _munchlax(mode="coop", links=links)
    # 333 in pending-Gruppe bleibt brauchbar -> kein Wipe
    assert _run(m, [(111, 25, True), (222, 180, False), (333, 16, False)]) is False
    m2 = _munchlax(mode="coop", links=links)
    assert _run(m2, [(111, 25, True), (222, 180, False)]) is True


def test_gate_fehler_loest_wipe_marker_nicht():
    m = _munchlax()
    m.nuz["rule_run_start_on_ball"] = True
    m.pokedex_db = None  # DB weg -> Gate False wegen Fehler, nicht "kein Ball"
    assert asyncio.run(m._run_active_for_local_player(1, 32)) is False
    assert m._gate_error_by_player[1] is True
