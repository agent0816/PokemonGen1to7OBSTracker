"""Persistenz von Arceus-Encounter-Cache, Soullink-Links und -Toden.

Roundtrip: Arceus schreibt per Write-Through in die pokemon.db der Session,
eine frische Arceus-Instanz (= Host-Neustart) laedt denselben Stand wieder.
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.classes.arceus import Arceus
from backend.classes.pokedex_db import PokedexDB


REM = {"client_port": "43886", "helper_port": "43887"}


def _make_arceus(session: Path) -> Arceus:
    a = Arceus("localhost", 43886, REM, configsave=f"{session}/")
    a.munchlaxes = {}
    a.soullink_config = {
        "mode": "coop",
        "player_count": 2,
        "link_strategy": "full_chain",
        "team_membership": {},
        "expected_owners": ["Alice", "Bob"],
    }
    return a


def enc(pv, owner, route=5, dex=25, outcome="caught"):
    return {
        "personality": pv, "owner": owner, "edition": 51,
        "route": route, "dexnr": dex, "lvl": 5, "shiny": 0,
        "is_first": 1, "is_shiny_override": 0, "is_dupes_skip": 0,
        "has_balls": 1, "method": "wild", "outcome": outcome,
        "timestamp": "t",
    }


async def _flush(a: Arceus) -> None:
    while a._persist_futures:
        await asyncio.gather(*list(a._persist_futures))


def test_links_und_tode_ueberleben_neustart(tmp_path):
    session = tmp_path / "s1"

    async def erster_lauf():
        a = _make_arceus(session)
        await a.load_persistent_state()
        a._assign_link_group(enc(100, "Alice_1"))
        link = a._assign_link_group(enc(200, "Bob_2", dex=12))
        link["compensation_paid"] = True
        a._persist_link(link)
        death = a._process_death({"personality": 100, "owner": "Alice_1", "edition": 51})
        a._persist_death(death)
        a._persist_link(a.soullink_links[death["link_id"]])
        await _flush(a)
        return link["link_id"]

    link_id = asyncio.run(erster_lauf())

    # Encounter schreibt im echten Betrieb der Host-Munchlax in dieselbe DB.
    db = PokedexDB(session)
    db.connect()
    db.sync_encounter(enc(100, "Alice_1"))
    db.close()

    async def zweiter_lauf():
        b = _make_arceus(session)
        await b.load_persistent_state()
        return b

    b = asyncio.run(zweiter_lauf())
    assert set(b.soullink_links) == {link_id}
    link = b.soullink_links[link_id]
    assert link["state"] == "dead"
    assert set(link["members"]) == {"Alice", "Bob"}
    assert link["members"]["Alice"]["outcome"] == "dead"
    assert link["compensation_paid"] is True
    assert b.soullink_next_id == link_id + 1
    assert (100, "Alice") in b.soullink_deaths
    assert b.soullink_deaths[(100, "Alice")]["partners"][0]["owner"] == "Bob"
    assert (100, "Alice_1") in b.encounters


def test_gleicher_pfad_laedt_nicht_neu(tmp_path):
    session = tmp_path / "s1"

    async def lauf():
        a = _make_arceus(session)
        await a.load_persistent_state()
        a.soullink_links = {99: {"marker": True}}
        await a.load_persistent_state()
        return a

    a = asyncio.run(lauf())
    assert a.soullink_links == {99: {"marker": True}}


def test_reset_leert_persistierte_links(tmp_path):
    session = tmp_path / "s1"

    async def lauf():
        a = _make_arceus(session)
        await a.load_persistent_state()
        a._assign_link_group(enc(100, "Alice_1"))
        link = a._assign_link_group(enc(200, "Bob_2"))
        a._persist_link(link)
        await _flush(a)
        a._reset_session_state()
        await _flush(a)

    asyncio.run(lauf())
    db = PokedexDB(session)
    db.connect()
    assert db.load_links() == {}
    assert db.load_deaths() == {}
    db.close()


def test_ohne_session_pfad_kein_write(tmp_path):
    a = Arceus("localhost", 43886, REM)

    async def lauf():
        a._persist("clear_links")

    asyncio.run(lauf())
    assert a._persist_futures == set()


def test_lesefehler_deaktiviert_persistenz(tmp_path, monkeypatch):
    session = tmp_path / "s1"

    def kaputt(self, raise_errors=False):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(PokedexDB, "load_links", kaputt)

    async def lauf():
        a = _make_arceus(session)
        await a.load_persistent_state()
        return a

    a = asyncio.run(lauf())
    # Kein Pfad gesetzt → kein Ueberschreiben des unlesbaren Stands
    assert a._persist_path is None


def test_session_wechsel_leert_live_caches_ohne_neue_db_zu_leeren(tmp_path):
    """Session-Wechsel: Teams/Positionen der alten Session weg, Links der neuen
    Session werden geladen und NICHT geloescht (Testrunde 2026-11-02)."""
    s1 = tmp_path / "s1"
    s2 = tmp_path / "s2"

    async def vorbereiten_s2():
        b = _make_arceus(s2)
        await b.load_persistent_state()
        b._assign_link_group(enc(300, "Alice_1"))
        b._persist_link(next(iter(b.soullink_links.values())))
        await _flush(b)

    asyncio.run(vorbereiten_s2())

    async def wechsel():
        a = _make_arceus(s1)
        await a.load_persistent_state()
        a.teams = {1: ["alt"]}
        a.positions = {1: {"x": 1}}
        a.configsave = f"{s2}/"
        reloaded = await a.load_persistent_state()
        await _flush(a)
        return a, reloaded

    a, reloaded = asyncio.run(wechsel())
    assert reloaded is True
    assert a.teams == {} and a.positions == {}
    assert len(a.soullink_links) == 1
