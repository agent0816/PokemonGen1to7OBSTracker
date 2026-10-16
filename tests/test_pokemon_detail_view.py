"""Tests fuer backend/pokemon_detail_view.py (Resolver + DB-Row-Adapter)."""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.pokemon_detail_view import (  # noqa: E402
    edition_int, ev_max, exp_at_level, exp_progress, growth_curve, is_gen1_edition,
    iv_max, move_type_id, nature_text, resolve_types_from_rando, row_to_pokemon,
    type_color_hex, type_image_path, type_name_id,
)
from backend.pokemon_detail_view import _coerce_dexnr, _maybe_json  # noqa: E402


def test_is_gen1_edition() -> None:
    assert is_gen1_edition(11) and is_gen1_edition(13)
    assert not is_gen1_edition(10) and not is_gen1_edition(14)


def test_nature_text_modifikatoren() -> None:
    # id 3 = Hart/Adamant: +Ang / -SpAng
    assert nature_text(3).endswith("(+Ang / -SpAng)")
    # id 0 = Robust: keine Modifikatoren -> kein Klammer-Zusatz
    assert "(" not in nature_text(0)
    # out of range -> reiner Name-Fallback, kein Crash
    assert nature_text(999) == "#999"
    assert nature_text(-1) == "#-1"


def test_resolve_types_from_rando_fallback() -> None:
    # unbekannter Typ bleibt unveraendert (kein Crash, kein Verlust)
    assert resolve_types_from_rando(["NotAType"]) == ["NotAType"]
    assert resolve_types_from_rando([]) == []


def test_coerce_dexnr() -> None:
    assert _coerce_dexnr("25") == 25
    assert _coerce_dexnr("egg") == "egg"
    assert _coerce_dexnr(None) is None
    assert _coerce_dexnr("abc") == "abc"


def test_maybe_json() -> None:
    assert _maybe_json('{"hp": 1}') == {"hp": 1}
    assert _maybe_json('[1, 2]') == [1, 2]
    assert _maybe_json({"a": 1}) == {"a": 1}  # schon dekodiert
    assert _maybe_json(None) is None
    assert _maybe_json("kein json") is None


def test_edition_int() -> None:
    assert edition_int("31") == 31
    assert edition_int(31) == 31
    assert edition_int(None) == 0
    assert edition_int("x") == 0


def test_row_to_pokemon() -> None:
    row = {
        "personality": 0xABC, "dexnr": "25", "nickname": "Blitz", "lvl": 42,
        "shiny": 1, "female": 0, "form": "", "item": "4", "route": 88,
        "nature": 3, "ability": 9,
        "ivs": '{"hp": 31, "attack": 30}', "evs": '{"hp": 4}',
        "moves": '[{"id": 85, "pp": 15}]', "exp": 125000,
    }
    p = row_to_pokemon(row)
    assert p.dexnr == 25
    assert p.nickname == "Blitz"
    assert p.lvl == 42
    assert p.shiny is True
    assert p.nature == 3 and p.ability == 9
    assert p.ivs == {"hp": 31, "attack": 30}
    assert p.evs == {"hp": 4}
    assert p.moves == [{"id": 85, "pp": 15}]
    assert p.experience_points == 125000
    # DB kennt diese Felder nicht -> None
    assert p.cur_hp is None and p.ot_name is None and p.status is None


def test_type_color_hex() -> None:
    assert type_color_hex(12) == "FAC000"   # Elektro
    assert type_color_hex(0) == "9FA19F"    # Normal
    assert type_color_hex(999) == "7F7F7F"  # Fallback
    assert len(type_color_hex(17)) == 6     # Fee, valides Hex6


def test_type_name_id() -> None:
    assert type_name_id("Elektro") == 12
    assert type_name_id("Normal") == 0
    assert type_name_id("Gibtsnicht") is None


def test_move_type_id() -> None:
    assert move_type_id(85) == 12   # Donnerblitz -> Elektro
    assert move_type_id(33) == 0    # Tackle -> Normal
    assert move_type_id(0) is None  # "-----"
    assert move_type_id(None) is None
    assert move_type_id(99999999) is None


def test_iv_ev_max_pro_gen() -> None:
    # Gen 1 (11-13), Gen 2 (21-25): DV 15, Stat-Exp 65535
    assert iv_max(11) == 15 and ev_max(11) == 65535
    assert iv_max(22) == 15 and ev_max(22) == 65535
    # Gen 3+: IV 31, EV 252
    assert iv_max(31) == 31 and ev_max(31) == 252
    assert iv_max(71) == 31 and ev_max(71) == 252


def test_type_image_path() -> None:
    # Elektro (12) -> type_wide_12.png existiert im Repo-Asset
    p = type_image_path(12)
    assert p.endswith("type_wide_12.png")
    assert os.path.exists(p)
    assert type_image_path(None) == ""
    # Stellar (18) hat kein Bild -> leer
    assert type_image_path(18) == ""


def test_exp_at_level_l100() -> None:
    # Kanonische L100-Gesamt-EP pro Kurve
    assert exp_at_level("medium_fast", 100) == 1_000_000
    assert exp_at_level("fast", 100) == 800_000
    assert exp_at_level("slow", 100) == 1_250_000
    assert exp_at_level("medium_slow", 100) == 1_059_860
    assert exp_at_level("erratic", 100) == 600_000
    assert exp_at_level("fluctuating", 100) == 1_640_000
    # Level 1 = 0 für alle
    assert exp_at_level("medium_slow", 1) == 0


def test_growth_curve() -> None:
    assert growth_curve(25) == "medium_fast"   # Pikachu
    assert growth_curve(1) == "medium_slow"     # Bisasam
    assert growth_curve(999999) == "medium_fast"  # unbekannt -> Default


def test_exp_progress() -> None:
    # Pikachu (medium_fast) L36, EP 48000: exp_at(36)=46656, exp_at(37)=50653
    p = exp_progress(25, 36, 48000)
    assert p["in_level"] == 1344 and p["needed"] == 3997 and p["to_next"] == 2653
    assert 0.33 < p["fraction"] < 0.34
    # Level 100 -> kein nächstes Level
    assert exp_progress(25, 100, 0) is None
    # Fehlende Daten
    assert exp_progress(25, None, 100) is None
    # EP über dem nächsten Level (abweichende Kurve) -> fraction geklemmt
    assert exp_progress(25, 5, 10_000_000)["fraction"] == 1.0


def test_row_to_pokemon_egg_und_leer() -> None:
    p = row_to_pokemon({"dexnr": "egg"})
    assert p.dexnr == "egg"
    assert p.nickname == ""
    assert p.ivs is None and p.moves is None
