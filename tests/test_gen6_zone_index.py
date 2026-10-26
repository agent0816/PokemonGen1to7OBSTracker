"""Tests fuer den Gen-6-Zonen-Index (Index -> met_location)."""
import logging
import os

import yaml

from backend.classes.citrahandler import CitraHandler

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "backend", "data")


def _load(game: str) -> dict:
    with open(os.path.join(DATA_DIR, f"zone_index_met_{game}.yml"), encoding="utf-8") as f:
        return yaml.safe_load(f)


def test_tabellen_struktur():
    # Tatsaechliche Record-Zahlen der ROMs: XY 360, ORAS 536 (wie im Plan erwartet)
    for game, expected in (("xy", 360), ("oras", 536)):
        table = _load(game)
        assert len(table) == expected
        assert all(isinstance(k, int) for k in table)
        assert all(isinstance(v, int) and v >= 0 for v in table.values())


def test_ground_truth():
    xy, oras = _load("xy"), _load("oras")
    assert xy[268] == 50
    assert xy[157] == 40
    assert oras[23] == 204
    assert oras[24] == 206
    assert oras[25] == 208


class _FakeCitra:
    def __init__(self, mem: dict[int, bytes | None]):
        self.mem = mem

    def read_memory(self, addr: int, size: int):
        return self.mem.get(addr)


class _FakeHandler:
    """Minimales Objekt mit den Attributen, die die Methoden brauchen."""

    def __init__(self, pointer: dict, mem: dict):
        self.pointer = pointer
        self.citra_instance = _FakeCitra(mem)
        self._zone_index_met = {5: 77, 6: 0}
        self._zone_id_mappings = {0x0123: 42}
        self.logger = logging.getLogger("fake")

    _read_zone_id = CitraHandler._read_zone_id
    _read_zone_index = CitraHandler._read_zone_index
    _has_zone_index_pointer = CitraHandler._has_zone_index_pointer
    _met_for_index = CitraHandler._met_for_index
    _current_met_location = CitraHandler._current_met_location


_PTR = {"encounter_zone_index": 0x100, "encounter_zone_pointer": 0x200}


def test_gueltiger_index():
    h = _FakeHandler(_PTR, {0x100: (5).to_bytes(2, "little")})
    assert h._current_met_location() == 77


def test_index_nicht_in_tabelle():
    h = _FakeHandler(_PTR, {0x100: (999).to_bytes(2, "little")})
    assert h._current_met_location() == 0


def test_index_ffff_kein_veralteter_fallback():
    # Pointer vorhanden, aber transienter 0xFFFF-Read: KEIN Rueckfall auf die
    # veraltete zone_id-Tabelle (sonst falsche met_location in der DB).
    h = _FakeHandler(_PTR, {0x100: b"\xff\xff", 0x200: (0x0123).to_bytes(2, "little")})
    assert h._read_zone_index() is None
    assert h._current_met_location() == 0


def test_read_none_kein_veralteter_fallback():
    h = _FakeHandler(_PTR, {0x100: None, 0x200: (0x0123).to_bytes(2, "little")})
    assert h._read_zone_index() is None
    assert h._current_met_location() == 0


def test_fehlender_pointer_alter_weg():
    # Nur wenn der Index-Pointer ganz fehlt (Gen 7/Altconfig) greift der alte Weg.
    h = _FakeHandler({"encounter_zone_pointer": 0x200}, {0x200: (0x0123).to_bytes(2, "little")})
    assert h._read_zone_index() is None
    assert h._current_met_location() == 42
