"""GUI-Durchlauf SettingsMenu (KivyMD, Phase D) mit gemockten Backend-Objekten.

Baut den Screen in einem MDScreenManager mit FadeTransition (wie appmd) — das
fängt Render-Bugs der MDTabsCarousel im ScreenManager ab (vgl. RecycleView-Bug).
Prüft Tab-Aufbau, Feld-Befüllung aus der Config, 2-PC-Ausklapp + Save, Tabwechsel
und schiesst pro Tab einen Screenshot.

Ausfuehrung:
    .venv/py3115/Scripts/python.exe -m pytest tests/gui/test_settingsmenumd.py -v
"""
from __future__ import annotations

import traceback
from types import SimpleNamespace

import pytest

from kivy.clock import Clock
from kivy.core.window import Window
from kivymd.app import MDApp
from kivymd.uix.screenmanager import MDScreenManager
from kivy.uix.screenmanager import FadeTransition

from frontend.widgets.settingsmenumd import SettingsMenu


def _mock_backends():
    arceus = SimpleNamespace(rem={"helper_port": "43887"}, port="43876", is_connected=False)
    bizhawk = SimpleNamespace(server=None, is_connected=False)
    obs_ws = SimpleNamespace(ws=None, is_connected=False)
    overlay = SimpleNamespace(is_connected=False)
    munchlax = SimpleNamespace(is_connected=False, set_bundle_session_path=lambda p: None,
                               end_run_manual=lambda reason: None)
    return arceus, bizhawk, munchlax, obs_ws, overlay


class _DriveApp(MDApp):
    def __init__(self, out: str, configsave: str, **kw):
        super().__init__(**kw)
        self._out = out
        self._configsave = configsave
        self.results: dict = {}
        self.save_calls: list = []

    def check(self, name, ok, detail=None):
        self.results[name] = {"ok": bool(ok), "detail": detail}

    def build(self):
        self.theme_cls.theme_style = "Dark"
        self.theme_cls.primary_palette = "Orange"
        arceus, bizhawk, munchlax, obs_ws, overlay = _mock_backends()
        sp = {"common_path": "C:/sprites/pkmn", "items_path": "C:/sprites/items",
              "badges_path": "C:/sprites/badges", "single_path_check": True,
              "obs_2_pc": False}
        bh = {"path": "C:/BizHawk/EmuHawk.exe", "port": "41001"}
        obs = {"host": "127.0.0.1", "port": "4455", "password": "geheim"}
        rem = {"client_port": "43876", "helper_port": "43887",
               "server_ip_adresse": "203.0.113.5", "server_port": "43876"}
        pl = {"your_name": "Alice", "session_game": "Feuerrot", "player_count": 2,
              "remote_1": False, "obs_1": False, "remote_2": True, "obs_2": True}
        rnd = {"jar_path": "C:/rnd/Rando.jar", "java_path": "", "settings_path": "C:/rnd/a.rnqs",
               "rom_path": "C:/roms/fr.gba"}
        ov = {"port": "43888", "layout": "horizontal", "badge_layout": "horizontal",
              "animation_duration_ms": 300}
        nuz: dict = {}

        self.settings = SettingsMenu(arceus, bizhawk, munchlax, obs_ws, overlay,
                                     "84.1.2.3", "2a01::7f3", self._configsave,
                                     sp, rem, obs, bh, pl, rnd, ov, nuz, "9.9.9")
        self.sm = MDScreenManager(transition=FadeTransition())
        self.sm.add_widget(self.settings)
        return self.sm

    def on_start(self):
        self.sm.current = "SettingsMenu"
        Clock.schedule_once(lambda dt: self.safe(self.s_render), 1.0)
        Clock.schedule_once(lambda dt: self.stop(), 24)

    def _next(self, step, delay=1.2):
        Clock.schedule_once(lambda dt: self.safe(step), delay)

    def safe(self, step):
        try:
            step()
        except Exception:
            self.results[step.__name__] = {"ok": False, "detail": traceback.format_exc()}

    def shot(self, name):
        Clock.schedule_once(lambda dt: Window.screenshot(name=f"{self._out}/{name}.png"), 0.5)

    def s_render(self):
        s = self.settings
        # Feld-Referenzen vorhanden
        self.check("felder_vorhanden",
                   all(getattr(s, a, None) is not None for a in
                       ("_bizhawk_exe", "_obs_host", "_ip_server", "_overlay_port",
                        "_common_path", "_your_name", "_jar_path", "_console_level")))
        # 5 Content-Panes = 5 Tabs gerendert
        self.check("fuenf_panes", len(self.settings._panes) == 5, len(self.settings._panes))
        # Config-Werte geladen
        self.check("bizhawk_geladen", s._bizhawk_exe.text == "C:/BizHawk/EmuHawk.exe"
                   and s._bizhawk_port.text == "41001", s._bizhawk_port.text)
        self.check("name_geladen", s._your_name.text == "Alice", s._your_name.text)
        self.check("session_game_readonly", s._session_game.text == "Feuerrot"
                   and s._session_game.disabled, s._session_game.text)
        self.check("count_segment_2", s._count_items[2].active, 2)
        # Overlay-Links gebaut (Solo-Team + 2 Spieler)
        self.check("overlay_links_gebaut", len(s._overlay_links_box.children) > 0,
                   len(s._overlay_links_box.children))
        self.shot("01_verbindung")
        self._next(self.s_sprites)

    def s_sprites(self):
        self.settings.select_tab("Sprites")
        # Kein MDTabsCarousel im Widgetbaum (fing Touches ab) — Regression-Guard.
        from kivymd.uix.tab import MDTabsCarousel
        has_carousel = any(isinstance(w, MDTabsCarousel) for w in self.settings.walk())
        self.check("kein_carousel", not has_carousel)
        self.shot("02_sprites")
        self._next(self.s_obs_toggle)

    def s_obs_toggle(self):
        s = self.settings
        # save_sprites mocken, damit kein YAML/Server-Restart passiert
        s.controller.save_sprites = lambda values: self.save_calls.append(values)
        before = len(self.save_calls)
        s._obs2pc_switch.active = True
        self.check("obs2pc_expand", len(s._obs_expand.children) > 0, len(s._obs_expand.children))
        self.check("obs2pc_save", len(self.save_calls) > before
                   and self.save_calls[-1].get("obs_2_pc") is True, self.save_calls[-1:])
        self.shot("03_sprites_2pc")
        self._next(self.s_player)

    def s_player(self):
        s = self.settings
        self.settings.select_tab("Spieler")
        self.check("player_expand_2", len(s._player_switches) == 4, list(s._player_switches))
        self.shot("04_spieler")
        self._next(self.s_randomizer)

    def s_randomizer(self):
        self.settings.select_tab("Randomizer")
        self.shot("05_randomizer")
        self._next(self.s_logging)

    def s_logging(self):
        s = self.settings
        self.settings.select_tab("Logging")
        self.check("console_level_dd", s._console_level is not None)
        # DropdownField-Wiring: on_release öffnet das MDDropdownMenu. Zusammen mit
        # "kein_carousel" (keine Touch-fressende Carousel mehr) deckt das den
        # gemeldeten "Dropdown reagiert nicht"-Bug ab.
        try:
            s._console_level.dispatch("on_release")
            opened = getattr(s._console_level, "_menu", None) is not None
            if opened:
                s._console_level._menu.dismiss()
        except Exception:
            opened = False
        self.check("dropdown_oeffnet_menu", opened)
        self.shot("06_logging")
        self._next(self.s_finish, 0.8)

    def s_finish(self):
        self.stop()


_EXPECTED = {"felder_vorhanden", "fuenf_panes", "bizhawk_geladen", "name_geladen",
             "session_game_readonly", "count_segment_2", "overlay_links_gebaut",
             "kein_carousel", "obs2pc_expand", "obs2pc_save", "player_expand_2",
             "console_level_dd", "dropdown_oeffnet_menu"}


@pytest.mark.gui
def test_settingsmenu_aufbau_und_interaktion(screenshot_dir, config_root, run_md_app):
    app = run_md_app(_DriveApp(screenshot_dir, config_root))
    fehlende = _EXPECTED - set(app.results)
    assert not fehlende, f"Checks nicht gelaufen (Step-Crash?): {fehlende}"
    fehler = {k: v["detail"] for k, v in app.results.items() if not v["ok"]}
    assert not fehler, fehler
