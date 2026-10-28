"""KivyMD-Hauptmenü (Phase C des KivyMD-Umzugs).

Ersetzt den rein optischen Prototyp durch die volle Verdrahtung der klassischen
``frontend/widgets/mainmenu.py``: Verbindung (Server/Client/OBS/Overlay/Emulator),
Anzeige-Toggles + Sortierung, Aktionen (Randomisieren, Logs, Session-Reset/-Wechsel)
und die Team-Anzeige (``TrainerBoxMD``).

Die Geschäftslogik liegt wie in der Klassik in den Controllern
(``ConnectionController`` / ``RandomizerController`` / ``SettingsController``) und
in ``backend.rando_sync`` — hier lebt nur UI + Button-State.

Navigation zu Pokedex/Box/Tasche/Encounter/Settings/Sessions macht die
NavigationRail (B1); daher keine Nav-Buttons hier. Nuzlocke folgt in Phase E.
"""
import asyncio
import traceback
from pathlib import Path

from kivy.clock import Clock
from kivy.core.clipboard import Clipboard
from kivy.core.window import Window
from kivy.metrics import dp

from kivymd.app import MDApp
from kivymd.uix.boxlayout import MDBoxLayout
from kivymd.uix.button import MDButton, MDButtonText, MDIconButton
from kivymd.uix.card import MDCard
from kivymd.uix.dialog import (MDDialog, MDDialogHeadlineText,
                               MDDialogSupportingText, MDDialogButtonContainer)
from kivymd.uix.divider import MDDivider
from kivymd.uix.gridlayout import MDGridLayout
from kivymd.uix.label import MDLabel
from kivymd.uix.screen import MDScreen
from kivymd.uix.scrollview import MDScrollView
from kivymd.uix.segmentedbutton import (MDSegmentedButton,
                                        MDSegmentedButtonItem,
                                        MDSegmentButtonLabel)
from kivymd.uix.selectioncontrol import MDSwitch
from kivy.uix.widget import Widget

from backend.classes.obs import OBS
from backend.controller.connection_controller import ConnectionController
from backend.controller.randomizer_controller import RandomizerController
from backend.controller.settings_controller import SettingsController
from backend.logging_setup import get_logger
from backend.perf_probe import perf_span
from backend.player_colors import PLAYER_COLORS
from backend.rando_sync import apply_rando_to_munchlax
from frontend.widgets.md_common import confirm_dialog, show_snackbar
from frontend.widgets.statusbarmd import PollingStatusDot
from frontend.widgets.trainerboxmd import TrainerBoxMD

logger = get_logger(__name__, 'logs/frontend.log')

# Spiele, die über Citra/Azahar laufen (kein BizHawk).
CITRA_GAMES = ['X', 'Y', 'Omega Rubin', 'Alpha Saphir',
               'Sonne', 'Mond', 'Ultra Sonne', 'Ultra Mond']
# Unter dieser Breite klappt die Steuer-Spalte automatisch zur schmalen
# Icon-Leiste ein (Team behält den Fokus); darüber bleibt sie ausgeklappt.
# Der Chevron-Toggle übersteuert die Automatik (_user_collapsed).
COLLAPSE_WIDTH = 900
TRAINER_MIN_WIDTH = 300
CONTROL_WIDTH = dp(340)   # feste Breite der Steuer-Spalte (breit genug für Buttons)


def _section_title(text: str) -> MDLabel:
    lbl = MDLabel(text=text, adaptive_height=True)
    lbl.role = "small"
    lbl.bold = True
    return lbl


def _full_button(text: str, on_release) -> MDButton:
    """Vollbreiter MDButton (filled) mit Text-Callback."""
    return MDButton(MDButtonText(text=text), style="filled",
                    theme_width="Custom", size_hint_x=1, on_release=on_release)


class MainMenu(MDScreen):
    def __init__(self, arceus, bizhawk, citra, bizhawk_instances, munchlax,
                 obs_websocket, overlay_server, configsave, sp, rem, obs, bh, pl,
                 rnd, ov, app_version, **kwargs):
        super().__init__(**kwargs)
        self.name = "MainMenu"
        self.arceus = arceus
        self.bizhawk = bizhawk
        self.citra = citra
        self.bizhawk_instances = bizhawk_instances
        self.munchlax = munchlax
        self.obs_websocket: OBS = obs_websocket
        self.overlay_server = overlay_server
        self.configsave = configsave
        self.sp = sp
        self.rem = rem
        self.obs = obs
        self.bh = bh
        self.pl = pl
        self.rnd = rnd
        self.ov = ov
        self.app_version = app_version
        self.selected_session = ""

        # Strong-Refs für asyncio-Tasks (sonst GC, Memory feedback_async_task_strong_ref)
        self.connectors: set = set()
        # 3-Poll-Grace gegen Button-Text-Flackern beim Verbinden
        self._btn_sync_strikes = {"client": 0, "obs": 0}
        # Unterdrückt save_changes während init_config programmatisch Switches setzt
        # (MDSwitch/SegmentedButton feuern on_active auch bei programmatischem Set).
        self._suspend_save = False
        # Aktueller Modus/Sort-State
        self._server_mode = bool(rem.get("start_server", False))
        self._current_sort = sp.get("order", "team")
        self._emulator_is_citra = pl.get("session_game") in CITRA_GAMES
        # Einklapp-Zustand der Control-Spalte: None = automatisch (nach Breite),
        # True/False = vom User per Chevron erzwungen.
        self._user_collapsed = None
        self._collapsed = None   # aktuell angewandter Zustand (gegen Resize-Thrash)

        _app = MDApp.get_running_app()
        _bh_thread = getattr(_app, 'bizhawk_thread', None)
        _arc_thread = getattr(_app, 'arceus_thread', None)
        _mun_thread = getattr(_app, 'munchlax_thread', None)
        self.controller = SettingsController(configsave, sp, rem, obs, bh, pl, rnd,
                                             arceus, bizhawk, munchlax, obs_websocket,
                                             ov, overlay_server, bizhawk_thread=_bh_thread)
        self.connection = ConnectionController(arceus, bizhawk, citra, bizhawk_instances,
                                               munchlax, obs_websocket, bh, pl, overlay_server,
                                               bizhawk_thread=_bh_thread, arceus_thread=_arc_thread,
                                               munchlax_thread=_mun_thread)
        self.randomizer = RandomizerController(rnd, pl, configsave=configsave)

        # Transparenz-Toasts für host-angefragte Log-Bundles (Backend-Threads →
        # Kivy-Thread via Clock).
        try:
            if munchlax is not None:
                munchlax.set_bundle_request_notify(self._on_bundle_request_notify)
        except Exception as err:
            logger.warning(f"set_bundle_request_notify failed: {err}")
        try:
            if arceus is not None:
                arceus.set_bundle_response_notify(self._on_bundle_response_notify)
        except Exception as err:
            logger.warning(f"set_bundle_response_notify failed: {err}")

        self.trainer_boxes: dict[int, TrainerBoxMD] = {}
        # Beim Schrumpfen der Spieleranzahl geparkte Boxen (Tick gestoppt, nicht
        # im Grid) — beim Wachsen wiederverwendet statt neu gebaut.
        self._parked_boxes: dict[int, TrainerBoxMD] = {}
        self._client_rows: dict = {}

        self._build_ui()

        # Verbindungs-/Client-Status 1s-getickt nachziehen (wie Klassik).
        self._client_tick = Clock.schedule_interval(self._refresh_client_list, 1)
        Window.bind(on_resize=self._on_window_resize)

        # Initiale UI-States aus sp/rem/bh/pl setzen (sonst stehen alle Switches
        # auf False und der erste Toggle würde die User-Config mit Defaults
        # überschreiben — sync-Review KRITISCH). Die Klassik baut das inline in
        # create_control_frame; MD macht es hier einmalig beim Konstruieren.
        self.init_config()
        self.update_munchlax_connections()

    def _track(self, task) -> None:
        """asyncio-Task stark halten (self.connectors) + Auto-Cleanup +
        Exception-Logging (Memory feedback_async_task_strong_ref)."""
        if task is None:
            return
        self.connectors.add(task)
        task.add_done_callback(self._on_task_done)

    def _on_task_done(self, task) -> None:
        self.connectors.discard(task)
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
            logger.error(f"MainMenu-Task fehlgeschlagen: {exc!r}\n{tb}")

    # -- Aufbau ----------------------------------------------------------------

    def _build_ui(self) -> None:
        scroll = MDScrollView(do_scroll_x=False)
        self.body = MDBoxLayout(orientation="horizontal", size_hint_y=None,
                                adaptive_height=True, padding=dp(16), spacing=dp(16))

        # Control-Spalte (ausgeklappt): Chevron-Einklappen + 3 Karten.
        self.control_column = MDBoxLayout(orientation="vertical", size_hint_x=1,
                                          size_hint_y=None, adaptive_height=True,
                                          spacing=dp(12), pos_hint={"top": 1})
        collapse_row = MDBoxLayout(orientation="horizontal", size_hint_y=None,
                                   height=dp(32))
        collapse_row.add_widget(Widget())
        collapse_row.add_widget(MDIconButton(
            icon="chevron-left", on_release=lambda *_: self._toggle_collapse(True)))
        self.control_column.add_widget(collapse_row)
        self.control_column.add_widget(self._build_connection_card())
        self.control_column.add_widget(self._build_display_card())
        self.control_column.add_widget(self._build_action_card())

        # Control-Spalte (eingeklappt): schmale Icon-Leiste.
        self.icon_strip = self._build_icon_strip()

        # Holder hält entweder control_column oder icon_strip; seine Breite
        # bestimmt den belegten Platz (Team bekommt den Rest).
        self.control_holder = MDBoxLayout(orientation="vertical", size_hint_x=None,
                                          width=CONTROL_WIDTH, size_hint_y=None,
                                          adaptive_height=True, pos_hint={"top": 1})
        self.control_holder.add_widget(self.control_column)

        self.team_column = MDBoxLayout(orientation="vertical", size_hint_y=None,
                                       adaptive_height=True, spacing=dp(8),
                                       size_hint_x=1, pos_hint={"top": 1})
        self.team_column.add_widget(_section_title("Teams"))
        self.trainer_grid = MDGridLayout(cols=1, size_hint_y=None, adaptive_height=True,
                                         spacing=dp(12))
        self.team_column.add_widget(self.trainer_grid)
        # Spaltenzahl folgt der tatsächlichen Breite der Team-Spalte.
        self.team_column.bind(width=lambda *_: self._update_cols())
        self.team_column.add_widget(MDLabel(
            text="Klick auf ein Pokemon → Detailansicht.", role="small",
            adaptive_height=True, size_hint_y=None))

        self.body.add_widget(self.control_holder)
        self.body.add_widget(self.team_column)
        scroll.add_widget(self.body)
        self.add_widget(scroll)

        self._build_trainer_boxes()
        Clock.schedule_once(lambda dt: self._apply_responsive(Window.width), 0)

    def _build_icon_strip(self) -> MDBoxLayout:
        """Schmale Icon-Leiste für den eingeklappten Zustand."""
        strip = MDBoxLayout(orientation="vertical", size_hint_x=1, size_hint_y=None,
                            adaptive_height=True, spacing=dp(4), padding=(dp(4), dp(4)),
                            pos_hint={"top": 1})
        strip.add_widget(MDIconButton(
            icon="chevron-right", pos_hint={"center_x": 0.5},
            on_release=lambda *_: self._toggle_collapse(False)))
        strip.add_widget(self._strip_item("lan", self._on_role_button,
                                          self._role_connected))
        strip.add_widget(self._strip_item("video-outline", self.toggle_obs,
                                          lambda: self.obs_websocket.is_connected))
        strip.add_widget(self._strip_item("layers-outline", self.toggle_overlay,
                                          lambda: self.overlay_server.is_connected))
        strip.add_widget(self._strip_item("gamepad-variant-outline", self.toggle_emulator,
                                          self._emulator_connected))
        strip.add_widget(self._strip_item("dice-multiple-outline", self.start_randomization))
        return strip

    def _strip_item(self, icon: str, on_release, provider=None) -> MDBoxLayout:
        box = MDBoxLayout(orientation="vertical", size_hint_y=None, adaptive_height=True,
                          spacing=0)
        btn = MDIconButton(icon=icon, pos_hint={"center_x": 0.5})
        btn.bind(on_release=lambda *_: on_release())
        box.add_widget(btn)
        if provider is not None:
            dot = PollingStatusDot(provider)
            dot.pos_hint = {"center_x": 0.5}  # nicht als kwarg (StatusDot setzt pos_hint selbst)
            box.add_widget(dot)
        return box

    def _toggle_collapse(self, collapse: bool) -> None:
        self._user_collapsed = collapse
        self._collapsed = collapse
        self._apply_collapse(collapse)

    def _apply_collapse(self, collapsed: bool) -> None:
        self.control_holder.clear_widgets()
        if collapsed:
            self.control_holder.width = dp(56)
            self.control_holder.add_widget(self.icon_strip)
        else:
            self.control_holder.width = CONTROL_WIDTH
            self.control_holder.add_widget(self.control_column)
        Clock.schedule_once(lambda dt: self._update_cols(), 0)

    def _build_connection_card(self) -> MDCard:
        card = MDCard(orientation="vertical", style="elevated", padding=dp(12),
                      spacing=dp(10), size_hint_y=None, adaptive_height=True,
                      radius=[dp(16)] * 4)
        card.add_widget(_section_title("VERBINDUNG"))

        # Server/Client-Segmented
        self.role_segment = MDSegmentedButton()
        self._seg_server = MDSegmentedButtonItem(MDSegmentButtonLabel(text="Server"))
        self._seg_client = MDSegmentedButtonItem(MDSegmentButtonLabel(text="Client"))
        self.role_segment.add_widget(self._seg_server)
        self.role_segment.add_widget(self._seg_client)
        # bind(active=...) (Property-Change), NICHT on_active: letzteres ist ein
        # Event-Bind, das bei MDSegmentedButtonItem.active NICHT feuert (deshalb
        # blieb der Modus beim Umschalten hängen).
        self._seg_server.bind(active=self._on_role_segment)
        self._seg_client.bind(active=self._on_role_segment)
        card.add_widget(self.role_segment)

        # Aktions-Button (Server/Client starten/beenden) + Inline-Status
        self.role_button = _full_button("Server starten", self._on_role_button)
        card.add_widget(self._row_with_dot(self.role_button, self._role_connected))

        # Fester Slot für den "Alle Clients zurücksetzen"-Button (nur Server-Modus)
        self._clear_holder = MDBoxLayout(orientation="vertical", size_hint_y=None,
                                         adaptive_height=True)
        card.add_widget(self._clear_holder)
        self.clear_button = MDButton(MDButtonText(text="Alle Clients zurücksetzen"),
                                     style="tonal", theme_width="Custom", size_hint_x=1,
                                     on_release=self.clear_clients)

        self.obs_button = _full_button("OBS verbinden", self.toggle_obs)
        self.obs_button.style = "outlined"
        card.add_widget(self._row_with_dot(
            self.obs_button, lambda: self.obs_websocket.is_connected))

        self.overlay_button = _full_button("Overlay starten", self.toggle_overlay)
        self.overlay_button.style = "outlined"
        card.add_widget(self._row_with_dot(
            self.overlay_button, lambda: self.overlay_server.is_connected))

        self.emulator_button = _full_button("Bizhawk starten", self.toggle_emulator)
        card.add_widget(self._row_with_dot(
            self.emulator_button, self._emulator_connected))

        card.add_widget(MDDivider())
        card.add_widget(MDLabel(text="Verbundene Clients", role="small",
                                adaptive_height=True, size_hint_y=None))
        self.client_list_box = MDBoxLayout(orientation="vertical", size_hint_y=None,
                                           adaptive_height=True, spacing=dp(2))
        card.add_widget(self.client_list_box)
        return card

    def _row_with_dot(self, button: MDButton, provider) -> MDBoxLayout:
        row = MDBoxLayout(orientation="horizontal", size_hint_y=None, height=dp(44),
                          spacing=dp(8))
        row.add_widget(button)
        dot = PollingStatusDot(provider)
        row.add_widget(dot)
        return row

    def _build_display_card(self) -> MDCard:
        card = MDCard(orientation="vertical", style="elevated", padding=dp(12),
                      spacing=dp(10), size_hint_y=None, adaptive_height=True,
                      radius=[dp(16)] * 4)
        card.add_widget(_section_title("ANZEIGE"))

        card.add_widget(MDLabel(text="Sortierung", role="small", adaptive_height=True,
                                size_hint_y=None))
        self.sort_segment = MDSegmentedButton()
        self._sort_items: dict[str, MDSegmentedButtonItem] = {}
        # Vorerst nur Team + Route (DexNr/Level liefen über den Rand der schmalen
        # Steuer-Spalte; die Sortier-Logik in munchlax unterstützt weiterhin alle).
        for text, sort_id in (("Team", "team"), ("Route", "route")):
            item = MDSegmentedButtonItem(MDSegmentButtonLabel(text=text))
            item._sort_id = sort_id
            item.bind(active=self._on_sort_segment)
            self.sort_segment.add_widget(item)
            self._sort_items[sort_id] = item
        card.add_widget(self.sort_segment)

        # Toggles (MDSwitch + Label)
        toggles = (
            ("names", "Namen anzeigen"),
            ("badges", "Orden anzeigen"),
            ("items", "Items anzeigen"),
            ("animated", "animierte Sprites"),
            ("hp", "KP-Leiste"),
            ("status", "Status-Effekte"),
            ("reorder", "Verschiebung animieren"),
            ("saveram", "SaveRAM automatisch"),
        )
        self._switches: dict[str, MDSwitch] = {}
        grid = MDGridLayout(cols=2, size_hint_y=None, adaptive_height=True,
                            spacing=dp(6))
        for key, label in toggles:
            row = MDBoxLayout(orientation="horizontal", size_hint_y=None, height=dp(40),
                              spacing=dp(6))
            sw = MDSwitch()
            sw.bind(active=self._on_toggle)
            self._switches[key] = sw
            row.add_widget(sw)
            row.add_widget(MDLabel(text=label, role="small", valign="center"))
            grid.add_widget(row)
        card.add_widget(grid)
        return card

    def _build_action_card(self) -> MDCard:
        card = MDCard(orientation="vertical", style="elevated", padding=dp(12),
                      spacing=dp(8), size_hint_y=None, adaptive_height=True,
                      radius=[dp(16)] * 4)
        card.add_widget(_section_title("AKTIONEN"))

        self.randomize_button = _full_button("Randomisieren", self.start_randomization)
        card.add_widget(self.randomize_button)

        logs_row = MDBoxLayout(orientation="horizontal", size_hint_y=None, height=dp(44),
                               spacing=dp(8))
        self.pack_logs_button = MDButton(MDButtonText(text="Logs packen"), style="outlined",
                                         theme_width="Custom", size_hint_x=1,
                                         on_release=self._on_pack_logs)
        self.request_logs_button = MDButton(MDButtonText(text="Logs anfordern"),
                                            style="outlined", theme_width="Custom",
                                            size_hint_x=1, on_release=self._on_request_logs)
        logs_row.add_widget(self.pack_logs_button)
        logs_row.add_widget(self.request_logs_button)
        card.add_widget(logs_row)

        self.change_session_button = MDButton(MDButtonText(text="Session wechseln"),
                                              style="tonal", theme_width="Custom",
                                              size_hint_x=1, on_release=self.change_session)
        card.add_widget(self.change_session_button)

        self.reset_session_button = MDButton(MDButtonText(text="Session zurücksetzen"),
                                             style="text", theme_width="Custom",
                                             size_hint_x=1, on_release=self._confirm_session_reset)
        card.add_widget(self.reset_session_button)

        card.add_widget(MDLabel(text="Nuzlocke folgt in Phase E.", role="small",
                                adaptive_height=True, size_hint_y=None))
        return card

    # -- Team ------------------------------------------------------------------

    def _build_trainer_boxes(self) -> None:
        for player in range(1, self.pl["player_count"] + 1):
            box = TrainerBoxMD(player, self.munchlax, self.obs_websocket, self,
                               PLAYER_COLORS.get(player, PLAYER_COLORS[1]))
            self.trainer_boxes[player] = box
            self.trainer_grid.add_widget(box)

    def _sync_trainer_boxes(self) -> None:
        """Team-Boxen an pl['player_count'] angleichen und dabei bestehende Boxen
        wiederverwenden (reset_for_session) statt sie neu zu bauen — der Neubau
        kostete beim Session-Wechsel ~130 ms pro Box, Ausreißer bis ~800 ms.
        Überzählige Boxen werden geparkt, nicht verworfen."""
        count = self.pl["player_count"]
        for player in sorted(p for p in self.trainer_boxes if p > count):
            box = self.trainer_boxes.pop(player)
            try:
                box.cleanup()
            except Exception:
                logger.warning(f"TrainerBoxMD.cleanup fehlgeschlagen: {traceback.format_exc()}")
            self.trainer_grid.remove_widget(box)
            self._parked_boxes[player] = box
        for player in range(1, count + 1):
            box = self.trainer_boxes.get(player)
            if box is None and player in self._parked_boxes:
                # Geparkte Box zurückholen: Tick neu starten, an Spieler-Position einfügen
                box = self._parked_boxes.pop(player)
                try:
                    box.resume()
                except Exception:
                    logger.error(f"TrainerBoxMD.resume (Spieler {player}) fehlgeschlagen: "
                                 f"{traceback.format_exc()}")
                self.trainer_boxes[player] = box
                index = len(self.trainer_grid.children) - (player - 1)
                self.trainer_grid.add_widget(box, index=max(0, index))
            if box is not None:
                try:
                    box.reset_for_session()
                    continue
                except Exception:
                    # Fallback: kaputte Box verwerfen und neu bauen
                    logger.error(f"TrainerBoxMD.reset_for_session (Spieler {player}) "
                                 f"fehlgeschlagen, baue neu: {traceback.format_exc()}")
                    self.trainer_boxes.pop(player, None)
                    try:
                        box.cleanup()
                    except Exception:
                        logger.warning(f"TrainerBoxMD.cleanup fehlgeschlagen: {traceback.format_exc()}")
                    self.trainer_grid.remove_widget(box)
            # Neue Box an Spieler-Position einfügen (Grid-Reihenfolge = Spieler-ID)
            box = TrainerBoxMD(player, self.munchlax, self.obs_websocket, self,
                               PLAYER_COLORS.get(player, PLAYER_COLORS[1]))
            self.trainer_boxes[player] = box
            index = len(self.trainer_grid.children) - (player - 1)
            self.trainer_grid.add_widget(box, index=max(0, index))

    def _clear_trainer_boxes(self) -> None:
        for box in self.trainer_boxes.values():
            try:
                box.cleanup()
            except Exception:
                logger.warning(f"TrainerBoxMD.cleanup fehlgeschlagen: {traceback.format_exc()}")
        self.trainer_grid.clear_widgets()
        self.trainer_boxes.clear()
        # Geparkte Boxen (Tick bereits gestoppt) verwerfen
        self._parked_boxes.clear()

    def rebuild_trainer_boxes(self) -> None:
        """Team-Boxen für die aktuelle pl['player_count'] neu aufbauen.

        Öffentlicher Hook, damit der Settings-Screen nach einer Änderung der
        Spieleranzahl die MainMenu-Teams sofort angleicht (Parität zum Klassik-
        update_trainer_boxes). Reiner UI-Rebuild, keine Verbindungs-Nebenwirkung.
        """
        try:
            self._clear_trainer_boxes()
            self._build_trainer_boxes()
        except Exception as err:
            logger.error(f"rebuild_trainer_boxes fehlgeschlagen: {err}")
            logger.error(traceback.format_exc())

    # -- Responsive ------------------------------------------------------------

    def _on_window_resize(self, _window, width, _height) -> None:
        self._apply_responsive(width)

    def _apply_responsive(self, width: float) -> None:
        # body bleibt immer horizontal; bei wenig Platz wird die Control-Spalte
        # zur schmalen Icon-Leiste eingeklappt (statt zu stapeln), damit die
        # Teams den Fokus behalten. _user_collapsed übersteuert die Automatik.
        collapsed = (self._user_collapsed if self._user_collapsed is not None
                     else (width < COLLAPSE_WIDTH))
        # Nur bei echtem Zustandswechsel neu aufbauen (sonst Layout-Thrash beim
        # Fenster-Ziehen, _apply_responsive feuert pro on_resize-Event).
        if collapsed == self._collapsed:
            return
        self._collapsed = collapsed
        self._apply_collapse(collapsed)

    def _update_cols(self, *_args) -> None:
        avail = self.team_column.width
        if avail <= 1:
            return
        cols = max(1, int(avail / (TRAINER_MIN_WIDTH + dp(12))))
        if self.trainer_grid.cols != cols:
            self.trainer_grid.cols = cols

    # -- Session-Hook (appmd._on_session_changed) ------------------------------

    def load_from_session(self) -> None:
        """Beim Session-Wechsel: Team-Boxen für neue player_count neu bauen +
        Controller-Werte in die UI laden (Port aus sessionsmenu→MainMenu-Flow)."""
        self.selected_session = str(self.configsave) if self.configsave is not None else ""
        self._emulator_is_citra = self.pl.get("session_game") in CITRA_GAMES
        # Session-Wechsel = harter Reset bestehender Verbindungen (Parität zum
        # Klassik-change_session-Flow, der immer disconnect_all machte). Ohne das
        # würden init_config/update_munchlax_connections Button-Texte + host/port
        # unter einer laufenden Verbindung umbiegen (Review-WARNUNG).
        if (getattr(self.munchlax, "is_connected", False)
                or getattr(self.arceus, "is_connected", False)
                or getattr(self.obs_websocket, "is_connected", False)
                or getattr(self.overlay_server, "is_connected", False)
                or getattr(self.bizhawk, "is_connected", False)):
            with perf_span("main.disconnect_all"):
                self.disconnect_all()
        with perf_span("main.clear_client_rows"):
            self._clear_client_rows()
        with perf_span("main.sync_trainer_boxes"):
            self._sync_trainer_boxes()
        with perf_span("main.init_config"):
            self.init_config()
        with perf_span("main.update_munchlax_connections"):
            self.update_munchlax_connections()
        Clock.schedule_once(lambda dt: self._apply_responsive(Window.width), 0)

    # -- Verbindung: Rolle -----------------------------------------------------

    def _on_role_segment(self, item, *_args) -> None:
        # on_active feuert für beide Items (True+False) → nur auf den aktiven reagieren.
        if self._suspend_save or not item.active:
            return
        self._set_server_mode(item is self._seg_server)

    def _set_server_mode(self, is_server: bool, initializing: bool = False) -> None:
        self._server_mode = is_server
        # Client-Zeilen gehören zum verlassenen Modus → beim Umschalten räumen.
        if not initializing:
            self._clear_client_rows()
        if is_server:
            self._set_button_text(self.role_button, "Server starten")
            if self.clear_button.parent is None:
                self._clear_holder.add_widget(self.clear_button)
        else:
            self._set_button_text(self.role_button, "Client starten")
            if self.clear_button.parent is not None:
                self._clear_holder.remove_widget(self.clear_button)
            if not initializing and self.rem.get("start_server"):
                self.connection.stop_server()
        if not initializing:
            self.save_changes()

    def _on_role_button(self, *_args) -> None:
        text = self._get_button_text(self.role_button)
        if self._server_mode:
            if text == "Server starten":
                self._track(self.connection.start_server())
                # Server impliziert lokalen Client-Connect (wie Klassik launchserver)
                self._connect_client_action()
                self._set_button_text(self.role_button, "Server beenden")
            elif text == "Server beenden":
                self.connection.stop_server()
                self._set_button_text(self.role_button, "Server starten")
        else:
            self._on_client_button()

    def _on_client_button(self) -> None:
        text = self._get_button_text(self.role_button)
        if text.endswith("starten"):
            self._connect_client_action()
            self._set_button_text(self.role_button, "Client beenden")
        elif text == "Client beenden":
            self.connection.disconnect_client()
            self._set_button_text(self.role_button, "Client starten")

    def _connect_client_action(self) -> None:
        self._track(self.connection.connect_client())

    def clear_clients(self, *_args) -> None:
        self.munchlax.clear_everything()
        # snapshot_clients (Thread-safe) für die zu behaltenden IDs
        keep = set(self.arceus.snapshot_clients().keys())
        if not self.munchlax.is_connected:
            keep.add(self.munchlax.client_id)
        for client_id in list(self._client_rows.keys()):
            if client_id not in keep:
                self._remove_client_row(client_id)

    # -- Verbindung: OBS / Overlay / Emulator ----------------------------------

    def toggle_obs(self, *_args) -> None:
        text = self._get_button_text(self.obs_button)
        if text == "OBS verbinden":
            self._track(self.connection.connect_obs())
            self._set_button_text(self.obs_button, "OBS trennen")
        elif text == "OBS trennen":
            self._track(self.connection.disconnect_obs())
            self._set_button_text(self.obs_button, "OBS verbinden")

    def toggle_overlay(self, *_args) -> None:
        text = self._get_button_text(self.overlay_button)
        if text == "Overlay starten":
            self._track(self.connection.start_overlay())
            self._set_button_text(self.overlay_button, "Overlay beenden")
        elif text == "Overlay beenden":
            self._track(self.connection.stop_overlay())
            self._set_button_text(self.overlay_button, "Overlay starten")

    def _emulator_connected(self):
        return self.citra.is_connected if self._emulator_is_citra else self.bizhawk.is_connected

    def _role_connected(self):
        # Status-Dot der Rolle: im Server-Modus arceus, im Client-Modus munchlax.
        return self.arceus.is_connected if self._server_mode else self.munchlax.is_connected

    def toggle_emulator(self, *_args) -> None:
        if self._emulator_is_citra:
            self._connect_citra()
        else:
            self._launchbh()

    def _launchbh(self) -> None:
        bizhawk_path = Path(self.bh["path"])
        if bizhawk_path.exists() and bizhawk_path.is_file() and self.bh["path"].endswith(".exe"):
            self.emulator_button.disabled = True
            text = self._get_button_text(self.emulator_button)
            if text == "Bizhawk starten":
                self.connection.start_bizhawk()
                self._set_button_text(self.emulator_button, "Bizhawk beenden")
            elif text == "Bizhawk beenden":
                confirm_dialog(
                    "Speichern nicht vergessen!",
                    "Hast du im Emulator gespeichert? BizHawk wird beendet.",
                    on_confirm=self._stop_bizhawk,
                    confirm_text="Ja, beenden", cancel_text="Abbrechen",
                )
        Clock.schedule_once(lambda dt: setattr(self.emulator_button, 'disabled', False), 5)

    def _stop_bizhawk(self) -> None:
        from frontend.bh_dispatch import bh_dispatch
        bh_dispatch(self.bizhawk.stop_and_terminate(self.bizhawk_instances),
                    name='bizhawk.stop_and_terminate')
        self._set_button_text(self.emulator_button, "Bizhawk starten")

    def _connect_citra(self) -> None:
        if not self.citra.started:
            self.citra.check_connection()
        text = self._get_button_text(self.emulator_button)
        if text == "Citra verbinden" and self.citra.is_connected:
            self._set_button_text(self.emulator_button, "Citra trennen")
            self._track(asyncio.create_task(
                self.citra.start(self.munchlax, self.emulator_button)))
        else:
            self._set_button_text(self.emulator_button, "Citra verbinden")
            if not self.citra.player_number:
                self.citra.is_connected = False
                show_snackbar("Kein Spieler ausgewählt — bitte in den Einstellungen setzen.",
                              level='warning', duration=5.0)
            self._track(asyncio.create_task(self.citra.stop()))

    def disconnect_all(self) -> None:
        self.connection.disconnect_all()

    # -- Verbindung: Status-Poll -----------------------------------------------

    def _refresh_client_list(self, _dt) -> None:
        try:
            self._sync_connection_button_texts()
            seen = set()
            if self.rem.get("start_server"):
                clients = self.arceus.snapshot_clients()
                for client_id, (name, _status) in clients.items():
                    seen.add(client_id)
                    self._ensure_client_row(
                        client_id, name,
                        lambda cid=client_id: self.arceus.munchlax_status.get(cid))
            else:
                if (self.munchlax.is_connected
                        and self._get_button_text(self.role_button) == "Client starten"):
                    self._set_button_text(self.role_button, "Client beenden")
                status_snapshot = self.munchlax.snapshot_remote_connection_status()
                names_snapshot = self.munchlax.snapshot_remote_connection_names()
                for client_id, _connected in status_snapshot.items():
                    seen.add(client_id)
                    name = names_snapshot.get(client_id, client_id[:8])
                    self._ensure_client_row(
                        client_id, name,
                        lambda cid=client_id: self.munchlax.remote_connection_status.get(cid))
            # Verwaiste Zeilen (nicht mehr im Snapshot) entfernen — sonst bleiben
            # disconnectete Clients samt Dot-Poll-Tick stehen (Review-Leak).
            for client_id in list(self._client_rows.keys()):
                if client_id not in seen:
                    self._remove_client_row(client_id)
        except Exception:
            logger.error(f"_refresh_client_list fehlgeschlagen: {traceback.format_exc()}")

    def _clear_client_rows(self) -> None:
        for client_id in list(self._client_rows.keys()):
            self._remove_client_row(client_id)

    def _ensure_client_row(self, client_id, name, provider) -> None:
        if client_id in self._client_rows:
            _row, _dot, label = self._client_rows[client_id]
            if label.text != name:
                label.text = name
            return
        row = MDBoxLayout(orientation="horizontal", size_hint_y=None, height=dp(24),
                          spacing=dp(8))
        dot = PollingStatusDot(provider)
        label = MDLabel(text=name, role="small", valign="center")
        row.add_widget(dot)
        row.add_widget(label)
        self.client_list_box.add_widget(row)
        self._client_rows[client_id] = (row, dot, label)

    def _remove_client_row(self, client_id) -> None:
        entry = self._client_rows.pop(client_id, None)
        if entry is not None:
            row, dot, _label = entry
            try:
                dot.stop()
            except Exception:
                pass
            self.client_list_box.remove_widget(row)

    def _sync_connection_button_texts(self) -> None:
        """Setzt Verbindungs-Button-Texte zurück, wenn eine Verbindung endgültig
        weg ist (3-Poll-Grace gegen Flackern; munchlax.reconnecting überbrückt
        den Auto-Reconnect ~35s). Port aus Klassik."""
        if (not self._server_mode and self._get_button_text(self.role_button) == "Client beenden"
                and not self.munchlax.is_connected and not self.munchlax.reconnecting):
            self._btn_sync_strikes["client"] += 1
            if self._btn_sync_strikes["client"] >= 3:
                self._set_button_text(self.role_button, "Client starten")
                self._btn_sync_strikes["client"] = 0
                logger.info("Client-Button zurückgesetzt: Verbindung weg, kein Auto-Reconnect")
        else:
            self._btn_sync_strikes["client"] = 0

        if (self._get_button_text(self.obs_button) == "OBS trennen"
                and not self.obs_websocket.is_connected):
            self._btn_sync_strikes["obs"] += 1
            if self._btn_sync_strikes["obs"] >= 3:
                self._set_button_text(self.obs_button, "OBS verbinden")
                self._btn_sync_strikes["obs"] = 0
                logger.info("OBS-Button zurückgesetzt: WebSocket weg")
        else:
            self._btn_sync_strikes["obs"] = 0

    # -- Anzeige: Toggles + Sortierung -----------------------------------------

    def _on_sort_segment(self, item, *_args) -> None:
        if self._suspend_save or not item.active:
            return
        self._current_sort = item._sort_id
        self.save_changes()

    def _on_toggle(self, *_args) -> None:
        if self._suspend_save:
            return
        self.save_changes()

    def init_config(self) -> None:
        """Lädt die Anzeige-/Verbindungs-Einstellungen in die UI (Port aus Klassik).
        Setzt Switches programmatisch → _suspend_save verhindert save_changes-Loop."""
        sp = self.controller.load_sprites()
        bh = self.controller.load_bizhawk()
        rem = self.controller.load_remote()
        pl = self.controller.load_player()

        self._suspend_save = True
        try:
            self._switches["animated"].active = bool(sp["animated"])
            self._switches["names"].active = bool(sp["show_nicknames"])
            self._switches["items"].active = bool(sp["show_items"])
            self._switches["hp"].active = bool(sp.get("show_hp_bars"))
            self._switches["status"].active = bool(sp.get("show_status_effects"))
            self._switches["reorder"].active = bool(sp.get("animate_obs_reorder"))

            game = pl["session_game"]
            if game in ['Sonne', 'Mond', 'Ultra Sonne', 'Ultra Mond']:
                self._switches["badges"].disabled = True
                self._switches["badges"].active = False
                self._switches["saveram"].disabled = True
                self._switches["saveram"].active = False
                self.sp["show_badges"] = False
            elif game in ['X', 'Y', 'Alpha Saphir', 'Omega Rubin']:
                self._switches["saveram"].disabled = True
                self._switches["saveram"].active = False
                self._switches["badges"].disabled = False
                self._switches["badges"].active = bool(sp["show_badges"])
            else:
                self._switches["saveram"].disabled = False
                self._switches["badges"].disabled = False
                self._switches["badges"].active = bool(sp["show_badges"])
            self._switches["saveram"].active = bool(bh["save_automatically"])

            # Sortierung
            self._current_sort = sp["order"]
            for sort_id, item in self._sort_items.items():
                item.active = (sort_id == sp["order"])

            # Rolle
            is_server = bool(rem["start_server"])
            self._seg_server.active = is_server
            self._seg_client.active = not is_server

            # Emulator-Button-Kontext (BizHawk vs Citra)
            self._emulator_is_citra = game in CITRA_GAMES
            self._set_button_text(
                self.emulator_button,
                "Citra verbinden" if self._emulator_is_citra else "Bizhawk starten")
            # OBS/Overlay-Button-Texte auf Start-State zurücksetzen: init_config
            # läuft beim Konstruieren und nach load_from_session (dort trennt
            # disconnect_all OBS/Overlay) — sonst blieben die Texte stale.
            self._set_button_text(self.obs_button, "OBS verbinden")
            self._set_button_text(self.overlay_button, "Overlay starten")
        finally:
            self._suspend_save = False

        self._set_server_mode(bool(rem["start_server"]), initializing=True)
        self.munchlax.change_order()

    def save_changes(self, *_args) -> None:
        values = {
            'animated': self._switches["animated"].active,
            'show_nicknames': self._switches["names"].active,
            'show_items': self._switches["items"].active,
            'show_badges': self._switches["badges"].active,
            'show_hp_bars': self._switches["hp"].active,
            'show_status_effects': self._switches["status"].active,
            'animate_obs_reorder': self._switches["reorder"].active,
            'save_automatically': self._switches["saveram"].active,
            'start_server': self._server_mode,
        }
        if self._current_sort:
            values['order'] = self._current_sort
        self.controller.save_main_menu_settings(values)
        self.munchlax.change_order()

    def update_munchlax_connections(self) -> None:
        ip = "127.0.0.1" if self.rem["start_server"] else self.rem["server_ip_adresse"]
        port = self.rem["client_port"] if self.rem["start_server"] else self.rem["server_port"]
        self.munchlax.host = ip
        self.munchlax.port = port

    # -- Aktionen: Randomisieren -----------------------------------------------

    def start_randomization(self, *_args) -> None:
        self.randomize_button.disabled = True
        self._set_button_text(self.randomize_button, "Randomisierung…")
        self._track(asyncio.create_task(self._run_randomization()))

    async def _run_randomization(self) -> None:
        success, message, slot_dirs = False, "", None
        try:
            success, message, slot_dirs = await self.randomizer.randomize()
            if success:
                apply_rando_to_munchlax(self.munchlax, self.randomizer, self.pl, logger)
        except Exception as err:
            logger.error(f"Fehler bei der Randomisierung: {type(err)}, {err}")
            logger.error(traceback.format_exc())
            show_snackbar(f"Randomisierung fehlgeschlagen: {err}", level='error', duration=6.0)
            return
        finally:
            self.randomize_button.disabled = False
            self._set_button_text(self.randomize_button, "Randomisieren")
        # Ergebnis-Dialog ausserhalb des try: ein Fehler hier soll nicht als
        # "Randomisierung fehlgeschlagen" fehletikettiert werden.
        self._show_rando_result(success, message, slot_dirs)

    def _show_rando_result(self, success: bool, message: str, slot_dirs) -> None:
        paths = [entry["dir"] for entry in (slot_dirs or [])]
        body = message
        if paths:
            body += "\n\nAusgabe-Ordner:\n" + "\n".join(f"Slot {e['slot']}: {e['dir']}"
                                                         for e in slot_dirs)
        dialog = MDDialog(auto_dismiss=False)
        dialog.add_widget(MDDialogHeadlineText(text="Randomisierung" if success else "Fehler"))
        dialog.add_widget(MDDialogSupportingText(text=body))

        buttons = MDDialogButtonContainer(spacing="8dp")
        buttons.add_widget(Widget())
        if paths:
            def _copy(*_):
                Clipboard.copy("\n".join(paths))
                show_snackbar("Pfad(e) kopiert.", level='success')
            buttons.add_widget(MDButton(MDButtonText(text="Pfade kopieren"),
                                        style="tonal", on_release=_copy))
        buttons.add_widget(MDButton(MDButtonText(text="OK"), style="filled",
                                    on_release=lambda *_: dialog.dismiss()))
        dialog.add_widget(buttons)
        dialog.open()

    # -- Aktionen: Logs --------------------------------------------------------

    def _on_pack_logs(self, *_args) -> None:
        self.pack_logs_button.disabled = True
        show_snackbar("Logs werden gepackt…", level='info')
        self._track(asyncio.create_task(self._pack_logs_async()))

    async def _pack_logs_async(self) -> None:
        from backend.log_bundler import build_bundle
        try:
            raw_session = str(self.configsave) if self.configsave is not None else ""
            if not raw_session.strip():
                show_snackbar("Keine Session gewählt — Logs packen abgebrochen.",
                              level='error', duration=4.0)
                return
            session_path = Path(raw_session)
            if not session_path.is_dir():
                show_snackbar(f"Session-Pfad fehlt: {session_path}", level='error', duration=4.0)
                return
            player_name = str(self.pl.get("your_name", "") or "unknown")
            is_host = bool(self.rem.get("start_server", False))
            try:
                self.munchlax.set_bundle_session_path(session_path)
            except Exception as err:
                logger.warning(f"set_bundle_session_path failed: {err}")

            try:
                bundle_path = await asyncio.to_thread(build_bundle, session_path,
                                                      player_name, is_host, None)
            except Exception as err:
                logger.error(f"build_bundle failed: {type(err).__name__}: {err}")
                logger.error(traceback.format_exc())
                show_snackbar(f"Logs packen fehlgeschlagen: {err}", level='error', duration=5.0)
                return

            size_mib = bundle_path.stat().st_size / (1024 * 1024)
            if is_host:
                show_snackbar(f"Bundle gespeichert ({size_mib:.1f} MiB): {bundle_path.name}",
                              level='success', duration=4.0)
                return
            if not getattr(self.munchlax, "is_connected", False):
                show_snackbar(f"Bundle lokal gespeichert ({bundle_path.name}). "
                              f"Kein Host verbunden — Upload übersprungen.",
                              level='info', duration=5.0)
                return
            try:
                ok, upload_msg = await self.munchlax.submit_cross_thread(
                    self.munchlax.upload_log_bundle(bundle_path, player_name))
            except Exception as err:
                logger.error(f"upload_log_bundle submit failed: {err}")
                logger.error(traceback.format_exc())
                show_snackbar(f"Bundle lokal da ({bundle_path.name}), Upload fehlgeschlagen: {err}",
                              level='error', duration=5.0)
                return
            show_snackbar(
                (f"Bundle lokal + Host-Upload fertig: {bundle_path.name}" if ok
                 else f"Bundle lokal da ({bundle_path.name}), Upload fehlgeschlagen: {upload_msg}"),
                level='success' if ok else 'error', duration=4.0)
        except Exception as err:
            logger.error(f"_pack_logs_async unhandled: {err}")
            logger.error(traceback.format_exc())
            show_snackbar(f"Fehler beim Packen/Senden: {err}", level='error', duration=5.0)
        finally:
            self.pack_logs_button.disabled = False

    def _on_request_logs(self, *_args) -> None:
        if not bool(self.rem.get("start_server", False)):
            show_snackbar("Nur der Host darf Logs von allen anfordern.",
                          level='warning', duration=4.0)
            return
        if getattr(self, "arceus", None) is None or getattr(self.arceus, "is_connected", None) != 'connected':
            show_snackbar("Arceus-Server nicht aktiv.", level='error', duration=3.0)
            return
        self.request_logs_button.disabled = True
        show_snackbar("Logs-Anfrage wird gesendet…", level='info')

        async def _runner():
            try:
                try:
                    sent = await self.connection.request_logs_from_all()
                except Exception as err:
                    logger.error(f"request_logs_from_all failed: {err}")
                    logger.error(traceback.format_exc())
                    show_snackbar(f"Anfrage fehlgeschlagen: {err}", level='error', duration=4.0)
                    return
                if sent and sent > 0:
                    show_snackbar(f"Anfrage an {sent} Client(s) gesendet. "
                                  f"Bundles landen unter support_bundles/.",
                                  level='success', duration=4.0)
                else:
                    show_snackbar("Keine Clients verbunden — Anfrage ohne Wirkung.",
                                  level='warning', duration=4.0)
            finally:
                self.request_logs_button.disabled = False

        self._track(asyncio.create_task(_runner()))

    # -- Aktionen: Session -----------------------------------------------------

    def _confirm_session_reset(self, *_args) -> None:
        if not self.rem.get("start_server"):
            show_snackbar("Nur der Host darf die Session zurücksetzen.",
                          level='warning', duration=4.0)
            return
        if getattr(self, "munchlax", None) is None:
            show_snackbar("Kein Munchlax-Objekt vorhanden.", level='error', duration=3.0)
            return
        confirm_dialog(
            "Session zurücksetzen — irreversibel",
            "Alle Encounter, Teams, Bag-Items und Soullink-Links werden gelöscht "
            "(auf allen verbundenen Trackern). Config, Snapshots und Runs bleiben.",
            on_confirm=self._start_session_reset,
            confirm_text="Ja, zurücksetzen", cancel_text="Abbrechen",
        )

    def _start_session_reset(self) -> None:
        self.reset_session_button.disabled = True
        show_snackbar("Session wird zurückgesetzt…", level='info')
        self._track(asyncio.create_task(self._session_reset_async()))

    async def _session_reset_async(self) -> None:
        try:
            try:
                ok, msg = await self.munchlax.submit_cross_thread(
                    self.munchlax.reset_session_data())
            except Exception as err:
                logger.error(f"reset_session_data failed: {type(err)},{err}")
                logger.error(traceback.format_exc())
                show_snackbar(f"Session-Reset fehlgeschlagen: {err}", level='error', duration=4.0)
                return
            show_snackbar(("Session zurückgesetzt — " if ok else "Session-Reset fehlgeschlagen — ") + msg,
                          level='success' if ok else 'error',
                          duration=3.0 if ok else 4.0)
        finally:
            self.reset_session_button.disabled = False

    def _needs_safe_switch(self) -> bool:
        """True, wenn vor einem Session-Wechsel der sichere Pfad nötig ist:
        laufende BizHawks oder eine aktive Verbindung (OBS/Overlay/Server/Client).
        Genutzt vom Rail-Navigations-Guard (appmd)."""
        if self.bizhawk_instances:
            return True
        for obj in (self.munchlax, self.arceus, self.obs_websocket,
                    self.overlay_server, self.bizhawk):
            if getattr(obj, "is_connected", False):
                return True
        return False

    def change_session(self, *_args) -> None:
        # Sicherer Pfad: vor dem Wechsel ggf. Save-Hinweis (laufende BizHawks),
        # dann trennen + speichern + zur SessionMenu navigieren.
        if self.bizhawk_instances:
            confirm_dialog(
                "Speichern nicht vergessen!",
                "Hast du gespeichert? Die Session wird gewechselt.",
                on_confirm=self._do_session_switch,
                confirm_text="Ja, wechseln", cancel_text="Abbrechen",
            )
        else:
            self._do_session_switch()

    def _do_session_switch(self) -> None:
        self.disconnect_all()
        self.save_changes()
        if self.manager is not None and self.manager.has_screen("SessionMenu"):
            self.manager.current = "SessionMenu"

    # -- Bundle-Transparenz-Toasts (Backend-Threads → Kivy) --------------------

    def _on_bundle_request_notify(self, request_id: str, stage: str, info: str = "") -> None:
        try:
            text_map = {
                "incoming": f"Host fordert Logs an — {info or 'sende gleich'}",
                "sending": "Host-Logs-Upload startet…",
                "finished": "Host-Logs-Upload fertig",
                "failed": f"Host-Logs-Upload fehlgeschlagen: {info}",
                "declined": f"Host-Logs-Upload abgelehnt: {info}",
            }
            text = text_map.get(stage, f"Host-Logs {stage}: {info}")
            level = {"finished": "success", "failed": "error",
                     "declined": "warning"}.get(stage, "info")
            dur = 10.0 if stage == "incoming" else 4.0
            Clock.schedule_once(lambda dt: show_snackbar(text, level=level, duration=dur), 0)
        except Exception as err:
            logger.warning(f"_on_bundle_request_notify failed: {err}")

    def _on_bundle_response_notify(self, client_id: str, player_name: str,
                                   request_id: str, status: str, info: str) -> None:
        try:
            label = player_name or client_id[:8]
            text_map = {
                "received": (f"Logs empfangen von {label}: {info}", "success"),
                "starting": (f"{label}: Upload startet", "info"),
                "finished": (f"{label}: Upload fertig", "success"),
                "declined": (f"{label}: Upload abgelehnt ({info})", "warning"),
                "failed": (f"{label}: Upload fehlgeschlagen ({info})", "error"),
            }
            text, level = text_map.get(status, (f"{label}: {status} {info}", "info"))
            Clock.schedule_once(lambda dt: show_snackbar(text, level=level, duration=4.0), 0)
        except Exception as err:
            logger.warning(f"_on_bundle_response_notify failed: {err}")

    # -- Helfer ----------------------------------------------------------------

    def _set_button_text(self, button: MDButton, text: str) -> None:
        """Setzt den Text eines MDButton (Text liegt im MDButtonText-Kind)."""
        for child in button.children:
            if isinstance(child, MDButtonText):
                child.text = text
                return

    def _get_button_text(self, button: MDButton) -> str:
        """Liest den Text eines MDButton (aus dem MDButtonText-Kind)."""
        for child in button.children:
            if isinstance(child, MDButtonText):
                return child.text
        return ""
