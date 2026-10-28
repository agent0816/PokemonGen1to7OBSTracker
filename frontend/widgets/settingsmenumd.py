"""KivyMD-Einstellungs-Screen (Phase D).

5 thematische Tabs (Verbindung, Sprites, Spieler, Randomizer, Logging) ueber eine
eigene Button-Leiste + manuellen Content-Tausch (KEIN MDTabsCarousel — der frisst
Touches interaktiver Kinder). Die gesamte Lade-/Speicher-/Restart-Logik liegt
in backend.controller.SettingsController; dieser Screen baut nur die Oberflaeche
und reicht Werte durch. Reine Overlay-Link-/Team-Logik: backend.settings_view.

Wichtige Muster (siehe Skill kivymd-screen-migration):
- MDSwitch/Dropdown setzen in init_config/load programmatisch Werte -> Save-Loop
  vermeiden via _suspend_save-Guard.
- init_config im __init__ UND in load_from_session.
- Keine RecycleView — Listen als MDScrollView + MDBoxLayout.
- file_picker/sprite_setup in MD-Varianten (file_picker_md, sprite_setup_popupmd).
"""
from __future__ import annotations

import asyncio
import os
import traceback
from pathlib import Path
from kivy.core.clipboard import Clipboard
from kivy.metrics import dp

from kivymd.app import MDApp
from kivymd.uix.boxlayout import MDBoxLayout
from kivymd.uix.button import MDButton, MDButtonText, MDIconButton
from kivymd.uix.divider import MDDivider
from kivymd.uix.label import MDLabel
from kivymd.uix.screen import MDScreen
from kivymd.uix.scrollview import MDScrollView
from kivymd.uix.segmentedbutton import (MDSegmentedButton, MDSegmentedButtonItem,
                                        MDSegmentButtonLabel)
from kivymd.uix.selectioncontrol import MDSwitch
from kivymd.uix.textfield import MDTextField

from backend import app_state
from backend import settings_view as sv
from backend.controller.settings_controller import SettingsController
from backend.controller.randomizer_controller import RandomizerController
from backend.rando_sync import apply_rando_to_munchlax
from backend.logging_setup import (get_logger, get_console_level, set_console_level,
                                    list_known_modules, get_module_level, set_module_level)
from backend.dev_mode import is_developer
from backend.perf_probe import perf_log, perf_span
from frontend.perf_hooks import count_widgets
from frontend.widgets.md_common import show_snackbar, confirm_dialog
from frontend.widgets.settings_widgets import (LABEL_W, DropdownField, browse_row,
                                               labeled_row, make_textfield)
from frontend.widgets.file_picker_md import pick_file, pick_directory
from frontend.widgets.sprite_setup_popupmd import SpriteSetupPopupMD

logger = get_logger(__name__, 'logs/frontend.log')

LOG_LEVELS = ('DEBUG', 'INFO', 'WARNING', 'ERROR')
LAYOUTS = ('horizontal', 'vertical', '2x3', '3x2')
BADGE_LAYOUTS = ('horizontal', 'vertical', '2x4', '4x2', '4x4', '2x4-center', '4x2-center', '4x4-center')
ANIM_DURATIONS = [str(v) for v in range(300, 801, 100)]

# Hinweis: Der Citra-Zwang pro Spieler (Klassik check_player_for_citra) wird
# bewusst NICHT portiert — die Pro-Spieler-Config wird konzeptionell neu gemacht
# (Memory project_pro_spieler_config_konzept). Der Spieler-Tab zeigt die Toggles
# nur als Platzhalter.


# Sektions-Container-Hintergrund (#211F26, wie MainMenu CARD_BG). Bewusst
# MDBoxLayout statt MDCard: MDCard hat StateLayerBehavior und tönt beim Hover
# die ganze Karte — hier unerwünscht (Skill kivymd-screen-migration).
CARD_BG = (0.129, 0.122, 0.149, 1)


def _section_card(title: str) -> MDBoxLayout:
    card = MDBoxLayout(orientation="vertical", padding=dp(14),
                       spacing=dp(12), size_hint_y=None, adaptive_height=True,
                       radius=[dp(16)] * 4, md_bg_color=CARD_BG)
    lbl = MDLabel(text=title, role="small", bold=True, adaptive_height=True)
    lbl.theme_text_color = "Custom"
    lbl.text_color = MDApp.get_running_app().theme_cls.primaryColor
    card.add_widget(lbl)
    return card


class SettingsMenu(MDScreen):
    def __init__(self, arceus, bizhawk, munchlax, obs_websocket, overlay_server,
                 externalIPv4, externalIPv6, configsave, sp, rem, obs, bh, pl,
                 rnd, ov, nuz, app_version, **kwargs):
        super().__init__(**kwargs)
        self.name = "SettingsMenu"

        self.arceus = arceus
        self.bizhawk = bizhawk
        self.munchlax = munchlax
        self.obs_websocket = obs_websocket
        self.overlay_server = overlay_server
        self.externalIPv4 = externalIPv4
        self.externalIPv6 = externalIPv6
        self.configsave = configsave
        self.sp = sp
        self.rem = rem
        self.obs = obs
        self.bh = bh
        self.pl = pl
        self.rnd = rnd
        self.ov = ov
        self.nuz = nuz
        self.app_version = app_version

        self._suspend_save = False
        self._tasks: set = set()
        # Zuletzt gespeicherter Stand je Sektion — verhindert redundante
        # controller.save_* (und damit redundante redraw_obs/Server-Reconfigure)
        # bei Fokus-Verlust ohne Änderung bzw. Dropdown-Reopen mit gleichem Wert.
        # Klassik bündelt per Button; MD speichert pro Feld, daher Change-Guard.
        self._last_saved: dict[str, dict] = {}
        # Session-Wechsel bei unsichtbarem Screen: init_config wird erst im
        # nächsten on_pre_enter nachgeholt (spart 0,3–1,5 s im Session-Klick).
        # Solange True zeigen die Widgets Werte der vorigen Session → _save sperrt.
        self._config_dirty = False
        # True, während init_config die Sprite-Switches programmatisch setzt
        self._loading_switches = False
        # (runs_root, list_runs-Ergebnis) der aktuell angezeigten Run-Zeilen
        self._run_rows_key: tuple | None = None
        # Per-Game-/2-PC-Ausklapp-Zustand (ersetzt ToggleButton-States der Klassik)
        self._game_sprites_expanded = False      # lokale Per-Game-Sprites an (→ single_path_check)
        self._obs_sprites_expanded = False        # 2-PC-Setup an (→ obs_2_pc)
        # Per-Game-Sprites auf dem OBS-PC — UNABHÄNGIG vom lokalen Per-Game-Schalter
        # auswählbar (User-Feedback 2026-10-19). Kein eigener Config-Key: beim Laden
        # aus dem Vorhandensein von <game>_obs-Werten abgeleitet.
        self._obs_game_sprites_expanded = False
        # Solange False bleibt obs_single_path_check=None (gekoppelt an das lokale
        # single_path_check). Erst ein expliziter OBS-Per-Game-Toggle schreibt den
        # Key und entkoppelt — ein reiner lokaler Save lässt die Kopplung bestehen.
        self._obs_flag_explicit = False
        # Reine Sicht-Zustände der langen Spiel-Listen (einklappen, aus dem Weg).
        self._game_list_collapsed = False
        self._obs_game_list_collapsed = False
        self._game_fields: dict[str, MDTextField] = {}
        self._obs_game_fields: dict[str, MDTextField] = {}
        self._player_switches: dict[str, MDSwitch] = {}
        # Re-Render-Closures der öffentlichen-IP-Zeilen. Die IPs werden in der
        # KivyMD-UI erst asynchron nach dem Start geladen (Splash-Boot), daher
        # müssen die schon gebauten Zeilen nachträglich aktualisierbar sein.
        self._public_ip_refreshers: list = []

        _app = MDApp.get_running_app()
        _bh_thread = getattr(_app, 'bizhawk_thread', None)
        self.controller = SettingsController(configsave, sp, rem, obs, bh, pl, rnd,
                                             arceus, bizhawk, munchlax, obs_websocket,
                                             ov, overlay_server, nuz, bizhawk_thread=_bh_thread)
        self.randomizer = RandomizerController(rnd, pl, configsave=configsave)

        self.games = {
            'Rot und Blau': 'gen1_red', 'Gelb': 'gen1_yellow',
            'Silber': 'gen2_silver', 'Gold': 'gen2_gold', 'Kristall': 'gen2_crystal',
            'Rubin und Saphir': 'gen3_ruby', 'Smaragd': 'gen3_emerald', 'Feuerrot und Blattgrün': 'gen3_firered',
            'Diamant und Perl': 'gen4_diamond', 'Platin': 'gen4_platinum', 'Herzgold und Seelensilber': 'gen4_heartgold',
            'Schwarz und Weiß (2)': 'gen5_black',
            'X und Y': 'gen6_x', 'Alpha Saphir und Omega Rubin': 'gen6_alphasapphire',
            'Sonne und Mond': 'gen7_sun', 'Ultra Sonne und Ultra Mond': 'gen7_usun',
        }

        self._build_ui()
        # init_config MUSS im __init__ laufen (nicht nur im Session-Hook), sonst
        # stehen Switches/Dropdowns auf Defaults und der erste Toggle schreibt
        # falsche Werte in die Config.
        self.init_config()

    # ---------------------------------------------------------------- UI-Aufbau
    def _build_ui(self) -> None:
        root = MDBoxLayout(orientation="vertical")

        head = MDBoxLayout(orientation="horizontal", size_hint_y=None, height=dp(44),
                           padding=[dp(16), 0], spacing=dp(12))
        head.add_widget(MDLabel(text="Einstellungen", role="large", bold=True,
                                valign="center"))
        self._session_label = MDLabel(text="", role="small", halign="right",
                                      valign="center")
        head.add_widget(self._session_label)
        root.add_widget(head)

        # Eigene Tab-LEISTE aus MDButtons statt MDTabsPrimary+MDTabsCarousel:
        # Der Carousel ist ein Swipe-Widget (on_touch_move) und fängt Touches für
        # interaktive Kinder (Buttons/Switches/Dropdowns) ab; MDTabsPrimary ohne
        # Carousel ist layout-fragil (ids.container-Timing). Eigene Buttons geben
        # volle Touch-Kontrolle. Content wird manuell in _content_holder getauscht.
        self._panes: dict[str, MDScrollView] = {}
        self._tab_buttons: dict[str, MDButton] = {}
        tab_bar = MDBoxLayout(orientation="horizontal", size_hint=(None, None),
                              height=dp(48), adaptive_width=True, spacing=dp(4),
                              padding=[dp(8), 0])
        for name, builder in (
            ("Verbindung", self._build_connection_tab),
            ("Sprites", self._build_sprites_tab),
            ("Spieler", self._build_player_tab),
            ("Randomizer", self._build_randomizer_tab),
            ("Logging", self._build_logging_tab),
        ):
            btn = MDButton(MDButtonText(text=name), style="text",
                           on_release=lambda *_a, n=name: self.select_tab(n))
            self._tab_buttons[name] = btn
            tab_bar.add_widget(btn)
            self._panes[name] = self._scroll(builder())
        # Bei wenig Breite horizontal scrollbar statt Überlauf.
        tab_scroll = MDScrollView(size_hint_y=None, height=dp(48), do_scroll_y=False)
        tab_scroll.add_widget(tab_bar)

        self._content_holder = MDBoxLayout(size_hint=(1, 1))

        root.add_widget(tab_scroll)
        root.add_widget(MDDivider())
        root.add_widget(self._content_holder)
        self.add_widget(root)

        self.select_tab("Verbindung")

    def select_tab(self, name: str) -> None:
        """Tab wählen: Content tauschen + aktiven Button hervorheben."""
        pane = self._panes.get(name)
        if pane is None:
            return
        self._content_holder.clear_widgets()
        self._content_holder.add_widget(pane)
        for n, btn in self._tab_buttons.items():
            btn.style = "tonal" if n == name else "text"
        if name == "Randomizer":
            self._refresh_run_history()

    def _scroll(self, content: MDBoxLayout) -> MDScrollView:
        # Nur vertikal scrollen — horizontales Scrollen der Tab-Inhalte abschalten
        # (User-Feedback 2026-10-19). Die Felder sind size_hint-basiert und passen
        # sich der Breite an.
        sc = MDScrollView(size_hint=(1, 1), do_scroll_x=False)
        content.padding = [dp(16), dp(12), dp(16), dp(40)]
        sc.add_widget(content)
        return sc

    def _pane(self) -> MDBoxLayout:
        box = MDBoxLayout(orientation="vertical", size_hint_y=None,
                          adaptive_height=True, spacing=dp(16))
        return box

    # ------------------------------------------------------------ Verbindung
    def _build_connection_tab(self) -> MDBoxLayout:
        pane = self._pane()

        # Bizhawk
        card = _section_card("Bizhawk")
        self._bizhawk_exe = make_textfield("Pfad der EmuHawk.exe",
                                           on_validate=lambda: self._save("bizhawk"))
        card.add_widget(browse_row("EmuHawk.exe", self._bizhawk_exe,
                                   lambda f: self._browse(f, "file")))
        self._bizhawk_port = make_textfield("Port", is_port=True,
                                            on_validate=lambda: self._save("bizhawk"))
        card.add_widget(labeled_row("Port", self._bizhawk_port))
        pane.add_widget(card)

        # OBS Websocket
        card = _section_card("OBS Websocket")
        self._obs_host = make_textfield("IP-Adresse", on_validate=lambda: self._save("obs"))
        card.add_widget(labeled_row("IP-Adresse", self._obs_host))
        self._obs_port = make_textfield("Port", is_port=True, on_validate=lambda: self._save("obs"))
        card.add_widget(labeled_row("Port", self._obs_port))
        self._obs_password = make_textfield("Passwort", password=True,
                                            on_validate=lambda: self._save("obs"))
        card.add_widget(self._reveal_row("Passwort", self._obs_password))
        pane.add_widget(card)

        # Remote
        card = _section_card("Remote")
        card.add_widget(MDLabel(text="Server-Einstellungen", role="small",
                                adaptive_height=True))
        self._port_client = make_textfield("Host-Port", is_port=True,
                                           on_validate=lambda: self._save("remote"))
        card.add_widget(labeled_row("Host-Port", self._port_client))
        self._helper_port = make_textfield("Helper-Port (OBS-PC)", is_port=True,
                                           on_validate=lambda: self._save("remote"))
        card.add_widget(labeled_row("Helper-Port", self._helper_port))
        card.add_widget(MDLabel(text="Client-Einstellungen", role="small",
                                adaptive_height=True))
        # Ziel-Server-IP maskiert (potenziell öffentliche IP — Stream-Leak-Schutz)
        self._ip_server = make_textfield("Ziel-Server IP-Adresse", password=True,
                                         on_validate=lambda: self._save("remote"))
        card.add_widget(self._reveal_row("Ziel-Server IP", self._ip_server))
        self._port_server = make_textfield("Port", is_port=True,
                                           on_validate=lambda: self._save("remote"))
        card.add_widget(labeled_row("Port", self._port_server))
        card.add_widget(self._public_ip_row("Deine öffentliche IPv4", "externalIPv4"))
        card.add_widget(self._public_ip_row("Deine öffentliche IPv6", "externalIPv6"))
        pane.add_widget(card)

        # Overlay
        card = _section_card("Browser Overlay")
        self._overlay_port = make_textfield("Port", is_port=True,
                                            on_validate=lambda: (self._save("overlay"),
                                                                 self._rebuild_overlay_links()))
        card.add_widget(labeled_row("Port", self._overlay_port))
        self._layout_dd = DropdownField(LAYOUTS, self.ov.get('layout', 'horizontal'),
                                        lambda v: self._on_overlay_option())
        card.add_widget(labeled_row("Team-Layout", self._layout_dd))
        self._badge_dd = DropdownField(BADGE_LAYOUTS, self.ov.get('badge_layout', 'horizontal'),
                                       lambda v: self._on_overlay_option())
        card.add_widget(labeled_row("Badge-Layout", self._badge_dd))
        self._anim_dd = DropdownField(ANIM_DURATIONS, str(self.ov.get('animation_duration_ms', 300)),
                                      lambda v: self._on_overlay_option())
        card.add_widget(labeled_row("Animationsdauer (ms)", self._anim_dd))
        self._overlay_links_box = MDBoxLayout(orientation="vertical", size_hint_y=None,
                                              adaptive_height=True, spacing=dp(8))
        card.add_widget(self._overlay_links_box)
        pane.add_widget(card)
        return pane

    def _reveal_row(self, label_text: str, field: MDTextField) -> MDBoxLayout:
        """Zeile mit maskiertem Eingabefeld + Auge-Button zum Aufdecken."""
        row = MDBoxLayout(orientation="horizontal", size_hint_y=None,
                          adaptive_height=True, spacing=dp(8))
        row.add_widget(MDLabel(text=label_text, size_hint_x=None, width=LABEL_W,
                               role="small", valign="center"))
        row.add_widget(field)

        def _toggle(_btn):
            field.password = not field.password

        row.add_widget(MDIconButton(icon="eye-outline", on_release=_toggle))
        return row

    def _public_ip_row(self, label_text: str, attr_name: str) -> MDBoxLayout:
        """Nur-Lese-Anzeige einer maskierten öffentlichen IP mit Aufdecken + Kopieren.

        Liest den Wert live über `attr_name` (z.B. "externalIPv4") statt ihn
        beim Bauen einzufrieren — die IPs treffen in der MD-UI erst nach dem
        Start asynchron ein und werden über `set_public_ips` nachgezogen.
        """
        row = MDBoxLayout(orientation="horizontal", size_hint_y=None,
                          adaptive_height=True, spacing=dp(8))
        row.add_widget(MDLabel(text=label_text, size_hint_x=None, width=LABEL_W,
                               role="small", valign="center"))

        def _current() -> str:
            return getattr(self, attr_name, "") or ""

        shown = MDLabel(text=sv.masked_placeholder(_current()), size_hint_x=1,
                        role="small", valign="center")
        state = {"revealed": False}

        def _render(*_args):
            val = _current()
            shown.text = (val or "—") if state["revealed"] else sv.masked_placeholder(val)

        def _toggle(_btn):
            state["revealed"] = not state["revealed"]
            _render()

        def _copy(_btn):
            val = _current()
            if val:
                # Clipboard bekommt die echte IP (zum Einfügen), die Snackbar aber
                # nur das, was gerade sichtbar ist — verdeckt bei zugedeckter IP,
                # Klartext nur wenn aufgedeckt (Stream-Leak-Schutz, Feedback 2026-10-19).
                Clipboard.copy(val)
                shown_val = val if state["revealed"] else sv.masked_placeholder(val)
                show_snackbar(f"Kopiert: {shown_val}", level='success')

        row.add_widget(shown)
        row.add_widget(MDIconButton(icon="eye-outline", on_release=_toggle))
        row.add_widget(MDIconButton(icon="content-copy", on_release=_copy))
        self._public_ip_refreshers.append(_render)
        return row

    def set_public_ips(self, ipv4: str, ipv6: str) -> None:
        """Asynchron nachgeladene öffentliche IPs übernehmen + Anzeige auffrischen.
        Von `appmd.Screens._boot_sequence` nach `load_external_ips_async` aufgerufen."""
        self.externalIPv4 = ipv4 or ""
        self.externalIPv6 = ipv6 or ""
        for refresh in self._public_ip_refreshers:
            try:
                refresh()
            except Exception as err:
                logger.error(f"IP-Zeile konnte nicht aktualisiert werden: {err}")

    def _on_overlay_option(self) -> None:
        if self._suspend_save:
            return
        self._save("overlay")
        self._rebuild_overlay_links()

    @perf_span("settings._rebuild_overlay_links")
    def _rebuild_overlay_links(self) -> None:
        box = self._overlay_links_box
        box.clear_widgets()
        try:
            data = sv.build_overlay_links(
                port=self._overlay_port.text or self.ov.get('port', '43888'),
                layout=self._layout_dd.value, badge_layout=self._badge_dd.value,
                player_count=int(self.pl.get('player_count', 1) or 1),
                two_pc=bool(self._obs_sprites_expanded),
                nuz=self.nuz, pl=self.pl, rem=self.rem,
            )
        except Exception as err:
            logger.error(f"build_overlay_links fehlgeschlagen: {err}")
            logger.error(traceback.format_exc())
            return
        box.add_widget(MDLabel(text=data["hint"], role="small", adaptive_height=True))
        for player in data["players"]:
            row = MDBoxLayout(orientation="horizontal", size_hint_y=None,
                              adaptive_height=True, spacing=dp(6))
            row.add_widget(MDLabel(text=player["label"], size_hint_x=0.25,
                                   role="small", valign="center"))
            for link in player["links"]:
                row.add_widget(self._copy_button(f"Pokemon ({link['host_label']})", link["team_url"]))
                row.add_widget(self._copy_button(f"Badges ({link['host_label']})", link["badge_url"]))
            box.add_widget(row)
        if data["teams"]:
            box.add_widget(MDLabel(text="Team-Overlays", role="small", bold=True,
                                   adaptive_height=True))
            for team in data["teams"]:
                row = MDBoxLayout(orientation="horizontal", size_hint_y=None,
                                  adaptive_height=True, spacing=dp(6))
                row.add_widget(MDLabel(text=team["label"], size_hint_x=0.25,
                                       role="small", valign="center"))
                for link in team["links"]:
                    row.add_widget(self._copy_button(f"Team-Badges ({link['host_label']})",
                                                     link["badge_url"]))
                box.add_widget(row)

    def _copy_button(self, text: str, url: str) -> MDButton:
        return MDButton(MDButtonText(text=text), style="outlined",
                        on_release=lambda *_a: self._copy_overlay_url(url))

    def _copy_overlay_url(self, url: str) -> None:
        Clipboard.copy(url)
        show_snackbar(f"Link kopiert: {url}", level='success')

    # --------------------------------------------------------------- Sprites
    def _build_sprites_tab(self) -> MDBoxLayout:
        pane = self._pane()

        card = _section_card("Sprites")
        self._common_path = make_textfield("Dateipfad Sprites",
                                           on_validate=lambda: self._save("sprites"))
        card.add_widget(browse_row("Sprites", self._common_path, lambda f: self._browse(f, "dir")))
        self._items_path = make_textfield("Dateipfad Items",
                                          on_validate=lambda: self._save("sprites"))
        card.add_widget(browse_row("Items", self._items_path, lambda f: self._browse(f, "dir")))
        self._badges_path = make_textfield("Dateipfad Orden",
                                           on_validate=lambda: self._save("sprites"))
        card.add_widget(browse_row("Orden", self._badges_path, lambda f: self._browse(f, "dir")))
        btn_row = MDBoxLayout(orientation="horizontal", size_hint_y=None,
                              adaptive_height=True, spacing=dp(10))
        btn_row.add_widget(MDButton(MDButtonText(text="Sprites herunterladen"),
                                    style="filled", on_release=lambda *_a: self._download_sprites()))
        btn_row.add_widget(MDButton(MDButtonText(text="Sprites aktualisieren"),
                                    style="tonal", on_release=lambda *_a: self._update_sprites()))
        card.add_widget(btn_row)
        card.add_widget(MDLabel(
            text="„Herunterladen“ richtet das Sprite-Repo ein (Git-Clone + Pfade). "
                 "„Aktualisieren“ zieht per git pull.",
            role="small", adaptive_height=True))
        pane.add_widget(card)

        # Per-Game-Ausklapp
        card = _section_card("Sprites je Spiel")
        sw_row = MDBoxLayout(orientation="horizontal", size_hint_y=None,
                             adaptive_height=True, spacing=dp(8))
        sw_row.add_widget(MDLabel(text="Sprites jedes Spiels einzeln festlegen",
                                  role="small", valign="center"))
        self._game_switch = MDSwitch()
        self._game_switch.bind(active=self._on_game_switch)
        sw_row.add_widget(self._game_switch)
        card.add_widget(sw_row)
        self._game_expand = MDBoxLayout(orientation="vertical", size_hint_y=None,
                                        adaptive_height=True, spacing=dp(10))
        card.add_widget(self._game_expand)
        pane.add_widget(card)

        # 2-PC-Ausklapp (inkl. OBS-Helper-Paket)
        card = _section_card("Streamsetup mit zwei PCs")
        sw_row = MDBoxLayout(orientation="horizontal", size_hint_y=None,
                             adaptive_height=True, spacing=dp(8))
        sw_row.add_widget(MDLabel(text="Hast du ein Streamsetup mit zwei PCs?",
                                  role="small", valign="center"))
        self._obs2pc_switch = MDSwitch()
        self._obs2pc_switch.bind(active=self._on_obs2pc_switch)
        sw_row.add_widget(self._obs2pc_switch)
        card.add_widget(sw_row)
        self._obs_expand = MDBoxLayout(orientation="vertical", size_hint_y=None,
                                       adaptive_height=True, spacing=dp(10))
        card.add_widget(self._obs_expand)
        pane.add_widget(card)
        return pane

    def _collapse_label(self, collapsed: bool, noun: str) -> str:
        """Beschriftung des Einklapp-Buttons. Reiner Text (keine Chevron-Glyphe —
        Roboto rendert ▸/▾ als Tofu-Kästchen, Kivy-Unicode-Falle)."""
        return f"{noun} anzeigen" if collapsed else f"{noun} ausblenden"

    def _on_game_switch(self, _inst, active: bool) -> None:
        if self._loading_switches:
            # init_config setzt Flags selbst und baut danach einmal neu.
            return
        self._game_sprites_expanded = active
        self._rebuild_game_expand()
        # Solange OBS gekoppelt ist (Flag False, obs_single_path_check=None), folgt
        # der OBS-PC backend-seitig dem lokalen single_path_check. Den OBS-Schalter
        # spiegeln, damit die UI nicht verschoben ist (sonst: OBS-Zweig aktiv, aber
        # Schalter zeigt aus + leere _obs-Felder → kaputte Pfade). active-vor-bind
        # in _rebuild_obs_expand verhindert dabei einen Save-Loop.
        if not self._obs_flag_explicit:
            self._obs_game_sprites_expanded = active
            self._rebuild_obs_expand()
        if not self._suspend_save:
            self._save("sprites")

    @perf_span("settings._rebuild_game_expand")
    def _rebuild_game_expand(self) -> None:
        box = self._game_expand
        box.clear_widgets()
        self._game_fields.clear()
        if not self._game_sprites_expanded:
            return
        # Einklapp-Button: die lange 30er-Liste lässt sich aus dem Weg räumen,
        # ohne Per-Game abzuschalten (Feedback 2026-10-19).
        self._game_collapse_text = MDButtonText(
            text=self._collapse_label(self._game_list_collapsed, "Spiel-Liste"))
        self._game_collapse_btn = MDButton(self._game_collapse_text, style="text",
                                           on_release=lambda *_a: self._toggle_game_list())
        box.add_widget(self._game_collapse_btn)
        self._game_list_box = MDBoxLayout(orientation="vertical", size_hint_y=None,
                                          adaptive_height=True, spacing=dp(10))
        for text, game_id in self.games.items():
            sp_key = game_id.split('_', 1)[1]
            field = make_textfield(text, text=self.sp.get(sp_key, ""),
                                   on_validate=lambda: self._save("sprites"))
            self._game_fields[game_id] = field
            self._game_list_box.add_widget(browse_row(text, field, lambda f: self._browse(f, "dir")))
        if not self._game_list_collapsed:
            box.add_widget(self._game_list_box)

    def _toggle_game_list(self) -> None:
        # Reines Sicht-Toggle: Felder bleiben in _game_fields referenziert, also
        # gehen getippte Werte nicht verloren und _collect_sprites sammelt weiter.
        self._game_list_collapsed = not self._game_list_collapsed
        if self._game_list_collapsed:
            if self._game_list_box.parent:
                self._game_expand.remove_widget(self._game_list_box)
        elif not self._game_list_box.parent:
            self._game_expand.add_widget(self._game_list_box)
        self._game_collapse_text.text = self._collapse_label(
            self._game_list_collapsed, "Spiel-Liste")

    def _on_obs2pc_switch(self, _inst, active: bool) -> None:
        if self._loading_switches:
            # init_config setzt Flags selbst und baut danach einmal neu.
            return
        self._obs_sprites_expanded = active
        self._rebuild_obs_expand()
        self._rebuild_overlay_links()
        if not self._suspend_save:
            self._save("sprites")

    @perf_span("settings._rebuild_obs_expand")
    def _rebuild_obs_expand(self) -> None:
        box = self._obs_expand
        box.clear_widgets()
        self._obs_game_fields.clear()
        if not self._obs_sprites_expanded:
            return
        self._common_obs_path = make_textfield("Dateipfad Sprites (OBS-PC)",
                                               text=self.sp.get('common_obs_path', '') or self.sp.get('common_path', ''),
                                               on_validate=lambda: self._save("sprites"))
        box.add_widget(browse_row("Sprites (OBS-PC)", self._common_obs_path, lambda f: self._browse(f, "dir")))
        self._items_obs_path = make_textfield("Dateipfad Items (OBS-PC)",
                                              text=self.sp.get('items_obs_path', '') or self.sp.get('items_path', ''),
                                              on_validate=lambda: self._save("sprites"))
        box.add_widget(browse_row("Items (OBS-PC)", self._items_obs_path, lambda f: self._browse(f, "dir")))
        self._badges_obs_path = make_textfield("Dateipfad Orden (OBS-PC)",
                                               text=self.sp.get('badges_obs_path', '') or self.sp.get('badges_path', ''),
                                               on_validate=lambda: self._save("sprites"))
        box.add_widget(browse_row("Orden (OBS-PC)", self._badges_obs_path, lambda f: self._browse(f, "dir")))
        # Eigener Schalter für Per-Game-Sprites auf dem OBS-PC — unabhängig vom
        # lokalen Per-Game-Schalter auswählbar (Feedback 2026-10-19).
        sw_row = MDBoxLayout(orientation="horizontal", size_hint_y=None,
                             adaptive_height=True, spacing=dp(8))
        sw_row.add_widget(MDLabel(text="Sprites jedes Spiels einzeln festlegen (OBS-PC)",
                                  role="small", valign="center"))
        self._obs_game_switch = MDSwitch()
        # active VOR dem bind setzen (fire-before-ids-Crash vermeiden + kein Save-Loop).
        self._obs_game_switch.active = self._obs_game_sprites_expanded
        self._obs_game_switch.bind(active=self._on_obs_game_switch)
        sw_row.add_widget(self._obs_game_switch)
        box.add_widget(sw_row)
        self._obs_game_expand = MDBoxLayout(orientation="vertical", size_hint_y=None,
                                            adaptive_height=True, spacing=dp(10))
        box.add_widget(self._obs_game_expand)
        self._rebuild_obs_game_expand()
        box.add_widget(MDLabel(
            text="OBS-PC-Paket: kopiert Helper-EXE + Config zum Übertragen auf den Streaming-PC.",
            role="small", adaptive_height=True))
        box.add_widget(MDButton(MDButtonText(text="OBS-Helper-Paket erstellen"),
                                style="outlined",
                                on_release=lambda *_a: self._create_obs_helper_package()))

    def _on_obs_game_switch(self, _inst, active: bool) -> None:
        self._obs_game_sprites_expanded = active
        # Expliziter Toggle → ab jetzt wird obs_single_path_check geschrieben
        # (entkoppelt vom lokalen single_path_check).
        self._obs_flag_explicit = True
        self._rebuild_obs_game_expand()
        if not self._suspend_save:
            self._save("sprites")

    @perf_span("settings._rebuild_obs_game_expand")
    def _rebuild_obs_game_expand(self) -> None:
        box = self._obs_game_expand
        box.clear_widgets()
        self._obs_game_fields.clear()
        if not self._obs_game_sprites_expanded:
            return
        self._obs_collapse_text = MDButtonText(
            text=self._collapse_label(self._obs_game_list_collapsed, "Spiel-Liste (OBS-PC)"))
        self._obs_collapse_btn = MDButton(self._obs_collapse_text, style="text",
                                          on_release=lambda *_a: self._toggle_obs_game_list())
        box.add_widget(self._obs_collapse_btn)
        self._obs_game_list_box = MDBoxLayout(orientation="vertical", size_hint_y=None,
                                              adaptive_height=True, spacing=dp(10))
        for text, game_id in self.games.items():
            sp_key = game_id.split('_', 1)[1] + '_obs'
            field = make_textfield(f"{text} (OBS-PC)", text=self.sp.get(sp_key, ""),
                                   on_validate=lambda: self._save("sprites"))
            self._obs_game_fields[game_id] = field
            self._obs_game_list_box.add_widget(browse_row(f"{text} (OBS-PC)", field, lambda f: self._browse(f, "dir")))
        if not self._obs_game_list_collapsed:
            box.add_widget(self._obs_game_list_box)

    def _toggle_obs_game_list(self) -> None:
        self._obs_game_list_collapsed = not self._obs_game_list_collapsed
        if self._obs_game_list_collapsed:
            if self._obs_game_list_box.parent:
                self._obs_game_expand.remove_widget(self._obs_game_list_box)
        elif not self._obs_game_list_box.parent:
            self._obs_game_expand.add_widget(self._obs_game_list_box)
        self._obs_collapse_text.text = self._collapse_label(
            self._obs_game_list_collapsed, "Spiel-Liste (OBS-PC)")

    def _download_sprites(self) -> None:
        SpriteSetupPopupMD(self.sp, self.configsave,
                           save_callback=self._after_sprite_download).open()

    def _after_sprite_download(self) -> None:
        prev = self._suspend_save
        self._suspend_save = True
        try:
            self._common_path.text = self.sp.get('common_path', '')
            self._items_path.text = self.sp.get('items_path', '')
            self._badges_path.text = self.sp.get('badges_path', '')
            # apply_sprite_paths setzt auch Edition-/OBS-Keys in sp — Per-Game-
            # und OBS-Felder aus sp neu aufbauen, sonst überschreibt der folgende
            # Save die frisch geklonten Pfade mit alten Widget-Werten.
            self._rebuild_game_expand()
            self._rebuild_obs_expand()
        finally:
            self._suspend_save = prev
        self._save("sprites")

    def _update_sprites(self) -> None:
        from backend.sprite_repo import (get_repo_root_from_subpath, is_sprite_repo,
                                          pull_sprite_repo)
        repo_root = get_repo_root_from_subpath(self.sp.get('common_path', ''))
        if not repo_root or not is_sprite_repo(repo_root):
            show_snackbar("Kein Sprite-Repository gefunden. Bitte zuerst herunterladen.",
                          level='error', duration=3.0)
            return
        show_snackbar("Sprites werden aktualisiert …", level='info', duration=2.5)

        async def _run():
            try:
                loop = asyncio.get_event_loop()
                result = await loop.run_in_executor(None, pull_sprite_repo, repo_root)
                if result.success:
                    msg = "Sprites aktualisiert" if result.updated else "Sprites bereits aktuell"
                    show_snackbar(msg, level='success')
                else:
                    show_snackbar("Fehler beim Aktualisieren. Siehe Log.", level='error', duration=3.0)
            except Exception as err:
                logger.error(f"_update_sprites fehlgeschlagen: {err}")
                logger.error(traceback.format_exc())
                show_snackbar("Fehler beim Aktualisieren. Siehe Log.", level='error', duration=3.0)
        self._spawn(_run())

    def _create_obs_helper_package(self) -> None:
        pick_directory(on_select=self._build_obs_helper_package,
                       title="Zielordner für OBS-PC Paket wählen")

    def _build_obs_helper_package(self, target: str) -> None:
        import shutil
        import yaml as _yaml
        if not target:
            return
        try:
            helper_dir = os.path.join(target, "sprite_helper")
            os.makedirs(helper_dir, exist_ok=True)
            copied = []
            for src in (os.path.join("utils", "sprite_helper.exe"),
                        os.path.join("utils", "pull_task.exe")):
                if os.path.exists(src):
                    shutil.copy2(src, helper_dir)
                    copied.append(os.path.basename(src))
            if not copied:
                show_snackbar("Helper-EXE nicht gefunden unter utils/. Bitte zuerst kompilieren.",
                              level='error', duration=3.5)
                return
            helper_port = int(self.arceus.rem.get('helper_port', int(self.arceus.port) + 1))
            config = {
                "repo_url": "https://github.com/agent0816/sprites.git",
                "tracker_ip": self.externalIPv4,
                "tracker_port": helper_port,
            }
            with open(os.path.join(helper_dir, "config.yml"), 'w') as f:
                _yaml.dump(config, f)
            show_snackbar(f"OBS-PC Paket erstellt in {helper_dir}", level='success', duration=4.0)
        except Exception as err:
            logger.error(f"_build_obs_helper_package fehlgeschlagen: {err}")
            logger.error(traceback.format_exc())
            show_snackbar(f"OBS-Paket fehlgeschlagen: {err}", level='error', duration=4.0)

    # ---------------------------------------------------------------- Spieler
    def _build_player_tab(self) -> MDBoxLayout:
        pane = self._pane()
        card = _section_card("Spieler")
        self._your_name = make_textfield("Anzeigename", on_validate=lambda: self._save("player"))
        card.add_widget(labeled_row("Anzeigename", self._your_name))
        self._session_game = make_textfield("Spiel der Session", readonly=True)
        card.add_widget(labeled_row("Spiel der Session", self._session_game))

        card.add_widget(MDLabel(text="Spieleranzahl", role="small", adaptive_height=True))
        self._count_segment = MDSegmentedButton()
        self._count_items: dict[int, MDSegmentedButtonItem] = {}
        for i in range(1, 5):
            item = MDSegmentedButtonItem(MDSegmentButtonLabel(text=str(i)))
            item.bind(active=self._on_count_segment)
            self._count_items[i] = item
            self._count_segment.add_widget(item)
        card.add_widget(self._count_segment)

        card.add_widget(MDLabel(
            text="Pro-Spieler-Einstellungen (remote/OBS). Konzept wird noch überarbeitet.",
            role="small", adaptive_height=True))
        self._player_expand = MDBoxLayout(orientation="vertical", size_hint_y=None,
                                          adaptive_height=True, spacing=dp(8))
        card.add_widget(self._player_expand)
        pane.add_widget(card)
        return pane

    def _on_count_segment(self, item, active: bool) -> None:
        if not active or self._suspend_save:
            return
        for count, it in self._count_items.items():
            if it is item:
                self.pl["player_count"] = count
                break
        self._rebuild_player_expand()
        self._save("player")
        # MainMenu-Team-Boxen an neue Spieleranzahl angleichen (Parität Klassik
        # update_trainer_boxes) + Overlay-Links (Spieleranzahl ändert die URLs).
        try:
            if self.manager is not None and self.manager.has_screen("MainMenu"):
                self.manager.get_screen("MainMenu").rebuild_trainer_boxes()
        except Exception as err:
            logger.error(f"MainMenu-Rebuild nach Spieleranzahl fehlgeschlagen: {err}")
            logger.error(traceback.format_exc())
        self._rebuild_overlay_links()

    @perf_span("settings._rebuild_player_expand")
    def _rebuild_player_expand(self) -> None:
        box = self._player_expand
        box.clear_widgets()
        self._player_switches.clear()
        count = int(self.pl.get('player_count', 1) or 1)
        for i in range(1, count + 1):
            row = MDBoxLayout(orientation="horizontal", size_hint_y=None,
                              height=dp(40), spacing=dp(8))
            row.add_widget(MDLabel(text=f"Spieler {i}", size_hint_x=0.3,
                                   role="small", valign="center"))
            # active NICHT als kwarg (feuert on_active vor ids.thumb -> Crash);
            # nach der Konstruktion setzen.
            remote_sw = MDSwitch()
            remote_sw.active = bool(self.pl.get(f"remote_{i}", False))
            obs_sw = MDSwitch()
            obs_sw.active = bool(self.pl.get(f"obs_{i}", False))
            obs_sw.disabled = not remote_sw.active
            remote_sw.bind(active=lambda _i, val, n=i, o=obs_sw: self._on_player_remote(n, val, o))
            obs_sw.bind(active=lambda _i, val, n=i: self._on_player_obs(n, val))
            self._player_switches[f"remote_{i}"] = remote_sw
            self._player_switches[f"obs_{i}"] = obs_sw
            row.add_widget(MDLabel(text="remote", size_hint_x=0.2, role="small", valign="center"))
            row.add_widget(remote_sw)
            row.add_widget(MDLabel(text="OBS", size_hint_x=0.2, role="small", valign="center"))
            row.add_widget(obs_sw)
            box.add_widget(row)

    def _on_player_remote(self, n: int, val: bool, obs_sw: MDSwitch) -> None:
        obs_sw.disabled = not val
        if not val:
            # Unter Suspend, sonst feuert _on_player_obs einen zweiten save_player.
            # Vorwert merken statt hart False — falls dieser Handler je in einem
            # bereits suspendierten Kontext läuft, bleibt dessen Guard erhalten.
            prev = self._suspend_save
            self._suspend_save = True
            try:
                obs_sw.active = False
            finally:
                self._suspend_save = prev
        if not self._suspend_save:
            self._save("player")

    def _on_player_obs(self, n: int, val: bool) -> None:
        if not self._suspend_save:
            self._save("player")

    # ------------------------------------------------------------- Randomizer
    def _build_randomizer_tab(self) -> MDBoxLayout:
        pane = self._pane()
        card = _section_card("Randomizer")
        self._jar_path = make_textfield("Pfad zur PokeRandoZX.jar",
                                        on_validate=lambda: self._save("randomizer"))
        card.add_widget(browse_row("PokeRandoZX.jar", self._jar_path, lambda f: self._browse(f, "file")))
        self._java_path = make_textfield("Java-Pfad (leer = auto)",
                                         on_validate=lambda: self._save("randomizer"))
        card.add_widget(browse_row("Java-Pfad", self._java_path, lambda f: self._browse(f, "file")))
        self._rnqs_path = make_textfield("Einstellungsdatei (.rnqs)",
                                         on_validate=lambda: self._save("randomizer"))
        card.add_widget(browse_row("Einstellungen (.rnqs)", self._rnqs_path, lambda f: self._browse(f, "file")))
        btn_row = MDBoxLayout(orientation="horizontal", size_hint_y=None,
                              adaptive_height=True, spacing=dp(10))
        btn_row.add_widget(MDButton(MDButtonText(text="Randomizer-GUI öffnen"),
                                    style="tonal", on_release=lambda *_a: self._open_randomizer_gui()))
        btn_row.add_widget(MDButton(MDButtonText(text="Log importieren"),
                                    style="outlined", on_release=lambda *_a: self._import_randomizer_log()))
        card.add_widget(btn_row)
        self._rom_path = make_textfield("ROM-Datei",
                                        on_validate=lambda: self._save("randomizer"))
        card.add_widget(browse_row("ROM-Datei", self._rom_path, lambda f: self._browse(f, "file")))
        pane.add_widget(card)

        # Run-Historie
        card = _section_card("Run-Historie")
        active_row = MDBoxLayout(orientation="horizontal", size_hint_y=None,
                                 adaptive_height=True, spacing=dp(10))
        self._active_run_label = MDLabel(text="(kein aktiver Run)", size_hint_x=0.7,
                                         role="small", valign="center")
        active_row.add_widget(self._active_run_label)
        self._end_run_button = MDButton(MDButtonText(text="Run manuell beenden"),
                                        style="outlined",
                                        on_release=lambda *_a: self._end_run_manual())
        self._end_run_button.disabled = True
        active_row.add_widget(self._end_run_button)
        card.add_widget(active_row)
        self._run_list_box = MDBoxLayout(orientation="vertical", size_hint_y=None,
                                         adaptive_height=True, spacing=dp(6))
        card.add_widget(self._run_list_box)
        card.add_widget(MDButton(MDButtonText(text="Run-Historie aktualisieren"),
                                 style="tonal", on_release=lambda *_a: self._refresh_run_history()))
        pane.add_widget(card)
        return pane

    def _open_randomizer_gui(self) -> None:
        self._save("randomizer")
        success, error = self.randomizer.open_gui()
        if not success:
            show_snackbar(error, level='error', duration=3.5)

    def _import_randomizer_log(self) -> None:
        pick_file(on_select=self._process_randomizer_log, filters=['*.log'],
                  title="Randomizer-Log importieren")

    def _process_randomizer_log(self, log_path: str) -> None:
        if not log_path:
            return
        success, msg = self.randomizer.parse_log(log_path)
        if success:
            try:
                apply_rando_to_munchlax(self.munchlax, self.randomizer, self.pl, logger)
            except Exception as err:
                logger.error(f"apply_rando_to_munchlax nach Log-Import fehlgeschlagen: {err}")
                logger.error(traceback.format_exc())
        show_snackbar(msg, level='success' if success else 'error',
                      duration=2.5 if success else 3.5)

    def _get_run_manager(self):
        from backend.controller.run_manager import RunManager
        if self.configsave is None:
            return None
        try:
            return RunManager(str(self.configsave))
        except Exception as err:
            logger.error(f"RunManager-Init im Settings-UI fehlgeschlagen: {err}")
            return None

    @perf_span("settings._refresh_run_history")
    def _refresh_run_history(self) -> None:
        rm = self._get_run_manager()
        if rm is None:
            self._run_list_box.clear_widgets()
            self._run_rows_key = None
            self._active_run_label.text = "(Session-Pfad fehlt)"
            self._end_run_button.disabled = True
            return
        try:
            active = rm.get_active_run()
            if active is None:
                self._active_run_label.text = "(kein aktiver Run)"
                self._end_run_button.disabled = True
            else:
                slots = [str(r.get("player_slot")) for r in (active.get("roms") or [])]
                self._active_run_label.text = (
                    f"Aktiv: {active.get('run_id', '?')} — Slots {','.join(slots) or '-'} "
                    f"— seit {active.get('start_ts', '?')}")
                self._end_run_button.disabled = False
            with perf_span("settings.list_runs"):
                runs = rm.list_runs()
            # Zeilen nur neu bauen, wenn sich Session oder Run-Metadaten geändert
            # haben — der Neubau kostet ~23 ms pro Zeile, list_runs nur ~20 ms gesamt.
            key = (str(rm.runs_root), runs)
            if key == self._run_rows_key:
                perf_log(f"settings.run_rows unverändert n={len(runs)}")
                return
            self._run_list_box.clear_widgets()
            for entry in reversed(runs):
                self._run_list_box.add_widget(self._build_run_row(entry))
            self._run_rows_key = key
            perf_log(f"settings.run_rows n={len(runs)}")
        except Exception as err:
            logger.error(f"_refresh_run_history fehlgeschlagen: {err}")
            logger.error(traceback.format_exc())
            self._run_list_box.clear_widgets()
            self._run_rows_key = None
            self._active_run_label.text = "(Fehler beim Laden der Run-Historie)"

    def _build_run_row(self, entry: dict) -> MDBoxLayout:
        row = MDBoxLayout(orientation="vertical", size_hint_y=None,
                          adaptive_height=True, spacing=dp(2), padding=[dp(5), dp(4)])
        run_id = entry.get("run_id", "?")
        idx = entry.get("run_index", "?")
        reason = entry.get("end_reason") or "(aktiv)"
        start = entry.get("start_ts", "?")
        end = entry.get("end_ts") or "-"
        dur = entry.get("duration_seconds")
        dur_txt = f"{dur}s" if isinstance(dur, int) else "-"
        wipe = entry.get("team_wipe") or {}
        wipe_txt = f" wipe={wipe.get('player_id')}" if wipe.get("player_id") else ""
        row.add_widget(MDLabel(
            text=f"#{idx} {run_id} — {reason} — {start} → {end} ({dur_txt}){wipe_txt}",
            role="small", adaptive_height=True))
        for rom_entry in (entry.get("roms") or []):
            slot = rom_entry.get("player_slot")
            subdir_name = rom_entry.get("subdir") or f"p{slot}"
            run_path = entry.get("_path") or ""
            slot_dir = os.path.join(run_path, subdir_name)
            log_path = os.path.join(slot_dir, rom_entry.get("log_file") or "randomizer.log")
            slot_row = MDBoxLayout(orientation="horizontal", size_hint_y=None,
                                   adaptive_height=True, spacing=dp(6))
            slot_row.add_widget(MDLabel(text=f"Slot {slot}", size_hint_x=0.15,
                                        role="small", valign="center"))
            slot_row.add_widget(MDButton(MDButtonText(text="Log öffnen"), style="text",
                                         on_release=lambda *_a, p=log_path: self._open_path(p)))
            slot_row.add_widget(MDButton(MDButtonText(text="Ordner-Pfad kopieren"), style="text",
                                         on_release=lambda *_a, p=slot_dir: self._copy_to_clipboard(p)))
            slot_row.add_widget(MDButton(MDButtonText(text="Ordner öffnen"), style="text",
                                         on_release=lambda *_a, p=slot_dir: self._open_path(p)))
            row.add_widget(slot_row)
        return row

    def _end_run_manual(self) -> None:
        if self.munchlax is None:
            show_snackbar("Munchlax nicht verfügbar.", level='error', duration=3.0)
            return
        confirm_dialog("Aktiven Run wirklich beenden?",
                       on_confirm=lambda: self._spawn(self._do_end_run_manual()),
                       confirm_text="Ja, beenden")

    async def _do_end_run_manual(self) -> None:
        show_snackbar("Run wird beendet …", level='info', duration=2.5)
        try:
            loop = asyncio.get_event_loop()
            run_id = await loop.run_in_executor(None, self.munchlax.end_run_manual, "manual")
        except Exception as err:
            logger.error(f"end_run_manual failed: {err}")
            logger.error(traceback.format_exc())
            show_snackbar(f"Run beenden fehlgeschlagen: {err}", level='error', duration=3.0)
            return
        if run_id:
            show_snackbar(f"Run {run_id} als 'manual' abgeschlossen.", level='success')
        else:
            show_snackbar("Kein aktiver Run vorhanden.", level='info')
        self._refresh_run_history()

    def _open_path(self, path: str) -> None:
        if not path or not os.path.exists(path):
            show_snackbar(f"Pfad nicht gefunden: {path}", level='error', duration=3.0)
            return
        try:
            os.startfile(path)  # type: ignore[attr-defined]
        except Exception as err:
            logger.error(f"os.startfile({path}) failed: {err}")
            show_snackbar(f"Konnte Pfad nicht öffnen: {err}", level='error', duration=3.0)

    def _copy_to_clipboard(self, text: str) -> None:
        value = text or ""
        if value:
            try:
                value = str(Path(value).resolve())
            except Exception as err:
                logger.warning(f"_copy_to_clipboard resolve fehlgeschlagen: {err}")
        Clipboard.copy(value)
        show_snackbar(f"Kopiert: {value or '(leer)'}", level='success')

    # ---------------------------------------------------------------- Logging
    def _build_logging_tab(self) -> MDBoxLayout:
        pane = self._pane()

        # --- Darstellung / UI-Modus (Phase F2) ----------------------------
        # Der Wechsel wird sofort in app_state gespeichert, greift aber erst
        # nach einem Neustart (main.py liest den Modus VOR dem Kivy-Import).
        ui_card = _section_card("Darstellung")
        ui_card.add_widget(MDLabel(
            text="Oberfläche — Wechsel wird erst nach einem Neustart aktiv.",
            role="small", adaptive_height=True))
        self._ui_segment = MDSegmentedButton()
        self._ui_items: dict[str, MDSegmentedButtonItem] = {}
        for ui_value, label in ((app_state.UI_MD, "Material (MD)"),
                                (app_state.UI_CLASSIC, "Klassik")):
            item = MDSegmentedButtonItem(MDSegmentButtonLabel(text=label))
            item.bind(active=lambda _it, val, u=ui_value: self._on_ui_segment(u, val))
            self._ui_items[ui_value] = item
            self._ui_segment.add_widget(item)
        ui_card.add_widget(self._ui_segment)
        pane.add_widget(ui_card)
        # Der aktuell laufende Modus: diese MD-Settings existieren nur in der
        # MD-App, also immer "md". Der Neustart-Dialog vergleicht gegen diesen
        # Wert (nicht gegen die gespeicherte Pref), damit ein Rückwechsel auf
        # den laufenden Modus keinen sinnlosen Dialog auslöst.
        self._running_ui = app_state.UI_MD
        # Initiale Auswahl aus der gespeicherten Präferenz — unter Suspend, damit
        # das programmatische active-Setzen nicht den Neustart-Dialog auslöst.
        prev = self._suspend_save
        self._suspend_save = True
        try:
            self._ui_items[app_state.get_ui_preference()].active = True
        finally:
            self._suspend_save = prev

        card = _section_card("Logging")
        self._console_level = DropdownField(LOG_LEVELS, get_console_level(),
                                            lambda v: set_console_level(v))
        card.add_widget(labeled_row("Konsolen-Log-Level", self._console_level))
        pane.add_widget(card)

        if is_developer():
            card = _section_card("Diagnose (Dev)")
            card.add_widget(MDLabel(
                text="Nur im Source-Run / TRACKER_DEV=1 sichtbar.",
                role="small", adaptive_height=True))
            card.add_widget(MDLabel(text="Per-Modul File-Log-Level", role="small",
                                    bold=True, adaptive_height=True))
            self._dev_module_box = MDBoxLayout(orientation="vertical", size_hint_y=None,
                                               adaptive_height=True, spacing=dp(4))
            card.add_widget(self._dev_module_box)
            dev_btns = MDBoxLayout(orientation="horizontal", size_hint_y=None,
                                   adaptive_height=True, spacing=dp(10))
            dev_btns.add_widget(MDButton(MDButtonText(text="Module neu einlesen"),
                                         style="outlined",
                                         on_release=lambda *_a: self._refresh_dev_module_log_levels()))
            dev_btns.add_widget(MDButton(MDButtonText(text="Logs packen"), style="outlined",
                                         on_release=lambda *_a: self._on_pack_logs_pressed_dev()))
            dev_btns.add_widget(MDButton(MDButtonText(text="Support-Bundle-Ordner öffnen"),
                                         style="outlined",
                                         on_release=lambda *_a: self._open_support_bundles_folder()))
            card.add_widget(dev_btns)
            pane.add_widget(card)
            self._refresh_dev_module_log_levels()
        else:
            self._dev_module_box = None
        return pane

    def _on_ui_segment(self, ui_value: str, active: bool) -> None:
        # MDSegmentedButton feuert active=True fürs neu gewählte und active=False
        # fürs abgewählte Item — nur auf die Auswahl reagieren.
        if not active or self._suspend_save:
            return
        # Präferenz immer (idempotent) persistieren — greift beim nächsten Start.
        # "Später" lässt sie also stehen.
        try:
            app_state.set_ui_preference(ui_value)
        except Exception as err:
            logger.error(f"UI-Modus speichern fehlgeschlagen: {err}\n{traceback.format_exc()}")
            show_snackbar("UI-Modus konnte nicht gespeichert werden.", level='error', duration=4.0)
            return
        # Neustart-Dialog nur, wenn das Ziel vom laufenden Modus abweicht.
        if ui_value == self._running_ui:
            return
        label = "Material (MD)" if ui_value == app_state.UI_MD else "klassischen"
        confirm_dialog(
            "UI-Modus gewechselt",
            f"Die App nutzt nach einem Neustart die {label} Oberfläche.",
            on_confirm=lambda: MDApp.get_running_app().relaunch(),
            confirm_text="Jetzt neu starten",
            cancel_text="Später",
        )

    def _refresh_dev_module_log_levels(self) -> None:
        box = getattr(self, "_dev_module_box", None)
        if box is None:
            return
        box.clear_widgets()
        modules = list_known_modules()
        if not modules:
            box.add_widget(MDLabel(text="(noch keine Modul-Logger registriert)",
                                   role="small", adaptive_height=True))
            return
        for module_key in modules:
            dd = DropdownField(LOG_LEVELS, get_module_level(module_key),
                               lambda val, mk=module_key: set_module_level(mk, val))
            box.add_widget(labeled_row(module_key, dd, label_hint_x=0.6))

    def _on_pack_logs_pressed_dev(self) -> None:
        show_snackbar("Logs werden gepackt …", level='info', duration=2.5)

        async def _runner():
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
                    if self.munchlax is not None:
                        self.munchlax.set_bundle_session_path(session_path)
                except Exception as err:
                    logger.warning(f"[Dev] set_bundle_session_path failed: {err}")
                bundle_path = await asyncio.to_thread(build_bundle, session_path,
                                                      player_name, is_host, None)
                size_mib = bundle_path.stat().st_size / (1024 * 1024)
                show_snackbar(f"Bundle gespeichert ({size_mib:.1f} MiB): {bundle_path.name}",
                              level='success', duration=4.0)
            except Exception as err:
                logger.error(f"[Dev] _on_pack_logs_pressed_dev: {err}")
                logger.error(traceback.format_exc())
                show_snackbar(f"Logs packen fehlgeschlagen: {err}", level='error', duration=5.0)
        self._spawn(_runner())

    def _open_support_bundles_folder(self) -> None:
        from backend.log_bundler import SUPPORT_BUNDLES_DIR
        SUPPORT_BUNDLES_DIR.mkdir(parents=True, exist_ok=True)
        self._open_path(str(SUPPORT_BUNDLES_DIR.resolve()))

    # ------------------------------------------------------------ gemeinsames
    def _browse(self, field: MDTextField, modus: str) -> None:
        def _apply(path: str):
            if not path:
                return
            field.text = self._strip_common(field, path)
            self._save_for_field(field)
        current = field.text or None
        if modus == "file":
            pick_file(on_select=_apply, start_path=current, title="Datei auswählen")
        else:
            pick_directory(on_select=_apply, start_path=current, title="Ordner auswählen")

    def _strip_common(self, field: MDTextField, path: str) -> str:
        """Per-Game-Felder (lokal/OBS) speichern relativ zum gemeinsamen Pfad —
        der common_path-Präfix wird abgeschnitten (Parität zur Klassik-UI,
        settingsmenu.browse). Nicht-Per-Game-Felder bleiben unverändert."""
        if field in self._game_fields.values():
            common = self._common_path.text or ""
        elif field in getattr(self, "_obs_game_fields", {}).values():
            obs_common = getattr(self, "_common_obs_path", None)
            common = obs_common.text if obs_common is not None else ""
        else:
            return path
        return path.replace(common, "", 1) if common else path

    def _save_for_field(self, field: MDTextField) -> None:
        """Speichert die Sektion, zu der ein (per Browse gesetztes) Feld gehoert."""
        if field in (self._bizhawk_exe,):
            self._save("bizhawk")
        elif field in (self._common_path, self._items_path, self._badges_path) \
                or field in self._game_fields.values() \
                or field in getattr(self, "_obs_game_fields", {}).values() \
                or field in (getattr(self, "_common_obs_path", None),
                             getattr(self, "_items_obs_path", None),
                             getattr(self, "_badges_obs_path", None)):
            self._save("sprites")
        elif field in (self._jar_path, self._java_path, self._rnqs_path, self._rom_path):
            self._save("randomizer")
        else:
            self._save("sprites")

    def _spawn(self, coro) -> None:
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    # ------------------------------------------------------------- Save/Load
    @perf_span("settings._collect_for_save")
    def _collect_for_save(self, section: str) -> dict | None:
        """Sammelt die Werte einer Sektion. Gibt None zurück, wenn ein Pflicht-
        Port leer ist — dann wird NICHT gespeichert (alter Wert bleibt erhalten),
        statt einen leeren Port ins Config-Dict/an set_port zu schreiben."""
        if section == "bizhawk":
            if not self._bizhawk_port.text:
                return None
            return {'path': self._bizhawk_exe.text, 'port': self._bizhawk_port.text}
        if section == "obs":
            if not self._obs_port.text:
                return None
            return {'password': self._obs_password.text, 'host': self._obs_host.text,
                    'port': self._obs_port.text}
        if section == "remote":
            if not (self._port_client.text and self._helper_port.text and self._port_server.text):
                return None
            return {'client_port': self._port_client.text, 'helper_port': self._helper_port.text,
                    'server_ip_adresse': self._ip_server.text, 'server_port': self._port_server.text}
        if section == "player":
            return self._collect_player()
        if section == "randomizer":
            return {'jar_path': self._jar_path.text, 'java_path': self._java_path.text,
                    'settings_path': self._rnqs_path.text, 'rom_path': self._rom_path.text}
        if section == "sprites":
            return self._collect_sprites()
        if section == "overlay":
            if not self._overlay_port.text:
                return None
            try:
                duration_ms = int(self._anim_dd.value)
            except (TypeError, ValueError):
                duration_ms = 300
            return {'port': self._overlay_port.text, 'layout': self._layout_dd.value,
                    'badge_layout': self._badge_dd.value, 'animation_duration_ms': duration_ms}
        return None

    def _save(self, section: str) -> None:
        if self._suspend_save:
            return
        if self._config_dirty:
            # Widgets halten noch Werte der vorigen Session — nicht in die neue schreiben.
            logger.warning(f"Settings _save('{section}') übersprungen: Config nach Session-Wechsel noch nicht geladen")
            return
        try:
            values = self._collect_for_save(section)
            if values is None:
                return
            # Change-Guard: nur speichern, wenn sich wirklich etwas geändert hat.
            # Klassik bündelt per Button; MD speichert pro Feld, ohne Guard würde
            # jeder Fokus-Verlust/Dropdown-Reopen ein controller.save_* samt
            # redraw_obs/Server-Reconfigure auslösen.
            if self._last_saved.get(section) == values:
                return
            prev = self._last_saved.get(section)
            getattr(self.controller, f"save_{section}")(values)
            # Baseline erst NACH dem Save setzen (controller.save_* fängt Fehler
            # intern ab; bei einem Fehler vor Rückkehr bliebe sonst ein nicht
            # geschriebener Wert als "gespeichert" markiert).
            self._last_saved[section] = dict(values)
            if section == "overlay":
                # animation_duration_ms zusätzlich in sprites (Parität Klassik),
                # aber nur bei echter Änderung (sonst unnötiges redraw_obs).
                if prev is None or prev.get('animation_duration_ms') != values['animation_duration_ms']:
                    self.controller.save_sprites({'obs_animation_duration_ms': values['animation_duration_ms']})
        except Exception as err:
            logger.error(f"Settings _save('{section}') fehlgeschlagen: {err}")
            logger.error(traceback.format_exc())

    def _collect_sprites(self) -> dict:
        values = {
            'common_path': self._common_path.text,
            'items_path': self._items_path.text,
            'badges_path': self._badges_path.text,
            'single_path_check': not self._game_sprites_expanded,
            'obs_2_pc': self._obs_sprites_expanded,
        }
        # obs_single_path_check NUR schreiben, wenn der OBS-Per-Game-Schalter je
        # explizit berührt wurde — sonst bleibt der Key None (gekoppelt), auch bei
        # einem reinen lokalen Sprite-Save (Backend: obs.py get_sprite two_pc-Zweig).
        if self._obs_flag_explicit:
            values['obs_single_path_check'] = not self._obs_game_sprites_expanded
        if self._obs_sprites_expanded and hasattr(self, "_common_obs_path"):
            values['common_obs_path'] = self._common_obs_path.text
            values['items_obs_path'] = self._items_obs_path.text
            values['badges_obs_path'] = self._badges_obs_path.text
        if self._game_sprites_expanded:
            for _, game_id in self.games.items():
                field = self._game_fields.get(game_id)
                if field is not None:
                    values[game_id.split('_', 1)[1]] = field.text
        # Per-Game-OBS-Pfade nur, wenn 2-PC AN und der eigene OBS-Per-Game-Schalter an.
        if self._obs_sprites_expanded and self._obs_game_sprites_expanded:
            for _, game_id in self.games.items():
                field = self._obs_game_fields.get(game_id)
                if field is not None:
                    values[game_id.split('_', 1)[1] + '_obs'] = field.text
        return values

    def _collect_player(self) -> dict:
        values = {'your_name': self._your_name.text}
        count = int(self.pl.get('player_count', 1) or 1)
        for i in range(1, count + 1):
            r = self._player_switches.get(f"remote_{i}")
            o = self._player_switches.get(f"obs_{i}")
            if r is not None:
                values[f"remote_{i}"] = bool(r.active)
            if o is not None:
                values[f"obs_{i}"] = bool(o.active)
        return values

    @perf_span("settings.init_config")
    def init_config(self) -> None:
        """Lädt alle Werte aus dem Controller in die Widgets. Läuft im __init__
        und bei jedem Session-Wechsel. Setzt Switches/Dropdowns programmatisch →
        _suspend_save verhindert Save-Loops."""
        self._suspend_save = True
        # Baseline der vorigen Session verwerfen, sonst könnte bei einem leeren
        # Pflicht-Port (collect→None) die alte Baseline einen späteren Save mit
        # identischen Werten fälschlich unterdrücken.
        self._last_saved.clear()
        try:
            self._session_label.text = f"Version {self.app_version} · Session: {self.selected_session()}"

            sp = self.controller.load_sprites()
            self._common_path.text = sp.get('common_path', '')
            self._items_path.text = sp.get('items_path', '')
            self._badges_path.text = sp.get('badges_path', '')
            self._game_sprites_expanded = not sp.get('single_path_check', True)
            self._obs_sprites_expanded = bool(sp.get('obs_2_pc', False))
            # Eigener Config-Key obs_single_path_check mit Sentinel None = gekoppelt
            # an single_path_check (alte/Klassik-Sessions). Erst ein expliziter
            # Toggle schreibt True/False und entkoppelt.
            obs_single = sp.get('obs_single_path_check')
            self._obs_flag_explicit = obs_single is not None
            if obs_single is None:
                obs_single = sp.get('single_path_check', True)
            self._obs_game_sprites_expanded = not obs_single
            # Programmatisches active= feuert die Switch-Handler, die selbst neu
            # bauen — Flag unterdrückt das, der Neubau folgt hier genau einmal.
            self._loading_switches = True
            try:
                self._game_switch.active = self._game_sprites_expanded
                self._obs2pc_switch.active = self._obs_sprites_expanded
            finally:
                self._loading_switches = False
            self._rebuild_game_expand()
            self._rebuild_obs_expand()

            bh = self.controller.load_bizhawk()
            self._bizhawk_exe.text = bh.get('path', '')
            self._bizhawk_port.text = bh.get('port', '')

            obs = self.controller.load_obs()
            self._obs_host.text = obs.get('host', '')
            self._obs_port.text = obs.get('port', '')
            self._obs_password.text = obs.get('password', '')

            rem = self.controller.load_remote()
            self._port_client.text = rem.get('client_port', '')
            self._helper_port.text = rem.get('helper_port', '43887')
            self._ip_server.text = rem.get('server_ip_adresse', '')
            self._port_server.text = rem.get('server_port', '')

            pl = self.controller.load_player()
            self._your_name.text = pl.get('your_name', '')
            self._session_game.text = pl.get('session_game', '')
            count = int(pl.get('player_count', 1) or 1)
            for i, item in self._count_items.items():
                item.active = (i == count)
            self._rebuild_player_expand()

            rnd = self.controller.load_randomizer()
            self._jar_path.text = rnd.get('jar_path', '')
            self._java_path.text = rnd.get('java_path', '')
            self._rnqs_path.text = rnd.get('settings_path', '')
            self._rom_path.text = rnd.get('rom_path', '')

            ov = self.controller.load_overlay()
            self._overlay_port.text = ov.get('port', '43888')
            self._layout_dd.set_value(ov.get('layout', 'horizontal'))
            self._badge_dd.set_value(ov.get('badge_layout', 'horizontal'))
            self._anim_dd.set_value(str(ov.get('animation_duration_ms', 300)))
        except Exception as err:
            logger.error(f"Settings init_config fehlgeschlagen: {err}")
            logger.error(traceback.format_exc())
        finally:
            self._suspend_save = False
        # Change-Guard-Baseline aus den gerade geladenen Werten setzen, damit der
        # erste echte User-Change korrekt erkannt und ein unveränderter Fokus-
        # Verlust nicht als Änderung gespeichert wird.
        self._capture_saved_baseline()
        # Overlay-Links nach dem Laden bauen (außerhalb des Suspend-Guards).
        self._rebuild_overlay_links()

    def _capture_saved_baseline(self) -> None:
        for sec in ("bizhawk", "obs", "remote", "player", "randomizer", "sprites", "overlay"):
            try:
                v = self._collect_for_save(sec)
                if v is not None:
                    self._last_saved[sec] = v
            except Exception:
                pass

    def selected_session(self) -> str:
        try:
            return os.path.basename(str(self.configsave).rstrip("/\\")) or "default"
        except Exception:
            return "default"

    @perf_span("settings.load_from_session")
    def load_from_session(self) -> None:
        """Session-Wechsel-Hook (von Screens._on_session_changed).

        Ist der Screen gerade nicht sichtbar, wird nur markiert und init_config
        lazy im nächsten on_pre_enter nachgeholt — der komplette Widget-Neubau
        lief sonst synchron im Session-Klick."""
        manager = self.manager
        if manager is not None and manager.current_screen is not self:
            self._config_dirty = True
            logger.debug("Settings load_from_session: Screen nicht sichtbar, init_config verzögert")
            return
        try:
            self.init_config()
            self._config_dirty = False
        except Exception as err:
            logger.error(f"Settings load_from_session fehlgeschlagen: {err}")
            logger.error(traceback.format_exc())

    @perf_span("settings.on_pre_enter")
    def on_pre_enter(self, *args) -> None:
        if self._config_dirty:
            # Verzögertes init_config nach Session-Wechsel; lädt sp ohnehin neu,
            # _reload_sprite_paths entfällt dann.
            try:
                self.init_config()
                self._config_dirty = False
            except Exception as err:
                logger.error(f"Settings init_config on_pre_enter fehlgeschlagen: {err}")
                logger.error(traceback.format_exc())
        else:
            # Sprite-Pfade neu aus sp laden — sp kann extern geändert worden sein
            # (z.B. durch den Erst-Start-SpriteSetupPopupMD, der die Pfade in-place
            # setzt). Ohne das würde der nächste Fokus-Verlust eines Sprite-Felds die
            # frisch geklonten Pfade mit den alten (leeren) Widget-Werten überschreiben.
            try:
                self._reload_sprite_paths()
            except Exception as err:
                logger.debug(f"reload_sprite_paths on_pre_enter failed: {err}")
        # Run-Historie beim Öffnen neu einlesen (wie Klassik on_enter).
        try:
            self._refresh_run_history()
        except Exception as err:
            logger.debug(f"refresh_run_history on_pre_enter failed: {err}")
        perf_log(f"settings.widgets={count_widgets(self)}")

    @perf_span("settings._reload_sprite_paths")
    def _reload_sprite_paths(self) -> None:
        sp = self.controller.load_sprites()
        prev = self._suspend_save
        self._suspend_save = True
        try:
            self._common_path.text = sp.get('common_path', '')
            self._items_path.text = sp.get('items_path', '')
            self._badges_path.text = sp.get('badges_path', '')
            # Auch Per-Game- und OBS-Felder aus sp auffrischen (apply_sprite_paths
            # setzt Edition-/OBS-Keys), sonst stale Werte + Clobber beim nächsten Save.
            # Nur Texte setzen statt Neubau: die Ausklapp-Flags ändern sich hier nicht
            # (nur init_config/Switches), der Neubau kostete bis ~1 s pro Betreten.
            self._refresh_sprite_field_texts(sp)
        finally:
            self._suspend_save = prev
        baseline = self._collect_for_save('sprites')
        if baseline is not None:
            self._last_saved['sprites'] = baseline

    @perf_span("settings._refresh_sprite_field_texts")
    def _refresh_sprite_field_texts(self, sp: dict) -> None:
        """Bestehende Per-Game-/OBS-Sprite-Felder mit Werten aus sp füllen —
        gleiche Quellen/Fallbacks wie _rebuild_game_expand/_rebuild_obs_expand."""
        for game_id, field in self._game_fields.items():
            field.text = sp.get(game_id.split('_', 1)[1], "")
        if self._obs_sprites_expanded and hasattr(self, "_common_obs_path"):
            self._common_obs_path.text = sp.get('common_obs_path', '') or sp.get('common_path', '')
            self._items_obs_path.text = sp.get('items_obs_path', '') or sp.get('items_path', '')
            self._badges_obs_path.text = sp.get('badges_obs_path', '') or sp.get('badges_path', '')
        for game_id, field in self._obs_game_fields.items():
            field.text = sp.get(game_id.split('_', 1)[1] + '_obs', "")
