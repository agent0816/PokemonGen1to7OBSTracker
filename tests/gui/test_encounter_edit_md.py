"""GUI-Durchlauf Encounter-Bearbeiten-Dialog (KivyMD) gegen eine temporaere Session-DB.

Prueft: Dialog oeffnen, Status/Level aendern -> Speichern sendet nur die Diffs,
Unlock-Button sendet unlock, Loeschen nach Bestaetigung sendet delete=True,
fremde Karte -> kein Dialog, "Geloeschte zeigen"-Schalter, Tippen auf Soullink-Mitglied.
Marker ``gui``: braucht ein echtes GL-Fenster.

Ausfuehrung (einzeln laufen lassen):
    .venv/py3115/Scripts/python.exe -m pytest tests/gui/test_encounter_edit_md.py -v
"""
from __future__ import annotations

import asyncio
import traceback
from pathlib import Path

import pytest

from kivy.clock import Clock
from kivy.core.window import Window
from kivymd.app import MDApp
from kivymd.uix.button import MDButton, MDButtonText
from kivymd.uix.screenmanager import MDScreenManager

from backend.classes.pokedex_db import PokedexDB
from frontend.widgets.encounter_edit_md import compute_changes, initial_state
from frontend.widgets.encountermenumd import EncounterCard, EncounterMenu, LinkGroupCard, MemberBlock


def _seed_db(session_dir: str) -> None:
    db = PokedexDB(Path(session_dir))
    db.connect()
    try:
        flat = [
            (0x1, "Alice_1", 34, 1, 16, 3, False, "caught"),
            (0x2, "Alice_1", 34, 2, 19, 4, True, "not_caught"),
            (0x3, "Bob_2", 34, 3, 10, 5, False, "unknown"),
            (0x4, "Alice_1", 34, 5, 21, 6, False, "caught"),
        ]
        for pv, owner, ed, route, dex, lvl, shiny, outcome in flat:
            db.insert_encounter(pv, owner, ed, route, dex, lvl, shiny, False, False, False, False,
                                "wild", outcome)
        db.insert_encounter(0x10, "Alice_1", 34, 4, 25, 6, False, False, False, False, False,
                            "wild", "caught")
        db.insert_encounter(0x11, "Bob_2", 34, 4, 43, 6, False, False, False, False, False,
                            "wild", "caught")
        # Gesperrte Felder -> Schloss + "Automatik"-Button im Dialog
        db.apply_encounter_edit(0x2, "Alice_1", {"outcome": "caught", "lvl": 9})
        # Soft-Delete -> nur mit "Gelöschte zeigen" sichtbar
        db.apply_encounter_edit(0x4, "Alice_1", {}, None, True)
    finally:
        db.close()


class _FakeMunchlax:
    """Zeichnet edit_encounter-Aufrufe auf; submit_cross_thread laeuft auf dem aktuellen Loop."""

    def __init__(self) -> None:
        self.on_soullink_state_changed_callback = None
        self.on_edit_rejected_callback = None
        self.calls: list[dict] = []
        self.links = {1: {
            "link_id": 1, "route": 4, "edition_gen": 3, "link_index": 0, "state": "complete",
            "expected_owners": ["Alice", "Bob"],
            "members": {
                "Alice": {"personality": 0x10, "edition": 34, "outcome": "caught", "dexnr": 25},
                "Bob": {"personality": 0x11, "edition": 34, "outcome": "caught", "dexnr": 43},
            }}}

    def snapshot_soullink_links(self) -> dict:
        return {k: dict(v) for k, v in self.links.items()}

    def owned_player_ids(self) -> list[int]:
        return [1]

    async def edit_encounter(self, personality, owner, changes=None, unlock=None,
                             delete=None, create=None):
        self.calls.append({"personality": personality, "owner": owner, "changes": changes,
                           "unlock": unlock, "delete": delete})
        return True, "Gespeichert"

    def submit_cross_thread(self, coro):
        return asyncio.ensure_future(coro)


def _walk(widget):
    yield widget
    for child in widget.children:
        yield from _walk(child)


class _DriveApp(MDApp):
    def __init__(self, session_dir: str, out: str, **kw):
        super().__init__(**kw)
        self._session_dir = session_dir
        self._out = out
        self.results: dict = {}
        self.nuz = {"run_mode": "nuzlocke", "soullink_player_count": 2}

    def check(self, name, ok, detail=None):
        self.results[name] = {"ok": bool(ok), "detail": detail}

    def build(self):
        self.theme_cls.theme_style = "Dark"
        self.theme_cls.primary_palette = "Orange"
        self.sm = MDScreenManager()
        self.fake = _FakeMunchlax()
        self.menu = EncounterMenu(self._session_dir, {"player_count": 2}, self.nuz, "test",
                                  munchlax=self.fake, rem={"start_server": False})
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
            self.stop()

    def shot(self, name):
        Clock.schedule_once(lambda dt: Window.screenshot(name=f"{self._out}/{name}.png"), 0.6)

    def _rows(self):
        return [w for w in self.menu.cards_grid.children if isinstance(w, EncounterCard)]

    def _row_of(self, pv: int) -> dict:
        return next(c.row for c in self._rows() if c.row["personality"] == pv)

    def s_flat(self):
        rows = self._rows()
        if not rows and self._tries < 20:
            self._tries += 1
            self._next(self.s_flat, 0.3)
            return
        # 0x4 ist geloescht -> nur 0x1, 0x2, 0x3 sichtbar (0x10/0x11 auf Route 4 ebenfalls: siehe unten)
        self.check("flat_5_sichtbar", len(rows) == 5, len(rows))
        self.check("rejected_callback_gesetzt", callable(self.fake.on_edit_rejected_callback))
        self._next(self.s_foreign, 0.2)

    def s_foreign(self):
        self.menu._open_edit(self._row_of(0x3))  # Bob_2 = Slot 2, nicht lokal
        self.check("fremd_kein_dialog", self.menu._edit_dialog is None)
        self._next(self.s_open_save, 0.8)

    def s_open_save(self):
        self.menu._open_edit(self._row_of(0x1))
        dlg = self.menu._edit_dialog
        self.check("dialog_offen", dlg is not None)
        self.check("kein_unlock_button_ohne_sperre", not dlg.unlock_buttons)
        dlg.set_value("outcome", "not_caught")
        dlg.set_value("lvl", "12")
        dlg.save()
        self._next(self.s_after_save, 1.2)

    def s_after_save(self):
        calls = self.fake.calls
        ok = bool(calls) and calls[-1]["changes"] == {"outcome": "not_caught", "lvl": 12}
        self.check("speichern_nur_diffs", ok, calls)
        self.check("speichern_kein_unlock_delete",
                   ok and calls[-1]["unlock"] is None and calls[-1]["delete"] is None)
        self._next(self.s_open_unlock, 1.2)

    def s_open_unlock(self):
        self.check("dialog_nach_speichern_zu", self.menu._edit_dialog is None)
        self.menu._open_edit(self._row_of(0x2))
        dlg = self.menu._edit_dialog
        self.check("unlock_buttons_fuer_sperren",
                   dlg is not None and set(dlg.unlock_buttons) == {"outcome", "lvl"},
                   None if dlg is None else set(dlg.unlock_buttons))
        self.shot("encounter_edit_dialog")
        self._next(self.s_scroll_bottom, 1.2)

    def s_scroll_bottom(self):
        # Unteren Dialog-Teil (Level, Geschlecht, Form, Flags, Tot) sichtbar machen
        self.menu._edit_dialog.scroll.scroll_y = 0
        self.shot("encounter_edit_dialog_unten")
        self._next(self.s_unlock_click, 1.2)

    def s_unlock_click(self):
        dlg = self.menu._edit_dialog
        dlg.unlock_buttons["outcome"].dispatch("on_release")
        self._next(self.s_after_unlock, 1.2)

    def s_after_unlock(self):
        calls = self.fake.calls
        self.check("unlock_gesendet", len(calls) >= 2 and calls[-1]["unlock"] == ["outcome"]
                   and calls[-1]["changes"] is None, calls)
        self._next(self.s_open_delete, 1.2)

    def s_open_delete(self):
        self.menu._open_edit(self._row_of(0x1))
        dlg = self.menu._edit_dialog
        dlg.ask_delete()
        self._next(self.s_confirm_delete, 0.8)

    def s_confirm_delete(self):
        own = self.menu._edit_dialog.delete_button
        confirm = None
        for root in Window.children:
            for w in _walk(root):
                if isinstance(w, MDButtonText) and w.text == "Löschen" and w.parent is not own:
                    confirm = w.parent
        self.check("confirm_button_gefunden", isinstance(confirm, MDButton))
        n = len(self.fake.calls)
        if confirm is not None:
            confirm.dispatch("on_press")  # confirm_dialog bindet on_press
        self._calls_before = n
        self._next(self.s_after_delete, 1.2)

    def s_after_delete(self):
        calls = self.fake.calls
        self.check("loeschen_delete_true", len(calls) > self._calls_before
                   and calls[-1]["delete"] is True, calls)
        self._next(self.s_show_deleted, 1.4)

    def s_show_deleted(self):
        self.menu.deleted_switch.active = True
        self._next(self.s_deleted_check, 0.8)

    def s_deleted_check(self):
        rows = self._rows()
        self.check("geloeschte_sichtbar", len(rows) == 6, len(rows))
        deleted = [c for c in rows if c.row["deleted"]]
        self.check("geloeschte_markiert", bool(deleted) and deleted[0].opacity < 1)
        self.shot("encounter_edit_deleted")
        self._next(self.s_soullink, 0.8)

    def s_soullink(self):
        self.nuz["run_mode"] = "coop"
        self.menu._apply_filters()
        self._next(self.s_member_tap, 0.6)

    def s_member_tap(self):
        cards = [w for w in self.menu.cards_grid.children if isinstance(w, LinkGroupCard)]
        members = [w for c in cards for w in _walk(c) if isinstance(w, MemberBlock)]
        self.check("members_tappbar", len(members) == 2, len(members))
        self.menu._open_member_edit(cards[0].group["members"][0])
        self.check("member_dialog_offen", self.menu._edit_dialog is not None)
        self.check("member_dialog_alice", self.menu._edit_dialog.personality == 0x10, self.menu._edit_dialog.personality)
        self._next(self.s_finish, 0.8)

    def s_finish(self):
        self.menu._edit_dialog.dismiss()
        self._next(self.stop, 0.8)


_EXPECTED = {
    "flat_5_sichtbar", "rejected_callback_gesetzt", "fremd_kein_dialog", "dialog_offen",
    "kein_unlock_button_ohne_sperre", "speichern_nur_diffs", "unlock_buttons_fuer_sperren",
    "unlock_gesendet", "confirm_button_gefunden", "loeschen_delete_true",
    "geloeschte_sichtbar", "geloeschte_markiert", "members_tappbar", "member_dialog_offen",
    "member_dialog_alice",
}


@pytest.mark.gui
def test_encounter_edit_dialog(config_root, screenshot_dir, run_md_app):
    _seed_db(config_root)
    app = run_md_app(_DriveApp(config_root, screenshot_dir))

    fehlende = _EXPECTED - set(app.results)
    assert not fehlende, f"Checks nicht gelaufen (Step-Crash?): {fehlende} {app.results}"

    fehler = {k: v["detail"] for k, v in app.results.items() if not v["ok"]}
    assert not fehler, fehler


def test_compute_changes_diff_und_typen():
    row = {"outcome": "caught", "dexnr": 4, "route": 2, "method": "wild", "lvl": 5,
           "female": False, "female_raw": None, "form": "", "shiny": False,
           "is_first": 1, "is_dupes_skip": 0, "is_shiny_override": 0, "has_balls": 1,
           "dead": False}
    orig = initial_state(row)
    assert compute_changes(orig, dict(orig)) == {}
    cur = dict(orig, outcome="unknown", dexnr=7, shiny=True, female=True, dead=True,
               lvl="50", form=" 2 ", is_first=False)
    assert compute_changes(orig, cur) == {
        "outcome": "unknown", "dexnr": 7, "shiny": 1, "female": True, "dead": 1,
        "lvl": 50, "form": "2", "is_first": 0}
    with pytest.raises(ValueError):
        compute_changes(orig, dict(orig, lvl="101"))
    with pytest.raises(ValueError):
        compute_changes(orig, dict(orig, lvl=""))
