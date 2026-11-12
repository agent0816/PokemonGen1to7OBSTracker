"""Box-Gift-Erkennung (Phase 3, Live-Session 2026-11-11): Geschenk bei voller
Party landet in der PC-Box (Evoli Prismania-Villa) und muss trotzdem als Gift
verbucht werden."""
import asyncio
import logging
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.classes.bizhawk import Bizhawk  # noqa: E402

GIFT_LAYOUT = 188
EDITION_FR = 34
CLIENT = "player001"


def _mon(pv, dex=133, lvl=25, checksum_ok=True):
    return SimpleNamespace(personality=pv, dexnr=dex, lvl=lvl, shiny=False,
                           female=False, form="", route=94,
                           checksum_given=1, checksum_calculated=1 if checksum_ok else 2)


class _FakeDB:
    def __init__(self, known=()):
        self.access_lock = threading.Lock()
        self.known = set(known)

    def encounter_import_context(self, owner, edition):
        return set(self.known), set()


class _FakeTracker:
    def __init__(self, db):
        self.pokedex_db = db
        self.calls = []

    def _load_gifts(self, edition):
        return {GIFT_LAYOUT: [{"type": "gift", "name": "Evoli", "edition": [34, 35]}],
                8: [{"type": "gift", "name": "Karpador", "edition": [34, 35]}]}

    def process_gift_encounter(self, **kw):
        self.calls.append(kw)
        return SimpleNamespace(
            already_logged=False, token_earned=False, route=kw["route"],
            dexnr=kw["dexnr"], lvl=kw["lvl"], shiny=kw["shiny"], is_first=True,
            is_shiny_override=False, is_dupes_skip=False, has_balls=True,
            method="gift", outcome="obtained", female=kw["female"], form=kw["form"])


class _FakeMunchlax:
    def __init__(self, team):
        self.pl = {"your_name": "Tester"}
        self.team = team
        self.updated_boxes = []
        self.synced = []

    def snapshot_unsorted_teams(self):
        return {1: list(self.team)}

    async def update_boxes(self, player, boxes):
        self.updated_boxes.append((player, boxes))

    def send_encounter_sync(self, payload):
        self.synced.append(payload)
        return payload


def _make(party_count=6, team=None, boxes_seq=None, known=()):
    bh = Bizhawk.__new__(Bizhawk)
    bh.logger = logging.getLogger("test_box_gift")
    team = team if team is not None else [_mon(100 + i, dex=1) for i in range(6)]
    bh.munchlax = _FakeMunchlax(team)
    bh.encounter_tracker = _FakeTracker(_FakeDB(known))
    bh._ensure_encounter_tracker = lambda: True
    bh._box_gift_last_layout = {}
    bh._box_gift_watch = {}
    bh._box_gift_tasks = set()
    bh.box_request_queues = {CLIENT: []}
    bh._box_gift_busy = {}
    bh._team_ready = {CLIENT: True}
    bh._team_updated_events = {CLIENT: object()}
    bh.edition_per_client = {CLIENT: EDITION_FR}
    bh.box_reads = 0
    seq = list(boxes_seq or [])

    async def _party(client_id, edition):
        return party_count

    async def _boxes(client_id):
        bh.box_reads += 1
        return seq.pop(0) if seq else []

    async def _call(coro):
        return await coro

    bh._read_party_count = _party
    bh.read_and_decode_boxes = _boxes
    bh._call_on_munchlax = _call
    bh._dispatch_on_munchlax = lambda payload: None
    return bh


async def _walk(bh, layouts, edition=EDITION_FR):
    for layout in layouts:
        bh._track_box_gift_layout(CLIENT, edition, 1, layout)
        # Tasks laufen lassen
        for _ in range(5):
            await asyncio.sleep(0)
        if bh._box_gift_tasks:
            await asyncio.gather(*list(bh._box_gift_tasks))


def test_neue_box_pv_wird_als_gift_verbucht():
    before = [[_mon(1, dex=16), None]]
    after = [[_mon(1, dex=16), _mon(777)]]
    bh = _make(boxes_seq=[before, after])
    asyncio.run(_walk(bh, [187, GIFT_LAYOUT, 187]))
    calls = bh.encounter_tracker.calls
    assert len(calls) == 1
    assert calls[0]["personality"] == 777
    assert calls[0]["map_header_id"] == GIFT_LAYOUT
    assert calls[0]["owner"].startswith("Tester")
    assert bh.munchlax.updated_boxes  # Box-Ansicht aktualisiert


def test_party_nicht_voll_kein_box_read():
    bh = _make(party_count=5, boxes_seq=[[], [[_mon(777)]]])
    asyncio.run(_walk(bh, [187, GIFT_LAYOUT, 187]))
    assert bh.encounter_tracker.calls == []
    assert bh.box_reads == 0


def test_eingelagertes_teammitglied_ist_kein_gift():
    team = [_mon(100 + i, dex=1) for i in range(6)]
    before = [[None]]
    after = [[_mon(100, dex=1)]]  # Team-PV in Box gelegt
    bh = _make(team=team, boxes_seq=[before, after])
    asyncio.run(_walk(bh, [GIFT_LAYOUT, 187]))
    assert bh.encounter_tracker.calls == []


def test_bekannte_pv_aus_db_wird_nicht_verbucht():
    bh = _make(boxes_seq=[[[None]], [[_mon(777)]]], known={777})
    asyncio.run(_walk(bh, [GIFT_LAYOUT, 187]))
    assert bh.encounter_tracker.calls == []


def test_zu_viele_neue_pvs_unplausibel():
    after = [[_mon(701), _mon(702), _mon(703)]]
    bh = _make(boxes_seq=[[[None]], after])
    asyncio.run(_walk(bh, [GIFT_LAYOUT, 187]))
    assert bh.encounter_tracker.calls == []


def test_ei_wird_ignoriert():
    bh = _make(boxes_seq=[[[None]], [[_mon(777, dex="egg")]]])
    asyncio.run(_walk(bh, [GIFT_LAYOUT, 187]))
    assert bh.encounter_tracker.calls == []


def test_kein_gift_layout_keine_watch():
    bh = _make(boxes_seq=[[[None]], [[_mon(777)]]])
    asyncio.run(_walk(bh, [187, 95, 187]))
    assert bh.box_reads == 0
    assert bh.encounter_tracker.calls == []


def test_gleiches_layout_mehrfach_nur_eine_watch():
    bh = _make(boxes_seq=[[[None]], [[_mon(777)]]])
    asyncio.run(_walk(bh, [GIFT_LAYOUT, GIFT_LAYOUT, GIFT_LAYOUT, 187]))
    assert bh.box_reads == 2
    assert len(bh.encounter_tracker.calls) == 1


def test_gen4_wird_ignoriert():
    bh = _make(boxes_seq=[[[None]], [[_mon(777)]]])
    asyncio.run(_walk(bh, [GIFT_LAYOUT, 187], edition=43))
    assert bh.box_reads == 0


def test_echter_tracker_bucht_evoli_an_layout_188(tmp_path):
    """Integration: echte gift_encounters_gen3.yml + PokedexDB."""
    from backend.classes.pokedex_db import PokedexDB
    from backend.encounter_tracker import EncounterTracker
    db = PokedexDB(tmp_path)
    db.connect()
    try:
        tracker = EncounterTracker(db, {})
        res = tracker.process_gift_encounter(
            owner="Tester_1", edition=EDITION_FR, personality=777, dexnr=133,
            lvl=25, shiny=False, route=0, map_header_id=GIFT_LAYOUT)
        assert res is not None
        assert res.method == "gift"
        assert not res.already_logged
        # Non-repeatable: zweites Gift am selben Ort wird abgelehnt
        again = tracker.process_gift_encounter(
            owner="Tester_1", edition=EDITION_FR, personality=778, dexnr=133,
            lvl=25, shiny=False, route=0, map_header_id=GIFT_LAYOUT)
        assert again is None
    finally:
        db.close()


def test_halb_geschriebener_slot_checksum_falsch_kein_gift():
    bh = _make(boxes_seq=[[[None]], [[_mon(777, checksum_ok=False)]]])
    asyncio.run(_walk(bh, [GIFT_LAYOUT, 187]))
    assert bh.encounter_tracker.calls == []


def test_dex_ausserhalb_gen3_kein_gift():
    bh = _make(boxes_seq=[[[None]], [[_mon(777, dex=412)]]])
    asyncio.run(_walk(bh, [GIFT_LAYOUT, 187]))
    assert bh.encounter_tracker.calls == []


def test_team_unvollstaendig_keine_baseline():
    team = [_mon(100 + i, dex=1) for i in range(4)]  # Snapshot lueckenhaft
    bh = _make(team=team, boxes_seq=[[[None]], [[_mon(777)]]])
    asyncio.run(_walk(bh, [GIFT_LAYOUT, 187]))
    assert bh.encounter_tracker.calls == []
    assert bh.box_reads == 0


def test_leerer_box_read_keine_baseline():
    bh = _make(boxes_seq=[[], [[_mon(777)]]])
    asyncio.run(_walk(bh, [GIFT_LAYOUT, 187]))
    assert bh.encounter_tracker.calls == []
    assert bh.box_reads == 1  # Diff liest nicht mehr


def test_boxanzahl_abweichend_kein_diff():
    bh = _make(boxes_seq=[[[None], [None]], [[_mon(777)]]])
    asyncio.run(_walk(bh, [GIFT_LAYOUT, 187]))
    assert bh.encounter_tracker.calls == []


def test_reconnect_zwischen_baseline_und_diff_kein_gift():
    bh = _make(boxes_seq=[[[None]], [[_mon(777)]]])

    async def _run():
        await _walk(bh, [GIFT_LAYOUT])
        bh._team_updated_events[CLIENT] = object()  # neuer Handshake
        await _walk(bh, [187])

    asyncio.run(_run())
    assert bh.encounter_tracker.calls == []


def test_spielstand_nicht_bereit_keine_watch():
    bh = _make(boxes_seq=[[[None]], [[_mon(777)]]])
    bh._team_ready = {}
    asyncio.run(_walk(bh, [GIFT_LAYOUT, 187]))
    assert bh.box_reads == 0


def test_tracker_fehlt_layout_wird_spaeter_erneut_geprueft():
    bh = _make(boxes_seq=[[[None]], [[_mon(777)]]])
    tracker = bh.encounter_tracker
    bh.encounter_tracker = None
    bh._ensure_encounter_tracker = lambda: False
    asyncio.run(_walk(bh, [GIFT_LAYOUT]))
    assert CLIENT not in bh._box_gift_last_layout
    bh.encounter_tracker = tracker
    bh._ensure_encounter_tracker = lambda: True
    asyncio.run(_walk(bh, [GIFT_LAYOUT, 187]))
    assert len(tracker.calls) == 1


def test_wieder_betreten_waehrend_diff_bekommt_neue_watch():
    """Busy-Guard (Review R2): Wieder-Betreten startet Baseline NACH dem Diff."""
    seq = [[[None]], [[None]], [[None]], [[_mon(777)]]]
    bh = _make(boxes_seq=seq)

    async def _run():
        bh._track_box_gift_layout(CLIENT, EDITION_FR, 1, GIFT_LAYOUT)
        await asyncio.sleep(0)
        bh._track_box_gift_layout(CLIENT, EDITION_FR, 1, 187)   # Diff startet
        bh._track_box_gift_layout(CLIENT, EDITION_FR, 1, GIFT_LAYOUT)  # sofort zurueck
        while bh._box_gift_tasks:
            await asyncio.gather(*list(bh._box_gift_tasks))
        await _walk(bh, [187])

    asyncio.run(_run())
    assert bh.box_reads == 4
    assert len(bh.encounter_tracker.calls) == 1


def test_spielstand_spaeter_bereit_holt_baseline_nach():
    bh = _make(boxes_seq=[[[None]], [[_mon(777)]]])
    bh._team_ready = {}
    asyncio.run(_walk(bh, [GIFT_LAYOUT]))
    assert CLIENT not in bh._box_gift_last_layout
    bh._team_ready = {CLIENT: True}
    asyncio.run(_walk(bh, [GIFT_LAYOUT, 187]))
    assert len(bh.encounter_tracker.calls) == 1
