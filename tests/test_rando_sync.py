"""Tests für backend/rando_sync.py (Rando→Munchlax-Sync-Glue)."""
import logging
from types import SimpleNamespace

import pytest

from backend import pokedecoder
from backend import rando_sync
from backend.rando_sync import apply_rando_to_munchlax, local_player_slots


# -- Fakes -------------------------------------------------------------------

class FakeMunchlax:
    def __init__(self):
        self.rando_tm_moves: dict = {}
        self.rando_hm_moves: dict = {}
        self.is_connected = False
        self.dispatched: list = []

    def send_rando_moves_sync(self, slot, tm_moves, hm_moves):
        # Echte Methode liefert eine Coroutine; hier reicht ein Sentinel-Tupel,
        # da dispatch_cross_thread gefaket ist.
        return ("coro", slot, tm_moves, hm_moves)

    def dispatch_cross_thread(self, coro, name=None):
        self.dispatched.append((coro, name))


class FakeRandomizer:
    def __init__(self, data):
        self._data = data

    def get_log_data(self):
        return self._data


def _rando_data(tm=None, hm=None, pokemon=None):
    return SimpleNamespace(
        tm_moves=tm or {},
        hm_moves=hm or {},
        pokemon=pokemon or {},
    )


# -- local_player_slots ------------------------------------------------------

def test_local_player_slots_zwei_lokal():
    assert local_player_slots({"player_count": 2}) == [1, 2]


def test_local_player_slots_remote_ausgeschlossen():
    pl = {"player_count": 3, "remote_2": True}
    assert local_player_slots(pl) == [1, 3]


def test_local_player_slots_alle_remote_fallback(caplog):
    pl = {"player_count": 2, "remote_1": True, "remote_2": True}
    logger = logging.getLogger("rando_sync_test")
    with caplog.at_level(logging.INFO, logger="rando_sync_test"):
        assert local_player_slots(pl, logger) == [1]
    assert any("remote" in r.message for r in caplog.records)


def test_local_player_slots_unlesbar_default_1():
    assert local_player_slots({"player_count": "abc"}) == [1]
    assert local_player_slots({}) == [1]


# -- apply_rando_to_munchlax: mit rando_data --------------------------------

def test_apply_schreibt_und_dispatcht(monkeypatch):
    monkeypatch.setattr(pokedecoder, "set_gen3_abilities", lambda lut: None)
    tm = {1: "tackle", 2: "ember"}
    hm = {1: "cut"}
    mun = FakeMunchlax()
    rnd = FakeRandomizer(_rando_data(tm=tm, hm=hm))
    pl = {"player_count": 2}
    logger = logging.getLogger("rando_sync_test")

    apply_rando_to_munchlax(mun, rnd, pl, logger)

    assert set(mun.rando_tm_moves.keys()) == {1, 2}
    assert mun.rando_tm_moves[1] == tm
    assert mun.rando_hm_moves[2] == hm
    # Je lokalem Slot ein Dispatch
    assert len(mun.dispatched) == 2
    assert all(name == "send_rando_moves_sync" for _, name in mun.dispatched)


def test_apply_speichert_kopie_dispatcht_original(monkeypatch):
    monkeypatch.setattr(pokedecoder, "set_gen3_abilities", lambda lut: None)
    tm = {1: "tackle"}
    mun = FakeMunchlax()
    apply_rando_to_munchlax(mun, FakeRandomizer(_rando_data(tm=tm)),
                            {"player_count": 1}, logging.getLogger("x"))
    # Store ist eine Kopie (nicht dieselbe Referenz)…
    assert mun.rando_tm_moves[1] == tm
    assert mun.rando_tm_moves[1] is not tm
    # …Dispatch bekommt das Original-Dict
    coro, _name = mun.dispatched[0]
    assert coro[2] is tm


def test_apply_baut_ability_lut(monkeypatch):
    captured = {}
    monkeypatch.setattr(pokedecoder, "set_gen3_abilities",
                        lambda lut: captured.update(lut=lut))
    monkeypatch.setattr(pokedecoder, "resolve_ability_name",
                        lambda name: {"Blaze": 66, "Overgrow": 65}.get(name))
    pokemon = {
        6: SimpleNamespace(abilities=["Blaze", "Unbekannt"]),
        3: SimpleNamespace(abilities=["Overgrow"]),
        1: SimpleNamespace(abilities=["Unbekannt"]),  # keine ID → nicht in LUT
    }
    mun = FakeMunchlax()
    apply_rando_to_munchlax(mun, FakeRandomizer(_rando_data(tm={1: "x"}, pokemon=pokemon)),
                            {"player_count": 1}, logging.getLogger("x"))
    assert captured["lut"] == {6: [66], 3: [65]}


# -- apply_rando_to_munchlax: ohne rando_data -------------------------------

def test_apply_ohne_rando_leert_slots(monkeypatch):
    calls = []
    monkeypatch.setattr(pokedecoder, "set_gen3_abilities", lambda lut: calls.append(lut))
    mun = FakeMunchlax()
    mun.rando_tm_moves = {1: {9: "a"}, 2: {9: "a"}}
    mun.rando_hm_moves = {1: {9: "b"}}
    apply_rando_to_munchlax(mun, FakeRandomizer(None),
                            {"player_count": 2}, logging.getLogger("x"))
    # Lokale Slots 1,2 geleert
    assert 1 not in mun.rando_tm_moves
    assert 2 not in mun.rando_tm_moves
    # Dispatch mit leeren Dicts je Slot
    assert len(mun.dispatched) == 2
    assert mun.dispatched[0][0][2] == {}
    # Abilities zurückgesetzt
    assert calls == [None]


# -- apply_rando_after_session_load -----------------------------------------

def test_after_session_load_mit_daten_pusht(monkeypatch):
    monkeypatch.setattr(pokedecoder, "set_gen3_abilities", lambda lut: None)
    mun = FakeMunchlax()
    rando_sync.apply_rando_after_session_load(
        mun, FakeRandomizer(_rando_data(tm={1: "ember"})),
        {"player_count": 1}, logging.getLogger("x"))
    assert mun.rando_tm_moves == {1: {1: "ember"}}
    assert len(mun.dispatched) == 1


def test_after_session_load_ohne_daten_app_start_kein_push(monkeypatch):
    calls = []
    monkeypatch.setattr(pokedecoder, "set_gen3_abilities", lambda lut: calls.append(lut))
    mun = FakeMunchlax()
    # Remote-Slot 2 hat Maps (vom Server), lokaler Slot 1 nicht
    mun.rando_tm_moves = {2: {9: "a"}}
    rando_sync.apply_rando_after_session_load(
        mun, FakeRandomizer(None), {"player_count": 1}, logging.getLogger("x"))
    assert mun.rando_tm_moves == {2: {9: "a"}}
    assert mun.dispatched == []
    assert calls == [None]


def test_after_session_load_ohne_daten_leert_alte_lokale_maps(monkeypatch):
    monkeypatch.setattr(pokedecoder, "set_gen3_abilities", lambda lut: None)
    mun = FakeMunchlax()
    mun.rando_tm_moves = {1: {9: "a"}, 2: {9: "b"}}
    rando_sync.apply_rando_after_session_load(
        mun, FakeRandomizer(None), {"player_count": 1}, logging.getLogger("x"))
    # Session-Wechsel aus Rando-Session: lokaler Slot geleert + leerer Push
    assert 1 not in mun.rando_tm_moves
    assert mun.rando_tm_moves[2] == {9: "b"}
    assert len(mun.dispatched) == 1
    assert mun.dispatched[0][0][2] == {}


# -- RandomizerController.load_active_run_log --------------------------------

def _make_run(tmp_path, slots, write_log_for=()):
    from backend.controller.run_manager import RunManager
    rom = tmp_path / "src.gba"
    rom.write_bytes(b"ROM" * 16)
    run = RunManager(tmp_path / "session").create_run(rom, player_slots=slots)
    assert run is not None
    for target in run["rom_targets"]:
        if target["player_slot"] in write_log_for:
            with open(target["log_path"], "w", encoding="utf-8") as f:
                f.write("Randomizer Version: 4.6.1\n\n--TM Moves--\n"
                        "TM01 TRAUMFRESSER\nTM02 HAERTNER\n\n")
    return tmp_path / "session"


def test_load_active_run_log_parst_lokalen_slot(tmp_path):
    from backend.controller.randomizer_controller import RandomizerController
    session = _make_run(tmp_path, [2], write_log_for=(2,))
    ctrl = RandomizerController({}, {"player_count": 2, "remote_1": True},
                                configsave=str(session))
    old_parser = ctrl.log_parser
    parser, log_path = ctrl.load_active_run_log([2], str(session))
    assert log_path is not None and log_path.endswith("randomizer.log")
    # Executor-tauglich: self bleibt unveraendert bis adopt
    assert ctrl.log_parser is old_parser
    assert ctrl.get_log_data() is None
    ctrl.adopt_log_parser(parser)
    data = ctrl.get_log_data()
    assert data is not None
    assert len(data.tm_moves) == 2


def test_load_active_run_log_ohne_log_liefert_leeren_parser(tmp_path):
    from backend.controller.randomizer_controller import RandomizerController
    session = _make_run(tmp_path, [1], write_log_for=())
    ctrl = RandomizerController({}, {"player_count": 1}, configsave=str(session))
    parser, log_path = ctrl.load_active_run_log([1], str(session))
    assert log_path is None
    assert parser.get_data() is None


def test_load_active_run_log_ohne_run(tmp_path):
    from backend.controller.randomizer_controller import RandomizerController
    leer = tmp_path / "leer"
    leer.mkdir()
    ctrl = RandomizerController({}, {"player_count": 1}, configsave=str(leer))
    parser, log_path = ctrl.load_active_run_log([1], str(leer))
    assert log_path is None
    assert parser.get_data() is None
    # Kein RunManager ohne runs/ -> Ordner wird NICHT angelegt
    assert not (leer / "runs").exists()


def test_load_active_run_log_fremder_slot_ignoriert(tmp_path):
    from backend.controller.randomizer_controller import RandomizerController
    session = _make_run(tmp_path, [1, 2], write_log_for=(2,))
    ctrl = RandomizerController({}, {"player_count": 1}, configsave=str(session))
    _parser, log_path = ctrl.load_active_run_log([1], str(session))
    assert log_path is None
