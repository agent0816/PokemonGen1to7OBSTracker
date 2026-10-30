"""Tests fuer backend/stat_calc.py und backend/exp_curves.py."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.exp_curves import exp_at_level, infer_curve, level_from_exp  # noqa: E402
from backend.stat_calc import (  # noqa: E402
    calc_stats_gen12, calc_stats_gen3plus, hp_dv_gen12, normalize_base_stats, scale_cur_hp,
)


def test_gen3plus_bulbapedia_beispiel_knakrack() -> None:
    # Bulbapedia "Stat": Knakrack L78, Hart, IVs 24/12/30/16/23/5, EVs 74/190/91/48/84/23
    base = {"hp": 108, "attack": 130, "defense": 95, "special_attack": 80,
            "special_defense": 85, "speed": 102}
    ivs = {"hp": 24, "attack": 12, "defense": 30, "special_attack": 16,
           "special_defense": 23, "speed": 5}
    evs = {"hp": 74, "attack": 190, "defense": 91, "special_attack": 48,
           "special_defense": 84, "speed": 23}
    stats = calc_stats_gen3plus(base, ivs, evs, nature=3, level=78)
    assert stats == {"hp": 289, "attack": 278, "defense": 193, "special_attack": 135,
                     "special_defense": 171, "speed": 171}


def test_gen3plus_ninjatask_hp_eins() -> None:
    base = {"hp": 1, "attack": 90, "defense": 45, "special_attack": 30,
            "special_defense": 30, "speed": 40}
    assert calc_stats_gen3plus(base, {}, {}, 0, 50, dexnr=292)["hp"] == 1


def test_rando_schluessel_normalisiert() -> None:
    assert normalize_base_stats({"atk": 10, "satk": 20, "hp": 5}) == {
        "attack": 10, "special_attack": 20, "hp": 5}


def test_gen12_formel_handgerechnet() -> None:
    # Pikachu Gen 1 (35/55/30/90/50), L50, DVs Atk15 Def14 Init13 Spez12, keine Stat-Exp
    base = {"hp": 35, "attack": 55, "defense": 30, "speed": 90,
            "special_attack": 50, "special_defense": 50}
    dvs = {"attack": 15, "defense": 14, "speed": 13, "special": 12}
    assert hp_dv_gen12(dvs) == 0b1010
    stats = calc_stats_gen12(base, dvs, {}, 50)
    assert stats["hp"] == (35 + 10) * 2 * 50 // 100 + 60
    assert stats["attack"] == (55 + 15) * 2 * 50 // 100 + 5
    assert stats["special_attack"] == stats["special_defense"]


def test_gen12_stat_exp_deckel() -> None:
    base = {"hp": 100}
    # ceil(sqrt(65535)) = 256, Spiel deckelt bei 255 -> 63
    full = calc_stats_gen12(base, {}, {"hp": 65535}, 100)["hp"]
    none = calc_stats_gen12(base, {}, {}, 100)["hp"]
    assert full - none == 63
    # 1 Stat-Exp -> ceil(sqrt(1)) = 1 -> 0
    assert calc_stats_gen12(base, {}, {"hp": 17}, 100)["hp"] - none == 1  # ceil(sqrt(17))=5 -> 1


def test_scale_cur_hp() -> None:
    assert scale_cur_hp(30, 40, 50) == 40      # 10 Schaden bleibt
    assert scale_cur_hp(5, 40, 20) == 1        # lebt weiter
    assert scale_cur_hp(0, 40, 50) == 0        # besiegt bleibt besiegt
    assert scale_cur_hp(40, 40, 30) == 30


def test_level_from_exp() -> None:
    assert level_from_exp("medium_fast", 125000) == 50
    assert level_from_exp("medium_fast", 124999) == 49
    assert level_from_exp("medium_fast", 0) == 1
    assert level_from_exp("slow", 10_000_000) == 100
    for curve in ("fast", "slow", "medium_slow", "erratic", "fluctuating"):
        for lvl in (2, 15, 37, 68, 99, 100):
            assert level_from_exp(curve, exp_at_level(curve, lvl)) == lvl


def test_infer_curve() -> None:
    # Bisasam (#1) ist medium_slow
    assert infer_curve(1, 20, exp_at_level("medium_slow", 20)) == "medium_slow"
    # Passt nur zu slow (z.B. UPR "Standardize EXP Curves") -> slow
    exp = exp_at_level("slow", 50)
    assert infer_curve(1, 50, exp) == "slow"
    # Unplausibles Paar -> Vanilla-Fallback
    assert infer_curve(1, 5, 10_000_000) == "medium_slow"
