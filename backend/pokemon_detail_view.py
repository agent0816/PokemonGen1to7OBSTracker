"""Reine Auflöse-/Adapterlogik für die Pokemon-Detailansicht (ohne Kivy).

Gemeinsame Basis für die klassische `PokemonDetailPanel` (frontend) und den
KivyMD-Detail-Screen. Enthält die Namens-/Stat-Auflösung aus den YAML-LUTs und
einen Adapter, der eine PokedexDB-Zeile (dict) in ein Pokemon-ähnliches Objekt
übersetzt, wie es die Detailansicht erwartet (Attribut-Zugriff via getattr).
"""
from __future__ import annotations

import json
import os
from types import SimpleNamespace

import yaml

from backend.logging_setup import get_logger

logger = get_logger(__name__, './logs/pokemon_detail.log')


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
species_personal_gen1 = _load_yaml("backend/data/species_personal_gen1.yml")
species_personal_gen2to5 = _load_yaml("backend/data/species_personal_gen2to5.yml")
species_personal_gen6 = _load_yaml("backend/data/species_personal_gen6.yml")
species_personal_gen7 = _load_yaml("backend/data/species_personal_gen7.yml")


def get_species_personal(edition: int) -> dict:
    """Wählt generationsspezifische Personal-Daten anhand der Edition."""
    if 11 <= edition <= 13:
        return species_personal_gen1
    if 61 <= edition <= 64:
        return species_personal_gen6
    if 71 <= edition <= 74:
        return species_personal_gen7
    return species_personal_gen2to5


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

STAT_LABELS_GEN1 = ["KP", "Ang", "Ver", "Spez", "Init"]
STAT_KEYS_PERSONAL_GEN1 = ["hp", "attack", "defense", "special_attack", "speed"]
STAT_KEYS_RANDO_GEN1 = ["hp", "atk", "def", "satk", "spd"]

# Achsen-Reihenfolge für EV/IV-Radar (im Uhrzeigersinn ab oben wie USUM):
# KP, Angriff, Vert, Initiative, SpVert, SpAng. EV/IV-Dicts (aus dem Decoder)
# nutzen diese Schlüssel; Gen 1 hat "special" statt der SpAng/SpVer-Teilung.
RADAR_DICT_LABELS = ["KP", "Ang", "Ver", "Init", "SpVer", "SpAng"]
RADAR_DICT_KEYS = ["hp", "attack", "defense", "speed", "special_defense", "special_attack"]
RADAR_DICT_LABELS_GEN1 = ["KP", "Ang", "Ver", "Init", "Spez"]
RADAR_DICT_KEYS_GEN1 = ["hp", "attack", "defense", "speed", "special"]


def _gen_12(edition: int) -> bool:
    """True für Gen 1 (11–15) und Gen 2 (21–25)."""
    return 11 <= edition <= 15 or 21 <= edition <= 25


def iv_max(edition: int) -> int:
    """Max. IV/DV pro Statuswert. Gen 1/2: DV 0–15, Gen 3+: 0–31.

    Quelle: etablierte Pokemon-Mechanik (DVs sind 4-Bit in Gen 1/2, ab Gen 3
    sind IVs 5-Bit, 0–31).
    """
    return 15 if _gen_12(edition) else 31


def ev_max(edition: int) -> int:
    """Max. EV pro Statuswert. Gen 1/2: Stat-Erfahrung 0–65535, Gen 3+: 0–252.

    Quelle: etablierte Pokemon-Mechanik (Stat-Exp bis 65535 in Gen 1/2; ab Gen 3
    EVs pro Wert auf 252 gedeckelt, 510 gesamt).
    """
    return 65535 if _gen_12(edition) else 252


_TYPE_IMAGE_DIR = "backend/ressources/types/de"


def type_image_path(type_id) -> str:
    """Pfad zum Typ-Bild (type_wide_<NN>.png, Index = Typ-ID); '' wenn keins da.

    Die Bilder liegen als Repo-Asset vor und werden im Nuitka-Onefile-Build über
    --include-data-dir=backend/ressources mitgepackt. Pfad ist relativ, greift
    im Dev wie im Build (main.py chdir't bei __compiled__ ins Exe-Verzeichnis).
    """
    if type_id is None:
        return ""
    path = f"{_TYPE_IMAGE_DIR}/type_wide_{int(type_id):02d}.png"
    return path if os.path.exists(path) else ""


def is_gen1_edition(edition: int) -> bool:
    return 11 <= edition <= 13


def nature_text(nature_id: int) -> str:
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


def resolve_types_from_rando(rando_types: list[str]) -> list[str]:
    """Löst englische Typnamen aus dem Rando-Log in deutsche auf."""
    result = []
    for t in rando_types:
        type_id = types_en_reverse.get(t)
        if type_id is not None:
            result.append(types_de.get(type_id, t))
        else:
            result.append(t)
    return result


def resolve_abilities_from_rando(rando_abilities: list[str]) -> list[str]:
    """Löst Fähigkeitsnamen (EN oder DE) aus dem Rando-Log in deutsche auf."""
    from backend.pokedecoder import resolve_ability_name
    result = []
    for a in rando_abilities:
        ability_id = resolve_ability_name(a)
        if ability_id is not None:
            result.append(abilities_de.get(ability_id, a))
        else:
            result.append(a)
    return result


def resolve_vanilla_types(dexnr, edition: int) -> list[str]:
    """Vanilla-Typen aus generationsspezifischen Personal-Daten auflösen."""
    if isinstance(dexnr, int):
        personal = get_species_personal(edition).get(dexnr)
        if personal and "types" in personal:
            type_ids = personal["types"]
            names = [types_de.get(tid, f"#{tid}") for tid in type_ids]
            if len(names) == 2 and names[0] == names[1]:
                return [names[0]]
            return names
    return []


def resolve_vanilla_stats(dexnr, edition: int) -> list[int]:
    """Vanilla-Basiswerte aus generationsspezifischen Personal-Daten."""
    if isinstance(dexnr, int):
        personal = get_species_personal(edition).get(dexnr)
        if personal and "stats" in personal:
            keys = STAT_KEYS_PERSONAL_GEN1 if is_gen1_edition(edition) else STAT_KEYS_PERSONAL
            return [personal["stats"].get(k, 0) for k in keys]
    return []


def _maybe_json(value):
    """DB-Spalten ivs/evs/moves liegen als JSON-Text vor. Dekodiert defensiv;
    bereits dekodierte Werte (dict/list) werden durchgereicht."""
    if isinstance(value, (dict, list)) or value is None:
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (ValueError, TypeError):
            return None
    return value


def _coerce_dexnr(raw):
    """DB-dexnr ist TEXT ('25' oder 'egg'); Detailansicht erwartet int oder 'egg'."""
    if raw == "egg":
        return "egg"
    try:
        return int(raw)
    except (TypeError, ValueError):
        return raw


def row_to_pokemon(row: dict) -> SimpleNamespace:
    """Übersetzt eine PokedexDB-Zeile in ein Pokemon-ähnliches Objekt.

    So kann der Detail-Code dieselben getattr-Zugriffe nutzen wie bei einem
    echten Decoder-Pokemon. JSON-Felder (ivs/evs/moves) werden dekodiert,
    ``exp`` auf ``experience_points`` gemappt. In der DB nicht vorhandene
    Felder (cur_hp/max_hp/ot_name/ot_id/status) bleiben None.
    """
    return SimpleNamespace(
        personality=row.get("personality"),
        dexnr=_coerce_dexnr(row.get("dexnr")),
        nickname=row.get("nickname") or "",
        lvl=row.get("lvl"),
        shiny=bool(row.get("shiny")),
        female=bool(row.get("female")),
        form=row.get("form") or "",
        item=row.get("item"),
        route=row.get("route"),
        nature=row.get("nature"),
        ability=row.get("ability"),
        ivs=_maybe_json(row.get("ivs")),
        evs=_maybe_json(row.get("evs")),
        moves=_maybe_json(row.get("moves")),
        experience_points=row.get("exp"),
        cur_hp=None, max_hp=None, ot_name=None, ot_id=None, status=None,
    )


def edition_int(raw) -> int:
    """Editions-ID als int (DB speichert sie als TEXT); 0 bei Fehler."""
    try:
        return int(raw)
    except (TypeError, ValueError):
        return 0


# --- Typ-Farben + Move-Typ (für USUM-artige Anzeige) --------------------------

# Standard-Pokemon-Typfarben als RGB-Hex ohne '#', Index = Typ-ID (types_de).
TYPE_COLORS: dict[int, str] = {
    0: "9FA19F",   # Normal
    1: "FF8000",   # Kampf
    2: "81B9EF",   # Flug
    3: "9141CB",   # Gift
    4: "915121",   # Boden
    5: "AFA981",   # Gestein
    6: "91A119",   # Käfer
    7: "704170",   # Geist
    8: "60A1B8",   # Stahl
    9: "E62829",   # Feuer
    10: "2980EF",  # Wasser
    11: "3FA129",  # Pflanze
    12: "FAC000",  # Elektro
    13: "EF4179",  # Psycho
    14: "3FD8FF",  # Eis
    15: "5060E1",  # Drache
    16: "624D4E",  # Unlicht
    17: "EF70EF",  # Fee
    18: "40B5A5",  # Stellar
}
_TYPE_FALLBACK = "7F7F7F"


def type_color_hex(type_id) -> str:
    """RGB-Hex (ohne '#') zur Typ-ID; grau bei unbekannt."""
    return TYPE_COLORS.get(type_id, _TYPE_FALLBACK)


def type_name_id(name_de: str) -> int | None:
    """Typ-ID zu einem deutschen Typnamen (Reverse von types_de)."""
    for tid, name in types_de.items():
        if name == name_de:
            return tid
    return None


_moves_en = _load_yaml("backend/data/moves_en_reverse.yml")  # EN-Name -> Move-ID
_moves_types = _load_yaml("backend/data/moves.yml")          # EN-Name -> Typ-Slug
_move_id_to_type: dict[int, int] | None = None


def _build_move_type_map() -> dict[int, int]:
    """Baut Move-ID -> Typ-ID (einmal). Kette: ID→EN-Name→Slug→Typ-ID."""
    result: dict[int, int] = {}
    for en_name, move_id in _moves_en.items():
        slug = _moves_types.get(en_name)
        if not slug:
            continue
        tid = types_en_reverse.get(str(slug).capitalize())
        if tid is not None:
            try:
                result[int(move_id)] = tid
            except (TypeError, ValueError):
                continue
    return result


def move_type_id(move_id) -> int | None:
    """Typ-ID einer Attacke (für Typ-Badge/Farbe); None wenn unbekannt."""
    global _move_id_to_type
    if _move_id_to_type is None:
        _move_id_to_type = _build_move_type_map()
    try:
        return _move_id_to_type.get(int(move_id))
    except (TypeError, ValueError):
        return None


# --- EXP-Kurven / Fortschritt zum nächsten Level ------------------------------
#
# Growth-Kurve pro Spezies aus backend/data/growth_rates.yml (Vanilla, Quelle
# veekun/pokedex). CAVEAT: Der Universal Randomizer kann per "Standardize EXP
# Curves" die ROM-Kurve ändern (ordinary→medium_fast, legendär→slow); das steht
# NICHT im Rando-Log (verifiziert gegen UPR-ZX-Source), daher nur Vanilla
# verfügbar. Bei standardisierten Runs kann der Fortschritt abweichen.

_growth_rates: dict[int, str] | None = None


def _load_growth_rates() -> dict[int, str]:
    global _growth_rates
    if _growth_rates is None:
        data = _load_yaml("backend/data/growth_rates.yml")
        _growth_rates = {int(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}
    return _growth_rates


def growth_curve(dexnr) -> str:
    """Vanilla-Growth-Kurve einer Spezies; 'medium_fast' als Default."""
    try:
        return _load_growth_rates().get(int(dexnr), "medium_fast")
    except (TypeError, ValueError):
        return "medium_fast"


def exp_at_level(curve: str, n: int) -> int:
    """Gesamt-EP, um Level ``n`` zu erreichen (n≥1; Level 1 = 0).

    Formeln nach Bulbapedia/veekun für die sechs Kurven.
    """
    if n <= 1:
        return 0
    if curve == "medium_fast":
        val = n ** 3
    elif curve == "fast":
        val = 4 * n ** 3 / 5
    elif curve == "slow":
        val = 5 * n ** 3 / 4
    elif curve == "medium_slow":
        val = 6 / 5 * n ** 3 - 15 * n ** 2 + 100 * n - 140
    elif curve == "erratic":
        if n <= 50:
            val = n ** 3 * (100 - n) / 50
        elif n <= 68:
            val = n ** 3 * (150 - n) / 100
        elif n <= 98:
            val = n ** 3 * ((1911 - 10 * n) // 3) / 500
        else:
            val = n ** 3 * (160 - n) / 100
    elif curve == "fluctuating":
        if n <= 15:
            val = n ** 3 * ((n + 1) // 3 + 24) / 50
        elif n <= 36:
            val = n ** 3 * (n + 14) / 50
        else:
            val = n ** 3 * (n // 2 + 32) / 50
    else:
        val = n ** 3
    return max(0, int(val))


def exp_progress(dexnr, level, current_exp) -> dict | None:
    """Fortschritt im aktuellen Level: dict mit in_level/needed/fraction/curve/
    total_next, oder None wenn Daten fehlen/Level 100.

    ``fraction`` ist auf 0..1 geklemmt (robust gegen abweichende ROM-Kurve bei
    standardisierten Randomizer-Runs).
    """
    try:
        lvl = int(level)
        cur = int(current_exp)
    except (TypeError, ValueError):
        return None
    if lvl < 1 or lvl >= 100:
        return None
    curve = growth_curve(dexnr)
    base = exp_at_level(curve, lvl)
    nxt = exp_at_level(curve, lvl + 1)
    needed = nxt - base
    if needed <= 0:
        return None
    in_level = cur - base
    fraction = min(1.0, max(0.0, in_level / needed))
    return {
        "curve": curve,
        "in_level": max(0, in_level),
        "needed": needed,
        "to_next": max(0, nxt - cur),
        "fraction": fraction,
    }
