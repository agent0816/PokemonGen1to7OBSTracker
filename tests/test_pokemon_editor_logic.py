"""Tests für die Kivy-freie Pokemon-Editor-Logik."""
from types import SimpleNamespace

from backend.pokemon_editor_logic import (
    build_edit, clamp_level, current_move_ids, edit_allowed, find_party_slot,
    format_stats, move_choices, preview_stats, validate_moves,
)

IVS = {k: 31 for k in ("hp", "attack", "defense", "speed", "special_attack", "special_defense")}


def test_clamp_level():
    assert clamp_level("50") == 50
    assert clamp_level("0") == 1
    assert clamp_level("250") == 100
    assert clamp_level("") is None
    assert clamp_level("x") is None


def test_current_move_ids_pads():
    poke = SimpleNamespace(moves=[{"id": 85, "pp": 10}, {"id": 0, "pp": 0}])
    assert current_move_ids(poke) == [85, 0, 0, 0]
    assert current_move_ids(SimpleNamespace(moves=None)) == [0, 0, 0, 0]


def test_validate_moves():
    assert validate_moves([0, 0, 0, 0]) is not None
    assert validate_moves([85, 85, 0, 0]) is not None
    assert validate_moves([85, 0, 33, 0]) is None


def test_build_edit_four_ids():
    edit = build_edit(40, [85, 33])
    assert edit.new_level == 40
    assert edit.moves == [85, 33, 0, 0]


def test_pool_grows_with_level_and_keeps_known():
    low = move_choices(33, 25, 5, [], None)
    high = move_choices(33, 25, 50, [], None)
    assert set(low) <= set(high) and len(high) > len(low)
    # Bekannte Attacke bleibt auch unter dem Lern-Level enthalten (85 = Donner)
    assert 85 not in low
    assert 85 in move_choices(33, 25, 5, [85, 0], None)


def test_preview_stats_and_format():
    stats = preview_stats(33, 25, 50, IVS, {}, 3)
    assert stats is not None and stats["hp"] > 0
    assert "KP" in format_stats(stats)
    low = preview_stats(33, 25, 10, IVS, {}, 3)
    assert low["hp"] < stats["hp"]
    assert preview_stats(33, 25, 50, None, {}, 3) is None


def test_find_party_slot_uses_personality():
    team = [SimpleNamespace(personality=1), None, SimpleNamespace(personality=7)]
    assert find_party_slot(team, 7) == 2
    assert find_party_slot(team, 99) is None


def test_edit_allowed():
    assert edit_allowed(33, False, 25, remote=False, has_bizhawk=True)
    assert not edit_allowed(33, False, 25, remote=True, has_bizhawk=True)
    assert not edit_allowed(33, False, 25, remote=False, has_bizhawk=False)
    assert not edit_allowed(33, False, "egg", remote=False, has_bizhawk=True)
    assert not edit_allowed(11, False, 25, remote=False, has_bizhawk=True)  # Gen 1
