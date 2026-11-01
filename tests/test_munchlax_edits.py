"""Client-Seite manueller Korrekturen: Sender, Capability-Gate, Solo-Pfad, Handler."""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.classes.munchlax import Munchlax
from backend.classes.pokedex_db import PokedexDB


def _munchlax(tmp_path) -> Munchlax:
    m = Munchlax("localhost", 12345, {"client_id": "c1"}, {}, {"your_name": "Bob"},
                 nuz={"run_mode": "nuzlocke"})
    db = PokedexDB(tmp_path)
    db.connect()
    m.pokedex_db = db
    return m


def _connect_fake(m: Munchlax, features=("manual_edits",)) -> list:
    sent: list = []
    m.is_connected = True
    m._server_supports_resume = True
    m._server_features = set(features)

    async def fake_send(message):
        sent.append(message)

    m.send_message = fake_send
    return sent


def enc(pv, owner="Bob_2"):
    return {
        "personality": pv, "owner": owner, "edition": 51, "route": 5,
        "dexnr": 25, "lvl": 5, "shiny": 0, "is_first": 1, "outcome": "caught",
        "method": "wild", "timestamp": "t",
    }


def test_solo_edit_schreibt_lokal_mit_sperre(tmp_path):
    m = _munchlax(tmp_path)
    m.pokedex_db.sync_encounter(enc(100))
    ok, _ = asyncio.run(m.edit_encounter(100, "Bob_2", changes={"outcome": "not_caught"}))
    row = m.pokedex_db.get_encounter(100, "Bob_2")
    assert ok and row["outcome"] == "not_caught"
    assert "outcome" in row["manual_fields"]


def test_solo_anlegen(tmp_path):
    m = _munchlax(tmp_path)
    ok, _ = asyncio.run(m.edit_encounter(-7, "Bob_2", create={"edition": 51, "route": 9}))
    row = m.pokedex_db.get_encounter(-7, "Bob_2")
    assert ok and row["route"] == 9 and row["deleted"] == 0


def test_mit_server_wird_gesendet_nicht_lokal(tmp_path):
    m = _munchlax(tmp_path)
    m.pokedex_db.sync_encounter(enc(100))

    async def lauf():
        m.writer_lock = asyncio.Lock()
        sent = _connect_fake(m)
        res = await m.edit_encounter(100, "Bob_2", changes={"lvl": 9})
        return res, sent

    (ok, _), sent = asyncio.run(lauf())
    assert ok and sent[0]["type"] == "encounter_edit"
    assert sent[0]["changes"] == {"lvl": 9}
    # lokal erst beim Server-Echo
    assert m.pokedex_db.get_encounter(100, "Bob_2")["lvl"] == 5


def test_alter_server_ohne_feature_wird_nicht_beschickt(tmp_path):
    m = _munchlax(tmp_path)

    async def lauf():
        m.writer_lock = asyncio.Lock()
        sent = _connect_fake(m, features=())
        res = await m.edit_soullink_link("unlink", personality=1, owner="Bob_2")
        return res, sent

    (ok, reason), sent = asyncio.run(lauf())
    assert not ok and "aktualisieren" in reason
    assert sent == []


def test_link_edit_ohne_verbindung_abgelehnt(tmp_path):
    m = _munchlax(tmp_path)
    ok, reason = asyncio.run(m.edit_soullink_death(1, "Bob_2", False))
    assert not ok and "Verbindung" in reason


def test_snapshot_ersetzt_links_und_tode(tmp_path):
    m = _munchlax(tmp_path)
    m.soullink_links = {99: {"link_id": 99}}
    m.soullink_deaths = {(1, "Bob"): {}}
    calls = []
    m.on_soullink_state_changed_callback = lambda: calls.append(1)
    m._handle_soullink_state_snapshot({
        "links": {3: {"link_id": 3}},
        "deaths": [{"personality": 5, "owner": "Alice"}],
        "alive_locks": [[7, "Bob"]],
    })
    assert set(m.soullink_links) == {3}
    assert set(m.soullink_deaths) == {(5, "Alice")}
    assert m.soullink_alive_locks == {(7, "Bob")}
    assert calls


def test_death_state_entfernt_tod_und_setzt_sperre(tmp_path):
    m = _munchlax(tmp_path)
    m.soullink_deaths = {(5, "Bob"): {}}
    m._handle_remote_death_state({"personality": 5, "owner": "Bob",
                                  "dead": False, "manual": True})
    assert (5, "Bob") not in m.soullink_deaths
    assert (5, "Bob") in m.soullink_alive_locks
    m._handle_remote_death_state({"personality": 5, "owner": "Bob",
                                  "dead": False, "manual": False})
    assert m.soullink_alive_locks == set()


def test_edit_rejected_ruft_callback(tmp_path):
    m = _munchlax(tmp_path)
    got = []
    m.on_edit_rejected_callback = lambda t, r: got.append((t, r))
    m._handle_edit_rejected({"request_type": "encounter_edit", "reason": "nein"})
    assert got == [("encounter_edit", "nein")]


def test_remote_sync_schreibt_sperre_ohne_rueckantwort(tmp_path):
    m = _munchlax(tmp_path)
    m.pokedex_db.sync_encounter(enc(100))
    locked = dict(enc(100), outcome="not_caught", manual_fields="outcome")

    async def lauf():
        m.writer_lock = asyncio.Lock()
        sent = _connect_fake(m)
        await m._handle_remote_encounters([locked])
        return sent

    sent = asyncio.run(lauf())
    assert sent == []  # kein Ping-Pong
    row = m.pokedex_db.get_encounter(100, "Bob_2")
    assert row["manual_fields"] == "outcome"
    # Automatik darf das gesperrte Outcome lokal nicht mehr ueberschreiben
    assert m.pokedex_db.update_encounter_outcome(100, "Bob_2", "caught") is False


def test_host_ohne_verbindung_schreibt_nicht_lokal(tmp_path):
    m = _munchlax(tmp_path)
    m.rem["start_server"] = True
    m.pokedex_db.sync_encounter(enc(100))
    ok, _ = asyncio.run(m.edit_encounter(100, "Bob_2", changes={"lvl": 9}))
    assert not ok
    assert m.pokedex_db.get_encounter(100, "Bob_2")["lvl"] == 5


def test_state_reload_nur_vom_host(tmp_path):
    m = _munchlax(tmp_path)

    async def lauf(host: bool):
        m.rem["start_server"] = host
        m.writer_lock = asyncio.Lock()
        sent = _connect_fake(m)
        await m.request_server_state_reload(clear=True)
        return sent

    assert asyncio.run(lauf(False)) == []
    assert asyncio.run(lauf(True))[0] == {"type": "server_state_reload", "clear": True}


def test_edit_messages_werden_nicht_replayed(tmp_path):
    m = _munchlax(tmp_path)
    for t in ("encounter_edit", "soullink_link_edit", "soullink_death_edit",
              "server_state_reload"):
        assert not m._is_bufferable({"type": t})


def test_auto_tod_markiert_lokal_und_respektiert_sperre(tmp_path):
    m = _munchlax(tmp_path)
    m.pokedex_db.sync_encounter(enc(100))
    m.pokedex_db.sync_encounter(enc(101))
    m.pokedex_db.apply_encounter_edit(101, "Bob_2", {"dead": 0})  # manuell lebendig
    m.pl["your_name"] = "Bob"
    asyncio.run(m._report_deaths(2, 51, [(100, 25, True), (101, 25, True)]))
    assert m.pokedex_db.get_encounter(100, "Bob_2")["dead"] == 1
    assert m.pokedex_db.get_encounter(101, "Bob_2")["dead"] == 0


def test_is_marked_dead():
    from types import SimpleNamespace
    from backend.party_rules import is_marked_dead
    dead = frozenset({5})
    assert is_marked_dead(SimpleNamespace(personality=5, dexnr=25), dead)
    assert not is_marked_dead(SimpleNamespace(personality=6, dexnr=25), dead)
    assert not is_marked_dead(SimpleNamespace(personality=None, dexnr=25), dead)  # Gen 1/2
    assert not is_marked_dead(SimpleNamespace(personality=5, dexnr="egg"), dead)
    assert not is_marked_dead({"personality": 5, "dexnr": 0}, dead)
    assert not is_marked_dead(SimpleNamespace(personality=5, dexnr=25), None)


def test_dead_pvs_cache_aus_db(tmp_path):
    m = _munchlax(tmp_path)
    m.configsave = str(tmp_path)
    m.pokedex_db.sync_encounter(enc(100, "Bob_2"))
    m.pokedex_db.sync_encounter(enc(101, "Alice_1"))
    m.pokedex_db.mark_encounter_dead(100, "Bob_2")
    m.pokedex_db.mark_encounter_dead(101, "Alice_1")
    m.pokedex_db.apply_encounter_edit(101, "Alice_1", {}, delete=True)  # geloescht zaehlt nicht
    asyncio.run(m.refresh_dead_pvs("test"))
    assert m.snapshot_dead_pvs() == {2: frozenset({100})}
