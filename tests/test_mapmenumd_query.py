"""Regressionstest Karte: _query_catches muss die Session-DB wirklich lesen.

Live-Session 2026-11-18: die Abfrage legte PokedexDB an, rief aber nie
connect() auf → leere Liste, die Karte zeigte nie gefangene Pokemon.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.classes.pokedex_db import PokedexDB  # noqa: E402
from frontend.widgets.mapmenumd import MapMenu  # noqa: E402


def _insert(db: PokedexDB, pv: int, owner: str, edition: int, route: int) -> None:
    assert db.insert_encounter(pv, owner, edition, route, dexnr=193, lvl=15,
                               shiny=False, is_first=True, is_shiny_override=False,
                               is_dupes_skip=False, has_balls=True, outcome="caught")


def test_query_catches_liest_encounter(tmp_path: Path):
    db = PokedexDB(tmp_path)
    db.connect()
    _insert(db, 1, "Stephan_4", 34, 140)
    _insert(db, 2, "Bothhaft_1", 35, 140)
    db.close()

    rows = MapMenu._query_catches(str(tmp_path), 34)
    assert [(r["personality"], r["route"]) for r in rows] == [(1, 140)]
    assert len(MapMenu._query_catches(str(tmp_path), 0)) == 2


def test_query_catches_ohne_db_legt_nichts_an(tmp_path: Path):
    missing = tmp_path / "gibt_es_nicht"
    assert MapMenu._query_catches(str(missing), 34) == []
    assert not missing.exists()
    assert MapMenu._query_catches("", 34) == []
