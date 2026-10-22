"""First-Startup-Walkthrough (Phase F4, KivyMD).

Mehrstufiger Assistent, der beim allerersten Start durch die Grundkonfiguration
führt: Sprites herunterladen → Name/Spiel → BizHawk → OBS. Schreibt direkt in
die Standard-Session (``backend/config/default/``) — es wird KEINE benannte
Session angelegt (User-Entscheidung 2026-10-21); benannte Sessions legt der
User später im SessionMenu an.

Schritt-Tausch über einen Content-Holder (wie settingsmenumd / nuzlockemenu_md),
KEIN MDTabsCarousel (frisst Touches). Trigger + Marker (``app_state``) liegen im
Aufrufer (``appmd``), diese Klasse ruft am Ende nur ``on_complete``.
"""
from __future__ import annotations

import asyncio
import traceback
from typing import Any, Callable, Optional

from kivy.metrics import dp
from kivy.uix.widget import Widget
from kivymd.app import MDApp
from kivymd.uix.boxlayout import MDBoxLayout
from kivymd.uix.button import MDButton, MDButtonText
from kivymd.uix.label import MDLabel
from kivymd.uix.screen import MDScreen
from kivymd.uix.scrollview import MDScrollView
from kivymd.uix.textfield import MDTextField, MDTextFieldHintText

from backend.logging_setup import get_logger
from backend.sprite_repo import clone_sprite_repo, apply_sprite_paths
from frontend.widgets.file_picker_md import pick_directory, pick_file
from frontend.widgets.md_common import show_snackbar
from frontend.widgets.sessionsmenumd import GameNavigationBar, GameSelectionScreen

logger = get_logger(__name__, 'logs/frontend.log')


class OnboardingScreen(MDScreen):
    def __init__(self, configsave: Any, sp: dict, pl: dict, bh: dict, obs: dict,
                 app_version: Optional[str] = None,
                 on_complete: Optional[Callable[[], None]] = None, **kwargs) -> None:
        super().__init__(**kwargs)
        self.name = "Onboarding"
        self.configsave = configsave
        self.sp = sp
        self.pl = pl
        self.bh = bh
        self.obs = obs
        self.app_version = app_version
        self.on_complete = on_complete

        self._tasks: set[asyncio.Task] = set()
        self._selected_game = ""   # Anzeigename (edition_lut-Schlüssel)
        self._index = 0
        # Feld-Referenzen der Schritte. Werden beim Rendern des jeweiligen
        # Schritts gesetzt; vorher None, damit _commit_step bei (teilweise)
        # fehlgeschlagenem Build nicht mit AttributeError "Weiter" blockiert.
        self._name_field = None
        self._bh_path_field = None
        self._obs_host_field = None
        self._obs_port_field = None
        self._sprite_status = None
        self._cloning = False      # Doppelklick-Guard Sprite-Download

        # Schritt-Definitionen: (Titel, Builder). Builder liefert das Content-Widget.
        self._steps = [
            ("Willkommen", self._build_welcome),
            ("Sprites", self._build_sprites),
            ("Name & Spiel", self._build_name_game),
            ("BizHawk", self._build_bizhawk),
            ("OBS", self._build_obs),
            ("Fertig", self._build_done),
        ]

        root = MDBoxLayout(orientation="vertical", padding=dp(16), spacing=dp(12))

        self._title_label = MDLabel(font_style="Headline", role="small",
                                    adaptive_height=True)
        self._step_label = MDLabel(role="small", theme_text_color="Secondary",
                                   adaptive_height=True)
        root.add_widget(self._title_label)
        root.add_widget(self._step_label)

        # Scrollbarer Inhaltsbereich (Schritte können unterschiedlich hoch sein).
        self._scroll = MDScrollView(do_scroll_x=False)
        self._holder = MDBoxLayout(orientation="vertical", adaptive_height=True,
                                   spacing=dp(12), padding=(0, dp(4)))
        self._scroll.add_widget(self._holder)
        root.add_widget(self._scroll)

        # Navigations-Leiste.
        nav = MDBoxLayout(orientation="horizontal", size_hint_y=None, height=dp(48),
                          spacing=dp(8))
        self._back_btn = MDButton(MDButtonText(text="Zurück"), style="text",
                                  on_release=lambda *_: self._prev())
        self._skip_btn = MDButton(MDButtonText(text="Überspringen"), style="text",
                                  on_release=lambda *_: self._finish())
        self._next_btn = MDButton(MDButtonText(text="Weiter"), style="filled",
                                  on_release=lambda *_: self._next())
        nav.add_widget(self._back_btn)
        nav.add_widget(Widget())  # Spacer
        nav.add_widget(self._skip_btn)
        nav.add_widget(self._next_btn)
        root.add_widget(nav)

        self.add_widget(root)
        self._render_step()

    # --- Navigation ---------------------------------------------------------

    def _render_step(self) -> None:
        title, builder = self._steps[self._index]
        self._title_label.text = title
        self._step_label.text = f"Schritt {self._index + 1} von {len(self._steps)}"
        # Schritt-Feld-Refs zurücksetzen: der jeweilige Builder setzt nur seine
        # eigenen; so bleibt keine stale Referenz eines vorigen Schritts stehen
        # (und _commit_step blockiert nicht bei fehlgeschlagenem Build).
        self._name_field = None
        self._bh_path_field = None
        self._obs_host_field = None
        self._obs_port_field = None
        self._sprite_status = None
        self._holder.clear_widgets()
        try:
            self._holder.add_widget(builder())
        except Exception as err:
            logger.error(f"Onboarding-Schritt '{title}' Aufbau fehlgeschlagen: {err}")
            logger.error(traceback.format_exc())
            self._holder.add_widget(MDLabel(text="Dieser Schritt konnte nicht geladen werden.",
                                            adaptive_height=True))
        last = self._index == len(self._steps) - 1
        self._back_btn.disabled = self._index == 0
        self._next_btn.children[0].text = "Fertigstellen" if last else "Weiter"

    def _next(self) -> None:
        # Vor dem Weiterschalten den aktuellen Schritt speichern/validieren.
        if not self._commit_step():
            return
        if self._index >= len(self._steps) - 1:
            self._finish()
            return
        self._index += 1
        self._render_step()

    def _prev(self) -> None:
        if self._index > 0:
            self._index -= 1
            self._render_step()

    def _commit_step(self) -> bool:
        """Persistiert die Eingaben des aktuellen Schritts. Return False blockt
        das Weiterschalten (nur bei harter Validierung)."""
        title = self._steps[self._index][0]
        try:
            if title == "Name & Spiel":
                if self._name_field is None:
                    return True  # Build fehlgeschlagen — nicht blockieren
                name = (self._name_field.text or "").strip()
                if name and "_" in name:
                    show_snackbar("Der Name darf keinen '_' enthalten.", level='error')
                    return False
                self.pl["your_name"] = name
                if self._selected_game:
                    self.pl["session_game"] = self._selected_game
                self._save("player.yml", self.pl)
            elif title == "BizHawk":
                if self._bh_path_field is None:
                    return True
                self.bh["path"] = (self._bh_path_field.text or "").strip()
                self._save("bh_config.yml", self.bh)
            elif title == "OBS":
                if self._obs_host_field is None:
                    return True
                self.obs["host"] = (self._obs_host_field.text or "").strip() or "localhost"
                port = (self._obs_port_field.text or "").strip()
                if port:
                    self.obs["port"] = port
                self._save("obs_config.yml", self.obs)
        except Exception as err:
            logger.error(f"Onboarding-Schritt '{title}' speichern fehlgeschlagen: {err}")
            logger.error(traceback.format_exc())
            show_snackbar("Speichern fehlgeschlagen, siehe Log.", level='error')
            return False
        return True

    def _finish(self) -> None:
        # Letzten bearbeiteten Schritt noch sichern (best effort), dann Marker/Nav
        # im Aufrufer.
        self._commit_step()
        if self.on_complete is not None:
            try:
                self.on_complete()
            except Exception as err:
                logger.error(f"Onboarding on_complete fehlgeschlagen: {err}")
                logger.error(traceback.format_exc())

    def _save(self, filename: str, data: dict) -> None:
        app = MDApp.get_running_app()
        path = f"{self.configsave}{filename}"
        if app is not None and hasattr(app, "save_config"):
            app.save_config(path, data)
        else:  # Fallback (z.B. Test ohne App)
            import yaml
            with open(path, "w", encoding="utf-8") as f:
                yaml.dump(data, f)

    # --- Schritt-Builder ----------------------------------------------------

    def _build_welcome(self) -> MDBoxLayout:
        box = MDBoxLayout(orientation="vertical", adaptive_height=True, spacing=dp(8))
        box.add_widget(MDLabel(
            text="Willkommen beim Pokémon OBS Tracker!",
            font_style="Title", role="small", adaptive_height=True))
        box.add_widget(MDLabel(
            text=("Dieser kurze Assistent richtet die Grundeinstellungen ein: "
                  "die Sprite-Grafiken, deinen Namen und dein Spiel sowie optional "
                  "BizHawk und OBS. Du kannst jeden Schritt überspringen und später "
                  "unter Einstellungen ändern."),
            adaptive_height=True))
        return box

    def _build_sprites(self) -> MDBoxLayout:
        box = MDBoxLayout(orientation="vertical", adaptive_height=True, spacing=dp(8))
        box.add_widget(MDLabel(
            text=("Die Sprite-Grafiken werden von GitHub geladen. Wähle einen "
                  "Zielordner — vorgeschlagen ist 'sprites' neben der Anwendung."),
            adaptive_height=True))
        current = self.sp.get("common_path") or ""
        self._sprite_status = MDLabel(
            text=(f"Aktuell: {current}" if current else "Noch keine Sprites eingerichtet."),
            role="small", theme_text_color="Secondary", adaptive_height=True)
        box.add_widget(MDButton(
            MDButtonText(text="Sprites herunterladen"), style="tonal",
            on_release=lambda *_: self._pick_sprite_dir()))
        box.add_widget(self._sprite_status)
        return box

    def _pick_sprite_dir(self) -> None:
        import os
        pick_directory(
            on_select=self._start_clone,
            start_path=os.path.abspath("."),
            title="Zielordner für Sprite-Repository wählen",
        )

    def _start_clone(self, target: str) -> None:
        import os
        if not target:
            return
        # Doppelklick-Guard: kein zweiter paralleler Clone.
        if self._cloning:
            show_snackbar("Sprite-Download läuft bereits …", level='info', duration=2.0)
            return
        clone_target = os.path.join(target, "sprites")
        if os.path.exists(clone_target) and os.listdir(clone_target):
            show_snackbar(f"Ordner existiert bereits: {clone_target}", level='error', duration=4.0)
            return
        self._cloning = True
        if self._sprite_status is not None:
            self._sprite_status.text = "Sprites werden geladen …"
        task = asyncio.create_task(self._do_clone(clone_target))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _do_clone(self, clone_target: str) -> None:
        def _status(text: str) -> None:
            # Status-Label kann weg sein, wenn der Schritt zwischenzeitlich neu
            # gebaut wurde — dann nur den aktuellen setzen, falls vorhanden.
            if self._sprite_status is not None:
                self._sprite_status.text = text
        try:
            loop = asyncio.get_running_loop()
            success = await loop.run_in_executor(None, clone_sprite_repo, clone_target)
            if success:
                apply_sprite_paths(self.sp, clone_target)
                self._save("sprites.yml", self.sp)
                _status(f"Sprites geladen: {clone_target}")
                show_snackbar("Sprites erfolgreich geladen", level='success', duration=4.0)
            else:
                _status("Download fehlgeschlagen (siehe Log).")
                show_snackbar("Sprite-Download fehlgeschlagen. Siehe Log.", level='error', duration=4.0)
        except Exception as err:
            logger.error(f"Onboarding Sprite-Clone fehlgeschlagen: {err}")
            logger.error(traceback.format_exc())
            _status("Unerwarteter Fehler (siehe Log).")
            show_snackbar("Unerwarteter Fehler beim Sprite-Download. Siehe Log.", level='error', duration=4.0)
        finally:
            self._cloning = False

    def _build_name_game(self) -> MDBoxLayout:
        box = MDBoxLayout(orientation="vertical", adaptive_height=True, spacing=dp(12))
        self._name_field = MDTextField(
            MDTextFieldHintText(text="Dein Name (für die Anzeige)"),
            mode="outlined", size_hint_x=0.8, pos_hint={"center_x": 0.5},
            text=self.pl.get("your_name", "") or "",
        )
        box.add_widget(self._name_field)
        box.add_widget(MDLabel(text="Wähle dein Spiel:", role="small",
                               theme_text_color="Secondary", adaptive_height=True))
        # Bereits gewähltes Spiel anzeigen (der Cover-Grid wird bei jedem Betreten
        # neu gebaut und zeigt das Highlight nicht erneut, daher Text-Rückmeldung).
        preset = self._selected_game or self.pl.get("session_game") or ""
        self._game_choice_label = MDLabel(
            text=(f"Gewählt: {preset}" if preset else "Noch kein Spiel gewählt."),
            role="small", adaptive_height=True)
        box.add_widget(self._game_choice_label)

        # Cover-Picker aus dem SessionMenu wiederverwenden.
        self._game_selection = GameSelectionScreen(self._on_game_selected)
        self._game_selection.size_hint_y = None
        self._game_selection.height = dp(300)
        item_list = [{"screen_name": gen} for gen in self._game_selection.games]
        navbar = GameNavigationBar(self._game_selection, item_list,
                                   size_hint_y=None, height=dp(56))
        picker = MDBoxLayout(orientation="vertical", size_hint_y=None,
                             height=dp(360), spacing=dp(4))
        picker.add_widget(navbar)
        picker.add_widget(self._game_selection)
        box.add_widget(picker)
        # Bereits gewählt? Anzeige zurückspiegeln (Text, Karten-Highlight folgt
        # erst beim erneuten Klick — bewusst schlicht gehalten).
        return box

    def _on_game_selected(self, card) -> None:
        self._selected_game = card.game_display
        label = getattr(self, "_game_choice_label", None)
        if label is not None:
            label.text = f"Gewählt: {card.game_display}"
        show_snackbar(f"Spiel gewählt: {card.game_display}", level='info', duration=2.0)

    def _build_bizhawk(self) -> MDBoxLayout:
        box = MDBoxLayout(orientation="vertical", adaptive_height=True, spacing=dp(8))
        box.add_widget(MDLabel(
            text=("Optional: Pfad zur BizHawk-Anwendung (EmuHawk.exe) für lokales "
                  "Tracking. Kannst du auch später in den Einstellungen setzen."),
            adaptive_height=True))
        self._bh_path_field = MDTextField(
            MDTextFieldHintText(text="Pfad zu EmuHawk.exe"),
            mode="outlined", size_hint_x=1,
            text=self.bh.get("path", "") or "",
        )
        box.add_widget(self._bh_path_field)
        box.add_widget(MDButton(
            MDButtonText(text="Durchsuchen …"), style="tonal",
            on_release=lambda *_: self._pick_bizhawk()))
        return box

    def _pick_bizhawk(self) -> None:
        # Der File-Manager ist ein Overlay und kann nach Zurück/Weiter noch
        # offen sein, während _render_step das Feld schon auf None gesetzt hat.
        if self._bh_path_field is None:
            return
        import os
        pick_file(
            on_select=self._set_bizhawk_path,
            start_path=os.path.dirname(self._bh_path_field.text) or os.path.abspath("."),
            filters=["*.exe"],
            title="BizHawk-Anwendung wählen",
        )

    def _set_bizhawk_path(self, path: str) -> None:
        if self._bh_path_field is None:
            return
        if path:
            self._bh_path_field.text = path

    def _build_obs(self) -> MDBoxLayout:
        box = MDBoxLayout(orientation="vertical", adaptive_height=True, spacing=dp(8))
        box.add_widget(MDLabel(
            text=("Optional: OBS-WebSocket-Verbindung. Standard ist localhost:4455. "
                  "Das Passwort setzt du bei Bedarf später in den Einstellungen."),
            adaptive_height=True))
        self._obs_host_field = MDTextField(
            MDTextFieldHintText(text="OBS Host"),
            mode="outlined", size_hint_x=1,
            text=self.obs.get("host", "localhost") or "localhost",
        )
        self._obs_port_field = MDTextField(
            MDTextFieldHintText(text="OBS Port"),
            mode="outlined", size_hint_x=0.4,
            text=str(self.obs.get("port", "4455") or "4455"),
        )
        box.add_widget(self._obs_host_field)
        box.add_widget(self._obs_port_field)
        return box

    def _build_done(self) -> MDBoxLayout:
        box = MDBoxLayout(orientation="vertical", adaptive_height=True, spacing=dp(8))
        name = self.pl.get("your_name") or "—"
        game = self.pl.get("session_game") or self._selected_game or "—"
        sprites = self.sp.get("common_path") or "—"
        box.add_widget(MDLabel(text="Fertig!", font_style="Title", role="small",
                               adaptive_height=True))
        box.add_widget(MDLabel(
            text=(f"Name: {name}\nSpiel: {game}\nSprites: {sprites}\n\n"
                  "Du kannst alle Einstellungen jederzeit unter 'Settings' ändern "
                  "und im 'Sessions'-Menü weitere Spiele/Runs anlegen."),
            adaptive_height=True))
        return box
