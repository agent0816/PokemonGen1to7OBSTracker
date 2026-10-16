"""PokemonDetailPanel + PokemonDetailScreen — Detailansicht für ein einzelnes Pokemon.

`PokemonDetailPanel` ist ein reusable BoxLayout mit der gesamten UI + Logik.
Es kann direkt in andere Screens eingebettet werden (MainMenu bettet es rechts
über dem Team-Overlay ein).  `PokemonDetailScreen` ist ein dünner Screen-Wrapper
um ein Panel — wird als Top-Level-Screen für Full-Screen-Nutzung registriert
(BoxMenu navigiert dorthin).  So teilen sich beide Nutzungen dieselbe Klasse,
die Instanzen leben in unterschiedlichen Containern.

Zeigt alle verfügbaren Attribute eines Pokemon-Objekts aufgelöst in menschen-
lesbare Namen (deutsch).  Optional überlagert mit Daten aus dem Randomizer-Log.
"""
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.gridlayout import GridLayout
from kivy.uix.image import Image
from kivy.uix.label import Label
from kivy.uix.screenmanager import Screen
from kivy.uix.scrollview import ScrollView
from backend.classes.pokedex_db import PokedexDB
from backend.logging_setup import get_logger


logger = get_logger(__name__, './logs/pokemon_detail.log')

# Auflöse-/Stat-Logik + LUTs liegen gemeinsam mit der KivyMD-Variante in
# backend/pokemon_detail_view.py (kein Kivy). Hier nur die Namen importieren,
# die der Panel-Code nutzt (Aliase mit führendem _ für die Funktionsnamen, um
# die bestehenden Aufrufstellen unverändert zu lassen).
from backend.pokemon_detail_view import (  # noqa: E402
    STAT_LABELS, STAT_LABELS_GEN1, STAT_KEYS_RANDO, STAT_KEYS_RANDO_GEN1,
    abilities_de, moves_de, species_de,
    nature_text as _nature_text,
    resolve_types_from_rando as _resolve_types_from_rando,
    resolve_abilities_from_rando as _resolve_abilities_from_rando,
    resolve_vanilla_types as _resolve_vanilla_types_fn,
    resolve_vanilla_stats as _resolve_vanilla_stats_fn,
)


class PokemonDetailPanel(BoxLayout):
    """Detailansicht für ein einzelnes Pokemon — reusable Widget."""

    def __init__(self, obs_websocket, **kwargs):
        kwargs.setdefault("orientation", "vertical")
        kwargs.setdefault("padding", "10dp")
        kwargs.setdefault("spacing", "5dp")
        super().__init__(**kwargs)
        self.obs_websocket = obs_websocket
        self._back_callback = None
        self._edition = 0
        self._refresh_player_id = None
        self._refresh_slot = None
        self._refresh_munchlax = None
        self._refresh_rando_getter = None
        self._last_pokemon = None

        header = BoxLayout(orientation="horizontal", size_hint_y=None, height="40dp")
        self.back_button = Button(text="Zurück", size_hint_x=0.2, on_press=self._on_back)
        header.add_widget(self.back_button)
        self.title_label = Label(text="Pokemon-Detail", font_size="18sp")
        header.add_widget(self.title_label)
        self.add_widget(header)

        scroll = ScrollView()
        self.detail_layout = BoxLayout(
            orientation="vertical", size_hint_y=None, spacing="5dp", padding="5dp",
        )
        self.detail_layout.bind(minimum_height=self.detail_layout.setter("height"))
        scroll.add_widget(self.detail_layout)
        self.add_widget(scroll)

    @property
    def _is_gen1(self) -> bool:
        return 11 <= self._edition <= 13

    def show(self, pokemon, edition: int, rando_data=None, back_callback=None,
             munchlax=None, owner: str | None = None):
        """Befüllt die Detailansicht mit Pokemon-Daten.

        Args:
            pokemon: Pokemon-Objekt aus dem Decoder.
            edition: Editions-ID (für Sprite-Pfad).
            rando_data: RandomizerLogData oder None.
            back_callback: Callable für den Zurück-Button.
            munchlax: Optional — für Soullink-Partner-Anzeige.
            owner: Optional — Owner-String des Pokemon (für Link-Zuordnung).
                    Wenn None: aus munchlax.pl + rem abgeleitet.
        """
        self._back_callback = back_callback
        self._edition = edition
        self._last_pokemon = pokemon
        self._detail_munchlax = munchlax if munchlax is not None else self._refresh_munchlax
        self._detail_owner = owner
        self.detail_layout.clear_widgets()

        if pokemon is None or getattr(pokemon, "dexnr", 0) in (0, "", None):
            self.title_label.text = "Kein Pokemon"
            return

        dexnr = pokemon.dexnr
        is_egg = dexnr == "egg"
        species_name = "Ei" if is_egg else species_de.get(dexnr, f"#{dexnr}")
        nickname = getattr(pokemon, "nickname", "")
        self.title_label.text = f"#{dexnr} {species_name}" if not is_egg else "Ei"

        rando_pokemon = None
        if rando_data and not is_egg and isinstance(dexnr, int):
            rando_pokemon = rando_data.pokemon.get(dexnr)

        # -- Sprite + Basisdaten nebeneinander --
        top_row = BoxLayout(orientation="horizontal", size_hint_y=None, height="180dp")

        try:
            sprite_path = self.obs_websocket.get_sprite(pokemon, False, edition, two_pc=False)
        except Exception:
            sprite_path = f"{self.obs_websocket.conf['common_path']}/{self.obs_websocket.conf['red']}/0.png"
        sprite = Image(source=sprite_path, fit_mode="contain", size_hint_x=0.35)
        top_row.add_widget(sprite)

        info_col = BoxLayout(orientation="vertical", size_hint_x=0.65, spacing="2dp")
        info_col.add_widget(self._label(f"Name: {species_name}", bold=True))
        if nickname and nickname != species_name:
            info_col.add_widget(self._label(f"Spitzname: {nickname}"))
        if not is_egg:
            info_col.add_widget(self._label(f"DexNr: {dexnr}"))
        lvl = getattr(pokemon, "lvl", None)
        if lvl:
            info_col.add_widget(self._label(f"Level: {lvl}"))
        cur_hp = getattr(pokemon, "cur_hp", None)
        max_hp = getattr(pokemon, "max_hp", None)
        if cur_hp is not None and max_hp is not None:
            info_col.add_widget(self._label(f"KP: {cur_hp}/{max_hp}"))

        # Typen — bei Rando Vanilla + Rando nebeneinander
        vanilla_types = self._resolve_vanilla_types(dexnr)
        rando_types = _resolve_types_from_rando(rando_pokemon.types) if rando_pokemon and rando_pokemon.types else []
        if rando_types and rando_types != vanilla_types:
            info_col.add_widget(self._label(f"Typen: {' / '.join(vanilla_types)}  →  {' / '.join(rando_types)} (Rando)"))
        elif vanilla_types:
            info_col.add_widget(self._label(f"Typen: {' / '.join(vanilla_types)}"))

        # Item
        item = getattr(pokemon, "item", 0)
        if item and item != 0:
            info_col.add_widget(self._label(f"Item: {item}"))

        # Shiny
        if getattr(pokemon, "shiny", False):
            info_col.add_widget(self._label("Shiny: Ja"))

        top_row.add_widget(info_col)
        self._add_section(top_row)

        if is_egg:
            return

        # -- Wesen + Fähigkeit --
        nature = getattr(pokemon, "nature", None)
        ability = getattr(pokemon, "ability", None)
        if nature is not None or ability is not None:
            self._add_separator("Wesen & Fähigkeit")
            if nature is not None:
                self._add_section(self._label(_nature_text(nature)))
            if ability is not None:
                ability_name = abilities_de.get(ability, f"#{ability}")
                self._add_section(self._label(f"Fähigkeit: {ability_name}"))
            if rando_pokemon and rando_pokemon.abilities:
                rando_ab = _resolve_abilities_from_rando(rando_pokemon.abilities)
                self._add_section(self._label(f"Fähigkeiten (Rando): {' / '.join(rando_ab)}"))

        # -- Attacken --
        moves = getattr(pokemon, "moves", None)
        if moves:
            self._add_separator("Attacken")
            moves_grid = GridLayout(cols=2, size_hint_y=None, height="100dp", spacing="2dp")
            for move in moves:
                move_id = move.get("id", 0) if isinstance(move, dict) else 0
                pp = move.get("pp", 0) if isinstance(move, dict) else 0
                move_name = moves_de.get(move_id, f"#{move_id}") if move_id else "---"
                moves_grid.add_widget(self._label(f"{move_name}", size_hint_y=None, height=25))
                moves_grid.add_widget(self._label(f"AP: {pp}", size_hint_y=None, height=25))
            self._add_section(moves_grid)

        # -- Basiswerte —— bei Rando beide Zeilen + Delta --
        stat_labels = STAT_LABELS_GEN1 if self._is_gen1 else STAT_LABELS
        rando_keys = STAT_KEYS_RANDO_GEN1 if self._is_gen1 else STAT_KEYS_RANDO
        vanilla_stats = self._resolve_vanilla_stats(dexnr)
        rando_stats = [rando_pokemon.stats.get(k, 0) for k in rando_keys] if rando_pokemon and rando_pokemon.stats else []
        has_rando_stats = rando_stats and rando_stats != vanilla_stats
        if vanilla_stats or rando_stats:
            self._add_separator("Basiswerte")
            display_stats = rando_stats if rando_stats else vanilla_stats
            num_cols = len(stat_labels) + 1
            rows = 2 if has_rando_stats else 1
            stats_grid = GridLayout(cols=num_cols, size_hint_y=None, height=f"{25 * (rows + 1)}dp", spacing="2dp")
            stats_grid.add_widget(self._label("", font_size="11sp", size_hint_y=None, height=25))
            for lbl in stat_labels:
                stats_grid.add_widget(self._label(lbl, bold=True, font_size="11sp", size_hint_y=None, height=25))
            if has_rando_stats:
                stats_grid.add_widget(self._label("Vanilla", font_size="10sp", bold=True, size_hint_y=None, height=25))
                for val in vanilla_stats:
                    stats_grid.add_widget(self._label(str(val), font_size="11sp", size_hint_y=None, height=25))
                stats_grid.add_widget(self._label("Rando", font_size="10sp", bold=True, size_hint_y=None, height=25))
                for i, val in enumerate(rando_stats):
                    delta = val - vanilla_stats[i] if i < len(vanilla_stats) else 0
                    delta_str = f" ({'+' if delta > 0 else ''}{delta})" if delta != 0 else ""
                    stats_grid.add_widget(self._label(f"{val}{delta_str}", font_size="11sp", size_hint_y=None, height=25))
            else:
                stats_grid.add_widget(self._label("", font_size="11sp", size_hint_y=None, height=25))
                for val in display_stats:
                    stats_grid.add_widget(self._label(str(val), font_size="11sp", size_hint_y=None, height=25))
            self._add_section(stats_grid)

        # -- EVs --
        evs = getattr(pokemon, "evs", None)
        if evs:
            self._add_separator("EVs")
            self._add_section(self._stat_row(evs))

        # -- IVs --
        ivs = getattr(pokemon, "ivs", None)
        if ivs:
            self._add_separator("IVs")
            self._add_section(self._stat_row(ivs))

        # -- Sonstiges --
        ot_name = getattr(pokemon, "ot_name", None)
        ot_id = getattr(pokemon, "ot_id", None)
        experience = getattr(pokemon, "experience_points", None)
        status = getattr(pokemon, "status", None)
        if any(v is not None for v in (ot_name, ot_id, experience, status)):
            self._add_separator("Sonstiges")
            if ot_name:
                self._add_section(self._label(f"OT: {ot_name}"))
            if ot_id is not None:
                self._add_section(self._label(f"OT-ID: {ot_id}"))
            if experience is not None:
                self._add_section(self._label(f"EP: {experience}"))
            if status:
                self._add_section(self._label(f"Status: {status}"))

        # -- Soullink-Partner --
        self._render_soullink_section(pokemon)

        # -- Randomizer-Extras --
        if rando_pokemon:
            if rando_pokemon.item:
                self._add_separator("Rando-Item (Spezies)")
                self._add_section(self._label(rando_pokemon.item))
            if rando_data and isinstance(dexnr, int) and dexnr in rando_data.evolutions:
                evos = rando_data.evolutions[dexnr]
                if evos:
                    self._add_separator("Entwicklungen (Rando)")
                    self._add_section(self._label(" → ".join(evos)))

    def _render_soullink_section(self, pokemon):
        """Zeigt Partner-Pokemon aus Soullink-Link falls vorhanden."""
        m = getattr(self, "_detail_munchlax", None)
        if m is None:
            return
        pv = getattr(pokemon, "personality", None)
        if pv is None:
            return
        links = getattr(m, "soullink_links", None)
        if not isinstance(links, dict) or not links:
            return
        # Owner ermitteln (falls nicht explizit gesetzt)
        owner = getattr(self, "_detail_owner", None)
        if owner is None:
            try:
                your_name = m.pl.get("your_name", "") if m.pl else ""
                client_id = str(m.rem.get("client_id", 0)) if m.rem else "0"
                owner = PokedexDB.build_owner(your_name, client_id)
            except Exception:
                return
        # Passenden Link suchen
        link_match = None
        for link in links.values():
            member = (link.get("members") or {}).get(owner)
            if member and member.get("personality") == pv:
                link_match = link
                break
        if link_match is None:
            return

        self._add_separator(f"Soullink-Partner (Link #{link_match.get('link_id', '?')} — Route {link_match.get('route', '?')} — {link_match.get('state', '?')})")
        for other_owner, member in (link_match.get("members") or {}).items():
            if other_owner == owner:
                continue
            partner_dex = member.get("dexnr")
            partner_species = species_de.get(partner_dex, f"#{partner_dex}") if partner_dex else "?"
            outcome = member.get("outcome", "unknown")
            outcome_map = {
                "caught": "gefangen", "obtained": "erhalten",
                "not_caught": "verpasst", "dead": "gestorben",
                "unknown": "offen",
            }
            outcome_de = outcome_map.get(outcome, outcome)
            row = BoxLayout(orientation="horizontal", size_hint_y=None, height="30dp",
                             spacing="6dp")
            row.add_widget(self._label(f"{other_owner}:", size_hint_x=0.35))
            row.add_widget(self._label(f"#{partner_dex} {partner_species}",
                                          size_hint_x=0.45))
            row.add_widget(self._label(outcome_de, size_hint_x=0.20))
            self._add_section(row)

    def _resolve_vanilla_types(self, dexnr) -> list[str]:
        """Vanilla-Typen aus generationsspezifischen Personal-Daten auflösen."""
        return _resolve_vanilla_types_fn(dexnr, self._edition)

    def _resolve_vanilla_stats(self, dexnr) -> list[int]:
        """Vanilla-Basiswerte aus generationsspezifischen Personal-Daten."""
        return _resolve_vanilla_stats_fn(dexnr, self._edition)

    def _stat_row(self, stat_dict: dict) -> GridLayout:
        """Erstellt eine Grid-Zeile für EVs oder IVs."""
        keys = list(stat_dict.keys())
        grid = GridLayout(cols=len(keys), size_hint_y=None, height="50dp", spacing="2dp")
        label_map = {
            "hp": "KP", "attack": "Ang", "defense": "Ver",
            "special_attack": "SpAng", "special_defense": "SpVer",
            "speed": "Init", "special": "Spez",
        }
        for k in keys:
            grid.add_widget(self._label(label_map.get(k, k), bold=True, font_size="11sp", size_hint_y=None, height=25))
        for k in keys:
            grid.add_widget(self._label(str(stat_dict[k]), font_size="11sp", size_hint_y=None, height=25))
        return grid

    def _add_separator(self, text: str):
        lbl = Label(
            text=text, size_hint_y=None, height="25dp",
            font_size="14sp", bold=True, halign="left", valign="middle",
        )
        lbl.bind(size=lbl.setter("text_size"))
        self.detail_layout.add_widget(lbl)

    def _add_section(self, widget):
        self.detail_layout.add_widget(widget)

    @staticmethod
    def _label(text: str, bold=False, font_size="12sp", **kwargs) -> Label:
        lbl = Label(
            text=text, font_size=font_size, bold=bold,
            halign="left", valign="middle",
            size_hint_y=kwargs.pop("size_hint_y", None),
            height=kwargs.pop("height", "20dp"),
        )
        lbl.bind(size=lbl.setter("text_size"))
        return lbl

    def set_refresh_source(self, player_id: int, slot: int, munchlax, rando_getter=None):
        """Speichert Refresh-Quelle für automatische Aktualisierung."""
        self._refresh_player_id = player_id
        self._refresh_slot = slot
        self._refresh_munchlax = munchlax
        self._refresh_rando_getter = rando_getter

    def refresh_if_active(self):
        """Aktualisiert die Ansicht wenn sich das Pokemon geändert hat."""
        if self._refresh_munchlax is None or self._refresh_player_id is None:
            return
        if self._refresh_player_id not in self._refresh_munchlax.sorted_teams:
            return
        team = self._refresh_munchlax.sorted_teams[self._refresh_player_id]
        if self._refresh_slot >= len(team):
            return
        pokemon = team[self._refresh_slot]
        if self._last_pokemon is not None and pokemon == self._last_pokemon:
            return
        edition = self._refresh_munchlax.editions.get(self._refresh_player_id, 0)
        rando_data = self._refresh_rando_getter() if self._refresh_rando_getter else None
        self.show(pokemon, edition, rando_data, self._back_callback)

    def _on_back(self, instance):
        if self._back_callback:
            self._back_callback()


class PokemonDetailScreen(Screen):
    """Dünner Screen-Wrapper um ein PokemonDetailPanel — für Full-Screen-Nutzung.

    BoxMenu navigiert per Top-Level-ScreenManager hierher.  MainMenu bettet
    stattdessen ein eigenes PokemonDetailPanel direkt in seinen Content-Bereich
    ein (kein Wrapper) und behält so die halbseitige Anzeige über dem Team-
    Overlay.
    """

    def __init__(self, obs_websocket, **kwargs):
        super().__init__(**kwargs)
        self.name = "PokemonDetail"
        self.panel = PokemonDetailPanel(obs_websocket)
        self.add_widget(self.panel)

    def show(self, *args, **kwargs):
        return self.panel.show(*args, **kwargs)

    def set_refresh_source(self, *args, **kwargs):
        return self.panel.set_refresh_source(*args, **kwargs)

    def refresh_if_active(self):
        return self.panel.refresh_if_active()

    @property
    def _refresh_player_id(self):
        return self.panel._refresh_player_id
