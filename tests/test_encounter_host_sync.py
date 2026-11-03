"""Host-Sync "Encounter bei allen erfassen" (Testrunde 2026-11-03)."""
import asyncio
import logging
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.classes.arceus import Arceus
from backend.encounter_import import ImportCandidate, RouteGroup, auto_select

REM = {"client_port": "43886", "helper_port": "43887"}


# ---------------------------------------------------------------- Auswahl

def _cand(pv, route, lvl=5, method="wild"):
    return ImportCandidate(pv=pv, dexnr=16, lvl=lvl, shiny=False, female=None, form="",
                           route=route, method=method, source="Team")


def test_auto_select_alle_und_vorschlag_als_erstes():
    groups = [RouteGroup(route=1, candidates=[_cand(1, 1), _cand(2, 1)], default_first_pv=2),
              RouteGroup(route=2, candidates=[_cand(3, 2)], default_first_pv=None,
                         has_existing_first=True)]
    selected, first = auto_select(groups)
    assert selected == {1, 2, 3}
    assert first == {2}


# ---------------------------------------------------------------- Server

def _arceus():
    a = Arceus("localhost", 43886, REM)
    a.munchlaxes = {"host": object(), "c2": object()}
    a.munchlax_names = {"host": "Ash", "c2": "Bob"}
    a.sent = []

    async def send_to_client(cid, msg):
        a.sent.append((cid, msg))
    a.send_to_client = send_to_client
    return a


def test_server_broadcast_an_alle_und_started_an_ausloeser():
    async def run():
        a = _arceus()
        a._handle_encounter_import_request("host", {"type": "encounter_import_request",
                                                    "request_id": "r1"})
        await asyncio.sleep(0.01)
        return a
    a = asyncio.run(run())
    requests = [cid for cid, m in a.sent if m["type"] == "encounter_import_request"]
    assert sorted(requests) == ["c2", "host"]
    started = [(cid, m) for cid, m in a.sent if m["type"] == "encounter_import_started"]
    assert started == [("host", {"type": "encounter_import_started", "request_id": "r1",
                                 "clients": ["Ash", "Bob"]})]


def test_server_leitet_ergebnis_an_ausloeser():
    async def run():
        a = _arceus()
        a._handle_encounter_import_request("host", {"request_id": "r1"})
        await asyncio.sleep(0.01)
        a.sent.clear()
        a._handle_encounter_import_result("c2", {"request_id": "r1",
                                                 "results": [{"player": 2, "count": 3}]})
        a._handle_encounter_import_result("c2", {"request_id": "unbekannt", "results": []})
        await asyncio.sleep(0.01)
        return a
    a = asyncio.run(run())
    assert a.sent == [("host", {"type": "encounter_import_result", "request_id": "r1",
                                "client_name": "Bob",
                                "results": [{"player": 2, "count": 3}]})]


# ---------------------------------------------------------------- Client

def _munchlax(host=True, features=("encounter_import_request",)):
    from backend.classes.munchlax import Munchlax
    m = Munchlax.__new__(Munchlax)
    m.logger = logging.getLogger("test_host_sync")
    m.rem = {"start_server": host}
    m.is_connected = True
    m._server_supports_resume = True
    m._server_features = set(features)
    m.sent = []

    async def send_message(msg):
        m.sent.append(msg)
    m.send_message = send_message
    return m


def test_client_request_nur_host_und_mit_feature():
    async def run(m):
        m.writer_lock = asyncio.Lock()
        return await m.request_encounter_import()
    m = _munchlax(host=False)
    assert asyncio.run(run(m))[0] is False and m.sent == []
    m = _munchlax(features=())
    assert asyncio.run(run(m))[0] is False and m.sent == []
    m = _munchlax()
    ok, _ = asyncio.run(run(m))
    assert ok is True and m.sent[0]["type"] == "encounter_import_request"
    assert m.sent[0]["request_id"]


def test_client_ruft_frontend_callbacks():
    m = _munchlax()
    calls = []
    m.on_encounter_import_feedback = lambda kind, d: calls.append(kind)
    m._call_frontend("on_encounter_import_feedback", "started", {})
    m.on_encounter_import_request = None
    m._call_frontend("on_encounter_import_request", {})  # nicht registriert → nur Log
    assert calls == ["started"]


# ---------------------------------------------------------------- Auto-Import

class _Pkm(SimpleNamespace):
    pass


def _pkm(pv, route=16):
    return _Pkm(dexnr=16, personality=pv, lvl=4, shiny=False, route=route,
                ot_id=1, ot_secret_id=2, gender=None, form=0)


class _FakeMunchlax:
    def __init__(self, team, boxes, existing=()):
        self._team, self._boxes, self._existing = team, boxes, set(existing)
        self.imported = []
        self.boxes_updated = []

    def owned_player_ids(self):
        return [1]

    def submit_cross_thread(self, coro):
        return asyncio.ensure_future(coro)

    async def update_boxes(self, pid, boxes):
        self.boxes_updated.append(pid)
        self._boxes = boxes

    async def encounter_import_context(self, pid):
        return {"owner": "Ash_1", "edition": 32, "existing_pvs": self._existing,
                "routes_with_first": set()}

    def snapshot_sorted_teams(self):
        return {1: self._team}

    def snapshot_boxes(self, pid):
        return self._boxes

    async def import_encounters(self, pid, encounters):
        self.imported.extend(encounters)
        return True, len(encounters), f"{len(encounters)} übernommen"


def test_auto_import_team_und_boxen(monkeypatch):
    import frontend.encounter_auto_import as ai

    async def fake_read(pl, citra, bizhawk, pid):
        return [[_pkm(30, route=17)]], None
    monkeypatch.setattr(ai, "read_boxes_for_player", fake_read)
    m = _FakeMunchlax([_pkm(10), _pkm(20)], [])
    results = asyncio.run(ai.run_auto_import(m, {}, None, None))
    assert results[0]["ok"] is True and results[0]["count"] == 3
    assert m.boxes_updated == [1]
    firsts = {e["personality"] for e in m.imported if e["is_first"]}
    assert len([pv for pv in firsts if pv in (10, 20)]) == 1 and 30 in firsts


def test_auto_import_ohne_lesbare_boxen_nutzt_gespeicherte(monkeypatch):
    import frontend.encounter_auto_import as ai

    async def fake_read(pl, citra, bizhawk, pid):
        return None, "Emulator nicht verbunden"
    monkeypatch.setattr(ai, "read_boxes_for_player", fake_read)
    m = _FakeMunchlax([_pkm(10)], [[_pkm(40, route=18)]], existing={10})
    results = asyncio.run(ai.run_auto_import(m, {}, None, None))
    assert m.boxes_updated == []
    assert results[0]["count"] == 1 and "gespeicherte Boxen" in results[0]["msg"]


def test_result_text():
    text, ok = __import__("frontend.encounter_auto_import", fromlist=["x"]).result_text(
        "Bob", [{"player": 2, "ok": True, "count": 3}, {"player": 3, "ok": False,
                                                       "count": 0, "msg": "Gen 1/2 nicht unterstützt"}])
    assert text == "Bob – Slot 2: 3 übernommen, Slot 3: Gen 1/2 nicht unterstützt"
    assert ok is False


def test_server_verwirft_ergebnis_wenn_ausloeser_weg():
    async def run():
        a = _arceus()
        a._handle_encounter_import_request("host", {"request_id": "r1"})
        await asyncio.sleep(0.01)
        a.sent.clear()
        del a.munchlaxes["host"]
        a._handle_encounter_import_result("c2", {"request_id": "r1", "results": []})
        await asyncio.sleep(0.01)
        return a
    assert asyncio.run(run()).sent == []


def test_result_text_ohne_slot():
    from frontend.encounter_auto_import import result_text, summary_text
    text, ok = result_text("Bob", [{"player": None, "ok": False, "msg": "läuft bereits"}])
    assert text == "Bob – läuft bereits" and ok is False
    text, _ = summary_text([("Ash", [{"player": 1, "ok": True, "count": 2}]),
                            ("Bob", [{"player": None, "ok": False, "msg": "läuft bereits"}])])
    assert text == "Encounter-Erfassung: Ash – Slot 1: 2 übernommen | Bob – läuft bereits"
