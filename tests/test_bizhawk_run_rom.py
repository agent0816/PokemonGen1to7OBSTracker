"""BizHawk-Start mit ROM des aktiven Randomizer-Runs (Wunsch 2026-11-11)."""
from unittest import mock

from backend.controller.connection_controller import ConnectionController


class _RM:
    def __init__(self, roms, active=True):
        self._roms = roms
        self._active = active

    def get_active_run(self):
        return {"run_id": "run_001"} if self._active else None

    def get_run_paths(self, run_id):
        return {"roms": self._roms}


def _controller(tmp_path, bh_extra=None, run_manager=None, player_count=2, remote2=False):
    munchlax = mock.Mock()
    munchlax.get_run_manager.return_value = run_manager
    bizhawk = mock.Mock()
    bizhawk.server = object()  # Server läuft schon → kein submit
    bh = {"path": "C:/EmuHawk.exe", "host": "127.0.0.1", "port": "43885"}
    bh.update(bh_extra or {})
    pl = {"player_count": player_count, "remote_1": False, "remote_2": remote2}
    return ConnectionController(None, bizhawk, None, [], munchlax, None, bh, pl)


def _spawn_args(ctrl):
    with mock.patch("backend.controller.connection_controller.subprocess.Popen") as popen:
        popen.return_value.pid = 1
        ctrl.start_bizhawk()
    return [call.args[0] for call in popen.call_args_list]


def test_rom_pro_slot_als_letztes_argument(tmp_path):
    rom1 = tmp_path / "p1" / "rom.gba"
    rom2 = tmp_path / "p2" / "rom.gba"
    for r in (rom1, rom2):
        r.parent.mkdir()
        r.write_bytes(b"x")
    rm = _RM({1: {"rom": str(rom1)}, 2: {"rom": str(rom2)}})
    calls = _spawn_args(_controller(tmp_path, run_manager=rm))
    assert len(calls) == 2
    assert calls[0][-1] == str(rom1.resolve())
    assert calls[1][-1] == str(rom2.resolve())
    assert calls[0][1].startswith("--lua=")


def test_ohne_aktiven_run_oder_rom_wie_bisher(tmp_path):
    calls = _spawn_args(_controller(tmp_path, run_manager=_RM({}, active=False)))
    assert all(c[-1].startswith("--socket_port=") for c in calls)
    # ROM-Datei fehlt auf Platte
    rm = _RM({1: {"rom": str(tmp_path / "fehlt.gba")}})
    calls = _spawn_args(_controller(tmp_path, run_manager=rm, player_count=1))
    assert calls[0][-1].startswith("--socket_port=")


def test_einstellung_aus_und_remote_slot(tmp_path):
    rom1 = tmp_path / "rom.gba"
    rom1.write_bytes(b"x")
    rm = _RM({1: {"rom": str(rom1)}})
    calls = _spawn_args(_controller(tmp_path, {"start_with_run_rom": False}, rm, player_count=1))
    assert calls[0][-1].startswith("--socket_port=")
    # Remote-Slot 2 → nur ein Prozess
    calls = _spawn_args(_controller(tmp_path, run_manager=rm, remote2=True))
    assert len(calls) == 1 and calls[0][-1] == str(rom1.resolve())


def test_fehler_beim_aufloesen_startet_trotzdem(tmp_path):
    ctrl = _controller(tmp_path, player_count=1)
    ctrl.munchlax.get_run_manager.side_effect = RuntimeError("kaputt")
    calls = _spawn_args(ctrl)
    assert len(calls) == 1 and calls[0][-1].startswith("--socket_port=")
