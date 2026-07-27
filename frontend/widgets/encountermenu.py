import traceback
from pathlib import Path

import yaml
from kivy.graphics import Color, Rectangle
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.gridlayout import GridLayout
from kivy.uix.label import Label
from kivy.uix.screenmanager import Screen
from kivy.uix.scrollview import ScrollView
from kivy.uix.spinner import Spinner
from kivy.uix.textinput import TextInput

from backend.classes.pokedex_db import PokedexDB
from backend.logging_setup import get_logger


logger = get_logger(__name__, './logs/encountermenu.log')


try:
    species_de = yaml.safe_load(open("backend/data/Species_de.yaml", encoding="utf-8"))
except Exception as err:
    logger.error(f"Species_de.yaml laden fehlgeschlagen: {err}")
    species_de = {}

_ENCOUNTER_LOCATIONS: dict[int, dict] = {}
for _gen, _filename in ((3, "encounter_locations_gen3.yml"), (4, "encounter_locations_gen4.yml"), (5, "encounter_locations_gen5.yml"), (6, "encounter_locations_gen6.yml")):
    try:
        with open(f"backend/data/{_filename}", encoding="utf-8") as f:
            _ENCOUNTER_LOCATIONS[_gen] = yaml.safe_load(f) or {}
    except Exception as err:
        logger.error(f"{_filename} laden fehlgeschlagen: {err}")
        _ENCOUNTER_LOCATIONS[_gen] = {}


def _edition_to_gen(edition: int) -> int:
    if 31 <= edition <= 35:
        return 3
    if 41 <= edition <= 45:
        return 4
    if 51 <= edition <= 54:
        return 5
    if 61 <= edition <= 64:
        return 6
    return 0


_EDITION_NAME = {
    31: "Rubin", 32: "Saphir", 33: "Smaragd", 34: "Feuerrot", 35: "Blattgrün",
    41: "Diamant", 42: "Perl", 43: "Platin", 44: "HeartGold", 45: "SoulSilver",
    51: "Schwarz", 52: "Weiß", 53: "Schwarz 2", 54: "Weiß 2",
    61: "X", 62: "Y", 63: "Omega Rubin", 64: "Alpha Saphir",
}

COLUMNS = [
    ("Rt", 0.06),
    ("Location", 0.20),
    ("Species", 0.16),
    ("Lv", 0.05),
    ("S", 0.04),
    ("Typ", 0.09),
    ("Status", 0.12),
]

STATUS_COLORS = {
    "caught":    (0.2, 0.7, 0.2, 0.25),
    "obtained":  (0.2, 0.7, 0.2, 0.25),
    "not_caught": (0.7, 0.2, 0.2, 0.25),
    "unknown":   (0.7, 0.7, 0.2, 0.25),
    "dupes":     (0.5, 0.5, 0.5, 0.20),
    "open":      (0.3, 0.3, 0.3, 0.10),
}

STATUS_TEXT = {
    # ASCII-Symbole statt Unicode-Glyphs: Kivy-Default-Font (Roboto) hat kein
    # Glyph fuer ✓✗⏳↷— und rendert Ersatz-Kaestchen.
    "caught":    "[+] gefangen",
    "obtained":  "[+] erhalten",
    "not_caught": "[x] verpasst",
    "unknown":   "[?] offen",
    "dupes":     "[/] dupe",
    "open":      "[-] offen",
}


def _species_name(dexnr) -> str:
    try:
        return species_de.get(int(dexnr), f"#{dexnr}")
    except (ValueError, TypeError):
        return f"#{dexnr}"


def _location_name(route_id: int, edition: int = 0) -> str:
    # STARTER_ROUTE (-1) ist der virtuelle Slot fuer den Starter — Label
    # ohne "Route "-Prefix, damit er im Encounter-Menue klar erkennbar ist.
    if route_id == -1:
        return "Starter"
    gen = _edition_to_gen(edition)
    locs = _ENCOUNTER_LOCATIONS.get(gen, {})
    loc = locs.get(route_id)
    if loc and isinstance(loc, dict):
        return loc.get("name", f"Route {route_id}")
    for g in _ENCOUNTER_LOCATIONS.values():
        loc = g.get(route_id)
        if loc and isinstance(loc, dict):
            return loc.get("name", f"Route {route_id}")
    return f"Route {route_id}"


def _locations_for_edition(edition: int) -> list[int]:
    gen = _edition_to_gen(edition)
    locs = _ENCOUNTER_LOCATIONS.get(gen, {})
    result = []
    for loc_id, loc_data in locs.items():
        if not isinstance(loc_data, dict):
            continue
        games = loc_data.get("games", [])
        if edition in games:
            result.append(int(loc_id))
    return sorted(result)


class EncounterMenu(Screen):
    def __init__(self, configsave, pl, app_version, **kwargs):
        super().__init__(**kwargs)
        self.name = "EncounterMenu"
        self.configsave = configsave
        self.pl = pl
        self.app_version = app_version

        self._encounters: list[dict] = []

        root = BoxLayout(orientation="vertical", padding=("10dp", "10dp"), spacing="5dp")

        header = BoxLayout(orientation="horizontal", size_hint_y=None, height="40dp", spacing="10dp")
        self.back_button = Button(text="Zurück zum Hauptmenü", size_hint_x=0.3, on_press=self._back)
        header.add_widget(self.back_button)
        self.head_label = Label(text="Encounter-Übersicht")
        header.add_widget(self.head_label)
        self.refresh_button = Button(text="Aktualisieren", size_hint_x=0.2, on_press=lambda _i: self._reload())
        header.add_widget(self.refresh_button)
        root.add_widget(header)

        filter_bar = BoxLayout(orientation="horizontal", size_hint_y=None, height="40dp", spacing="10dp")

        filter_bar.add_widget(Label(text="Spieler:", size_hint_x=0.07))
        # Values sind Owner-Composite-Keys (PokedexDB.build_owner-Format
        # "{your_name}_{client_id}") — reine Player-Nummern ("1", "2") matchten
        # nichts, weil die DB-Spalte owner den Composite speichert. Default
        # "alle" analog zu bagmenu, damit Erstsicht keinen Filter mitzieht.
        # Values-Update kommt aus _update_player_spinner nach _load_from_db.
        self.player_spinner = Spinner(text="alle", values=["alle"], size_hint_x=0.12)
        self.player_spinner.bind(text=lambda _i, _v: self._apply_filters())
        filter_bar.add_widget(self.player_spinner)

        filter_bar.add_widget(Label(text="Edition:", size_hint_x=0.07))
        self.edition_spinner = Spinner(text="alle", values=["alle"], size_hint_x=0.13)
        self.edition_spinner.bind(text=lambda _i, _v: self._apply_filters())
        filter_bar.add_widget(self.edition_spinner)

        filter_bar.add_widget(Label(text="Suche:", size_hint_x=0.06))
        self.search_input = TextInput(
            multiline=False, write_tab=False, size_hint_x=0.20,
            hint_text="Route, Location, Species …",
        )
        self.search_input.bind(text=lambda _i, _v: self._apply_filters())
        filter_bar.add_widget(self.search_input)

        self.count_label = Label(text="0 Encounters", size_hint_x=0.25)
        filter_bar.add_widget(self.count_label)
        root.add_widget(filter_bar)

        header_row = GridLayout(cols=len(COLUMNS), size_hint_y=None, height="30dp")
        for title, width in COLUMNS:
            header_row.add_widget(Label(text=f"[b]{title}[/b]", markup=True, size_hint_x=width))
        root.add_widget(header_row)

        self.empty_hint = Label(
            text="", size_hint_y=None, height=0, opacity=0,
            color=(0.7, 0.7, 0.7, 1), font_size="14sp",
        )
        root.add_widget(self.empty_hint)

        scroll = ScrollView(do_scroll_x=False, do_scroll_y=True)
        self.rows_grid = GridLayout(
            cols=1, size_hint_y=None,
            row_default_height="28dp", row_force_default=True,
        )
        self.rows_grid.bind(minimum_height=self.rows_grid.setter("height"))
        scroll.add_widget(self.rows_grid)
        root.add_widget(scroll)

        self.add_widget(root)

    def on_pre_enter(self, *args):
        self._reload()

    def _reload(self):
        self._encounters = self._load_from_db()
        self._update_edition_spinner()
        self._update_player_spinner()
        self._apply_filters()

    def _load_from_db(self) -> list[dict]:
        # DB-Load lädt IMMER alle Encounters — Player-Filter greift jetzt in
        # _apply_filters gegen die Owner-Composite-Werte. Sonst brauchten wir
        # bei jedem Spinner-Wechsel einen frischen DB-Round-Trip.
        if self.configsave is None:
            return []
        session_path = Path(str(self.configsave))
        db = PokedexDB(session_path)
        try:
            db.connect()
            if db.connection is None:
                return []
            cursor = db.connection.cursor()
            cursor.execute(
                "SELECT * FROM encounters ORDER BY route, timestamp"
            )
            return [dict(row) for row in cursor.fetchall()]
        except Exception as err:
            logger.error(f"EncounterMenu DB-Load failed: {type(err)},{err}")
            logger.error(f"{traceback.format_exc()}")
            return []
        finally:
            db.close()

    def _update_player_spinner(self):
        """Baut Spinner-Values aus distincten Owner-Composite-Keys der aktuell
        geladenen Encounters. 'alle' bleibt immer erste Option."""
        owners: set[str] = set()
        for enc in self._encounters:
            o = str(enc.get("owner", "") or "").strip()
            if o:
                owners.add(o)
        values = ["alle"] + sorted(owners)
        self.player_spinner.values = values
        if self.player_spinner.text not in values:
            self.player_spinner.text = "alle"

    def _update_edition_spinner(self):
        editions: set[int] = set()
        for enc in self._encounters:
            try:
                editions.add(int(enc.get("edition", 0)))
            except (TypeError, ValueError):
                continue
        labels = ["alle"] + [
            f"{ed} ({_EDITION_NAME.get(ed, '')})" if ed in _EDITION_NAME else str(ed)
            for ed in sorted(editions)
        ]
        self.edition_spinner.values = labels
        if self.edition_spinner.text not in labels:
            self.edition_spinner.text = "alle"

    def _parse_edition_filter(self) -> int | None:
        text = (self.edition_spinner.text or "alle").strip()
        if text == "alle":
            return None
        try:
            return int(text.split(" ")[0])
        except (ValueError, IndexError):
            return None

    def _apply_filters(self):
        edition_filter = self._parse_edition_filter()
        player_filter = (self.player_spinner.text or "alle").strip()
        query = (self.search_input.text or "").strip().lower()

        encountered_routes: dict[int, list[dict]] = {}
        for enc in self._encounters:
            if player_filter != "alle":
                if str(enc.get("owner", "") or "") != player_filter:
                    continue
            if edition_filter is not None:
                try:
                    if int(enc.get("edition", 0)) != edition_filter:
                        continue
                except (TypeError, ValueError):
                    continue
            route = int(enc.get("route", 0))
            encountered_routes.setdefault(route, []).append(enc)

        rows: list[dict] = []

        for route, encs in encountered_routes.items():
            for enc in encs:
                enc_edition = int(enc.get("edition", 0))
                status = self._determine_status(enc)
                rows.append({
                    "route": route,
                    "location": _location_name(route, enc_edition),
                    "species": _species_name(enc.get("dexnr", 0)),
                    "dexnr": enc.get("dexnr", 0),
                    "lvl": enc.get("lvl", ""),
                    "shiny": bool(enc.get("shiny", 0)),
                    "method": enc.get("method", "wild"),
                    "status": status,
                    "is_open": False,
                })

        if edition_filter is not None:
            open_routes = _locations_for_edition(edition_filter)
            for route_id in open_routes:
                if route_id not in encountered_routes:
                    rows.append({
                        "route": route_id,
                        "location": _location_name(route_id, edition_filter),
                        "species": "—",
                        "dexnr": 0,
                        "lvl": "",
                        "shiny": False,
                        "method": "—",
                        "status": "open",
                        "is_open": True,
                    })

        if query:
            filtered = []
            for row in rows:
                haystack = f"{row['route']} {row['location']} {row['species']}".lower()
                if query in haystack:
                    filtered.append(row)
            rows = filtered

        rows.sort(key=lambda r: (r["route"], 0 if not r["is_open"] else 1))

        enc_count = sum(1 for r in rows if not r["is_open"])
        open_count = sum(1 for r in rows if r["is_open"])
        self.count_label.text = f"{enc_count} Encounters, {open_count} offen"

        if not rows:
            if not self._encounters:
                self.empty_hint.text = "Noch keine Encounters — Nuzlocke starten oder auf Aktualisieren klicken."
            else:
                self.empty_hint.text = "Kein Treffer mit aktuellen Filtern."
            self.empty_hint.height = 60
            self.empty_hint.opacity = 1
        else:
            self.empty_hint.text = ""
            self.empty_hint.height = 0
            self.empty_hint.opacity = 0

        self._render_rows(rows)

    def _determine_status(self, enc: dict) -> str:
        if enc.get("is_dupes_skip", 0):
            return "dupes"
        outcome = enc.get("outcome", "unknown")
        if outcome in STATUS_TEXT:
            return outcome
        return "unknown"

    def _render_rows(self, rows: list[dict]):
        self.rows_grid.clear_widgets()
        for row in rows:
            row_widget = self._make_row(row)
            self.rows_grid.add_widget(row_widget)

    def _make_row(self, row: dict) -> BoxLayout:
        status = row["status"]
        color = STATUS_COLORS.get(status, (0.3, 0.3, 0.3, 0.10))

        row_layout = BoxLayout(
            orientation="horizontal", size_hint_y=None, height="28dp",
        )

        with row_layout.canvas.before:
            Color(*color)
            bg = Rectangle(pos=row_layout.pos, size=row_layout.size)
        row_layout.bind(
            pos=lambda w, v, bg=bg: setattr(bg, 'pos', v),
            size=lambda w, v, bg=bg: setattr(bg, 'size', v),
        )

        row_layout.add_widget(Label(
            text=str(row["route"]), size_hint_x=COLUMNS[0][1],
        ))
        row_layout.add_widget(Label(
            text=row["location"], size_hint_x=COLUMNS[1][1],
            halign="left", valign="middle",
            shorten=True, shorten_from="right",
        ))
        row_layout.add_widget(Label(
            text=row["species"], size_hint_x=COLUMNS[2][1],
        ))
        row_layout.add_widget(Label(
            text=str(row["lvl"]) if row["lvl"] else "", size_hint_x=COLUMNS[3][1],
        ))
        row_layout.add_widget(Label(
            text="*" if row["shiny"] else "", size_hint_x=COLUMNS[4][1],
        ))
        row_layout.add_widget(Label(
            text=row["method"], size_hint_x=COLUMNS[5][1],
        ))
        row_layout.add_widget(Label(
            text=STATUS_TEXT.get(status, status), size_hint_x=COLUMNS[6][1],
        ))
        return row_layout

    def _back(self, instance):
        self.manager.current = "MainMenu"
