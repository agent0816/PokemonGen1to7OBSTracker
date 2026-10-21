"""Tests fuer backend/app_state.py (app-weiter UI-Modus + Onboarding-Marker).

Deckt: Defaults bei fehlender Datei, Roundtrip, Teil-Keys-Ergaenzung, defekte
Datei faellt auf Defaults zurueck, UI-Validierung, Onboarding-Marker.

Ausführung:
    .venv/py3115/Scripts/python.exe -m pytest tests/test_app_state.py -v
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend import app_state  # noqa: E402


@pytest.fixture
def state_path(tmp_path, monkeypatch):
    """Lenkt den Modul-Pfad auf eine temporaere Datei (keine echte Config)."""
    p = tmp_path / "app_state.yml"
    monkeypatch.setattr(app_state, "_APP_STATE_PATH", str(p))
    return p


def test_load_defaults_when_missing(state_path):
    assert not state_path.exists()
    state = app_state.load_app_state()
    assert state == {"ui": "md", "onboarding_done": False}


def test_get_ui_preference_default_md(state_path):
    assert app_state.get_ui_preference() == "md"


def test_roundtrip(state_path):
    app_state.set_ui_preference("classic")
    app_state.set_onboarding_done(True)
    assert app_state.get_ui_preference() == "classic"
    assert app_state.is_onboarding_done() is True
    # Datei existiert jetzt und enthaelt beide Keys.
    data = yaml.safe_load(state_path.read_text(encoding="utf-8"))
    assert data["ui"] == "classic"
    assert data["onboarding_done"] is True


def test_partial_keys_filled_with_defaults(state_path):
    # Nur ui gesetzt -> onboarding_done kommt aus Defaults.
    state_path.write_text(yaml.dump({"ui": "classic"}), encoding="utf-8")
    state = app_state.load_app_state()
    assert state["ui"] == "classic"
    assert state["onboarding_done"] is False


def test_broken_file_falls_back_to_defaults(state_path):
    state_path.write_text("::: not valid yaml :::", encoding="utf-8")
    state = app_state.load_app_state()
    assert state == {"ui": "md", "onboarding_done": False}


def test_non_dict_file_falls_back(state_path):
    state_path.write_text(yaml.dump(["a", "b"]), encoding="utf-8")
    assert app_state.get_ui_preference() == "md"


def test_invalid_ui_value_in_file_normalized_to_md(state_path):
    state_path.write_text(yaml.dump({"ui": "quantum"}), encoding="utf-8")
    assert app_state.get_ui_preference() == "md"


def test_set_invalid_ui_raises(state_path):
    with pytest.raises(ValueError):
        app_state.set_ui_preference("quantum")


def test_set_ui_preserves_onboarding(state_path):
    app_state.set_onboarding_done(True)
    app_state.set_ui_preference("classic")
    # UI-Wechsel darf den Onboarding-Marker nicht zuruecksetzen.
    assert app_state.is_onboarding_done() is True


def test_onboarding_default_false(state_path):
    assert app_state.is_onboarding_done() is False
