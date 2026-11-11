"""KivyMD-Friedhof-Screen. Aufbereitung liegt in backend/graveyard_view.

Chronik aller Tode (älteste oben), Soullink-Gruppen als gemeinsame Karte,
Filter nach Projekt/Spieler/Edition und CSV-Export. Daten kommen blockierend aus
SQLite (Munchlax.snapshot_graves) und werden per Executor geladen.
MDScrollView + Boxen statt RecycleView (siehe Memory reference_recycleview_screenmanager).
"""
import asyncio
import os
import traceback
from pathlib import Path
from types import SimpleNamespace

from kivy.clock import Clock
from kivy.core.clipboard import Clipboard
from kivy.metrics import dp
from kivymd.uix.boxlayout import MDBoxLayout
from kivymd.uix.button import MDButton, MDButtonIcon, MDButtonText
from kivymd.uix.fitimage import FitImage
from kivymd.uix.label import MDIcon, MDLabel
from kivymd.uix.menu import MDDropdownMenu
from kivymd.uix.screen import MDScreen
from kivymd.uix.scrollview import MDScrollView

from backend.bag_view import edition_name
from backend.graveyard_view import (
    default_export_name, export_csv, filter_graves, filter_options, grave_row, group_graves,
)
from backend.logging_setup import get_logger
from backend.pokedex_view import load_species_de
from frontend.widgets.md_common import bind_theme_color, show_snackbar

logger = get_logger(__name__, 'logs/graveyardmenu.log')

SPRITE_WIDTH = dp(56)
ROW_HEIGHT = dp(64)
SCOPE_CURRENT = "__current__"
SCOPE_ALL = "__all__"
EMPTY_TEXT = "Noch keine Tode — gut so!"


def _label(text: str, height: float, bold: bool = False, small: bool = True,
           secondary: bool = False) -> MDLabel:
    lbl = MDLabel(text=text, bold=bold, size_hint_y=None, height=height, halign="left",
                  valign="center", shorten=True, shorten_from="right")
    if small:
        lbl.role = "small"
    if secondary:
        bind_theme_color(lbl, "text_color", "onSurfaceVariantColor")
        lbl.theme_text_color = "Custom"
    # shorten braucht eine feste Textbreite, sonst wird nicht gekürzt
    lbl.bind(width=lambda w, v: setattr(w, "text_size", (v, None)))
    return lbl


class GraveRow(MDBoxLayout):
    """Ein Grab: Sprite links, drei Textzeilen rechts."""

    def __init__(self, info: dict, sprite_source: str = "", **kwargs):
        super().__init__(orientation="horizontal", size_hint_y=None, height=ROW_HEIGHT,
                         spacing=dp(8), **kwargs)
        self.info = info
        counted = info["counted"]
        if not counted:
            # Soullink-Partner zählt nicht zum Todescounter → dezent
            self.opacity = 0.65
        # Ohne Quelle zeichnet FitImage ein weißes Feld → leerer Platzhalter
        if sprite_source:
            self.add_widget(FitImage(source=sprite_source, fit_mode="contain",
                                     size_hint=(None, 1), width=SPRITE_WIDTH))
        else:
            self.add_widget(MDBoxLayout(size_hint_x=None, width=SPRITE_WIDTH))

        text_box = MDBoxLayout(orientation="vertical", spacing=dp(1))
        title_row = MDBoxLayout(orientation="horizontal", size_hint_y=None, height=dp(22),
                                spacing=dp(4))
        title_row.add_widget(_label(info["title"], dp(22), bold=True, small=False))
        if info["shiny"]:
            # Shiny-Marker als Icon (Roboto hat kein Stern-Glyph)
            title_row.add_widget(MDIcon(icon="star", icon_color=(0.95, 0.75, 0.1, 1),
                                        size_hint=(None, None), size=(dp(18), dp(18)),
                                        pos_hint={"center_y": .5}))
        text_box.add_widget(title_row)
        line2 = " · ".join(p for p in (info["owner"], info["level"], info["place"]) if p)
        text_box.add_widget(_label(line2, dp(18)))
        cause = info["cause"] if counted else f"{info['cause']} (zählt nicht)"
        line3 = " · ".join(p for p in (cause, info["date"], info["edition"]) if p)
        text_box.add_widget(_label(line3, dp(18), secondary=True))
        self.add_widget(text_box)


class GraveGroupCard(MDBoxLayout):
    """Karte pro Gruppe (Einzeltod oder Soullink-Gruppe)."""

    def __init__(self, rows: list[GraveRow], soullink: bool, **kwargs):
        super().__init__(orientation="vertical", size_hint_y=None, padding=dp(10),
                         spacing=dp(6), radius=[dp(12)] * 4, adaptive_height=True, **kwargs)
        bind_theme_color(self, "md_bg_color", "surfaceContainerColor")
        if soullink:
            head = _label("Soullink-Gruppe", dp(20), bold=True)
            bind_theme_color(head, "text_color", "primaryColor")
            head.theme_text_color = "Custom"
            self.add_widget(head)
        for row in rows:
            self.add_widget(row)


class GraveyardMenu(MDScreen):
    def __init__(self, munchlax=None, obs_websocket=None, **kwargs):
        super().__init__(**kwargs)
        self.name = "GraveyardMenu"
        self.munchlax = munchlax
        self.obs_websocket = obs_websocket
        self._species_de: dict = {}
        self._graves: list[dict] = []
        self._sessions: list[str] = []
        self._current_session: str = ""
        self._scope: str = SCOPE_CURRENT
        self._owner: str | None = None
        self._edition: int | None = None
        self._dirty: bool = True
        # Debounce: Serie von Toden (Soullink-Partner) → ein Rebuild
        self._reload_ev = None
        self._load_gen: int = 0
        self._tasks: set[asyncio.Task] = set()
        self._menus: list[MDDropdownMenu] = []
        self._sprite_cache: dict[tuple, str] = {}
        self._export_running: bool = False

        root = MDBoxLayout(orientation="vertical", padding="10dp", spacing="8dp")
        header = MDBoxLayout(orientation="horizontal", size_hint_y=None, height="48dp", spacing="10dp")
        self.head_label = MDLabel(text="Friedhof", role="large", valign="center",
                                  size_hint_min_x=dp(110))
        header.add_widget(self.head_label)
        self.count_label = MDLabel(text="", valign="center", halign="right")
        header.add_widget(self.count_label)
        self.export_button = MDButton(
            MDButtonIcon(icon="file-export-outline"), MDButtonText(text="CSV exportieren"),
            style="tonal", pos_hint={"center_y": .5}, on_release=lambda _i: self._start_export())
        header.add_widget(self.export_button)
        root.add_widget(header)

        filter_bar = MDBoxLayout(orientation="horizontal", size_hint_y=None, height="56dp", spacing="10dp")
        self.scope_text = MDButtonText(text="Projekt: Aktuelle Session")
        self.scope_button = MDButton(self.scope_text, style="tonal", pos_hint={"center_y": .5},
                                     on_release=lambda i: self._open_scope_menu(i))
        filter_bar.add_widget(self.scope_button)
        self.owner_text = MDButtonText(text="Spieler: Alle")
        self.owner_button = MDButton(self.owner_text, style="tonal", pos_hint={"center_y": .5},
                                     on_release=lambda i: self._open_owner_menu(i))
        filter_bar.add_widget(self.owner_button)
        self.edition_text = MDButtonText(text="Edition: Alle")
        self.edition_button = MDButton(self.edition_text, style="tonal", pos_hint={"center_y": .5},
                                       on_release=lambda i: self._open_edition_menu(i))
        filter_bar.add_widget(self.edition_button)
        filter_bar.add_widget(MDBoxLayout())  # Füller
        root.add_widget(filter_bar)

        self.empty_hint = MDLabel(text="", size_hint_y=None, height=0, opacity=0, halign="center")
        root.add_widget(self.empty_hint)

        scroll = MDScrollView(do_scroll_x=False)
        self.cards_box = MDBoxLayout(orientation="vertical", size_hint_y=None, spacing=dp(8),
                                     padding=dp(8))
        self.cards_box.bind(minimum_height=self.cards_box.setter("height"))
        scroll.add_widget(self.cards_box)
        root.add_widget(scroll)
        self.add_widget(root)

        # Tod-Ereignisse live anzeigen (Callback kann aus dem Munchlax-Thread kommen)
        if self.munchlax is not None and hasattr(self.munchlax, "add_state_listener"):
            self.munchlax.add_state_listener(self._on_state_event)

    # --- Lebenszyklus ---------------------------------------------------------

    def on_pre_enter(self, *args) -> None:
        # Nur neu laden, wenn sich seit dem letzten Laden etwas geändert hat
        # (Tod/Session-Wechsel setzen _dirty) — sonst kompletter Karten-
        # Neubau bei jedem Screen-Wechsel
        if self._dirty:
            self._reload()

    def load_from_session(self) -> None:
        """Session-Wechsel: Filter zurücksetzen, nur bei Sichtbarkeit neu laden."""
        self._scope = SCOPE_CURRENT
        self._owner = None
        self._edition = None
        self._graves = []
        self._dirty = True
        # Sprite-Pfade hängen an der Session (sprites.yml)
        self._sprite_cache.clear()
        self._update_filter_texts()
        if self._is_visible():
            self._reload()

    def _is_visible(self) -> bool:
        return self.manager is not None and self.manager.current == self.name

    def _on_state_event(self, reason: str) -> None:
        if reason != "graveyard":
            return
        # Thread-sicher auf den Kivy-Thread
        Clock.schedule_once(lambda _dt: self._handle_graveyard_event(), 0)

    def _handle_graveyard_event(self) -> None:
        self._dirty = True
        if not self._is_visible():
            return
        if self._reload_ev is not None:
            self._reload_ev.cancel()
        self._reload_ev = Clock.schedule_once(self._debounced_reload, 0.3)

    def _debounced_reload(self, _dt) -> None:
        self._reload_ev = None
        if self._is_visible():
            self._reload()

    # --- Laden ----------------------------------------------------------------

    def _spawn(self, coro) -> None:
        task = asyncio.get_running_loop().create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def _reload(self) -> None:
        if self.munchlax is None:
            self._graves = []
            self._render()
            return
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            logger.warning("Friedhof: kein laufender Event-Loop, Reload übersprungen")
            return
        self._load_gen += 1
        self._spawn(self._load(self._load_gen, self._scope))

    def _load_blocking(self, scope: str) -> tuple[list[str], list[dict], str]:
        current = self.munchlax.graveyard_session_name()
        sessions = self.munchlax.graveyard_sessions()
        if scope == SCOPE_ALL:
            graves = self.munchlax.snapshot_graves(None)
        elif scope == SCOPE_CURRENT:
            graves = self.munchlax.snapshot_graves(current)
        else:
            graves = self.munchlax.snapshot_graves(scope)
        return list(sessions or []), list(graves or []), current or ""

    async def _load(self, gen: int, scope: str) -> None:
        try:
            if not self._species_de:
                self._species_de = load_species_de()
            loop = asyncio.get_running_loop()
            sessions, graves, current = await loop.run_in_executor(None, self._load_blocking, scope)
            if gen != self._load_gen:
                return  # neuerer Reload läuft
            self._sessions, self._graves, self._current_session = sessions, graves, current
            self._dirty = False
            self._render()
        except Exception as err:
            logger.error(f"Friedhof laden fehlgeschlagen: {err}")
            logger.error(traceback.format_exc())

    # --- Filter ---------------------------------------------------------------

    def _open_menu(self, caller, entries: list[tuple[str, object]], on_pick) -> None:
        items = [{"text": text, "on_release": (lambda v=value, t=text: self._menu_pick(on_pick, v, t))}
                 for text, value in entries]
        menu = MDDropdownMenu(caller=caller, items=items)
        self._menus = [menu]
        menu.open()

    def _menu_pick(self, on_pick, value, text) -> None:
        for m in self._menus:
            m.dismiss()
        on_pick(value, text)

    def _open_scope_menu(self, caller) -> None:
        entries: list[tuple[str, object]] = [("Aktuelle Session", SCOPE_CURRENT),
                                             ("Alle Projekte", SCOPE_ALL)]
        entries += [(name, name) for name in self._sessions]
        self._open_menu(caller, entries, self._pick_scope)

    def _open_owner_menu(self, caller) -> None:
        owners = filter_options(self._graves)["owners"]
        self._open_menu(caller, [("Alle", None)] + [(o, o) for o in owners], self._pick_owner)

    def _open_edition_menu(self, caller) -> None:
        editions = filter_options(self._graves)["editions"]
        entries: list[tuple[str, object]] = [("Alle", None)]
        entries += [(edition_name(e) or str(e), e) for e in editions]
        self._open_menu(caller, entries, self._pick_edition)

    def _pick_scope(self, value, _text) -> None:
        self._scope = value
        # Spieler-/Edition-Filter gelten nur im alten Datenbestand
        self._owner = None
        self._edition = None
        self._update_filter_texts()
        self._reload()

    def _pick_owner(self, value, _text) -> None:
        self._owner = value
        self._update_filter_texts()
        self._render()

    def _pick_edition(self, value, _text) -> None:
        self._edition = value
        self._update_filter_texts()
        self._render()

    def _scope_label(self) -> str:
        if self._scope == SCOPE_CURRENT:
            return "Aktuelle Session"
        if self._scope == SCOPE_ALL:
            return "Alle Projekte"
        return self._scope

    def _update_filter_texts(self) -> None:
        self.scope_text.text = f"Projekt: {self._scope_label()}"
        self.owner_text.text = f"Spieler: {self._owner or 'Alle'}"
        ed = "Alle" if self._edition is None else (edition_name(self._edition) or str(self._edition))
        self.edition_text.text = f"Edition: {ed}"

    def _filtered(self) -> list[dict]:
        return filter_graves(self._graves, self._owner, self._edition)

    # --- Rendering ------------------------------------------------------------

    def _sprite_source(self, g: dict) -> str:
        """Sprite-Pfad via OBS.get_sprite; leer bei Fehlern/ohne Sprite-Repo (gecacht)."""
        if self.obs_websocket is None:
            return ""
        female = bool(g.get("female"))
        form = g.get("form") or ""
        key = (g.get("dexnr"), g.get("edition"), bool(g.get("shiny")), female, form)
        if key in self._sprite_cache:
            return self._sprite_cache[key]
        try:
            dexnr = int(g.get("dexnr"))
            if dexnr <= 0:
                source = ""
            else:
                shim = SimpleNamespace(shiny=bool(g.get("shiny")), female=female,
                                       dexnr=dexnr, form=form)
                source = self.obs_websocket.get_sprite(shim, False, int(g.get("edition"))) or ""
                if source and not os.path.exists(source):
                    source = ""
        except Exception as err:
            logger.debug(f"Friedhof Sprite-Pfad fehlgeschlagen: {type(err)},{err}")
            source = ""
        self._sprite_cache[key] = source
        return source

    def _render(self) -> None:
        # Filterwerte, die im neuen Datenbestand nicht mehr existieren, verwerfen
        opts = filter_options(self._graves)
        if self._owner is not None and self._owner not in opts["owners"]:
            self._owner = None
        if self._edition is not None and self._edition not in opts["editions"]:
            self._edition = None
        self._update_filter_texts()

        graves = self._filtered()
        groups = group_graves(graves)
        self.cards_box.clear_widgets()
        self.count_label.text = f"{len(graves)} Einträge"
        if not groups:
            self.empty_hint.text = EMPTY_TEXT
            self.empty_hint.height = dp(48)
            self.empty_hint.opacity = 1
            return
        self.empty_hint.text = ""
        self.empty_hint.height = 0
        self.empty_hint.opacity = 0
        for group in groups:
            rows = [GraveRow(grave_row(g, self._species_de), self._sprite_source(g))
                    for g in group["graves"]]
            self.cards_box.add_widget(GraveGroupCard(rows, soullink=group["link_id"] is not None))

    # --- CSV-Export -----------------------------------------------------------

    def _start_export(self) -> None:
        if self._export_running:
            return
        graves = self._filtered()
        if not graves:
            show_snackbar("Nichts zu exportieren", "warning")
            return
        if self._scope == SCOPE_CURRENT:
            project = self._current_session or None
        elif self._scope == SCOPE_ALL:
            project = None
        else:
            project = self._scope
        self._export_running = True
        try:
            self._spawn(self._export(graves, project))
        except Exception:
            self._export_running = False
            raise

    async def _export(self, graves: list[dict], project: str | None) -> None:
        try:
            # cwd = EXE-Verzeichnis (main.py chdir, portable Layout), wie config_backups/
            path = Path(os.getcwd()) / "exports" / default_export_name(project)
            loop = asyncio.get_running_loop()
            result = await loop.run_in_executor(None, export_csv, graves, path, self._species_de)
            logger.info(f"Friedhof-CSV exportiert: {result} ({len(graves)} Einträge)")
            try:
                Clipboard.copy(str(result))
            except Exception as clip_err:
                # Datei ist geschrieben — Zwischenablage-Fehler nicht als Export-Fehler melden
                logger.warning(f"Friedhof-Export: Pfad nicht in Zwischenablage: {clip_err}")
            show_snackbar(f"Exportiert: {result}", "success")
        except Exception as err:
            logger.error(f"Friedhof-CSV-Export fehlgeschlagen: {err}")
            logger.error(traceback.format_exc())
            show_snackbar(f"Export fehlgeschlagen: {err}", "error")
        finally:
            self._export_running = False
