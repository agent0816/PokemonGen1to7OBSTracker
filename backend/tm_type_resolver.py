import re
from pathlib import Path

import yaml
from backend.logging_setup import get_logger

logger = get_logger(__name__, './logs/tm_type_resolver.log')

_RE_TM_NUM = re.compile(r'^(tm|hm)(\d+)$')


def _load_yaml(filename: str) -> dict:
    path = Path("backend/data") / filename
    try:
        with path.open(encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception as err:
        logger.warning(f"{filename} laden fehlgeschlagen: {err}")
        return {}


_MOVES_LUT: dict[str, str] = _load_yaml("moves.yml")
_TM_MOVES_LUT: dict[int, dict] = _load_yaml("tm_moves.yml")

_MOVES_LOWER: dict[str, str] = {k.lower(): v for k, v in _MOVES_LUT.items()}


def _norm_de(name: str) -> str:
    """Vergleichsschluessel fuer deutsche Namen: Case, ß/ss, Leer-/Bindestriche egal."""
    return (str(name).casefold().replace("ß", "ss")
            .replace(" ", "").replace("-", ""))


def _build_de_lookup() -> dict[str, str]:
    """Deutscher Attackenname (normalisiert) -> englischer Name.

    Randomizer-Logs deutscher ROMs nennen die TM-Attacken mit dem ROM-Namen
    (Gen 1–3: "SOLARSTRAHL", alte Namen wie "PFUND"/"GIFTBLICK"). Quellen:
    moves_de_rom.yml (aus den DE-ROMs extrahiert, tools/extract_move_names_de.py)
    hat Vorrang vor moves_de.yml (heutige Namen, deckt Gen 4+ ab).
    """
    id_to_en = {int(v): k for k, v in _load_yaml("moves_en_reverse.yml").items()}
    lookup: dict[str, str] = {}
    for move_id, name in (_load_yaml("moves_de.yml") or {}).items():
        en = id_to_en.get(int(move_id))
        if en and name:
            lookup.setdefault(_norm_de(name), en)
    for gen_table in (_load_yaml("moves_de_rom.yml") or {}).values():
        for name, move_id in (gen_table or {}).items():
            en = id_to_en.get(int(move_id))
            if en:
                lookup[_norm_de(name)] = en
    return lookup


_DE_TO_EN: dict[str, str] = _build_de_lookup()
_unresolved_logged: set[str] = set()


def move_type(move_name: str) -> str | None:
    mtype = _MOVES_LUT.get(move_name) or _MOVES_LOWER.get(move_name.lower())
    if mtype:
        return mtype
    en = _DE_TO_EN.get(_norm_de(move_name))
    return _MOVES_LUT.get(en) if en else None


def resolve_tm_hm_sprite(edition: int, slug: str,
                          rando_tm_moves: dict[int, str] | None = None,
                          rando_hm_moves: dict[int, str] | None = None) -> str:
    num_match = _RE_TM_NUM.match(slug)
    if not num_match:
        return slug

    prefix = num_match.group(1)
    number = int(num_match.group(2))

    move_name = None
    source = None

    if prefix == "tm":
        if rando_tm_moves:
            move_name = rando_tm_moves.get(number)
            if move_name:
                source = "rando"
        if not move_name:
            ed_data = _TM_MOVES_LUT.get(edition, {})
            move_name = ed_data.get("tm", {}).get(number)
            if move_name:
                source = "vanilla"
    else:
        if rando_hm_moves:
            move_name = rando_hm_moves.get(number)
            if move_name:
                source = "rando"
        if not move_name:
            ed_data = _TM_MOVES_LUT.get(edition, {})
            move_name = ed_data.get("hm", {}).get(number)
            if move_name:
                source = "vanilla"

    if not move_name:
        logger.debug(
            f"resolve_tm_hm_sprite: no mapping edition={edition} slug={slug} "
            f"rando_tm={bool(rando_tm_moves)} rando_hm={bool(rando_hm_moves)}"
        )
        return slug

    mtype = move_type(move_name)
    if not mtype:
        # WARNING einmal pro Name: sonst fehlt das TM-Typ-Sprite unbemerkt
        # (DE-Rando-Logs waren bis 2026-11-03 komplett unaufgeloest).
        if move_name not in _unresolved_logged:
            _unresolved_logged.add(move_name)
            logger.warning(
                f"resolve_tm_hm_sprite: unbekannter Attackenname '{move_name}' "
                f"(edition={edition} slug={slug} source={source}) — kein Typ-Sprite"
            )
        return slug

    result = f"{prefix}-{mtype}"
    logger.debug(
        f"resolve_tm_hm_sprite: {slug} → {result} "
        f"(edition={edition} move={move_name} source={source})"
    )
    return result
