import json
import logging
import sys
import traceback
from pathlib import Path

import yaml
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.checkbox import CheckBox
from kivy.uix.gridlayout import GridLayout
from kivy.uix.label import Label
from kivy.uix.screenmanager import Screen
from kivy.uix.scrollview import ScrollView
from kivy.uix.textinput import TextInput

from backend.classes.pokedex_db import PokedexDB


def _init_logging():
    logger = logging.getLogger(__name__)
    logger.setLevel(logging.INFO)

    formatter = logging.Formatter('[%(asctime)s] %(levelname)s: %(message)s')

    file_handler = logging.FileHandler('./logs/pokedexmenu.log', 'w')
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)

    return logger


logger = _init_logging()


try:
    species_de = yaml.safe_load(open("backend/data/Species_de.yaml", encoding="utf-8"))
except Exception as err:  # pragma: no cover
    logger.error(f"Species_de.yaml laden fehlgeschlagen: {err}")
    species_de = {}


COLUMNS = [
    ("DexNr", 0.08),
    ("Name", 0.16),
    ("Spitzname", 0.16),
    ("Lv.", 0.05),
    ("S", 0.04),
    ("Edition", 0.10),
    ("Slot", 0.05),
    ("Item", 0.14),
    ("Route", 0.06),
    ("PV", 0.10),
    ("Zuletzt", 0.16),
]


def species_name(dexnr_text: str) -> str:
    if dexnr_text == "egg":
        return species_de.get(0, "Ei")
    try:
        return species_de.get(int(dexnr_text), f"#{dexnr_text}")
    except (ValueError, TypeError):
        return f"#{dexnr_text}"


class PokedexMenu(Screen):
    def __init__(self, configsave, app_version, **kwargs):
        super().__init__(**kwargs)
        self.name = "PokedexMenu"
        self.configsave = configsave
        self.app_version = app_version

        self._all_rows: list[dict] = []

        root = BoxLayout(orientation="vertical", padding=("10dp", "10dp"), spacing="5dp")

        header = BoxLayout(orientation="horizontal", size_hint_y=None, height="40dp", spacing="10dp")
        self.back_button = Button(text="Zurück zum Hauptmenü", size_hint_x=0.3, on_press=self._back)
        header.add_widget(self.back_button)
        self.head_label = Label(text="Pokemon-Datenbank")
        header.add_widget(self.head_label)
        self.refresh_button = Button(text="Aktualisieren", size_hint_x=0.2, on_press=lambda _i: self._reload())
        header.add_widget(self.refresh_button)
        root.add_widget(header)

        filter_bar = BoxLayout(orientation="horizontal", size_hint_y=None, height="40dp", spacing="10dp")

        filter_bar.add_widget(Label(text="Suche:", size_hint_x=0.08))
        self.search_input = TextInput(
            multiline=False, write_tab=False, size_hint_x=0.27,
            hint_text="Name, Spitzname, Edition …",
        )
        self.search_input.bind(text=lambda _i, _v: self._apply_filters())
        filter_bar.add_widget(self.search_input)

        filter_bar.add_widget(Label(text="Slot:", size_hint_x=0.05))
        self.slot_input = TextInput(
            multiline=False, write_tab=False, size_hint_x=0.08,
            hint_text="1–4",
        )
        self.slot_input.bind(text=lambda _i, _v: self._apply_filters())
        filter_bar.add_widget(self.slot_input)

        filter_bar.add_widget(Label(text="nur Shinys:", size_hint_x=0.12))
        self.shiny_checkbox = CheckBox(size_hint_x=0.05)
        self.shiny_checkbox.bind(active=lambda _i, _v: self._apply_filters())
        filter_bar.add_widget(self.shiny_checkbox)

        self.count_label = Label(text="0 Einträge", size_hint_x=0.25)
        filter_bar.add_widget(self.count_label)
        root.add_widget(filter_bar)

        header_row = GridLayout(cols=len(COLUMNS), size_hint_y=None, height="30dp")
        for title, width in COLUMNS:
            header_row.add_widget(Label(text=f"[b]{title}[/b]", markup=True, size_hint_x=width))
        root.add_widget(header_row)

        scroll = ScrollView(do_scroll_x=False, do_scroll_y=True)
        self.rows_grid = GridLayout(cols=len(COLUMNS), size_hint_y=None, row_default_height="26dp", row_force_default=True)
        self.rows_grid.bind(minimum_height=self.rows_grid.setter("height"))
        scroll.add_widget(self.rows_grid)
        root.add_widget(scroll)

        self.add_widget(root)

    def on_pre_enter(self, *args):
        self._reload()

    def _reload(self):
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

    def _apply_filters(self):
        query = (self.search_input.text or "").strip().lower()
        slot_filter = (self.slot_input.text or "").strip()
        shiny_only = bool(self.shiny_checkbox.active)

        filtered = []
        for row in self._all_rows:
            if shiny_only and not row.get("shiny"):
                continue
            if slot_filter and str(row.get("owner", "")) != slot_filter:
                continue
            if query:
                haystack = " ".join(str(row.get(field, "")) for field in ("nickname", "edition", "dexnr")).lower()
                haystack += " " + species_name(str(row.get("dexnr", ""))).lower()
                if query not in haystack:
                    continue
            filtered.append(row)

        self.count_label.text = f"{len(filtered)} Einträge"
        self._render_rows(filtered)

    def _render_rows(self, rows: list[dict]):
        self.rows_grid.clear_widgets()
        for row in rows:
            dexnr = str(row.get("dexnr", ""))
            self.rows_grid.add_widget(Label(text=dexnr, size_hint_x=COLUMNS[0][1]))
            self.rows_grid.add_widget(Label(text=species_name(dexnr), size_hint_x=COLUMNS[1][1]))
            self.rows_grid.add_widget(Label(text=str(row.get("nickname") or ""), size_hint_x=COLUMNS[2][1]))
            self.rows_grid.add_widget(Label(text=str(row.get("lvl") or ""), size_hint_x=COLUMNS[3][1]))
            self.rows_grid.add_widget(Label(text="★" if row.get("shiny") else "", size_hint_x=COLUMNS[4][1]))
            self.rows_grid.add_widget(Label(text=str(row.get("edition") or ""), size_hint_x=COLUMNS[5][1]))
            self.rows_grid.add_widget(Label(text=str(row.get("owner") or ""), size_hint_x=COLUMNS[6][1]))
            self.rows_grid.add_widget(Label(text=str(row.get("item") or ""), size_hint_x=COLUMNS[7][1]))
            self.rows_grid.add_widget(Label(text=str(row.get("route") or ""), size_hint_x=COLUMNS[8][1]))
            pv = row.get("personality")
            pv_text = f"{pv:08X}" if isinstance(pv, int) else ""
            self.rows_grid.add_widget(Label(text=pv_text, size_hint_x=COLUMNS[9][1]))
            last_seen = str(row.get("last_seen") or "")
            # ISO-Timestamp aufhübschen: "2026-04-17T13:23:35+00:00" -> "2026-04-17 13:23"
            pretty = last_seen.replace("T", " ")[:16]
            self.rows_grid.add_widget(Label(text=pretty, size_hint_x=COLUMNS[10][1]))

    def _back(self, instance):
        self.manager.current = "MainMenu"
