"""Host legt Regeln + spielrelevante Einstellungen fest, Clients spiegeln sie.

Live-Session 2026-11-18: satougame hatte lokal gifts_are_additional=False
(alle anderen True) → sein Relaxo-Static wurde verworfen. Nicht-rule_-Keys
wurden bisher nicht vom Host übertragen.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.classes.munchlax import Munchlax  # noqa: E402
from backend.nuzlocke_view import SYNCED_SETTINGS, collect_config  # noqa: E402


def _client(nuz: dict) -> Munchlax:
    return Munchlax("localhost", 12345, {"client_id": "c2"}, {}, {"your_name": "sato"}, nuz=nuz)


def test_collect_config_sendet_settings():
    nuz = {"gifts_are_additional": True, "shiny_clause": False, "rule_x": True,
           "snapshot_backup_path": "C:/geheim", "soullink_preset_id": "p"}
    cfg = collect_config("coop", [("A", "A"), ("B", "A")], nuz)
    assert cfg["settings"] == {"gifts_are_additional": True, "shiny_clause": False}
    assert "snapshot_backup_path" not in cfg["settings"]
    assert set(cfg["settings"]) <= set(SYNCED_SETTINGS)


def test_client_uebernimmt_host_settings():
    nuz = {"gifts_are_additional": False, "dupes_clause": True, "snapshot_backup_path": "lokal"}
    m = _client(nuz)
    n = m._mirror_host_config({"mode": "coop", "rules": {"rule_a": True},
                               "settings": {"gifts_are_additional": True,
                                            "dupes_clause": True,
                                            "snapshot_backup_path": "host"}})
    assert nuz["gifts_are_additional"] is True
    assert nuz["snapshot_backup_path"] == "lokal"  # nicht in der Whitelist
    assert nuz["run_mode"] == "coop" and nuz["rule_a"] is True
    assert n == 2  # rule_a + gifts_are_additional (dupes_clause unverändert)
    assert m.nuz is nuz  # in-place: EncounterTracker hält dieselbe Referenz


def test_alter_host_ohne_settings_laesst_lokal():
    nuz = {"gifts_are_additional": False}
    m = _client(nuz)
    m._mirror_host_config({"mode": "coop", "rules": {}})
    assert nuz["gifts_are_additional"] is False
