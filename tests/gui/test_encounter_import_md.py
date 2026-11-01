"""GUI-Durchlauf Encounter-Import-Dialog (KivyMD) + Auswahl-Logik.

Prueft: Button im EncounterMenu, Boxen-Lesefehler -> Hinweis, Lesen + update_boxes,
Vorschau (Gruppierung, Standard-Erstes, Geschenk-Badge, Eier/Bekannte uebersprungen),
Auswahl/Erstes aendern, Uebernehmen -> import_encounters bekommt die richtigen Dicts.
Marker ``gui``: braucht ein echtes GL-Fenster.

Ausfuehrung (einzeln laufen lassen):
    .venv/py3115/Scripts/python.exe -m pytest tests/gui/test_encounter_import_md.py -v
"""
from __future__ import annotations

import asyncio
import os
import traceback
from pathlib import Path

import pytest

from kivy.clock import Clock
from kivy.core.window import Window
from kivymd.app import MDApp
from kivymd.uix.screenmanager import MDScreenManager

from backend.classes.Pokemon import Pokemon
from backend.classes.pokedex_db import PokedexDB
from backend.encounter_import import ImportCandidate, RouteGroup
from frontend.widgets import encounter_import_md
from frontend.widgets.encounter_import_md import ImportSelection, summary_text
from frontend.widgets.encountermenumd import EncounterMenu

OWN = {"ot_id": 111, "ot_secret_id": 222}


def _mon(dex, lvl, pv, route, **extra) -> Pokemon:
    return Pokemon(dex, lvl=lvl, personality=pv, route=route, **{**OWN, **extra})


def _seed_db(session_dir: str) -> None:
    db = PokedexDB(Path(session_dir))
    db.connect()
    try:
        db.insert_encounter(0x1, "Alice_1", 34, 9, 1, 5, False, False, False, False, False,
                            "wild", "caught")
    finally:
        db.close()


class _FakeMunchlax:
    def __init__(self) -> None:
        self.on_soullink_state_changed_callback = None
        self.on_edit_rejected_callback = None
        self.team = [
            _mon(25, 12, 0xA1, 3),
            _mon(16, 5, 0xA2, 3),
            _mon(7, 9, 0xA3, 3, ot_id=999),  # Fremder OT -> Geschenk
            Pokemon("egg", lvl=1, personality=0xA4, route=3, **OWN),
        ]
        self.boxes = {1: []}
        self.read_boxes = [
            [_mon(1, 3, 0xB1, 4), _mon(4, 7, 0xB2, 4)],
            [_mon(43, 6, 0xB3, 5)],
        ]
        self.imports: list[tuple[int, list[dict]]] = []
        self.box_updates: list[tuple[int, list]] = []

    def snapshot_soullink_links(self) -> dict:
        return {}

    def snapshot_soullink_config(self) -> dict:
        return {}

    def snapshot_sorted_teams(self) -> dict:
        return {1: list(self.team)}

    def snapshot_editions(self) -> dict:
        return {1: 34}

    def owned_player_ids(self) -> list[int]:
        return [1]

    def snapshot_boxes(self, player_id: int) -> list:
        return [list(b) for b in self.boxes.get(player_id, [])]

    async def update_boxes(self, player_id: int, boxes: list):
        self.box_updates.append((player_id, boxes))
        self.boxes[player_id] = boxes

    async def encounter_import_context(self, player_id: int):
        return {"owner": "Alice_1", "edition": 34, "existing_pvs": {0xB2},
                "routes_with_first": {5}}

    async def import_encounters(self, player_id: int, encounters: list[dict]):
        self.imports.append((player_id, encounters))
        return True, len(encounters), f"{len(encounters)} Encounter übernommen"

    def submit_cross_thread(self, coro):
        return asyncio.ensure_future(coro)


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
        self.fake = _FakeMunchlax()
        self.menu = EncounterMenu(self._session_dir, {"player_count": 1, "your_name": "Alice"},
                                  {"run_mode": "nuzlocke"}, "test", munchlax=self.fake, rem={})
        self.sm.add_widget(self.menu)
        return self.sm

    def on_start(self):
        self._tries = 0
        self.sm.current = "EncounterMenu"
        Clock.schedule_once(lambda dt: self.safe(self.s_open), 1.0)

    def _next(self, step, delay=1.0):
        Clock.schedule_once(lambda dt: self.safe(step), delay)

    def safe(self, step):
        try:
            step()
        except Exception:
            self.results[step.__name__] = {"ok": False, "detail": traceback.format_exc()}
            self.stop()

    def shot(self, name):
        Clock.schedule_once(lambda dt: Window.screenshot(name=f"{self._out}/{name}.png"), 0.6)

    def _wait(self, cond, step, delay=0.3, limit=30):
        """Pollt auf Zustand; gibt True zurueck, wenn die Bedingung erfuellt ist."""
        if cond():
            self._tries = 0
            return True
        if self._tries < limit:
            self._tries += 1
            self._next(step, delay)
        else:
            self.check(f"timeout_{step.__name__}", False)
            self.stop()
        return False

    def s_open(self):
        self.check("import_button_sichtbar", self.menu.import_button.parent is not None)
        self.menu._open_import()
        self.dlg = self.menu._aux_dialog
        self.check("dialog_offen", self.dlg is not None)
        self.check("uebernehmen_gesperrt", self.dlg.save_button.disabled)
        self.dlg.start_read()  # Emulator nicht verbunden -> Fehlertext
        self._next(self.s_read_error, 0.5)

    def s_read_error(self):
        if not self._wait(lambda: not self.dlg._busy, self.s_read_error):
            return
        self.check("lesefehler_text", "nicht lesbar" in self.dlg.status_label.text,
                   self.dlg.status_label.text)
        self.check("noch_schritt_1", self.dlg.step == 1)
        self.check("zurueck_in_schritt_1_gesperrt", self.dlg.back_button.disabled)
        self.check("kein_update_boxes", not self.fake.box_updates)
        # jetzt erfolgreich lesen (Reader ersetzt)
        boxes = self.fake.read_boxes

        async def _ok(pl, citra, bizhawk, player):
            return boxes, None
        encounter_import_md.read_boxes_for_player = _ok
        self.dlg.start_read()
        self._next(self.s_preview, 0.5)

    def s_preview(self):
        if not self._wait(lambda: self.dlg.step == 2, self.s_preview):
            return
        d = self.dlg
        self.check("boxen_gecacht", len(self.fake.box_updates) == 1
                   and self.fake.box_updates[0][0] == 1)
        sel = d.selection
        pvs = sorted(sel.included)
        self.check("kandidaten", pvs == [0xA1, 0xA2, 0xA3, 0xB1, 0xB3], [hex(p) for p in pvs])
        self.check("uebersprungen", d.skipped == {"egg": 1, "known": 1, "no_pv": 0}, d.skipped)
        self.check("default_erstes", sel.first == {3: 0xA2, 4: 0xB1}, sel.first)
        self.check("route_mit_erstem_kein_default", 5 not in sel.first)
        self.check("geschenk_kein_stern", d._rows[0xA3][1] is None
                   and d._rows[0xB3][1] is None)
        self.check("zeilen", len(d._rows) == 5, len(d._rows))
        self.check("summary", d.summary_label.text.startswith("5 übernehmen")
                   and "1 Eier" in d.summary_label.text
                   and "1 bereits erfasst" in d.summary_label.text, d.summary_label.text)
        self.check("uebernehmen_frei", not d.save_button.disabled)
        self.check("zurueck_frei", not d.back_button.disabled)
        self.shot("i3_import_preview")
        self._next(self.s_back, 1.6)

    def s_back(self):
        d = self.dlg
        d.toggle_first(0xA1)  # Auswahl aendern, muss beim Zurueck verworfen werden
        d.go_back()
        self.check("zurueck_schritt_1", d.step == 1 and d.selection is None
                   and d.save_button.disabled and d.back_button.disabled)
        d.start_cached()
        self._next(self.s_preview_again, 0.5)

    def s_preview_again(self):
        if not self._wait(lambda: self.dlg.step == 2, self.s_preview_again):
            return
        self.check("vorschau_neu_default", self.dlg.selection.first == {3: 0xA2, 4: 0xB1},
                   self.dlg.selection.first)
        self._next(self.s_edit_selection, 0.5)

    def s_edit_selection(self):
        d = self.dlg
        box, _star = d._rows[0xB3]
        box.active = False  # echter Klick-Pfad ueber bind(active)
        d.toggle_first(0xA1)  # Erstes von Route 3 wechseln A2 -> A1
        self.check("erstes_gewechselt", d.selection.first.get(3) == 0xA1)
        self.check("abgewaehlt", 0xB3 not in d.selection.included
                   and d.summary_label.text.startswith("4 übernehmen"), d.summary_label.text)
        box2, _ = d._rows[0xB1]
        box2.active = False  # erstes von Route 4 abgewaehlt -> Erstes weg
        self.check("erstes_weg", 4 not in d.selection.first)
        d.save()
        self._next(self.s_after_import, 0.5)

    def s_after_import(self):
        if not self._wait(lambda: bool(self.fake.imports), self.s_after_import):
            return
        pid, encs = self.fake.imports[-1]
        by_pv = {e["personality"]: e for e in encs}
        self.check("import_pid", pid == 1)
        self.check("import_pvs", sorted(by_pv) == [0xA1, 0xA2, 0xA3], [hex(p) for p in by_pv])
        self.check("import_erstes", by_pv[0xA1]["is_first"] == 1 and by_pv[0xA2]["is_first"] == 0,
                   by_pv)
        self.check("import_geschenk", by_pv[0xA3]["method"] == "gift" and by_pv[0xA3]["is_first"] == 1)
        self.check("import_owner_edition", all(e["owner"] == "Alice_1" and e["edition"] == 34
                                               for e in encs))
        self._next(self.s_closed, 0.8)

    def s_closed(self):
        self.check("dialog_zu", self.menu._aux_dialog is None)
        self._next(self.stop, 0.3)


_EXPECTED = {
    "import_button_sichtbar", "dialog_offen", "uebernehmen_gesperrt", "lesefehler_text",
    "noch_schritt_1", "kein_update_boxes", "boxen_gecacht", "kandidaten", "uebersprungen",
    "default_erstes", "route_mit_erstem_kein_default", "geschenk_kein_stern", "zeilen",
    "summary", "uebernehmen_frei", "zurueck_in_schritt_1_gesperrt", "zurueck_frei",
    "zurueck_schritt_1", "vorschau_neu_default", "erstes_gewechselt", "abgewaehlt", "erstes_weg",
    "import_pid", "import_pvs", "import_erstes", "import_geschenk", "import_owner_edition",
    "dialog_zu",
}


@pytest.mark.gui
def test_encounter_import_dialog(config_root, screenshot_dir, run_md_app):
    _seed_db(config_root)
    original = encounter_import_md.read_boxes_for_player
    try:
        app = run_md_app(_DriveApp(
            config_root, os.environ.get("TRACKER_GUI_SHOT_DIR") or screenshot_dir))
    finally:
        encounter_import_md.read_boxes_for_player = original
    fehlende = _EXPECTED - set(app.results)
    assert not fehlende, f"Checks nicht gelaufen (Step-Crash?): {fehlende} {app.results}"
    fehler = {k: v["detail"] for k, v in app.results.items() if not v["ok"]}
    assert not fehler, fehler


# --- Auswahl-Logik (ohne Kivy-Fenster) ---------------------------------------------------

def _cand(pv, route, lvl=5, method="wild", foreign=False) -> ImportCandidate:
    return ImportCandidate(pv=pv, dexnr=1, lvl=lvl, shiny=False, female=None, form="",
                           route=route, method=method, source="Team Slot 1",
                           foreign_ot=foreign)


def _groups() -> list[RouteGroup]:
    return [
        RouteGroup(route=1, candidates=[_cand(1, 1, 5), _cand(2, 1, 8), _cand(3, 1, 9, "gift", True)],
                   default_first_pv=1),
        RouteGroup(route=2, candidates=[_cand(4, 2)], default_first_pv=None, has_existing_first=True),
    ]


def test_selection_defaults():
    sel = ImportSelection(_groups())
    assert sel.selected_pvs() == {1, 2, 3, 4}
    assert sel.first_pvs() == {1}
    assert sel.is_first(1) and not sel.is_first(2)


def test_selection_ein_erstes_pro_route():
    sel = ImportSelection(_groups())
    assert sel.set_first(2)
    assert sel.first_pvs() == {2}
    assert sel.set_first(2, False)
    assert sel.first_pvs() == set()


def test_selection_geschenk_und_vorhandene_erstbegegnung_nie_erstes():
    sel = ImportSelection(_groups())
    assert not sel.set_first(3)
    assert not sel.set_first(4)
    assert sel.first_pvs() == {1}


def test_selection_abwaehlen_loescht_erstes_und_setzen_waehlt_an():
    sel = ImportSelection(_groups())
    sel.toggle_include(1, False)
    assert 1 not in sel.selected_pvs() and sel.first_pvs() == set()
    assert sel.set_first(2)
    sel.toggle_include(2, False)
    assert sel.first_pvs() == set()
    sel.toggle_include(2, True)
    assert sel.first_pvs() == set()  # Erstes kommt nicht von allein zurueck
    sel.toggle_include(1, False)
    assert sel.set_first(1)  # waehlt zugleich an
    assert 1 in sel.selected_pvs()


def test_summary_text():
    assert summary_text(3, {"egg": 0, "known": 0, "no_pv": 0}) == "3 übernehmen"
    text = summary_text(5, {"egg": 1, "known": 2, "no_pv": 0})
    assert text == "5 übernehmen · übersprungen: 1 Eier, 2 bereits erfasst"
