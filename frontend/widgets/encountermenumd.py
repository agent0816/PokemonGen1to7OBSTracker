"""KivyMD-Encounter-Screen. Aufbereitung liegt in backend/encounter_view.

Encounter-Karten im Pokedex-Layout (Standard) bzw. Gruppen-Karten pro Route
(Soullink coop/versus). MDScrollView + Boxen statt RecycleView (rendert im ScreenManager
nicht, siehe Memory reference_recycleview_screenmanager).
"""
import asyncio
import os
import traceback
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace

from kivy.clock import Clock
from kivy.graphics import Color, Line
from kivy.metrics import dp
from kivymd.uix.boxlayout import MDBoxLayout
from kivymd.uix.button import MDButton, MDButtonText
from kivymd.uix.card import MDCard
from kivymd.uix.fitimage import FitImage
from kivymd.uix.gridlayout import MDGridLayout
from kivymd.uix.label import MDLabel
from kivymd.uix.menu import MDDropdownMenu
from kivymd.uix.screen import MDScreen
from kivymd.uix.scrollview import MDScrollView
from kivymd.uix.textfield import MDTextField, MDTextFieldHintText

from backend.classes.pokedex_db import PokedexDB
from backend.encounter_view import (
    LINK_STATE_TEXT, STATUS_TEXT, build_encounter_rows, edition_label,
    group_soullink, is_soullink,
)
from backend.logging_setup import get_logger
from backend.player_colors import player_rgba
from backend.pokedex_view import load_species_de, row_slot

logger = get_logger(__name__, 'logs/encountermenu.log')

# Soullink-Gruppenkarten sind breiter als die Einzel-Encounter-Karten
CARD_MIN_WIDTH = dp(320)
ENC_CARD_MIN_WIDTH = dp(240)
# Mindestbreite je Mitglied (Sprite 48dp + lesbarer Text) für 3–4-Spieler-Gruppen
MEMBER_MIN_WIDTH = dp(160)
ENC_CARD_HEIGHT = dp(96)
RENDER_BATCH = 12

# Dezente Kartentönung pro Status (Alpha-Tönung, trägt in Dark und Light)
STATUS_TINT = {
    "caught":     (0.20, 0.65, 0.25, 0.22),
    "obtained":   (0.20, 0.65, 0.25, 0.22),
    "not_caught": (0.80, 0.20, 0.20, 0.22),
    "unknown":    (0.90, 0.70, 0.10, 0.20),
    "dupes":      (0.55, 0.55, 0.55, 0.20),
    "open":       (0.55, 0.55, 0.55, 0.12),
}
# Volle Farbe für den Status-Chip der Encounter-Karte
STATUS_CHIP_COLOR = {
    "caught":     (0.20, 0.60, 0.25, 1),
    "obtained":   (0.20, 0.60, 0.25, 1),
    "not_caught": (0.75, 0.20, 0.20, 1),
    "unknown":    (0.85, 0.62, 0.05, 1),
    "dupes":      (0.45, 0.45, 0.45, 1),
    "open":       (0.45, 0.45, 0.45, 1),
}
LINK_BADGE_COLOR = {
    "complete": (0.20, 0.60, 0.25, 1),
    "pending":  (0.85, 0.62, 0.05, 1),
    "failed":   (0.75, 0.20, 0.20, 1),
}

def _small_label(text: str, height: float, bold: bool = False, small: bool = True) -> MDLabel:
    lbl = MDLabel(text=text, bold=bold, size_hint_y=None, height=height,
                  halign="left", valign="center", shorten=True, shorten_from="right")
    if small:
        lbl.role = "small"
    # shorten braucht eine feste Textbreite, sonst wird nicht gekürzt
    lbl.bind(width=lambda w, v: setattr(w, "text_size", (v, None)))
    return lbl


def _route_text(row: dict) -> str:
    """'Ort (Route N)' — location_name liefert bei unbekannter Route schon 'Route N'."""
    loc = row["location"]
    return loc if loc == f"Route {row['route']}" else f"{loc} (Route {row['route']})"


class EncounterCard(MDCard):
    """Encounter als Karte im Pokedex-Layout: Spieler-Akzent, Sprite, Infos, Status-Chip."""

    def __init__(self, row: dict, sprite_source: str = "", **kwargs):
        super().__init__(style="elevated", orientation="horizontal", size_hint_y=None,
                         height=ENC_CARD_HEIGHT, padding=0, spacing=dp(8),
                         radius=[dp(12)] * 4, **kwargs)
        self.row = row
        slot = None if row["is_open"] else row_slot(row)
        accent = player_rgba(slot, alpha=1.0) if slot is not None else (0.5, 0.5, 0.5, 1.0)
        # Farbstreifen links = Spieler (wie Pokedex), Kartentönung = Status
        self.add_widget(MDBoxLayout(size_hint_x=None, width=dp(6), md_bg_color=accent,
                                    radius=[dp(12), 0, 0, dp(12)]))
        self.md_bg_color = STATUS_TINT.get(row["status"], (0, 0, 0, 0))

        # Ohne Quelle (offene Route, kein Sprite-Repo) zeichnet FitImage ein weißes
        # Feld → leeren Platzhalter gleicher Breite nehmen.
        if sprite_source:
            self.add_widget(FitImage(source=sprite_source, fit_mode="contain",
                                     size_hint=(None, 1), width=dp(64)))
        else:
            self.add_widget(MDBoxLayout(size_hint_x=None, width=dp(64)))

        text_box = MDBoxLayout(orientation="vertical", padding=(0, dp(6), dp(8), dp(6)), spacing=dp(1))
        title = row["species"]
        if row["shiny"]:
            title = f"{title} (Shiny)"
        text_box.add_widget(_small_label(title, height=dp(22), bold=True, small=False))
        text_box.add_widget(_small_label(_route_text(row), height=dp(18)))
        lvl = str(row.get("lvl") or "") or "–"
        info = f"Lv. {lvl}  ·  {row['method']}" if not row["is_open"] else "noch kein Encounter"
        text_box.add_widget(_small_label(info, height=dp(18)))

        status = row["status"]
        chip = MDBoxLayout(size_hint=(None, None), size=(dp(84), dp(20)),
                           md_bg_color=STATUS_CHIP_COLOR.get(status, (0.5, 0.5, 0.5, 1)),
                           radius=[dp(10)] * 4)
        chip_lbl = MDLabel(text=STATUS_TEXT.get(status, status), halign="center", valign="center",
                           theme_text_color="Custom", text_color=(1, 1, 1, 1))
        chip_lbl.role = "small"
        chip.add_widget(chip_lbl)
        text_box.add_widget(chip)
        self.add_widget(text_box)


class MemberBlock(MDBoxLayout):
    """Mitglied einer Soullink-Gruppe: Spielerfarbe als Tönung + Rahmen, Sprite links."""

    def __init__(self, slot: int | None, species: str, status_text: str,
                 sprite_source: str = "", **kwargs):
        super().__init__(orientation="horizontal", padding=dp(6), spacing=dp(6), **kwargs)
        s = slot if slot is not None else 1
        rgba_fill = player_rgba(s, 0.18) if slot is not None else (0.5, 0.5, 0.5, 0.12)
        rgba_line = player_rgba(s, 1.0) if slot is not None else (0.5, 0.5, 0.5, 1.0)
        self.md_bg_color = rgba_fill
        with self.canvas.after:
            Color(*rgba_line)
            self._border = Line(rectangle=(self.x, self.y, self.width, self.height), width=1.1)
        self.bind(pos=self._upd_border, size=self._upd_border)

        # Ohne Quelle zeichnet FitImage ein weißes Feld → leerer Platzhalter
        if sprite_source:
            self.add_widget(FitImage(source=sprite_source, fit_mode="contain",
                                     size_hint=(None, 1), width=dp(48)))
        else:
            self.add_widget(MDBoxLayout(size_hint_x=None, width=dp(48)))

        text_box = MDBoxLayout(orientation="vertical", spacing=dp(2))
        head = f"P{slot}" if slot is not None else "P?"
        text_box.add_widget(self._lbl(head, role="small"))
        text_box.add_widget(self._lbl(species or "—", bold=True))
        text_box.add_widget(self._lbl(status_text, role="small"))
        self.add_widget(text_box)

    @staticmethod
    def _lbl(text: str, bold: bool = False, role: str | None = None) -> MDLabel:
        lbl = MDLabel(text=text, bold=bold, size_hint_y=None, height=dp(20),
                      halign="left", valign="center", shorten=True, shorten_from="right")
        if role:
            lbl.role = role
        lbl.bind(width=lambda w, v: setattr(w, "text_size", (v, None)))
        return lbl

    def _upd_border(self, *_args) -> None:
        self._border.rectangle = (self.x, self.y, self.width, self.height)


class LinkGroupCard(MDCard):
    """Gruppen-Karte pro Route: Kopf (Ort, Route, Badge) + Mitglieder nebeneinander."""

    def __init__(self, group: dict, expected_count: int,
                 sprite_for: Callable[[dict], str] | None = None, **kwargs):
        super().__init__(style="elevated", orientation="vertical", size_hint_y=None,
                         height=dp(132), padding=dp(8), spacing=dp(6),
                         radius=[dp(12)] * 4, **kwargs)
        self.group = group

        head = MDBoxLayout(orientation="horizontal", size_hint_y=None, height=dp(32), spacing=dp(8))
        # location_name liefert bei unbekannter Route bereits "Route N" — dann
        # nicht noch einmal "(Route N)" anhängen.
        loc = group["location"]
        head_text = loc if loc == f"Route {group['route']}" else f"{loc}  (Route {group['route']})"
        title = MDLabel(text=head_text, bold=True,
                        halign="left", valign="center", shorten=True, shorten_from="right")
        title.bind(width=lambda w, v: setattr(w, "text_size", (v, None)))
        head.add_widget(title)

        agg = group["agg_status"]
        badge = MDBoxLayout(size_hint=(None, None), size=(dp(110), dp(26)), pos_hint={"center_y": .5},
                            md_bg_color=LINK_BADGE_COLOR.get(agg, (0.5, 0.5, 0.5, 1)),
                            radius=[dp(13)] * 4)
        badge_lbl = MDLabel(text=LINK_STATE_TEXT.get(agg, agg), halign="center", valign="center",
                            theme_text_color="Custom", text_color=(1, 1, 1, 1))
        badge_lbl.role = "small"
        badge.add_widget(badge_lbl)
        head.add_widget(badge)
        self.add_widget(head)

        body = MDBoxLayout(orientation="horizontal", spacing=dp(6))
        present = set()
        for m in group["members"]:
            if m["slot"] is not None:
                present.add(m["slot"])
            text = STATUS_TEXT.get(m["status"], m["status"])
            if m["shiny"]:
                text += " *"
            sprite = sprite_for(m) if sprite_for is not None else ""
            body.add_widget(MemberBlock(m["slot"], m["species"], text, sprite))
        # Noch fehlende erwartete Mitglieder als Platzhalter "—"
        for slot in range(1, max(expected_count, 1) + 1):
            if slot not in present and len(body.children) < expected_count:
                body.add_widget(MemberBlock(slot, "—", "—"))
        self.add_widget(body)


class EncounterMenu(MDScreen):
    def __init__(self, configsave, pl, nuz, app_version, obs_websocket=None, **kwargs):
        super().__init__(**kwargs)
        self.name = "EncounterMenu"
        self.configsave = configsave
        self.obs_websocket = obs_websocket
        self._sprite_cache: dict[tuple, str] = {}
        self.pl = pl if pl is not None else {}
        self.nuz = nuz if nuz is not None else {}
        self.app_version = app_version

        self._encounters: list[dict] = []
        self._species_de: dict = load_species_de()
        self._player_filter: str = "alle"
        self._edition_filter: int | None = None
        self._load_task: asyncio.Task | None = None
        self._reload_pending: bool = False
        self._load_gen: int = 0
        self._render_event = None
        self._pending: list = []
        self._pending_mode: str = "flat"
        self._pending_expected: int = 2
        self._menus: list[MDDropdownMenu] = []

        root = MDBoxLayout(orientation="vertical", padding="10dp", spacing="8dp")

        header = MDBoxLayout(orientation="horizontal", size_hint_y=None, height="48dp", spacing="10dp")
        self.head_label = MDLabel(text="Encounter", role="large", valign="center")
        header.add_widget(self.head_label)
        self.refresh_button = MDButton(
            MDButtonText(text="Aktualisieren"), style="tonal",
            pos_hint={"center_y": .5}, on_release=lambda _i: self._force_reload(),
        )
        header.add_widget(self.refresh_button)
        root.add_widget(header)

        filter_bar = MDBoxLayout(orientation="horizontal", size_hint_y=None, height="56dp", spacing="10dp")
        self.player_button_text = MDButtonText(text="Spieler: alle")
        self.player_button = MDButton(self.player_button_text, style="tonal", pos_hint={"center_y": .5},
                                      on_release=lambda i: self._open_player_menu(i))
        filter_bar.add_widget(self.player_button)
        self.edition_button_text = MDButtonText(text="Edition: alle")
        self.edition_button = MDButton(self.edition_button_text, style="tonal", pos_hint={"center_y": .5},
                                       on_release=lambda i: self._open_edition_menu(i))
        filter_bar.add_widget(self.edition_button)
        self.search_input = MDTextField(MDTextFieldHintText(text="Route, Ort, Art …"),
                                        mode="outlined", size_hint_x=0.4)
        self._filter_trigger = Clock.create_trigger(lambda _dt: self._apply_filters(), 0.15)
        self.search_input.bind(text=lambda _i, _v: self._filter_trigger())
        filter_bar.add_widget(self.search_input)
        self.count_label = MDLabel(text="", valign="center", halign="right")
        filter_bar.add_widget(self.count_label)
        self.filter_bar = filter_bar
        root.add_widget(filter_bar)

        self.empty_hint = MDLabel(text="", size_hint_y=None, height=0, opacity=0, halign="center")
        root.add_widget(self.empty_hint)

        # Ein Grid für beide Modi; nur die Mindest-Kartenbreite unterscheidet sich
        scroll = MDScrollView(do_scroll_x=False)
        self.cards_grid = MDGridLayout(cols=2, size_hint_y=None, spacing=dp(8), padding=dp(8))
        self.cards_grid.bind(minimum_height=self.cards_grid.setter("height"))
        self._card_min_width = ENC_CARD_MIN_WIDTH
        scroll.add_widget(self.cards_grid)
        root.add_widget(scroll)
        self._scroll = scroll

        self.add_widget(root)
        # An die tatsächliche Grid-Breite binden (nicht die Screen-Breite):
        # sonst können die Karten bei Padding/Rail knapp unter CARD_MIN_WIDTH
        # rutschen. self.width als Fallback, solange das Grid noch 0 breit ist.
        self.cards_grid.bind(width=self._update_cols)
        self.bind(width=self._update_cols)

    # --- Responsive ------------------------------------------------------------

    def _update_cols(self, _widget=None, _value=None) -> None:
        width = self.cards_grid.width or self.width
        self.cards_grid.cols = max(1, int(width / self._card_min_width))

    # --- Lifecycle / Laden -----------------------------------------------------

    def on_pre_enter(self, *args) -> None:
        # Immer frisch laden: Encounter ändern sich live während des Spielens
        # (Parität zur Klassik, die bei jedem Eintritt neu lädt). _reload ist
        # async + gebatcht, der DB-Round-Trip ist günstig. Sprite-Cache mit
        # leeren: Sprite-Pfad/-Repo kann in den Settings in-place geändert sein.
        self._sprite_cache.clear()
        self._reload()

    def load_from_session(self) -> None:
        """Session-Wechsel-Hook: Filter zurücksetzen, DB neu laden."""
        self._player_filter = "alle"
        self._edition_filter = None
        self.player_button_text.text = "Spieler: alle"
        self.edition_button_text.text = "Edition: alle"
        self._force_reload()

    def _force_reload(self) -> None:
        self._sprite_cache.clear()  # Sprite-Repo könnte sich geändert haben (Settings)
        self._reload()

    def _reload(self) -> None:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            self._encounters = self._load_from_db()
            self._after_load()
            return
        if self._load_task is not None and not self._load_task.done():
            self._reload_pending = True
            return
        self._start_load_task()

    def _start_load_task(self) -> None:
        self._reload_pending = False
        self._load_gen += 1
        gen = self._load_gen
        # Session-Pfad beim Start festhalten: wechselt die Session mitten im
        # Load (configsave.text ändert sich), ist das Ergebnis veraltet — auch
        # wenn kein neuer Reload getriggert wurde (Guard gegen stale Anzeige).
        session = "" if self.configsave is None else str(self.configsave)
        self._load_task = asyncio.create_task(self._reload_async(gen, session))
        self._load_task.add_done_callback(self._on_load_done)

    async def _reload_async(self, gen: int, session: str) -> None:
        loop = asyncio.get_running_loop()
        rows = await loop.run_in_executor(None, self._load_from_db, session)
        if gen != self._load_gen or self._reload_pending:
            return  # veraltet (neuer Load bereits getriggert)
        if session != str(self.configsave):
            # Session mitten im Load gewechselt → Ergebnis verwerfen und
            # frisch nachladen (sonst bliebe die Anzeige auf alten Daten).
            self._reload_pending = True
            return
        self._encounters = rows
        self._after_load()

    def _on_load_done(self, task: asyncio.Task) -> None:
        if self._load_task is task:
            self._load_task = None
        if task.cancelled():
            return
        err = task.exception()
        if err is not None:
            logger.error(f"EncounterMenu Reload-Task fehlgeschlagen: {type(err)},{err}")
            logger.error("".join(traceback.format_exception(type(err), err, err.__traceback__)))
            # UI nicht still auf altem Stand lassen
            self._set_hint("Laden fehlgeschlagen (siehe Log).")
        if self._reload_pending:
            self._start_load_task()

    def _load_from_db(self, session: str | None = None) -> list[dict]:
        if session is None:
            session = str(self.configsave) if self.configsave is not None else ""
        if not session:
            return []
        db = PokedexDB(Path(session))
        try:
            with db.access_lock:
                db.connect()
                if db.connection is None:
                    return []
                cursor = db.connection.cursor()
                cursor.execute("SELECT * FROM encounters ORDER BY route, timestamp")
                return [dict(row) for row in cursor.fetchall()]
        except Exception as err:
            logger.error(f"EncounterMenu DB-Load failed: {type(err)},{err}")
            logger.error(f"{traceback.format_exc()}")
            return []
        finally:
            db.close()

    def _after_load(self) -> None:
        # Filter zurücksetzen, falls der gewählte Wert nicht mehr existiert
        if self._player_filter != "alle" and self._player_filter not in self._owners():
            self._player_filter = "alle"
            self.player_button_text.text = "Spieler: alle"
        if self._edition_filter is not None and self._edition_filter not in self._editions():
            self._edition_filter = None
            self.edition_button_text.text = "Edition: alle"
        self._apply_filters()

    # --- Filter ------------------------------------------------------------------

    def _owners(self) -> list[str]:
        owners = {str(e.get("owner", "") or "").strip() for e in self._encounters}
        return sorted(o for o in owners if o)

    def _editions(self) -> list[int]:
        result: set[int] = set()
        for e in self._encounters:
            try:
                result.add(int(e.get("edition")))
            except (TypeError, ValueError):
                continue
        return sorted(result)

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

    def _open_player_menu(self, caller) -> None:
        entries = [("alle", "alle")] + [(o, o) for o in self._owners()]
        self._open_menu(caller, entries, self._pick_player)

    def _open_edition_menu(self, caller) -> None:
        entries: list[tuple[str, object]] = [("alle", None)]
        entries += [(edition_label(ed), ed) for ed in self._editions()]
        self._open_menu(caller, entries, self._pick_edition)

    def _pick_player(self, value, text) -> None:
        self._player_filter = str(value)
        self.player_button_text.text = f"Spieler: {text}"
        self._apply_filters()

    def _pick_edition(self, value, text) -> None:
        self._edition_filter = value
        self.edition_button_text.text = f"Edition: {text}"
        self._apply_filters()

    # --- Rendern -----------------------------------------------------------------

    def _expected_count(self) -> int:
        try:
            return int(self.nuz.get("soullink_player_count", 2) or 2)
        except (TypeError, ValueError):
            return 2

    def _set_player_filter_visible(self, visible: bool) -> None:
        """Spieler-Filter nur in der flachen Ansicht zeigen — group_soullink
        kennt keinen Spieler-Filter, der Button wäre im Soullink-Modus wirkungslos.
        add_widget(index=len(children)) setzt ihn wieder ganz links (Ursprung)."""
        present = self.player_button in self.filter_bar.children
        if visible and not present:
            self.filter_bar.add_widget(self.player_button, index=len(self.filter_bar.children))
        elif not visible and present:
            self.filter_bar.remove_widget(self.player_button)

    def _apply_filters(self) -> None:
        query = (self.search_input.text or "").strip()
        self._set_player_filter_visible(not is_soullink(self.nuz))
        try:
            if is_soullink(self.nuz):
                groups = group_soullink(self._encounters, self._species_de, self._expected_count(),
                                        edition_filter=self._edition_filter, query=query)
                self.count_label.text = f"{len(groups)} Routen"
                self._set_hint("Noch keine verlinkten Encounter." if not groups else "")
                self._render("groups", groups)
            else:
                rows, enc_count, open_count = build_encounter_rows(
                    self._encounters, self._species_de, edition_filter=self._edition_filter,
                    player_filter=self._player_filter, query=query)
                self.count_label.text = f"{enc_count} Encounter, {open_count} offen"
                self._set_hint("Keine Encounter gefunden." if not rows else "")
                self._render("flat", rows)
        except Exception as err:
            logger.error(f"EncounterMenu Aufbereitung fehlgeschlagen: {type(err)},{err}")
            logger.error(f"{traceback.format_exc()}")
            self._set_hint("Fehler beim Aufbereiten der Encounter (siehe Log).")
            self._render("flat", [])

    def _set_hint(self, text: str) -> None:
        self.empty_hint.text = text
        self.empty_hint.height = dp(50) if text else 0
        self.empty_hint.opacity = 1 if text else 0

    def _render(self, mode: str, items: list) -> None:
        if self._render_event is not None:
            self._render_event.cancel()
            self._render_event = None
        self.cards_grid.clear_widgets()
        if mode == "flat":
            self._card_min_width = ENC_CARD_MIN_WIDTH
        else:
            self._card_min_width = max(CARD_MIN_WIDTH, MEMBER_MIN_WIDTH * self._expected_count())
        self._update_cols()
        self._pending = list(items)
        self._pending_mode = mode
        self._pending_expected = self._expected_count()
        if self._render_batch(0):
            self._render_event = Clock.schedule_interval(self._render_batch, 0)

    def _render_batch(self, _dt) -> bool:
        batch = self._pending[:RENDER_BATCH]
        del self._pending[:RENDER_BATCH]
        for item in batch:
            if self._pending_mode == "groups":
                self.cards_grid.add_widget(LinkGroupCard(item, self._pending_expected, self._sprite_source))
            else:
                self.cards_grid.add_widget(EncounterCard(item, self._sprite_source(item)))
        if not self._pending:
            self._render_event = None
            return False
        return True

    def _sprite_source(self, row: dict) -> str:
        """Sprite-Pfad via OBS.get_sprite; leer bei offenen Routen/Fehlern.
        Gecacht pro (dexnr, edition, shiny, female, form), wie im Pokedex."""
        if self.obs_websocket is None or row.get("is_open"):
            return ""
        female = bool(row.get("female"))
        form = row.get("form") or ""
        cache_key = (row.get("dexnr"), row.get("edition"), bool(row.get("shiny")), female, form)
        if cache_key in self._sprite_cache:
            return self._sprite_cache[cache_key]
        try:
            dexnr = int(row.get("dexnr"))
            if dexnr <= 0:
                source = ""
            else:
                shim = SimpleNamespace(shiny=bool(row.get("shiny")), female=female,
                                       dexnr=dexnr, form=form)
                source = self.obs_websocket.get_sprite(shim, False, int(row.get("edition"))) or ""
                # get_sprite prüft keine Existenz; fehlende Datei → FitImage zeichnet weiß
                if source and not os.path.exists(source):
                    source = ""
        except Exception as err:
            logger.debug(f"EncounterMenu Sprite-Pfad fehlgeschlagen: {type(err)},{err}")
            source = ""
        self._sprite_cache[cache_key] = source
        return source
