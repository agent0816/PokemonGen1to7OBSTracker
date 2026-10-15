"""Gemeinsame Fixtures fuer GUI-Durchlauf-Tests (KivyMD).

GUI-Tests fahren eine echte MDApp mit GL-Fenster und schiessen Screenshots.
Sie sind mit dem Marker ``gui`` versehen (siehe pytest.ini) und werden in
headless-Umgebungen mit ``-m "not gui"`` abgewaehlt.

Kivy/Window ist pro Prozess ein Singleton — pro Testprozess laeuft daher nur
eine App sauber durch. ``KIVY_LOG_MODE`` wird hier gesetzt, bevor irgendein
Kivy-Modul importiert wird (conftest wird vor den Testmodulen gesammelt).

Ausfuehrung (lokal, mit Fenster):
    .venv/py3115/Scripts/python.exe -m pytest tests/gui -v
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import pytest
import yaml

# Vor jedem Kivy-Import: ruhiges Python-Logging statt Kivy-eigenem Logger.
os.environ.setdefault("KIVY_LOG_MODE", "PYTHON")

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend import session_store  # noqa: E402
from backend.session_store import SessionConfigs  # noqa: E402


def _base_configs() -> SessionConfigs:
    """Minimal-Configs wie im urspruenglichen drive_sessionsmenumd-Spike."""
    return SessionConfigs(
        sp={"common_path": "x"}, bh={"port": 1}, obs={"host": "h"},
        rem={"client_port": 2},
        pl={"your_name": "Default", "session_game": "Rot"},
        rnd={"seed": "d"}, ov={"port": 3},
        nuz={"mode": "solo", "rule_dupes_clause": True},
    )


@pytest.fixture
def config_root(tmp_path: Path) -> str:
    """Temp-Config wie backend/config/: default/ + Alt-Session + session_list.yml.

    Liefert den Default-Pfad (mit abschliessendem Slash). session_list.yml liegt
    — wie im Produktivlayout — im Elternordner von default/.
    """
    default = f"{(tmp_path / 'default').as_posix()}/"
    base = _base_configs()
    session_store.write_session_files(default, base, create=True)
    (tmp_path / "session_list.yml").write_text(yaml.dump(["Alt"]))
    alt = session_store.create_session(default, "Alt", base, player_name="AltName")
    Path(alt, "nuzlocke.yml").write_text(yaml.dump({"mode": "coop"}))
    return default


@pytest.fixture
def screenshot_dir(tmp_path: Path) -> str:
    """Zielordner fuer Window.screenshot-Aufnahmen des Durchlaufs."""
    d = tmp_path / "shots"
    d.mkdir()
    return str(d)


@pytest.fixture
def run_md_app():
    """Faehrt eine MDApp via asyncio, bis sie sich selbst mit stop() beendet.

    Gibt die (gelaufene) App-Instanz zurueck, damit der Test an gesammelten
    Ergebnissen (z.B. app.results) pruefen kann.
    """
    def _run(app):
        asyncio.run(app.async_run(async_lib="asyncio"))
        return app
    return _run
