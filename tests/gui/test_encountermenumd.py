"""GUI-Durchlauf Encounter-Screen (KivyMD) gegen eine temporaere Session-DB.

Prueft: flache Tabelle (Standardmodus), Spieler-Filter, Soullink-Gruppenkarten.
Marker ``gui``: braucht ein echtes GL-Fenster.

Ausfuehrung:
    .venv/py3115/Scripts/python.exe -m pytest tests/gui/test_encountermenumd.py -v
"""
from __future__ import annotations

import traceback
from pathlib import Path

import pytest

from kivy.clock import Clock
from kivy.core.window import Window
from kivymd.app import MDApp
from kivymd.uix.screenmanager import MDScreenManager

from backend.classes.pokedex_db import PokedexDB
from frontend.widgets.encountermenumd import EncounterRow, EncounterMenu, LinkGroupCard


def _seed_db(session_dir: str) -> None:
    db = PokedexDB(Path(session_dir))
    db.connect()
    try:
        # (pv, owner, edition, route, dexnr, lvl, shiny, outcome)
        flat = [
            (0x1, "Alice_1", 34, 1, 16, 3, False, "caught"),
            (0x2, "Alice_1", 34, 2, 19, 4, True, "not_caught"),
            (0x3, "Bob_2", 34, 3, 10, 5, False, "unknown"),
        ]
        for pv, owner, ed, route, dex, lvl, shiny, outcome in flat:
            db.insert_encounter(pv, owner, ed, route, dex, lvl, shiny, False, False, False, False,
                                "wild", outcome)
        # Soullink-Paar auf Route 4
        db.insert_encounter(0x10, "Alice_1", 34, 4, 25, 6, False, False, False, False, False,
                            "wild", "caught")
        db.insert_encounter(0x11, "Bob_2", 34, 4, 43, 6, False, False, False, False, False,
                            "wild", "caught")
        with db.access_lock:
            db.connection.execute("UPDATE encounters SET link_group_id = 1 WHERE route = 4")
            db.connection.commit()
    finally:
        db.close()


class _DriveApp(MDApp):
    def __init__(self, session_dir: str, out: str, **kw):
        super().__init__(**kw)
        self._session_dir = session_dir
        self._out = out
        self.results: dict = {}
        self.nuz = {"soullink_mode": "nuzlocke", "soullink_player_count": 2}

    def check(self, name, ok, detail=None):
        self.results[name] = {"ok": bool(ok), "detail": detail}

    def build(self):
        self.theme_cls.theme_style = "Dark"
        self.theme_cls.primary_palette = "Orange"
        self.sm = MDScreenManager()
        self.menu = EncounterMenu(self._session_dir, {"player_count": 2}, self.nuz, "test")
        self.sm.add_widget(self.menu)
        return self.sm

    def on_start(self):
        self._tries = 0
        self.sm.current = "EncounterMenu"
        Clock.schedule_once(lambda dt: self.safe(self.s_flat), 1.0)

    def _next(self, step, delay=1.2):
        Clock.schedule_once(lambda dt: self.safe(step), delay)

    def safe(self, step):
        try:
            step()
        except Exception:
            self.results[step.__name__] = {"ok": False, "detail": traceback.format_exc()}

    def shot(self, name):
        Clock.schedule_once(lambda dt: Window.screenshot(name=f"{self._out}/{name}.png"), 0.6)

    def _rows(self):
        return [w for w in self.menu.list_box.children if isinstance(w, EncounterRow)]

    def _cards(self):
        return [w for w in self.menu.cards_grid.children if isinstance(w, LinkGroupCard)]

    def s_flat(self):
        rows = self._rows()
        if not rows and self._tries < 20:
            self._tries += 1
            self._next(self.s_flat, 0.3)
            return
        self.check("flat_5_zeilen", len(rows) == 5, len(rows))
        self.check("flat_keine_karten", not self._cards())
        self.shot("01_encounter_flat")
        self._next(self.s_filter)

    def s_filter(self):
        self.menu._pick_player("Bob_2", "Bob_2")
        rows = self._rows()
        self.check("spieler_filter_bob", len(rows) == 2, len(rows))
        self.menu._pick_player("alle", "alle")
        self._next(self.s_soullink, 0.5)

    def s_soullink(self):
        self.nuz["soullink_mode"] = "coop"
        self.menu._apply_filters()
        cards = self._cards()
        self.check("soullink_1_gruppe", len(cards) == 1, len(cards))
        self.check("soullink_2_member", bool(cards) and len(cards[0].group["members"]) == 2)
        self.check("soullink_keine_flat_zeilen", not self._rows())
        self.shot("02_encounter_soullink")
        self._next(self.s_finish, 0.8)

    def s_finish(self):
        self.stop()


_EXPECTED = {
    "flat_5_zeilen", "flat_keine_karten", "spieler_filter_bob",
    "soullink_1_gruppe", "soullink_2_member", "soullink_keine_flat_zeilen",
}


@pytest.mark.gui
def test_encounter_flat_und_soullink(config_root, screenshot_dir, run_md_app):
    _seed_db(config_root)
    app = run_md_app(_DriveApp(config_root, screenshot_dir))

    fehlende = _EXPECTED - set(app.results)
    assert not fehlende, f"Checks nicht gelaufen (Step-Crash?): {fehlende}"

    fehler = {k: v["detail"] for k, v in app.results.items() if not v["ok"]}
    assert not fehler, fehler
