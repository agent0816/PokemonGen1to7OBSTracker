"""GUI-Durchlauf MainMenu (KivyMD, Phase C) mit gemockten Backend-Objekten.

Kein Server/Emulator/Randomizer wird real gestartet; Verbindungs- und
Rando-Pfade werden nur strukturell geprüft (Buttons/Segmented/Switches,
Team-Boxen, Detail-Navigation).

Ausfuehrung:
    .venv/py3115/Scripts/python.exe -m pytest tests/gui/test_mainmenumd.py -v
"""
from __future__ import annotations

import traceback
from types import SimpleNamespace

import pytest

from kivy.clock import Clock
from kivy.core.window import Window
from kivymd.app import MDApp
from kivymd.uix.screen import MDScreen
from kivymd.uix.screenmanager import MDScreenManager

from frontend.widgets.mainmenumd import MainMenu
from frontend.widgets.trainerboxmd import TrainerBoxMD


def _poke(dexnr, nick, lvl, cur, mx, item=0):
    return SimpleNamespace(dexnr=dexnr, nickname=nick, lvl=lvl, cur_hp=cur, max_hp=mx,
                           item=item, shiny=False, female=False, form="", status={})


class _FakeObs:
    conf = {"common_path": "x", "red": "y", "items_path": "i", "badges_path": "b"}
    is_connected = False

    def get_sprite(self, pokemon, anim, edition, two_pc=False):
        return ""


class _DetailScreen(MDScreen):
    def __init__(self, **kw):
        super().__init__(**kw)
        self.name = "PokemonDetail"
        self.shown = None
        self.refresh_src = None

    def show(self, *args, **kwargs):
        self.shown = (args, kwargs)

    def set_refresh_source(self, player_id, slot, munchlax, rando_getter):
        self.refresh_src = (player_id, slot, munchlax, rando_getter)


def _make_munchlax(pl):
    return SimpleNamespace(
        player_names={1: "Alice", 2: "Bob"},
        sorted_teams={1: [_poke(25, "Blitz", 22, 40, 60), _poke(6, "Draco", 36, 0, 150)]},
        editions={1: 34, 2: 35},
        badges={1: 3, 2: 0},
        nuz={},
        pl=pl,
        is_connected=False,
        reconnecting=False,
        client_id="abc12345",
        remote_connection_status={},
        snapshot_remote_connection_status=lambda: {},
        snapshot_remote_connection_names=lambda: {},
        change_order=lambda: None,
        set_bundle_request_notify=lambda cb: None,
        host=None, port=None,
    )


class _DriveApp(MDApp):
    def __init__(self, out: str, **kw):
        super().__init__(**kw)
        self._out = out
        self.results: dict = {}
        self.save_calls: list = []

    def check(self, name, ok, detail=None):
        self.results[name] = {"ok": bool(ok), "detail": detail}

    def build(self):
        self.theme_cls.theme_style = "Dark"
        self.theme_cls.primary_palette = "Orange"
        pl = {"player_count": 2, "session_game": "Rot", "your_name": "Alice"}
        sp = {"common_path": "x", "red": "y", "items_path": "i", "badges_path": "b",
              "animated": False, "show_nicknames": True, "show_items": True,
              "show_hp_bars": True, "show_status_effects": True,
              "animate_obs_reorder": False, "show_badges": True, "order": "team"}
        bh = {"path": "nonexist.exe", "save_automatically": False}
        rem = {"start_server": True, "server_ip_adresse": "1.2.3.4",
               "client_port": 5, "server_port": 6}
        munchlax = _make_munchlax(pl)
        arceus = SimpleNamespace(is_connected=False, snapshot_clients=lambda: {},
                                 munchlax_status={}, set_bundle_response_notify=lambda cb: None)
        overlay = SimpleNamespace(is_connected=False)
        bizhawk = SimpleNamespace(is_connected=False)
        citra = SimpleNamespace(is_connected=False, started=False, player_number=0)

        self.menu = MainMenu(arceus, bizhawk, citra, {}, munchlax, _FakeObs(), overlay,
                             "", sp, rem, {}, bh, pl, {}, {}, "9.9.9")
        self.sm = MDScreenManager()
        self.sm.add_widget(self.menu)
        self.sm.add_widget(_DetailScreen())
        return self.sm

    def on_start(self):
        self.sm.current = "MainMenu"
        Clock.schedule_once(lambda dt: self.safe(self.s_render), 1.0)
        # Watchdog: App IMMER beenden, auch wenn ein Step die Kette abbricht.
        Clock.schedule_once(lambda dt: self.stop(), 20)

    def _next(self, step, delay=0.8):
        Clock.schedule_once(lambda dt: self.safe(step), delay)

    def safe(self, step):
        try:
            step()
        except Exception:
            self.results[step.__name__] = {"ok": False, "detail": traceback.format_exc()}

    def shot(self, name):
        Clock.schedule_once(lambda dt: Window.screenshot(name=f"{self._out}/{name}.png"), 0.5)

    def s_render(self):
        self.menu.load_from_session()
        m = self.menu
        self.check("zwei_trainerboxen", len(m.trainer_boxes) == 2, list(m.trainer_boxes))
        self.check("acht_switches", len(m._switches) == 8, list(m._switches))
        self.check("server_segment_aktiv", m._seg_server.active and not m._seg_client.active,
                   (m._seg_server.active, m._seg_client.active))
        self.check("sort_team_aktiv", m._sort_items["team"].active, m._current_sort)
        self.check("switch_states",
                   m._switches["names"].active and m._switches["hp"].active
                   and not m._switches["animated"].active,
                   {k: s.active for k, s in m._switches.items()})
        self.check("role_button_server", m._get_button_text(m.role_button) == "Server beenden"
                   or m._get_button_text(m.role_button) == "Server starten",
                   m._get_button_text(m.role_button))
        self.check("clear_button_sichtbar", m.clear_button.parent is not None)
        self.check("munchlax_hostport", m.munchlax.host == "127.0.0.1" and m.munchlax.port == 5,
                   (m.munchlax.host, m.munchlax.port))
        self.shot("01_mainmenu")
        self._next(self.s_toggle)

    def s_toggle(self):
        m = self.menu
        m.controller.save_main_menu_settings = lambda values: self.save_calls.append(values)
        # Netz-Aktionen isolieren (Mock-Backends ohne echte Stop-Maschinerie)
        m.connection.stop_server = lambda: None
        before = len(self.save_calls)
        m._switches["animated"].active = True
        self.check("toggle_speichert", len(self.save_calls) > before, self.save_calls)
        # Rolle auf Client umschalten über den ECHTEN Klick-Pfad (mark_item →
        # active-Property-Change → bind(active=...) → _on_role_segment). Fängt den
        # Bug ab, bei dem bind(on_active=...) nicht feuerte.
        m.role_segment.mark_item(m._seg_client)
        self.check("client_mode", m._server_mode is False, m._server_mode)
        self.check("role_text_client",
                   m._get_button_text(m.role_button) == "Client starten",
                   m._get_button_text(m.role_button))
        self.check("clear_button_weg", m.clear_button.parent is None)
        self.shot("02_client_mode")
        self._next(self.s_detail)

    def s_detail(self):
        m = self.menu
        m.trainer_boxes[1]._show_detail(0)
        self.check("detail_nav", m.manager.current == "PokemonDetail", m.manager.current)
        detail = m.manager.get_screen("PokemonDetail")
        self.check("detail_show_aufgerufen", detail.shown is not None)
        self.check("detail_live_source",
                   detail.refresh_src is not None and detail.refresh_src[0] == 1
                   and detail.refresh_src[1] == 0,
                   detail.refresh_src)
        self.shot("03_detail")
        m.manager.current = "MainMenu"
        self._next(self.s_finish, 0.6)

    def s_finish(self):
        self.stop()


_EXPECTED = {"zwei_trainerboxen", "acht_switches", "server_segment_aktiv", "sort_team_aktiv",
             "switch_states", "role_button_server", "clear_button_sichtbar", "munchlax_hostport",
             "toggle_speichert", "client_mode", "role_text_client", "clear_button_weg",
             "detail_nav", "detail_show_aufgerufen", "detail_live_source"}


@pytest.mark.gui
def test_mainmenu_aufbau_und_interaktion(screenshot_dir, run_md_app):
    app = run_md_app(_DriveApp(screenshot_dir))
    fehlende = _EXPECTED - set(app.results)
    assert not fehlende, f"Checks nicht gelaufen (Step-Crash?): {fehlende}"
    fehler = {k: v["detail"] for k, v in app.results.items() if not v["ok"]}
    assert not fehler, fehler
