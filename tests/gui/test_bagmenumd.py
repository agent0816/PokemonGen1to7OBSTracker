"""GUI-Durchlauf Bag-Screen (KivyMD) gegen eine temporaere Session-DB.

Prueft: Sektionen + Pocket-Karten gerendert, Item-Namen, Pocket-Filter,
"nur Baelle"-Filter, Zaehler. Schreib-Pfad (Sonderbonbon) wird nicht ausgefuehrt
(bizhawk/citra = None -> kein Button).
Marker ``gui``: braucht ein echtes GL-Fenster.

Ausfuehrung:
    .venv/py3115/Scripts/python.exe -m pytest tests/gui/test_bagmenumd.py -v
"""
from __future__ import annotations

import traceback
from pathlib import Path

import pytest

from kivy.clock import Clock
from kivy.core.window import Window
from kivymd.app import MDApp
from kivymd.uix.label import MDLabel
from kivymd.uix.screenmanager import MDScreenManager

from backend.bag_decoder import BagItem
from backend.classes.pokedex_db import PokedexDB
from frontend.widgets.bagmenumd import BagMenu, PocketCard


def _seed_db(session_dir: str) -> None:
    db = PokedexDB(Path(session_dir))
    db.connect()
    try:
        # Alice: Gen 3 (Feuerrot=34), Bob: Gen 4 (Platin=43)
        db.upsert_bag_pocket("Alice_1", 34, "baelle", [BagItem(1, 1), BagItem(4, 5)])
        db.upsert_bag_pocket("Alice_1", 34, "medizin", [BagItem(13, 3)])
        db.upsert_bag_pocket("Alice_1", 34, "items", [BagItem(68, 2)])
        db.upsert_bag_pocket("Bob_2", 43, "baelle", [BagItem(3, 2)])
        db.upsert_bag_pocket("Bob_2", 43, "medizin", [BagItem(17, 4)])
    finally:
        db.close()


class _DriveApp(MDApp):
    def __init__(self, session_dir: str, out: str, **kw):
        super().__init__(**kw)
        self._session_dir = session_dir
        self._out = out
        self.results: dict = {}

    def check(self, name, ok, detail=None):
        self.results[name] = {"ok": bool(ok), "detail": detail}

    def build(self):
        self.theme_cls.theme_style = "Dark"
        self.theme_cls.primary_palette = "Orange"
        self.sm = MDScreenManager()
        self.menu = BagMenu(self._session_dir, {"player_count": 2}, {}, None, None, "test")
        self.sm.add_widget(self.menu)
        return self.sm

    def on_start(self):
        self._tries = 0
        self.sm.current = "BagMenu"
        Clock.schedule_once(lambda dt: self.safe(self.s_all), 1.0)

    def _next(self, step, delay=1.2):
        Clock.schedule_once(lambda dt: self.safe(step), delay)

    def safe(self, step):
        try:
            step()
        except Exception:
            self.results[step.__name__] = {"ok": False, "detail": traceback.format_exc()}

    def shot(self, name):
        Clock.schedule_once(lambda dt: Window.screenshot(name=f"{self._out}/{name}.png"), 0.6)

    def _cards(self):
        return [w for w in self.menu.content_box.walk(restrict=True) if isinstance(w, PocketCard)]

    def _texts(self):
        return {w.text for w in self.menu.content_box.walk(restrict=True) if isinstance(w, MDLabel)}

    def s_all(self):
        cards = self._cards()
        if not cards and self._tries < 20:
            self._tries += 1
            self._next(self.s_all, 0.3)
            return
        self.check("alle_5_karten", len(cards) == 5, len(cards))
        self.check("count_6", self.menu.count_label.text == "6 Einträge", self.menu.count_label.text)
        texts = self._texts()
        self.check("sektion_alice", any("Spieler 1 — Edition 34 (Feuerrot)" in t for t in texts), texts)
        self.check("sektion_bob", any("Spieler 2 — Edition 43 (Platin)" in t for t in texts))
        self.check("item_namen", {"Master Ball", "Poke Ball", "Potion", "Rare Candy"} <= texts, texts)
        self.check("qty_label", "×5" in texts, texts)
        self.check("kein_candy_ohne_emulator", "Sonderbonbons:" not in texts)
        self.shot("01_bag_alle")
        self._next(self.s_pocket)

    def s_pocket(self):
        self.menu._pick_pocket("Medizin", "Medizin")
        cards = self._cards()
        self.check("pocket_medizin_2_karten", len(cards) == 2, len(cards))
        self.check("pocket_count_2", self.menu.count_label.text == "2 Einträge", self.menu.count_label.text)
        self.menu._pick_pocket("alle", "alle")
        self._next(self.s_balls, 0.3)

    def s_balls(self):
        self.menu.balls_only_switch.active = True
        self.menu._apply_filters()  # Debounce umgehen
        cards = self._cards()
        self.check("baelle_2_karten", len(cards) == 2, len(cards))
        self.check("baelle_count_3", self.menu.count_label.text == "3 Einträge", self.menu.count_label.text)
        self.check("baelle_kein_potion", "Potion" not in self._texts())
        self.shot("02_bag_baelle")
        self.menu.balls_only_switch.active = False
        self._next(self.s_player, 0.3)

    def s_player(self):
        self.menu._pick_player("Bob_2", "Spieler 2")
        self.check("spieler_bob_2_karten", len(self._cards()) == 2, len(self._cards()))
        self._next(self.s_finish, 0.8)

    def s_finish(self):
        self.stop()


_EXPECTED = {
    "alle_5_karten", "count_6", "sektion_alice", "sektion_bob", "item_namen", "qty_label",
    "kein_candy_ohne_emulator", "pocket_medizin_2_karten", "pocket_count_2",
    "baelle_2_karten", "baelle_count_3", "baelle_kein_potion", "spieler_bob_2_karten",
}


@pytest.mark.gui
def test_bag_sektionen_und_filter(config_root, screenshot_dir, run_md_app):
    _seed_db(config_root)
    app = run_md_app(_DriveApp(config_root, screenshot_dir))

    fehlende = _EXPECTED - set(app.results)
    assert not fehlende, f"Checks nicht gelaufen (Step-Crash?): {fehlende} / {app.results}"

    fehler = {k: v["detail"] for k, v in app.results.items() if not v["ok"]}
    assert not fehler, fehler
