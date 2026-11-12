"""Phase 3b (2026-11-12): Static-Kaempfe per gBattleTypeFlags als method=static,
nicht fangbare Sonderkaempfe (Alter Mann, Geister) ohne Encounter."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.battle_type_flags import classify_gen3_battle  # noqa: E402

FR, BG, RU, SA, SM = 34, 35, 31, 32, 33
IS_MASTER = 1 << 2


@pytest.mark.parametrize("edition,flags,erwartet", [
    # FRLG
    (FR, 0, "wild"),
    (FR, IS_MASTER, "wild"),
    (FR, IS_MASTER | (1 << 3), "trainer"),
    (FR, IS_MASTER | (1 << 7), "wild"),                       # Safari
    (FR, IS_MASTER | (1 << 17), "static"),                    # Relaxo/Lektrobal
    (BG, IS_MASTER | (1 << 13) | (1 << 18), "static"),        # Zapdos/Mewtu
    (FR, IS_MASTER | (1 << 10), "static"),                    # Roamer
    (FR, IS_MASTER | (1 << 9), "skip"),                       # Alter Mann
    (FR, IS_MASTER | (1 << 15), "skip"),                      # Turm-Geist
    (FR, IS_MASTER | (1 << 15) | (1 << 13), "skip"),          # Tragosso mit Scope
    # RSE
    (RU, 0, "wild"),                                          # auch dowildbattle
    (SM, IS_MASTER | (1 << 13), "static"),                    # Lati@s Suedinsel
    (SM, (1 << 13) | (1 << 30), "static"),                    # Rayquaza
    (SA, (1 << 13) | (1 << 14), "static"),                    # Regi
    (RU, 1 << 10, "static"),                                  # Roamer
    (SM, 1 << 9, "skip"),                                     # Wally
    (SM, (1 << 3) | (1 << 15), "trainer"),                    # Doppel-Trainer
    (SM, 1 << 17, "wild"),                                    # Emerald PALACE-Bit != static
    # Pointer fehlt / andere Gens
    (FR, None, "wild"),
    (43, 1 << 3, "trainer"),
    (43, 1 << 17, "wild"),
])
def test_klassifikation(edition, flags, erwartet):
    assert classify_gen3_battle(edition, flags) == erwartet


# ---------------------------------------------------------------------------
# EncounterTracker mit echter PokedexDB
# ---------------------------------------------------------------------------

def _opp(pv, dex=143, shiny=False):
    return {"personality": pv, "dexnr": dex, "lvl": 30, "shiny": shiny,
            "female": False, "form": ""}


@pytest.fixture
def tracker(tmp_path):
    from backend.classes.pokedex_db import PokedexDB
    from backend.encounter_tracker import EncounterTracker
    db = PokedexDB(tmp_path)
    db.connect()
    try:
        yield EncounterTracker(db, {})
    finally:
        db.close()


OWNER = "Tester_1"
ROUTE12 = 99


def test_static_ohne_ball_wird_verbucht(tracker):
    """Kein Ball-Gate fuer static (wie Gifts)."""
    res = tracker.process_wild_encounter(OWNER, FR, _opp(1), ROUTE12, "static")
    assert res is not None
    assert res.method == "static"
    assert res.is_first


def test_wild_ohne_ball_bleibt_gesperrt(tracker):
    assert tracker.process_wild_encounter(OWNER, FR, _opp(1), ROUTE12) is None


def test_static_auf_belegter_route(tracker, monkeypatch):
    monkeypatch.setattr(tracker.pokedex_db, "has_encounter_on_route",
                        lambda *a, **k: True)
    res = tracker.process_wild_encounter(OWNER, FR, _opp(1), ROUTE12, "static")
    assert res is not None and res.method == "static"


def test_static_soft_reset_nicht_doppelt(tracker):
    assert tracker.process_wild_encounter(OWNER, FR, _opp(1), ROUTE12, "static")
    # gleicher Ort, gleiche Species, neue PV (Soft-Reset) → verworfen
    assert tracker.process_wild_encounter(OWNER, FR, _opp(2), ROUTE12, "static") is None


def test_static_andere_species_gleicher_ort(tracker):
    """Kraftwerk: Lektrobal und Zapdos teilen sich die MAPSEC."""
    assert tracker.process_wild_encounter(OWNER, FR, _opp(1, dex=101), 170, "static")
    res = tracker.process_wild_encounter(OWNER, FR, _opp(2, dex=145), 170, "static")
    assert res is not None and not res.already_logged


def test_static_belegt_wild_route_nicht(tracker):
    tracker.process_wild_encounter(OWNER, FR, _opp(1), ROUTE12, "static")
    assert not tracker.pokedex_db.has_encounter_on_route(OWNER, FR, ROUTE12)


def test_unbekannte_methode_faellt_auf_wild(tracker):
    assert tracker.process_wild_encounter(OWNER, FR, _opp(1), ROUTE12, "gift") is None


def test_statics_nicht_zusaetzlich_route_belegt(tmp_path, monkeypatch):
    """gifts_are_additional=False: Static zaehlt wie Routen-Encounter."""
    from backend.classes.pokedex_db import PokedexDB
    from backend.encounter_tracker import EncounterTracker
    db = PokedexDB(tmp_path)
    db.connect()
    try:
        tr = EncounterTracker(db, {"gifts_are_additional": False})
        monkeypatch.setattr(db, "has_encounter_on_route", lambda *a, **k: True)
        assert tr.process_wild_encounter(OWNER, FR, _opp(1), ROUTE12, "static") is None
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Bizhawk._read_encounter_data: Flags -> method / skip
# ---------------------------------------------------------------------------

def _bizhawk_mit_flags(monkeypatch, flags):
    import asyncio
    import logging
    from types import SimpleNamespace
    import backend.bh_pointers as bh_pointers
    import backend.pokedecoder as pokedecoder
    from backend.classes.bizhawk import Bizhawk

    flags_ptr, opp_ptr = 0x02022B4C, 0x0202402C
    monkeypatch.setattr(bh_pointers, "get_pointers", lambda e, l: {
        "battleopponentidpointer": 0x1, "battleopponentpointer": opp_ptr,
        "battletypeflagspointer": flags_ptr})
    monkeypatch.setattr(pokedecoder, "decode_opponent_gen3",
                        lambda b, e: dict(_opp(777), met_location=99))

    class _Queue(list):
        def append(self, item):
            _cmd, addr, size, fut, _ = item
            if addr == flags_ptr:
                fut.set_result(int(flags).to_bytes(4, "little"))
            else:
                fut.set_result(bytes(size))

    calls = []

    class _Tracker:
        def process_wild_encounter(self, owner, edition, opp, route, method="wild"):
            calls.append(method)
            return None

    bh = Bizhawk.__new__(Bizhawk)
    bh.logger = logging.getLogger("test_static")
    bh.language_per_client = {"p1": "de"}
    bh.box_request_queues = {"p1": _Queue()}
    bh._encounter_inflight = {"p1": True}
    bh._last_battle_pv = {}
    bh._last_map_header = {}
    bh._last_party_count = {}
    bh._wild_encounters_processed = {}
    bh.munchlax = SimpleNamespace(pl={"your_name": "Tester"},
                                  set_last_opponent=lambda *a: None)
    bh.encounter_tracker = _Tracker()
    bh._ensure_encounter_tracker = lambda: True

    async def _pc(cid, ed):
        return 6
    bh._read_party_count = _pc
    asyncio.run(bh._read_encounter_data("p1", 1, FR))
    return bh, calls


def test_bizhawk_static_flag_uebergibt_method(monkeypatch):
    bh, calls = _bizhawk_mit_flags(monkeypatch, IS_MASTER | (1 << 17))
    assert calls == ["static"]
    assert bh._last_battle_pv["p1"] == 777
    assert bh._encounter_inflight["p1"] is False


def test_bizhawk_wild_flag(monkeypatch):
    _bh, calls = _bizhawk_mit_flags(monkeypatch, IS_MASTER)
    assert calls == ["wild"]


def test_bizhawk_alter_mann_kein_encounter(monkeypatch):
    bh, calls = _bizhawk_mit_flags(monkeypatch, IS_MASTER | (1 << 9))
    assert calls == []
    assert "p1" not in bh._last_battle_pv
    assert bh._encounter_inflight["p1"] is False


def test_bizhawk_trainer_kein_encounter(monkeypatch):
    _bh, calls = _bizhawk_mit_flags(monkeypatch, IS_MASTER | (1 << 3))
    assert calls == []
