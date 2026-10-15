"""GUI-Durchlauf SessionMenu (KivyMD) gegen eine temporaere Config.

Ueberfuehrt aus spikes/kivymd_build/_out/drive_sessionsmenumd.py. Faehrt den
kompletten Session-Lebenszyklus in der MD-UI ab und prueft jeweils Datei- und
In-Memory-Zustand:

- anlegen ueber den 3-Schritt-Dialog (Name, Spielername, Spielauswahl)
- aktivieren inkl. Speichern der vorigen Session vor dem Wechsel und
  nuz-Stale-Key-Fix (In-place-Update der Config-Dicts)
- loeschen inkl. runs/-Ordner, aktive Session wird nicht geloescht

Marker ``gui``: braucht ein echtes GL-Fenster, nicht headless.

Ausfuehrung:
    .venv/py3115/Scripts/python.exe -m pytest tests/gui/test_sessionsmenumd.py -v
"""
from __future__ import annotations

import traceback
from pathlib import Path

import pytest
import yaml

from kivy.clock import Clock
from kivy.core.window import Window
from kivymd.app import MDApp

from backend import session_store
from frontend.app_core import MutableString
from frontend.widgets.sessionsmenumd import SessionMenu, SessionCard, GenToggleButton


class _DriveApp(MDApp):
    """MDApp, die den Session-Durchlauf scriptgesteuert ueber den Clock abfaehrt.

    Jeder Schritt schreibt sein Ergebnis nach self.results; der Test wertet das
    nach dem Lauf aus. default = Default-Config-Pfad, out = Screenshot-Ordner.
    """

    def __init__(self, default: str, out: str, **kw):
        super().__init__(**kw)
        self._default = default
        self._out = out
        self._list_file = Path(default).parent / "session_list.yml"
        self.results: dict = {}

    def check(self, name, ok, detail=None):
        self.results[name] = {"ok": bool(ok), "detail": detail}

    def build(self):
        self.theme_cls.theme_style = "Dark"
        self.theme_cls.primary_palette = "Orange"
        self.live = session_store.read_session(self._default)
        self.cs = MutableString(self._default)
        self.changed = []
        c = self.live
        self.menu = SessionMenu(["Alt"], self.cs, c.sp, c.rem, c.obs, c.bh, c.pl, c.rnd, c.ov, c.nuz, "test",
                                on_session_changed=self.changed.append)
        return self.menu

    def on_start(self):
        steps = [self.s_open_dialog, self.s_fill_dialog, self.s_confirm, self.s_activate, self.s_delete_active,
                 self.s_deactivate, self.s_delete, self.s_check_delete, self.s_finish]
        for i, s in enumerate(steps):
            Clock.schedule_once(lambda dt, s=s: self.safe(s), 1.0 + i * 1.6)

    def safe(self, step):
        try:
            step()
        except Exception:
            self.results[step.__name__] = {"ok": False, "detail": traceback.format_exc()}

    def shot(self, name):
        Clock.schedule_once(lambda dt: Window.screenshot(name=f"{self._out}/{name}.png"), 0.8)

    def card(self, name):
        return next(w for w in self.menu.grid_layout.children if isinstance(w, SessionCard) and w.session_name == name)

    def s_open_dialog(self):
        self.menu.add_session_card.dispatch("on_release")
        from frontend.widgets.sessionsmenumd import CreateSessionDialog
        self.dlg = next(w for w in Window.children if isinstance(w, CreateSessionDialog))
        self.check("dialog_offen", True)

    def s_fill_dialog(self):
        d = self.dlg
        d.session_name_text_box.text = "Neu Run"; d.on_next(None)
        d.name_text_box.text = "Misty"; d.on_next(None)
        btn = next(w for w in d.game_selection_manager.get_screen("Gen 2").walk() if isinstance(w, GenToggleButton) and w.game_text.text == "Gold")
        btn.state = "down"; btn.dispatch("on_press")
        self.check("dialog_schritt3", d.current_screen_index == 2 and d.selected_game.text == "Gold", d.selected_game.text)
        self.shot("01_dialog_spielwahl")

    def s_confirm(self):
        self.dlg.on_confirmation(None)
        p = Path(session_store.session_path(self._default, "Neu Run"))
        files = sorted(x.name for x in p.iterdir()) if p.exists() else []
        self.check("angelegt_8_yamls", len(files) == 8, files)
        created = session_store.read_session(str(p) + "/")
        self.check("pl_override", created.pl["your_name"] == "Misty" and created.pl["session_game"] == "Gold", created.pl)
        self.check("template_unveraendert", self.live.pl["your_name"] == "Default")
        self.check("session_list", yaml.safe_load(self._list_file.read_text()) == ["Alt", "Neu Run"])
        self.shot("02_grid_nach_anlegen")

    def s_activate(self):
        nuz = self.live.nuz
        self.live.rnd["seed"] = "default_geaendert"   # ungespeicherte Aenderung in Default
        self.card("Alt").dispatch("on_release")
        self.check("aktiv_configsave", self.cs.text == session_store.session_path(self._default, "Alt"), self.cs.text)
        self.check("aktiv_nuz_inplace_stale_weg", self.live.nuz is nuz and self.live.nuz == {"mode": "coop"}, self.live.nuz)
        self.check("aktiv_pl", self.live.pl["your_name"] == "AltName")
        self.check("callback", self.changed[-1:] == ["Alt"], self.changed)
        self.check("default_vor_wechsel_gespeichert", session_store.read_session(self._default).rnd["seed"] == "default_geaendert")
        self.shot("03_alt_aktiv")

    def s_delete_active(self):
        self.menu.delete_session("Alt", self.card("Alt"))
        self.check("aktive_nicht_geloescht", Path(session_store.session_path(self._default, "Alt")).exists())

    def s_deactivate(self):
        self.live.pl["your_name"] = "AltGeaendert"   # ungespeicherte Aenderung in Session Alt
        self.card("Alt").dispatch("on_release")
        saved = session_store.read_session(session_store.session_path(self._default, "Alt"))
        self.check("vor_wechsel_gespeichert", saved.pl["your_name"] == "AltGeaendert", saved.pl)
        self.check("zurueck_default", self.cs.text == self._default and self.live.pl["your_name"] == "Default" and self.changed[-1] is None, self.cs.text)
        Path(session_store.session_path(self._default, "Neu Run"), "runs", "run_001", "p1").mkdir(parents=True)

    def s_delete(self):
        self.menu.delete_session("Neu Run", self.card("Neu Run"))

    def s_check_delete(self):
        self.check("geloescht_mit_runs", not Path(session_store.session_path(self._default, "Neu Run")).exists())
        self.check("liste_nach_loeschen", yaml.safe_load(self._list_file.read_text()) == ["Alt"])
        self.check("karte_weg", all(getattr(w, "session_name", None) != "Neu Run" for w in self.menu.grid_layout.children))
        self.shot("04_nach_loeschen")

    def s_finish(self):
        self.stop()


# Erwartete Checks — stellt sicher, dass keiner still wegfaellt (z.B. Step-Crash).
_EXPECTED = {
    "dialog_offen", "dialog_schritt3", "angelegt_8_yamls", "pl_override",
    "template_unveraendert", "session_list", "aktiv_configsave",
    "aktiv_nuz_inplace_stale_weg", "aktiv_pl", "callback",
    "default_vor_wechsel_gespeichert", "aktive_nicht_geloescht",
    "vor_wechsel_gespeichert", "zurueck_default", "geloescht_mit_runs",
    "liste_nach_loeschen", "karte_weg",
}


@pytest.mark.gui
def test_sessionmenu_lebenszyklus(config_root, screenshot_dir, run_md_app):
    app = run_md_app(_DriveApp(config_root, screenshot_dir))

    fehlende = _EXPECTED - set(app.results)
    assert not fehlende, f"Checks nicht gelaufen (Step-Crash?): {fehlende}"

    fehler = {k: v["detail"] for k, v in app.results.items() if not v["ok"]}
    assert not fehler, fehler
