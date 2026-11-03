"""Auto-Bindung manueller Encounter (Pseudo-PV) an echte PVs (Testrunde 2026-11-02)."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.classes.arceus import Arceus
from backend.classes.pokedex_db import PokedexDB
from backend.encounter_edit import find_rebind_candidate, new_encounter


def _row(pv, dex, deleted=0):
    return {"personality": pv, "dexnr": dex, "deleted": deleted}


def test_kandidat_gleiche_art():
    rows = [_row(-5, 180), _row(123, 180), _row(-6, 16)]
    assert find_rebind_candidate(rows, 180)["personality"] == -5


def test_kandidat_familie_wenn_eindeutig():
    # Manuell als Voltilamm (179) angelegt, gefangen/entwickelt als Waaty (180)
    rows = [_row(-5, 179)]
    assert find_rebind_candidate(rows, 180, [179, 180, 181])["personality"] == -5


def test_kandidat_mehrdeutig_none():
    rows = [_row(-5, 180), _row(-6, 180)]
    assert find_rebind_candidate(rows, 180) is None


def test_kandidat_geloescht_oder_echt_ignoriert():
    rows = [_row(-5, 180, deleted=1), _row(77, 180)]
    assert find_rebind_candidate(rows, 180) is None


def test_db_rebind(tmp_path):
    db = PokedexDB(tmp_path)
    db.connect()
    db.sync_encounter(new_encounter(-5, "Ash_1", {"edition": 32, "dexnr": 180, "route": 18}))
    assert db.rebind_encounter("Ash_1", -5, 4242) is True
    assert db.get_encounter(-5, "Ash_1") is None
    assert db.get_encounter(4242, "Ash_1")["dexnr"] == 180
    # Ziel-PV existiert schon -> kein Rebind
    db.sync_encounter(new_encounter(-6, "Ash_1", {"edition": 32, "dexnr": 16, "route": 16}))
    assert db.rebind_encounter("Ash_1", -6, 4242) is False
    db.close()


REM = {"client_port": "43886", "helper_port": "43887"}


def _arceus():
    a = Arceus("localhost", 43886, REM)
    a.munchlaxes = {}
    a.soullink_config = {"mode": "coop", "player_count": 2, "link_strategy": "full_chain",
                         "team_membership": {}, "expected_owners": ["Ash", "Bob"]}
    a._client_owns_owner = lambda cid, owner: True
    return a


def test_server_rebind_schluesselt_encounter_link_und_tod_um():
    async def run():
        a = _arceus()
        enc = new_encounter(-5, "Ash_1", {"edition": 32, "dexnr": 180, "route": 18})
        a.encounters[(-5, "Ash_1")] = enc
        link = a._assign_link_group(enc)
        a.soullink_deaths[(-5, "Ash")] = {"personality": -5, "owner": "Ash", "link_id": link["link_id"]}
        a._handle_encounter_rebind("c1", {"type": "encounter_rebind", "owner": "Ash_1",
                                          "old_personality": -5, "new_personality": 4242})
        await asyncio.sleep(0)
        return a, link
    a, link = asyncio.run(run())
    assert (-5, "Ash_1") not in a.encounters
    assert a.encounters[(4242, "Ash_1")]["personality"] == 4242
    assert link["members"]["Ash"]["personality"] == 4242
    assert (4242, "Ash") in a.soullink_deaths and (-5, "Ash") not in a.soullink_deaths


def test_server_rebind_lehnt_echte_alte_pv_ab():
    async def run():
        a = _arceus()
        a.encounters[(77, "Ash_1")] = new_encounter(77, "Ash_1", {"edition": 32, "dexnr": 180})
        a._handle_encounter_rebind("c1", {"type": "encounter_rebind", "owner": "Ash_1",
                                          "old_personality": 77, "new_personality": 4242})
        await asyncio.sleep(0)
        return a
    a = asyncio.run(run())
    assert (77, "Ash_1") in a.encounters and (4242, "Ash_1") not in a.encounters


class _FakeDB:
    connection = object()

    def __init__(self, rows):
        self.rows = rows

    def get_encounter(self, pv, owner):
        return next((r for r in self.rows if r["personality"] == pv), None)

    def get_encounters(self, owner, edition=None):
        return list(self.rows)


def _client(rows, answer):
    import logging
    from backend.classes.munchlax import Munchlax
    m = Munchlax.__new__(Munchlax)
    m.logger = logging.getLogger("test_rebind_client")
    m.pokedex_db = _FakeDB(rows)
    m._ensure_pokedex_db = lambda: None
    m.is_connected = True
    m._server_supports_resume = True
    m._server_features = {"encounter_rebind"}
    m._pending_rebinds = {}
    m.sent = []

    async def send_message(msg):
        m.sent.append(msg)
        if answer is not None:
            asyncio.get_event_loop().call_soon(
                m._resolve_pending_rebind, dict(msg), answer)
    m.send_message = send_message
    return m


def test_client_true_nur_bei_server_bestaetigung():
    rows = [{"personality": -5, "dexnr": 180, "deleted": 0}]

    async def run(answer):
        m = _client(rows, answer)
        m.writer_lock = asyncio.Lock()
        return await m.try_rebind_manual_encounter("Ash_1", 32, 4242, 180), m
    ok, m = asyncio.run(run(True))
    assert ok is True and m.sent[0]["old_personality"] == -5
    ok, _ = asyncio.run(run(False))
    assert ok is False


def test_client_kandidat_in_arbeit_wird_nicht_doppelt_vergeben():
    rows = [{"personality": -5, "dexnr": 180, "deleted": 0}]

    async def run():
        m = _client(rows, None)
        m.writer_lock = asyncio.Lock()
        m._pending_rebinds[("Ash_1", -5)] = asyncio.get_event_loop().create_future()
        return await m.try_rebind_manual_encounter("Ash_1", 32, 4243, 180), m
    ok, m = asyncio.run(run())
    assert ok is False and m.sent == []
