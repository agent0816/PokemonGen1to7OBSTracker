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


def move_type(move_name: str) -> str | None:
    return _MOVES_LUT.get(move_name) or _MOVES_LOWER.get(move_name.lower())


def resolve_tm_hm_sprite(edition: int, slug: str,
                          rando_tm_moves: dict[int, str] | None = None,
                          rando_hm_moves: dict[int, str] | None = None) -> str:
    num_match = _RE_TM_NUM.match(slug)
    if not num_match:
        return slug

    prefix = num_match.group(1)
    number = int(num_match.group(2))

    move_name = None

    if prefix == "tm":
        if rando_tm_moves:
            move_name = rando_tm_moves.get(number)
        if not move_name:
            ed_data = _TM_MOVES_LUT.get(edition, {})
            move_name = ed_data.get("tm", {}).get(number)
    else:
        if rando_hm_moves:
            move_name = rando_hm_moves.get(number)
        if not move_name:
            ed_data = _TM_MOVES_LUT.get(edition, {})
            move_name = ed_data.get("hm", {}).get(number)

    if not move_name:
        return slug

    mtype = move_type(move_name)
    if not mtype:
        return slug

    return f"{prefix}-{mtype}"
