"""NuzlockeMenu — Nuzlocke/Soullink-Modus, Regel-Presets, Timer/Countdown, Snapshots."""

import asyncio
import traceback
from pathlib import Path

from kivy.app import App
from kivy.clock import Clock
from kivy.graphics import Color, Rectangle
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.checkbox import CheckBox
from kivy.uix.floatlayout import FloatLayout
from kivy.uix.gridlayout import GridLayout
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
from frontend.widgets.toast import show_toast, show_pending_toast

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

# Alle Preset-relevanten Regeln als (key, label, default). Reihenfolge = UI-
# Reihenfolge in der 2-Spalten-Grid. Defaults dienen als Fallback wenn nuz-
# Dict den Key noch nicht enthaelt (z.B. alte Sessions vor Preset-Migration).
RULE_CHECKBOXES: list[tuple[str, str, bool]] = [
    ("rule_run_start_on_ball",                "Run startet erst mit Ball",       True),
    ("rule_dead_pokemon_unusable",            "Tote Pokemon nicht mehr nutzbar", True),
    ("rule_one_encounter_per_area",           "Ein Encounter pro Route",         True),
    ("rule_must_catch_first_encounter",       "Erster Encounter muss gefangen werden", True),
    ("rule_nickname_required",                "Nickname Pflicht",                True),
    ("rule_species_clause_cross_players",     "Species-Clause spielerübergreifend", False),
    ("rule_same_species_retry",               "Dupes-Clause (gleiche Species = Retry)", True),
    ("rule_token_rule",                       "Token-Regel",                     False),
    ("rule_shiny_clause_always_catchable",    "Shiny-Clause (immer fangbar)",    True),
    ("rule_restart_on_total_wipe",            "Neustart bei Total-Wipe",         False),
    ("rule_first_type_clause_linked",         "Ersttyp-Clause (verlinkt)",       False),
    ("rule_first_type_clause_static_exception", "Ersttyp: Static-Ausnahme",      False),
    ("rule_single_type_per_team",             "Ein Typ pro Team (Mono-Type)",    False),
]

CUSTOM_PRESET_LABEL = "(Custom)"
CUSTOM_PRESET_ID = "__custom__"


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
    def __init__(self, munchlax, configsave, nuz: dict, rem: dict | None = None,
                 bh: dict | None = None, bizhawk=None,
                 rnd: dict | None = None, **kwargs):
        super().__init__(**kwargs)
        self.name = "NuzlockeMenu"
        self.munchlax = munchlax
        self.configsave = configsave
        self.nuz = nuz
        self.rem = rem or {}
        self.bh = bh or {}
        self.rnd = rnd or {}
        self.bizhawk = bizhawk
        self._owner_rows: list[OwnerRow] = []
        self._presets = load_presets()
        self._preset_choices = preset_choices(self._presets)
        # Custom-Preset als virtuelle Option — wird gesetzt, sobald User eine
        # Regel-Checkbox aendert. Kein Eintrag in soullink_presets.yml.
        self._preset_choices.append((CUSTOM_PRESET_ID, CUSTOM_PRESET_LABEL))
        self._preset_label_to_id = {label: pid for pid, label in self._preset_choices}
        # Guard fuer _on_rule_checkbox_change — waehrend _refresh_rule_checkboxes
        # setzen wir CheckBox.active programmatisch und wollen nicht in den
        # Custom-Mode kippen. Wird via _suspend_rule_change() gesetzt.
        self._suspend_rule_change_events = False
        # Wird in _build_rule_checkboxes gefuellt: rule_key -> CheckBox-Widget.
        self._rule_checkboxes: dict[str, CheckBox] = {}

        # Screen-Root: FloatLayout statt BoxLayout — der Header wird als
        # Overlay VOR dem ScrollView platziert (last-added = touch-priority).
        # Frueher als vertikaler BoxLayout hatte der Header zwar visuell den
        # Top-Slot, aber der Zurueck-Button war nicht klickbar sobald der
        # aeussere ScrollView gescrollt war (nested-ScrollView-Touch-Grab
        # scheint Touch-Dispatch fuer Widgets oberhalb der ScrollView zu
        # stoeren). FloatLayout + last-added-Header umgeht das komplett.
        screen_root = FloatLayout()

        header_height_dp = 40
        # ScrollView fuellt gesamten Screen — Content bekommt oben Padding
        # in Header-Hoehe, damit die ersten Widgets nicht hinter dem Header
        # verschwinden. Kein separater "Nicht-Scroll"-Bereich noetig.
        scroll_all = ScrollView(size_hint=(1, 1), do_scroll_x=False,
                                  pos_hint={"x": 0, "y": 0})
        root = BoxLayout(orientation="vertical",
                          padding=("10dp", f"{header_height_dp + 10}dp", "10dp", "10dp"),
                          spacing="10dp",
                          size_hint_y=None)
        root.bind(minimum_height=root.setter("height"))
        scroll_all.add_widget(root)
        screen_root.add_widget(scroll_all)

        # Header als sticky Overlay oben — nach dem ScrollView adden, damit
        # er in der Touch-Dispatch-Reihenfolge (self.children iteriert vom
        # neuesten Kind an) VOR dem ScrollView drankommt und Touches im
        # Header-Bereich zuerst konsumieren darf.
        header = BoxLayout(orientation="horizontal",
                            size_hint=(1, None), height=f"{header_height_dp}dp",
                            padding=("10dp", "5dp"), spacing="10dp",
                            pos_hint={"top": 1, "x": 0})
        # Undurchsichtiger Hintergrund, damit gescrollter Content unterhalb
        # des Headers nicht durchblitzt. Farbe aus dem Kivy-Default-Grau.
        with header.canvas.before:
            Color(0.12, 0.12, 0.12, 1)
            header_bg = Rectangle(pos=header.pos, size=header.size)
        header.bind(pos=lambda inst, val: setattr(header_bg, 'pos', val),
                     size=lambda inst, val: setattr(header_bg, 'size', val))
        back_btn = Button(text="Zurück zum Hauptmenü", size_hint_x=0.3, on_press=self._go_back)
        header.add_widget(back_btn)
        header.add_widget(Label(text="Nuzlocke & Timer", font_size="20sp"))
        screen_root.add_widget(header)

        # Preset-Auswahl
        preset_row = BoxLayout(orientation="horizontal", size_hint_y=None,
                                height="40dp", spacing="6dp")
        preset_row.add_widget(Label(text="Regel-Preset:", size_hint_x=0.20))
        preset_labels = [label for _, label in self._preset_choices] or ["(keine)"]
        # Letzten gespeicherten Preset-Wert (nuz.soullink_preset_id) als Startwert
        # zeigen, damit der User nach App-Start / Session-Wechsel weiss welches
        # Preset zuletzt aktiv war. Fallback: erstes Label.
        initial_preset_id = str(self.nuz.get("soullink_preset_id", "") or "")
        initial_preset_label = preset_labels[0]
        for pid, label in self._preset_choices:
            if pid == initial_preset_id:
                initial_preset_label = label
                break
        self.preset_spinner = Spinner(text=initial_preset_label, values=preset_labels,
                                        size_hint_x=0.55)
        preset_row.add_widget(self.preset_spinner)
        self.preset_apply_button = Button(text="Preset anwenden", size_hint_x=0.25,
                                           on_press=lambda *_: self._apply_preset())
        preset_row.add_widget(self.preset_apply_button)
        root.add_widget(preset_row)

        # Regel-Checkboxen ausklappbar — Feineinstellung nach Preset-Wahl.
        # Jede Aenderung kippt den Preset-Spinner auf "(Custom)". Start
        # collapsed, damit die 13 Regeln den Screen nicht erschlagen.
        self.rules_toggle_button = Button(
            text=">  Regeln (Preset-Feineinstellung)",
            size_hint_y=None, height="30dp", halign="left",
        )
        self.rules_toggle_button.bind(
            size=lambda inst, val: setattr(inst, 'text_size', val)
        )
        self.rules_toggle_button.bind(
            on_press=lambda *_: self._toggle_rules_panel()
        )
        root.add_widget(self.rules_toggle_button)
        # Container fuer die Regel-Grid — wird bei Klick sichtbar/unsichtbar.
        # Kivy braucht Container-Widget im Baum damit Ausklapp-Position stimmt;
        # ein spaeteres add_widget nach Position wuerde die Reihenfolge relativ
        # zu host_hint_label/config_row brechen. Kein minimum_height-Bind —
        # sonst wuerde Container beim Collapse sofort wieder auf Grid-Hoehe
        # gezogen. Hoehe wird von _expand/_collapse_rules_panel manuell gesetzt.
        self.rules_container = BoxLayout(
            orientation="vertical", size_hint_y=None, height=0, spacing="4dp",
        )
        root.add_widget(self.rules_container)
        self._rules_expanded = False
        # Trotzdem einmal bauen, damit self._rule_checkboxes existiert (wird
        # von _refresh_rule_checkboxes / _sync_from_server_state / _apply_edit_lock
        # angesprochen — die duerfen nicht auf Ausklappen warten).
        self._build_rule_checkboxes(self.rules_container)
        # Direkt nach Build wieder collapsen (Widgets bleiben in self.ids-artigem
        # _rule_checkboxes-dict; Container-height=0 verbirgt sie visuell).
        self._collapse_rules_panel()

        # Hinweis-Label für Client-Only-Mode (siehe _apply_edit_lock). Bleibt
        # leer wenn User Server-Host ist.
        self.host_hint_label = Label(
            text="", size_hint_y=None, height="24dp",
            color=(1, 0.85, 0.2, 1),
        )
        root.add_widget(self.host_hint_label)

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
            values=["1", "2", "3", "4"],
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
        self.send_button = Button(text="Config an Server senden",
                                    on_press=lambda *_: self._send_config())
        actions.add_widget(self.send_button)
        self.fill_from_clients_button = Button(text="Aus Clients übernehmen",
                                    on_press=lambda *_: self._fill_owners_from_clients(overwrite=True))
        actions.add_widget(self.fill_from_clients_button)
        self.load_from_nuz_button = Button(text="Aus Session laden",
                                    on_press=lambda *_: self._load_from_nuz())
        actions.add_widget(self.load_from_nuz_button)
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
        # Config-Lock initial anwenden (Client-Only-Mode → alle Config-Widgets disabled)
        Clock.schedule_once(lambda dt: self._apply_edit_lock(), 0)

    def _build_rule_checkboxes(self, parent_box):
        """Baut 2-Spalten-Grid mit allen Regel-Checkboxen unter parent_box.

        GridLayout mit fester Zeilenhoehe, damit im aeusseren ScrollView
        keine Rechenkonflikte entstehen. Jede Zeile ist ein BoxLayout
        (Label + CheckBox), damit horizontales Spacing sauber greift.
        """
        rows = (len(RULE_CHECKBOXES) + 1) // 2
        grid = GridLayout(cols=2, size_hint_y=None,
                            height=(int(28) * rows), spacing="4dp")
        for rule_key, label_text, default in RULE_CHECKBOXES:
            cell = BoxLayout(orientation="horizontal", size_hint_y=None,
                              height="28dp", spacing="4dp")
            lbl = Label(text=label_text, size_hint_x=0.85, halign="left",
                         valign="middle")
            lbl.bind(size=lambda inst, val: setattr(inst, 'text_size', val))
            cell.add_widget(lbl)
            cb = CheckBox(size_hint_x=0.15, active=bool(self.nuz.get(rule_key, default)))
            cb.bind(active=lambda inst, val, k=rule_key: self._on_rule_checkbox_change(k, val))
            self._rule_checkboxes[rule_key] = cb
            cell.add_widget(cb)
            grid.add_widget(cell)
        parent_box.add_widget(grid)
        # Referenz merken, damit _toggle_rules_panel die Grid-Groesse an den
        # Container weiterreichen kann.
        self._rules_grid = grid

    def _toggle_rules_panel(self):
        if getattr(self, "_rules_expanded", False):
            self._collapse_rules_panel()
        else:
            self._expand_rules_panel()

    def _expand_rules_panel(self):
        # Grid komplett aus dem Container entfernen, wenn collapsed. opacity=0
        # + height=0 reicht nicht: Grid haengt weiter im Widget-Tree und die
        # CheckBoxes verschlucken Touches im Bereich anderer Widgets. Beim
        # Expand fuegen wir es zurueck und setzen die Container-Hoehe.
        if not hasattr(self, "_rules_grid"):
            return
        if self._rules_grid.parent is None:
            self.rules_container.add_widget(self._rules_grid)
        self.rules_container.height = self._rules_grid.height
        self.rules_toggle_button.text = "v  Regeln (Preset-Feineinstellung)"
        self._rules_expanded = True

    def _collapse_rules_panel(self):
        if not hasattr(self, "_rules_grid"):
            return
        if self._rules_grid.parent is not None:
            self.rules_container.remove_widget(self._rules_grid)
        self.rules_container.height = 0
        self.rules_toggle_button.text = ">  Regeln (Preset-Feineinstellung)"
        self._rules_expanded = False

    def _refresh_rule_checkboxes(self):
        """Setzt CheckBox-States aus self.nuz. Waehrend des Refresh keine
        Change-Events verarbeiten, sonst kippt Preset auf Custom obwohl
        wir gerade programmatisch aus einem Preset laden."""
        self._suspend_rule_change_events = True
        try:
            for rule_key, _label, default in RULE_CHECKBOXES:
                cb = self._rule_checkboxes.get(rule_key)
                if cb is None:
                    continue
                cb.active = bool(self.nuz.get(rule_key, default))
        finally:
            self._suspend_rule_change_events = False

    def _on_rule_checkbox_change(self, rule_key: str, active: bool):
        """User hat eine Regel-Checkbox umgeschaltet.

        - self.nuz[rule_key] setzen
        - Preset-Spinner auf Custom kippen (wenn nicht schon)
        - Custom-Preset-ID persistieren, damit nach Session-Wechsel Custom bleibt
        - Auto-Push an Server (analog _on_mode_changed-Muster)
        """
        if self._suspend_rule_change_events:
            return
        if not self._is_host():
            return
        self.nuz[rule_key] = bool(active)
        if self.preset_spinner.text != CUSTOM_PRESET_LABEL:
            self.preset_spinner.text = CUSTOM_PRESET_LABEL
        self.nuz["soullink_preset_id"] = CUSTOM_PRESET_ID
        self.status_label.text = f"Regel geaendert: {rule_key}={bool(active)} (Custom-Preset)"
        self._auto_push_config()

    def _is_host(self) -> bool:
        """True wenn User Server-Host ist (start_server=True in rem)."""
        return bool(self.rem.get("start_server", False))

    def _apply_edit_lock(self):
        """Sperrt Config-schreibende Widgets für Non-Host-Clients.

        Nur Server-Host darf soullink_config setzen — Broadcast geht sonst an
        alle verbundenen Munchlaxen und würde die Config anderer Spieler
        überschreiben (Last-Write-Wins-Race). Server bleibt permissiv; die
        Sperre ist reine UI-Klärung. Für zukünftigen Headless-Arceus sollte
        das durch einen expliziten Host-Handshake ersetzt werden.

        hasattr-Guards, weil _rebuild_owner_rows() bereits während __init__
        aufgerufen wird — zu dem Zeitpunkt existieren die Action-Buttons und
        das Hint-Label noch nicht. Der finale schedule_once(_apply_edit_lock)
        am Ende von __init__ setzt dann alle Widgets korrekt.
        """
        host = self._is_host()
        # Preset
        if hasattr(self, "preset_spinner"):
            self.preset_spinner.disabled = not host
        if hasattr(self, "preset_apply_button"):
            self.preset_apply_button.disabled = not host
        # Regel-Checkboxen
        for cb in getattr(self, "_rule_checkboxes", {}).values():
            cb.disabled = not host
        # Modus + Player-Count
        if hasattr(self, "mode_spinner"):
            self.mode_spinner.disabled = not host
        if hasattr(self, "player_count_spinner"):
            self.player_count_spinner.disabled = not host
        # Owner-Rows
        for row in self._owner_rows:
            row.owner_input.disabled = not host
            # Team-Spinner nur setzen wenn ohnehin für aktuellen Modus sichtbar,
            # sonst überschreibt Disable die Modus-abhängige Ausblendung nicht.
            if row.team_spinner.opacity > 0.5:
                row.team_spinner.disabled = not host
        # Action-Buttons
        if hasattr(self, "send_button"):
            self.send_button.disabled = not host
        if hasattr(self, "fill_from_clients_button"):
            self.fill_from_clients_button.disabled = not host
        if hasattr(self, "load_from_nuz_button"):
            self.load_from_nuz_button.disabled = not host
        # Info-Hinweis
        if hasattr(self, "host_hint_label"):
            if host:
                self.host_hint_label.text = ""
            else:
                self.host_hint_label.text = (
                    "Nur der Server-Host darf die Soullink-Config ändern. "
                    "Du siehst hier den aktuellen Server-Stand."
                )

    def on_pre_enter(self, *_):
        """Bei Screen-Wechsel Edit-Lock aktualisieren + Server-State spiegeln."""
        self._apply_edit_lock()
        if not self._is_host():
            self._sync_from_server_state()

    def on_enter(self, *_):
        """Client-Only: Poll-Interval starten, damit UI Server-Updates zeigt.

        Server-Host hat keine Poll — der aendert die Config selbst und pusht.
        Interval 1.0s reicht: Nutzer bemerken den Delay im Config-Menue nicht,
        und wir vermeiden Poll-Sturm auf grosse dicts.
        """
        if self._is_host():
            return
        if getattr(self, "_sync_interval_ev", None) is None:
            self._sync_interval_ev = Clock.schedule_interval(
                lambda dt: self._safe_sync_from_server_state(), 1.0
            )

    def _safe_sync_from_server_state(self):
        try:
            self._sync_from_server_state()
        except Exception as err:
            logger.warning(f"_sync_from_server_state (interval) failed: {err}")
            logger.warning(traceback.format_exc())

    def on_leave(self, *_):
        ev = getattr(self, "_sync_interval_ev", None)
        if ev is not None:
            ev.cancel()
            self._sync_interval_ev = None

    def _sync_from_server_state(self):
        """Spiegelt munchlax.soullink_config in die UI (Client-Only-Ansicht)."""
        cfg = getattr(self.munchlax, "soullink_config", {}) or {}
        try:
            mode_val = normalize_mode(cfg.get("mode"))
            self.mode_spinner.text = MODE_LABELS[mode_val]
            count = cfg.get("player_count", 2) or 2
            self.player_count_spinner.text = str(max(1, min(4, int(count))))
        except Exception as err:
            logger.warning(f"_sync_from_server_state Mode/Count: {err}")
        # Owner-Rows aus expected_owners neu bauen (überschreibt bestehende
        # Text-Inputs, da wir hier explizit den Server-State zeigen wollen).
        expected = cfg.get("expected_owners", []) or []
        team_map = cfg.get("team_membership", {}) or {}
        for i, row in enumerate(self._owner_rows):
            if i < len(expected):
                row.owner_input.text = expected[i]
                tm = team_map.get(expected[i], "")
                if tm in TEAM_LETTERS:
                    row.team_spinner.text = tm
            else:
                row.owner_input.text = ""
        # Regel-Checkboxen aus server-cfg.rules spiegeln — Client sieht damit
        # den aktuellen Regel-Stand, den der Host per Preset/Custom pusht.
        server_rules = cfg.get("rules") or {}
        if isinstance(server_rules, dict) and server_rules:
            self._suspend_rule_change_events = True
            try:
                for rule_key, _label, default in RULE_CHECKBOXES:
                    cb = self._rule_checkboxes.get(rule_key)
                    if cb is None:
                        continue
                    cb.active = bool(server_rules.get(rule_key, default))
            finally:
                self._suspend_rule_change_events = False
        self._apply_edit_lock()

    def _go_back(self, *_):
        try:
            self.manager.current = "MainMenu"
        except Exception as err:
            logger.warning(f"NuzlockeMenu._go_back failed: {err}")

    def _rebuild_owner_rows(self):
        try:
            count = int(self.player_count_spinner.text)
        except (ValueError, TypeError):
            count = 2
        count = max(1, min(4, count))
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

        # Nach Rebuild leere Rows aus verbundenen Clients vorbefüllen. Manuell
        # gesetzte Namen bleiben unangetastet (overwrite=False). Nur Host, sonst
        # würde der Auto-Push die Server-Config aus einem Client heraus setzen.
        if self._is_host():
            self._fill_owners_from_clients(overwrite=False)
        # Neue Rows müssen den aktuellen Edit-Lock-Zustand bekommen (im
        # Client-Only-Mode sind alle Owner-Text-Inputs disabled).
        self._apply_edit_lock()

    def _connected_owner_names(self) -> list[str]:
        """Distinct Client-Namen sortiert nach player_id (Netz-Slot).

        Primaere Quelle: munchlax.player_names (dict[player_id -> client_name]) —
        wird sowohl aus declared_player_ids (sofort beim Connect, ohne BizHawk)
        als auch aus BizHawk-/Citra-Handshake gefuellt. Sortierung nach player_id
        macht die Reihenfolge deterministisch: Spieler 1 (Slot 1) zuerst,
        unabhaengig von Verbindungsreihenfolge oder Alphabet.

        Fallback: client_names (dict[client_id -> client_name]) fuer Clients
        deren declared_player_ids noch nicht angekommen sind — dict-Insertion-
        Order gilt (Verbindungsreihenfolge).

        Eigener Name (pl.your_name) wird ergaenzt, damit lokal-hostende Setups
        den Owner auch ohne Server-Roundtrip haben; landet am Ende, falls er
        nicht schon durch player_names/client_names aufgetaucht ist.
        """
        ordered: list[str] = []
        seen: set[str] = set()

        def _add(raw):
            if not isinstance(raw, str):
                return
            n = raw.strip()
            if n and n not in seen:
                seen.add(n)
                ordered.append(n)

        try:
            player_names = getattr(self.munchlax, "player_names", {}) or {}
            # Keys koennen int oder str sein (BizHawk sendet int, Pickle-Roundtrip
            # behaelt Typ; player_names-Broadcast baut dict[int, str]).
            def _pid_key(pid):
                try:
                    return int(pid)
                except (TypeError, ValueError):
                    return 10**9
            for pid in sorted(player_names.keys(), key=_pid_key):
                _add(player_names.get(pid))
        except Exception:
            pass
        try:
            for name in (getattr(self.munchlax, "client_names", {}) or {}).values():
                _add(name)
        except Exception:
            pass
        try:
            own = ((getattr(self.munchlax, "pl", {}) or {}).get("your_name") or "")
            _add(own)
        except Exception:
            pass
        return ordered

    def _fill_owners_from_clients(self, overwrite: bool = False):
        """Befüllt Owner-Text-Felder aus verbundenen Clients.

        overwrite=False: nur leere Rows. overwrite=True: alle Rows.
        Doppelte Namen werden übersprungen, sonst tauchen sie doppelt auf.
        Kein Effekt im Client-Only-Mode (Config-Schreiben nur für Host).
        """
        if not self._is_host():
            return
        candidates = self._connected_owner_names()
        if not candidates:
            return
        # Bereits gesetzte Namen aus dem Kandidatenpool ziehen, damit wir keine
        # Duplikate erzeugen wenn nur leere Rows befüllt werden.
        used: set[str] = set()
        if not overwrite:
            for row in self._owner_rows:
                n = row.owner
                if n:
                    used.add(n)
        available = [n for n in candidates if n not in used]
        changed = 0
        for row in self._owner_rows:
            if not available:
                break
            if row.owner and not overwrite:
                continue
            row.owner_input.text = available.pop(0)
            changed += 1
        if changed:
            logger.info(f"NuzlockeMenu: {changed} Owner-Feld(er) aus verbundenen Clients befüllt "
                        f"(overwrite={overwrite}, kandidaten={candidates})")
            self._auto_push_config()

    def _on_mode_changed(self, spinner, value):
        show_team = (MODE_LABEL_TO_VALUE.get(value, value) == "versus")
        for row in self._owner_rows:
            row.set_team_visible(show_team)
        self._auto_push_config()

    def _mode_value(self) -> str:
        return MODE_LABEL_TO_VALUE.get(self.mode_spinner.text, "nuzlocke")

    def _on_player_count_changed(self, spinner, value):
        self._rebuild_owner_rows()
        self._auto_push_config()

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
        if not self._is_host():
            self.status_label.text = "Config-Änderungen nur für den Server-Host erlaubt."
            logger.info("NuzlockeMenu._send_config: skipped — kein Host")
            return
        cfg = self._collect_config()
        try:
            asyncio.create_task(self.munchlax.send_soullink_config(cfg))
            self.status_label.text = f"Config gesendet: mode={cfg['mode']}, {len(cfg['expected_owners'])} Spieler"
            logger.info(f"Soullink-Config gesendet: {cfg}")
        except Exception as err:
            logger.error(f"send_soullink_config failed: {type(err)},{err}")
            logger.error(f"{traceback.format_exc()}")
            self.status_label.text = f"Fehler: {err}"

    def _auto_push_config(self):
        """Auto-Push nach struktureller Aenderung (Preset/Mode/Count/Save).
        Silent-No-Op wenn Munchlax nicht connected — send_soullink_config
        returned dann eh sofort. Owner-Text-Aenderungen loesen KEIN Auto-Push
        (waere pro Tastendruck) — der 'Config an Server senden'-Button bleibt
        dafuer.

        Zusätzlich: nur Host darf pushen — sonst würde ein Client-Ereignis
        (Screen-Öffnen, Modus-Anzeige-Sync etc.) den Server umkonfigurieren.
        """
        if not self._is_host():
            return
        if not getattr(self.munchlax, "is_connected", False):
            return
        try:
            self._send_config()
        except Exception as err:
            logger.warning(f"auto-push config failed: {err}")

    def _load_from_nuz(self):
        mode_value = normalize_mode(self.nuz.get("soullink_mode"))
        self.mode_spinner.text = MODE_LABELS[mode_value]
        self.player_count_spinner.text = str(self.nuz.get("soullink_player_count", 2) or 2)
        # Preset-Spinner auf zuletzt gespeicherte ID setzen (rein visuell,
        # kein Auto-Apply). Bei unbekannter/leerer ID nichts aendern.
        preset_id = str(self.nuz.get("soullink_preset_id", "") or "")
        if preset_id:
            for pid, label in self._preset_choices:
                if pid == preset_id:
                    self.preset_spinner.text = label
                    break
        self._refresh_rule_checkboxes()
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
                                 bh_config=self.bh, munchlax=self.munchlax,
                                 rnd=self.rnd)

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
        handle = show_pending_toast("SaveRAM wird geflusht", level='info')
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
            handle.update("Snapshot wird erstellt")
            self._set_status("Snapshot wird erstellt...")
            await self._create_snapshot_async(label, handle)
        finally:
            def _reenable(_dt):
                self.snapshot_create_button.disabled = False
            Clock.schedule_once(_reenable, 0)

    async def _create_snapshot_async(self, label: str, handle=None):
        try:
            sm = self._snapshot_manager()
            loop = asyncio.get_event_loop()
            snap_id = await loop.run_in_executor(None, sm.create, label)
        except Exception as err:
            logger.error(f"snapshot create failed: {type(err)},{err}")
            logger.error(f"{traceback.format_exc()}")
            self._set_status(f"Snapshot-Fehler: {err}")
            if handle is not None:
                handle.finish(f"Snapshot-Fehler: {err}", level='error', duration=3.0)
            return
        if snap_id:
            self._set_status(f"Snapshot erstellt: {snap_id}")
            if handle is not None:
                handle.finish(f"Snapshot erstellt: {snap_id}", level='success')
            self._refresh_snapshot_list()
        else:
            self._set_status("Snapshot-Erstellung fehlgeschlagen")
            if handle is not None:
                handle.finish("Snapshot-Erstellung fehlgeschlagen", level='error', duration=3.0)

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
        handle = show_pending_toast(f"Snapshot {snap_id} wird geladen", level='info')
        asyncio.create_task(self._restore_snapshot_async(snap_id, handle))

    async def _restore_snapshot_async(self, snap_id: str, handle):
        try:
            self.munchlax.close_pokedex_db()
            sm = self._snapshot_manager()
            loop = asyncio.get_event_loop()
            ok = await loop.run_in_executor(None, sm.restore, snap_id)
        except Exception as err:
            logger.error(f"snapshot restore failed: {type(err)},{err}")
            logger.error(f"{traceback.format_exc()}")
            self._set_status(f"Restore-Fehler: {err}")
            handle.finish(f"Restore-Fehler: {err}", level='error', duration=3.0)
            return
        if ok:
            self._set_status(f"Snapshot geladen: {snap_id}")
            handle.finish(f"Snapshot {snap_id} geladen", level='success')
        else:
            self._set_status(f"Restore fehlgeschlagen: {snap_id}")
            handle.finish(f"Restore fehlgeschlagen: {snap_id}", level='error', duration=3.0)

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
            show_toast(f"Delete-Fehler: {err}", level='error', duration=3.0)
            return
        self._refresh_snapshot_list()
        self._set_status(f"Snapshot gelöscht: {snap_id}")
        show_toast(f"Snapshot {snap_id} gelöscht", level='success')

    def _apply_preset(self):
        if not self._is_host():
            self.status_label.text = "Preset-Anwendung nur für den Server-Host erlaubt."
            return
        pid = self._preset_label_to_id.get(self.preset_spinner.text)
        if pid == CUSTOM_PRESET_ID:
            self.status_label.text = "Custom-Preset: nichts anzuwenden (Regeln direkt setzen)"
            return
        if pid is None or pid not in self._presets:
            self.status_label.text = "Preset nicht gefunden"
            return
        preset = self._presets[pid]
        apply_preset(self.nuz, preset)
        # Preset-ID merken, damit der Spinner nach App-Neustart / Session-Wechsel
        # wieder auf diese Auswahl steht. Persistiert wird erst durch
        # _save_to_nuz — hier nur in-memory, konsistent mit apply_preset.
        self.nuz["soullink_preset_id"] = pid
        self._load_from_nuz()
        self.status_label.text = f"Preset '{preset.get('label', pid)}' angewendet (noch nicht gespeichert)"
        self._auto_push_config()

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
        # Nach Save auch pushen — Owner-Texte sind persistiert, Server bekommt
        # sauberen Snapshot inkl. moeglicher Owner-Aenderungen die _on_mode/
        # _on_player_count nicht mitgekriegt haben (die pushen ohne Owner-Save).
        self._auto_push_config()
