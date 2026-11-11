"""GUI-Durchlauf Friedhof-Screen: Leer-Zustand (eigene Datei = eigener Prozess,
Kivy-Window ist Singleton).

Ausfuehrung:
    .venv/py3115/Scripts/python.exe -m pytest tests/gui/test_graveyardmenu_md_leer.py -v
"""
from __future__ import annotations

import pytest

from tests.gui.test_graveyardmenu_md import _DriveApp


@pytest.mark.gui
def test_friedhof_leer_zustand(screenshot_dir, run_md_app):
    app = run_md_app(_DriveApp(screenshot_dir, []))

    assert {"leer_hinweis", "leer_keine_karten"} <= set(app.results), app.results
    fehler = {k: v["detail"] for k, v in app.results.items() if not v["ok"]}
    assert not fehler, fehler
