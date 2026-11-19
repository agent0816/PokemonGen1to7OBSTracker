"""Race-Timer: Host pausiert/setzt direkt fort, Mitspieler per Konsens.

User-Entscheid 2026-11-19 (Live-Session 2026-11-18: Host-Pause zeigte
"pause_consent 1/4" und passierte nie). Plus Anzeige-Helper backend.timer_view.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.classes.arceus import Arceus  # noqa: E402
from backend.timer_view import race_status_suffix, split_lines  # noqa: E402

REM = {"client_port": "43886", "helper_port": "43887"}


def _arceus() -> Arceus:
    a = Arceus("localhost", 43886, REM)
    a.munchlaxes = {"host": None, "c2": None, "c3": None, "c4": None}
    a.set_local_client_id("host")
    a._timer_start()
    return a


def test_host_pausiert_und_setzt_direkt_fort():
    a = _arceus()
    assert a._is_host_client("host") and not a._is_host_client("c2")
    assert a._timer_force("pause") is True
    assert a.timer_state["running"] is False and a.timer_state["pause_start_ts"] is not None
    assert a._timer_force("pause") is False  # schon pausiert
    assert a._timer_force("resume") is True
    assert a.timer_state["running"] is True and a.timer_state["pause_start_ts"] is None


def test_host_pause_verwirft_offene_anfragen():
    a = _arceus()
    a._timer_add_consent("pause", "c2")
    assert a.timer_state["pause_consent"] == ["c2"] and a.timer_state["running"]
    a._timer_force("pause")
    assert a.timer_state["pause_consent"] == [] and not a.timer_state["running"]


def test_mitspieler_brauchen_weiter_konsens():
    a = _arceus()
    for cid in ("c2", "c3", "c4"):
        a._timer_add_consent("pause", cid)
    assert a.timer_state["running"] is True  # 3/4 reicht nicht
    a._timer_add_consent("pause", "host")
    assert a.timer_state["running"] is False


def test_host_force_ohne_gestarteten_timer_tut_nichts():
    a = _arceus()
    a._timer_reset()
    assert a._timer_force("pause") is False
    assert a._timer_force("resume") is False


def test_anzeige_suffix():
    assert race_status_suffix({"start_ts": None, "running": False}) == ""  # nach Reset
    assert race_status_suffix({"start_ts": 1.0, "running": True}) == ""
    assert race_status_suffix({"start_ts": 1.0, "running": False}) == " (Pause)"
    assert race_status_suffix({"start_ts": 1.0, "running": True, "pause_consent": ["c2"],
                               "consent_required": 4}) == " (Pause angefragt 1/4)"
    assert race_status_suffix({"start_ts": 1.0, "running": False, "resume_consent": ["c2", "c3"],
                               "consent_required": 4}) == " (Pause, Fortsetzen angefragt 2/4)"
    assert race_status_suffix(None) == ""


def test_split_lines():
    ts = {"splits": [{"label": "manual", "elapsed": 7.0},
                     {"label": "Orden 3", "elapsed": 3725.4}]}
    assert split_lines(ts) == ["1. 00:00:07", "2. 01:02:05  Orden 3"]
    assert split_lines({}) == []


def test_dispatch_host_force_mitspieler_konsens():
    a = _arceus()
    assert a._handle_timer_control("c2", "timer_pause_request") is True  # Konsens-Eintrag
    assert a.timer_state["running"] is True
    assert a._handle_timer_control("host", "timer_pause_request") is True
    assert a.timer_state["running"] is False


def test_dispatch_start_von_mitspieler_bei_pause_ist_resume_anfrage():
    a = _arceus()
    a._timer_force("pause")
    assert a._handle_timer_control("c2", "timer_start") is True
    assert a.timer_state["running"] is False
    assert a.timer_state["resume_consent"] == ["c2"]
    assert a._handle_timer_control("host", "timer_start") is True
    assert a.timer_state["running"] is True


def test_dispatch_start_von_mitspieler_nach_reset_startet():
    a = _arceus()
    a._timer_reset()
    assert a._handle_timer_control("c2", "timer_start") is True
    assert a.timer_state["running"] is True


def test_host_fallback_ueber_rem_client_id():
    a = Arceus("localhost", 43886, {**REM, "client_id": "hostrem"})
    a.munchlaxes = {"hostrem": None, "c2": None}
    a._timer_start()
    assert a._handle_timer_control("hostrem", "timer_pause_request") is True
    assert a.timer_state["running"] is False


def test_start_auf_laufendem_timer_loescht_keine_anfragen():
    a = _arceus()
    a._handle_timer_control("c2", "timer_pause_request")
    assert a._handle_timer_control("c3", "timer_start") is False
    assert a.timer_state["pause_consent"] == ["c2"]
