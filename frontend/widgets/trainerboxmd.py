"""KivyMD-Team-Anzeige: PokemonBoxMD (ein Slot) + TrainerBoxMD (ein Spieler-Team).

Render-Logik 1:1 aus der klassischen frontend/widgets/trainerbox.py übernommen
(Team-Tick, gesperrte Slots, KP-Farbe, Status, Orden); nur die Kivy-Widgets sind
durch KivyMD ersetzt. Statt der weakref.ids-Krücke gibt es direkte Attribut-Refs.
"""
import traceback
from typing import Callable, Optional

from kivy.clock import Clock
from kivy.metrics import dp
from kivymd.uix.boxlayout import MDBoxLayout
from kivymd.uix.card import MDCard
from kivymd.uix.fitimage import FitImage
from kivymd.uix.gridlayout import MDGridLayout
from kivymd.uix.label import MDLabel

from backend.logging_setup import get_logger
from backend.perf_probe import perf_span
from backend.party_rules import first_type_dupe_slots
from backend.player_colors import hex8_to_rgba, player_rgba
from frontend.widgets.status_labels import STATUS_LABELS, STATUS_PRIORITY

logger = get_logger(__name__, 'logs/frontend.log')

# KP-Balken-Farben nach Anteil (wie Klassik)
HP_COLOR_HIGH = (0.3, 0.69, 0.31, 1)
HP_COLOR_MID = (1.0, 0.6, 0.0, 1)
HP_COLOR_LOW = (0.96, 0.26, 0.21, 1)
HP_BG_COLOR = (0.35, 0.35, 0.35, 1)

DIM_COLOR = (0.4, 0.4, 0.4, 1)
NORMAL_COLOR = (1, 1, 1, 1)


# Höhe eines Team-Slots (Sprite links, 4 Info-Zeilen + KP-Bar rechts).
SLOT_HEIGHT = dp(92)
CARD_BG = (0.13, 0.12, 0.14, 1)   # ~#211F26, Surface-Ton (statt MDCard → kein Hover)


def _small_label(text: str = "", markup: bool = False) -> MDLabel:
    """Kompaktes, einzeiliges Label mit Kürzung statt Überlauf."""
    lbl = MDLabel(text=text, markup=markup, halign="left", valign="center",
                  shorten=True, shorten_from="right",
                  size_hint_y=None, height=dp(16))
    lbl.role = "small"
    lbl.bind(width=lambda w, v: setattr(w, "text_size", (v, None)))
    return lbl


class PokemonBoxMD(MDCard):
    """Ein Team-Slot: Sprite + Item links, Nickname/Level/KP/Status rechts."""

    def __init__(self, obs_websocket, **kwargs):
        super().__init__(style="elevated", ripple_behavior=True, orientation="horizontal",
                         padding=dp(4), spacing=dp(4), radius=[dp(8)] * 4,
                         size_hint_y=None, height=SLOT_HEIGHT, **kwargs)
        self.obs_websocket = obs_websocket
        # Vom TrainerBoxMD gesetzt: callback(slot_index)
        self.click_callback: Optional[Callable[[int], None]] = None
        self.slot_index: int = 0

        conf = obs_websocket.conf

        # Links: Sprite + Item-Bild
        sprite_box = MDBoxLayout(orientation="vertical", size_hint_x=0.4)
        self.sprite = FitImage(source=f"{conf['common_path']}/{conf['red']}/0.png",
                               fit_mode="contain")
        sprite_box.add_widget(self.sprite)
        self.item_image = FitImage(source=f"{conf['items_path']}/0.png", fit_mode="contain",
                                   size_hint_y=None, height=dp(24))
        sprite_box.add_widget(self.item_image)
        self.add_widget(sprite_box)

        # Rechts: Info
        info_box = MDBoxLayout(orientation="vertical", size_hint_x=0.6, spacing=0)
        self.nickname_label = _small_label("— leer —")
        self.level_label = _small_label("lvl. 0")
        self.hp_text = _small_label("1/1")
        info_box.add_widget(self.nickname_label)
        info_box.add_widget(self.level_label)
        info_box.add_widget(self.hp_text)

        # KP-Leiste: grauer Hintergrund + farbiger Vordergrund (size_hint_x = pct)
        self.hp_bar = MDBoxLayout(orientation="horizontal", size_hint_y=None, height=dp(6),
                                  md_bg_color=HP_BG_COLOR)
        self.hp_fill = MDBoxLayout(size_hint_x=1, md_bg_color=HP_COLOR_HIGH)
        # transparenter Rest-Platzhalter (eigene Ref statt fragilem children[0])
        self.hp_rest = MDBoxLayout(size_hint_x=0)
        self.hp_bar.add_widget(self.hp_fill)
        self.hp_bar.add_widget(self.hp_rest)
        info_box.add_widget(self.hp_bar)

        self.item_label = _small_label("-")
        self.status_label = _small_label("", markup=True)
        info_box.add_widget(self.item_label)
        info_box.add_widget(self.status_label)
        self.add_widget(info_box)

        self.bind(on_release=self._handle_click)

    def _handle_click(self, *_args) -> None:
        # NICHT _on_release nennen: das ist eine StateLayerBehavior-Interna, die
        # MDCard.on_release selbst aufruft — ein Override würde doppelt feuern
        # und den Press/Hover-State kaputtmachen.
        if self.click_callback is not None:
            self.click_callback(self.slot_index)

    def set_dimmed(self, dimmed: bool) -> None:
        """Sprite ausgrauen (gefainted/gesperrt)."""
        self.sprite.color = DIM_COLOR if dimmed else NORMAL_COLOR
        self.sprite.opacity = 0.55 if dimmed else 1.0

    def set_hp(self, cur_hp: int, max_hp: int) -> None:
        """KP-Text + Leiste (Breite = Anteil, Farbe nach Anteil)."""
        self.hp_text.text = f"{cur_hp}/{max_hp}"
        pct = max(0.0, min(1.0, cur_hp / max(max_hp, 1)))
        if pct > 0.5:
            color = HP_COLOR_HIGH
        elif pct > 0.2:
            color = HP_COLOR_MID
        else:
            color = HP_COLOR_LOW
        self.hp_fill.md_bg_color = color
        self.hp_fill.size_hint_x = pct
        # Restbreite = transparenter Platzhalter
        self.hp_rest.size_hint_x = 1 - pct if pct < 1 else 0


class TrainerBoxMD(MDBoxLayout):
    """Team eines Spielers: Header, 6 Slots (2 Spalten), Orden-Reihe.

    Bewusst MDBoxLayout statt MDCard: eine MDCard tönt beim Hover (StateLayer)
    die ganze Spieler-Karte, sobald man über einen Slot fährt — unerwünscht.
    Höhe folgt dem Inhalt (adaptive_height) statt fester dp-Höhe, sonst
    überläuft der Slot-Inhalt die Karte."""

    @perf_span("main.TrainerBoxMD.__init__")
    def __init__(self, player_id, munchlax, obs_websocket, screen, color, **kwargs):
        kwargs.pop("height", None)  # Höhe kommt aus adaptive_height, nicht von aussen
        super().__init__(orientation="horizontal", padding=0, spacing=0,
                         radius=[dp(10)] * 4, md_bg_color=CARD_BG,
                         adaptive_height=True, **kwargs)
        self.player_id = player_id
        self.munchlax = munchlax
        self.obs_websocket = obs_websocket
        self.screen = screen
        self.pokemon_boxes: dict[str, PokemonBoxMD] = {}
        self.badges: dict[int, FitImage] = {}
        self.badge_lut = {
            11: "kanto", 12: "kanto", 13: "kanto",
            21: "johto", 22: "johto", 23: "johto",
            31: "hoenn", 32: "hoenn", 33: "hoenn",
            34: "kanto", 35: "kanto",
            41: "sinnoh", 42: "sinnoh", 43: "sinnoh",
            44: "johto", 45: "johto",
            51: "unova", 52: "unova", 53: "unova2", 54: "unova2",
            61: "kalos", 62: "kalos", 63: "hoenn", 64: "hoenn",
            71: 'alola', 72: 'alola', 73: 'alola', 74: 'alola',
        }
        self.edition_lut = {
            "Rot": 11, "Blau": 12, "Gelb": 13,
            "Gold": 21, "Silber": 22, "Kristall": 23,
            "Rubin": 31, "Saphir": 32, "Smaragd": 33,
            "Feuerrot": 34, "Blattgrün": 35,
            "Diamant": 41, "Perl": 42, "Platin": 43,
            "Herzgold": 44, "Seelensilber": 45,
            "Schwarz": 51, "Weiß": 52, "Schwarz 2": 53, "Weiß 2": 54,
            "X": 61, "Y": 62, "Omega Rubin": 63, "Alpha Saphir": 64,
        }

        # Linker Akzent-Streifen in Spielerfarbe
        try:
            accent = hex8_to_rgba(color, alpha=1.0)
            tint = hex8_to_rgba(color, alpha=0.25)
        except (ValueError, TypeError):
            accent = player_rgba(player_id, alpha=1.0)
            tint = player_rgba(player_id, alpha=0.25)
        self.add_widget(MDBoxLayout(size_hint_x=None, width=dp(6), md_bg_color=accent))

        content = MDBoxLayout(orientation="vertical", padding=dp(6), spacing=dp(4),
                              size_hint_y=None, adaptive_height=True)

        # Header: getönt, Spielername + Edition
        header = MDBoxLayout(orientation="horizontal", size_hint_y=None, height=dp(30),
                             padding=(dp(8), 0), md_bg_color=tint, radius=[dp(6)] * 4)
        self.name_label = MDLabel(text=f"Spieler {self.player_id}", valign="center")
        self.name_label.role = "medium"
        header.add_widget(self.name_label)
        content.add_widget(header)

        grid = MDGridLayout(cols=2, spacing=dp(4), size_hint_y=None, adaptive_height=True)
        for slot in range(6):
            box = PokemonBoxMD(self.obs_websocket)
            box.slot_index = slot
            box.click_callback = self._show_detail
            self.pokemon_boxes[f"slot{slot}"] = box
            grid.add_widget(box)
        content.add_widget(grid)

        content.add_widget(self.create_badge_box())
        self.add_widget(content)

        self._tick_event = Clock.schedule_interval(self.team_aktualisieren, 1)

    def reset_for_session(self) -> None:
        """Anzeige auf Ausgangszustand wie nach __init__ zurücksetzen (Session-
        Wechsel). Ersetzt den teuren Neubau (~130 ms pro Box, KivyMD-Widget-Init);
        Sprite-/Orden-Pfade kommen frisch aus der neuen Session-Config."""
        self.name_label.text = f"Spieler {self.player_id}"
        empty_sprite = self._empty_sprite()
        empty_item = f"{self.obs_websocket.conf['items_path']}/0.png"
        for slot_box in self.pokemon_boxes.values():
            slot_box.nickname_label.text = "— leer —"
            slot_box.level_label.text = "lvl. 0"
            slot_box.item_label.text = "-"
            slot_box.status_label.text = ""
            if slot_box.sprite.source != empty_sprite:
                slot_box.sprite.source = empty_sprite
            if slot_box.item_image.source != empty_item:
                slot_box.item_image.source = empty_item
            slot_box.set_dimmed(False)
            slot_box.set_hp(1, 1)
        edition = self.edition_lut.get(self.munchlax.pl.get("session_game"), 11)
        region = self.badge_lut.get(edition)
        badges_path = self.obs_websocket.conf['badges_path']
        for badge, badge_image in self.badges.items():
            badge_image.source = f"{badges_path}/{region}{badge}empty.png"

    def resume(self) -> None:
        """Tick nach cleanup() wieder starten (geparkte Box wird wiederverwendet)."""
        if self._tick_event is None:
            self._tick_event = Clock.schedule_interval(self.team_aktualisieren, 1)

    def cleanup(self) -> None:
        """Tick stoppen (z.B. beim Entfernen des Widgets)."""
        if self._tick_event is not None:
            self._tick_event.cancel()
            self._tick_event = None

    # --- Detail-Navigation ----------------------------------------------------

    def _show_detail(self, slot: int) -> None:
        team = self.munchlax.sorted_teams.get(self.player_id)
        if not team or slot >= len(team):
            return
        pokemon = team[slot]
        edition = self.munchlax.editions.get(self.player_id, 0)
        rando_data = None
        try:
            if hasattr(self.screen, 'randomizer'):
                rando_data = self.screen.randomizer.get_log_data()
        except Exception:
            logger.warning(f"Rando-Daten für Detail nicht lesbar: {traceback.format_exc()}")
        mgr = self.screen.manager
        if mgr is None or not mgr.has_screen("PokemonDetail"):
            return
        detail = mgr.get_screen("PokemonDetail")
        detail.show(pokemon, edition, rando_data, return_screen="MainMenu",
                    munchlax=self.munchlax, owner=None)
        # Live-Refresh: Detail aktualisiert sich aus dem Live-Team, solange offen.
        rando_getter = None
        if hasattr(self.screen, 'randomizer'):
            rando_getter = self.screen.randomizer.get_log_data
        if hasattr(detail, 'set_refresh_source'):
            detail.set_refresh_source(self.player_id, slot, self.munchlax, rando_getter)
        self._set_detail_edit_target(detail, pokemon)
        mgr.current = "PokemonDetail"

    def _set_detail_edit_target(self, detail, pokemon) -> None:
        """Edit-Ziel für das Bearbeiten-Dialog setzen. Der RAM-Party-Slot ist der
        Index in unsorted_teams (sortierter Index != RAM-Slot) → per personality."""
        if not hasattr(detail, 'set_edit_target'):
            return
        try:
            from backend.pokemon_editor_logic import find_party_slot
            personality = getattr(pokemon, "personality", None)
            ram_slot = find_party_slot(
                self.munchlax.snapshot_unsorted_teams().get(self.player_id) or [], personality)
            target = None
            if ram_slot is not None:
                target = {"kind": "party", "slot": ram_slot, "personality": personality}
            detail.set_edit_target(target, getattr(self.screen, "bizhawk", None),
                                   self.player_id, getattr(self.screen, "pl", None))
        except Exception:
            logger.error(f"Edit-Ziel setzen fehlgeschlagen: {traceback.format_exc()}")
            detail.set_edit_target(None, None, None)

    # --- Orden ----------------------------------------------------------------

    def create_badge_box(self) -> MDBoxLayout:
        badge_box = MDBoxLayout(orientation="horizontal", size_hint_y=None, height=dp(32),
                                spacing=dp(2))
        edition = self.edition_lut.get(self.munchlax.pl.get("session_game"), 11)
        region = self.badge_lut.get(edition)
        for badge in range(1, 9):
            badge_image = FitImage(
                source=f"{self.obs_websocket.conf['badges_path']}/{region}{badge}empty.png",
                fit_mode="contain",
            )
            self.badges[badge] = badge_image
            badge_box.add_widget(badge_image)
        return badge_box

    # --- Rendering ------------------------------------------------------------

    def _empty_sprite(self) -> str:
        conf = self.obs_websocket.conf
        return f"{conf['common_path']}/{conf['red']}/0.png"

    def _render_blocked_slot(self, slot_box: PokemonBoxMD, pokemon=None) -> None:
        """Slot-Anzeige 'gesperrt' (Regel-Sperre); zeigt Identität ausgegraut."""
        empty_sprite = self._empty_sprite()
        sprite_widget = slot_box.sprite
        if pokemon is not None and pokemon.dexnr not in (0, 'egg'):
            slot_box.level_label.text = f"lvl {pokemon.lvl}"
            slot_box.nickname_label.text = pokemon.nickname
            try:
                sprite_path = self.obs_websocket.get_sprite(
                    pokemon, False,
                    self.munchlax.editions[self.player_id],
                    two_pc=False,
                )
            except Exception:
                logger.warning(
                    f"_render_blocked_slot get_sprite failed: {traceback.format_exc()}"
                )
                sprite_path = empty_sprite
            if sprite_widget.source != sprite_path:
                sprite_widget.source = sprite_path
            slot_box.set_dimmed(True)
            slot_box.status_label.text = "[color=FFAA00]GESPERRT[/color]"
        else:
            slot_box.level_label.text = "-"
            slot_box.nickname_label.text = "— gesperrt —"
            if sprite_widget.source != empty_sprite:
                sprite_widget.source = empty_sprite
            slot_box.set_dimmed(False)
            slot_box.status_label.text = ""
        slot_box.item_label.text = "-"
        slot_box.item_image.source = f"{self.obs_websocket.conf['items_path']}/0.png"
        slot_box.set_hp(0, 0)

    def team_aktualisieren(self, _dt) -> None:
        try:
            self._team_aktualisieren()
        except Exception:
            logger.error(f"TrainerBoxMD.team_aktualisieren (Spieler {self.player_id}) "
                         f"fehlgeschlagen: {traceback.format_exc()}")

    def _team_aktualisieren(self) -> None:
        display_name = self.munchlax.player_names.get(self.player_id, f"Spieler {self.player_id}")
        if self.name_label.text != display_name:
            self.name_label.text = display_name

        if (self.screen.name == "SessionMenu"
                or self.player_id not in self.munchlax.sorted_teams
                or self.player_id not in self.munchlax.editions
                or self.player_id not in self.munchlax.badges):
            return

        new_team = self.munchlax.sorted_teams[self.player_id]
        blocked_slots = first_type_dupe_slots(new_team, self.munchlax.nuz)

        for slot, pokemon in enumerate(new_team):
            slot_box = self.pokemon_boxes.get(f"slot{slot}")
            if slot_box is None:
                continue

            if slot in blocked_slots:
                self._render_blocked_slot(slot_box, pokemon)
                continue

            slot_box.level_label.text = f"lvl {pokemon.lvl}"
            slot_box.nickname_label.text = pokemon.nickname
            sprite_widget = slot_box.sprite
            sprite_path = self.obs_websocket.get_sprite(
                pokemon, False, self.munchlax.editions[self.player_id], two_pc=False
            )
            if sprite_widget.source != sprite_path:
                sprite_widget.source = sprite_path
            slot_box.item_label.text = "-" if pokemon.item == 0 else f"{pokemon.item}"
            item_src = f"{self.obs_websocket.conf['items_path']}/{pokemon.item}.png"
            if slot_box.item_image.source != item_src:
                slot_box.item_image.source = item_src
            slot_box.set_hp(pokemon.cur_hp, pokemon.max_hp)

            fainted = (pokemon.cur_hp == 0 and pokemon.dexnr not in (0, 'egg')
                       and pokemon.max_hp > 0)
            slot_box.set_dimmed(fainted)

            status = getattr(pokemon, 'status', {})
            status_text = ''
            for key in STATUS_PRIORITY:
                if status.get(key):
                    status_text = STATUS_LABELS[key][0]
                    break
            slot_box.status_label.text = status_text

        # Orden
        edition = self.munchlax.editions[self.player_id]
        region = self.badge_lut.get(edition)
        if region is None:
            return
        badges = self.munchlax.badges[self.player_id]
        badge_string = f"{self.obs_websocket.conf['badges_path']}"
        for badge in range(8):
            suffix = "" if badges & 2 ** badge else "empty"
            self.badges[badge + 1].source = f"{badge_string}/{region}{badge + 1}{suffix}.png"
