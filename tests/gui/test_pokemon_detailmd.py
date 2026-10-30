"""GUI-Durchlauf Pokemon-Detail (KivyMD): Pokedex-Karte -> Detail -> Zurück.

Prüft die Drilldown-Navigation und dass die Detailansicht aus einer DB-Zeile
befüllt wird (row_to_pokemon-Adapter + Sektionen gerendert).

Marker ``gui``: braucht ein echtes GL-Fenster.
Ausführung: .venv/py3115/Scripts/python -m pytest tests/gui/test_pokemon_detailmd.py -q
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
from frontend.widgets.pokemon_detailmd import PokemonDetailMD


def _poke(pv, dexnr, nick):
    return SimpleNamespace(
        personality=pv, dexnr=dexnr, nickname=nick, lvl=36, shiny=False, female=False,
        form="", item=None, route=None, nature=3, ability=9,
        ivs={"hp": 31, "attack": 30, "defense": 31},
        evs={"hp": 4, "speed": 252},
        moves=[{"id": 85, "pp": 15}, {"id": 33, "pp": 35}],
        experience_points=120000,
    )


def _seed_db(session_dir: str) -> None:
    db = PokedexDB(Path(session_dir))
    db.connect()
    try:
        db.upsert_pokemon("Alice_1", 31, _poke(0x111, 25, "Blitz"))
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
        self.menu = PokedexMenu(self._session_dir, None, "test", pl={"player_count": 1})
        self.detail = PokemonDetailMD(None)
        self.sm.add_widget(self.menu)
        self.sm.add_widget(self.detail)
        return self.sm

    def on_start(self):
        self._tries = 0
        self.sm.current = "PokedexMenu"
        # Erst klicken, wenn die async geladene Karte da ist (statt feste Zeit).
        Clock.schedule_once(lambda dt: self.safe(self.s_click), 1.0)

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

    def s_click(self):
        cards = self._cards()
        if not cards and self._tries < 20:
            self._tries += 1
            self._next(self.s_click, 0.3)
            return
        self.check("eine_karte", len(cards) == 1, len(cards))
        cards[0].dispatch("on_release")
        self._next(self.s_detail)

    def s_detail(self):
        self.check("detail_aktiv", self.sm.current == "PokemonDetail", self.sm.current)
        self.check("titel_gesetzt", "Blitz" in self.detail.title_label.text or "#25" in self.detail.title_label.text,
                   self.detail.title_label.text)
        self.check("sektionen_gerendert", len(self.detail.detail_layout.children) > 3,
                   len(self.detail.detail_layout.children))
        self.check("return_screen", self.detail.return_screen == "PokedexMenu", self.detail.return_screen)
        self.shot("01_detail")
        self._next(self.s_soullink_mitspieler)

    def _texts(self):
        return [w.text for w in self.detail.detail_layout.walk() if hasattr(w, "text")]

    def s_soullink_mitspieler(self):
        # Pokemon eines Mitspielers (Slot 4): link["members"] ist mit dem
        # Spielernamen geschlüsselt (arceus owner_root), nicht mit dem Slot.
        munchlax = SimpleNamespace(
            pl={"your_name": "Alice"},
            player_names={1: "Alice", 4: "Stephan"},
            soullink_links={7: {"link_id": 7, "route": 5, "state": "complete", "members": {
                "Stephan": {"personality": 0x444, "dexnr": 25, "outcome": "caught"},
                "Alice": {"personality": 0x111, "dexnr": 1, "outcome": "caught"},
            }}},
        )
        self.detail.show(_poke(0x444, 25, "Pika"), 31, return_screen="PokedexMenu",
                         munchlax=munchlax, owner=None, player_slot=4)
        texts = self._texts()
        self.check("soullink_partner_mitspieler", "Alice:" in texts, texts[-6:])
        self.check("soullink_kein_selbst", "Stephan:" not in texts)
        # Slot ohne bekannten Namen: kein Abschnitt — kein Fallback auf den
        # eigenen Namen (der alte Bug)
        munchlax.player_names = {1: "Alice"}
        self.detail.show(_poke(0x111, 1, "Bisa"), 31, return_screen="PokedexMenu",
                         munchlax=munchlax, owner=None, player_slot=4)
        texts = self._texts()
        self.check("soullink_slot_ohne_name", not any("Soullink-Partner" in str(t) for t in texts),
                   texts[-6:])
        self._next(self.s_back)

    def s_back(self):
        self.detail.back_button.dispatch("on_release")
        self.check("zurueck_pokedex", self.sm.current == "PokedexMenu", self.sm.current)
        self._next(self.s_finish, 0.6)

    def s_finish(self):
        self.stop()


_EXPECTED = {"eine_karte", "detail_aktiv", "titel_gesetzt", "sektionen_gerendert",
             "return_screen", "soullink_partner_mitspieler", "soullink_kein_selbst",
             "soullink_slot_ohne_name", "zurueck_pokedex"}


@pytest.mark.gui
def test_pokedex_detail_drilldown(config_root, screenshot_dir, run_md_app):
    _seed_db(config_root)
    app = run_md_app(_DriveApp(config_root, screenshot_dir))

    fehlende = _EXPECTED - set(app.results)
    assert not fehlende, f"Checks nicht gelaufen (Step-Crash?): {fehlende}"
    fehler = {k: v["detail"] for k, v in app.results.items() if not v["ok"]}
    assert not fehler, fehler
