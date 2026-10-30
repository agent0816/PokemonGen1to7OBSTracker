"""Tests fuer backend/learnsets.py (Attacken-Pool des Pokemon-Editors)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.controller.randomizer_log_parser import RandomizerLogParser  # noqa: E402
from backend.learnsets import (  # noqa: E402
    base_pp, move_pool, pre_evolutions, resolve_move_name, version_group,
)

RANDO_LOGS = Path(__file__).resolve().parents[1] / "tools" / "rando_logs"


def test_version_group_mapping() -> None:
    assert version_group(34) == "frlg"
    assert version_group(13) == "y"
    assert version_group(74) == "usum"
    assert version_group(39) == "e"  # unbekannte Gen-3-Edition -> Fallback


def test_pre_evolutions_vanilla() -> None:
    assert pre_evolutions(3) == [2, 1]
    assert pre_evolutions(1) == []


def test_move_pool_inkl_vorstufen() -> None:
    # Bisaflor (#3) Feuerrot L10: Tackle(33), Heuler(45), Egelsamen(73), Rankenhieb(22)
    pool = move_pool(34, 3, 10)
    assert {33, 45, 73, 22} <= set(pool)
    assert 76 not in pool  # Solarstrahl erst L65
    # Vorstufen-Attacke: Bisasam lernt Rasierblatt (75) L20, Bisaknosp/Bisaflor L22
    assert 75 in move_pool(34, 3, 20)
    assert 75 not in move_pool(34, 3, 19)


def test_move_pool_bekannte_attacken_bleiben() -> None:
    assert 999 in move_pool(34, 1, 5, known_moves=[999, 0])
    assert 0 not in move_pool(34, 1, 5, known_moves=[0])


def test_base_pp_gen_spezifisch() -> None:
    assert base_pp(34, 33) == 35   # Tackle Gen 3
    assert base_pp(71, 33) == 35   # Tackle Gen 7
    assert base_pp(34, 14) == 30   # Schwerttanz Gen 3 (ab Gen 6: 20)
    assert base_pp(61, 14) == 20


def test_resolve_move_name() -> None:
    assert resolve_move_name("TACKLE") == 33
    assert resolve_move_name("Pound") == 1
    assert resolve_move_name("PFUND") == 1
    assert resolve_move_name("ANTIK-KRAFT") == 246
    assert resolve_move_name("GIBTSNICHT") is None


def test_rando_moveset_und_move_updates() -> None:
    data = RandomizerLogParser().parse(str(RANDO_LOGS / "Pokemon Blau (D)_randomized.gbc.log"))
    assert data is not None and data.evolutions_randomized
    pool = move_pool(12, 1, 7, rando_data=data)
    # Bisasam im Rando-Log: L1 KLAMMERGRIFF(20), DONNERSCHLAG(9), L7 BLUBBER(145)
    assert set(pool) >= {20, 9, 145}
    assert 33 not in pool  # Vanilla-Tackle nicht im Rando-Pool

    rubin = RandomizerLogParser().parse(str(RANDO_LOGS / "Pokemon - Rubin (D)_randomized.gba.log"))
    assert base_pp(31, 14, rubin) == 20  # "Made SCHWERTTANZ have 20 PP"
    assert 33 in move_pool(31, 1, 1, rando_data=rubin)  # Movesets unverändert -> Vanilla
