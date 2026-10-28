"""GUI-Durchlauf Pokedex (KivyMD) gegen eine temporaere Session-DB.

Prueft die Pokedex-v2-Erweiterungen am echten MD-Screen:
- async DB-Load + batched Rendern (P5): alle Karten landen im Grid
- Spieler-Buttons (P3): sichtbar bei 2 Spielern, Slot-Filter wirkt
- Spieler-Farbe pro Karte (P1): Akzent/md_bg_color gesetzt
- horizontale Karte (P4) baut ohne Fehler

Marker ``gui``: braucht ein echtes GL-Fenster, nicht headless.

Ausfuehrung:
    .venv/py3115/Scripts/python.exe -m pytest tests/gui/test_pokedexmenumd.py -v
"""
from __future__ import annotations

import traceback
from pathlib import Path
from types import SimpleNamespace

import pytest

from kivy.clock import Clock
from kivy.core.window import Window
from kivymd.app import MDApp
from kivymd.uix.screenmanager import MDScreenManager

from backend.classes.pokedex_db import PokedexDB
from frontend.widgets.pokedexmenumd import PokedexMenu, PokemonCard


def _poke(pv: int, dexnr: int, nick: str, shiny: bool = False) -> SimpleNamespace:
    return SimpleNamespace(
        personality=pv, dexnr=dexnr, nickname=nick, lvl=12, shiny=shiny, female=False,
        form="", item=None, route=None, nature=None, ability=None,
        ivs=None, evs=None, moves=None, experience_points=None,
    )


def _seed_db(session_dir: str) -> None:
    db = PokedexDB(Path(session_dir))
    db.connect()
    try:
        db.upsert_pokemon("Alice_1", 31, _poke(0x111, 25, "Blitz", shiny=True))
        db.upsert_pokemon("Alice_1", 31, _poke(0x222, 6, "Draco"))
        db.upsert_pokemon("Bob_2", 31, _poke(0x333, 7, ""))
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
        # obs_websocket=None -> Sprites leer (kein Repo noetig); pl mit 2 Spielern.
        self.menu = PokedexMenu(self._session_dir, None, "test", pl={"player_count": 2})
        self.sm.add_widget(self.menu)
        return self.sm

    def on_start(self):
        # on_pre_enter feuert beim current-Set -> async Load startet.
        self._tries = 0
        self.sm.current = "PokedexMenu"
        # Auf die async geladenen Karten pollen (statt feste Zeit), sonst flaky
        # bei GL-Startup-Jitter / geteiltem Prozess.
        Clock.schedule_once(lambda dt: self.safe(self.s_loaded), 1.0)

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
        return [w for w in self.menu.cards_grid.children if isinstance(w, PokemonCard)]

    def s_loaded(self):
        cards = self._cards()
        if not cards and self._tries < 20:
            self._tries += 1
            self._next(self.s_loaded, 0.3)
            return
        self.check("rows_geladen", len(self.menu._all_rows) == 3,
                   (len(self.menu._all_rows), self.menu._needs_reload, self.menu._load_task))
        self.check("drei_karten", len(cards) == 3, len(cards))
        self.check("player_bar_sichtbar", self.menu.player_bar.opacity == 1)
        self.check("buttons_alle_p1_p2", set(self.menu._player_buttons) == {"", "1", "2"},
                   list(self.menu._player_buttons))
        # P1: jede Karte hat eine Spieler-Toenung (md_bg_color mit Alpha != 0)
        self.check("karten_eingefaerbt", all(c.md_bg_color[3] > 0 for c in cards))
        self.shot("01_pokedex_alle")
        self._next(self.s_filter_p2)

    def s_filter_p2(self):
        self.menu._select_slot("2")
        cards = self._cards()
        self.check("p2_filter_eine_karte", len(cards) == 1, len(cards))
        self.shot("02_pokedex_p2")
        self._next(self.s_finish, 0.6)

    def s_finish(self):
        self.stop()


_EXPECTED = {
    "rows_geladen", "drei_karten", "player_bar_sichtbar", "buttons_alle_p1_p2",
    "karten_eingefaerbt", "p2_filter_eine_karte",
}


@pytest.mark.gui
def test_pokedex_spieler_und_render(config_root, screenshot_dir, run_md_app):
    _seed_db(config_root)
    app = run_md_app(_DriveApp(config_root, screenshot_dir))

    fehlende = _EXPECTED - set(app.results)
    assert not fehlende, f"Checks nicht gelaufen (Step-Crash?): {fehlende}"

    fehler = {k: v["detail"] for k, v in app.results.items() if not v["ok"]}
    assert not fehler, fehler
