"""BagMenu — zeigt den Bag-Inventory pro Spieler an.

Liest aus der PokedexDB (Schema v2: bag_inventory + bag_first_seen) und stellt
die Items pro Pocket, Edition und Spieler in einer filterbaren Tabelle dar.
Item-Namen werden aus den generationsspezifischen items*.yml-LUTs aufgeloest
(Slug-Form 'master-ball' wird als 'Master Ball' dargestellt).

Datenquelle ist die DB — die Tabelle aktualisiert sich beim Wechsel auf den
Screen oder per "Aktualisieren"-Button. Live-Updates folgen automatisch durch
den BizHawk/Citra-Bag-Refresh-Tick (alle 5 s schreibt der Reader in die DB).
"""
import asyncio
import logging
import sys
import traceback
from pathlib import Path

import yaml
from kivy.clock import Clock
from kivy.metrics import dp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.checkbox import CheckBox
from kivy.uix.gridlayout import GridLayout
from kivy.uix.image import Image
from kivy.uix.label import Label
from kivy.uix.screenmanager import Screen
from kivy.uix.scrollview import ScrollView
from kivy.uix.spinner import Spinner
from kivy.uix.textinput import TextInput

from backend.bag_decoder import POCKET_MAX_SLOTS
from backend.classes.pokedex_db import PokedexDB


def _init_logging():
    logger = logging.getLogger(__name__)
    logger.setLevel(logging.INFO)

    formatter = logging.Formatter('[%(asctime)s] %(levelname)s: %(message)s')

    file_handler = logging.FileHandler('./logs/bagmenu.log', 'w')
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)

    return logger


logger = _init_logging()


# Generation -> Item-LUT-Datei. Identisch zur Logik in pokedecoder.py /
# test_bag_decoder.py — Gen 6/7 teilen items.yml.
_GEN_ITEM_LUT_FILE = {
    1: "items1.yml",
    2: "items2.yml",
    3: "items3.yml",
    4: "items4.yml",
    5: "items5.yml",
    6: "items.yml",
    7: "items.yml",
}


def _load_item_luts() -> dict[int, dict]:
    luts: dict[int, dict] = {}
    for gen, fname in _GEN_ITEM_LUT_FILE.items():
        path = Path("backend/data") / fname
        try:
            with path.open(encoding="utf-8") as f:
                luts[gen] = yaml.safe_load(f) or {}
        except Exception as err:
            logger.warning(f"Item-LUT {fname} laden fehlgeschlagen: {err}")
            luts[gen] = {}
    return luts


_ITEM_LUTS = _load_item_luts()


# Pocket-Anzeigenamen (deutsche Bezeichnungen analog zur Pointer-YAML-Struktur).
POCKET_LABEL = {
    "tasche": "Tasche",
    "items": "Items",
    "keyitems": "Schlüssel-Items",
    "tmvm": "TM/VM",
    "medizin": "Medizin",
    "beeren": "Beeren",
    "baelle": "Bälle",
    "kampfitems": "Kampf-Items",
    "mailitems": "Mail-Items",
    "zkristalle": "Z-Kristalle",
    "pc": "PC",
}


# Pocket-Reihenfolge in der Tabelle (Tasche ganz oben fuer Gen 1, dann
# klassische Reihenfolge wie im Spielmenue).
_POCKET_ORDER = [
    "tasche", "items", "medizin", "baelle", "tmvm", "beeren",
    "keyitems", "kampfitems", "mailitems", "zkristalle", "pc",
]
_POCKET_INDEX = {p: i for i, p in enumerate(_POCKET_ORDER)}


# Edition-Nummer -> Anzeigename. Quellen: edition_lut in trainerbox.py (Gen 1-6)
# und citrahandler.py (Gen 6/7). Wenn eine Edition fehlt, wird im Section-Titel
# nur die Nummer angezeigt.
_EDITION_NAME = {
    11: "Rot", 12: "Blau", 13: "Gelb",
    21: "Gold", 22: "Silber", 23: "Kristall",
    31: "Rubin", 32: "Saphir", 33: "Smaragd", 34: "Feuerrot", 35: "Blattgrün",
    41: "Diamant", 42: "Perl", 43: "Platin", 44: "Herzgold", 45: "Seelensilber",
    51: "Schwarz", 52: "Weiß", 53: "Schwarz 2", 54: "Weiß 2",
    61: "X", 62: "Y", 63: "Omega Rubin", 64: "Alpha Saphir",
    71: "Sonne", 72: "Mond", 73: "Ultra Sonne", 74: "Ultra Mond",
}


# Layout-Konstanten fuer die Section-Berechnung (size_hint_y=None braucht
# explizite Heights, weil die Sections in einer ScrollView haengen).
_SECTION_TITLE_H = dp(28)
_POCKET_HEADER_H = dp(28)
_ITEM_ROW_H = dp(34)
_SECTION_GAP = dp(12)


def edition_to_gen(edition_str: str) -> int | None:
    try:
        return int(edition_str) // 10
    except (TypeError, ValueError):
        return None


def item_slug(edition_str: str, item_id: int) -> str | None:
    """Liefert den rohen Slug aus der items*.yml ('master-ball') oder None."""
    gen = edition_to_gen(edition_str)
    if gen is None:
        return None
    lut = _ITEM_LUTS.get(gen) or {}
    slug = lut.get(item_id)
    return str(slug) if slug else None


def item_display_name(edition_str: str, item_id: int) -> str:
    """Loest item_id ueber die generationspezifische items*.yml in einen
    'Master Ball'-Style-Namen auf. Fallback: '#<id>'.
    """
    slug = item_slug(edition_str, item_id)
    if not slug:
        return f"#{item_id}"
    # 'master-ball' -> 'Master Ball'
    return " ".join(part.capitalize() for part in slug.split("-"))


def is_ball(edition_str: str, item_id: int) -> bool:
    """True, wenn der Item-Slug auf '-ball' endet (Pokeball, Hyperball, Tauchball, ...).

    Treiber: Nuzlocke-Regel "Run startet bei Erhalt jeder Ball-Art". Funktioniert
    fuer alle Gens, weil die items*.yml-Slugs konsistent '<typ>-ball' verwenden.
    """
    slug = item_slug(edition_str, item_id)
    return bool(slug and slug.endswith("-ball"))


def pretty_iso(ts: str | None) -> str:
    if not ts:
        return ""
    # ISO-Timestamp aufhuebschen: '2026-04-17T13:23:35+00:00' -> '2026-04-17 13:23'
    return str(ts).replace("T", " ")[:16]


class BagMenu(Screen):
    def __init__(self, configsave, pl, sp, bizhawk, app_version, **kwargs):
        super().__init__(**kwargs)
        self.name = "BagMenu"
        self.configsave = configsave
        self.pl = pl
        # sp = sprites-Config; gleiche Quelle wie obs.py / trainerbox.py fuer
        # items_path. Items-Icons sind '<slug>.png' im konfigurierten
        # Verzeichnis. Leerer Pfad -> Icons werden weggelassen.
        self.sp = sp
        # Bizhawk-Server-Referenz fuer den Sonderbonbon-Schreibknopf. Nur
        # lokale Spieler bekommen den Knopf; remote angebundene Spieler haben
        # eigene Bizhawk-Instanzen, an die wir nicht herankommen.
        self.bizhawk = bizhawk
        self.app_version = app_version

        # Cache aller Zeilen aus der DB; Filter werden in-memory angewendet.
        self._all_rows: list[dict] = []
        # Sonderbonbon-Anzahl pro (owner, edition). Ueberlebt das 5s-Reload,
        # das sonst das TextInput zerstoeren und den Wert auf "1" zuruecksetzen
        # wuerde — der Nutzer haette nur waehrend des Tippens 5 s Zeit.
        self._candy_count_per_section: dict[tuple[str, str], str] = {}

        root = BoxLayout(orientation="vertical", padding=("10dp", "10dp"), spacing="5dp")

        header = BoxLayout(orientation="horizontal", size_hint_y=None, height="40dp", spacing="10dp")
        self.back_button = Button(text="Zurück zum Hauptmenü", size_hint_x=0.3, on_press=self._back)
        header.add_widget(self.back_button)
        self.head_label = Label(text="Tasche")
        header.add_widget(self.head_label)
        self.refresh_button = Button(text="Aktualisieren", size_hint_x=0.2, on_press=lambda _i: self._reload())
        header.add_widget(self.refresh_button)
        root.add_widget(header)

        filter_bar = BoxLayout(orientation="horizontal", size_hint_y=None, height="40dp", spacing="10dp")

        filter_bar.add_widget(Label(text="Spieler:", size_hint_x=0.07))
        player_values = ["alle"] + [str(i) for i in range(1, max(1, self.pl.get("player_count", 1)) + 1)]
        self.player_spinner = Spinner(text="alle", values=player_values, size_hint_x=0.08)
        self.player_spinner.bind(text=lambda _i, _v: self._on_player_change())
        filter_bar.add_widget(self.player_spinner)

        filter_bar.add_widget(Label(text="Pocket:", size_hint_x=0.07))
        # Werte werden nach jedem _reload() / Spielerwechsel auf die fuer die
        # tatsaechlich vorhandenen Editionen relevanten Pockets eingeschraenkt.
        self.pocket_spinner = Spinner(text="alle", values=["alle"], size_hint_x=0.13)
        self.pocket_spinner.bind(text=lambda _i, _v: self._apply_filters())
        filter_bar.add_widget(self.pocket_spinner)

        filter_bar.add_widget(Label(text="Suche:", size_hint_x=0.06))
        self.search_input = TextInput(
            multiline=False, write_tab=False, size_hint_x=0.18,
            hint_text="Name oder ID …",
        )
        self.search_input.bind(text=lambda _i, _v: self._apply_filters())
        filter_bar.add_widget(self.search_input)

        filter_bar.add_widget(Label(text="nur Bälle:", size_hint_x=0.08))
        self.balls_only_checkbox = CheckBox(size_hint_x=0.05)
        self.balls_only_checkbox.bind(active=lambda _i, _v: self._apply_filters())
        filter_bar.add_widget(self.balls_only_checkbox)

        self.count_label = Label(text="0 Einträge", size_hint_x=0.18)
        filter_bar.add_widget(self.count_label)
        root.add_widget(filter_bar)

        # Sections (eine pro Spieler+Edition) in vertikaler BoxLayout, alles
        # in einer ScrollView. Pockets innerhalb einer Section liegen
        # nebeneinander (siehe _build_section).
        scroll = ScrollView(do_scroll_x=False, do_scroll_y=True)
        self.content_box = BoxLayout(orientation="vertical", size_hint_y=None,
                                     spacing="10dp", padding=(0, "5dp"))
        self.content_box.bind(minimum_height=self.content_box.setter("height"))
        scroll.add_widget(self.content_box)
        root.add_widget(scroll)

        self.add_widget(root)

    def on_pre_enter(self, *args):
        self._reload()
        # Auto-Refresh waehrend Screen offen ist — die Bag-Reader schreiben im
        # 5-s-Takt in die DB, mit gleichem Intervall holen wir uns die neuesten
        # Stand-Daten. Cancel beim Screen-Verlassen.
        if not getattr(self, "_refresh_event", None):
            self._refresh_event = Clock.schedule_interval(
                lambda _dt: self._reload(), 5.0
            )

    def on_leave(self, *args):
        ev = getattr(self, "_refresh_event", None)
        if ev:
            ev.cancel()
            self._refresh_event = None

    def _reload(self):
        self._all_rows = self._load_from_db()
        self._update_pocket_spinner_values()
        self._apply_filters()

    def _on_player_change(self):
        # Spielerwechsel kann andere Editionen sichtbar machen — Pocket-Spinner
        # neu befuellen. Falls der bisher gewaehlte Pocket fuer die neue
        # Spieler-Edition gar nicht existiert, faellt _update_pocket_spinner_values
        # auf "alle" zurueck.
        self._update_pocket_spinner_values()
        self._apply_filters()

    def _editions_for_player(self, player_filter: str) -> set[int]:
        editions: set[int] = set()
        for row in self._all_rows:
            if player_filter != "alle" and str(row.get("owner", "")) != player_filter:
                continue
            try:
                editions.add(int(row.get("edition")))
            except (TypeError, ValueError):
                continue
        return editions

    def _update_pocket_spinner_values(self):
        """Beschraenkt die Pocket-Auswahl auf das, was die aktiven Editionen
        ueberhaupt unterstuetzen. Quelle ist POCKET_MAX_SLOTS aus dem Decoder —
        damit ist die UI synchron zur Decoder-Realitaet (z.B. Gen 1 nur 'tasche'
        und 'pc', Gen 5 keine 'baelle'-Pocket).
        """
        player_filter = (self.player_spinner.text or "alle").strip()
        editions = self._editions_for_player(player_filter)
        if editions:
            keys: set[str] = set()
            for ed in editions:
                keys.update(POCKET_MAX_SLOTS.get(ed, {}).keys())
            ordered = sorted(keys, key=lambda k: _POCKET_INDEX.get(k, 99))
            labels = ["alle"] + [POCKET_LABEL.get(k, k) for k in ordered]
        else:
            # Noch keine DB-Daten fuer diesen Spieler -> nur "alle" anbieten.
            labels = ["alle"]
        self.pocket_spinner.values = labels
        if self.pocket_spinner.text not in labels:
            self.pocket_spinner.text = "alle"

    def _load_from_db(self) -> list[dict]:
        """Liest bag_inventory + bag_first_seen und joint sie pro
        (owner, edition, item_id) zu einer flachen Zeilen-Liste.
        """
        if self.configsave is None:
            return []
        session_path = Path(str(self.configsave))
        db = PokedexDB(session_path)
        try:
            db.connect()
            if db.connection is None:
                return []
            # get_bag_inventory verlangt owner; wir wollen alle Spieler sehen.
            inventory = self._fetch_all_inventory(db)
            first_seen_by_owner_edition = self._fetch_all_first_seen(db)

            rows: list[dict] = []
            for inv in inventory:
                owner = inv.get("owner")
                edition = str(inv.get("edition") or "")
                item_id = int(inv.get("item_id") or 0)
                fs_map = first_seen_by_owner_edition.get((owner, edition), {})
                rows.append({
                    "owner": owner,
                    "edition": edition,
                    "pocket": inv.get("pocket") or "",
                    "item_id": item_id,
                    "qty": int(inv.get("qty") or 0),
                    "last_seen": inv.get("last_seen") or "",
                    "first_seen": fs_map.get(item_id, ""),
                })
            return rows
        except Exception as err:
            logger.error(f"BagMenu DB-Load failed: {type(err)},{err}")
            logger.error(f"{traceback.format_exc()}")
            return []
        finally:
            db.close()

    @staticmethod
    def _fetch_all_inventory(db: PokedexDB) -> list[dict]:
        """get_bag_inventory verlangt einen owner — wir wollen aber alle.
        Direktes Query gegen die Tabelle.
        """
        if db.connection is None:
            return []
        cur = db.connection.cursor()
        cur.execute("SELECT * FROM bag_inventory ORDER BY owner, edition, pocket, item_id")
        return [dict(row) for row in cur.fetchall()]

    @staticmethod
    def _fetch_all_first_seen(db: PokedexDB) -> dict[tuple[str, str], dict[int, str]]:
        """Map (owner, edition) -> {item_id: first_seen_iso}."""
        if db.connection is None:
            return {}
        cur = db.connection.cursor()
        cur.execute("SELECT owner, edition, item_id, first_seen FROM bag_first_seen")
        result: dict[tuple[str, str], dict[int, str]] = {}
        for row in cur.fetchall():
            key = (row["owner"], str(row["edition"] or ""))
            result.setdefault(key, {})[int(row["item_id"])] = row["first_seen"]
        return result

    def _apply_filters(self):
        player_filter = (self.player_spinner.text or "alle").strip()
        pocket_label = (self.pocket_spinner.text or "alle").strip()
        # Pocket-Label zurueck auf Pocket-Key abbilden
        pocket_filter = None
        if pocket_label != "alle":
            for k, v in POCKET_LABEL.items():
                if v == pocket_label:
                    pocket_filter = k
                    break
        balls_only = bool(self.balls_only_checkbox.active)
        query = (self.search_input.text or "").strip().lower()

        filtered: list[dict] = []
        for row in self._all_rows:
            if player_filter != "alle" and str(row.get("owner", "")) != player_filter:
                continue
            if pocket_filter and row.get("pocket") != pocket_filter:
                continue
            if balls_only and not is_ball(row.get("edition") or "",
                                          int(row.get("item_id") or 0)):
                continue
            if query:
                edition_str = row.get("edition") or ""
                item_id = row.get("item_id") or 0
                name = item_display_name(edition_str, item_id).lower()
                haystack = f"{name} {item_id} {edition_str}".lower()
                if query not in haystack:
                    continue
            filtered.append(row)

        # Sortierung: Spieler, Edition, Pocket-Reihenfolge wie im Spielmenue, ID
        filtered.sort(key=lambda r: (
            str(r.get("owner") or ""),
            str(r.get("edition") or ""),
            _POCKET_INDEX.get(r.get("pocket"), 99),
            int(r.get("item_id") or 0),
        ))

        self.count_label.text = f"{len(filtered)} Einträge"
        self._render_groups(filtered)

    def _render_groups(self, rows: list[dict]):
        """Gruppiert nach (Spieler, Edition) und baut pro Gruppe eine Section
        mit Pockets nebeneinander auf. Leere Pockets (nach Filter) werden
        weggelassen, damit die Spalten nicht unnoetig breit werden.
        """
        self.content_box.clear_widgets()

        groups: dict[tuple[str, str], dict[str, list[dict]]] = {}
        for row in rows:
            key = (str(row.get("owner") or ""), str(row.get("edition") or ""))
            pocket = str(row.get("pocket") or "")
            groups.setdefault(key, {}).setdefault(pocket, []).append(row)

        for owner, edition in sorted(groups.keys()):
            section = self._build_section(owner, edition, groups[(owner, edition)])
            self.content_box.add_widget(section)

    def _build_section(self, owner: str, edition: str,
                       pockets: dict[str, list[dict]]) -> BoxLayout:
        # Sortierte Pockets gemaess der spielmenue-Reihenfolge.
        pocket_keys = sorted(pockets.keys(), key=lambda k: _POCKET_INDEX.get(k, 99))
        max_items = max((len(items) for items in pockets.values()), default=0)

        # Hoehe der Section vorab ausrechnen — size_hint_y=None braucht das.
        section_height = (
            _SECTION_TITLE_H + _POCKET_HEADER_H
            + max_items * _ITEM_ROW_H + _SECTION_GAP
        )
        section = BoxLayout(orientation="vertical", size_hint_y=None,
                            height=section_height, spacing="2dp")

        edition_name = _EDITION_NAME.get(_safe_int(edition))
        title_text = f"[b]Spieler {owner} — Edition {edition}"
        if edition_name:
            title_text += f" ({edition_name})"
        title_text += "[/b]"

        title_bar = BoxLayout(orientation="horizontal", size_hint_y=None,
                              height=_SECTION_TITLE_H, spacing="6dp")
        title_bar.add_widget(Label(
            text=title_text, markup=True,
            halign="left", valign="middle",
        ))
        if self._can_write_for_owner(owner, edition):
            title_bar.add_widget(self._build_candy_controls(owner, edition))
        section.add_widget(title_bar)

        if not pocket_keys:
            return section

        grid = GridLayout(
            cols=len(pocket_keys), size_hint_y=None, spacing=("4dp", "2dp"),
        )
        grid.bind(minimum_height=grid.setter("height"))

        # Header-Zeile mit Pocket-Bezeichnungen.
        for pk in pocket_keys:
            grid.add_widget(Label(
                text=f"[b]{POCKET_LABEL.get(pk, pk)}[/b]",
                markup=True, size_hint_y=None, height=_POCKET_HEADER_H,
            ))

        # Item-Zeilen — pro "Reihe" geht's quer durch alle Pockets. Kuerzere
        # Pockets werden mit leeren Labels aufgefuellt, damit die Spalten
        # vertikal ausgerichtet bleiben.
        for i in range(max_items):
            for pk in pocket_keys:
                items = pockets[pk]
                if i < len(items):
                    grid.add_widget(self._make_item_cell(edition, items[i]))
                else:
                    grid.add_widget(Label(text="", size_hint_y=None,
                                          height=_ITEM_ROW_H))

        section.add_widget(grid)
        return section

    def _make_item_cell(self, edition: str, row: dict) -> BoxLayout:
        cell = BoxLayout(orientation="horizontal", size_hint_y=None,
                         height=_ITEM_ROW_H, spacing="4dp")
        item_id = int(row.get("item_id") or 0)
        qty = int(row.get("qty") or 0)
        items_path = (self.sp.get("items_path") if self.sp else "") or ""

        # Aktuelle Namenskonvention: '<slug>.png' (z.B. 'master-ball.png') aus
        # items*.yml. Wenn kein Slug auffindbar ist (unbekannte ID) -> kein Icon.
        slug = item_slug(edition, item_id) if items_path else None
        if slug:
            cell.add_widget(Image(
                source=f"{items_path}/{slug}.png",
                size_hint_x=None, width=dp(28),
                fit_mode="contain", allow_stretch=True,
            ))

        name = item_display_name(edition, item_id)
        label_text = f"{name}  ×{qty}" if qty > 1 else name
        cell.add_widget(Label(
            text=label_text, halign="left", valign="middle",
            shorten=True, shorten_from="right",
            text_size=(None, _ITEM_ROW_H),
        ))
        return cell

    def _can_write_for_owner(self, owner: str, edition: str) -> bool:
        """True, wenn dieser Spieler einen Sonderbonbon-Knopf bekommt.

        Bedingungen:
          - bizhawk-Server ist initialisiert
          - Spieler ist nicht remote (pl[remote_N] != True)
          - Edition ist BizHawk-faehig (Gen 1-5). Gen 6/7 laeuft ueber Citra
            und ist hier noch nicht implementiert.
        """
        if self.bizhawk is None:
            return False
        try:
            owner_int = int(owner)
            edition_int = int(edition)
        except (TypeError, ValueError):
            return False
        if self.pl.get(f"remote_{owner_int}", False):
            return False
        return edition_int < 60

    def _build_candy_controls(self, owner: str, edition: str) -> BoxLayout:
        bar = BoxLayout(orientation="horizontal", size_hint_x=None,
                        width=dp(220), spacing="4dp")
        bar.add_widget(Label(text="Sonderbonbons:", size_hint_x=None,
                             width=dp(110), halign="right", valign="middle"))
        key = (owner, edition)
        initial = self._candy_count_per_section.get(key, "1")
        count_input = TextInput(text=initial, multiline=False, input_filter="int",
                                size_hint_x=None, width=dp(45))
        # Tipp-Stand zwischen Auto-Reloads konservieren.
        count_input.bind(
            text=lambda _i, v: self._candy_count_per_section.__setitem__(key, v),
        )
        bar.add_widget(count_input)
        btn = Button(text="+", size_hint_x=None, width=dp(50))
        btn.bind(on_press=lambda _i: self._on_add_rare_candies(
            owner, edition, count_input,
        ))
        bar.add_widget(btn)
        return bar

    def _on_add_rare_candies(self, owner: str, edition: str,
                             count_input: TextInput):
        try:
            count = int((count_input.text or "0").strip())
        except ValueError:
            count = 0
        if count <= 0:
            self.count_label.text = "Anzahl muss > 0 sein."
            return
        client_id = f"player{int(owner):03d}"
        asyncio.create_task(self._do_add_rare_candies(client_id, count))

    async def _do_add_rare_candies(self, client_id: str, count: int):
        try:
            success, msg = await self.bizhawk.add_rare_candies(client_id, count)
        except Exception as err:
            logger.error(f"add_rare_candies failed: {type(err)},{err}")
            logger.error(traceback.format_exc())
            self.count_label.text = "Schreiben fehlgeschlagen — Logs pruefen."
            return
        logger.info(
            f"add_rare_candies({client_id}, +{count}): success={success}, msg={msg}"
        )
        self.count_label.text = msg
        if success:
            # Nicht direkt _reload() — der Bag-Reader-Tick im BizhawkServer
            # braucht ggf. einen Frame, bis das geschriebene Pocket auch in
            # der DB landet. Kurz warten, dann reload.
            Clock.schedule_once(lambda _dt: self._reload(), 0.5)

    def _back(self, instance):
        self.manager.current = "MainMenu"


def _safe_int(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0
