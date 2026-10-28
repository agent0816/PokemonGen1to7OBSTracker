"""KivyMD-MapMenu — Regionskarte mit Live-Position + anklickbaren Routen.

Zeigt die Regionskarte des aktuellen (Gen-3-)Spiels, markiert die Route/Stadt,
auf der der Spieler gerade steht (``map_section`` = regionMapSectionId aus dem
Positions-Payload, siehe Kartenfeature Phase 2), und blendet beim Klick auf eine
Route eine Legende ein (hier gefangenes Pokemon bzw. Route noch offen).

Koordinaten-Hinweis: die Live-x/y des Spielers sind MAP-LOKALE Tile-Koordinaten
(Position innerhalb der aktuellen Map), NICHT Regionskarten-Zellen. Auf der
Regionskarte wird der Spieler deshalb auf der Zelle seiner aktuellen
``map_section`` markiert (die Hotspot-Geometrie stammt aus backend/data/maps/).
Die feinen x/y sind für spätere Innenraum-Detailkarten reserviert.
"""
import os
import traceback
from pathlib import Path
from types import SimpleNamespace

import yaml
from kivy.clock import Clock
from kivy.core.image import Image as CoreImage
from kivy.graphics import Color, Line, Rectangle, Ellipse
from kivy.metrics import dp
from kivy.uix.widget import Widget
from kivymd.uix.boxlayout import MDBoxLayout
from kivymd.uix.button import MDButton, MDButtonText
from kivymd.uix.fitimage import FitImage
from kivymd.uix.label import MDLabel
from kivymd.uix.menu import MDDropdownMenu
from kivymd.uix.screen import MDScreen
from kivymd.uix.scrollview import MDScrollView

from backend.logging_setup import get_logger
from backend.perf_probe import perf_span
from backend.pokemon_detail_view import resource_path
from backend.classes.pokedex_db import PokedexDB

logger = get_logger(__name__, 'logs/mapmenu.log')

# Edition → Hotspot-YAML. Section-Key je Region = der Live-Place-ID-Raum:
# Gen 3 = regionMapSectionId (MAPSEC), Gen 4 Sinnoh = met_location (== mapLabelTextID,
# via mapid_section_gen4.yml). Gen 4 Sinnoh: Diamant/Perl/Platin (41–43), eine
# gemeinsame Karte; D/P-Abweichungen der Fangquellen über pokemon_by_game. HGSS
# (44/45): eine Pokégear-Karte Johto+Kanto. Live-Position für alle Gen-4-
# Editionen (Offsets in pointer_gen4.yml). ORAS (63/64) nutzt ein anderes
# Zonen-System und ist NICHT dabei.
EDITION_REGION = {
    31: "hoenn_rse.yml", 32: "hoenn_rse.yml", 33: "hoenn_rse.yml",
    34: "kanto_frlg.yml", 35: "kanto_frlg.yml",
    41: "sinnoh_dppt.yml", 42: "sinnoh_dppt.yml", 43: "sinnoh_dppt.yml",
    44: "johto_kanto_hgss.yml", 45: "johto_kanto_hgss.yml",
}
EDITION_LUT = {
    "Rubin": 31, "Saphir": 32, "Smaragd": 33, "Feuerrot": 34, "Blattgrün": 35,
    "Diamant": 41, "Perl": 42, "Platin": 43, "Herzgold": 44, "Seelensilber": 45,
}
MAPS_DATA_DIR = os.path.join("backend", "data", "maps")
# Zusätzliche Teilkarten je Edition (neben EDITION_REGION = Standardkarte). Das
# MapMenu wechselt automatisch dorthin, sobald die Live-map_section in einer
# Teilkarte liegt (FRLG: Sevii-Inseln 1–3 / 4–5 / 6–7, eigene In-Game-Karten).
_SEVII = ["sevii_123_frlg.yml", "sevii_45_frlg.yml", "sevii_67_frlg.yml"]
EDITION_SUBREGIONS = {34: _SEVII, 35: _SEVII}
# Region-Datei → Section-Keys (lazy, für den Teilkarten-Wechsel per Position).
_REGION_SECTIONS: dict[str, set[int]] = {}
# Hotspot-Feld ``pokemon`` (Quellen laut Decomp) → Anzeige in der Legende.
SOURCE_LABELS = {
    "land": "Wild", "wild": "Wild", "surf": "Surfer", "fish": "Angeln",
    "rock_smash": "Zertrümmerer", "static": "Statisch", "gift": "Geschenk",
    "trade": "Tausch",
}


class MapCanvas(Widget):
    """Zeichnet die Regionskarte (pixelgenau skaliert, Seitenverhältnis gewahrt)
    mit Hotspot-Umrissen, Marker auf der aktuellen map_section und Klick-Hit-Test.

    Format-agnostisch: arbeitet rein in NATIVEN BILD-PIXELN. Jeder Hotspot ist
    ``{"label": str, "rects": [(x, y, w, h), ...]}`` mit Rects in Bild-Pixeln
    (Ursprung oben-links). Gen 3 (eine Grid-Zelle) und Gen 4 Sinnoh (mehrere
    7px-Zellen pro Route mit Pixel-Offset) liefern dasselbe Rect-Modell — die
    Umrechnung YAML→Rects macht der Adapter in ``MapMenu._load_region``. Die
    Bildgröße (img_w/img_h) kommt aus der Textur, auf die sich die Rects beziehen.
    """

    def __init__(self, on_select=None, **kwargs):
        super().__init__(**kwargs)
        self._texture = None
        self.hotspots: dict[int, dict] = {}
        self.img_w = 1
        self.img_h = 1
        self.current_section: int | None = None
        self.selected_sections: list[int] = []
        self.on_select = on_select
        self._img_rect = (0, 0, 0, 0)  # ix, iy, iw, ih (letterbox-Rect)
        self.bind(size=self._redraw, pos=self._redraw)

    def load_map(self, image_path: str, hotspots: dict):
        self.hotspots = hotspots or {}
        self.current_section = None
        self.selected_sections = []
        self._texture = None
        self.img_w = 1
        self.img_h = 1
        try:
            if image_path and os.path.isfile(image_path):
                cimg = CoreImage(image_path)
                tex = cimg.texture
                # Pixel-Art knackig halten statt verschwommen skalieren.
                tex.mag_filter = "nearest"
                tex.min_filter = "nearest"
                self._texture = tex
                self.img_w = max(1, tex.width)
                self.img_h = max(1, tex.height)
                logger.info(
                    f"load_map: Textur {self.img_w}x{self.img_h} geladen "
                    f"aus {image_path}")
            else:
                logger.warning(
                    f"load_map: Kartenbild fehlt oder ist keine Datei: "
                    f"{image_path!r} (hotspots={len(self.hotspots)})")
        except Exception as err:
            logger.error(f"Kartenbild laden gescheitert ({image_path}): {err}")
            logger.error(traceback.format_exc())
        self._redraw()

    def clear_map(self):
        self.hotspots = {}
        self._texture = None
        self.img_w = 1
        self.img_h = 1
        self.current_section = None
        self.selected_sections = []
        self._redraw()

    # --- Rendering ------------------------------------------------------------

    def _fit_rect(self):
        """Letterbox-Rect (ix, iy, iw, ih) für das Bild-Seitenverhältnis."""
        w, h = self.width, self.height
        if w <= 0 or h <= 0:
            return (self.x, self.y, 0, 0)
        aspect = self.img_w / self.img_h
        if w / h > aspect:
            ih = h
            iw = h * aspect
        else:
            iw = w
            ih = w / aspect
        ix = self.x + (w - iw) / 2
        iy = self.y + (h - ih) / 2
        return (ix, iy, iw, ih)

    def _px_rect(self, x, y, w, h):
        """Bild-Pixel-Rect → Screen-Rect (y von oben nach unten gespiegelt)."""
        ix, iy, iw, ih = self._img_rect
        sx = iw / self.img_w
        sy = ih / self.img_h
        rx = ix + x * sx
        ry = iy + ih - (y + h) * sy
        return (rx, ry, w * sx, h * sy)

    def _rects_bbox_center(self, rects):
        """Screen-Mittelpunkt + kleinste Rect-Kantenlänge über alle Zellen eines
        Hotspots (für den Positions-Marker: Punkt in der Zellen-Bounding-Box)."""
        xs0, ys0, xs1, ys1 = [], [], [], []
        min_side = None
        for (x, y, w, h) in rects:
            sx, sy, sw, sh = self._px_rect(x, y, w, h)
            xs0.append(sx); ys0.append(sy); xs1.append(sx + sw); ys1.append(sy + sh)
            side = min(sw, sh)
            min_side = side if min_side is None else min(min_side, side)
        cx = (min(xs0) + max(xs1)) / 2
        cy = (min(ys0) + max(ys1)) / 2
        return cx, cy, (min_side or 0)

    def _redraw(self, *args):
        self.canvas.clear()
        self._img_rect = self._fit_rect()
        ix, iy, iw, ih = self._img_rect
        if iw <= 0 or ih <= 0:
            return
        with self.canvas:
            Color(0.07, 0.09, 0.13, 1)
            Rectangle(pos=(self.x, self.y), size=(self.width, self.height))
            if self._texture is None:
                return
            Color(1, 1, 1, 1)
            Rectangle(texture=self._texture, pos=(ix, iy), size=(iw, ih))
            # Hotspot-Umrisse (dezent) über alle Zellen jeder Section; Orte ohne
            # fang-/erhaltbare Pokemon (pokemon: []) noch schwächer.
            for empty, alpha in ((True, 0.07), (False, 0.18)):
                Color(1, 1, 1, alpha)
                for ms, e in self.hotspots.items():
                    if (e.get("pokemon") == []) != empty:
                        continue
                    for (x, y, w, h) in e["rects"]:
                        sx, sy, sw, sh = self._px_rect(x, y, w, h)
                        Line(rectangle=(sx, sy, sw, sh), width=1)
            # Ausgewählte Section(s) (Gold) — bei überlappenden Zellen alle.
            Color(1, 0.85, 0.2, 1)
            for ms in self.selected_sections:
                for (x, y, w, h) in self.hotspots.get(ms, {}).get("rects", ()):
                    sx, sy, sw, sh = self._px_rect(x, y, w, h)
                    Line(rectangle=(sx, sy, sw, sh), width=1.6)
            # Positions-Marker auf der aktuellen map_section: Zellen füllen +
            # Punkt in der Bounding-Box-Mitte.
            if self.current_section in self.hotspots:
                rects = self.hotspots[self.current_section]["rects"]
                Color(0.2, 0.55, 1, 0.30)
                for (x, y, w, h) in rects:
                    sx, sy, sw, sh = self._px_rect(x, y, w, h)
                    Rectangle(pos=(sx, sy), size=(sw, sh))
                cx, cy, min_side = self._rects_bbox_center(rects)
                r = max(dp(6), min_side * 0.45)
                Color(1, 1, 1, 1)
                Ellipse(pos=(cx - r - dp(1.5), cy - r - dp(1.5)),
                        size=(2 * (r + dp(1.5)), 2 * (r + dp(1.5))))
                Color(0.15, 0.5, 1, 1)
                Ellipse(pos=(cx - r, cy - r), size=(2 * r, 2 * r))

    # --- Interaktion ----------------------------------------------------------

    def set_current_section(self, section: int | None):
        if section != self.current_section:
            self.current_section = section
            self._redraw()

    def _sections_at(self, px, py) -> list[int]:
        """Alle Sections unter dem Screen-Punkt (YAML-Reihenfolge). Mehrere,
        wenn Dungeons/Seen/Landmarks die Zelle mit Stadt/Route teilen."""
        ix, iy, iw, ih = self._img_rect
        if iw <= 0 or not (ix <= px <= ix + iw and iy <= py <= iy + ih):
            return []
        # Screen → Bild-Pixel.
        img_x = (px - ix) / (iw / self.img_w)
        img_y = (iy + ih - py) / (ih / self.img_h)  # von oben gezählt
        hits = []
        for ms, e in self.hotspots.items():
            for (x, y, w, h) in e["rects"]:
                if x <= img_x < x + w and y <= img_y < y + h:
                    hits.append(ms)
                    break
        return hits

    def on_touch_down(self, touch):
        if not self.collide_point(*touch.pos):
            return super().on_touch_down(touch)
        hits = self._sections_at(*touch.pos)
        # ``linked``: kleine Orte (z.B. Pyroberg auf Route 122) beim Klick auf
        # den Elternort mit anzeigen, damit sie nicht pixelgenau getroffen
        # werden müssen.
        for ms in list(hits):
            for linked in self.hotspots[ms].get("linked") or ():
                if linked in self.hotspots and linked not in hits:
                    hits.append(linked)
        if hits:
            self.selected_sections = hits
            self._redraw()
            if self.on_select:
                self.on_select(hits)
            return True
        return super().on_touch_down(touch)


class MapMenu(MDScreen):
    def __init__(self, munchlax, obs_websocket, pl, configsave, **kwargs):
        super().__init__(**kwargs)
        self.name = "MapMenu"
        self.munchlax = munchlax
        self.obs_websocket = obs_websocket
        self.pl = pl
        self.configsave = configsave
        # Start-Slot = erster lokaler Slot (nicht hart 1): in kopierten
        # Mehrspieler-Sessions ist Slot 1 oft remote (siehe _default_player).
        self.current_player: int = self._default_player()
        self._poll_ev = None
        self._tasks: set = set()
        self._menus: list[MDDropdownMenu] = []
        # {mapsec: [encounter-row-dict, ...]} des aktuellen Spielers.
        self._route_catches: dict[int, list[dict]] = {}
        self._region_file: str | None = None
        self._loaded_edition: int | None = None
        # Generationszähler gegen out-of-order Encounter-Loads (Spielerwechsel).
        self._load_gen = 0

        root = MDBoxLayout(orientation="vertical", padding="10dp", spacing="8dp")

        header = MDBoxLayout(orientation="horizontal", size_hint_y=None,
                             height="48dp", spacing="10dp")
        self.title_label = MDLabel(text="Karte", role="large", valign="center")
        header.add_widget(self.title_label)
        self.player_button_text = MDButtonText(text="Spieler: 1")
        self.player_button = MDButton(self.player_button_text, style="tonal",
                                      pos_hint={"center_y": .5})
        self.player_button.bind(on_release=lambda btn: self._open_player_menu(btn))
        header.add_widget(self.player_button)
        root.add_widget(header)

        self.map_canvas = MapCanvas(on_select=self._on_section_selected, size_hint_y=0.68)
        root.add_widget(self.map_canvas)

        # Legende/Detail-Panel unter der Karte.
        self.legend_title = MDLabel(text="Route anklicken für Details",
                                    size_hint_y=None, height="26dp", valign="center")
        self.legend_title.role = "small"
        root.add_widget(self.legend_title)

        self.legend_scroll = MDScrollView(size_hint_y=0.32, do_scroll_x=False)
        self.legend_box = MDBoxLayout(orientation="vertical", size_hint_y=None,
                                      spacing="6dp", padding=(0, dp(2)))
        self.legend_box.bind(minimum_height=self.legend_box.setter("height"))
        self.legend_scroll.add_widget(self.legend_box)
        root.add_widget(self.legend_scroll)

        self.add_widget(root)

    # --- Lifecycle ------------------------------------------------------------

    def on_pre_enter(self, *args):
        self._refresh_player_button()
        self._load_region()
        self._schedule_catch_load()

    def on_enter(self, *args):
        if self._poll_ev is None:
            self._poll_ev = Clock.schedule_interval(self._poll_position, 0.5)
        self._poll_position()

    def on_leave(self, *args):
        if self._poll_ev is not None:
            self._poll_ev.cancel()
            self._poll_ev = None

    def load_from_session(self) -> None:
        """Session-Wechsel-Hook: zurück auf den ersten lokalen Slot, Region neu laden."""
        self.current_player = self._default_player()
        self._refresh_player_button()
        self._load_region()
        self._schedule_catch_load()

    # --- Spieler-Umschalter ---------------------------------------------------

    def _player_count(self) -> int:
        try:
            return max(1, int(self.pl.get("player_count", 1)))
        except (TypeError, ValueError):
            return 1

    def _default_player(self) -> int:
        """Start-Slot der Karte: erster LOKAL bediente Slot, sonst 1. In einer
        aus einer Mehrspieler-Session kopierten Config ist Slot 1 oft ein
        Remote-Slot (remote_1=True) ohne Live-Daten — defaultete die Karte dann
        hart auf 1, ließe sich die Edition offline nicht auflösen (edition=0 →
        keine Regionskarte, obwohl das eigene Spiel korrekt angezeigt wird).
        Der eigene Slot (z.B. Spieler 4) fällt dagegen auf session_game zurück."""
        try:
            owned = [p for p in self.munchlax._owned_player_ids()
                     if 1 <= p <= self._player_count()]
        except Exception:
            owned = []
        return owned[0] if owned else 1

    def _refresh_player_button(self):
        if self.current_player > self._player_count():
            self.current_player = self._default_player()
        self.player_button_text.text = f"Spieler: {self.current_player}"
        self.player_button.disabled = self._player_count() <= 1

    def _open_player_menu(self, caller):
        items = [{
            "text": f"Spieler {p}",
            "on_release": (lambda p=p: self._select_player(p)),
        } for p in range(1, self._player_count() + 1)]
        menu = MDDropdownMenu(caller=caller, items=items)
        self._menus.append(menu)
        menu.open()

    def _select_player(self, player: int):
        for menu in self._menus:
            menu.dismiss()
        self._menus.clear()
        self.current_player = player
        self._refresh_player_button()
        self._load_region()
        self._schedule_catch_load()
        self._poll_position()

    # --- Region/Edition -------------------------------------------------------

    def _edition_with_source(self) -> tuple[int, str]:
        """Edition des gewählten Spielers + Quelle (für Diagnose-Logging).
        Priorität:
        1. edition aus dem Positions-Payload (zuverlässigste Live-Quelle, auch
           für Remote-Spieler — selber Tick wie map_section),
        2. snapshot_editions() (vom Team gemeldet),
        3. nur für LOKAL bediente Slots die konfigurierte session_game — für
           Remote-Slots NICHT auf die lokale Edition raten (0 = unbekannt)."""
        try:
            pos = self.munchlax.snapshot_positions().get(self.current_player)
        except Exception:
            pos = None
        if isinstance(pos, dict) and pos.get("edition"):
            try:
                return int(pos["edition"]), "position_payload"
            except (TypeError, ValueError):
                pass
        try:
            ed = int(self.munchlax.snapshot_editions().get(self.current_player, 0) or 0)
        except (TypeError, ValueError, AttributeError):
            ed = 0
        if ed:
            return ed, "snapshot_editions"
        if self._is_local_player(self.current_player):
            return EDITION_LUT.get(self.pl.get("session_game"), 0), "session_game(local)"
        return 0, "unaufgelöst(remote/keine-daten)"

    def _edition(self) -> int:
        return self._edition_with_source()[0]

    def _is_local_player(self, player: int) -> bool:
        try:
            return player in set(self.munchlax._owned_player_ids())
        except Exception:
            return True  # Solo/kein Netz → als lokal behandeln

    @perf_span("map._load_region")
    def _load_region(self, edition: int | None = None, region_file: str | None = None):
        """Lädt die Karte der Edition. Ohne ``region_file``: die Karte, auf der
        die Live-Position liegt (Teilkarte aus EDITION_SUBREGIONS), sonst die
        Standardkarte. Mit ``region_file`` (reiner Teilkarten-Wechsel, gleiche
        Edition): Encounter-Daten bleiben erhalten, kein DB-Reload nötig."""
        if region_file is not None:
            source = "teilkarte"
        elif edition is None:
            edition, source = self._edition_with_source()
        else:
            source = "poll_payload"
        same_edition = region_file is not None and edition == self._loaded_edition
        self._loaded_edition = edition
        # Alt-Daten verwerfen, damit ein Klick direkt nach dem Wechsel nicht
        # kurz die Encounter des vorigen Spielers/Spiels zeigt.
        if not same_edition:
            self._route_catches = {}
        if region_file is None:
            # Direkt die Teilkarte der Live-Position wählen (kein Kanto-Flash,
            # wenn der Spieler beim Betreten des Screens auf Sevii steht).
            live = self._live_section()
            region_file = ((self._region_for_section(edition, live) if live is not None
                            else None) or EDITION_REGION.get(edition))
        self._region_file = region_file
        # Diagnose-Logging (Kartenfeature, alpha-Debug): festhalten, WIE die
        # Edition aufgelöst wurde und ob es überhaupt eine Regionskarte gibt.
        # So wird "Karte lädt nicht, obwohl das Spiel korrekt angezeigt wird"
        # nachvollziehbar (z.B. aus Munchlax kopierte Session mit Remote-Slots:
        # Edition landet dann nur im Team-Display, nicht im Positions-/Editions-
        # Snapshot des Karten-Slots → source=unaufgelöst, edition=0).
        try:
            pos_snap = self.munchlax.snapshot_positions().get(self.current_player)
        except Exception:
            pos_snap = None
        try:
            ed_snap = self.munchlax.snapshot_editions().get(self.current_player)
        except Exception:
            ed_snap = None
        logger.info(
            f"_load_region: player={self.current_player} "
            f"local={self._is_local_player(self.current_player)} "
            f"edition={edition} (source={source}) "
            f"session_game={self.pl.get('session_game')!r} "
            f"snapshot_editions[{self.current_player}]={ed_snap!r} "
            f"position={pos_snap!r} region_file={region_file!r}"
        )
        self._clear_legend("Route anklicken für Details")
        if region_file is None:
            logger.warning(
                f"_load_region: keine Regionskarte für edition={edition} "
                f"(source={source}) — unterstützte Editionen: {sorted(EDITION_REGION)}"
            )
            self.map_canvas.clear_map()
            self.title_label.text = "Karte"
            self.legend_title.text = (
                "Für dieses Spiel gibt es noch keine Regionskarte "
                "(aktuell Gen 3: Kanto/Hoenn/Sevii, Gen 4: Sinnoh und Johto/Kanto)."
            )
            return
        try:
            yaml_path = resource_path(os.path.join(MAPS_DATA_DIR, region_file))
            with open(yaml_path, encoding="utf-8") as fh:
                data = yaml.safe_load(fh)
            hotspots = self._build_hotspots(data, edition)
            _REGION_SECTIONS[region_file] = set(hotspots)
            image_rel = data.get("image", "")
            image_path = resource_path(
                os.path.join("backend", "ressources", "maps", image_rel))
            img_ok = bool(image_path) and os.path.isfile(image_path)
            logger.info(
                f"_load_region: region_file={region_file} geladen — "
                f"image={image_rel!r} image_path={image_path!r} exists={img_ok} "
                f"hotspots={len(hotspots)}"
            )
            self.map_canvas.load_map(image_path, hotspots)
            title = data.get("title") or data.get("region", "").title()
            self.title_label.text = f"Karte — {title}"
            self.legend_title.text = "Route anklicken für Details"
        except Exception as err:
            logger.error(f"Region {region_file} laden gescheitert: {err}")
            logger.error(traceback.format_exc())
            self.map_canvas.clear_map()
            self.legend_title.text = "Karte konnte nicht geladen werden."

    @staticmethod
    def _build_hotspots(data: dict, edition: int | None = None) -> dict[int, dict]:
        """Wandelt ein Hotspot-YAML in das Pixel-Rect-Modell der MapCanvas.

        Unterstützt zwei Formate:
        - Gen 3 (grid + Rect): Eintrag ``{x, y, width, height}`` in Grid-Zellen;
          Pixel = ``x*tile_px + x_offset`` / ``y*tile_px + y_offset`` (Default 8,
          Offset 0). Kanto/Hoenn nutzen jetzt die echten In-Game-Spielkarten als
          Bild; diese sind der alte Decomp-Crop rechts+unten erweitert, Offset 0
          (via Raw-/Edge-/Land-Masken-Match bestaetigt). Der Offset bleibt
          generisch verfuegbar, falls ein Bild doch verschoben werden muss.
        - Gen 4 Sinnoh (cells + Offset): Eintrag ``{cells: [[x, z], ...]}``;
          Pixel = ``x*tile_px + x_offset`` / ``z*tile_px + y_offset`` je Zelle
          (``tile_px``/``x_offset``/``y_offset`` auf Datei-Ebene).
        - Override ``{px_rects: [[x, y, w, h], ...]}``: direkt native Bild-Pixel,
          für Orte, die nicht aufs Zellen-Raster fallen (z.B. Voll-/Neumondinsel).

        ``pokemon_by_game: {edition: [...]}`` ersetzt ``pokemon`` für die
        genannten Editionen (z.B. D/P-Skripte weichen von Platin ab).
        ``linked: [section, ...]`` zeigt diese Orte beim Klick mit an.
        """
        tile_px = int(data.get("tile_px", 8))
        x_off = int(data.get("x_offset", 0))
        y_off = int(data.get("y_offset", 0))
        hotspots: dict[int, dict] = {}
        for k, e in (data.get("entries") or {}).items():
            sec = int(k)
            if "px_rects" in e:
                rects = [tuple(int(v) for v in r) for r in e["px_rects"]]
            elif "cells" in e:
                rects = [(int(cx) * tile_px + x_off, int(cz) * tile_px + y_off,
                          tile_px, tile_px) for cx, cz in e["cells"]]
            else:
                rects = [(int(e["x"]) * tile_px + x_off,
                          int(e["y"]) * tile_px + y_off,
                          int(e.get("width", 1)) * tile_px,
                          int(e.get("height", 1)) * tile_px)]
            by_game = e.get("pokemon_by_game") or {}
            pokemon = by_game[edition] if edition in by_game else e.get("pokemon")
            hotspots[sec] = {
                "label": e.get("label"),
                "rects": rects,
                "games": e.get("games"),
                # Quellen laut Decomp (land/surf/…); [] = nichts fangbar,
                # None = unbekannt (Feld fehlt) → wie fangbar behandeln.
                "pokemon": pokemon,
                "linked": [int(s) for s in e.get("linked") or ()],
            }
        return hotspots

    @staticmethod
    def _region_sections(region_file: str) -> set[int]:
        """Section-Keys einer Region-Datei. Gecacht (auch aus _load_region
        befüllt); ein Lesefehler wird NICHT gecacht, damit ein transienter
        Fehler den Teilkarten-Wechsel nicht bis zum Neustart abschaltet."""
        if region_file not in _REGION_SECTIONS:
            try:
                path = resource_path(os.path.join(MAPS_DATA_DIR, region_file))
                with open(path, encoding="utf-8") as fh:
                    data = yaml.safe_load(fh) or {}
                _REGION_SECTIONS[region_file] = {int(k) for k in (data.get("entries") or {})}
            except Exception as err:
                logger.error(f"_region_sections({region_file}) gescheitert: {err}")
                logger.error(traceback.format_exc())
                return set()
        return _REGION_SECTIONS[region_file]

    def _live_section(self) -> int | None:
        """Aktuelle map_section des gewählten Spielers (None = unbekannt)."""
        try:
            pos = self.munchlax.snapshot_positions().get(self.current_player)
        except Exception:
            return None
        return pos.get("map_section") if isinstance(pos, dict) else None

    def _region_for_section(self, edition: int | None, section: int) -> str | None:
        """Karte der Edition (Standard + Teilkarten), die ``section`` enthält."""
        candidates = [EDITION_REGION.get(edition)] + EDITION_SUBREGIONS.get(edition, [])
        for region_file in candidates:
            if region_file and section in self._region_sections(region_file):
                return region_file
        return None

    # --- Position -------------------------------------------------------------

    def _poll_position(self, *args):
        try:
            positions = self.munchlax.snapshot_positions()
        except Exception:
            positions = {}
        pos = positions.get(self.current_player)
        # Region NUR bei einer KONKRETEN neuen Edition aus dem Payload wechseln
        # (Emulator-Spielwechsel / Remote-Spieler mit anderer Edition). Fehlt
        # pos/edition (Spieler offline, Disconnect-Clear, noch kein Tick), die
        # zuletzt geladene Region behalten und nur den Marker entfernen — sonst
        # würde die Karte irreführend geleert ("kein Kartenmaterial").
        if isinstance(pos, dict) and pos.get("edition"):
            try:
                ed = int(pos["edition"])
            except (TypeError, ValueError):
                ed = None
            if ed is not None and ed != self._loaded_edition:
                self._load_region(ed)
                self._schedule_catch_load()
        section = pos.get("map_section") if isinstance(pos, dict) else None
        # Teilkarten-Wechsel (FRLG Kanto ↔ Sevii): liegt die Live-Section nicht
        # auf der geladenen Karte, aber auf einer anderen Karte der Edition,
        # dorthin umschalten. Unbekannte Sections (Innenraum/MAPSEC_NONE) lassen
        # die aktuelle Karte stehen.
        if section is not None and section not in self.map_canvas.hotspots:
            target = self._region_for_section(self._loaded_edition, section)
            if target is not None and target != self._region_file:
                logger.info(f"_poll_position: section={section} → Teilkarte {target}")
                # Gleiche Edition + Slot → Encounter-Daten bleiben, kein Reload.
                self._load_region(self._loaded_edition, region_file=target)
        # Nur bei Wechsel loggen (sonst 2×/s Spam): zeigt, ob überhaupt eine
        # map_section ankommt. Fehlt sie trotz Position (z.B. FR/LG-Innenraum =
        # MAPSEC_NONE, oder kein mapheaderpointer), bleibt der Marker aus.
        if section != self.map_canvas.current_section:
            ed = pos.get("edition") if isinstance(pos, dict) else None
            logger.debug(
                f"_poll_position: player={self.current_player} "
                f"section={section!r} edition={ed!r}")
        self.map_canvas.set_current_section(section)

    # --- Legende (Encounter pro Route) ---------------------------------------

    def _schedule_catch_load(self):
        """Lädt die Encounter des aktuellen Spielers gruppiert nach Route im
        Executor (DB-I/O nicht im Kivy-Loop). No-op ohne laufenden asyncio-Loop
        (z.B. GUI-Test ohne async_run)."""
        import asyncio
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._load_gen += 1
        gen = self._load_gen
        edition = self._edition()
        session = str(self.configsave) if self.configsave is not None else ""
        slot = self.current_player
        task = loop.create_task(self._load_catches(gen, session, slot, edition))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _load_catches(self, gen: int, session: str, slot: int, edition: int):
        import asyncio
        try:
            rows = await asyncio.get_running_loop().run_in_executor(
                None, self._query_catches, session, edition)
        except Exception as err:
            logger.error(f"Encounter-Load für Karte gescheitert: {err}")
            logger.error(traceback.format_exc())
            return
        if gen != self._load_gen:
            return  # neuerer Load (Spielerwechsel) hat Vorrang
        grouped: dict[int, list[dict]] = {}
        for row in rows:
            # Slot-Filter statt exaktem Owner: der DB-owner ist 'name_<slot>',
            # der lokale your_name stimmt bei Remote-Spielern NICHT (KRITISCH).
            # method-Filter gegen die Route-Namespace-Kollision: nur Wild-Routen
            # sind auf MAPSEC (= Hotspot-Key) gekeyt; Gift/Starter nutzen andere
            # ID-Räume und würden sonst unter fremden Routen auftauchen.
            if PokedexDB.owner_slot(row.get("owner")) != slot:
                continue
            if row.get("method") not in (None, "wild"):
                continue
            try:
                grouped.setdefault(int(row.get("route")), []).append(row)
            except (TypeError, ValueError):
                continue
        self._route_catches = grouped
        # Legende nachziehen, falls schon eine Route gewählt war, bevor die
        # Daten da waren (sonst bliebe sie fälschlich auf "Route frei").
        sel = self.map_canvas.selected_sections
        if sel:
            self._on_section_selected(sel)

    @staticmethod
    def _query_catches(session: str, edition: int) -> list[dict]:
        """Alle Encounter der Edition (owner-übergreifend) — der Slot-Filter
        läuft danach in _load_catches. Eigene DB-Instanz im Executor-Thread."""
        db = None
        try:
            db = PokedexDB(Path(session))
            if db.connection is None:
                return []
            cur = db.connection.cursor()
            if edition:
                cur.execute("SELECT * FROM encounters WHERE edition = ?",
                            (int(edition),))
            else:
                cur.execute("SELECT * FROM encounters")
            return [dict(r) for r in cur.fetchall()]
        except Exception as err:
            logger.error(f"_query_catches gescheitert: {err}")
            logger.error(traceback.format_exc())
            return []
        finally:
            try:
                if db is not None:
                    db.close()
            except Exception:
                pass

    def _on_section_selected(self, mapsecs: int | list[int]):
        """Legende für die angeklickte(n) Section(s). Teilen sich mehrere
        Sections die Zelle (Dungeon auf Route, See am Ufer), zeigt die
        Sammel-Legende jede mit eigener Zwischenüberschrift."""
        if isinstance(mapsecs, int):
            mapsecs = [mapsecs]
        hotspots = self.map_canvas.hotspots
        labels = [hotspots.get(ms, {}).get("label") or f"MAPSEC {ms}" for ms in mapsecs]
        self.legend_title.text = " / ".join(labels)
        self.legend_box.clear_widgets()
        edition = self._edition()
        multi = len(mapsecs) > 1
        for ms, label in zip(mapsecs, labels):
            sources = hotspots.get(ms, {}).get("pokemon")
            src_txt = self._sources_text(sources)
            if multi:
                head_txt = f"{label}  ·  {src_txt}" if src_txt else label
                head = MDLabel(text=head_txt, size_hint_y=None, height="22dp",
                               valign="center", bold=True)
                head.role = "small"
                self.legend_box.add_widget(head)
            elif src_txt:
                self.legend_title.text = f"{label}  ·  {src_txt}"
            rows = self._route_catches.get(ms, [])
            # Nur echte Wild-Erstencounter als "hier gefangen" zeigen.
            caught = [r for r in rows if r.get("outcome") in ("caught", "obtained")]
            if not caught:
                text = ("Hier gibt es keine Pokemon" if sources == []
                        else "Route frei — noch kein Pokemon gefangen")
                self.legend_box.add_widget(self._legend_line(None, text, edition))
                continue
            for r in caught:
                nick = r.get("nickname") or ""
                lvl = r.get("lvl")
                txt = nick or f"Dex #{r.get('dexnr')}"
                if lvl:
                    txt += f"  Lv {lvl}"
                self.legend_box.add_widget(self._legend_line(r, txt, edition))

    @staticmethod
    def _sources_text(sources: list[str] | None) -> str:
        """Hotspot-Quellen (aus dem YAML-Feld ``pokemon``) als deutscher Kurztext."""
        if not sources:
            return ""
        if isinstance(sources, str):  # Tippfehler-Schutz: "pokemon: wild"
            sources = [sources]
        return ", ".join(SOURCE_LABELS.get(s, s) for s in sources)

    def _legend_line(self, row, text: str, edition: int) -> MDBoxLayout:
        try:
            bg = self.theme_cls.surfaceContainerHighestColor
        except Exception:
            bg = (0.16, 0.18, 0.22, 1)
        card = MDBoxLayout(orientation="horizontal", size_hint_y=None, height="44dp",
                           spacing="8dp", padding=(dp(6), 0),
                           md_bg_color=bg, radius=[dp(8)] * 4)
        sprite = FitImage(source=self._sprite_for(row, edition), fit_mode="contain",
                          size_hint=(None, 1), width=dp(40))
        card.add_widget(sprite)
        lbl = MDLabel(text=text, valign="center", shorten=True, shorten_from="right")
        lbl.role = "small"
        card.add_widget(lbl)
        return card

    def _sprite_for(self, row, edition: int) -> str:
        if row is None:
            return ""
        try:
            dexnr = row.get("dexnr")
            mon = SimpleNamespace(
                shiny=bool(row.get("shiny")),
                female=False,
                dexnr=int(dexnr) if dexnr not in (None, "", "egg") else "egg",
                form=row.get("form") or "",
            )
            return self.obs_websocket.get_sprite(mon, False, int(edition), two_pc=False)
        except Exception:
            return ""

    def _clear_legend(self, title: str):
        self.legend_title.text = title
        self.legend_box.clear_widgets()
        self.map_canvas.selected_sections = []
