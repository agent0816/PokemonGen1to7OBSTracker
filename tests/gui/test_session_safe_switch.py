"""Session-Wechsel-Hinweis: nur bei laufendem Emulator (Testrunde 2026-11-02)."""
from types import SimpleNamespace

from frontend.widgets.mainmenumd import MainMenu


class _Proc:
    def __init__(self, alive: bool):
        self._alive = alive

    def poll(self):
        return None if self._alive else 0


def _menu(instances, citra_connected=False, **connected):
    flags = {name: SimpleNamespace(is_connected=connected.get(name, False))
             for name in ("munchlax", "arceus", "obs_websocket", "overlay_server", "bizhawk")}
    return SimpleNamespace(bizhawk_instances=instances,
                           citra=SimpleNamespace(is_connected=citra_connected),
                           **flags)


def _bind(menu):
    menu._alive_bizhawks = lambda: MainMenu._alive_bizhawks(menu)
    menu._emulator_running = lambda: MainMenu._emulator_running(menu)
    return menu


def test_beendeter_bizhawk_loest_keinen_hinweis_aus():
    menu = _bind(_menu([_Proc(alive=False)]))
    assert MainMenu._emulator_running(menu) is False
    assert MainMenu._needs_safe_switch(menu) is False


def test_laufender_bizhawk_loest_hinweis_aus():
    menu = _bind(_menu([_Proc(alive=False), _Proc(alive=True)]))
    assert MainMenu._emulator_running(menu) is True
    assert MainMenu._needs_safe_switch(menu) is True


def test_citra_verbunden_zaehlt_als_emulator():
    menu = _bind(_menu([], citra_connected=True))
    assert MainMenu._emulator_running(menu) is True


def test_nur_verbindung_braucht_safe_switch_ohne_hinweis():
    menu = _bind(_menu([], munchlax=True))
    assert MainMenu._emulator_running(menu) is False
    assert MainMenu._needs_safe_switch(menu) is True
