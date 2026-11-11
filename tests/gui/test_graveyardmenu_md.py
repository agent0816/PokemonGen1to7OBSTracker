"""GUI-Durchlauf Friedhof-Screen (KivyMD) mit Fake-Munchlax.

Prueft: Gruppen-Karten (Soullink-Gruppe + Einzelgrab), Spieler-Filter reduziert
Eintraege, "zaehlt nicht"-Hinweis, Leer-Zustand.
Marker ``gui``: braucht ein echtes GL-Fenster.

Ausfuehrung:
    .venv/py3115/Scripts/python.exe -m pytest tests/gui/test_graveyardmenu_md.py -v
"""
from __future__ import annotations

import traceback

import pytest

from kivy.clock import Clock
from kivy.core.window import Window
from kivymd.app import MDApp
from kivymd.uix.label import MDLabel
from kivymd.uix.screenmanager import MDScreenManager

from frontend.widgets.graveyardmenu_md import (
    EMPTY_TEXT, GraveGroupCard, GraveRow, GraveyardMenu,
)


def _grave(gid: int, owner: str, ts: float, link_id=None, cause="faint", dexnr=25) -> dict:
    return {
        "id": gid, "session": "S", "run_id": "r1", "personality": 1000 + gid,
        "owner_name": owner, "owner_slot": 1, "ts": ts, "edition": 34, "dexnr": dexnr,
        "form": "", "female": 0, "shiny": 0, "nickname": "", "level": 20 + gid,
        "route": None, "method": "walk", "cause": cause, "link_id": link_id,
        "partners": [], "team": "A",
    }


GRAVES = [
    _grave(1, "Alice", 100.0, link_id=7),
    _grave(2, "Bob", 101.0, link_id=7, cause="soullink_partner", dexnr=4),
    _grave(3, "Alice", 200.0, dexnr=1),
]


class _FakeMunchlax:
    def __init__(self, graves):
        self._graves = graves

    def graveyard_session_name(self) -> str:
        return "S"

    def graveyard_sessions(self) -> list[str]:
        return ["S"]

    def snapshot_graves(self, session=None) -> list[dict]:
        return list(self._graves)

    def add_state_listener(self, cb) -> None:
        pass


class _DriveApp(MDApp):
    def __init__(self, out: str, graves: list[dict], **kw):
        super().__init__(**kw)
        self._out = out
        self._graves = graves
        self.results: dict = {}

    def check(self, name, ok, detail=None):
        self.results[name] = {"ok": bool(ok), "detail": detail}

    def build(self):
        self.theme_cls.theme_style = "Dark"
        self.theme_cls.primary_palette = "Orange"
        self.sm = MDScreenManager()
        self.menu = GraveyardMenu(_FakeMunchlax(self._graves), None)
        self.sm.add_widget(self.menu)
        return self.sm

    def on_start(self):
        self._tries = 0
        self.sm.current = "GraveyardMenu"
        Clock.schedule_once(lambda dt: self.safe(self.s_all), 1.0)

    def _next(self, step, delay=1.0):
        Clock.schedule_once(lambda dt: self.safe(step), delay)

    def safe(self, step):
        try:
            step()
        except Exception:
            self.results[step.__name__] = {"ok": False, "detail": traceback.format_exc()}

    def shot(self, name):
        Clock.schedule_once(lambda dt: Window.screenshot(name=f"{self._out}/{name}.png"), 0.6)

    def _cards(self):
        return [w for w in self.menu.cards_box.children if isinstance(w, GraveGroupCard)]

    def _rows(self):
        return [w for w in self.menu.cards_box.walk(restrict=True) if isinstance(w, GraveRow)]

    def _texts(self):
        return {w.text for w in self.menu.cards_box.walk(restrict=True) if isinstance(w, MDLabel)}

    def s_all(self):
        # Auf Zustand pollen: Laden laeuft im Executor
        geladen = len(self._cards()) >= 2 if self._graves else bool(self.menu.empty_hint.text)
        if not geladen and self._tries < 20:
            self._tries += 1
            self._next(self.s_all, 0.3)
            return
        if not self._graves:
            self.check("leer_hinweis", self.menu.empty_hint.text == EMPTY_TEXT, self.menu.empty_hint.text)
            self.check("leer_keine_karten", len(self._cards()) == 0)
            self.shot("00_leer")
            self._next(self.s_finish, 0.8)
            return
        self.check("zwei_gruppen", len(self._cards()) == 2, len(self._cards()))
        self.check("drei_zeilen", len(self._rows()) == 3, len(self._rows()))
        texts = self._texts()
        self.check("soullink_untertitel", "Soullink-Gruppe" in texts, texts)
        self.check("zaehlt_nicht", any("zählt nicht" in t for t in texts), texts)
        self.check("count_3", self.menu.count_label.text == "3 Einträge", self.menu.count_label.text)
        self.shot("01_alle")
        self._next(self.s_owner)

    def s_owner(self):
        self.menu._pick_owner("Alice", "Alice")
        self.check("alice_2_zeilen", len(self._rows()) == 2, len(self._rows()))
        self.check("alice_2_gruppen", len(self._cards()) == 2, len(self._cards()))
        self.menu._pick_owner("Bob", "Bob")
        self.check("bob_1_zeile", len(self._rows()) == 1, len(self._rows()))
        self.check("bob_button_text", self.menu.owner_text.text == "Spieler: Bob", self.menu.owner_text.text)
        self.menu._pick_owner(None, "Alle")
        self.check("alle_wieder_3", len(self._rows()) == 3, len(self._rows()))
        self._next(self.s_finish, 0.5)

    def s_finish(self):
        self.stop()


_EXPECTED_FULL = {
    "zwei_gruppen", "drei_zeilen", "soullink_untertitel", "zaehlt_nicht", "count_3",
    "alice_2_zeilen", "alice_2_gruppen", "bob_1_zeile", "bob_button_text", "alle_wieder_3",
}


@pytest.mark.gui
def test_friedhof_gruppen_und_filter(screenshot_dir, run_md_app):
    app = run_md_app(_DriveApp(screenshot_dir, GRAVES))

    fehlende = _EXPECTED_FULL - set(app.results)
    assert not fehlende, f"Checks nicht gelaufen (Step-Crash?): {fehlende} / {app.results}"
    fehler = {k: v["detail"] for k, v in app.results.items() if not v["ok"]}
    assert not fehler, fehler
