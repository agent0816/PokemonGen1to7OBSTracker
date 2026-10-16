"""KivyMD-Detailansicht für ein einzelnes Pokemon (Top-Level-Drilldown).

Erreichbar aus dem Pokedex (Karten-Klick) und später aus Box/Main. Rendert die
Sektionen der klassischen ``PokemonDetailPanel`` mit MD-Widgets; die Auflöse-
und Adapterlogik liegt gemeinsam in ``backend/pokemon_detail_view.py``.

Rück-Navigation über ``return_screen`` (woher der Drilldown kam), gesetzt beim
``show``-Aufruf. So dient derselbe Screen mehreren Einstiegspunkten.
"""
import os

from kivy.metrics import dp
from kivy.uix.widget import Widget
from kivymd.uix.boxlayout import MDBoxLayout
from kivymd.uix.button import MDButton, MDButtonText
from kivymd.uix.card import MDCard
from kivymd.uix.divider import MDDivider
from kivymd.uix.fitimage import FitImage
from kivymd.uix.gridlayout import MDGridLayout
from kivymd.uix.label import MDLabel
from kivymd.uix.progressindicator import MDLinearProgressIndicator
from kivymd.uix.screen import MDScreen
from kivymd.uix.scrollview import MDScrollView

from backend.logging_setup import get_logger
from backend.pokedex_view import item_name
from backend.player_colors import hex8_to_rgba
from backend.pokemon_detail_view import (
    RADAR_DICT_KEYS, RADAR_DICT_KEYS_GEN1, RADAR_DICT_LABELS, RADAR_DICT_LABELS_GEN1,
    STAT_LABELS, STAT_LABELS_GEN1, STAT_KEYS_RANDO, STAT_KEYS_RANDO_GEN1,
    abilities_de, ev_max, exp_progress, is_gen1_edition, iv_max, moves_de, move_type_id, nature_text,
    resolve_abilities_from_rando, resolve_types_from_rando,
    resolve_vanilla_stats, resolve_vanilla_types, species_de,
    type_color_hex, type_image_path, type_name_id, types_de,
)
from backend.tm_type_resolver import resolve_tm_hm_sprite
from frontend.widgets.stat_radar import RADAR_SCALE, StatRadar

logger = get_logger(__name__, './logs/pokemon_detail.log')

_OUTCOME_DE = {
    "caught": "gefangen", "obtained": "erhalten", "not_caught": "verpasst",
    "dead": "gestorben", "unknown": "offen",
}


class PokemonDetailMD(MDScreen):
    """Full-Screen-Detailansicht. Mehrere Einstiegspunkte via return_screen."""

    def __init__(self, obs_websocket, **kwargs):
        super().__init__(**kwargs)
        self.name = "PokemonDetail"
        self.obs_websocket = obs_websocket
        self.return_screen = "PokedexMenu"
        self._edition = 0
        self._detail_munchlax = None
        self._detail_owner = None

        root = MDBoxLayout(orientation="vertical", padding="10dp", spacing="8dp")

        header = MDBoxLayout(orientation="horizontal", size_hint_y=None, height="48dp", spacing="10dp")
        self.back_button = MDButton(
            MDButtonText(text="Zurück"), style="tonal",
            pos_hint={"center_y": .5}, on_release=lambda _i: self._go_back(),
        )
        header.add_widget(self.back_button)
        self.title_label = MDLabel(text="Pokemon-Detail", role="large", valign="center")
        header.add_widget(self.title_label)
        root.add_widget(header)

        scroll = MDScrollView(do_scroll_x=False)
        self.detail_layout = MDBoxLayout(orientation="vertical", size_hint_y=None,
                                         spacing="6dp", padding="4dp")
        self.detail_layout.bind(minimum_height=self.detail_layout.setter("height"))
        scroll.add_widget(self.detail_layout)
        root.add_widget(scroll)

        self.add_widget(root)

    def _go_back(self) -> None:
        if self.manager is not None and self.manager.has_screen(self.return_screen):
            self.manager.current = self.return_screen

    # -- Befüllung -------------------------------------------------------------

    def show(self, pokemon, edition: int, rando_data=None, return_screen: str = "PokedexMenu",
             munchlax=None, owner: str | None = None) -> None:
        self._edition = edition
        self.return_screen = return_screen
        self._detail_munchlax = munchlax
        self._detail_owner = owner
        self.detail_layout.clear_widgets()

        if pokemon is None or getattr(pokemon, "dexnr", 0) in (0, "", None):
            self.title_label.text = "Kein Pokemon"
            return

        dexnr = pokemon.dexnr
        is_egg = dexnr == "egg"
        species_name = "Ei" if is_egg else species_de.get(dexnr, f"#{dexnr}")
        nickname = getattr(pokemon, "nickname", "")
        self.title_label.text = "Ei" if is_egg else f"#{dexnr} {species_name}"

        rando_pokemon = None
        if rando_data and not is_egg and isinstance(dexnr, int):
            rando_pokemon = rando_data.pokemon.get(dexnr)

        self._add_top_row(pokemon, dexnr, is_egg, species_name, nickname, edition, rando_pokemon)
        if is_egg:
            return

        self._add_nature_ability(pokemon, rando_pokemon)
        self._add_moves(pokemon)
        self._add_stat_radars(dexnr, rando_pokemon, pokemon)
        self._add_exp(pokemon, dexnr)
        self._add_misc(pokemon)
        self._render_soullink_section(pokemon)
        self._add_rando_extras(rando_data, rando_pokemon, dexnr)

    def _add_top_row(self, pokemon, dexnr, is_egg, species_name, nickname, edition, rando_pokemon):
        top = MDCard(style="elevated", orientation="horizontal", size_hint_y=None,
                     height=dp(180), padding=dp(8), spacing=dp(8), radius=[dp(12)] * 4)
        top.add_widget(FitImage(source=self._sprite(pokemon, edition), fit_mode="contain",
                                size_hint_x=0.35))
        info = MDBoxLayout(orientation="vertical", size_hint_x=0.65, spacing=dp(2))
        info.add_widget(self._line(f"Name: {species_name}", bold=True))
        if nickname and nickname != species_name:
            info.add_widget(self._line(f"Spitzname: {nickname}"))
        if not is_egg:
            info.add_widget(self._line(f"DexNr: {dexnr}"))
        lvl = getattr(pokemon, "lvl", None)
        if lvl:
            info.add_widget(self._line(f"Level: {lvl}"))
        cur_hp, max_hp = getattr(pokemon, "cur_hp", None), getattr(pokemon, "max_hp", None)
        if cur_hp is not None and max_hp is not None:
            info.add_widget(self._line(f"KP: {cur_hp}/{max_hp}"))

        vanilla_types = resolve_vanilla_types(dexnr, edition)
        rando_types = resolve_types_from_rando(rando_pokemon.types) if rando_pokemon and rando_pokemon.types else []
        show_types = rando_types if (rando_types and rando_types != vanilla_types) else vanilla_types
        if show_types:
            info.add_widget(self._types_row(show_types, rando=bool(rando_types and rando_types != vanilla_types)))

        item = getattr(pokemon, "item", 0)
        if item and item not in (0, "0"):
            info.add_widget(self._item_row(item, edition))
        if getattr(pokemon, "shiny", False):
            info.add_widget(self._line("Shiny: Ja"))

        top.add_widget(info)
        self.detail_layout.add_widget(top)

    def _add_nature_ability(self, pokemon, rando_pokemon):
        nature = getattr(pokemon, "nature", None)
        ability = getattr(pokemon, "ability", None)
        if nature is None and ability is None:
            return
        self._separator("Wesen & Fähigkeit")
        if nature is not None:
            self.detail_layout.add_widget(self._line(nature_text(nature)))
        if ability is not None:
            self.detail_layout.add_widget(self._line(f"Fähigkeit: {abilities_de.get(ability, f'#{ability}')}"))
        if rando_pokemon and rando_pokemon.abilities:
            rando_ab = resolve_abilities_from_rando(rando_pokemon.abilities)
            self.detail_layout.add_widget(self._line(f"Fähigkeiten (Rando): {' / '.join(rando_ab)}"))

    def _add_moves(self, pokemon):
        moves = getattr(pokemon, "moves", None)
        if not moves:
            return
        self._separator("Attacken")
        grid = MDGridLayout(cols=2, size_hint_y=None, spacing=dp(6), padding=(0, dp(2)))
        grid.bind(minimum_height=grid.setter("height"))
        # Schmal -> 1 Spalte (responsiv).
        grid.bind(width=lambda g, w: setattr(g, "cols", 1 if w < dp(520) else 2))
        for move in moves:
            move_id = move.get("id", 0) if isinstance(move, dict) else 0
            pp = move.get("pp", 0) if isinstance(move, dict) else 0
            if not move_id:
                continue
            grid.add_widget(self._move_card(move_id, pp))
        self.detail_layout.add_widget(grid)

    def _move_card(self, move_id, pp):
        name = moves_de.get(move_id, f"#{move_id}")
        tid = move_type_id(move_id)
        card = MDCard(style="elevated", theme_bg_color="Custom", orientation="horizontal",
                      size_hint_y=None, height=dp(46), radius=[dp(10)] * 4,
                      padding=(dp(10), dp(4)), spacing=dp(6), md_bg_color=self._type_rgba(tid, 0.28))
        name_lbl = MDLabel(text=name, bold=True, valign="center")
        name_lbl.role = "small"
        card.add_widget(name_lbl)
        if tid is not None:
            card.add_widget(self._type_badge(types_de.get(tid, "")))
        ap = MDLabel(text=f"AP {pp}", halign="right", valign="center", size_hint_x=None, width=dp(58))
        ap.role = "small"
        card.add_widget(ap)
        return card

    # Mindestbreite pro Radar-Zelle für die responsive Spaltenzahl (Radar + Rand).
    _RADAR_CELL_MIN = dp(270)

    def _add_stat_radars(self, dexnr, rando_pokemon, pokemon):
        """Basiswerte-, EV- und IV-Radar in einem responsiven Grid: nebeneinander
        wenn breit genug, sonst untereinander (wie die Attacken-Karten)."""
        gen1 = is_gen1_edition(self._edition)
        specs = []

        # Basiswerte (Rando überlagert, wenn abweichend)
        labels = STAT_LABELS_GEN1 if gen1 else STAT_LABELS
        rando_keys = STAT_KEYS_RANDO_GEN1 if gen1 else STAT_KEYS_RANDO
        vanilla = resolve_vanilla_stats(dexnr, self._edition)
        rando = [rando_pokemon.stats.get(k, 0) for k in rando_keys] if rando_pokemon and rando_pokemon.stats else []
        stats = rando if (rando and rando != vanilla) else vanilla
        if stats:
            # Achsen im Uhrzeigersinn ab oben (USUM): KP, Ang, Ver, Init, SpVer, SpAng.
            order = [0, 1, 2, 4, 3] if gen1 else [0, 1, 2, 5, 4, 3]
            title = "Basiswerte" + (" (Rando)" if rando and rando != vanilla else "")
            specs.append((title, [labels[i] for i in order],
                          [stats[i] if i < len(stats) else 0 for i in order],
                          RADAR_SCALE, (0.20, 0.55, 0.95, 1.0)))

        # EVs / IVs (gen-abhängige Skala)
        dlabels = RADAR_DICT_LABELS_GEN1 if gen1 else RADAR_DICT_LABELS
        dkeys = RADAR_DICT_KEYS_GEN1 if gen1 else RADAR_DICT_KEYS
        for name, stat_dict, scale, accent in (
            ("EVs", getattr(pokemon, "evs", None), ev_max(self._edition), (0.30, 0.70, 0.38, 1.0)),
            ("IVs", getattr(pokemon, "ivs", None), iv_max(self._edition), (0.62, 0.42, 0.82, 1.0)),
        ):
            if stat_dict:
                specs.append((f"{name} (max {int(scale)})", dlabels,
                              [int(stat_dict.get(k, 0) or 0) for k in dkeys], float(scale), accent))

        if not specs:
            return
        self._separator("Werte")
        grid = MDGridLayout(size_hint_y=None, spacing=dp(8), cols=len(specs))
        grid.bind(minimum_height=grid.setter("height"))
        n = len(specs)
        # Schmal -> weniger Spalten (bis 1), nie mehr Spalten als Radare.
        grid.bind(width=lambda g, w: setattr(g, "cols", max(1, min(n, int(w / self._RADAR_CELL_MIN)))))
        for title, lbls, vals, scale, accent in specs:
            grid.add_widget(self._radar_cell(title, lbls, vals, scale, accent))
        self.detail_layout.add_widget(grid)

    def _radar_cell(self, title, labels, values, scale, accent) -> MDBoxLayout:
        """Eine Radar-Zelle: Titel oben, Radar (feste Größe, zentriert) darunter."""
        cell = MDBoxLayout(orientation="vertical", size_hint_y=None, height=dp(268), spacing=dp(2))
        t = MDLabel(text=title, bold=True, size_hint_y=None, height=dp(24),
                    halign="center", valign="center")
        t.role = "small"
        cell.add_widget(t)
        row = MDBoxLayout(orientation="horizontal")
        row.add_widget(Widget())
        row.add_widget(StatRadar(labels, values, accent=accent, scale=float(scale),
                                 size_hint=(None, None), size=(dp(240), dp(240))))
        row.add_widget(Widget())
        cell.add_widget(row)
        return cell

    def _item_sprite_path(self, item_id, edition) -> str:
        """Pfad zum Item-Sprite aus dem Sprite-Repo (gen3+); '' wenn nicht da.
        Wie obs._build_item_updates: items_path + resolve_tm_hm_sprite(id).png."""
        conf = getattr(self.obs_websocket, "conf", None)
        if not conf or edition <= 20:
            return ""
        items_path = conf.get("items_path") if hasattr(conf, "get") else None
        if not items_path:
            return ""
        slug = resolve_tm_hm_sprite(edition, str(item_id), None, None)
        path = f"{items_path}/{slug}.png"
        return path if os.path.exists(path) else ""

    def _item_row(self, item_id, edition) -> MDBoxLayout:
        row = MDBoxLayout(orientation="horizontal", size_hint_y=None, height=dp(28), spacing=dp(6))
        sprite = self._item_sprite_path(item_id, edition)
        if sprite:
            row.add_widget(FitImage(source=sprite, size_hint=(None, 1), width=dp(24), fit_mode="contain"))
        name = item_name(item_id, edition) or str(item_id)
        row.add_widget(self._line(f"Item: {name}", height=dp(28)))
        return row

    def _add_exp(self, pokemon, dexnr):
        """Erfahrung: EP-Zahl + 'Bis zum nächsten Lv' mit Fortschrittsbalken
        (Vanilla-Growth-Kurve; bei standardisierten Rando-Runs ggf. ungenau)."""
        exp = getattr(pokemon, "experience_points", None)
        if exp is None:
            return
        self._separator("Erfahrung")
        self.detail_layout.add_widget(self._line(f"EP: {exp}"))
        prog = exp_progress(dexnr, getattr(pokemon, "lvl", None), exp)
        if prog is None:
            return
        self.detail_layout.add_widget(self._line(
            f"Bis zum nächsten Lv: {prog['to_next']}  ({prog['in_level']} / {prog['needed']})"))
        self.detail_layout.add_widget(MDLinearProgressIndicator(
            type="determinate", value=prog["fraction"] * 100,
            size_hint_y=None, height=dp(6)))

    def _add_misc(self, pokemon):
        ot_name = getattr(pokemon, "ot_name", None)
        ot_id = getattr(pokemon, "ot_id", None)
        status = getattr(pokemon, "status", None)
        if all(v is None for v in (ot_name, ot_id, status)):
            return
        self._separator("Sonstiges")
        if ot_name:
            self.detail_layout.add_widget(self._line(f"OT: {ot_name}"))
        if ot_id is not None:
            self.detail_layout.add_widget(self._line(f"OT-ID: {ot_id}"))
        if status:
            self.detail_layout.add_widget(self._line(f"Status: {status}"))

    def _render_soullink_section(self, pokemon):
        m = self._detail_munchlax
        if m is None:
            return
        pv = getattr(pokemon, "personality", None)
        if pv is None:
            return
        links = getattr(m, "soullink_links", None)
        if not isinstance(links, dict) or not links:
            return
        owner = self._detail_owner
        if owner is None:
            try:
                # Soullink-Member sind mit Root-Owner (ohne Slot) geschlüsselt.
                owner = m.pl.get("your_name", "") if m.pl else ""
            except Exception:
                return
        if not owner:
            return
        link_match = None
        for link in links.values():
            member = (link.get("members") or {}).get(owner)
            if member and member.get("personality") == pv:
                link_match = link
                break
        if link_match is None:
            return
        self._separator(f"Soullink-Partner (Link #{link_match.get('link_id', '?')} — "
                        f"Route {link_match.get('route', '?')} — {link_match.get('state', '?')})")
        for other_owner, member in (link_match.get("members") or {}).items():
            if other_owner == owner:
                continue
            partner_dex = member.get("dexnr")
            partner_species = species_de.get(partner_dex, f"#{partner_dex}") if partner_dex else "?"
            outcome = _OUTCOME_DE.get(member.get("outcome", "unknown"), member.get("outcome", "unknown"))
            # adaptive_height: Labels können umbrechen (lange Owner-/Speziesnamen)
            # → Zeile wächst mit, statt zu überlappen.
            row = MDBoxLayout(orientation="horizontal", size_hint_y=None, adaptive_height=True,
                              spacing=dp(6))
            row.add_widget(self._line(f"{other_owner}:", size_hint_x=0.35, height=dp(28)))
            row.add_widget(self._line(f"#{partner_dex} {partner_species}", size_hint_x=0.45, height=dp(28)))
            row.add_widget(self._line(outcome, size_hint_x=0.20, height=dp(28)))
            self.detail_layout.add_widget(row)

    def _add_rando_extras(self, rando_data, rando_pokemon, dexnr):
        if not rando_pokemon:
            return
        if rando_pokemon.item:
            self._separator("Rando-Item (Spezies)")
            self.detail_layout.add_widget(self._line(rando_pokemon.item))
        if rando_data and isinstance(dexnr, int) and dexnr in rando_data.evolutions:
            evos = rando_data.evolutions[dexnr]
            if evos:
                self._separator("Entwicklungen (Rando)")
                self.detail_layout.add_widget(self._line(" → ".join(evos)))

    # -- Helfer ----------------------------------------------------------------

    @staticmethod
    def _type_rgba(type_id, alpha: float = 1.0):
        return hex8_to_rgba(type_color_hex(type_id), alpha)

    def _type_badge(self, type_name_de: str):
        """Typ-Anzeige: bevorzugt das Typ-Bild (backend/ressources/types/de),
        Fallback farbige Pille mit Typnamen."""
        tid = type_name_id(type_name_de)
        img = type_image_path(tid)
        if img:
            return FitImage(source=img, size_hint=(None, None), size=(dp(84), dp(24)),
                            fit_mode="contain")
        badge = MDCard(style="elevated", theme_bg_color="Custom", size_hint=(None, None),
                       height=dp(26), width=dp(78), radius=[dp(13)] * 4, padding=(dp(6), 0),
                       md_bg_color=self._type_rgba(tid, 1.0))
        lbl = MDLabel(text=type_name_de, halign="center", valign="center", bold=True,
                      theme_text_color="Custom", text_color=(1, 1, 1, 1))
        lbl.role = "small"
        badge.add_widget(lbl)
        return badge

    def _types_row(self, type_names: list[str], rando: bool = False) -> MDBoxLayout:
        row = MDBoxLayout(orientation="horizontal", size_hint_y=None, height=dp(28), spacing=dp(6))
        for t in type_names:
            row.add_widget(self._type_badge(t))
        if rando:
            note = MDLabel(text="(Rando)", valign="center", size_hint_x=None, width=dp(64))
            note.role = "small"
            row.add_widget(note)
        # Badges sind fix breit; rechts auffüllen, damit sie links bündig sitzen.
        row.add_widget(Widget())
        return row

    def _sprite(self, pokemon, edition) -> str:
        if self.obs_websocket is None:
            return ""
        try:
            return self.obs_websocket.get_sprite(pokemon, False, edition, two_pc=False) or ""
        except Exception as err:
            logger.debug(f"PokemonDetailMD Sprite-Pfad fehlgeschlagen: {type(err)},{err}")
            return ""

    def _separator(self, text: str) -> None:
        self.detail_layout.add_widget(MDDivider(size_hint_y=None, height=dp(1)))
        # Separator-Titel kann lang sein (Soullink mit Route/State) → umbrechen.
        self.detail_layout.add_widget(self._line(text, bold=True, height=dp(26)))

    @staticmethod
    def _line(text: str, bold: bool = False, height: float = dp(22), **kwargs) -> MDLabel:
        lbl = MDLabel(text=text, bold=bold, size_hint_y=None, height=height,
                      halign="left", valign="top", **kwargs)
        lbl.role = "small"
        # Umbruch statt Abschneiden: text_size an Breite koppeln, Höhe mitwachsen
        # lassen (lange Zeilen: Typen Vanilla→Rando, Soullink, Rando-Item/Evos).
        lbl.bind(width=lambda w, v: setattr(w, "text_size", (v, None)))
        lbl.bind(texture_size=lambda w, ts: setattr(w, "height", max(height, ts[1])))
        return lbl
