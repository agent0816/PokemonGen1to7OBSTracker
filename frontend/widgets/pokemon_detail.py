"""PokemonDetailScreen — Detailansicht für ein einzelnes Pokemon.

Wird als Screen in einem nested ScreenManager eingebettet (in MainMenu und
BoxMenu).  Zeigt alle verfügbaren Attribute eines Pokemon-Objekts aufgelöst
in menschenlesbare Namen (deutsch).  Optional überlagert mit Daten aus dem
Randomizer-Log.
"""
import logging
import sys
import traceback

import yaml
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.gridlayout import GridLayout
from kivy.uix.image import Image
from kivy.uix.label import Label
from kivy.uix.screenmanager import Screen
from kivy.uix.scrollview import ScrollView


def _init_logging():
    logger = logging.getLogger(__name__)
    logger.setLevel(logging.INFO)
    formatter = logging.Formatter('[%(asctime)s] %(levelname)s: %(message)s')
    file_handler = logging.FileHandler('./logs/pokemon_detail.log', 'w')
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)
    return logger


logger = _init_logging()


def _load_yaml(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception as err:
        logger.error(f"{path} laden fehlgeschlagen: {err}")
        return {}


species_de = _load_yaml("backend/data/Species_de.yaml")
moves_de = _load_yaml("backend/data/moves_de.yml")
abilities_de = _load_yaml("backend/data/abilities_de.yml")
natures_de = _load_yaml("backend/data/natures_de.yml")
types_de = _load_yaml("backend/data/types_de.yml")
types_en_reverse = _load_yaml("backend/data/types_en_reverse.yml")
abilities_en_reverse = _load_yaml("backend/data/abilities_en_reverse.yml")
species_personal = _load_yaml("backend/data/species_personal.yml")

# Wesen-Stat-Modifikatortabelle aus PKHeX NatureAmp.cs.
# Index = Wesen-ID (0-24), Wert = [ATK, DEF, SPA, SPD, SPE] mit -1/0/+1.
NATURE_AMPS = [
    [ 0, 0, 0, 0, 0],  # Robust / Hardy
    [ 1,-1, 0, 0, 0],  # Solo / Lonely
    [ 1, 0, 0, 0,-1],  # Mutig / Brave
    [ 1, 0,-1, 0, 0],  # Hart / Adamant
    [ 1, 0, 0,-1, 0],  # Frech / Naughty
    [-1, 1, 0, 0, 0],  # Kühn / Bold
    [ 0, 0, 0, 0, 0],  # Sanft / Docile
    [ 0, 1, 0, 0,-1],  # Locker / Relaxed
    [ 0, 1,-1, 0, 0],  # Pfiffig / Impish
    [ 0, 1, 0,-1, 0],  # Lasch / Lax
    [-1, 0, 0, 0, 1],  # Scheu / Timid
    [ 0,-1, 0, 0, 1],  # Hastig / Hasty
    [ 0, 0, 0, 0, 0],  # Ernst / Serious
    [ 0, 0,-1, 0, 1],  # Froh / Jolly
    [ 0, 0, 0,-1, 1],  # Naiv / Naive
    [-1, 0, 1, 0, 0],  # Mäßig / Modest
    [ 0,-1, 1, 0, 0],  # Mild / Mild
    [ 0, 0, 1, 0,-1],  # Ruhig / Quiet
    [ 0, 0, 0, 0, 0],  # Zaghaft / Bashful
    [ 0, 0, 1,-1, 0],  # Hitzig / Rash
    [-1, 0, 0, 1, 0],  # Still / Calm
    [ 0,-1, 0, 1, 0],  # Zart / Gentle
    [ 0, 0, 0, 1,-1],  # Forsch / Sassy
    [ 0, 0,-1, 1, 0],  # Sacht / Careful
    [ 0, 0, 0, 0, 0],  # Kauzig / Quirky
]

STAT_LABELS = ["KP", "Ang", "Ver", "SpAng", "SpVer", "Init"]
STAT_KEYS_PERSONAL = ["hp", "attack", "defense", "special_attack", "special_defense", "speed"]
STAT_KEYS_RANDO = ["hp", "atk", "def", "satk", "sdef", "spd"]


def _nature_text(nature_id: int) -> str:
    """Gibt Wesensname + Stat-Modifikatoren zurück, z.B. 'Hart (+Ang / -SpAng)'."""
    name = natures_de.get(nature_id, f"#{nature_id}")
    if nature_id < 0 or nature_id >= len(NATURE_AMPS):
        return name
    amps = NATURE_AMPS[nature_id]
    amp_labels = ["Ang", "Ver", "SpAng", "SpVer", "Init"]
    plus = [amp_labels[i] for i, v in enumerate(amps) if v == 1]
    minus = [amp_labels[i] for i, v in enumerate(amps) if v == -1]
    if plus and minus:
        return f"{name} (+{plus[0]} / -{minus[0]})"
    return name


def _resolve_types_from_rando(rando_types: list[str]) -> list[str]:
    """Löst englische Typnamen aus dem Rando-Log in deutsche auf."""
    result = []
    for t in rando_types:
        type_id = types_en_reverse.get(t)
        if type_id is not None:
            result.append(types_de.get(type_id, t))
        else:
            result.append(t)
    return result


def _resolve_abilities_from_rando(rando_abilities: list[str]) -> list[str]:
    """Löst englische Fähigkeitsnamen aus dem Rando-Log in deutsche auf."""
    result = []
    for a in rando_abilities:
        ability_id = abilities_en_reverse.get(a)
        if ability_id is not None:
            result.append(abilities_de.get(ability_id, a))
        else:
            result.append(a)
    return result


class PokemonDetailScreen(Screen):
    """Detailansicht für ein einzelnes Pokemon."""

    def __init__(self, obs_websocket, **kwargs):
        super().__init__(**kwargs)
        self.name = "PokemonDetail"
        self.obs_websocket = obs_websocket
        self._back_callback = None

        self.root_layout = BoxLayout(orientation="vertical", padding="10dp", spacing="5dp")

        header = BoxLayout(orientation="horizontal", size_hint_y=None, height="40dp")
        self.back_button = Button(text="Zurück", size_hint_x=0.2, on_press=self._on_back)
        header.add_widget(self.back_button)
        self.title_label = Label(text="Pokemon-Detail", font_size="18sp")
        header.add_widget(self.title_label)
        self.root_layout.add_widget(header)

        scroll = ScrollView()
        self.detail_layout = BoxLayout(
            orientation="vertical", size_hint_y=None, spacing="5dp", padding="5dp",
        )
        self.detail_layout.bind(minimum_height=self.detail_layout.setter("height"))
        scroll.add_widget(self.detail_layout)
        self.root_layout.add_widget(scroll)

        self.add_widget(self.root_layout)

    def show(self, pokemon, edition: int, rando_data=None, back_callback=None):
        """Befüllt die Detailansicht mit Pokemon-Daten.

        Args:
            pokemon: Pokemon-Objekt aus dem Decoder.
            edition: Editions-ID (für Sprite-Pfad).
            rando_data: RandomizerLogData oder None.
            back_callback: Callable für den Zurück-Button.
        """
        self._back_callback = back_callback
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
        vanilla_stats = self._resolve_vanilla_stats(dexnr)
        rando_stats = [rando_pokemon.stats.get(k, 0) for k in STAT_KEYS_RANDO] if rando_pokemon and rando_pokemon.stats else []
        has_rando_stats = rando_stats and rando_stats != vanilla_stats
        if vanilla_stats or rando_stats:
            self._add_separator("Basiswerte")
            display_stats = rando_stats if rando_stats else vanilla_stats
            rows = 2 if has_rando_stats else 1
            stats_grid = GridLayout(cols=7, size_hint_y=None, height=f"{25 * (rows + 1)}dp", spacing="2dp")
            stats_grid.add_widget(self._label("", font_size="11sp", size_hint_y=None, height=25))
            for lbl in STAT_LABELS:
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

    def _resolve_vanilla_types(self, dexnr) -> list[str]:
        """Vanilla-Typen aus species_personal auflösen."""
        if isinstance(dexnr, int):
            personal = species_personal.get(dexnr)
            if personal and "types" in personal:
                type_ids = personal["types"]
                names = [types_de.get(tid, f"#{tid}") for tid in type_ids]
                if len(names) == 2 and names[0] == names[1]:
                    return [names[0]]
                return names
        return []

    def _resolve_vanilla_stats(self, dexnr) -> list[int]:
        """Vanilla-Basiswerte aus species_personal.  Reihenfolge: KP,Ang,Ver,SpAng,SpVer,Init."""
        if isinstance(dexnr, int):
            personal = species_personal.get(dexnr)
            if personal and "stats" in personal:
                return [personal["stats"].get(k, 0) for k in STAT_KEYS_PERSONAL]
        return []

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

    def _on_back(self, instance):
        if self._back_callback:
            self._back_callback()
