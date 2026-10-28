"""GUI-Durchlauf First-Startup-Walkthrough (KivyMD, Phase F4).

Baut den OnboardingScreen in einem MDScreenManager (wie appmd), rendert alle
Schritte, prüft Feld-Aufbau + das Speichern in die (temporäre) Standard-Session
beim Weiterschalten und das on_complete am Ende. Schreibt NUR in die Temp-Config
(config_root-Fixture), nie in backend/config.

Ausfuehrung:
    .venv/py3115/Scripts/python.exe -m pytest tests/gui/test_onboarding_md.py -v
"""
from __future__ import annotations

import traceback
from types import SimpleNamespace

import pytest
import yaml

from kivy.clock import Clock
from kivymd.app import MDApp
from kivymd.uix.screenmanager import MDScreenManager
from kivy.uix.screenmanager import FadeTransition

from frontend.widgets.onboarding_md import OnboardingScreen


class _DriveApp(MDApp):
    def __init__(self, configsave: str, **kw):
        super().__init__(**kw)
        self._configsave = configsave
        self.results: dict = {}
        self.completed = False

    # OnboardingScreen._save ruft app.save_config(path, dict).
    def save_config(self, path, setting):
        with open(path, "w") as f:
            yaml.dump(setting, f)

    def check(self, name, ok, detail=None):
        self.results[name] = {"ok": bool(ok), "detail": detail}

    def build(self):
        self.theme_cls.theme_style = "Dark"
        self.theme_cls.primary_palette = "Orange"
        self.sp = {"common_path": ""}
        self.pl = {"your_name": "", "session_game": ""}
        self.bh = {"path": "", "port": "43885"}
        self.obs = {"host": "localhost", "port": "4455"}
        self.onboarding = OnboardingScreen(
            self._configsave, self.sp, self.pl, self.bh, self.obs, "9.9.9",
            on_complete=self._on_complete)
        self.sm = MDScreenManager(transition=FadeTransition())
        self.sm.add_widget(self.onboarding)
        return self.sm

    def _on_complete(self):
        self.completed = True

    def on_start(self):
        self.sm.current = "Onboarding"
        Clock.schedule_once(lambda dt: self.safe(self.s_run), 1.0)
        Clock.schedule_once(lambda dt: self.stop(), 24)

    def safe(self, step):
        try:
            step()
        except Exception:
            self.results[step.__name__] = {"ok": False, "detail": traceback.format_exc()}

    def s_run(self):
        ob = self.onboarding
        self.check("sechs_schritte", len(ob._steps) == 6, len(ob._steps))

        # Alle Schritte rendern ohne Crash.
        rendered_ok = True
        for i in range(len(ob._steps)):
            try:
                ob._index = i
                ob._render_step()
            except Exception:
                rendered_ok = False
                self.results[f"render_{i}"] = {"ok": False, "detail": traceback.format_exc()}
        self.check("alle_schritte_rendern", rendered_ok)

        # Schritt "Name & Spiel" (Index 2): Eingaben setzen + committen → player.yml.
        ob._index = 2
        ob._render_step()
        ob._name_field.text = "Ash"
        ob._on_game_selected(SimpleNamespace(game_display="Feuerrot"))
        ok = ob._commit_step()
        self.check("name_game_commit_ok", ok)
        self.check("pl_name_gesetzt", self.pl["your_name"] == "Ash", self.pl.get("your_name"))
        self.check("pl_game_gesetzt", self.pl["session_game"] == "Feuerrot", self.pl.get("session_game"))
        try:
            saved = yaml.safe_load(open(f"{self._configsave}player.yml"))
        except Exception:
            saved = {}
        self.check("player_yml_geschrieben", saved.get("your_name") == "Ash", saved)

        # Name mit '_' wird abgelehnt (harte Validierung → False).
        ob._index = 2
        ob._render_step()
        ob._name_field.text = "bad_name"
        self.check("underscore_blockt", ob._commit_step() is False)

        # Schritt "BizHawk" (Index 3): Pfad committen → bh_config.yml.
        ob._index = 3
        ob._render_step()
        ob._bh_path_field.text = "C:/BizHawk/EmuHawk.exe"
        ob._commit_step()
        self.check("bh_pfad_gesetzt", self.bh["path"] == "C:/BizHawk/EmuHawk.exe", self.bh.get("path"))

        # Abschluss: on_complete wird gerufen.
        ob._index = len(ob._steps) - 1
        ob._render_step()
        ob._finish()
        self.check("on_complete_gerufen", self.completed)

        self.stop()


_EXPECTED = {"sechs_schritte", "alle_schritte_rendern", "name_game_commit_ok",
             "pl_name_gesetzt", "pl_game_gesetzt", "player_yml_geschrieben",
             "underscore_blockt", "bh_pfad_gesetzt", "on_complete_gerufen"}


@pytest.mark.gui
def test_onboarding_durchlauf(config_root, run_md_app):
    app = run_md_app(_DriveApp(config_root))
    fehlende = _EXPECTED - set(app.results)
    assert not fehlende, f"Checks nicht gelaufen (Step-Crash?): {fehlende}"
    fehler = {k: v["detail"] for k, v in app.results.items() if not v["ok"]}
    assert not fehler, fehler
