"""NuzlockeMenu (KivyMD, Phase E) — Nuzlocke/Soullink-Modus, Regel-Presets,
Client-Abgleich, Token-Regel, Snapshots, Race-/Countdown-Timer.

Tab-Leiste aus MDButtons (kein MDTabsCarousel — der fängt Touches ab, Phase-D-
Lehre) + manueller Content-Tausch. Reine Logik liegt in backend/nuzlocke_view.py
(geteilt mit der klassischen UI). Backend-Aufrufe sind 1:1 aus der Klassik
portiert: Config-Push/Token via munchlax.dispatch_cross_thread, Timer via
submit_cross_thread (Munchlax lebt seit Phase 3 auf eigenem Thread), Snapshots im
run_in_executor. Nur der Server-Host darf die Config ändern (Edit-Lock); Clients
spiegeln den Server-Stand per Poll.
"""
import asyncio
import traceback
from pathlib import Path

from kivy.clock import Clock
from kivy.metrics import dp

from kivymd.app import MDApp
from kivymd.uix.boxlayout import MDBoxLayout
from kivymd.uix.gridlayout import MDGridLayout
from kivymd.uix.button import MDButton, MDButtonText
from kivymd.uix.divider import MDDivider
from kivymd.uix.label import MDLabel, MDIcon
from kivymd.uix.screen import MDScreen
from kivymd.uix.scrollview import MDScrollView
from kivymd.uix.selectioncontrol import MDCheckbox

from backend.logging_setup import get_logger
from backend.snapshot_manager import SnapshotManager
from backend.soullink_presets import load_presets, apply_preset, preset_choices
from backend.nuzlocke_view import (
    TEAM_LETTERS, MODE_LABELS, MODE_LABEL_TO_VALUE,
    CUSTOM_PRESET_LABEL, CUSTOM_PRESET_ID, RULE_CHECKBOXES,
    normalize_mode, rule_value, clamp_player_count, collect_config,
    connected_owner_names, merge_owner_rows, client_verify_diff,
)
from frontend.widgets.settings_widgets import make_textfield, DropdownField, LABEL_W
from frontend.widgets.md_common import show_snackbar, confirm_dialog, format_hms

logger = get_logger(__name__, 'logs/nuzlockemenu_md.log')

# Sektions-Container wie SettingsMenu/MainMenu: MDBoxLayout statt MDCard (MDCard
# tönt beim Hover die ganze Karte, hier unerwünscht).
CARD_BG = (0.129, 0.122, 0.149, 1)


def _card(title: str | None = None) -> MDBoxLayout:
    card = MDBoxLayout(orientation="vertical", padding=dp(14), spacing=dp(10),
                       size_hint_y=None, adaptive_height=True,
                       radius=[dp(16)] * 4, md_bg_color=CARD_BG)
    if title:
        lbl = MDLabel(text=title, role="small", bold=True, adaptive_height=True)
        lbl.theme_text_color = "Custom"
        lbl.text_color = MDApp.get_running_app().theme_cls.primaryColor
        card.add_widget(lbl)
    return card


def _row(*, height: float | None = None, spacing: float = dp(10)) -> MDBoxLayout:
    r = MDBoxLayout(orientation="horizontal", spacing=spacing, size_hint_y=None)
    if height is not None:
        r.height = height
    else:
        r.adaptive_height = True
    return r


def _action_button(text: str, on_release, style: str = "text") -> MDButton:
    return MDButton(MDButtonText(text=text), style=style,
                    on_release=lambda *_a: on_release())


class OwnerRowMD(MDBoxLayout):
    """Owner-Zeile: Name (MDTextField) + Team-Auswahl (nur im Versus-Modus)."""

    def __init__(self, index: int, owner: str = "", team: str = "A",
                 show_team: bool = False, **kwargs) -> None:
        super().__init__(orientation="horizontal", spacing=dp(10),
                         size_hint_y=None, height=dp(60), **kwargs)
        self.add_widget(MDLabel(text=f"Spieler {index + 1}:", size_hint_x=None,
                                width=LABEL_W, role="small", valign="center"))
        self.name_field = make_textfield("Owner-Name", text=owner)
        self.add_widget(self.name_field)
        self.team_dd = DropdownField(TEAM_LETTERS,
                                     team if team in TEAM_LETTERS else "A",
                                     lambda _v: None, size_hint_x=None, width=dp(90))
        self.add_widget(self.team_dd)
        self.set_team_visible(show_team)

    def set_team_visible(self, visible: bool) -> None:
        self.team_dd.opacity = 1 if visible else 0
        self.team_dd.disabled = not visible

    @property
    def owner(self) -> str:
        return (self.name_field.text or "").strip()

    @property
    def team(self) -> str:
        return self.team_dd.value or "A"


class NuzlockeMenu(MDScreen):
    def __init__(self, munchlax, configsave, nuz: dict, rem: dict | None = None,
                 bh: dict | None = None, bizhawk=None, rnd: dict | None = None,
                 app_version: str | None = None, **kwargs) -> None:
        super().__init__(**kwargs)
        self.name = "NuzlockeMenu"
        self.munchlax = munchlax
        self.configsave = configsave
        self.nuz = nuz
        self.rem = rem or {}
        self.bh = bh or {}
        self.rnd = rnd or {}
        self.bizhawk = bizhawk

        self._owner_rows: list[OwnerRowMD] = []
        self._rule_checkboxes: dict[str, MDCheckbox] = {}
        self._suspend = False          # unterdrückt Handler bei programmatischem Set
        self._sync_interval_ev = None
        self._verify_interval_ev = None
        self._token_interval_ev = None
        self._timer_tick_ev = None
        self._tasks: set = set()       # Strong-Refs gegen GC (feedback_async_task_strong_ref)

        self._presets = load_presets()
        self._preset_choices = preset_choices(self._presets)
        self._preset_choices.append((CUSTOM_PRESET_ID, CUSTOM_PRESET_LABEL))
        self._preset_label_to_id = {label: pid for pid, label in self._preset_choices}
        self._preset_id_to_label = {pid: label for pid, label in self._preset_choices}

        self._build_ui()

        # Countdown-Ton (wie TimerWidget) — Callback aus dem Munchlax-Thread, daher
        # Clock-gewrappt auf den Kivy-Loop.
        try:
            self.munchlax.countdown_finished_callback = (
                lambda lbl, dur: Clock.schedule_once(
                    lambda _dt: self._on_countdown_finished(lbl, dur), 0)
            )
        except Exception as err:
            logger.warning(f"countdown_finished_callback setzen fehlgeschlagen: {err}")

        # Einmalige Initial-Refreshes. Die wiederkehrenden Polls (Verify 3s,
        # Token 1s, Sync 1s) laufen nur solange der Screen sichtbar ist
        # (on_enter/on_leave), damit sie im Hintergrund keine Widgets neu bauen.
        Clock.schedule_once(lambda _dt: self._safe_refresh_client_verify(), 0.5)
        Clock.schedule_once(lambda _dt: self._refresh_snapshot_list(), 0.5)
        Clock.schedule_once(lambda _dt: self._apply_edit_lock(), 0)

    # ------------------------------------------------------------------ UI-Aufbau
    def _build_ui(self) -> None:
        root = MDBoxLayout(orientation="vertical")

        head = _row(height=dp(44))
        head.padding = [dp(16), 0]
        head.add_widget(MDLabel(text="Nuzlocke & Timer", role="large", bold=True,
                                valign="center"))
        self._host_label = MDLabel(text="", role="small", halign="right",
                                   valign="center")
        self._host_label.theme_text_color = "Custom"
        self._host_label.text_color = (1, 0.85, 0.2, 1)
        head.add_widget(self._host_label)
        root.add_widget(head)

        # Tab-Leiste (MDButtons, kein Carousel).
        self._panes: dict[str, MDScrollView] = {}
        self._tab_buttons: dict[str, MDButton] = {}
        tab_bar = MDBoxLayout(orientation="horizontal", size_hint=(None, None),
                              height=dp(48), adaptive_width=True, spacing=dp(4),
                              padding=[dp(8), 0])
        for name, builder in (
            ("Regeln", self._build_regeln_tab),
            ("Spieler & Modus", self._build_spieler_tab),
            ("Snapshots", self._build_snapshots_tab),
            ("Timer", self._build_timer_tab),
        ):
            btn = MDButton(MDButtonText(text=name), style="text",
                           on_release=lambda *_a, n=name: self.select_tab(n))
            self._tab_buttons[name] = btn
            tab_bar.add_widget(btn)
            self._panes[name] = self._scroll(builder())
        tab_scroll = MDScrollView(size_hint_y=None, height=dp(48), do_scroll_y=False)
        tab_scroll.add_widget(tab_bar)

        self._content_holder = MDBoxLayout(size_hint=(1, 1))
        root.add_widget(tab_scroll)
        root.add_widget(MDDivider())
        root.add_widget(self._content_holder)
        self.add_widget(root)

        self.select_tab("Regeln")

    def _scroll(self, content: MDBoxLayout) -> MDScrollView:
        sc = MDScrollView(size_hint=(1, 1), do_scroll_x=False)
        content.padding = [dp(16), dp(12), dp(16), dp(40)]
        content.spacing = dp(14)
        sc.add_widget(content)
        return sc

    def select_tab(self, name: str) -> None:
        pane = self._panes.get(name)
        if pane is None:
            return
        self._content_holder.clear_widgets()
        self._content_holder.add_widget(pane)
        for n, btn in self._tab_buttons.items():
            btn.style = "tonal" if n == name else "text"

    def _spawn(self, coro):
        """create_task mit Strong-Ref + Auto-Cleanup + Exception-Log
        (feedback_async_task_strong_ref). Ohne laufenden Loop: Coro schliessen."""
        try:
            task = asyncio.create_task(coro)
        except RuntimeError:
            coro.close()
            return None
        self._tasks.add(task)
        task.add_done_callback(self._on_task_done)
        return task

    def _on_task_done(self, task) -> None:
        self._tasks.discard(task)
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            logger.error(f"NuzlockeMenu-Task-Fehler: {exc}")

    # -------------------------------------------------------------- Tab: Regeln
    def _build_regeln_tab(self) -> MDBoxLayout:
        box = MDBoxLayout(orientation="vertical", size_hint_y=None, adaptive_height=True)

        preset_card = _card("Preset")
        preset_row = _row(height=dp(56))
        preset_row.add_widget(MDLabel(text="Regelwerk", size_hint_x=None, width=LABEL_W,
                                      role="small", valign="center"))
        labels = [label for _pid, label in self._preset_choices] or [CUSTOM_PRESET_LABEL]
        initial_id = str(self.nuz.get("soullink_preset_id", "") or "")
        initial_label = self._preset_id_to_label.get(initial_id, labels[0])
        self._preset_dd = DropdownField(labels, initial_label, self._on_preset_select)
        preset_row.add_widget(self._preset_dd)
        self._preset_apply_btn = _action_button("Preset anwenden", self._apply_preset,
                                                 style="tonal")
        preset_row.add_widget(self._preset_apply_btn)
        preset_card.add_widget(preset_row)
        box.add_widget(preset_card)

        rules_card = _card("Regeln")
        grid = MDGridLayout(cols=2, size_hint_y=None, spacing=dp(4))
        grid.bind(minimum_height=grid.setter("height"))
        for rule_key, label_text in RULE_CHECKBOXES:
            cell = _row(height=dp(44), spacing=dp(6))
            cb = MDCheckbox(size_hint=(None, None), size=(dp(40), dp(40)),
                            pos_hint={"center_y": 0.5})
            cb.active = rule_value(self.nuz, rule_key)
            cb.bind(active=lambda _i, val, k=rule_key: self._on_rule_change(k, val))
            self._rule_checkboxes[rule_key] = cb
            cell.add_widget(cb)
            lbl = MDLabel(text=label_text, role="small", valign="center")
            cell.add_widget(lbl)
            grid.add_widget(cell)
        rules_card.add_widget(grid)
        box.add_widget(rules_card)

        token_card = _card("Token-Regel")
        tok_row = _row(height=dp(60))
        tok_row.add_widget(MDLabel(text="Owner / Edition / Route", size_hint_x=None,
                                   width=LABEL_W, role="small", valign="center"))
        self._token_owner = make_textfield("Owner")
        self._token_edition = make_textfield("Edition")
        self._token_route = make_textfield("Route")
        tok_row.add_widget(self._token_owner)
        tok_row.add_widget(self._token_edition)
        tok_row.add_widget(self._token_route)
        tok_row.add_widget(_action_button("Einlösen", self._send_token_redeem,
                                           style="tonal"))
        token_card.add_widget(tok_row)
        self._token_status = MDLabel(text="Tokens: —", role="small", adaptive_height=True)
        token_card.add_widget(self._token_status)
        box.add_widget(token_card)
        return box

    # -------------------------------------------------------- Tab: Spieler & Modus
    def _build_spieler_tab(self) -> MDBoxLayout:
        box = MDBoxLayout(orientation="vertical", size_hint_y=None, adaptive_height=True)

        mode_card = _card("Modus")
        mode_val = normalize_mode(self.nuz.get("soullink_mode"))
        mrow = _row(height=dp(56))
        mrow.add_widget(MDLabel(text="Soullink-Modus", size_hint_x=None, width=LABEL_W,
                                role="small", valign="center"))
        self._mode_dd = DropdownField(list(MODE_LABELS.values()),
                                      MODE_LABELS[mode_val], self._on_mode_select)
        mrow.add_widget(self._mode_dd)
        mode_card.add_widget(mrow)
        crow = _row(height=dp(56))
        crow.add_widget(MDLabel(text="Spieleranzahl", size_hint_x=None, width=LABEL_W,
                                role="small", valign="center"))
        self._count_dd = DropdownField(["1", "2", "3", "4"],
                                       str(clamp_player_count(
                                           self.nuz.get("soullink_player_count", 2))),
                                       self._on_count_select, size_hint_x=None, width=dp(90))
        crow.add_widget(self._count_dd)
        mode_card.add_widget(crow)
        box.add_widget(mode_card)

        owner_card = _card("Spieler")
        self._owner_area = MDBoxLayout(orientation="vertical", spacing=dp(6),
                                       size_hint_y=None, adaptive_height=True)
        owner_card.add_widget(self._owner_area)
        actions = _row(height=dp(56), spacing=dp(6))
        actions.add_widget(_action_button("Owner + Teams speichern", self._save_to_nuz,
                                           style="filled"))
        actions.add_widget(_action_button("Aus Clients übernehmen",
                                           lambda: self._fill_owners_from_clients(True)))
        # Lädt die UI aus dem gespeicherten nuz neu → verwirft ungespeicherte
        # Owner-Namen/Team-Eingaben (strukturelle Änderungen persistieren direkt).
        actions.add_widget(_action_button("Änderungen verwerfen", self.load_from_session))
        actions.add_widget(_action_button("Config an Server senden", self._send_config,
                                           style="tonal"))
        owner_card.add_widget(actions)
        box.add_widget(owner_card)
        self._rebuild_owner_rows()

        verify_card = _card("Client-Abgleich")
        vh = _row(height=dp(40))
        vh.add_widget(MDLabel(text="erwartet vs. verbunden", role="small",
                              valign="center"))
        vh.add_widget(_action_button("Aktualisieren", self._refresh_client_verify))
        verify_card.add_widget(vh)
        self._verify_container = MDBoxLayout(orientation="vertical", spacing=dp(4),
                                             size_hint_y=None, adaptive_height=True)
        verify_card.add_widget(self._verify_container)
        box.add_widget(verify_card)
        return box

    # ---------------------------------------------------------- Tab: Snapshots
    def _build_snapshots_tab(self) -> MDBoxLayout:
        box = MDBoxLayout(orientation="vertical", size_hint_y=None, adaptive_height=True)
        create_card = _card("Snapshot erstellen")
        row = _row(height=dp(60))
        row.add_widget(MDLabel(text="Bezeichnung", size_hint_x=None, width=LABEL_W,
                               role="small", valign="center"))
        self._snapshot_label_field = make_textfield("Label", text="manual")
        row.add_widget(self._snapshot_label_field)
        self._snapshot_create_btn = _action_button("Snapshot erstellen",
                                                    self._create_snapshot, style="filled")
        row.add_widget(self._snapshot_create_btn)
        row.add_widget(_action_button("Liste aktualisieren", self._refresh_snapshot_list))
        create_card.add_widget(row)
        box.add_widget(create_card)

        list_card = _card("Gespeicherte Snapshots")
        self._snapshot_list = MDBoxLayout(orientation="vertical", spacing=dp(4),
                                          size_hint_y=None, adaptive_height=True)
        list_card.add_widget(self._snapshot_list)
        box.add_widget(list_card)
        return box

    # -------------------------------------------------------------- Tab: Timer
    def _build_timer_tab(self) -> MDBoxLayout:
        box = MDBoxLayout(orientation="vertical", size_hint_y=None, adaptive_height=True)

        race_card = _card("Race-Timer")
        self._race_display = MDLabel(text="00:00:00", role="large", bold=True,
                                     font_style="Display", adaptive_height=True)
        race_card.add_widget(self._race_display)
        rbtns = _row(height=dp(56), spacing=dp(6))
        rbtns.add_widget(_action_button("Start", lambda: self._send(self.munchlax.send_timer_start()), style="filled"))
        rbtns.add_widget(_action_button("Pause", lambda: self._send(self.munchlax.send_timer_pause_request())))
        rbtns.add_widget(_action_button("Fortsetzen", lambda: self._send(self.munchlax.send_timer_resume_request())))
        rbtns.add_widget(_action_button("Split", lambda: self._send(self.munchlax.send_timer_split("manual")), style="tonal"))
        rbtns.add_widget(_action_button("Reset", lambda: self._send(self.munchlax.send_timer_reset())))
        race_card.add_widget(rbtns)
        box.add_widget(race_card)

        cd_card = _card("Countdown")
        crow = _row(height=dp(60))
        crow.add_widget(MDLabel(text="Dauer (Min) / Label", size_hint_x=None,
                                width=LABEL_W, role="small", valign="center"))
        self._cd_minutes = make_textfield("Minuten", text="5")
        self._cd_minutes.input_filter = "float"
        self._cd_label = make_textfield("Label")
        crow.add_widget(self._cd_minutes)
        crow.add_widget(self._cd_label)
        cd_card.add_widget(crow)
        self._cd_display = MDLabel(text="Countdown: --:--:--", role="medium",
                                   adaptive_height=True)
        cd_card.add_widget(self._cd_display)
        cbtns = _row(height=dp(56), spacing=dp(6))
        cbtns.add_widget(_action_button("Start", self._send_countdown_start, style="filled"))
        cbtns.add_widget(_action_button("Pause", lambda: self._send(self.munchlax.send_countdown_pause())))
        cbtns.add_widget(_action_button("Fortsetzen", lambda: self._send(self.munchlax.send_countdown_resume())))
        cbtns.add_widget(_action_button("Abbrechen", lambda: self._send(self.munchlax.send_countdown_cancel())))
        cd_card.add_widget(cbtns)
        box.add_widget(cd_card)
        return box

    # ------------------------------------------------------------ Host / Lock
    def _is_host(self) -> bool:
        return bool(self.rem.get("start_server", False))

    def _apply_edit_lock(self) -> None:
        """Sperrt config-schreibende Widgets für Non-Host-Clients (reine UI-
        Klärung; der Server bleibt permissiv). Timer-/Snapshot-Steuerung bleibt
        erlaubt — das sind keine Soullink-Config-Writes."""
        host = self._is_host()
        for w in (getattr(self, "_preset_dd", None), getattr(self, "_preset_apply_btn", None),
                  getattr(self, "_mode_dd", None), getattr(self, "_count_dd", None)):
            if w is not None:
                w.disabled = not host
        for cb in self._rule_checkboxes.values():
            cb.disabled = not host
        for row in self._owner_rows:
            row.name_field.disabled = not host
            if row.team_dd.opacity > 0.5:
                row.team_dd.disabled = not host
        self._host_label.text = "" if host else "Nur der Host darf die Config ändern — Server-Stand."

    # ----------------------------------------------------------- Owner-Zeilen
    def _rebuild_owner_rows(self) -> None:
        count = clamp_player_count(self._count_dd.value if hasattr(self, "_count_dd")
                                   else self.nuz.get("soullink_player_count", 2))
        existing_owners = [r.owner for r in self._owner_rows]
        existing_teams = [r.team for r in self._owner_rows]
        rows = merge_owner_rows(
            count, existing_owners, existing_teams,
            self.nuz.get("soullink_expected_owners", []),
            self.nuz.get("soullink_team_membership", {}),
            self._mode_value(),
        )
        self._owner_area.clear_widgets()
        self._owner_rows = []
        show_team = (self._mode_value() == "versus")
        for i, (owner, team) in enumerate(rows):
            row = OwnerRowMD(i, owner=owner, team=team, show_team=show_team)
            self._owner_rows.append(row)
            self._owner_area.add_widget(row)
        if self._is_host():
            self._fill_owners_from_clients(overwrite=False)
        self._apply_edit_lock()

    def _mode_value(self) -> str:
        dd = getattr(self, "_mode_dd", None)
        if dd is None:
            return normalize_mode(self.nuz.get("soullink_mode"))
        return MODE_LABEL_TO_VALUE.get(dd.value, "nuzlocke")

    # --------------------------------------------------------------- Handler
    def _on_preset_select(self, _label: str) -> None:
        # Auswahl im Dropdown ändert noch nichts — erst "Preset anwenden".
        pass

    def _apply_preset(self) -> None:
        if not self._is_host():
            show_snackbar("Preset-Anwendung nur für den Host.", level='info')
            return
        pid = self._preset_label_to_id.get(self._preset_dd.value)
        if pid == CUSTOM_PRESET_ID:
            show_snackbar("Custom-Preset: Regeln direkt setzen.", level='info')
            return
        if pid is None or pid not in self._presets:
            show_snackbar("Preset nicht gefunden.", level='error')
            return
        preset = self._presets[pid]
        apply_preset(self.nuz, preset)
        self.nuz["soullink_preset_id"] = pid
        self._load_from_nuz()
        show_snackbar(f"Preset '{preset.get('label', pid)}' angewendet.", level='success')
        self._persist_structural_change(f"preset:{pid}")

    def _on_rule_change(self, rule_key: str, active: bool) -> None:
        if self._suspend:
            return
        if not self._is_host():
            return
        self.nuz[rule_key] = bool(active)
        if self._preset_dd.value != CUSTOM_PRESET_LABEL:
            self._preset_dd.set_value(CUSTOM_PRESET_LABEL)
        self.nuz["soullink_preset_id"] = CUSTOM_PRESET_ID
        self._persist_structural_change(f"rule:{rule_key}")

    def _on_mode_select(self, _label: str) -> None:
        mode_value = self._mode_value()
        show_team = (mode_value == "versus")
        for row in self._owner_rows:
            row.set_team_visible(show_team)
        self._apply_edit_lock()
        if self._suspend or not self._is_host():
            return
        if self.nuz.get("soullink_mode") == mode_value:
            return
        self.nuz["soullink_mode"] = mode_value
        self._persist_structural_change("mode")

    def _on_count_select(self, _value: str) -> None:
        # UI-Rebuild vor dem Host-Guard (wie Klassik): Non-Host-Sync setzt den
        # Wert programmatisch, die Rows müssen trotzdem neu gebaut werden.
        self._rebuild_owner_rows()
        if self._suspend or not self._is_host():
            return
        count_int = clamp_player_count(self._count_dd.value)
        if self.nuz.get("soullink_player_count") == count_int:
            return
        self.nuz["soullink_player_count"] = count_int
        self._persist_structural_change("player_count")

    # ------------------------------------------------------- Config persist/push
    def _collect_config(self) -> dict:
        rows = [(r.owner, r.team) for r in self._owner_rows]
        return collect_config(self._mode_value(), rows, self.nuz)

    def _send_config(self, silent: bool = False) -> None:
        """Config an Server pushen. ``silent`` unterdrückt den Erfolgs-Snackbar
        (für Auto-Push nach Regel-/Modus-Änderung, der sonst bei jedem Klick
        tönt); Fehler werden immer gemeldet."""
        if not self._is_host():
            if not silent:
                show_snackbar("Config-Änderungen nur für den Host.", level='info')
            return
        cfg = self._collect_config()
        try:
            self.munchlax.dispatch_cross_thread(
                self.munchlax.send_soullink_config(cfg), name="send_soullink_config")
            if not silent:
                show_snackbar(
                    f"Config gesendet: {cfg['mode']}, {len(cfg['expected_owners'])} Spieler.",
                    level='success')
        except Exception as err:
            logger.error(f"send_soullink_config failed: {type(err)},{err}")
            logger.error(traceback.format_exc())
            show_snackbar(f"Fehler: {err}", level='error')

    def _persist_structural_change(self, reason: str = "") -> None:
        try:
            MDApp.get_running_app().save_config(f"{self.configsave}nuzlocke.yml", self.nuz)
        except Exception as err:
            logger.warning(f"nuzlocke.yml persist ({reason}) failed: {err}")
        self._auto_push_config()

    def _auto_push_config(self) -> None:
        if not self._is_host():
            return
        if not getattr(self.munchlax, "is_connected", False):
            return
        try:
            self._send_config(silent=True)
        except Exception as err:
            logger.warning(f"auto-push config failed: {err}")

    def _save_to_nuz(self) -> None:
        if not self._is_host():
            show_snackbar("Nur Host darf Config speichern.", level='info')
            return
        cfg = self._collect_config()
        self.nuz["soullink_mode"] = cfg["mode"]
        self.nuz["soullink_player_count"] = cfg["player_count"]
        self.nuz["soullink_link_strategy"] = cfg["link_strategy"]
        self.nuz["soullink_team_membership"] = cfg["team_membership"]
        self.nuz["soullink_expected_owners"] = cfg["expected_owners"]
        try:
            MDApp.get_running_app().save_config(f"{self.configsave}nuzlocke.yml", self.nuz)
            show_snackbar("Owner + Teams gespeichert.", level='success')
        except Exception as err:
            logger.error(f"nuzlocke.yml speichern failed: {type(err)},{err}")
            show_snackbar(f"Speichern-Fehler: {err}", level='error')
        self._auto_push_config()

    def _fill_owners_from_clients(self, overwrite: bool = False) -> None:
        if not self._is_host():
            return
        candidates = connected_owner_names(
            getattr(self.munchlax, "player_names", {}),
            getattr(self.munchlax, "client_names", {}),
            ((getattr(self.munchlax, "pl", {}) or {}).get("your_name") or ""),
        )
        if not candidates:
            return
        used: set[str] = set()
        if not overwrite:
            for row in self._owner_rows:
                if row.owner:
                    used.add(row.owner)
        available = [n for n in candidates if n not in used]
        changed = 0
        for row in self._owner_rows:
            if not available:
                break
            if row.owner and not overwrite:
                continue
            row.name_field.text = available.pop(0)
            changed += 1
        if changed:
            logger.info(f"NuzlockeMenu(MD): {changed} Owner-Feld(er) aus Clients befüllt "
                        f"(overwrite={overwrite})")
            self._auto_push_config()

    # ----------------------------------------------------------- Client-Verify
    def _safe_refresh_client_verify(self) -> None:
        try:
            self._refresh_client_verify()
        except Exception as err:
            logger.warning(f"_refresh_client_verify (interval) failed: {err}")
            logger.warning(traceback.format_exc())

    def _refresh_client_verify(self) -> None:
        container = getattr(self, "_verify_container", None)
        if container is None:
            return
        container.clear_widgets()
        expected_pairs = [(i, r.owner) for i, r in enumerate(self._owner_rows) if r.owner]
        connected = connected_owner_names(
            getattr(self.munchlax, "player_names", {}),
            getattr(self.munchlax, "client_names", {}),
            ((getattr(self.munchlax, "pl", {}) or {}).get("your_name") or ""),
        )
        matched, missing_pairs, extra = client_verify_diff(expected_pairs, connected)
        host = self._is_host()

        if not expected_pairs and not connected:
            container.add_widget(MDLabel(
                text="(keine expected_owners, keine Clients verbunden)",
                role="small", adaptive_height=True, theme_text_color="Hint"))
            return

        for name in matched:
            container.add_widget(self._verify_row(
                "check-circle", (0.27, 0.87, 0.27, 1), name,
                "verbunden", (0.6, 0.9, 0.6, 1)))

        empty_slots = [i for i, r in enumerate(self._owner_rows) if not r.owner]
        for slot_index, slot_owner in missing_pairs:
            buttons = []
            if host and extra:
                for cand in extra:
                    buttons.append((f"→ {cand}",
                                    lambda si=slot_index, c=cand: self._replace_owner_in_row(si, c)))
            elif not host:
                buttons.append(("(nur Host darf ersetzen)", None))
            container.add_widget(self._verify_row(
                "close-circle", (1.0, 0.33, 0.33, 1), slot_owner,
                f"nicht verbunden (Slot {slot_index + 1})", (1.0, 0.5, 0.5, 1), buttons))

        for cand in extra:
            buttons = []
            if host:
                if empty_slots:
                    for i in empty_slots:
                        buttons.append((f"Slot {i + 1} belegen",
                                        lambda si=i, c=cand: self._replace_owner_in_row(si, c)))
                else:
                    buttons.append(("(alle Slots belegt)", None))
            else:
                buttons.append(("(nur Host)", None))
            container.add_widget(self._verify_row(
                "alert-circle", (1.0, 0.73, 0.2, 1), cand,
                "verbunden, nicht in Config", (1.0, 0.75, 0.2, 1), buttons))

    def _verify_row(self, icon_name: str, icon_color, name: str, status: str,
                    status_color, buttons=None) -> MDBoxLayout:
        # Status-Symbol als MDIcon (Material-Symbols-Font) statt Text-Glyph: ✓/✗/⚠
        # sind in Robotos Default-Font Tofu-Kästchen (bekannte Falle, Phase D).
        # dp(44): MDButtons in den Host-Zeilen sind ~dp(40) hoch, dp(34) clippte.
        row = _row(height=dp(44), spacing=dp(6))
        name_cell = MDBoxLayout(orientation="horizontal", spacing=dp(4), size_hint_x=0.4)
        status_icon = MDIcon(icon=icon_name, size_hint=(None, None),
                             size=(dp(20), dp(20)), pos_hint={"center_y": 0.5})
        # icon_color (nicht text_color): die <MDIcon>-KV-Regel bindet color an
        # icon_color/Theme (wie StatusDot in statusbarmd, A5-Lehre).
        status_icon.icon_color = icon_color
        name_cell.add_widget(status_icon)
        name_lbl = MDLabel(text=name, role="small", valign="center")
        name_cell.add_widget(name_lbl)
        row.add_widget(name_cell)
        st = MDLabel(text=status, role="small", valign="center", size_hint_x=0.3)
        st.theme_text_color = "Custom"
        st.text_color = status_color
        row.add_widget(st)
        btn_area = MDBoxLayout(orientation="horizontal", spacing=dp(4), size_hint_x=0.3)
        for text, cb in (buttons or []):
            if cb is None:
                lbl = MDLabel(text=text, role="small", valign="center",
                              theme_text_color="Hint")
                btn_area.add_widget(lbl)
            else:
                btn_area.add_widget(_action_button(text, cb, style="tonal"))
        row.add_widget(btn_area)
        return row

    def _replace_owner_in_row(self, slot_index: int, new_owner: str) -> None:
        if not self._is_host():
            logger.info("_replace_owner_in_row: skipped — kein Host")
            return
        if slot_index < 0 or slot_index >= len(self._owner_rows):
            logger.warning(f"_replace_owner_in_row: Slot {slot_index} out of range")
            return
        row = self._owner_rows[slot_index]
        old = row.owner
        row.name_field.text = new_owner
        logger.info(f"NuzlockeMenu(MD): Owner Slot {slot_index + 1}: '{old}' -> '{new_owner}'")
        self._save_to_nuz()
        self._refresh_client_verify()

    # --------------------------------------------------------------- Token
    def _send_token_redeem(self) -> None:
        owner = (self._token_owner.text or "").strip()
        route_text = (self._token_route.text or "").strip()
        edition_text = (self._token_edition.text or "").strip()
        if not owner or not route_text:
            show_snackbar("Token: Owner + Route erforderlich.", level='info')
            return
        try:
            route = int(route_text)
        except ValueError:
            show_snackbar("Token: Route muss eine Zahl sein.", level='error')
            return
        edition = None
        if edition_text:
            try:
                edition = int(edition_text)
            except ValueError:
                edition = edition_text
        try:
            self.munchlax.dispatch_cross_thread(
                self.munchlax.send_soullink_token_redeem(owner, edition, route),
                name="send_soullink_token_redeem")
            show_snackbar(f"Token eingelöst: {owner} Route {route}.", level='success')
        except Exception as err:
            logger.error(f"send_soullink_token_redeem failed: {err}")
            show_snackbar(f"Token-Fehler: {err}", level='error')

    def _refresh_token_status(self, _dt) -> None:
        # Poll-Callback: eigenes try/except, sonst killt eine Exception (z.B.
        # st kein dict) den Interval bzw. loest das Crash-Popup aus.
        try:
            tokens = getattr(self.munchlax, "soullink_tokens", None) or {}
            if not tokens:
                self._token_status.text = "Tokens: —"
                return
            parts = []
            for owner, st in tokens.items():
                avail = int(st.get("earned", 0)) - int(st.get("used", 0))
                active = st.get("active_route")
                parts.append(f"{owner}: {avail} frei" + (f" | aktiv Route {active}" if active else ""))
            self._token_status.text = "Tokens: " + " ; ".join(parts)
        except Exception as err:
            logger.warning(f"_refresh_token_status failed: {err}")

    # ------------------------------------------------------------- Snapshots
    def _snapshot_manager(self) -> SnapshotManager:
        session_path = str(self.configsave)
        session_name = Path(session_path).name or "default"
        return SnapshotManager(session_path, session_name, bh_config=self.bh,
                               munchlax=self.munchlax, rnd=self.rnd)

    def _create_snapshot(self) -> None:
        label = (self._snapshot_label_field.text or "manual").strip() or "manual"

        def _confirm():
            # Erst beim Bestätigen sperren, damit Abbrechen den Button nicht hängen lässt.
            self._snapshot_create_btn.disabled = True
            if self._spawn(self._flush_and_create_snapshot_async(label)) is None:
                self._snapshot_create_btn.disabled = False
                logger.warning("Snapshot-Task nicht gestartet (kein Event-Loop)")

        confirm_dialog(
            "Snapshot vorbereiten",
            "Hast du im Spiel gespeichert? Ohne in-game-Save enthält der Snapshot "
            "einen alten Stand.",
            on_confirm=_confirm, confirm_text="Ja, Snapshot erstellen",
            cancel_text="Abbrechen")

    async def _flush_and_create_snapshot_async(self, label: str) -> None:
        try:
            if self.bizhawk is not None:
                try:
                    from frontend.bh_dispatch import bh_submit
                    results = await bh_submit(self.bizhawk.flush_all_saverams(timeout=3.0))
                    if results:
                        failed = [cid for cid, ok in results.items() if not ok]
                        if failed:
                            logger.warning(f"SaveRAM-Flush teils fehlgeschlagen: {failed}")
                        await asyncio.sleep(0.2)
                    else:
                        logger.info("Kein BizHawk-Client — Flush übersprungen")
                except Exception as err:
                    logger.error(f"flush_all_saverams failed: {err}")
                    logger.error(traceback.format_exc())
            await self._create_snapshot_async(label)
        finally:
            Clock.schedule_once(lambda _dt: setattr(self._snapshot_create_btn, "disabled", False), 0)

    async def _create_snapshot_async(self, label: str) -> None:
        try:
            sm = self._snapshot_manager()
            loop = asyncio.get_event_loop()
            snap_id = await loop.run_in_executor(None, sm.create, label)
        except Exception as err:
            logger.error(f"snapshot create failed: {type(err)},{err}")
            logger.error(traceback.format_exc())
            Clock.schedule_once(lambda _dt, e=err: show_snackbar(f"Snapshot-Fehler: {e}", level='error'), 0)
            return
        if snap_id:
            Clock.schedule_once(lambda _dt: show_snackbar(f"Snapshot erstellt: {snap_id}", level='success'), 0)
            self._refresh_snapshot_list()
        else:
            Clock.schedule_once(lambda _dt: show_snackbar("Snapshot-Erstellung fehlgeschlagen", level='error'), 0)

    def _refresh_snapshot_list(self, *_args) -> None:
        # _spawn schliesst die Coro, wenn kein Event-Loop laeuft (Capture-Script).
        self._spawn(self._refresh_snapshot_list_async())

    async def _refresh_snapshot_list_async(self) -> None:
        try:
            sm = self._snapshot_manager()
            loop = asyncio.get_event_loop()
            entries = await loop.run_in_executor(None, sm.list)
        except Exception as err:
            logger.error(f"snapshot list failed: {err}")
            entries = []
        Clock.schedule_once(lambda _dt: self._render_snapshot_list(entries), 0)

    def _render_snapshot_list(self, entries) -> None:
        self._snapshot_list.clear_widgets()
        if not entries:
            self._snapshot_list.add_widget(MDLabel(text="(keine Snapshots)", role="small",
                                                   adaptive_height=True, theme_text_color="Hint"))
            return
        for meta in entries:
            snap_id = meta.get("id", "?")
            label = meta.get("label", "?")
            ts = meta.get("timestamp", "?")
            saves = len(meta.get("save_backups", []))
            errors = len(meta.get("errors", []))
            row = _row(height=dp(40), spacing=dp(6))
            info = f"{label} — {ts} — Saves:{saves}" + (f" — Errors:{errors}" if errors else "")
            row.add_widget(MDLabel(text=info, role="small", valign="center", size_hint_x=0.55))
            row.add_widget(_action_button("Laden", lambda sid=snap_id: self._restore_snapshot(sid), style="tonal"))
            row.add_widget(_action_button("Löschen", lambda sid=snap_id: self._delete_snapshot(sid)))
            self._snapshot_list.add_widget(row)

    def _restore_snapshot(self, snap_id: str) -> None:
        show_snackbar(f"Snapshot wird geladen: {snap_id}", level='info')
        self._spawn(self._restore_snapshot_async(snap_id))

    async def _restore_snapshot_async(self, snap_id: str) -> None:
        try:
            self.munchlax.close_pokedex_db()
            sm = self._snapshot_manager()
            loop = asyncio.get_event_loop()
            ok = await loop.run_in_executor(None, sm.restore, snap_id)
        except Exception as err:
            logger.error(f"snapshot restore failed: {type(err)},{err}")
            logger.error(traceback.format_exc())
            Clock.schedule_once(lambda _dt, e=err: show_snackbar(f"Restore-Fehler: {e}", level='error'), 0)
            return
        if ok:
            Clock.schedule_once(lambda _dt: show_snackbar(f"Snapshot geladen: {snap_id}", level='success'), 0)
            try:
                await self.munchlax.submit_cross_thread(
                    self.munchlax.force_overlay_broadcast(f"snapshot restore (nuzlockemenu_md): {snap_id}"))
            except Exception as err:
                logger.warning(f"force_overlay_broadcast nach restore failed: {err}")
        else:
            Clock.schedule_once(lambda _dt: show_snackbar(f"Restore fehlgeschlagen: {snap_id}", level='error'), 0)

    def _delete_snapshot(self, snap_id: str) -> None:
        self._spawn(self._delete_snapshot_async(snap_id))

    async def _delete_snapshot_async(self, snap_id: str) -> None:
        try:
            sm = self._snapshot_manager()
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, sm.delete, snap_id)
        except Exception as err:
            logger.error(f"snapshot delete failed: {err}")
            Clock.schedule_once(lambda _dt, e=err: show_snackbar(f"Delete-Fehler: {e}", level='error'), 0)
            return
        self._refresh_snapshot_list()
        Clock.schedule_once(lambda _dt: show_snackbar(f"Snapshot gelöscht: {snap_id}", level='success'), 0)

    # ----------------------------------------------------------------- Timer
    def _send(self, coro) -> None:
        """Timer-/Countdown-Coro über den Munchlax-Loop senden (eigener Thread)."""
        if not getattr(self.munchlax, "is_connected", False):
            show_snackbar("Server nicht verbunden — Timer-Aktion verworfen.", level='info')
            coro.close()
            return

        async def _runner():
            try:
                await self.munchlax.submit_cross_thread(coro)
            except Exception as err:
                logger.error(f"Timer-Send fehlgeschlagen: {type(err)},{err}")
                logger.error(traceback.format_exc())
        if self._spawn(_runner()) is None:
            # Kein Loop: _runner-Coro geschlossen, aber der innere Timer-Coro
            # wurde nie awaited → selbst schliessen (sonst "never awaited").
            coro.close()
            logger.warning("Timer-Send-Task nicht gestartet (kein Event-Loop)")

    def _send_countdown_start(self) -> None:
        try:
            minutes = float((self._cd_minutes.text or "0").replace(",", "."))
        except ValueError:
            minutes = 0.0
        seconds = max(1.0, minutes * 60.0)
        label = (self._cd_label.text or "").strip()
        self._send(self.munchlax.send_countdown_start(seconds, label))

    def _timer_tick(self, _dt) -> None:
        try:
            elapsed = self.munchlax.current_timer_elapsed()
            ts = getattr(self.munchlax, "timer_state", None) or {}
            suffix = "" if ts.get("running") else " (Pause)"
            self._race_display.text = format_hms(elapsed) + suffix
            cd = getattr(self.munchlax, "countdown_state", None) or {}
            if cd.get("duration_seconds", 0) <= 0:
                self._cd_display.text = "Countdown: --:--:--"
            else:
                remaining = self.munchlax.current_countdown_remaining()
                lbl = cd.get("label", "") or "Countdown"
                if cd.get("finished"):
                    state = " ✅ fertig"
                elif not cd.get("running"):
                    state = " (Pause)"
                else:
                    state = ""
                self._cd_display.text = f"{lbl}: {format_hms(remaining)}{state}"
        except Exception as err:
            logger.debug(f"timer_tick: {err}")

    def _on_countdown_finished(self, label: str, duration_seconds: float) -> None:
        logger.info(f"Countdown fertig: '{label}' ({duration_seconds}s)")

        def _beep():
            try:
                import winsound
                winsound.Beep(1000, 400)
                winsound.Beep(1500, 400)
            except Exception as err:
                logger.warning(f"winsound.Beep failed: {err}")

        # winsound.Beep blockiert ~0.8s — in den Executor auslagern, damit der
        # Kivy-/Event-Loop nicht einfriert.
        try:
            asyncio.get_event_loop().run_in_executor(None, _beep)
        except RuntimeError:
            _beep()

    # --------------------------------------------------- Session / Lifecycle
    def _load_from_nuz(self) -> None:
        """Schreibt self.nuz zurück in alle Widgets (Session-Wechsel + Verwerfen)."""
        self._suspend = True
        try:
            initial_id = str(self.nuz.get("soullink_preset_id", "") or "")
            self._preset_dd.set_value(self._preset_id_to_label.get(
                initial_id, self._preset_dd.value))
            mode_val = normalize_mode(self.nuz.get("soullink_mode"))
            self._mode_dd.set_value(MODE_LABELS[mode_val])
            self._count_dd.set_value(str(clamp_player_count(
                self.nuz.get("soullink_player_count", 2))))
            for rule_key, _label in RULE_CHECKBOXES:
                cb = self._rule_checkboxes.get(rule_key)
                if cb is not None:
                    cb.active = rule_value(self.nuz, rule_key)
            self._rebuild_owner_rows()
        finally:
            self._suspend = False
        self._safe_refresh_client_verify()

    def load_from_session(self) -> None:
        """Von appmd._on_session_changed gerufen: UI aus frisch geladenem nuz neu."""
        self._load_from_nuz()
        self._apply_edit_lock()
        if not self._is_host():
            self._sync_from_server_state()

    def _sync_from_server_state(self) -> None:
        """Spiegelt munchlax.soullink_config in die UI (Client-Only-Ansicht)."""
        cfg = getattr(self.munchlax, "soullink_config", {}) or {}
        self._suspend = True
        try:
            mode_val = normalize_mode(cfg.get("mode"))
            self._mode_dd.set_value(MODE_LABELS[mode_val])
            self._count_dd.set_value(str(clamp_player_count(cfg.get("player_count", 2))))
            show_team = (mode_val == "versus")
            # Row-Anzahl an Server-Count anpassen.
            self._rebuild_owner_rows()
            expected = cfg.get("expected_owners", []) or []
            team_map = cfg.get("team_membership", {}) or {}
            for i, row in enumerate(self._owner_rows):
                if i < len(expected):
                    row.name_field.text = expected[i]
                    tm = team_map.get(expected[i], "")
                    if tm in TEAM_LETTERS:
                        row.team_dd.set_value(tm)
                else:
                    row.name_field.text = ""
                row.set_team_visible(show_team)
            server_rules = cfg.get("rules") or {}
            if isinstance(server_rules, dict) and server_rules:
                for rule_key, _label in RULE_CHECKBOXES:
                    cb = self._rule_checkboxes.get(rule_key)
                    if cb is not None:
                        cb.active = bool(server_rules.get(rule_key, rule_value(self.nuz, rule_key)))
        except Exception as err:
            logger.warning(f"_sync_from_server_state failed: {err}")
            logger.warning(traceback.format_exc())
        finally:
            self._suspend = False
        self._apply_edit_lock()

    def _safe_sync_from_server_state(self) -> None:
        try:
            self._sync_from_server_state()
        except Exception as err:
            logger.warning(f"_sync_from_server_state (interval) failed: {err}")
            logger.warning(traceback.format_exc())

    def on_pre_enter(self, *_args) -> None:
        self._apply_edit_lock()
        if not self._is_host():
            self._sync_from_server_state()

    def on_enter(self, *_args) -> None:
        # Polls nur solange der Screen sichtbar ist (kein Hintergrund-Rebuild).
        if self._timer_tick_ev is None:
            self._timer_tick_ev = Clock.schedule_interval(self._timer_tick, 0.25)
        if self._verify_interval_ev is None:
            self._verify_interval_ev = Clock.schedule_interval(
                lambda _dt: self._safe_refresh_client_verify(), 3.0)
        if self._token_interval_ev is None:
            self._token_interval_ev = Clock.schedule_interval(self._refresh_token_status, 1.0)
        if self._is_host():
            return
        if self._sync_interval_ev is None:
            self._sync_interval_ev = Clock.schedule_interval(
                lambda _dt: self._safe_sync_from_server_state(), 1.0)

    def on_leave(self, *_args) -> None:
        for attr in ("_timer_tick_ev", "_verify_interval_ev", "_token_interval_ev",
                     "_sync_interval_ev"):
            ev = getattr(self, attr)
            if ev is not None:
                ev.cancel()
                setattr(self, attr, None)
