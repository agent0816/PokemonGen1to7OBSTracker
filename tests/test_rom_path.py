"""Tests für backend.rom_path.resolve_own_rom_path."""
from backend.controller.run_manager import RunManager
from backend.rom_path import resolve_own_rom_path


class _FakeRM:
    def __init__(self, active, roms):
        self._active = active
        self._roms = roms

    def get_active_run(self):
        return self._active

    def get_run_paths(self, run_id):
        return {"roms": self._roms}


def test_kein_runmanager():
    assert resolve_own_rom_path(None, [1]) is None


def test_kein_aktiver_run():
    assert resolve_own_rom_path(_FakeRM(None, {}), [1]) is None


def test_erster_slot_mit_rom(tmp_path):
    rom2 = tmp_path / "p2.gba"
    rom2.write_bytes(b"x")
    rm = _FakeRM({"run_id": "r1"},
                 {1: {"rom": str(tmp_path / "fehlt.gba")}, 2: {"rom": str(rom2)}})
    assert resolve_own_rom_path(rm, [1, 2]) == str(rom2.resolve())


def test_slot_ohne_rom():
    rm = _FakeRM({"run_id": "r1"}, {3: {"rom": "x"}})
    assert resolve_own_rom_path(rm, [1]) is None


def test_leerer_rom_pfad():
    rm = _FakeRM({"run_id": "r1"}, {1: {"rom": ""}})
    assert resolve_own_rom_path(rm, [1]) is None


def test_mit_echtem_runmanager_ohne_run(tmp_path):
    assert resolve_own_rom_path(RunManager(tmp_path), [1]) is None
