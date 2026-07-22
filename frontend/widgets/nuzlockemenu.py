"""NuzlockeMenu — Nuzlocke/Soullink-Modus, Regel-Presets, Timer/Countdown, Snapshots."""

import asyncio
import traceback
from pathlib import Path

from kivy.app import App
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
from frontend.widgets.mainmenu import BizhawkSavePopup

logger = get_logger(__name__, './logs/nuzlockemenu.log')


TEAM_LETTERS = ["A", "B", "C", "D"]

# Backend erwartet die Roh-Keys ("nuzlocke"/"coop"/"versus"/"versus_ffa"/"disabled").
# Im UI zeigen wir Klartext-Labels und übersetzen an den Grenzen.
# "nuzlocke" = Regeln aktiv, kein Soullink (Default); "versus_ffa" = jeder gegen
# jeden (kein Soullink, Scoreboard pro Spieler); "disabled" = Tracker ohne
# Nuzlocke-Regeln (für normale Runs). Legacy-Wert "off" aus alten Configs/Peers
# entspricht "nuzlocke" (Migration in initialize_tree).
MODE_LABELS = {
    "nuzlocke": "Nuzlocke",
    "coop": "Coop",
    "versus": "Versus (Teams)",
    "versus_ffa": "Versus (jeder gegen jeden)",
    "disabled": "Aus",
}
MODE_LABEL_TO_VALUE = {v: k for k, v in MODE_LABELS.items()}


def normalize_mode(value) -> str:
    """Übersetzt Legacy-/Leer-Werte auf den heutigen Modus-Schlüssel."""
    if not value or value == "off":
        return "nuzlocke"
    return value if value in MODE_LABELS else "nuzlocke"


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


class NuzlockeMenu(Screen):
    def __init__(self, munchlax, configsave, nuz: dict, bh: dict | None = None,
                 bizhawk=None, **kwargs):
        super().__init__(**kwargs)
        self.name = "NuzlockeMenu"
        self.munchlax = munchlax
        self.configsave = configsave
        self.nuz = nuz
        self.bh = bh or {}
        self.bizhawk = bizhawk
        self._owner_rows: list[OwnerRow] = []
        self._presets = load_presets()
        self._preset_choices = preset_choices(self._presets)
        self._preset_label_to_id = {label: pid for pid, label in self._preset_choices}

        # Screen-Root: Header oben fix, Rest scrollt (Content-Höhe > Fensterhöhe).
        screen_root = BoxLayout(orientation="vertical")

        header = BoxLayout(orientation="horizontal", size_hint_y=None, height="40dp",
                            padding=("10dp", "5dp"), spacing="10dp")
        back_btn = Button(text="Zurück zum Hauptmenü", size_hint_x=0.3, on_press=self._go_back)
        header.add_widget(back_btn)
        header.add_widget(Label(text="Nuzlocke & Timer", font_size="20sp"))
        screen_root.add_widget(header)

        scroll_all = ScrollView(size_hint=(1, 1), do_scroll_x=False)
        root = BoxLayout(orientation="vertical", padding="10dp", spacing="10dp",
                          size_hint_y=None)
        root.bind(minimum_height=root.setter("height"))
        scroll_all.add_widget(root)
        screen_root.add_widget(scroll_all)

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
        mode_value = normalize_mode(self.nuz.get("soullink_mode"))
        self.mode_spinner = Spinner(
            text=MODE_LABELS[mode_value],
            values=list(MODE_LABELS.values()),
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
        root.add_widget(config_row)

        # Owner-Zeilen — feste Höhe damit im Outer-Scroll konsistent bleibt.
        self.owner_area = BoxLayout(orientation="vertical", size_hint_y=None, spacing="4dp")
        self.owner_area.bind(minimum_height=self.owner_area.setter("height"))
        scroll = ScrollView(size_hint_y=None, height="180dp")
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
        self.snapshot_create_button = Button(text="Snapshot erstellen", size_hint_x=0.25,
                                                on_press=lambda *_: self._create_snapshot())
        snap_header.add_widget(self.snapshot_create_button)
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

        self.add_widget(screen_root)

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
        show_team = (self._mode_value() == "versus")
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
        show_team = (MODE_LABEL_TO_VALUE.get(value, value) == "versus")
        for row in self._owner_rows:
            row.set_team_visible(show_team)

    def _mode_value(self) -> str:
        return MODE_LABEL_TO_VALUE.get(self.mode_spinner.text, "nuzlocke")

    def _on_player_count_changed(self, spinner, value):
        self._rebuild_owner_rows()

    def _collect_config(self) -> dict:
        owners = [r.owner for r in self._owner_rows if r.owner]
        team_map = {}
        mode_value = self._mode_value()
        if mode_value == "versus":
            for r in self._owner_rows:
                if r.owner:
                    team_map[r.owner] = r.team
        # Alle rule_* Keys aus nuz mitschicken, damit Server sie für
        # Enforcement (Ersttyp-Clash etc.) auslesen kann.
        rules = {k: v for k, v in self.nuz.items() if k.startswith("rule_")}
        return {
            "mode": mode_value,
            "player_count": len(owners),
            "link_strategy": "full_chain",
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
        mode_value = normalize_mode(self.nuz.get("soullink_mode"))
        self.mode_spinner.text = MODE_LABELS[mode_value]
        self.player_count_spinner.text = str(self.nuz.get("soullink_player_count", 2) or 2)
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

    def _set_status(self, text: str):
        def _apply(_dt):
            self.status_label.text = text
        Clock.schedule_once(_apply, 0)

    def _create_snapshot(self):
        label = (self.snapshot_label_input.text or "manual").strip() or "manual"
        # Button waehrend Popup + Flow sperren, sonst startet ein Doppelklick
        # zwei parallele Flush+Create-Flows: der zweite ueberschreibt das
        # asyncio.Event im Flush-Dict pro client_id und laesst den ersten
        # ins Timeout laufen ("SaveRAM-Flush teils fehlgeschlagen"-Fehler
        # obwohl der Flush selbst geklappt hat).
        self.snapshot_create_button.disabled = True
        # Popup vorschalten: der User muss bestaetigen dass in-game gespeichert
        # wurde. Bei "Ja" flushen wir zuerst die SaveRAM (wichtig fuer Gen 4/5,
        # bei denen BizHawk sonst mit einem stale Puffer arbeitet) und legen
        # dann den Snapshot an. Bei "Nein" wird abgebrochen.
        popup = BizhawkSavePopup(
            title_override="Snapshot vorbereiten",
            text_override="Hast du im Spiel gespeichert?\n"
                           "(Ohne in-game-Save enthaelt der Snapshot einen alten Stand.)",
            # on_confirm gibt Coroutine zurueck — BizhawkSavePopup.on_yes
            # wrappt sie via asyncio.create_task. Kein Task-Objekt direkt
            # zurueckgeben, sonst umgeht der Aufrufer den Vertrag.
            on_confirm=lambda: self._flush_and_create_snapshot_async(label),
        )
        # on_cancel setzt canceled=True — Status + Button-Reset via on_dismiss.
        popup.bind(on_dismiss=lambda p: self._on_snapshot_popup_dismiss(p))
        self.status_label.text = "Warte auf Bestaetigung..."
        popup.open()

    def _on_snapshot_popup_dismiss(self, popup):
        if getattr(popup, "canceled", False):
            self._set_status("Snapshot abgebrochen — bitte erst in-game speichern.")
            self.snapshot_create_button.disabled = False
        # Bei "Ja" bleibt der Button gesperrt — der Flush+Create-Flow
        # gibt ihn im Finally-Zweig von _flush_and_create_snapshot_async frei.

    async def _flush_and_create_snapshot_async(self, label: str):
        try:
            self._set_status("SaveRAM wird geflusht...")
            if self.bizhawk is not None:
                try:
                    results = await self.bizhawk.flush_all_saverams(timeout=3.0)
                    if results:
                        failed = [cid for cid, ok in results.items() if not ok]
                        if failed:
                            logger.warning(f"SaveRAM-Flush teils fehlgeschlagen: {failed}")
                        # BizHawk schreibt asynchron auf Disk — kurzer Puffer,
                        # damit die SaveRAM-Datei sicher aktualisiert ist.
                        await asyncio.sleep(0.2)
                    else:
                        logger.info("Kein BizHawk-Client verbunden — Flush uebersprungen")
                except Exception as err:
                    logger.error(f"flush_all_saverams failed: {err}")
                    logger.error(traceback.format_exc())
            await self._create_snapshot_async(label)
        finally:
            def _reenable(_dt):
                self.snapshot_create_button.disabled = False
            Clock.schedule_once(_reenable, 0)

    async def _create_snapshot_async(self, label: str):
        try:
            sm = self._snapshot_manager()
            loop = asyncio.get_event_loop()
            snap_id = await loop.run_in_executor(None, sm.create, label)
        except Exception as err:
            logger.error(f"snapshot create failed: {type(err)},{err}")
            logger.error(f"{traceback.format_exc()}")
            self._set_status(f"Snapshot-Fehler: {err}")
            return
        if snap_id:
            self._set_status(f"Snapshot erstellt: {snap_id}")
            self._refresh_snapshot_list()
        else:
            self._set_status("Snapshot-Erstellung fehlgeschlagen")

    def _refresh_snapshot_list(self):
        asyncio.create_task(self._refresh_snapshot_list_async())

    async def _refresh_snapshot_list_async(self):
        try:
            sm = self._snapshot_manager()
            loop = asyncio.get_event_loop()
            entries = await loop.run_in_executor(None, sm.list)
        except Exception as err:
            logger.error(f"snapshot list failed: {err}")
            entries = []
        Clock.schedule_once(lambda _dt: self._render_snapshot_list(entries), 0)

    def _render_snapshot_list(self, entries):
        self.snapshot_list_layout.clear_widgets()
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
        self.status_label.text = f"Snapshot wird geladen: {snap_id}"
        asyncio.create_task(self._restore_snapshot_async(snap_id))

    async def _restore_snapshot_async(self, snap_id: str):
        try:
            self.munchlax.close_pokedex_db()
            sm = self._snapshot_manager()
            loop = asyncio.get_event_loop()
            ok = await loop.run_in_executor(None, sm.restore, snap_id)
        except Exception as err:
            logger.error(f"snapshot restore failed: {type(err)},{err}")
            logger.error(f"{traceback.format_exc()}")
            self._set_status(f"Restore-Fehler: {err}")
            return
        self._set_status(
            f"Snapshot geladen: {snap_id}" if ok else f"Restore fehlgeschlagen: {snap_id}"
        )

    def _delete_snapshot(self, snap_id: str):
        asyncio.create_task(self._delete_snapshot_async(snap_id))

    async def _delete_snapshot_async(self, snap_id: str):
        try:
            sm = self._snapshot_manager()
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, sm.delete, snap_id)
        except Exception as err:
            logger.error(f"snapshot delete failed: {err}")
            self._set_status(f"Delete-Fehler: {err}")
            return
        self._refresh_snapshot_list()
        self._set_status(f"Snapshot gelöscht: {snap_id}")

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
            app = App.get_running_app()
            app.save_config(f"{self.configsave}nuzlocke.yml", self.nuz)
            self.status_label.text = "In Session gespeichert"
        except Exception as err:
            logger.error(f"nuzlocke.yml speichern failed: {type(err)},{err}")
            self.status_label.text = f"Speichern-Fehler: {err}"
