"""KivyMD-Pokedex-Screen. Filter-/Formatlogik liegt in backend/pokedex_view."""
import asyncio
import os
import traceback
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace

from kivy.clock import Clock
from kivy.metrics import dp
from kivymd.app import MDApp
from kivymd.uix.boxlayout import MDBoxLayout
from kivymd.uix.button import MDButton, MDButtonText
from kivymd.uix.card import MDCard
from kivymd.uix.fitimage import FitImage
from kivymd.uix.gridlayout import MDGridLayout
from kivymd.uix.label import MDLabel
from kivymd.uix.screen import MDScreen
from kivymd.uix.scrollview import MDScrollView
from kivymd.uix.selectioncontrol import MDSwitch
from kivymd.uix.textfield import MDTextField, MDTextFieldHintText

from backend.classes.pokedex_db import PokedexDB
from backend.logging_setup import get_logger
from backend.player_colors import PLAYER_COLORS, player_rgba
from backend.pokedex_view import (
    filter_rows, item_name, load_species_de, location_name, row_slot, species_name,
)
from backend.tm_type_resolver import resolve_tm_hm_sprite
from frontend.widgets.md_common import show_snackbar

logger = get_logger(__name__, './logs/pokedexmenu.log')

# Horizontale Karte: Sprite dp(72) links, rechts bis zu 5 Textzeilen à dp(18)
# + Padding. Mindestbreite pro Karte für die responsive Spaltenzahl.
CARD_HEIGHT = dp(104)
CARD_MIN_WIDTH = dp(240)
# Karten pro Render-Tick (P5: batched rendern, damit der Screen-Wechsel nicht
# ruckelt, während alle Karten auf einmal gebaut werden).
RENDER_BATCH = 12


class PokemonCard(MDCard):
    """Anklickbare Pokemon-Karte (horizontal): Farb-Akzent, Sprite, Infos, Owner."""

    def __init__(self, row: dict, species_de: dict, sprite_source: str = "",
                 slot: int | None = None, player_name: str = "", show_player: bool = False,
                 item_sprite: str = "", on_open: Callable[[dict], None] | None = None, **kwargs):
        super().__init__(style="elevated", ripple_behavior=True, orientation="horizontal",
                         size_hint_y=None, height=CARD_HEIGHT, padding=0, spacing=dp(8),
                         radius=[dp(12)] * 4, **kwargs)
        self.row = row
        self._on_open = on_open

        accent = player_rgba(slot, alpha=1.0) if slot is not None else (0.5, 0.5, 0.5, 1.0)
        # Dünner Farbstreifen links zur Spieler-Unterscheidung (P1). Volle Deckung,
        # damit er in hellem wie dunklem Theme trägt.
        self.add_widget(MDBoxLayout(size_hint_x=None, width=dp(6), md_bg_color=accent,
                                    radius=[dp(12), 0, 0, dp(12)]))
        # Dezente Tönung der ganzen Karte in Spielerfarbe (über dem Theme-Hintergrund).
        if slot is not None:
            self.md_bg_color = player_rgba(slot, alpha=0.12)

        self.add_widget(FitImage(source=sprite_source or "", fit_mode="contain",
                                 size_hint=(None, 1), width=dp(72)))

        text_box = MDBoxLayout(orientation="vertical", padding=(0, dp(6), dp(8), dp(6)), spacing=dp(1))

        species = species_name(str(row.get("dexnr", "")), species_de)
        title = str(row.get("nickname") or "") or species
        if row.get("shiny"):
            title = f"{title} (Shiny)"

        if show_player and slot is not None:
            text_box.add_widget(self._player_label(slot, player_name))

        text_box.add_widget(self._label(title, bold=True, height=dp(22)))

        edition = self._edition(row)
        lvl = str(row.get("lvl") or "") or "–"
        text_box.add_widget(self._label(f"Lv. {lvl}", height=dp(18), small=True))
        text_box.add_widget(self._item_line(self._item_text(row, edition), item_sprite))
        text_box.add_widget(self._label(f"Route: {self._route_text(row, edition)}", height=dp(18), small=True))

        self.add_widget(text_box)
        self.bind(on_release=self._handle_release)

    @staticmethod
    def _label(text: str, height: float, bold: bool = False, small: bool = False) -> MDLabel:
        lbl = MDLabel(text=text, bold=bold, size_hint_y=None, height=height,
                      halign="left", valign="center", shorten=True, shorten_from="right")
        if small:
            lbl.role = "small"
        # shorten braucht eine feste Textbreite, sonst wird nicht gekürzt
        lbl.bind(width=lambda w, v: setattr(w, "text_size", (v, None)))
        return lbl

    @staticmethod
    def _player_label(slot: int, name: str):
        """Spieler-Zeile: Slot-Nummer in Spielerfarbe + Trainer-Name in Weiß."""
        hexcol = PLAYER_COLORS.get(slot, "7f7f7f7f")
        text = f"[b][color={hexcol}]{slot}[/color][/b]"
        if name:
            text += f"  [color=ffffffff]{name}[/color]"
        lbl = MDLabel(text=text, markup=True, size_hint_y=None, height=dp(16),
                      halign="left", valign="center", shorten=True, shorten_from="right")
        lbl.role = "small"
        lbl.bind(width=lambda w, v: setattr(w, "text_size", (v, None)))
        return lbl

    def _item_line(self, text: str, sprite: str):
        """Item-Zeile: mit Sprite-Icon (wenn vorhanden) + Name, sonst 'Item: …'."""
        if not sprite:
            return self._label(f"Item: {text}", height=dp(18), small=True)
        box = MDBoxLayout(orientation="horizontal", size_hint_y=None, height=dp(18), spacing=dp(4))
        box.add_widget(FitImage(source=sprite, size_hint=(None, 1), width=dp(16), fit_mode="contain"))
        box.add_widget(self._label(text, height=dp(18), small=True))
        return box

    @staticmethod
    def _edition(row: dict) -> int | None:
        try:
            return int(row.get("edition"))
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _item_text(row: dict, edition: int | None) -> str:
        raw = row.get("item")
        if edition is None:
            return str(raw) if raw else "–"
        return item_name(raw, edition) or "–"

    @staticmethod
    def _route_text(row: dict, edition: int | None) -> str:
        raw = row.get("route")
        if raw is None or raw == "":
            return "–"
        if edition is None:
            return str(raw)
        return location_name(raw, edition)

    def _handle_release(self, *_args) -> None:
        if self._on_open is not None:
            self._on_open(self.row)


class PokedexMenu(MDScreen):
    def __init__(self, configsave, obs_websocket, app_version, pl=None, **kwargs):
        super().__init__(**kwargs)
        self.name = "PokedexMenu"
        self.configsave = configsave
        self.obs_websocket = obs_websocket
        self.app_version = app_version
        self.pl = pl if pl is not None else {}

        self._all_rows: list[dict] = []
        self._species_de: dict = load_species_de()
        self._active_slot: str = ""          # "" = alle Spieler
        self._sprite_cache: dict[tuple, str] = {}
        self._item_sprite_cache: dict[tuple, str] = {}
        self._needs_reload: bool = True       # Dirty-Flag (P5): nur bei Bedarf neu laden
        self._load_task: asyncio.Task | None = None
        self._reload_pending: bool = False    # Reload-Wunsch während laufendem Load
        self._load_gen: int = 0               # Generation-Counter: verwirft Ergebnis veralteter Loads
        self._render_event = None             # laufender batched-Render-Trigger
        self._pending_rows: list[dict] = []
        self._render_show_player: bool = False
        self._player_buttons: dict[str, MDButton] = {}

        root = MDBoxLayout(orientation="vertical", padding="10dp", spacing="8dp")

        header = MDBoxLayout(orientation="horizontal", size_hint_y=None, height="48dp", spacing="10dp")
        self.head_label = MDLabel(text="Pokemon-Datenbank", role="large", valign="center")
        header.add_widget(self.head_label)
        self.refresh_button = MDButton(
            MDButtonText(text="Aktualisieren"), style="tonal",
            pos_hint={"center_y": .5}, on_release=lambda _i: self._force_reload(),
        )
        header.add_widget(self.refresh_button)
        root.add_widget(header)

        filter_bar = MDBoxLayout(orientation="horizontal", size_hint_y=None, height="56dp", spacing="12dp")
        self.search_input = MDTextField(
            MDTextFieldHintText(text="Name, Spitzname, Edition …"),
            mode="outlined", size_hint_x=0.4,
        )
        # Debounce: Filter erst 0.15s nach dem letzten Tastendruck neu aufbauen,
        # sonst wird bei jedem Zeichen die ganze Zeilenliste neu gebaut.
        self._filter_trigger = Clock.create_trigger(lambda _dt: self._apply_filters(), 0.15)
        self.search_input.bind(text=lambda _i, _v: self._filter_trigger())
        filter_bar.add_widget(self.search_input)

        filter_bar.add_widget(MDLabel(text="nur Shinys", size_hint_x=None, width="90dp", valign="center"))
        self.shiny_switch = MDSwitch(pos_hint={"center_y": .5})
        self.shiny_switch.bind(active=lambda _i, _v: self._filter_trigger())
        filter_bar.add_widget(self.shiny_switch)

        self.count_label = MDLabel(text="0 Einträge", valign="center", halign="right")
        filter_bar.add_widget(self.count_label)
        root.add_widget(filter_bar)

        # Spieler-Auswahl (P3): nur bei Mehrspieler-Session. Setzt denselben
        # Slot-Filter, den früher das freie 1–4-Textfeld bediente.
        self.player_bar = MDBoxLayout(orientation="horizontal", size_hint_y=None, height="0dp",
                                      spacing="8dp", opacity=0)
        root.add_widget(self.player_bar)
        self._build_player_buttons()

        self.empty_hint = MDLabel(text="", size_hint_y=None, height=0, opacity=0, halign="center")
        root.add_widget(self.empty_hint)

        # MDScrollView + MDGridLayout statt RecycleView: die RecycleView rendert
        # eingebettet im ScreenManager (FadeTransition) keine Views. Gegen die Perf
        # helfen Debounce, async-DB-Load und batched Rendern (P5).
        scroll = MDScrollView(do_scroll_x=False)
        self.cards_grid = MDGridLayout(cols=3, size_hint_y=None, spacing=dp(8), padding=dp(8))
        self.cards_grid.bind(minimum_height=self.cards_grid.setter("height"))
        scroll.add_widget(self.cards_grid)
        root.add_widget(scroll)

        self.add_widget(root)
        # Responsive: Spaltenzahl an die Screen-Breite koppeln (Querschnitt).
        self.bind(width=self._update_cols)

    # --- Spieler-Buttons ------------------------------------------------------

    def _player_count(self) -> int:
        try:
            return int(self.pl.get("player_count", 1) or 1)
        except (TypeError, ValueError):
            return 1

    def _build_player_buttons(self) -> None:
        self.player_bar.clear_widgets()
        self._player_buttons.clear()
        count = self._player_count()
        if count <= 1:
            self.player_bar.height = dp(0)
            self.player_bar.opacity = 0
            return
        self.player_bar.height = dp(40)
        self.player_bar.opacity = 1

        def _add(key: str, text: str) -> None:
            btn = MDButton(MDButtonText(text=text), style="filled" if key == self._active_slot else "tonal",
                           on_release=lambda _i, k=key: self._select_slot(k))
            self._player_buttons[key] = btn
            self.player_bar.add_widget(btn)

        _add("", "Alle")
        for slot in range(1, count + 1):
            _add(str(slot), f"P{slot}")

    def _select_slot(self, key: str) -> None:
        self._active_slot = key
        for k, btn in self._player_buttons.items():
            btn.style = "filled" if k == key else "tonal"
        self._apply_filters()

    def _update_cols(self, _widget, width) -> None:
        self.cards_grid.cols = max(1, int(width / CARD_MIN_WIDTH))

    # --- Lifecycle / Laden ----------------------------------------------------

    def on_pre_enter(self, *args) -> None:
        # player_count kann sich in den Settings in-place geändert haben (ohne
        # Session-Wechsel) → Buttons/Filter an die aktuelle Spielerzahl anpassen.
        slot_reset = self._sync_player_buttons()
        if self._needs_reload:
            self._reload()
        elif slot_reset:
            self._apply_filters()

    def _sync_player_buttons(self) -> bool:
        """Baut die Spieler-Buttons an die aktuelle player_count. Gibt True zurück,
        wenn ein jetzt ungültiger aktiver Slot auf 'alle' zurückgesetzt wurde."""
        count = self._player_count()
        reset = False
        if self._active_slot and (not self._active_slot.isdigit() or int(self._active_slot) > count):
            self._active_slot = ""
            reset = True
        self._build_player_buttons()
        return reset

    def load_from_session(self) -> None:
        """Session-Wechsel-Hook: Buttons für neue Spielerzahl + DB neu laden."""
        self._active_slot = ""
        self._build_player_buttons()
        self._sprite_cache.clear()
        self._force_reload()

    def _force_reload(self) -> None:
        self._needs_reload = True
        self._sprite_cache.clear()  # Sprite-Repo könnte sich geändert haben (Settings)
        self._item_sprite_cache.clear()
        self._reload()

    def _reload(self) -> None:
        """Startet den DB-Load; async, wenn ein Event-Loop läuft (Kivy async_run),
        sonst synchron (Tests)."""
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            self._all_rows = self._load_from_db()
            self._needs_reload = False
            self._apply_filters()
            return

        if self._load_task is not None and not self._load_task.done():
            # Läuft schon ein Load, Wunsch vormerken: nach Abschluss frisch laden,
            # damit Session-Wechsel/Refresh nicht die alte DB zementieren.
            self._reload_pending = True
            return
        self._start_load_task()

    def _start_load_task(self) -> None:
        self._reload_pending = False
        self._load_gen += 1
        gen = self._load_gen
        self._load_task = asyncio.create_task(self._reload_async(gen))
        self._load_task.add_done_callback(self._on_load_done)

    async def _reload_async(self, gen: int) -> None:
        loop = asyncio.get_running_loop()
        rows = await loop.run_in_executor(None, self._load_from_db)
        if gen != self._load_gen or self._reload_pending:
            # veraltet, oder ein Rerun steht an (z.B. Session-Wechsel mitten im
            # Load) → Ergebnis verwerfen, damit keine Daten der alten Session kurz
            # aufblitzen. _needs_reload bleibt True, der Rerun lädt frisch.
            return
        self._all_rows = rows
        self._needs_reload = False
        self._apply_filters()

    def _on_load_done(self, task: asyncio.Task) -> None:
        # Identity-Guard: nur die eigene Strong-Ref lösen, nie die eines inzwischen
        # neu gestarteten Tasks (sonst GC-Risiko).
        if self._load_task is task:
            self._load_task = None
        if task.cancelled():
            return
        err = task.exception()
        if err is not None:
            logger.error(f"PokedexMenu Reload-Task fehlgeschlagen: {type(err)},{err}")
            logger.error("".join(traceback.format_exception(type(err), err, err.__traceback__)))
        if self._reload_pending:
            self._start_load_task()

    def _load_from_db(self) -> list[dict]:
        if self.configsave is None:
            return []
        session_path = Path(str(self.configsave))
        db = PokedexDB(session_path)
        try:
            db.connect()
            if db.connection is None:
                return []
            return db.get_all()
        except Exception as err:
            logger.error(f"PokedexMenu DB-Load failed: {type(err)},{err}")
            logger.error(f"{traceback.format_exc()}")
            return []
        finally:
            db.close()

    # --- Filtern / Rendern ----------------------------------------------------

    def _apply_filters(self) -> None:
        query = (self.search_input.text or "").strip().lower()
        shiny_only = bool(self.shiny_switch.active)

        filtered = filter_rows(self._all_rows, query, self._active_slot, shiny_only, self._species_de)
        self.count_label.text = f"{len(filtered)} Einträge"

        if not filtered:
            if not self._all_rows:
                self.empty_hint.text = "Noch keine Pokemon in der Datenbank — auf Aktualisieren klicken."
            else:
                self.empty_hint.text = "Kein Treffer mit aktuellen Filtern."
            self.empty_hint.height = dp(60)
            self.empty_hint.opacity = 1
        else:
            self.empty_hint.text = ""
            self.empty_hint.height = 0
            self.empty_hint.opacity = 0

        self._render_cards(filtered)

    def _render_cards(self, rows: list[dict]) -> None:
        # Laufenden Batch-Render abbrechen, sonst mischen sich alte und neue Karten.
        if self._render_event is not None:
            self._render_event.cancel()
            self._render_event = None
        self.cards_grid.clear_widgets()
        show_player = self._player_count() > 1
        self._pending_rows = list(rows)
        self._render_show_player = show_player
        self._render_event = None
        # Erster Batch sofort (kleine Trefferlisten sind damit synchron fertig,
        # große streamen den Rest frameweise nach → kein Ruckeln).
        if self._render_batch(0):
            self._render_event = Clock.schedule_interval(self._render_batch, 0)

    def _render_batch(self, _dt) -> bool:
        batch = self._pending_rows[:RENDER_BATCH]
        del self._pending_rows[:RENDER_BATCH]
        for r in batch:
            slot = row_slot(r)
            self.cards_grid.add_widget(PokemonCard(
                r, self._species_de, sprite_source=self._sprite_source(r),
                slot=slot, player_name=self._player_name(r), show_player=self._render_show_player,
                item_sprite=self._item_sprite(r), on_open=self._open_detail,
            ))
        if not self._pending_rows:
            self._render_event = None
            return False  # stoppt das Intervall
        return True

    def _item_sprite(self, row: dict) -> str:
        """Pfad zum Item-Sprite (gen3+, aus dem Sprite-Repo); '' wenn keins da.
        Pro (edition, item) gecacht — vermeidet Datei-I/O je Karte im Render."""
        conf = getattr(self.obs_websocket, "conf", None)
        if not conf or not hasattr(conf, "get"):
            return ""
        try:
            edition = int(row.get("edition"))
        except (TypeError, ValueError):
            return ""
        if edition <= 20:
            return ""
        item = row.get("item")
        if item in (None, "", 0, "0"):
            return ""
        key = (edition, str(item))
        if key in self._item_sprite_cache:
            return self._item_sprite_cache[key]
        items_path = conf.get("items_path")
        if not items_path:
            return ""
        slug = resolve_tm_hm_sprite(edition, str(item), None, None)
        path = f"{items_path}/{slug}.png"
        result = path if os.path.exists(path) else ""
        self._item_sprite_cache[key] = result
        return result

    def _player_name(self, row: dict) -> str:
        """Trainer-Name zum Spieler-Slot. Primär aus munchlax.player_names
        (Handshake-Name), sonst owner_root (falls im owner ein Name steckt)."""
        slot = row_slot(row)
        app = MDApp.get_running_app()
        names = getattr(getattr(app, "munchlax", None), "player_names", None) if app else None
        if isinstance(names, dict) and slot is not None:
            name = names.get(slot) or names.get(str(slot))
            if name:
                return str(name)
        owner = row.get("owner")
        if owner is None:
            return ""
        root = PokedexDB.owner_root(str(owner))
        # Reiner Zahl-Owner trägt keinen Namen → nichts anzeigen (nur Nummer).
        return "" if root.isdigit() else root

    def _open_detail(self, row: dict) -> None:
        """Navigiert zum Detail-Screen und befüllt ihn aus der DB-Zeile."""
        logger.info(f"PokedexMenu Detail angefordert: dexnr={row.get('dexnr')} pv={row.get('personality')}")
        if self.manager is None or not self.manager.has_screen("PokemonDetail"):
            show_snackbar("Detailansicht nicht verfügbar", level='info')
            return
        # Lazy-Import: hält den Backend-Adapter aus dem Modul-Ladepfad der GUI.
        from backend.pokemon_detail_view import edition_int, row_to_pokemon
        pokemon = row_to_pokemon(row)
        edition = edition_int(row.get("edition"))
        app = MDApp.get_running_app()
        munchlax = getattr(app, "munchlax", None) if app is not None else None
        detail = self.manager.get_screen("PokemonDetail")
        # Soullink-Member sind mit Root-Owner (ohne Slot-Suffix) geschlüsselt
        # (arceus), DB-owner ist name_<slot> → Root extrahieren, sonst matcht nichts.
        owner = PokedexDB.owner_root(str(row.get("owner") or "")) or None
        detail.show(pokemon, edition, return_screen="PokedexMenu",
                    munchlax=munchlax, owner=owner)
        self.manager.current = "PokemonDetail"

    def _sprite_source(self, row: dict) -> str:
        """Sprite-Pfad fuer eine DB-Zeile via OBS.get_sprite; leer bei Fehlern.
        Gecacht pro Identität (P5), damit gleiche Pfade nicht neu aufgelöst werden."""
        if self.obs_websocket is None:
            return ""
        cache_key = (
            row.get("dexnr"), row.get("edition"), row.get("form"),
            bool(row.get("shiny")), bool(row.get("female")),
        )
        if cache_key in self._sprite_cache:
            return self._sprite_cache[cache_key]
        try:
            raw = str(row.get("dexnr", ""))
            dexnr_val = "egg" if raw == "egg" else int(raw)
            edition_val = int(row.get("edition"))
            # form-Default "" (nicht 0): get_sprite hängt str(form) an den
            # Dateinamen; bei Standardform ist die DB-Spalte leer, dann muss
            # "327" + "" = "327.png" entstehen, nicht "3270.png".
            shim = SimpleNamespace(
                shiny=bool(row.get("shiny")), female=bool(row.get("female")),
                dexnr=dexnr_val, form=row.get("form") or "",
            )
            source = self.obs_websocket.get_sprite(shim, False, edition_val) or ""
        except Exception as err:
            logger.debug(f"PokedexMenu Sprite-Pfad fehlgeschlagen: {type(err)},{err}")
            source = ""
        self._sprite_cache[cache_key] = source
        return source
