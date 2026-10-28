"""Tests fuer backend/player_colors.py (gemeinsame Spieler-Palette)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.player_colors import PLAYER_COLORS, hex8_to_rgba, player_rgba  # noqa: E402


def test_palette_vollstaendig() -> None:
    assert set(PLAYER_COLORS) == {1, 2, 3, 4}
    for value in PLAYER_COLORS.values():
        assert len(value) == 8


def test_hex8_to_rgba_basis() -> None:
    assert hex8_to_rgba("000000ff") == (0.0, 0.0, 0.0, 1.0)
    assert hex8_to_rgba("ffffffff") == (1.0, 1.0, 1.0, 1.0)
    r, g, b, a = hex8_to_rgba("1a4d9a7f")
    assert r == pytest.approx(0x1a / 255)
    assert g == pytest.approx(0x4d / 255)
    assert b == pytest.approx(0x9a / 255)
    assert a == pytest.approx(0x7f / 255)


def test_hex8_alpha_override() -> None:
    assert hex8_to_rgba("1a4d9a7f", alpha=1.0)[3] == 1.0
    assert hex8_to_rgba("1a4d9a7f", alpha=0.0)[3] == 0.0


def test_hex6_wird_opak() -> None:
    assert hex8_to_rgba("1a4d9a")[3] == 1.0
    assert hex8_to_rgba("#1a4d9a")[3] == 1.0


def test_hex8_ungueltig() -> None:
    with pytest.raises(ValueError):
        hex8_to_rgba("12345")


def test_player_rgba_fallback() -> None:
    assert player_rgba(1) == hex8_to_rgba(PLAYER_COLORS[1])
    # unbekannter Slot -> Slot-1-Farbe
    assert player_rgba(99) == hex8_to_rgba(PLAYER_COLORS[1])
