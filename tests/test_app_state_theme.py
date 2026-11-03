"""Theme-Auswahl in app_state (Testrunde 2026-11-03)."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend import app_state


@pytest.fixture
def state_file(tmp_path, monkeypatch):
    path = tmp_path / "app_state.yml"
    monkeypatch.setattr(app_state, "_APP_STATE_PATH", str(path))
    return path


def test_default_dunkel_orange(state_file):
    assert app_state.get_theme() == ("Dark", "Orange")


def test_roundtrip_und_teilweise_setzen(state_file):
    app_state.set_theme(style="Light")
    assert app_state.get_theme() == ("Light", "Orange")
    app_state.set_theme(palette="Teal")
    assert app_state.get_theme() == ("Light", "Teal")
    # andere Keys bleiben erhalten
    app_state.set_ui_preference(app_state.UI_CLASSIC)
    assert app_state.get_theme() == ("Light", "Teal")


def test_ungueltige_werte(state_file):
    with pytest.raises(ValueError):
        app_state.set_theme(style="Sepia")
    with pytest.raises(ValueError):
        app_state.set_theme(palette="Regenbogen")
    state_file.write_text("theme_style: Sepia\nprimary_palette: Regenbogen\n", encoding="utf-8")
    assert app_state.get_theme() == ("Dark", "Orange")
