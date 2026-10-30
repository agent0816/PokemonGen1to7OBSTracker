"""GUI-Durchlauf Pokemon-Editor (KivyMD): Bearbeiten-Button + Dialog + Schreiben.

Prüft: Button nur mit Edit-Ziel sichtbar (Remote/kein Ziel -> unsichtbar), Dialog
öffnet, Level-Änderung vergrößert den Pool, Schreiben ruft ``edit_pokemon`` mit
korrektem Ziel/PokemonEdit auf (BizHawk gemockt), Live-Refresh pausiert.

Marker ``gui``: braucht ein echtes GL-Fenster.
Ausführung: .venv/py3115/Scripts/python -m pytest tests/gui/test_pokemon_editor_md.py -q
"""
from __future__ import annotations

import traceback
from types import SimpleNamespace

import pytest

from kivy.clock import Clock
from kivymd.app import MDApp
from kivymd.uix.screenmanager import MDScreenManager

from frontend.widgets.pokemon_detailmd import PokemonDetailMD

EDITION = 33  # Smaragd (Gen 3)


def _poke(dexnr=25, pv=0x1234):
    ivs = {k: 20 for k in ("hp", "attack", "defense", "speed", "special_attack", "special_defense")}
    return SimpleNamespace(
        personality=pv, dexnr=dexnr, nickname="Blitz", lvl=10, shiny=False, female=False,
        form="", item=None, route=None, nature=3, ability=9, ivs=ivs, evs={},
        moves=[{"id": 84, "pp": 30}, {"id": 0, "pp": 0}, {"id": 0, "pp": 0}, {"id": 0, "pp": 0}],
        experience_points=1000,
    )


class _FakeBizhawk:
    def __init__(self):
        self.calls: list[tuple] = []

    async def edit_pokemon(self, client_id, target, edit, rando_data=None):
        self.calls.append((client_id, dict(target), edit, rando_data))
        return True, "Gespeichert."


class _DriveApp(MDApp):
    def __init__(self, **kw):
        super().__init__(**kw)
        self.results: dict = {}
        self.bh = _FakeBizhawk()
        self.pl = {"remote_1": False, "remote_2": True}
        self.target = {"kind": "party", "slot": 3, "personality": 0x1234}
        self._tries = 0

    def check(self, name, ok, detail=None):
        self.results[name] = {"ok": bool(ok), "detail": detail}

    def build(self):
        self.theme_cls.theme_style = "Dark"
        self.sm = MDScreenManager()
        self.detail = PokemonDetailMD(None)
        self.sm.add_widget(self.detail)
        return self.sm

    def on_start(self):
        self._next(self.s_button)

    def _next(self, step, delay=0.5):
        Clock.schedule_once(lambda dt: self.safe(step), delay)

    def safe(self, step):
        try:
            step()
        except Exception:
            self.results[step.__name__] = {"ok": False, "detail": traceback.format_exc()}

    def _visible(self):
        return self.detail.edit_button.opacity == 1 and not self.detail.edit_button.disabled

    def s_button(self):
        d = self.detail
        d.show(_poke(), EDITION)
        self.check("ohne_ziel_unsichtbar", not self._visible())
        d.set_edit_target(self.target, self.bh, 1, self.pl)
        self.check("mit_ziel_sichtbar", self._visible())
        d.show(_poke(), EDITION)
        self.check("show_setzt_zurueck", not self._visible())
        d.set_edit_target(self.target, self.bh, 2, self.pl)
        self.check("remote_unsichtbar", not self._visible())
        d.set_edit_target(self.target, None, 1, self.pl)
        self.check("ohne_bizhawk_unsichtbar", not self._visible())
        d.show(_poke(dexnr="egg"), EDITION)
        d.set_edit_target(self.target, self.bh, 1, self.pl)
        self.check("ei_unsichtbar", not self._visible())
        d.show(_poke(), 11)  # Gen 1: nicht unterstützt
        d.set_edit_target(self.target, self.bh, 1, self.pl)
        self.check("gen1_unsichtbar", not self._visible())
        d.show(_poke(), EDITION)
        d.set_edit_target(self.target, self.bh, 1, self.pl)
        d.edit_button.dispatch("on_release")
        self._next(self.s_dialog)

    def s_dialog(self):
        d = self.detail
        ed = d._editor
        self.check("dialog_offen", ed is not None and d._editor_open)
        pool_low = list(ed.pool)
        ed.change_level(+40)
        self.check("level_aktualisiert", ed.level == 50 and ed.level_field.text == "50", ed.level)
        self.check("pool_waechst", len(ed.pool) > len(pool_low), (len(pool_low), len(ed.pool)))
        self.check("bekannte_im_pool", 84 in ed.pool)
        self.check("vorschau_da", "KP" in ed.preview_label.text, ed.preview_label.text)
        # Level runter: bekannte Attacke bleibt im Pool
        ed.set_level(1)
        self.check("bekannte_bleibt", 84 in ed.pool)
        ed.set_level(50)
        ed.pick_move(1, 85)
        self.check("move_gewaehlt", ed.moves[1] == 85, ed.moves)
        ed.pick_move(2, 85)
        self.check("duplikat_abgelehnt", ed.moves[2] == 0, ed.moves)
        # Live-Refresh darf nicht neu bauen, solange Dialog offen
        d._refresh_src = (1, 0, SimpleNamespace(sorted_teams={}), None)
        d._live_refresh(0)
        d._refresh_src = None
        ed.write()
        self.check("button_busy", ed.write_button.disabled)
        self._next(self.s_written, 0.5)

    def s_written(self):
        if not self.bh.calls and self._tries < 20:
            self._tries += 1
            self._next(self.s_written, 0.3)
            return
        self.check("edit_aufgerufen", len(self.bh.calls) == 1, len(self.bh.calls))
        cid, target, edit, _rando = self.bh.calls[0]
        self.check("client_id", cid == "player001", cid)
        self.check("target", target == self.target, target)
        self.check("edit", edit.new_level == 50 and edit.moves == [84, 85, 0, 0], (edit.new_level, edit.moves))
        self._next(self.s_closed, 0.8)

    def s_closed(self):
        self.check("dialog_geschlossen", not self.detail._editor_open)
        self.stop()


_EXPECTED = {"ohne_ziel_unsichtbar", "mit_ziel_sichtbar", "show_setzt_zurueck", "remote_unsichtbar",
             "ohne_bizhawk_unsichtbar", "ei_unsichtbar", "gen1_unsichtbar", "dialog_offen",
             "level_aktualisiert", "pool_waechst", "bekannte_im_pool", "vorschau_da",
             "bekannte_bleibt", "move_gewaehlt", "duplikat_abgelehnt", "button_busy",
             "edit_aufgerufen", "client_id", "target", "edit", "dialog_geschlossen"}


@pytest.mark.gui
def test_pokemon_editor(run_md_app):
    app = run_md_app(_DriveApp())
    fehlende = _EXPECTED - set(app.results)
    assert not fehlende, f"Checks nicht gelaufen (Step-Crash?): {fehlende} / {app.results}"
    fehler = {k: v["detail"] for k, v in app.results.items() if not v["ok"]}
    assert not fehler, fehler
