"""Ball-Gate nach Reconnect (Live-Test 2026-11-04): EncounterTracker muss an
die aktuelle PokedexDB gebunden werden, nicht an die beim Disconnect
geschlossene."""
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.classes.bizhawk import Bizhawk  # noqa: E402
from backend.classes.citrahandler import CitraHandler  # noqa: E402


class _FakeMunchlax:
    def __init__(self):
        self.nuz = {}
        self.pokedex_db = None
        self.created = 0

    def _ensure_pokedex_db(self):
        if self.pokedex_db is None:
            self.created += 1
            self.pokedex_db = SimpleNamespace(tag=self.created)

    def close_pokedex_db(self):
        self.pokedex_db = None


def _check(handler, attr):
    mx = handler.munchlax
    assert handler._ensure_encounter_tracker()
    tracker = getattr(handler, attr)
    first_db = tracker.pokedex_db
    assert first_db is mx.pokedex_db
    mx.close_pokedex_db()  # Disconnect
    assert handler._ensure_encounter_tracker()
    assert getattr(handler, attr) is tracker  # Tracker bleibt (Caches)
    assert tracker.pokedex_db is mx.pokedex_db
    assert tracker.pokedex_db is not first_db


def test_bizhawk_rebind_nach_disconnect():
    bh = Bizhawk.__new__(Bizhawk)
    bh.munchlax = _FakeMunchlax()
    bh.encounter_tracker = None
    bh.logger = __import__("logging").getLogger("test")
    _check(bh, "encounter_tracker")


def test_citra_rebind_nach_disconnect():
    ch = CitraHandler.__new__(CitraHandler)
    ch.munchlax = _FakeMunchlax()
    ch._encounter_tracker = None
    ch.logger = __import__("logging").getLogger("test")
    _check(ch, "_encounter_tracker")


def test_ohne_db_false():
    bh = Bizhawk.__new__(Bizhawk)
    bh.munchlax = SimpleNamespace(pokedex_db=None, _ensure_pokedex_db=lambda: None, nuz={})
    bh.encounter_tracker = None
    assert not bh._ensure_encounter_tracker()


def test_munchlax_ensure_baut_geschlossene_db_neu(tmp_path):
    import logging
    import threading
    from backend.classes.munchlax import Munchlax
    m = Munchlax.__new__(Munchlax)
    m._pokedex_db_lock = threading.RLock()
    m._pokedex_db_rebuild_at = 0.0
    m.logger = logging.getLogger("test")
    m.configsave = f"{tmp_path}/"
    m.pokedex_db = None
    m._ensure_pokedex_db()
    first = m.pokedex_db
    assert first is not None and first.connection is not None
    m._ensure_pokedex_db()
    assert m.pokedex_db is first  # offen + gleicher Pfad → wiederverwendet
    first.close()  # Verbindung weg, Referenz bleibt
    m._ensure_pokedex_db()
    assert m.pokedex_db is not first and m.pokedex_db.connection is not None
    assert m.close_pokedex_db() and m.pokedex_db is None
    m.close_pokedex_db()  # idempotent


def test_munchlax_rebuild_backoff(tmp_path, monkeypatch):
    """Dauerhaft scheiterndes connect: Neuaufbau hoechstens alle 5 s."""
    import logging
    import threading
    import backend.classes.munchlax as mx_mod
    from backend.classes.munchlax import Munchlax
    m = Munchlax.__new__(Munchlax)
    m._pokedex_db_lock = threading.RLock()
    m._pokedex_db_rebuild_at = 0.0
    m.logger = logging.getLogger("test")
    m.configsave = f"{tmp_path}/"
    m.pokedex_db = None
    m._ensure_pokedex_db()
    m.pokedex_db.close()
    clock = [100.0]
    monkeypatch.setattr(mx_mod.time, "monotonic", lambda: clock[0])
    built = []
    real_cls = mx_mod.PokedexDB

    class _FailingDB(real_cls):
        def connect(self, *a, **kw):
            built.append(self)  # connection bleibt None
    monkeypatch.setattr(mx_mod, "PokedexDB", _FailingDB)
    m._ensure_pokedex_db()
    assert len(built) == 1
    clock[0] += 1.0
    m._ensure_pokedex_db()
    assert len(built) == 1  # innerhalb Backoff kein neuer Versuch
    clock[0] += 5.0
    m._ensure_pokedex_db()
    assert len(built) == 2
