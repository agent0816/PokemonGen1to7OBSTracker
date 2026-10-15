"""KivyMD-Pokedex-Screen. Filter-/Formatlogik liegt in backend/pokedex_view."""
import traceback
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace

from kivy.clock import Clock
from kivy.metrics import dp
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
from backend.pokedex_view import (
    filter_rows, item_name, load_species_de, location_name, species_name,
)
from frontend.widgets.md_common import show_snackbar

logger = get_logger(__name__, './logs/pokedexmenu.log')

# Summe der Kinder: Sprite 72 + Titel 24 + 3×18 + Padding 2×8 + Spacing 4×2 = 174.
CARD_HEIGHT = dp(182)


class PokemonCard(MDCard):
    """Anklickbare Pokemon-Karte: Sprite, Spitzname/Art, Level, Item, Route."""

    def __init__(self, row: dict, species_de: dict, sprite_source: str = "",
                 on_open: Callable[[dict], None] | None = None, **kwargs):
        super().__init__(style="elevated", ripple_behavior=True, orientation="vertical",
                         size_hint_y=None, height=CARD_HEIGHT, padding=dp(8), spacing=dp(2),
                         radius=[dp(12)] * 4, **kwargs)
        self.row = row
        self._on_open = on_open

        self.add_widget(FitImage(source=sprite_source or "", fit_mode="contain",
                                 size_hint_y=None, height=dp(72)))

        species = species_name(str(row.get("dexnr", "")), species_de)
        title = str(row.get("nickname") or "") or species
        if row.get("shiny"):
            title = f"{title} (Shiny)"
        self.add_widget(self._label(title, bold=True, height=dp(24)))

        edition = self._edition(row)
        lvl = str(row.get("lvl") or "") or "–"
        self.add_widget(self._label(f"Lv. {lvl}", height=dp(18), small=True))
        self.add_widget(self._label(f"Item: {self._item_text(row, edition)}", height=dp(18), small=True))
        self.add_widget(self._label(f"Route: {self._route_text(row, edition)}", height=dp(18), small=True))

        self.bind(on_release=self._handle_release)

    @staticmethod
    def _label(text: str, height: float, bold: bool = False, small: bool = False) -> MDLabel:
        lbl = MDLabel(text=text, bold=bold, size_hint_y=None, height=height,
                      halign="center", valign="center", shorten=True, shorten_from="right")
        if small:
            lbl.role = "small"
        # shorten braucht eine feste Textbreite, sonst wird nicht gekürzt
        lbl.bind(width=lambda w, v: setattr(w, "text_size", (v, None)))
        return lbl

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
    def __init__(self, configsave, obs_websocket, app_version, **kwargs):
        super().__init__(**kwargs)
        self.name = "PokedexMenu"
        self.configsave = configsave
        self.obs_websocket = obs_websocket
        self.app_version = app_version

        self._all_rows: list[dict] = []
        self._species_de: dict = load_species_de()

        root = MDBoxLayout(orientation="vertical", padding="10dp", spacing="8dp")

        header = MDBoxLayout(orientation="horizontal", size_hint_y=None, height="48dp", spacing="10dp")
        self.head_label = MDLabel(text="Pokemon-Datenbank", role="large", valign="center")
        header.add_widget(self.head_label)
        self.refresh_button = MDButton(
            MDButtonText(text="Aktualisieren"), style="tonal",
            pos_hint={"center_y": .5}, on_release=lambda _i: self._reload(),
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

        self.slot_input = MDTextField(
            MDTextFieldHintText(text="1–4"),
            mode="outlined", size_hint_x=0.12,
        )
        self.slot_input.bind(text=lambda _i, _v: self._filter_trigger())
        filter_bar.add_widget(self.slot_input)

        filter_bar.add_widget(MDLabel(text="nur Shinys", size_hint_x=None, width="90dp", valign="center"))
        self.shiny_switch = MDSwitch(pos_hint={"center_y": .5})
        self.shiny_switch.bind(active=lambda _i, _v: self._filter_trigger())
        filter_bar.add_widget(self.shiny_switch)

        self.count_label = MDLabel(text="0 Einträge", valign="center", halign="right")
        filter_bar.add_widget(self.count_label)
        root.add_widget(filter_bar)

        self.empty_hint = MDLabel(text="", size_hint_y=None, height=0, opacity=0, halign="center")
        root.add_widget(self.empty_hint)

        # MDScrollView + MDGridLayout statt RecycleView: die RecycleView rendert
        # eingebettet im ScreenManager (FadeTransition) keine Views. Gegen die Perf
        # hilft der Debounce (search/slot/shiny).
        scroll = MDScrollView(do_scroll_x=False)
        self.cards_grid = MDGridLayout(cols=3, size_hint_y=None, spacing=dp(8), padding=dp(8))
        self.cards_grid.bind(minimum_height=self.cards_grid.setter("height"))
        scroll.add_widget(self.cards_grid)
        root.add_widget(scroll)

        self.add_widget(root)

    def on_pre_enter(self, *args) -> None:
        self._reload()

    def load_from_session(self) -> None:
        """Session-Wechsel-Hook: DB der neuen Session laden."""
        self._reload()

    def _reload(self) -> None:
        self._all_rows = self._load_from_db()
        self._apply_filters()

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

    def _apply_filters(self) -> None:
        query = (self.search_input.text or "").strip().lower()
        slot_filter = (self.slot_input.text or "").strip()
        shiny_only = bool(self.shiny_switch.active)

        filtered = filter_rows(self._all_rows, query, slot_filter, shiny_only, self._species_de)
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
        self.cards_grid.clear_widgets()
        for r in rows:
            self.cards_grid.add_widget(PokemonCard(
                r, self._species_de, sprite_source=self._sprite_source(r), on_open=self._open_detail,
            ))

    def _open_detail(self, row: dict) -> None:
        """Platzhalter: später Navigation zum Pokemon-Detail-Screen."""
        logger.info(f"PokedexMenu Detail angefordert: dexnr={row.get('dexnr')} pv={row.get('personality')}")
        show_snackbar("Detailansicht folgt", level='info')

    def _sprite_source(self, row: dict) -> str:
        """Sprite-Pfad fuer eine DB-Zeile via OBS.get_sprite; leer bei Fehlern."""
        if self.obs_websocket is None:
            return ""
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
            return self.obs_websocket.get_sprite(shim, False, edition_val) or ""
        except Exception as err:
            logger.debug(f"PokedexMenu Sprite-Pfad fehlgeschlagen: {type(err)},{err}")
            return ""
