"""Spieler-Slots live nachziehen (Testrunde 2026-11-02, owned=[4] nach Umstellung)."""
import asyncio
import logging
from types import SimpleNamespace

from backend.classes.citrahandler import CitraHandler
from backend.classes.munchlax import Munchlax


def _munchlax(pl: dict, connected: bool):
    m = Munchlax.__new__(Munchlax)
    m.pl = pl
    m.is_connected = connected
    m.logger = logging.getLogger("test_redeclare")
    m.sent = []

    async def send_message(msg):
        m.sent.append(msg)
    m.send_message = send_message
    return m


def test_redeclare_sendet_aktuelle_slots():
    pl = {"player_count": 4, "remote_1": False, "remote_2": True,
          "remote_3": True, "remote_4": True}
    m = _munchlax(pl, connected=True)

    async def run():
        m.writer_lock = asyncio.Lock()
        await m.redeclare_player_ids()
    asyncio.run(run())
    assert m.sent == [{"type": "declared_player_ids", "player_ids": [1]}]


def test_redeclare_ohne_verbindung_sendet_nichts():
    m = _munchlax({"player_count": 1, "remote_1": False}, connected=False)

    async def run():
        m.writer_lock = asyncio.Lock()
        await m.redeclare_player_ids()
    asyncio.run(run())
    assert m.sent == []


def _citra(pl: dict):
    c = CitraHandler.__new__(CitraHandler)
    c.logger = logging.getLogger("test_citra_slot")
    c.edition = 51
    c.player_number = 3  # Altwert muss zurückgesetzt werden
    c.munchlax = SimpleNamespace(pl=pl, editions={}, player_names={})
    return c


def test_citra_nimmt_letzten_lokalen_slot():
    c = _citra({"player_count": 3, "remote_1": False, "remote_2": False,
                "remote_3": True, "your_name": "Ash"})
    c.set_player_number()
    assert c.player_number == 2
    assert c.munchlax.editions == {2: 51}


def test_citra_alle_remote_kein_slot():
    c = _citra({"player_count": 2, "remote_1": True, "remote_2": True})
    c.set_player_number()
    assert c.player_number is None
