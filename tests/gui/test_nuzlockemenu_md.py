"""GUI-Durchlauf Nuzlocke-Screen (KivyMD, Phase E).

Prueft: Tab-Leiste (4 Tabs, kein Carousel), Tab-Wechsel tauscht Content,
Owner-Zeilen folgen Spieleranzahl + Versus-Team-Sichtbarkeit, Regel-Checkbox
schreibt nuz + pusht Config. Munchlax ist gemockt (kein echter Server/Netz).
Marker ``gui``: braucht ein echtes GL-Fenster.

Ausfuehrung:
    .venv/py3115/Scripts/python.exe -m pytest tests/gui/test_nuzlockemenu_md.py -v
"""
from __future__ import annotations

import traceback

import pytest

from kivy.clock import Clock
from kivy.core.window import Window
from kivymd.app import MDApp
from kivymd.uix.screenmanager import MDScreenManager

from frontend.widgets.nuzlockemenu_md import NuzlockeMenu, OwnerRowMD


class _MockMunchlax:
    """Minimaler Stand-in — nur was NuzlockeMenu(MD) liest/aufruft."""

    def __init__(self):
        self.is_connected = True
        self.player_names = {1: "Alice", 2: "Bob"}
        self.client_names = {}
        self.pl = {"your_name": "Alice"}
        self.soullink_config = {}
        self.soullink_tokens = {}
        self.timer_state = {}
        self.countdown_state = {}
        self.countdown_finished_callback = None
        self.sent: list[str] = []
        self.sent_cfg = None

    def current_timer_elapsed(self):
        return 0.0

    def current_countdown_remaining(self):
        return 0.0

    def dispatch_cross_thread(self, _payload, name=None):
        self.sent.append(name)

    def send_soullink_config(self, cfg):
        self.sent_cfg = cfg
        return None

    def send_soullink_token_redeem(self, _o, _e, _r):
        return None

    def close_pokedex_db(self):
        pass


class _DriveApp(MDApp):
    def __init__(self, session_dir: str, out: str, **kw):
        super().__init__(**kw)
        self._session_dir = session_dir
        self._out = out
        self.results: dict = {}
        self.munchlax = _MockMunchlax()
        # Host-Session mit Versus-Modus + 3 Spielern.
        self.nuz = {
            "run_mode": "versus",
            "soullink_player_count": 3,
            "soullink_expected_owners": ["Alice", "Bob"],
            "soullink_team_membership": {},
            "soullink_preset_id": "",
        }

    def save_config(self, _path, _data):
        # NuzlockeMenu._persist_structural_change ruft app.save_config.
        self._saved = True

    def check(self, name, ok, detail=None):
        self.results[name] = {"ok": bool(ok), "detail": detail}

    def build(self):
        self.theme_cls.theme_style = "Dark"
        self.theme_cls.primary_palette = "Orange"
        self.sm = MDScreenManager()
        self.menu = NuzlockeMenu(
            self.munchlax, self._session_dir, self.nuz,
            rem={"start_server": True}, bh={}, bizhawk=None, rnd={})
        self.sm.add_widget(self.menu)
        return self.sm

    def on_start(self):
        self._tries = 0
        self.sm.current = "NuzlockeMenu"
        Clock.schedule_once(lambda dt: self.safe(self.s_tabs), 1.0)

    def _next(self, step, delay=1.0):
        Clock.schedule_once(lambda dt: self.safe(step), delay)

    def safe(self, step):
        try:
            step()
        except Exception:
            self.results[step.__name__] = {"ok": False, "detail": traceback.format_exc()}

    def shot(self, name):
        Clock.schedule_once(lambda dt: Window.screenshot(name=f"{self._out}/{name}.png"), 0.5)

    def s_tabs(self):
        m = self.menu
        if not m._owner_rows and self._tries < 20:
            self._tries += 1
            self._next(self.s_tabs, 0.3)
            return
        self.check("vier_tabs", len(m._tab_buttons) == 4, list(m._tab_buttons))
        # Kein Carousel (Phase-D-Lehre).
        carousel = [w for w in self.root.walk()
                    if type(w).__name__ == "MDTabsCarousel"]
        self.check("kein_carousel", not carousel)
        # Owner-Zeilen folgen player_count=3, Versus zeigt Team-Auswahl.
        rows = [w for w in m._owner_area.children if isinstance(w, OwnerRowMD)]
        self.check("drei_owner_zeilen", len(rows) == 3, len(rows))
        self.check("versus_team_sichtbar",
                   bool(rows) and all(r.team_dd.opacity > 0.5 for r in rows))
        self.shot("01_nuzlocke_spieler")
        self._next(self.s_regeln)

    def s_regeln(self):
        m = self.menu
        m.select_tab("Regeln")
        holder = m._content_holder.children
        self.check("tab_regeln_aktiv",
                   bool(holder) and holder[0] is m._panes["Regeln"])
        # Regel-Checkbox toggeln -> nuz-Write + Config-Push (Host).
        cb = m._rule_checkboxes["rule_token_rule"]
        cb.active = True
        self.check("regel_in_nuz", self.nuz.get("rule_token_rule") is True)
        self.check("config_gepusht", "send_soullink_config" in self.munchlax.sent)
        self.shot("02_nuzlocke_regeln")
        self._next(self.s_timer)

    def s_timer(self):
        m = self.menu
        m.select_tab("Timer")
        holder = m._content_holder.children
        self.check("tab_timer_aktiv",
                   bool(holder) and holder[0] is m._panes["Timer"])
        self.check("race_display_da", m._race_display is not None)
        self.shot("03_nuzlocke_timer")
        self._next(self.s_snapshots, 0.8)

    def s_snapshots(self):
        m = self.menu
        m.select_tab("Snapshots")
        holder = m._content_holder.children
        self.check("tab_snapshots_aktiv",
                   bool(holder) and holder[0] is m._panes["Snapshots"])
        self.shot("04_nuzlocke_snapshots")
        self._next(self.s_finish, 0.8)

    def s_finish(self):
        self.stop()


_EXPECTED = {
    "vier_tabs", "kein_carousel", "drei_owner_zeilen", "versus_team_sichtbar",
    "tab_regeln_aktiv", "regel_in_nuz", "config_gepusht",
    "tab_timer_aktiv", "race_display_da", "tab_snapshots_aktiv",
}


@pytest.mark.gui
def test_nuzlocke_md_durchlauf(config_root, screenshot_dir, run_md_app):
    app = run_md_app(_DriveApp(config_root, screenshot_dir))

    fehlende = _EXPECTED - set(app.results)
    assert not fehlende, f"Checks nicht gelaufen (Step-Crash?): {fehlende}"

    fehler = {k: v["detail"] for k, v in app.results.items() if not v["ok"]}
    assert not fehler, fehler
