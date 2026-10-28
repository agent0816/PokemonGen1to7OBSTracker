"""GUI-Durchlauf MapMenu (KivyMD) für Gen 4 Sinnoh: Regionskarte Platin (ed 43),
Position auf Zweiblattdorf (Section 1 = met_location/mapLabelTextID), Hotspot-Klick
auf Route 201 (Section 16) zeigt Legende.

Prüft speziell den Offset-/Zellen-Modus der MapCanvas (px = x*7+6 / z*7-36) und
dass der Sinnoh-Hotspot-Datensatz + das gerenderte Kartenbild zusammenpassen.

Kein Emulator, kein asyncio-Loop → DB-Encounter-Load übersprungen (Legende zeigt
"Route frei").

Ausfuehrung:
    .venv/py3115/Scripts/python.exe -m pytest tests/gui/test_mapmenumd_sinnoh.py -v
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


def _munchlax():
    return SimpleNamespace(
        editions={1: 43},  # Platin → Sinnoh
        snapshot_positions=lambda: {
            # x/y = globale Sinnoh-Matrix-Koords (für Marker irrelevant),
            # map_section = Zweiblattdorf (1), edition = Platin.
            1: {"x": 112, "y": 884, "map_section": 1, "map_layout": 411,
                "edition": 43}
        },
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
                             "session_game": "Platin"}, "test_bg")
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
        if not self.menu.map_canvas.hotspots and _tries < 20:
            Clock.schedule_once(lambda dt: self.safe(lambda: self.s_render(_tries + 1)), 0.3)
            return
        self.menu._poll_position()
        hs = self.menu.map_canvas.hotspots
        self.check("hotspots_geladen", len(hs) >= 50, len(hs))
        self.check("bild_geladen", self.menu.map_canvas._texture is not None)
        self.check("bildgroesse", (self.menu.map_canvas.img_w, self.menu.map_canvas.img_h) == (216, 168),
                   (self.menu.map_canvas.img_w, self.menu.map_canvas.img_h))
        self.check("region_titel", "Sinnoh" in self.menu.title_label.text,
                   self.menu.title_label.text)
        self.check("position_zweiblatt", self.menu.map_canvas.current_section == 1,
                   self.menu.map_canvas.current_section)
        self.check("zweiblatt_hotspot", 1 in hs and hs[1].get("label") == "Zweiblattdorf",
                   hs.get(1))
        # Offset-Modus: Zweiblattdorf-Rect muss px = x*7+1 / z*7-38 sein
        # (cell 3,27 -> (22,151)); Offset per Routen-Pixel-Coverage kalibriert
        # (alt 6/-36 sass ~5px rechts, ~2px tief).
        self.check("offset_rect", hs.get(1, {}).get("rects") == [(22, 151, 7, 7)],
                   hs.get(1, {}).get("rects"))
        self.shot("03_map_sinnoh")
        self._next(self.s_click)

    def s_click(self):
        self.menu._on_section_selected(16)  # Route 201
        self.check("legende_titel", self.menu.legend_title.text.startswith("Route 201")
                   and "Wild" in self.menu.legend_title.text,
                   self.menu.legend_title.text)
        self.check("legende_befuellt", len(self.menu.legend_box.children) >= 1,
                   len(self.menu.legend_box.children))
        self.shot("04_legend_sinnoh")
        self._next(self.s_overlap)

    def s_overlap(self):
        # See der Wahrheit (76) teilt die Zellen mit dem Wahrheitsufer (72):
        # Sammel-Legende zeigt beide mit Zwischenüberschrift.
        hs = self.menu.map_canvas.hotspots
        self.check("see_auf_ufer", 76 in hs and hs[76]["rects"] == hs[72]["rects"],
                   (hs.get(72), hs.get(76)))
        self.menu._on_section_selected([72, 76])
        self.check("sammel_titel",
                   self.menu.legend_title.text == "Wahrheitsufer / See der Wahrheit",
                   self.menu.legend_title.text)
        # 2 Überschriften + je 1 "Route frei"-Zeile.
        self.check("sammel_zeilen", len(self.menu.legend_box.children) == 4,
                   len(self.menu.legend_box.children))
        # pokemon-Feld: Ufer ohne Wilde, See mit Wilden; Neumondinsel per
        # px_rects auf der Insel-Grafik (nicht auf der Decomp-Zelle im Meer).
        self.check("pokemon_feld", hs[72]["pokemon"] == [] and "wild" in hs[76]["pokemon"],
                   (hs[72].get("pokemon"), hs[76].get("pokemon")))
        self.check("neumond_px_rect", hs.get(79, {}).get("rects") == [(40, 42, 7, 7)],
                   hs.get(79, {}).get("rects"))
        self.shot("05_legend_sammel")
        self._next(self.s_finish, 0.8)

    def s_finish(self):
        self.stop()


_EXPECTED = {"hotspots_geladen", "bild_geladen", "bildgroesse", "region_titel",
             "position_zweiblatt", "zweiblatt_hotspot", "offset_rect",
             "legende_titel", "legende_befuellt", "see_auf_ufer", "sammel_titel",
             "sammel_zeilen", "pokemon_feld", "neumond_px_rect"}


@pytest.mark.gui
def test_mapmenu_sinnoh_render_und_klick(screenshot_dir, run_md_app):
    app = run_md_app(_DriveApp(screenshot_dir))

    fehlende = _EXPECTED - set(app.results)
    assert not fehlende, f"Checks nicht gelaufen (Step-Crash?): {fehlende}"
    fehler = {k: v["detail"] for k, v in app.results.items() if not v["ok"]}
    assert not fehler, fehler
