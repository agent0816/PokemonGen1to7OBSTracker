"""GUI-Durchlauf Update-Dialog in der MD-UI (MDDialog statt Kivy-Popup).

Kein Netz: der Dialog wird direkt mit einem Fake-Archiv geöffnet; Fortschritt,
Button-Sperre und der "Backup fehlgeschlagen"-Dialog werden geprüft.

Ausfuehrung:
    .venv/py3115/Scripts/python.exe -m pytest tests/gui/test_updatemenu_md.py -v
"""
from __future__ import annotations

import traceback
from types import SimpleNamespace

import pytest

from kivy.clock import Clock
from kivy.core.window import Window
from kivymd.app import MDApp
from kivymd.uix.dialog import MDDialog
from kivymd.uix.screenmanager import MDScreenManager

from frontend.widgets.updatemenu import Update


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
        self.update = Update("PokemonOBSTracker", "0.8.0")
        self.sm.add_widget(self.update)
        return self.sm

    def on_start(self):
        self._next(self.s_dialog, 1.0)

    def _next(self, step, delay=1.0):
        Clock.schedule_once(lambda dt: self.safe(step), delay)

    def safe(self, step):
        try:
            step()
        except Exception:
            self.results[step.__name__] = {"ok": False, "detail": traceback.format_exc()}

    def shot(self, name):
        Clock.schedule_once(lambda dt: Window.screenshot(name=f"{self._out}/{name}.png"), 0.6)

    def _dialogs(self):
        return [w for w in Window.children if isinstance(w, MDDialog)]

    def s_dialog(self):
        u = self.update
        u.show_update_popup(SimpleNamespace(version="0.8.1"))
        self.check("md_dialog", isinstance(u.popup, MDDialog), type(u.popup).__name__)
        self.check("version_im_text", "0.8.1" in u._status_label.text, u._status_label.text)
        u._set_progress(42.0)
        self.check("fortschritt", abs(u.progress_bar.value - 42.0) < 0.01, u.progress_bar.value)
        u._set_download_enabled(False)
        self.check("buttons_gesperrt", u._download_btn.disabled and u._cancel_btn.disabled)
        u._set_download_enabled(True)
        u._set_status("Konfiguration gesichert: x.zip")
        self.check("status_text", u._status_label.text.startswith("Konfiguration gesichert"))
        self.shot("01_update_dialog")
        self._next(self.s_backup)

    def s_backup(self):
        before = len(self._dialogs())
        self.update._ask_update_without_backup()
        self.check("backup_dialog_offen", len(self._dialogs()) == before + 1,
                   (before, len(self._dialogs())))
        self.shot("02_backup_dialog")
        self._next(self.s_finish)

    def s_finish(self):
        for d in self._dialogs():
            d.dismiss()
        self._next(self.stop, 0.5)


_EXPECTED = {"md_dialog", "version_im_text", "fortschritt", "buttons_gesperrt",
             "status_text", "backup_dialog_offen"}


@pytest.mark.gui
def test_update_dialog_md(screenshot_dir, run_md_app):
    app = run_md_app(_DriveApp(screenshot_dir))
    fehlende = _EXPECTED - set(app.results)
    assert not fehlende, f"Checks nicht gelaufen (Step-Crash?): {fehlende}"
    fehler = {k: v["detail"] for k, v in app.results.items() if not v["ok"]}
    assert not fehler, fehler
