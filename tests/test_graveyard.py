"""App-weiter Friedhof + Todescounter (Wunsch 2026-11-11)."""
import asyncio
import threading
from types import SimpleNamespace

import pytest

from backend.classes.Pokemon import Pokemon
from backend.graveyard import GraveyardDB


@pytest.fixture
def gy(tmp_path):
    db = GraveyardDB(tmp_path / "graveyard.db")
    yield db
    db.close()


def _e(pv, owner="Steffen", session="Projekt A", run="run_001", **kw):
    d = {"session": session, "run_id": run, "personality": pv, "owner_name": owner}
    d.update(kw)
    return d


def test_idempotent_und_server_ueberschreibt_ursache(gy):
    assert gy.record_death(_e(1, cause="faint", ts=100, level=12, nickname="Bob")) is True
    # gleicher Tod lokal nochmal → keine Änderung
    assert gy.record_death(_e(1, cause="faint", ts=200)) is False
    # Server sagt: Partner-Tod → überschreibt Ursache, Level/Nickname bleiben
    assert gy.record_death(_e(1, cause="soullink_partner", link_id=7, level=99),
                           authoritative=True) is True
    (row,) = gy.list_graves("Projekt A")
    assert row["cause"] == "soullink_partner" and row["link_id"] == 7
    assert row["level"] == 12 and row["nickname"] == "Bob" and row["ts"] == 100
    # lokale Meldung überschreibt Server-Ursache nicht
    gy.record_death(_e(1, cause="faint"))
    assert gy.list_graves("Projekt A")[0]["cause"] == "soullink_partner"


def test_counter_partner_zaehlt_nicht_und_pro_session(gy):
    gy.record_death(_e(1, owner="Steffen", cause="faint", ts=10))
    gy.record_death(_e(2, owner="web", cause="soullink_partner", ts=10))
    gy.record_death(_e(3, owner="web", cause="faint", ts=11))
    gy.record_death(_e(4, owner="web", cause="faint", ts=12, session="Projekt B"))
    assert gy.death_counts("Projekt A") == {"Steffen": 1, "web": 1}
    assert gy.death_counts("Projekt B") == {"web": 1}


@pytest.fixture
def clock(monkeypatch):
    """Lokale Uhr (recorded_at/reset_ts) steuerbar machen."""
    import backend.graveyard as g
    now = [1000.0]
    monkeypatch.setattr(g.time, "time", lambda: now[0])
    return now


def test_reset_nur_counter_friedhof_bleibt(gy, clock):
    gy.record_death(_e(1, cause="faint", ts=10))
    clock[0] = 2000
    gy.reset_counter("Projekt A")
    assert gy.death_counts("Projekt A") == {}
    assert len(gy.list_graves("Projekt A")) == 1
    clock[0] = 3000
    gy.record_death(_e(2, cause="faint", ts=30))
    assert gy.death_counts("Projekt A") == {"Steffen": 1}


def test_counter_nutzt_lokale_uhr_nicht_server_ts(gy, clock):
    # Server-Uhr hängt 1 h hinterher: Tod nach dem Reset zählt trotzdem
    gy.reset_counter("Projekt A")
    clock[0] = 1500
    gy.record_death(_e(1, cause="faint", ts=1000 - 3600), authoritative=True)
    assert gy.death_counts("Projekt A") == {"Steffen": 1}


def test_run_wechsel_und_snapshot_keine_doppelzeile(gy):
    # Lokal vor Run-Start (run ''), Server danach mit run_001, Replay mit run_002
    gy.record_death(_e(1, run="", cause="faint", ts=10))
    assert gy.record_death(_e(1, run="run_001", cause="faint", ts=20), authoritative=True) is False
    gy.record_death(_e(1, run="run_002", cause="faint", ts=30), authoritative=True)
    (row,) = gy.list_graves("Projekt A")
    assert row["run_id"] == "run_001" and row["ts"] == 10
    assert gy.death_counts("Projekt A") == {"Steffen": 1}


def test_manuell_lebendig_nur_server_belebt_wieder(gy, clock):
    gy.record_death(_e(1, cause="faint", ts=10))
    assert gy.mark_removed("Projekt A", 1, "Steffen") is True
    assert gy.death_counts("Projekt A") == {}
    assert gy.list_graves("Projekt A") == []
    assert len(gy.list_graves("Projekt A", include_removed=True)) == 1
    # Lokale Neuerkennung (z.B. nach Session-Reset) hebt die Korrektur nicht auf
    assert gy.record_death(_e(1, cause="faint")) is False
    assert gy.death_counts("Projekt A") == {}
    # Server meldet erneuten Tod nach Reset → zählt ab jetzt
    clock[0] = 2000
    gy.reset_counter("Projekt A")
    clock[0] = 3000
    assert gy.record_death(_e(1, cause="faint"), authoritative=True) is True
    assert gy.death_counts("Projekt A") == {"Steffen": 1}


def test_unvollstaendig_wird_verworfen(gy):
    assert gy.record_death({"session": "", "personality": 1, "owner_name": "x"}) is False
    assert gy.record_death(_e(None)) is False


def test_persistiert_ueber_neuoeffnen(tmp_path):
    db = GraveyardDB(tmp_path / "g.db")
    db.record_death(_e(5, cause="faint", ts=1, partners=["web"]))
    db.close()
    db2 = GraveyardDB(tmp_path / "g.db")
    try:
        (row,) = db2.list_graves()
        assert row["partners"] == ["web"]
        assert db2.sessions() == ["Projekt A"]
    finally:
        db2.close()


# --- Munchlax-Anbindung (ohne Netz) ---------------------------------------

class _FakePDB:
    connection = object()

    def __init__(self, enc):
        self._enc = enc

    def get_encounter(self, pv, owner):
        return self._enc if self._enc and self._enc["owner"] == owner else None

    def find_encounter_by_root(self, pv, root):
        return self._enc


def _munchlax(tmp_path, gy):
    from backend.classes.munchlax import Munchlax
    m = Munchlax.__new__(Munchlax)
    m.state_lock = threading.Lock()
    m.sorted_teams = {}
    m.bizhawk_teams = {}
    m.soullink_team_state = {}
    m.configsave = str(tmp_path / "config" / "Projekt A")
    m.logger = SimpleNamespace(info=lambda *a, **k: None, debug=lambda *a, **k: None,
                               warning=lambda *a, **k: None, error=lambda *a, **k: None)
    m._graveyard = gy
    m._graveyard_lock = threading.Lock()
    m.get_run_manager = lambda: None
    m._notified = []
    m._notify_state_listeners = m._notified.append
    return m


def test_munchlax_baut_eintrag_aus_team_und_encounter(tmp_path, gy):
    m = _munchlax(tmp_path, gy)
    mon = Pokemon(280, shiny=True, female=True, nickname="Trali", lvl=17, personality=42)
    m.sorted_teams = {2: [mon]}
    m.soullink_team_state = {"A": {"owners": ["web"]}}
    m.pokedex_db = _FakePDB({"owner": "web_2", "route": 101, "method": "wild",
                             "lvl": 5, "edition": 32, "dexnr": 280})
    # Server-Broadcast: Owner ohne Slot
    asyncio.run(m._graveyard_record_server_death(
        {"personality": 42, "owner": "web", "cause": "faint", "timestamp": 50,
         "link_id": 3, "partners": [{"owner": "Steffen"}]}))
    (row,) = gy.list_graves("Projekt A")
    assert (row["owner_name"], row["owner_slot"], row["level"], row["nickname"]) == ("web", 2, 17, "Trali")
    assert (row["route"], row["edition"], row["shiny"], row["female"]) == (101, 32, 1, 1)
    assert row["team"] == "A" and row["partners"] == ["Steffen"] and row["ts"] == 50
    assert m._notified == ["graveyard"]
    assert m.snapshot_death_counts() == {"web": 1}


def test_munchlax_ignoriert_manual_alive_im_snapshot(tmp_path, gy):
    m = _munchlax(tmp_path, gy)
    m.pokedex_db = _FakePDB(None)
    asyncio.run(m._graveyard_record_server_death(
        {"personality": 1, "owner": "web", "cause": "manual_alive", "dead": 0}))
    assert gy.list_graves() == []


def test_reset_counter_rueckgabe_und_eintraege_bleiben(gy):
    """Knopf "Todescounter auf 0": True, Counter 0, Einträge bleiben."""
    import time
    gy.record_death({"session": "S", "personality": 1, "owner_name": "A", "cause": "faint"})
    assert gy.death_counts("S") == {"A": 1}
    time.sleep(0.05)
    assert gy.reset_counter("S") is True
    assert gy.death_counts("S") == {}
    assert len(gy.list_graves("S")) == 1
