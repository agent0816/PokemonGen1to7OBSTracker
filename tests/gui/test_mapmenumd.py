"""GUI-Durchlauf MapMenu (KivyMD): Regionskarte Kanto (Feuerrot), Position auf
Alabastia (MAPSEC 88), Hotspot-Klick zeigt Legende, Teilkarten-Wechsel
Kanto → Sevii 6–7 (Tanibo-Kammer) → zurück per Live-Position.

Kein Emulator, kein asyncio-Loop → der DB-Encounter-Load wird bewusst
übersprungen (die Legende zeigt dann "Route frei").

Ausfuehrung:
    .venv/py3115/Scripts/python.exe -m pytest tests/gui/test_mapmenumd.py -v
"""
from __future__ import annotations

import traceback
from types import SimpleNamespace

import pytest

from kivy.clock import Clock
from kivy.core.window import Window
from kivymd.app import MDApp
from kivymd.uix.screenmanager import MDScreenManager

from frontend.widgets.mapmenumd import MapMenu


class _FakeObs:
    conf = {"common_path": "x", "red": "y"}

    def get_sprite(self, pokemon, anim, edition, two_pc=False):
        return ""


# Veränderbare Live-Position (Steps setzen map_section um).
_POS = {"x": 10, "y": 5, "map_section": 88, "map_layout": 5, "edition": 34}


def _munchlax():
    return SimpleNamespace(
        editions={1: 34},  # Feuerrot → Kanto
        snapshot_positions=lambda: {1: dict(_POS)},
    )


class _DriveApp(MDApp):
    def __init__(self, out: str, **kw):
        super().__init__(**kw)
        self._out = out
        self.results: dict = {}

    def check(self, name, ok, detail=None):
        self.results[name] = {"ok": bool(ok), "detail": detail}

    def build(self):
        self.theme_cls.theme_style = "Dark"
        self.theme_cls.primary_palette = "Orange"
        self.sm = MDScreenManager()
        self.menu = MapMenu(_munchlax(), _FakeObs(),
                            {"player_count": 1, "your_name": "Test",
                             "session_game": "Feuerrot"}, "test_bg")
        self.sm.add_widget(self.menu)
        return self.sm

    def on_start(self):
        self.sm.current = "MapMenu"
        self._next(self.s_render, 1.0)

    def _next(self, step, delay=1.0):
        Clock.schedule_once(lambda dt: self.safe(step), delay)

    def safe(self, step):
        try:
            step()
        except Exception:
            self.results[step.__name__] = {"ok": False, "detail": traceback.format_exc()}

    def shot(self, name):
        Clock.schedule_once(lambda dt: Window.screenshot(name=f"{self._out}/{name}.png"), 0.6)

    def s_render(self, _tries=0):
        # Auf geladene Hotspots pollen statt feste Zeit.
        if not self.menu.map_canvas.hotspots and _tries < 20:
            Clock.schedule_once(lambda dt: self.safe(lambda: self.s_render(_tries + 1)), 0.3)
            return
        self.menu._poll_position()
        hs = self.menu.map_canvas.hotspots
        self.check("hotspots_geladen", len(hs) >= 50, len(hs))
        self.check("bild_geladen", self.menu.map_canvas._texture is not None)
        self.check("region_titel", "Kanto" in self.menu.title_label.text,
                   self.menu.title_label.text)
        self.check("position_alabastia", self.menu.map_canvas.current_section == 88,
                   self.menu.map_canvas.current_section)
        self.check("pallet_hotspot", 88 in hs and hs[88].get("label") == "Alabastia",
                   hs.get(88))
        self.shot("01_map_kanto")
        self._next(self.s_click)

    def s_click(self):
        self.menu._on_section_selected(88)
        self.check("legende_titel", self.menu.legend_title.text.startswith("Alabastia"),
                   self.menu.legend_title.text)
        self.check("legende_befuellt", len(self.menu.legend_box.children) >= 1,
                   len(self.menu.legend_box.children))
        self.shot("02_legend")
        self._next(self.s_sevii)

    def s_sevii(self):
        # Einamon-Kammer (188) liegt nur auf der Sevii-6–7-Karte → Auto-Wechsel.
        _POS["map_section"] = 188
        self.menu._route_catches = {188: [{"outcome": "caught", "dexnr": 201}]}
        self.menu._poll_position()
        # Reiner Teilkarten-Wechsel (gleiche Edition/Slot): kein Reset/Reload.
        self.check("catches_behalten", 188 in self.menu._route_catches,
                   self.menu._route_catches)
        self.check("sevii_wechsel", self.menu._region_file == "sevii_67_frlg.yml"
                   and "6–7" in self.menu.title_label.text,
                   (self.menu._region_file, self.menu.title_label.text))
        self.check("sevii_marker", self.menu.map_canvas.current_section == 188
                   and 188 in self.menu.map_canvas.hotspots,
                   self.menu.map_canvas.current_section)
        self.shot("03_map_sevii_67")
        self._next(self.s_zurueck)

    def s_zurueck(self):
        _POS["map_section"] = 89  # Vertania City → zurück auf Kanto
        self.menu._poll_position()
        self.check("kanto_zurueck", self.menu._region_file == "kanto_frlg.yml",
                   self.menu._region_file)
        _POS["map_section"] = 88
        self._next(self.s_finish, 0.8)

    def s_finish(self):
        self.stop()


_EXPECTED = {"hotspots_geladen", "bild_geladen", "region_titel", "position_alabastia",
             "pallet_hotspot", "legende_titel", "legende_befuellt", "sevii_wechsel",
             "sevii_marker", "kanto_zurueck", "catches_behalten"}


@pytest.mark.gui
def test_mapmenu_render_und_klick(screenshot_dir, run_md_app):
    app = run_md_app(_DriveApp(screenshot_dir))

    fehlende = _EXPECTED - set(app.results)
    assert not fehlende, f"Checks nicht gelaufen (Step-Crash?): {fehlende}"
    fehler = {k: v["detail"] for k, v in app.results.items() if not v["ok"]}
    assert not fehler, fehler
