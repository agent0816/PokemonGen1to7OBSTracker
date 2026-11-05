"""Ball-Gate-Regression mit echter Client-DB aus dem Live-Test 2026-11-04.

Nachstellung: satougame_2 (Blattgruen, edition 35) hat Baelle seit dem
aktiven Run (run_022, start_ts 2026-09-30T18:04:28Z). Nach Disconnect
(Munchlax.close_pokedex_db) hielt der EncounterTracker die geschlossene DB →
Gate zu ("Run nicht gestartet"). Mit Rebind (_ensure_encounter_tracker)
muss das Gate offen bleiben.

Die DB liegt unter tools/ (gitignored) — fehlt sie, wird uebersprungen.
"""
import logging
import shutil
import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.classes.bizhawk import Bizhawk  # noqa: E402
from backend.classes.munchlax import Munchlax  # noqa: E402

LIVE_DB = ROOT / "tools/logs_to_analyze/live-session-test-frbg/satougame_pokemon.db"
RUN_START = "2026-09-30T18:04:28Z"
OWNER, EDITION = "satougame_2", 35

pytestmark = pytest.mark.skipif(not LIVE_DB.exists(), reason="Live-Test-DB nicht vorhanden")


@pytest.fixture
def munchlax(tmp_path):
    shutil.copy(LIVE_DB, tmp_path / "pokemon.db")  # Original nie anfassen
    m = Munchlax.__new__(Munchlax)
    m._pokedex_db_lock = threading.RLock()
    m._pokedex_db_rebuild_at = 0.0
    m.logger = logging.getLogger("test")
    m.configsave = f"{tmp_path}/"
    m.pokedex_db = None
    m.nuz = {"run_mode": "coop", "rule_run_start_on_ball": True}
    m.active_run_start_ts = lambda: RUN_START
    yield m
    m.close_pokedex_db()


@pytest.fixture
def bizhawk(munchlax):
    bh = Bizhawk.__new__(Bizhawk)
    bh.munchlax = munchlax
    bh.encounter_tracker = None
    bh.logger = logging.getLogger("test")
    return bh


def test_gate_offen_vor_disconnect(bizhawk):
    assert bizhawk._ensure_encounter_tracker()
    assert bizhawk.encounter_tracker._has_balls(OWNER, EDITION)


def test_alter_tracker_nach_disconnect_reproduziert_bug(bizhawk, munchlax):
    assert bizhawk._ensure_encounter_tracker()
    tracker = bizhawk.encounter_tracker
    munchlax.close_pokedex_db()  # Netz-Disconnect
    # Altverhalten: Tracker ohne Rebind sieht geschlossene DB → Gate zu
    assert not tracker._has_balls(OWNER, EDITION)


def test_rebind_nach_disconnect_gate_offen(bizhawk, munchlax):
    assert bizhawk._ensure_encounter_tracker()
    tracker = bizhawk.encounter_tracker
    munchlax.close_pokedex_db()
    assert bizhawk._ensure_encounter_tracker()  # naechster Encounter
    assert bizhawk.encounter_tracker is tracker
    assert tracker.pokedex_db.connection is not None
    assert tracker._has_balls(OWNER, EDITION)


def test_wild_encounter_nach_reconnect_zaehlt(bizhawk, munchlax):
    """Kompletter Regel-Check wie im Live-Test (20:28, Route 111 / dex 302)."""
    assert bizhawk._ensure_encounter_tracker()
    munchlax.close_pokedex_db()
    assert bizhawk._ensure_encounter_tracker()
    has_balls, _is_first, _shiny, _retry = bizhawk.encounter_tracker._check_nuzlocke_flags(
        OWNER, EDITION, 111, 302, False, "wild")
    assert has_balls
