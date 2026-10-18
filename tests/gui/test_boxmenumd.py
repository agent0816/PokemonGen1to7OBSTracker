"""GUI-Durchlauf BoxMenu (KivyMD) mit gemocktem Munchlax (kein Emulator).

Der Emulator-Refresh-Pfad wird bewusst NICHT ausgeführt.

Ausfuehrung:
    .venv/py3115/Scripts/python.exe -m pytest tests/gui/test_boxmenumd.py -v
"""
from __future__ import annotations

import traceback
from types import SimpleNamespace

import pytest

from kivy.clock import Clock
from kivy.core.window import Window
from kivymd.app import MDApp
from kivymd.uix.label import MDLabel
from kivymd.uix.screenmanager import MDScreenManager

from frontend.widgets.boxmenumd import BoxMenu, BoxSlotCard


def _poke(dexnr: int, nick: str, lvl: int) -> SimpleNamespace:
    return SimpleNamespace(dexnr=dexnr, nickname=nick, lvl=lvl, shiny=False, female=False, form="")


def _box(prefix: str) -> list:
    box: list = [None] * 30
    box[0] = _poke(25, f"{prefix}Blitz", 12)
    box[1] = _poke(6, f"{prefix}Draco", 34)
    box[7] = _poke(7, f"{prefix}Schiggy", 5)
    return box


class _FakeObs:
    conf = {"common_path": "x", "red": "y"}

    def get_sprite(self, pokemon, anim, edition, two_pc=False):
        return ""


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
        munchlax = SimpleNamespace(boxes={1: [_box("A"), _box("B")]}, editions={1: 34})
        self.menu = BoxMenu(None, None, munchlax, _FakeObs(), {"player_count": 2})
        self.sm.add_widget(self.menu)
        return self.sm

    def on_start(self):
        self.sm.current = "BoxMenu"
        Clock.schedule_once(lambda dt: self.safe(self.s_render), 1.0)

    def _next(self, step, delay=1.0):
        Clock.schedule_once(lambda dt: self.safe(step), delay)

    def safe(self, step):
        try:
            step()
        except Exception:
            self.results[step.__name__] = {"ok": False, "detail": traceback.format_exc()}

    def shot(self, name):
        Clock.schedule_once(lambda dt: Window.screenshot(name=f"{self._out}/{name}.png"), 0.6)

    def _texts(self):
        return [w.text for w in self.menu.walk(restrict=True) if isinstance(w, MDLabel)]

    def s_render(self):
        self.menu._render_current_box()
        texts = self._texts()
        cards = [w for w in self.menu.grid.children if isinstance(w, BoxSlotCard)]
        self.check("dreissig_slots", len(cards) == 30, len(cards))
        self.check("nickname_a", "ABlitz" in texts and "ADraco" in texts, texts)
        self.check("level_text", "Lv 12" in texts and "Lv 34" in texts, texts)
        self.check("nav_vorhanden", self.menu.prev_button and self.menu.next_button
                   and self.menu.player_button_text.text == "Spieler: 1")
        self.check("box_label", self.menu.box_button_text.text == "Box 1/2",
                   self.menu.box_button_text.text)
        self.shot("01_box1")
        self._next(self.s_next)

    def s_next(self):
        self.menu._next_box(None)
        self.check("box2_nickname", "BBlitz" in self._texts(), self._texts())
        self.check("box2_label", self.menu.box_button_text.text == "Box 2/2")
        self.menu._next_box(None)
        self.check("wrap_box1", self.menu.current_box_index == 0)
        self.shot("02_box_wrap")
        self._next(self.s_finish, 0.8)

    def s_finish(self):
        self.stop()


_EXPECTED = {"dreissig_slots", "nickname_a", "level_text", "nav_vorhanden", "box_label",
             "box2_nickname", "box2_label", "wrap_box1"}


@pytest.mark.gui
def test_boxmenu_render_und_nav(screenshot_dir, run_md_app):
    app = run_md_app(_DriveApp(screenshot_dir))

    fehlende = _EXPECTED - set(app.results)
    assert not fehlende, f"Checks nicht gelaufen (Step-Crash?): {fehlende}"
    fehler = {k: v["detail"] for k, v in app.results.items() if not v["ok"]}
    assert not fehler, fehler
