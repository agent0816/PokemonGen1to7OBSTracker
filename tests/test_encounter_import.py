"""Tests für backend/encounter_import.py."""
from types import SimpleNamespace as NS

from backend import encounter_import as ei


def mon(pv, dex=25, lvl=5, route=10, tid=1, sid=2):
    return NS(personality=pv, dexnr=dex, lvl=lvl, route=route, ot_id=tid,
              ot_secret_id=sid, shiny=False, female=None, form="")


def test_own_trainer_majority():
    egg = NS(dexnr="egg", ot_id=7, ot_secret_id=7)
    mons = [mon(1), mon(2), mon(3, tid=9, sid=9), NS(dexnr=0), None, egg, egg, egg]
    assert ei.own_trainer(mons) == (1, 2)
    assert ei.own_trainer([None]) is None


def test_skips_and_counts():
    team = [mon(1), NS(dexnr="egg", personality=5), None, mon(None), mon(77)]
    boxes = [[mon(2, tid=9, sid=9)], [NS(dexnr=0)]]
    cands, skipped = ei.collect_candidates(team, boxes, (1, 2), {77})
    assert skipped == {"egg": 1, "known": 1, "no_pv": 1}
    assert [c.pv for c in cands] == [1, 2]
    assert cands[0].source == "Team Slot 1"
    assert cands[1].source == "Box 1, Platz 1"
    assert cands[0].method == "wild" and not cands[0].foreign_ot
    assert cands[1].method == "gift" and cands[1].foreign_ot


def test_default_first_lowest_wild_own():
    team = [mon(1, lvl=8), mon(2, lvl=4), mon(3, lvl=2, tid=9, sid=9),
            mon(4, lvl=3, route=11)]
    cands, _ = ei.collect_candidates(team, [], (1, 2), set())
    groups = ei.group_by_route(cands, set())
    assert [g.route for g in groups] == [10, 11]
    assert groups[0].default_first_pv == 2
    assert groups[1].default_first_pv == 4


def test_route_with_existing_first_no_default():
    cands, _ = ei.collect_candidates([mon(1)], [], (1, 2), set())
    group = ei.group_by_route(cands, {10})[0]
    assert group.default_first_pv is None and group.has_existing_first


def test_gift_only_route_no_default():
    cands, _ = ei.collect_candidates([mon(1, tid=9, sid=9)], [], (1, 2), set())
    assert ei.group_by_route(cands, set())[0].default_first_pv is None


def test_build_encounters():
    team = [mon(1, lvl=8), mon(2, lvl=4), mon(3, tid=9, sid=9), mon(4)]
    cands, _ = ei.collect_candidates(team, [], (1, 2), set())
    groups = ei.group_by_route(cands, set())
    encs = ei.build_encounters(groups, {1, 2, 3}, {2}, "spieler_1", 5)
    by_pv = {e["personality"]: e for e in encs}
    assert set(by_pv) == {1, 2, 3}
    assert by_pv[2]["is_first"] == 1 and by_pv[1]["is_first"] == 0
    assert by_pv[3]["method"] == "gift" and by_pv[3]["is_first"] == 1
    for e in encs:
        assert e["manual_fields"] == "" and e["deleted"] == 0 and e["dead"] == 0
        assert e["outcome"] == "caught" and e["has_balls"] == 1
        assert e["is_dupes_skip"] == 0 and e["is_shiny_override"] == 0
        assert e["owner"] == "spieler_1" and e["edition"] == 5 and e["timestamp"]
