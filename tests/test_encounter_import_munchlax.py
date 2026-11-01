"""Encounter-Import: DB-Insert ohne Ueberschreiben, Server-Sync in Batches."""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import backend.classes.munchlax as munchlax_mod
from backend.classes.munchlax import Munchlax
from backend.classes.pokedex_db import PokedexDB


def _munchlax(tmp_path) -> Munchlax:
    m = Munchlax("localhost", 12345, {"client_id": "c1"}, {}, {"your_name": "Bob"},
                 nuz={"run_mode": "nuzlocke"})
    db = PokedexDB(tmp_path)
    db.connect()
    m.pokedex_db = db
    return m


def owner() -> str:
    return PokedexDB.build_owner("Bob", "1")


def enc(pv, route=5, is_first=1, method="wild", own=None):
    return {
        "personality": pv, "owner": own or owner(), "edition": 51, "route": route,
        "dexnr": 25, "lvl": 5, "shiny": 0, "is_first": is_first, "outcome": "caught",
        "method": method, "has_balls": 1, "timestamp": "t", "female": False, "form": "",
        "manual_fields": "", "deleted": 0, "dead": 0,
    }


def test_import_ueberschreibt_vorhandene_nicht(tmp_path):
    m = _munchlax(tmp_path)
    alt = dict(enc(100), lvl=40, manual_fields="lvl")
    m.pokedex_db.sync_encounter(alt)
    ok, count, _ = asyncio.run(m.import_encounters(1, [enc(100), enc(101, route=6)]))
    assert ok and count == 1
    assert m.pokedex_db.get_encounter(100, owner())["lvl"] == 40
    neu = m.pokedex_db.get_encounter(101, owner())
    assert neu["route"] == 6 and neu["manual_fields"] == "" and neu["outcome"] == "caught"


def test_import_fremder_spieler_abgelehnt(tmp_path):
    m = _munchlax(tmp_path)
    ok, count, _ = asyncio.run(m.import_encounters(3, [enc(100)]))
    assert not ok and count == 0
    assert m.pokedex_db.get_encounter(100, owner()) is None


def test_import_verwirft_fremden_owner(tmp_path):
    m = _munchlax(tmp_path)
    ok, count, _ = asyncio.run(m.import_encounters(1, [enc(100, own="Eve_1"), enc(101)]))
    assert ok and count == 1
    assert m.pokedex_db.get_encounter(100, "Eve_1") is None


def test_import_sendet_batches(tmp_path, monkeypatch):
    monkeypatch.setattr(munchlax_mod, "IMPORT_SYNC_BATCH", 2)
    m = _munchlax(tmp_path)
    sent: list = []

    async def lauf():
        m.writer_lock = asyncio.Lock()
        m.is_connected = True

        async def fake_send(message):
            sent.append(message)

        m.send_message = fake_send
        return await m.import_encounters(1, [enc(pv, route=pv) for pv in (1, 2, 3)])

    ok, count, _ = asyncio.run(lauf())
    assert ok and count == 3
    assert [msg["type"] for msg in sent] == ["encounter_sync", "encounter_sync"]
    assert [len(msg["encounters"]) for msg in sent] == [2, 1]


def test_import_kontext(tmp_path):
    m = _munchlax(tmp_path)
    m.editions[1] = 51
    m.pokedex_db.sync_encounter(enc(100, route=5))
    m.pokedex_db.sync_encounter(enc(101, route=6, is_first=0))
    m.pokedex_db.sync_encounter(enc(102, route=7, method="gift"))
    m.pokedex_db.sync_encounter(dict(enc(103, route=8), deleted=1))
    ctx = asyncio.run(m.encounter_import_context(1))
    assert ctx["owner"] == owner() and ctx["edition"] == 51
    assert ctx["existing_pvs"] == {100, 101, 102, 103}
    assert ctx["routes_with_first"] == {5}


def test_import_batch_fehler_bricht_nicht_ab(tmp_path, monkeypatch):
    monkeypatch.setattr(munchlax_mod, "IMPORT_SYNC_BATCH", 1)
    m = _munchlax(tmp_path)
    sent: list = []

    async def lauf():
        m.writer_lock = asyncio.Lock()
        m.is_connected = True

        async def fake_send(message):
            sent.append(message)
            if len(sent) == 1:
                raise ConnectionResetError("weg")

        m.send_message = fake_send
        return await m.import_encounters(1, [enc(pv, route=pv) for pv in (1, 2, 3)])

    ok, count, reason = asyncio.run(lauf())
    assert ok and count == 3 and len(sent) == 3
    assert "nachgeliefert" in reason


def test_import_host_ohne_verbindung_abgelehnt(tmp_path):
    m = _munchlax(tmp_path)
    m.rem["start_server"] = True
    ok, count, _ = asyncio.run(m.import_encounters(1, [enc(100)]))
    assert not ok and count == 0
    assert m.pokedex_db.get_encounter(100, owner()) is None
