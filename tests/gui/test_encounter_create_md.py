"""GUI-Durchlauf Encounter-Anlegen, "Ohne Gruppe" und Gruppen-Aktionen (KivyMD).

Prueft: Anlegen aus Team (echte PV uebernommen), aus Box, Freitext (negative PV),
Pokemon ohne PV -> Hinweis statt Senden, "Ohne Gruppe"-Abschnitt, Gruppen-Aktionen im
Edit-Dialog (verschieben / hinzufuegen / loesen), Host: Gruppe aufloesen + neue Gruppe.
Marker ``gui``: braucht ein echtes GL-Fenster.

Ausfuehrung (einzeln laufen lassen):
    .venv/py3115/Scripts/python.exe -m pytest tests/gui/test_encounter_create_md.py -v
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

from backend.classes.Pokemon import Pokemon
from backend.classes.pokedex_db import PokedexDB
from backend.encounter_view import link_targets
from frontend.widgets.encounter_create_md import build_create_payload, has_real_pv
from frontend.widgets.encountermenumd import EncounterCard, EncounterMenu, LinkGroupCard


def _seed_db(session_dir: str) -> None:
    db = PokedexDB(Path(session_dir))
    db.connect()
    try:
        rows = [
            # (pv, owner, route, dexnr, lvl, outcome)
            (0x10, "Alice_1", 4, 25, 6, "caught"),    # Link 1
            (0x11, "Bob_2", 4, 43, 6, "caught"),      # Link 1
            (0x30, "Bob_2", 5, 16, 5, "caught"),      # Link 2 (Alice fehlt)
            (0x1, "Alice_1", 1, 1, 5, "caught"),      # ohne Gruppe
            (0x20, "Alice_1", 7, 4, 7, "caught"),     # ohne Gruppe
            (0x3, "Bob_2", 3, 7, 5, "caught"),        # ohne Gruppe
        ]
        for pv, owner, route, dex, lvl, outcome in rows:
            db.insert_encounter(pv, owner, 34, route, dex, lvl, False, False, False, False, False,
                                "wild", outcome)
    finally:
        db.close()


class _FakeMunchlax:
    """Zeichnet edit_encounter/edit_soullink_link auf; Loop-Submit auf dem aktuellen Loop."""

    def __init__(self) -> None:
        self.on_soullink_state_changed_callback = None
        self.on_edit_rejected_callback = None
        self.soullink_config = {"expected_owners": ["Alice", "Bob"]}
        self.calls: list[dict] = []
        self.link_calls: list[tuple[str, dict]] = []
        self.team = [
            Pokemon(25, nickname="Pika", lvl=12, personality=0xABCD, shiny=True, female=True),
            Pokemon(16, nickname="Taubsi", lvl=8),  # ohne PV (z.B. Gen 1/2)
        ]
        self.boxes = {1: [[None, Pokemon(7, nickname="Schiggy", lvl=20, personality=0x777)],
                          [Pokemon(1, lvl=3, personality=0x888)]]}
        self.links = {
            1: {"link_id": 1, "route": 4, "edition_gen": 3, "link_index": 0, "state": "complete",
                "expected_owners": ["Alice", "Bob"],
                "members": {"Alice": {"personality": 0x10, "edition": 34, "outcome": "caught",
                                      "dexnr": 25},
                            "Bob": {"personality": 0x11, "edition": 34, "outcome": "caught",
                                    "dexnr": 43}}},
            2: {"link_id": 2, "route": 5, "edition_gen": 3, "link_index": 0, "state": "pending",
                "expected_owners": ["Alice", "Bob"],
                "members": {"Bob": {"personality": 0x30, "edition": 34, "outcome": "caught",
                                    "dexnr": 16}}},
        }

    def snapshot_soullink_links(self) -> dict:
        return {k: dict(v) for k, v in self.links.items()}

    def snapshot_sorted_teams(self) -> dict:
        return {1: list(self.team)}

    def snapshot_editions(self) -> dict:
        return {1: 34}

    def owned_player_ids(self) -> list[int]:
        return [1]

    def snapshot_boxes(self, player_id: int) -> list:
        return [list(b) for b in self.boxes.get(player_id, [])]

    def snapshot_soullink_config(self) -> dict:
        return dict(self.soullink_config)

    async def edit_encounter(self, personality, owner, changes=None, unlock=None,
                             delete=None, create=None):
        self.calls.append({"personality": personality, "owner": owner, "create": create})
        return True, "Gespeichert"

    async def edit_soullink_link(self, op, **kw):
        self.link_calls.append((op, kw))
        return True, "Gruppe geändert"

    def submit_cross_thread(self, coro):
        return asyncio.ensure_future(coro)


def _walk(widget):
    yield widget
    for child in widget.children:
        yield from _walk(child)


def _press_text(text: str, exclude_parent=None) -> bool:
    """Drueckt den (zuletzt gefundenen) Dialog-Button mit diesem Text (on_press-Variante)."""
    target = None
    for root in Window.children:
        for w in _walk(root):
            if isinstance(w, MDButtonText) and w.text == text and w.parent is not exclude_parent:
                target = w.parent
    if not isinstance(target, MDButton):
        return False
    target.dispatch("on_press")
    return True


class _DriveApp(MDApp):
    def __init__(self, session_dir: str, out: str, **kw):
        super().__init__(**kw)
        self._session_dir = session_dir
        self._out = out
        self.results: dict = {}
        self.nuz = {"run_mode": "coop", "soullink_player_count": 2}

    def check(self, name, ok, detail=None):
        self.results[name] = {"ok": bool(ok), "detail": detail}

    def build(self):
        self.theme_cls.theme_style = "Dark"
        self.theme_cls.primary_palette = "Orange"
        self.sm = MDScreenManager()
        self.fake = _FakeMunchlax()
        self.menu = EncounterMenu(self._session_dir,
                                  {"player_count": 2, "your_name": "Alice"}, self.nuz, "test",
                                  munchlax=self.fake, rem={"start_server": True})
        self.sm.add_widget(self.menu)
        return self.sm

    def on_start(self):
        self._tries = 0
        self.sm.current = "EncounterMenu"
        Clock.schedule_once(lambda dt: self.safe(self.s_view), 1.0)

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

    def _unlinked(self):
        return [w for w in self.menu.unlinked_grid.children if isinstance(w, EncounterCard)]

    def _enc_row(self, pv: int) -> dict:
        return next(c.row for c in self._unlinked() if c.row["personality"] == pv)

    # --- Ansicht ---------------------------------------------------------------

    def s_view(self):
        groups = [w for w in self.menu.cards_grid.children if isinstance(w, LinkGroupCard)]
        if len(groups) < 2 and self._tries < 20:
            self._tries += 1
            self._next(self.s_view, 0.3)
            return
        self.check("zwei_gruppenkarten", len(groups) == 2, len(groups))
        self.check("ohne_gruppe_3_karten", len(self._unlinked()) == 3, len(self._unlinked()))
        self.check("ohne_gruppe_header_sichtbar", self.menu.unlinked_header.height > 0
                   and "3" in self.menu.unlinked_header.text, self.menu.unlinked_header.text)
        self.check("host_menue_button", all(g.menu_button is not None for g in groups))
        self.check("neue_gruppe_button_host", self.menu.new_group_button.parent is not None)
        self.check("hinzufuegen_button", self.menu.add_button.parent is not None)
        self.shot("soullink_ohne_gruppe")
        self._next(self.s_create_team, 1.4)

    # --- Anlegen aus Team ---------------------------------------------------------

    def s_create_team(self):
        self.menu._open_create()
        dlg = self.menu._aux_dialog
        self.check("create_dialog_offen", dlg is not None)
        self.check("spieler_vorbelegt", dlg.state["pid"] == 1)
        # Pokemon ohne PV: Hinweis, nichts gewaehlt
        dlg.pick_team(1)
        self.check("ohne_pv_nicht_gewaehlt", dlg.chosen is None)
        dlg.pick_team(0)
        self.check("team_gewaehlt", dlg.chosen is not None and dlg.state["lvl"] == "12", dlg.state)
        dlg.set_route(dlg._route_picker.entries[-1][1])
        self.shot("create_dialog_team")
        self._next(self.s_create_team_send, 1.4)

    def s_create_team_send(self):
        dlg = self.menu._aux_dialog
        dlg.save()
        self._next(self.s_after_create_team, 1.2)

    def s_after_create_team(self):
        calls = self.fake.calls
        c = calls[-1] if calls else {}
        create = c.get("create") or {}
        self.check("team_pv_uebernommen", c.get("personality") == 0xABCD and c.get("owner") == "Alice_1", c)
        self.check("team_felder", create.get("dexnr") == 25 and create.get("lvl") == 12
                   and create.get("shiny") == 1 and create.get("female") is True
                   and create.get("edition") == 34 and create.get("outcome") == "caught", create)
        self.check("create_dialog_zu", self.menu._aux_dialog is None)
        self._next(self.s_create_box, 0.8)

    def s_create_box(self):
        self.menu._open_create()
        dlg = self.menu._aux_dialog
        dlg.set_source("box")
        dlg.set_box(0)
        dlg.pick_box(1)
        dlg.set_route(-1)
        self.check("box_gewaehlt", dlg.chosen is not None and dlg.chosen.personality == 0x777)
        dlg.save()
        self._next(self.s_after_create_box, 1.2)

    def s_after_create_box(self):
        c = self.fake.calls[-1]
        self.check("box_pv_uebernommen", c["personality"] == 0x777
                   and c["create"]["dexnr"] == 7 and c["create"]["route"] == -1, c)
        self._next(self.s_create_free, 0.8)

    def s_create_free(self):
        self.menu._open_create()
        dlg = self.menu._aux_dialog
        dlg.set_source("free")
        n = len(self.fake.calls)
        dlg.save()  # Route + Spezies fehlen -> nichts senden
        self.check("freitext_ohne_angaben_kein_senden", len(self.fake.calls) == n)
        dlg.set_species(25)
        dlg.set_route(-1)
        dlg.set_value("lvl", "5")
        self.shot("create_dialog_freitext")
        self._next(self.s_create_free_send, 1.2)

    def s_create_free_send(self):
        self.menu._aux_dialog.save()
        self._next(self.s_after_create_free, 1.2)

    def s_after_create_free(self):
        c = self.fake.calls[-1]
        self.check("freitext_negative_pv", c["personality"] < 0 and c["create"]["dexnr"] == 25
                   and c["create"]["lvl"] == 5, c)
        self._next(self.s_group_move, 0.8)

    # --- Gruppen-Aktionen im Edit-Dialog ------------------------------------------------

    def s_group_move(self):
        self.menu._open_edit(self._enc_row(0x1))
        dlg = self.menu._edit_dialog
        self.check("edit_dialog_mit_gruppe_button", dlg is not None and dlg.group_button is not None)
        self._next(self.s_group_menu, 1.2)

    def s_group_menu(self):
        dlg = self.menu._edit_dialog
        dlg.open_group_menu()
        self.check("menue_hinzufuegen", dlg._own_group() is None)
        self.shot("gruppen_menue")
        self._next(self.s_group_move_pick, 1.6)

    def s_group_move_pick(self):
        dlg = self.menu._edit_dialog
        dlg._group_action("move", dlg.group_button)
        self.shot("gruppen_zielauswahl")
        self._next(self.s_group_move_send, 1.6)

    def s_group_move_send(self):
        dlg = self.menu._edit_dialog
        self.check("zielgruppe_nur_link2", [t[1] for t in link_targets(
            dlg.groups, dlg.root_owner)] == [2])
        dlg._pick_target(2)
        self._next(self.s_after_move, 1.2)

    def s_after_move(self):
        ops = self.fake.link_calls
        op, kw = ops[-1] if ops else ("", {})
        self.check("move_gesendet", op == "move" and kw == {
            "personality": 0x1, "owner": "Alice_1", "target_link_id": 2}, ops)
        self.check("edit_dialog_nach_move_zu", self.menu._edit_dialog is None)
        self._next(self.s_unlink, 0.8)

    def s_unlink(self):
        group = next(w for w in self.menu.cards_grid.children
                     if isinstance(w, LinkGroupCard) and w.group["link_id"] == 1).group
        self.menu._open_member_edit(group["members"][0])  # Alice 0x10 (Slot 1)
        dlg = self.menu._edit_dialog
        self.check("unlink_dialog_alice", dlg is not None and dlg.personality == 0x10)
        self.check("in_gruppe", dlg._own_group() is not None and dlg._own_group()["link_id"] == 1)
        dlg._group_action("unlink", dlg.group_button)
        self._next(self.s_unlink_confirm, 0.8)

    def s_unlink_confirm(self):
        self.check("unlink_confirm", _press_text("Lösen"))
        self._next(self.s_after_unlink, 1.2)

    def s_after_unlink(self):
        op, kw = self.fake.link_calls[-1]
        self.check("unlink_gesendet", op == "unlink" and kw == {
            "personality": 0x10, "owner": "Alice_1"}, self.fake.link_calls)
        self._next(self.s_dissolve, 0.8)

    # --- Host: aufloesen / neue Gruppe --------------------------------------------------

    def s_dissolve(self):
        group = next(w for w in self.menu.cards_grid.children
                     if isinstance(w, LinkGroupCard) and w.group["link_id"] == 1).group
        self.menu._ask_dissolve(group)
        self._next(self.s_dissolve_confirm, 0.8)

    def s_dissolve_confirm(self):
        self.check("dissolve_confirm", _press_text("Auflösen"))
        self._next(self.s_after_dissolve, 1.2)

    def s_after_dissolve(self):
        op, kw = self.fake.link_calls[-1]
        self.check("dissolve_gesendet", op == "dissolve" and kw == {"link_id": 1},
                   self.fake.link_calls)
        self._next(self.s_new_group, 0.8)

    def s_new_group(self):
        self.menu._open_new_group()
        dlg = self.menu._aux_dialog
        self.check("neue_gruppe_dialog", dlg is not None and dlg.owners == ["Alice", "Bob"])
        n = len(self.fake.link_calls)
        dlg.save()  # nichts gewaehlt -> kein Senden
        self.check("neue_gruppe_ohne_auswahl_kein_senden", len(self.fake.link_calls) == n)
        alice = dlg._entries_for("Alice")
        bob = dlg._entries_for("Bob")
        self.check("auswahl_pro_spieler", len(alice) == 3 and len(bob) == 3, (alice, bob))
        # Alice 0x20 (Glumanda) + Bob 0x3 (Schiggy)
        idx_a = next(i for t, i in alice if t.startswith("Glumanda"))
        idx_b = next(i for t, i in bob if t.startswith("Schiggy"))
        dlg.select("Alice", idx_a)
        dlg.select("Bob", idx_b)
        self.shot("neue_gruppe_dialog")
        self._next(self.s_new_group_send, 1.2)

    def s_new_group_send(self):
        self.menu._aux_dialog.save()
        self._next(self.s_after_new_group, 1.2)

    def s_after_new_group(self):
        op, kw = self.fake.link_calls[-1]
        members = kw.get("members") or []
        self.check("create_mit_zwei_mitgliedern", op == "create" and sorted(
            (m["personality"], m["owner"]) for m in members) == [(0x3, "Bob_2"), (0x20, "Alice_1")],
            self.fake.link_calls)
        self._next(self.s_non_host, 0.8)

    def s_non_host(self):
        self.menu.rem["start_server"] = False
        self.menu._apply_filters()
        self._next(self.s_non_host_check, 0.6)

    def s_non_host_check(self):
        groups = [w for w in self.menu.cards_grid.children if isinstance(w, LinkGroupCard)]
        self.check("nicht_host_ohne_menue", groups and all(g.menu_button is None for g in groups))
        self.check("nicht_host_ohne_neue_gruppe", self.menu.new_group_button.parent is None)
        self._next(self.stop, 0.4)


_EXPECTED = {
    "zwei_gruppenkarten", "ohne_gruppe_3_karten", "ohne_gruppe_header_sichtbar",
    "host_menue_button", "neue_gruppe_button_host", "hinzufuegen_button",
    "create_dialog_offen", "spieler_vorbelegt", "ohne_pv_nicht_gewaehlt", "team_gewaehlt",
    "team_pv_uebernommen", "team_felder", "create_dialog_zu", "box_gewaehlt",
    "box_pv_uebernommen", "freitext_ohne_angaben_kein_senden", "freitext_negative_pv",
    "edit_dialog_mit_gruppe_button", "menue_hinzufuegen", "zielgruppe_nur_link2",
    "move_gesendet", "edit_dialog_nach_move_zu", "unlink_dialog_alice", "in_gruppe",
    "unlink_confirm", "unlink_gesendet", "dissolve_confirm", "dissolve_gesendet",
    "neue_gruppe_dialog", "neue_gruppe_ohne_auswahl_kein_senden", "auswahl_pro_spieler",
    "create_mit_zwei_mitgliedern", "nicht_host_ohne_menue", "nicht_host_ohne_neue_gruppe",
}


@pytest.mark.gui
def test_encounter_create_und_gruppen(config_root, screenshot_dir, run_md_app):
    _seed_db(config_root)
    app = run_md_app(_DriveApp(config_root, screenshot_dir))

    fehlende = _EXPECTED - set(app.results)
    assert not fehlende, f"Checks nicht gelaufen (Step-Crash?): {fehlende} {app.results}"

    fehler = {k: v["detail"] for k, v in app.results.items() if not v["ok"]}
    assert not fehler, fehler


def test_build_create_payload_validierung():
    mon = Pokemon(25, lvl=12, personality=0xABCD, shiny=False, female=False, form="")
    state = {"source": "team", "route": 3, "method": "wild", "outcome": "caught", "lvl": "12",
             "dexnr": 0}
    pv, create = build_create_payload(state, mon, 34)
    assert pv == 0xABCD and create["dexnr"] == 25 and create["female"] is False
    assert "form" not in create
    with pytest.raises(ValueError):
        build_create_payload(dict(state, route=None), mon, 34)
    with pytest.raises(ValueError):
        build_create_payload(state, mon, None)
    with pytest.raises(ValueError):
        build_create_payload(dict(state, lvl="0"), mon, 34)
    with pytest.raises(ValueError):
        build_create_payload(state, Pokemon(16, lvl=5), 34)  # keine PV
    assert not has_real_pv(Pokemon(16, lvl=5))
    pv, create = build_create_payload(dict(state, source="free", dexnr=7), None, 34)
    assert pv < 0 and create["dexnr"] == 7
