"""GUI-Durchlauf Status-Leiste (KivyMD): Countdown/Timer bleibt bei schmalem
Fenster sichtbar (Kompakt-Stufen), Race-Timer-Zusatz zeigt Konsens-Anfragen.

Live-Session 2026-11-18: Countdown verschwand rechts aus der Leiste, wenn das
Fenster zu schmal war.

Ausfuehrung:
    .venv/py3115/Scripts/python.exe -m pytest tests/gui/test_statusbarmd.py -v
"""
from __future__ import annotations

import time
import traceback
from types import SimpleNamespace

import pytest

from kivy.clock import Clock
from kivy.core.window import Window
from kivymd.app import MDApp
from kivymd.uix.boxlayout import MDBoxLayout
from kivy.uix.widget import Widget

from frontend.widgets.statusbarmd import StatusBarMD


def _munchlax():
    clients = {f"c{i}": "connected" for i in range(4)}
    return SimpleNamespace(
        is_connected=True,
        timer_state={"running": True, "start_ts": time.time() - 3725,
                     "pause_consent": ["c1"], "consent_required": 4},
        countdown_state={"duration_seconds": 300, "running": True, "finished": False,
                         "label": "Pause vorbei in"},
        current_timer_elapsed=lambda: 3725.0,
        current_countdown_remaining=lambda: 125.0,
        snapshot_remote_connection_status=lambda: dict(clients),
        snapshot_remote_connection_names=lambda: {c: f"Mitspieler{c}" for c in clients},
        remote_connection_status=clients,
    )


class _DriveApp(MDApp):
    def __init__(self, out: str, **kw):
        super().__init__(**kw)
        self._out = out
        self.results: dict = {}
        self.rem = {"start_server": False}
        self.munchlax = _munchlax()
        dead = SimpleNamespace(is_connected=False)
        self.arceus = self.obs_websocket = self.overlay_server = dead
        self.bizhawk = self.citra = dead

    def check(self, name, ok, detail=None):
        self.results[name] = {"ok": bool(ok), "detail": detail}

    def build(self):
        self.theme_cls.theme_style = "Dark"
        root = MDBoxLayout(orientation="vertical")
        root.add_widget(Widget())
        self.bar = StatusBarMD()
        root.add_widget(self.bar)
        return root

    def on_start(self):
        Window.size = (1500, 400)
        self._next(self.s_breit, 3.5)

    def _next(self, step, delay=1.0):
        Clock.schedule_once(lambda dt: self.safe(step), delay)

    def safe(self, step):
        try:
            step()
        except Exception:
            self.results[step.__name__] = {"ok": False, "detail": traceback.format_exc()}

    def shot(self, name):
        Clock.schedule_once(lambda dt: Window.screenshot(name=f"{self._out}/{name}.png"), 0.4)

    def _timer_sichtbar(self) -> bool:
        slot = self.bar.timer_slot
        return slot.parent is self.bar and slot.right <= self.bar.right + 1 and slot.x >= self.bar.x

    def s_breit(self):
        chip = self.bar.timer_chip
        self.check("konsens_im_text", "Pause angefragt 1/4" in chip.race_label.text,
                   chip.race_label.text)
        self.check("breit_stufe0", self.bar._compact_stage == 0, self.bar._compact_stage)
        self.check("breit_timer_sichtbar", self._timer_sichtbar(),
                   (self.bar.timer_slot.x, self.bar.timer_slot.right, self.bar.right))
        self.shot("01_breit")
        Clock.schedule_once(lambda dt: setattr(Window, "size", (620, 400)), 1.0)
        self._next(self.s_schmal, 4.5)

    def s_schmal(self):
        self.check("schmal_kompakt", self.bar._compact_stage >= 1, self.bar._compact_stage)
        self.check("schmal_timer_sichtbar", self._timer_sichtbar(),
                   (self.bar.timer_slot.x, self.bar.timer_slot.right, self.bar.right,
                    self.bar._compact_stage))
        self.shot("02_schmal")
        Clock.schedule_once(lambda dt: setattr(Window, "size", (1500, 400)), 1.0)
        self._next(self.s_wieder_breit, 4.5)

    def s_wieder_breit(self):
        self.check("wieder_stufe0", self.bar._compact_stage == 0, self.bar._compact_stage)
        self.check("clients_wieder_da", self.bar.client_area.parent is self.bar)
        self.check("namen_wieder_da", self.bar._items[0].name_label.text == "Server",
                   self.bar._items[0].name_label.text)
        self.shot("03_wieder_breit")
        self._next(self.stop, 0.8)


_EXPECTED = {"konsens_im_text", "breit_stufe0", "breit_timer_sichtbar", "schmal_kompakt",
             "schmal_timer_sichtbar", "wieder_stufe0", "clients_wieder_da", "namen_wieder_da"}


@pytest.mark.gui
def test_statusbar_timer_bleibt_sichtbar(screenshot_dir, run_md_app):
    app = run_md_app(_DriveApp(screenshot_dir))

    fehlende = _EXPECTED - set(app.results)
    assert not fehlende, f"Checks nicht gelaufen (Step-Crash?): {fehlende}"
    fehler = {k: v["detail"] for k, v in app.results.items() if not v["ok"]}
    assert not fehler, fehler
