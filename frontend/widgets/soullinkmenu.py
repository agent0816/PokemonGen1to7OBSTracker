"""SoullinkMenu — Modus/Player-Count/Team + eingebettetes Timer/Countdown-Widget."""

import asyncio
import traceback
from pathlib import Path

from kivy.clock import Clock
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.label import Label
from kivy.uix.screenmanager import Screen
from kivy.uix.scrollview import ScrollView
from kivy.uix.spinner import Spinner
from kivy.uix.textinput import TextInput

from backend.logging_setup import get_logger
from backend.snapshot_manager import SnapshotManager
from backend.soullink_presets import load_presets, apply_preset, preset_choices
from frontend.widgets.timer_widget import TimerWidget

logger = get_logger(__name__, './logs/soullinkmenu.log')


TEAM_LETTERS = ["A", "B", "C", "D"]


class OwnerRow(BoxLayout):
    """Eine Zeile: Owner-String (TextInput) + optionale Team-Auswahl."""

    def __init__(self, index: int, initial_owner: str = "",
                 initial_team: str = "A", show_team: bool = False, **kwargs):
        super().__init__(orientation="horizontal", size_hint_y=None,
                          height="40dp", spacing="6dp", **kwargs)
        self.add_widget(Label(text=f"Spieler {index+1}:", size_hint_x=0.25))
        self.owner_input = TextInput(
            text=initial_owner,
            multiline=False,
            size_hint_x=0.55,
        )
        self.add_widget(self.owner_input)
        self.team_spinner = Spinner(
            text=initial_team if initial_team in TEAM_LETTERS else "A",
            values=TEAM_LETTERS,
            size_hint_x=0.20,
        )
        self.add_widget(self.team_spinner)
        self.set_team_visible(show_team)

    def set_team_visible(self, visible: bool):
        self.team_spinner.disabled = not visible
        self.team_spinner.opacity = 1.0 if visible else 0.3

    @property
    def owner(self) -> str:
        return (self.owner_input.text or "").strip()

    @property
    def team(self) -> str:
        return self.team_spinner.text or "A"


class SoullinkMenu(Screen):
    def __init__(self, munchlax, configsave, nuz: dict, bh: dict | None = None, **kwargs):
        super().__init__(**kwargs)
        self.name = "SoullinkMenu"
        self.munchlax = munchlax
        self.configsave = configsave
        self.nuz = nuz
        self.bh = bh or {}
        self._owner_rows: list[OwnerRow] = []
        self._presets = load_presets()
        self._preset_choices = preset_choices(self._presets)
        self._preset_label_to_id = {label: pid for pid, label in self._preset_choices}

        root = BoxLayout(orientation="vertical", padding="10dp", spacing="10dp")

        header = BoxLayout(orientation="horizontal", size_hint_y=None, height="40dp")
        header.add_widget(Label(text="Soullink & Timer", font_size="20sp"))
        back_btn = Button(text="Zurück", size_hint_x=0.2, on_press=self._go_back)
        header.add_widget(back_btn)
        root.add_widget(header)

        # Preset-Auswahl
        preset_row = BoxLayout(orientation="horizontal", size_hint_y=None,
                                height="40dp", spacing="6dp")
        preset_row.add_widget(Label(text="Regel-Preset:", size_hint_x=0.20))
        preset_labels = [label for _, label in self._preset_choices] or ["(keine)"]
        self.preset_spinner = Spinner(text=preset_labels[0], values=preset_labels,
                                        size_hint_x=0.55)
        preset_row.add_widget(self.preset_spinner)
        preset_row.add_widget(Button(text="Preset anwenden", size_hint_x=0.25,
                                       on_press=lambda *_: self._apply_preset()))
        root.add_widget(preset_row)

        # Modus + Player-Count
        config_row = BoxLayout(orientation="horizontal", size_hint_y=None,
                                height="40dp", spacing="6dp")
        config_row.add_widget(Label(text="Modus:", size_hint_x=0.15))
        self.mode_spinner = Spinner(
            text=self.nuz.get("soullink_mode", "off") or "off",
            values=["off", "coop", "versus"],
            size_hint_x=0.20,
        )
        self.mode_spinner.bind(text=self._on_mode_changed)
        config_row.add_widget(self.mode_spinner)

        config_row.add_widget(Label(text="Spieler:", size_hint_x=0.15))
        self.player_count_spinner = Spinner(
            text=str(self.nuz.get("soullink_player_count", 2) or 2),
            values=["2", "3", "4"],
            size_hint_x=0.15,
        )
        self.player_count_spinner.bind(text=self._on_player_count_changed)
        config_row.add_widget(self.player_count_spinner)

        config_row.add_widget(Label(text="Strategie:", size_hint_x=0.15))
        self.strategy_spinner = Spinner(
            text=self.nuz.get("soullink_link_strategy", "full_chain") or "full_chain",
            values=["full_chain", "rotating"],
            size_hint_x=0.20,
        )
        config_row.add_widget(self.strategy_spinner)
        root.add_widget(config_row)

        # Owner-Zeilen
        self.owner_area = BoxLayout(orientation="vertical", size_hint_y=None, spacing="4dp")
        self.owner_area.bind(minimum_height=self.owner_area.setter("height"))
        scroll = ScrollView(size_hint_y=0.5)
        scroll.add_widget(self.owner_area)
        root.add_widget(scroll)
        self._rebuild_owner_rows()

        # Aktionen
        actions = BoxLayout(orientation="horizontal", size_hint_y=None,
                             height="40dp", spacing="6dp")
        actions.add_widget(Button(text="Config an Server senden",
                                    on_press=lambda *_: self._send_config()))
        actions.add_widget(Button(text="Aus Session laden",
                                    on_press=lambda *_: self._load_from_nuz()))
        actions.add_widget(Button(text="In Session speichern",
                                    on_press=lambda *_: self._save_to_nuz()))
        root.add_widget(actions)

        self.status_label = Label(text="", size_hint_y=None, height="30dp")
        root.add_widget(self.status_label)

        # Token-Regel Panel
        token_row = BoxLayout(orientation="horizontal", size_hint_y=None,
                               height="40dp", spacing="6dp")
        token_row.add_widget(Label(text="Token einlösen — Owner:", size_hint_x=0.25))
        self.token_owner_input = TextInput(text="", multiline=False, size_hint_x=0.25)
        token_row.add_widget(self.token_owner_input)
        token_row.add_widget(Label(text="Edition:", size_hint_x=0.10))
        self.token_edition_input = TextInput(text="", multiline=False, size_hint_x=0.10)
        token_row.add_widget(self.token_edition_input)
        token_row.add_widget(Label(text="Route:", size_hint_x=0.10))
        self.token_route_input = TextInput(text="", multiline=False, size_hint_x=0.10)
        token_row.add_widget(self.token_route_input)
        token_row.add_widget(Button(text="Einlösen", size_hint_x=0.10,
                                      on_press=lambda *_: self._send_token_redeem()))
        root.add_widget(token_row)

        self.token_status_label = Label(text="Tokens: —", size_hint_y=None, height="24dp")
        root.add_widget(self.token_status_label)
        Clock.schedule_interval(self._refresh_token_status, 1.0)

        # Snapshot-Panel
        snap_header = BoxLayout(orientation="horizontal", size_hint_y=None, height="40dp",
                                  spacing="6dp")
        snap_header.add_widget(Label(text="Snapshot Label:", size_hint_x=0.20))
        self.snapshot_label_input = TextInput(text="manual", multiline=False, size_hint_x=0.30)
        snap_header.add_widget(self.snapshot_label_input)
        snap_header.add_widget(Button(text="Snapshot erstellen", size_hint_x=0.25,
                                        on_press=lambda *_: self._create_snapshot()))
        snap_header.add_widget(Button(text="Liste aktualisieren", size_hint_x=0.25,
                                        on_press=lambda *_: self._refresh_snapshot_list()))
        root.add_widget(snap_header)

        self.snapshot_list_layout = BoxLayout(orientation="vertical", size_hint_y=None,
                                                spacing="2dp")
        self.snapshot_list_layout.bind(minimum_height=self.snapshot_list_layout.setter("height"))
        snap_scroll = ScrollView(size_hint_y=None, height="140dp")
        snap_scroll.add_widget(self.snapshot_list_layout)
        root.add_widget(snap_scroll)
        Clock.schedule_once(lambda dt: self._refresh_snapshot_list(), 0.5)

        # Timer/Countdown-Widget
        self.timer_widget = TimerWidget(munchlax)
        root.add_widget(self.timer_widget)

        self.add_widget(root)

        # Bei aktivem Ready-Flow: Modus initial anwenden
        Clock.schedule_once(lambda dt: self._on_mode_changed(self.mode_spinner, self.mode_spinner.text), 0)

    def _go_back(self, *_):
        try:
            self.manager.current = "MainMenu"
        except Exception:
            pass

    def _rebuild_owner_rows(self):
        try:
            count = int(self.player_count_spinner.text)
        except (ValueError, TypeError):
            count = 2
        count = max(2, min(4, count))
        existing_owners = [r.owner for r in self._owner_rows]
        existing_teams = [r.team for r in self._owner_rows]
        team_map = self.nuz.get("soullink_team_membership", {}) or {}
        expected = self.nuz.get("soullink_expected_owners", []) or []
        self.owner_area.clear_widgets()
        self._owner_rows = []
        show_team = (self.mode_spinner.text == "versus")
        for i in range(count):
            owner = existing_owners[i] if i < len(existing_owners) else (
                expected[i] if i < len(expected) else ""
            )
            team_letter = existing_teams[i] if i < len(existing_teams) else (
                team_map.get(owner, TEAM_LETTERS[i % 2])
            )
            row = OwnerRow(i, initial_owner=owner, initial_team=team_letter,
                            show_team=show_team)
            self._owner_rows.append(row)
            self.owner_area.add_widget(row)

    def _on_mode_changed(self, spinner, value):
        show_team = (value == "versus")
        for row in self._owner_rows:
            row.set_team_visible(show_team)

    def _on_player_count_changed(self, spinner, value):
        self._rebuild_owner_rows()

    def _collect_config(self) -> dict:
        owners = [r.owner for r in self._owner_rows if r.owner]
        team_map = {}
        if self.mode_spinner.text == "versus":
            for r in self._owner_rows:
                if r.owner:
                    team_map[r.owner] = r.team
        # Alle rule_* Keys aus nuz mitschicken, damit Server sie für
        # Enforcement (Ersttyp-Clash etc.) auslesen kann.
        rules = {k: v for k, v in self.nuz.items() if k.startswith("rule_")}
        return {
            "mode": self.mode_spinner.text,
            "player_count": len(owners),
            "link_strategy": self.strategy_spinner.text,
            "team_membership": team_map,
            "expected_owners": owners,
            "rules": rules,
        }

    def _send_config(self):
        cfg = self._collect_config()
        try:
            asyncio.create_task(self.munchlax.send_soullink_config(cfg))
            self.status_label.text = f"Config gesendet: mode={cfg['mode']}, {len(cfg['expected_owners'])} Spieler"
            logger.info(f"Soullink-Config gesendet: {cfg}")
        except Exception as err:
            logger.error(f"send_soullink_config failed: {type(err)},{err}")
            logger.error(f"{traceback.format_exc()}")
            self.status_label.text = f"Fehler: {err}"

    def _load_from_nuz(self):
        self.mode_spinner.text = self.nuz.get("soullink_mode", "off") or "off"
        self.player_count_spinner.text = str(self.nuz.get("soullink_player_count", 2) or 2)
        self.strategy_spinner.text = self.nuz.get("soullink_link_strategy", "full_chain") or "full_chain"
        self._rebuild_owner_rows()
        self.status_label.text = "Aus Session geladen"

    def _send_token_redeem(self):
        owner = (self.token_owner_input.text or "").strip()
        route_text = (self.token_route_input.text or "").strip()
        edition_text = (self.token_edition_input.text or "").strip()
        if not owner or not route_text:
            self.status_label.text = "Token: owner + route erforderlich"
            return
        try:
            route = int(route_text)
        except ValueError:
            self.status_label.text = "Token: route muss int sein"
            return
        edition = None
        if edition_text:
            try:
                edition = int(edition_text)
            except ValueError:
                edition = edition_text
        try:
            asyncio.create_task(
                self.munchlax.send_soullink_token_redeem(owner, edition, route)
            )
            self.status_label.text = f"Token-Redeem gesendet: {owner} route {route}"
        except Exception as err:
            logger.error(f"send_soullink_token_redeem failed: {err}")
            self.status_label.text = f"Token-Fehler: {err}"

    def _refresh_token_status(self, _dt):
        tokens = getattr(self.munchlax, "soullink_tokens", None) or {}
        if not tokens:
            self.token_status_label.text = "Tokens: —"
            return
        parts = []
        for owner, st in tokens.items():
            avail = int(st.get("earned", 0)) - int(st.get("used", 0))
            active = st.get("active_route")
            parts.append(f"{owner}: {avail} frei" + (f" | aktiv Route {active}" if active else ""))
        self.token_status_label.text = "Tokens: " + " ; ".join(parts)

    def _snapshot_manager(self) -> SnapshotManager:
        session_path = str(self.configsave)
        session_name = Path(session_path).name or "default"
        return SnapshotManager(session_path, session_name,
                                 bh_config=self.bh, munchlax=self.munchlax)

    def _create_snapshot(self):
        label = (self.snapshot_label_input.text or "manual").strip() or "manual"
        try:
            sm = self._snapshot_manager()
            snap_id = sm.create(label=label)
            if snap_id:
                self.status_label.text = f"Snapshot erstellt: {snap_id}"
                self._refresh_snapshot_list()
            else:
                self.status_label.text = "Snapshot-Erstellung fehlgeschlagen"
        except Exception as err:
            logger.error(f"snapshot create failed: {type(err)},{err}")
            logger.error(f"{traceback.format_exc()}")
            self.status_label.text = f"Snapshot-Fehler: {err}"

    def _refresh_snapshot_list(self):
        self.snapshot_list_layout.clear_widgets()
        try:
            entries = self._snapshot_manager().list()
        except Exception as err:
            logger.error(f"snapshot list failed: {err}")
            entries = []
        if not entries:
            self.snapshot_list_layout.add_widget(Label(text="(keine Snapshots)",
                                                        size_hint_y=None, height="30dp"))
            return
        for meta in entries:
            snap_id = meta.get("id", "?")
            label = meta.get("label", "?")
            ts = meta.get("timestamp", "?")
            saves = len(meta.get("save_backups", []))
            errors = len(meta.get("errors", []))
            row = BoxLayout(orientation="horizontal", size_hint_y=None,
                             height="30dp", spacing="4dp")
            info = f"{label} — {ts} — Saves:{saves}" + (f" — Errors:{errors}" if errors else "")
            row.add_widget(Label(text=info, size_hint_x=0.55, halign="left"))
            row.add_widget(Button(text="Laden", size_hint_x=0.20,
                                    on_press=lambda _btn, sid=snap_id: self._restore_snapshot(sid)))
            row.add_widget(Button(text="Löschen", size_hint_x=0.25,
                                    on_press=lambda _btn, sid=snap_id: self._delete_snapshot(sid)))
            self.snapshot_list_layout.add_widget(row)

    def _restore_snapshot(self, snap_id: str):
        try:
            # DB muss vor restore geschlossen werden
            db = getattr(self.munchlax, "pokedex_db", None)
            if db is not None:
                try:
                    db.close()
                except Exception as err:
                    logger.warning(f"pokedex_db close vor restore: {err}")
                self.munchlax.pokedex_db = None
            sm = self._snapshot_manager()
            ok = sm.restore(snap_id)
            self.status_label.text = (
                f"Snapshot geladen: {snap_id}" if ok else f"Restore fehlgeschlagen: {snap_id}"
            )
        except Exception as err:
            logger.error(f"snapshot restore failed: {type(err)},{err}")
            logger.error(f"{traceback.format_exc()}")
            self.status_label.text = f"Restore-Fehler: {err}"

    def _delete_snapshot(self, snap_id: str):
        try:
            self._snapshot_manager().delete(snap_id)
            self._refresh_snapshot_list()
            self.status_label.text = f"Snapshot gelöscht: {snap_id}"
        except Exception as err:
            logger.error(f"snapshot delete failed: {err}")
            self.status_label.text = f"Delete-Fehler: {err}"

    def _apply_preset(self):
        pid = self._preset_label_to_id.get(self.preset_spinner.text)
        if pid is None or pid not in self._presets:
            self.status_label.text = "Preset nicht gefunden"
            return
        preset = self._presets[pid]
        apply_preset(self.nuz, preset)
        self._load_from_nuz()
        self.status_label.text = f"Preset '{preset.get('label', pid)}' angewendet (noch nicht gespeichert)"

    def _save_to_nuz(self):
        cfg = self._collect_config()
        self.nuz["soullink_mode"] = cfg["mode"]
        self.nuz["soullink_player_count"] = cfg["player_count"]
        self.nuz["soullink_link_strategy"] = cfg["link_strategy"]
        self.nuz["soullink_team_membership"] = cfg["team_membership"]
        self.nuz["soullink_expected_owners"] = cfg["expected_owners"]
        try:
            import yaml
            with open(f"{self.configsave}nuzlocke.yml", "w") as f:
                yaml.dump(self.nuz, f)
            self.status_label.text = "In Session gespeichert"
        except Exception as err:
            logger.error(f"nuzlocke.yml speichern failed: {type(err)},{err}")
            self.status_label.text = f"Speichern-Fehler: {err}"
