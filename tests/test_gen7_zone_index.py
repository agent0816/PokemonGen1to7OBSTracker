"""Tests fuer den Gen-7-Zonen-Index (Index -> met_location, SM + USUM)."""
import logging
import os

import yaml

from backend.classes.citrahandler import CitraHandler

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "backend", "data")


def _load(game: str) -> dict:
    with open(os.path.join(DATA_DIR, f"zone_index_met_{game}.yml"), encoding="utf-8") as f:
        return yaml.safe_load(f)


def test_tabellen_struktur():
    # Record-Zahlen der ROMs: SM 295, USUM 380 (ZoneData a/0/7/7 Sub-File 0, 0x54 B).
    for game, expected in (("sm", 295), ("usum", 380)):
        table = _load(game)
        assert len(table) == expected
        assert all(isinstance(k, int) for k in table)
        assert all(isinstance(v, int) and v >= 0 for v in table.values())


def test_ground_truth():
    # Live durch Faenge belegt: Route 1 -> met8 (SM rec1), Hauholi-Stadtrand -> met6 (rec0);
    # USUM Route 3 -> met10 (rec6), Stadtrand -> met6 (rec0).
    sm, us = _load("sm"), _load("usum")
    assert sm[1] == 8
    assert sm[0] == 6
    assert us[6] == 10
    assert us[0] == 6


def test_pointer_hat_zone_index():
    for game, addr in (("sm", 0x3419A150), ("usum", 0x33FC1FD4)):
        with open(os.path.join(DATA_DIR, f"pointer_{game}.yml"), encoding="utf-8") as fh:
            p = yaml.safe_load(fh)
        assert p.get("encounter_zone_index") == addr


class _FakeCitra:
    def __init__(self, mem):
        self.mem = mem

    def read_memory(self, addr, size):
        return self.mem.get(addr)


class _FakeHandler:
    def __init__(self, pointer, mem, index_met):
        self.pointer = pointer
        self.citra_instance = _FakeCitra(mem)
        self._zone_index_met = index_met
        self._zone_id_mappings = {}
        self.logger = logging.getLogger("fake")

    _read_zone_id = CitraHandler._read_zone_id
    _read_zone_index = CitraHandler._read_zone_index
    _has_zone_index_pointer = CitraHandler._has_zone_index_pointer
    _met_for_index = CitraHandler._met_for_index
    _current_met_location = CitraHandler._current_met_location


class _MappingHandler:
    """Fake fuer _load_zone_mappings (liest echte YAMLs relativ zum Repo-Root)."""

    def __init__(self, edition):
        self.edition = edition
        self._zone_index_met = {}
        self._zone_id_mappings = {}
        self.logger = logging.getLogger("fake")

    _load_zone_mappings = CitraHandler._load_zone_mappings


def test_load_zone_mappings_gen7():
    # Edition 71/72 -> SM-Tabelle (295), 73/74 -> USUM (380); kein zone_id-Fallback.
    for edition, expected in ((71, 295), (72, 295), (73, 380), (74, 380)):
        h = _MappingHandler(edition)
        h._load_zone_mappings()
        assert len(h._zone_index_met) == expected
        assert h._zone_id_mappings == {}
        assert h._zone_index_met[0] == 6  # rec0 -> met6 in beiden


def test_current_met_location_gen7():
    # SM: Pointer 0x100, Index-Feld liest rec1 -> met8 (aus echter Tabelle).
    sm = _load("sm")
    ptr = {"encounter_zone_index": 0x100}
    h = _FakeHandler(ptr, {0x100: (1).to_bytes(2, "little")}, {int(k): v for k, v in sm.items()})
    assert h._current_met_location() == 8  # Route 1
    # rec0 -> met6
    h2 = _FakeHandler(ptr, {0x100: (0).to_bytes(2, "little")}, {int(k): v for k, v in sm.items()})
    assert h2._current_met_location() == 6
    # 0xFFFF (keine Zone) -> 0, kein Fallback
    h3 = _FakeHandler(ptr, {0x100: b"\xff\xff"}, {int(k): v for k, v in sm.items()})
    assert h3._current_met_location() == 0
