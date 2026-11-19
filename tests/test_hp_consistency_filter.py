"""HP-Consistency-Filter (Munchlax._is_pokemon_dead): Battle-RAM-Ausreißer
verwerfen, aber nach stabilem Editor-/Pensions-Level-Sprung neu baselinen.

Live-Session 2026-11-18: Smettbo per Editor 29->42, danach jeder Read als
Battle-RAM verworfen → Tod (hp=0/113) nie erkannt.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.classes.munchlax import (  # noqa: E402
    HP_REBASELINE_READS,
    WIPE_ZERO_READ_THRESHOLD,
    Munchlax,
)


def _m() -> Munchlax:
    return Munchlax("localhost", 12345, {"client_id": "c1"}, {}, {"your_name": "Alice"},
                    nuz={"run_mode": "nuzlocke"})


def _slot(cur_hp, max_hp, lvl):
    return {"personality": 2994178102, "dexnr": 12, "cur_hp": cur_hp,
            "max_hp": max_hp, "lvl": lvl}


def test_editor_level_sprung_dann_tod_wird_erkannt():
    m = _m()
    assert m._is_pokemon_dead(1, _slot(81, 81, 29)) is False  # Baseline
    # Editor: 29 -> 42, max_hp 81 -> 113 — erst verworfen, dann übernommen
    for _ in range(HP_REBASELINE_READS):
        assert m._is_pokemon_dead(1, _slot(113, 113, 42)) is False
    state = m._pokemon_hp_state[(1, 2994178102)]
    assert (state["last_max_hp"], state["last_lvl"]) == (113, 42)
    dead = False
    for _ in range(WIPE_ZERO_READ_THRESHOLD):
        dead = m._is_pokemon_dead(1, _slot(0, 113, 42))
    assert dead is True


def test_battle_ram_ausreisser_mit_hp_null_bleibt_verworfen():
    m = _m()
    m._is_pokemon_dead(1, _slot(30, 30, 15))
    for _ in range(6):
        assert m._is_pokemon_dead(1, _slot(0, 999, 50)) is False
    state = m._pokemon_hp_state[(1, 2994178102)]
    assert state["last_max_hp"] == 30 and state["zero_reads"] == 0


def test_kurzer_ausreisser_unterbricht_stabil_zaehler():
    m = _m()
    m._is_pokemon_dead(1, _slot(30, 30, 15))
    for _ in range(HP_REBASELINE_READS - 1):
        m._is_pokemon_dead(1, _slot(999, 999, 50))
    m._is_pokemon_dead(1, _slot(30, 30, 15))  # zurück auf echten Wert
    m._is_pokemon_dead(1, _slot(999, 999, 50))  # Zähler neu ab 1
    assert m._pokemon_hp_state[(1, 2994178102)]["last_max_hp"] == 30


def test_battle_ram_wert_mit_schwankender_hp_wird_nicht_baseline():
    """Falscher Kampf-Wert: HP ändern sich pro Treffer → nie volle HP in Folge."""
    m = _m()
    m._is_pokemon_dead(1, _slot(30, 30, 15))
    for hp in (900, 700, 500, 300, 100, 0):
        assert m._is_pokemon_dead(1, _slot(hp, 999, 50)) is False
    assert m._pokemon_hp_state[(1, 2994178102)]["last_max_hp"] == 30


def test_reset_hp_state_nach_editor_tod_sofort_erkannt():
    import asyncio
    m = _m()
    m._is_pokemon_dead(1, _slot(81, 81, 29))
    asyncio.run(m.reset_hp_state(1, 2994178102))
    assert m._is_pokemon_dead(1, _slot(113, 113, 42)) is False  # neue Baseline
    assert m._is_pokemon_dead(1, _slot(0, 113, 42)) is True


def test_in_battle_level_up_weiter_sofort_akzeptiert():
    m = _m()
    m._is_pokemon_dead(1, _slot(30, 30, 15))
    assert m._is_pokemon_dead(1, _slot(0, 32, 16)) is (WIPE_ZERO_READ_THRESHOLD <= 1)
    assert m._pokemon_hp_state[(1, 2994178102)]["last_max_hp"] == 32
