"""Ersttyp-Regeln nach Live-Test 2026-11-04 (Phase 3).

- party_rules.first_type_dupe_slots: ungueltige sperren, valide frei,
  zwischen validen bleibt die aeltere Gruppe (kleinere Link-ID) frei.
- EncounterTracker._has_type_clash_with_linked: nur Partner der Gruppe,
  in die der neue Encounter faellt.
- Retry-Grund: Ersttyp-Retry als RETRY_TYPE_CLASH, Anzeige "Retry (Ersttyp)".
"""
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.classes.pokedex_db import RETRY_DUPE, RETRY_TYPE_CLASH  # noqa: E402
from backend.encounter_tracker import EncounterTracker  # noqa: E402
from backend.encounter_view import STATUS_TEXT, determine_status  # noqa: E402
from backend.party_rules import first_type_dupe_slots, party_blocked_slots  # noqa: E402
from backend.type_lookup import first_type  # noqa: E402

NUZ = {"rule_single_type_per_team": True}
# Bisasam/Myrapla = Pflanze, Taubsi/Rattfratz = Normal, Glumanda = Feuer
GRASS_A, GRASS_B, NORMAL_A, NORMAL_B, FIRE = 1, 43, 16, 19, 4


def _mon(dex, pv):
    return SimpleNamespace(dexnr=dex, personality=pv)


def _link(link_id, members: dict, state="complete", route=1, idx=1,
          expected=("Ash", "Bob"), edition_gen=3, manual=False):
    link = {"link_id": link_id, "route": route, "edition_gen": edition_gen,
            "link_index": idx, "state": state, "expected_owners": list(expected),
            "members": members}
    if manual:
        link["manual"] = True
    return link


def _mem(pv, dex, outcome="caught"):
    return {"personality": pv, "dexnr": dex, "outcome": outcome}


def test_testdaten_typen():
    assert first_type(GRASS_A) == first_type(GRASS_B)
    assert first_type(NORMAL_A) == first_type(NORMAL_B)
    assert first_type(FIRE) != first_type(GRASS_A)


# ---------------------------------------------------------------- Party-Sperre

def test_regel_aus_nichts_gesperrt():
    team = [_mon(GRASS_A, 1), _mon(GRASS_B, 2)]
    assert first_type_dupe_slots(team, {}) == set()


def test_ohne_soullink_frueherer_slot_frei():
    team = [_mon(GRASS_A, 1), _mon(FIRE, 2), _mon(GRASS_B, 3)]
    assert first_type_dupe_slots(team, NUZ) == {2}


def test_zwei_valide_aeltere_gruppe_frei():
    team = [_mon(GRASS_B, 20), _mon(GRASS_A, 10)]
    links = {5: _link(5, {"Ash": _mem(10, GRASS_A)}),
             9: _link(9, {"Ash": _mem(20, GRASS_B)})}
    # Slot 0 (Gruppe 9) ist juenger → gesperrt, Slot 1 (Gruppe 5) frei
    assert first_type_dupe_slots(team, NUZ, links=links) == {0}


def test_failed_gruppe_sperrt_nur_sich_selbst():
    team = [_mon(GRASS_A, 10), _mon(GRASS_B, 20), _mon(NORMAL_A, 30)]
    links = {1: _link(1, {"Ash": _mem(10, GRASS_A, "not_caught")}, state="failed"),
             2: _link(2, {"Ash": _mem(20, GRASS_B)}),
             3: _link(3, {"Ash": _mem(30, NORMAL_A)})}
    assert first_type_dupe_slots(team, NUZ, links=links) == {0}


def test_ohne_gruppe_ungueltig():
    team = [_mon(NORMAL_A, 99), _mon(NORMAL_B, 30)]
    links = {3: _link(3, {"Ash": _mem(30, NORMAL_B)})}
    assert first_type_dupe_slots(team, NUZ, links=links) == {0}


def test_ungueltig_ohne_typ_konflikt_bleibt_frei():
    # Regel-Scope: nur Typ-Dopplungen; Retry-Fang mit eindeutigem Typ frei
    team = [_mon(FIRE, 99), _mon(GRASS_A, 10)]
    links = {1: _link(1, {"Ash": _mem(10, GRASS_A)})}
    assert first_type_dupe_slots(team, NUZ, links=links) == set()


def test_tot_markiert_ungueltig():
    team = [_mon(GRASS_A, 10), _mon(GRASS_B, 20)]
    links = {1: _link(1, {"Ash": _mem(10, GRASS_A)}),
             2: _link(2, {"Ash": _mem(20, GRASS_B)})}
    assert first_type_dupe_slots(team, NUZ, links=links, dead_pvs={10}) == {0}


def test_zwei_ungueltige_beide_gesperrt():
    team = [_mon(GRASS_A, 98), _mon(GRASS_B, 99)]
    assert first_type_dupe_slots(team, NUZ, links={}) == {0, 1}


def test_eier_ausgenommen():
    team = [_mon("egg", 1), _mon(GRASS_A, 10), _mon(0, 0)]
    links = {1: _link(1, {"Ash": _mem(10, GRASS_A)})}
    assert first_type_dupe_slots(team, NUZ, links=links) == set()


def test_party_blocked_slots_liest_munchlax():
    links = {1: _link(1, {"Ash": _mem(10, GRASS_A)}, state="dead"),
             2: _link(2, {"Ash": _mem(20, GRASS_B)})}
    mx = SimpleNamespace(
        nuz={"rule_single_type_per_team": True, "run_mode": "coop"},
        snapshot_soullink_links=lambda: links,
        snapshot_dead_pvs=lambda: {})
    team = [_mon(GRASS_A, 10), _mon(GRASS_B, 20)]
    assert party_blocked_slots(mx, 1, team) == {0}
    mx.nuz = {"rule_single_type_per_team": True, "run_mode": "nuzlocke"}
    assert party_blocked_slots(mx, 1, team) == {1}  # kein Soullink: Slot-Reihenfolge


# ---------------------------------------------------------------- Ersttyp-Clause

def _tracker(links):
    nuz = {"rule_first_type_clause_linked": True}
    return EncounterTracker(None, nuz, munchlax=SimpleNamespace(soullink_links=links))


def test_clash_nur_in_eigener_gruppe():
    # Route 5: Bob hat Pflanze in Gruppe idx 1 (Ash fehlt) → Clash
    links = {1: _link(1, {"Bob": _mem(10, GRASS_A)}, state="pending", route=5)}
    t = _tracker(links)
    assert t._has_type_clash_with_linked("Ash_1", GRASS_B, "wild", route=5, edition=33)


def test_kein_clash_mit_anderer_route():
    # Live-Bug: Partner-Typen aus fremden Gruppen (Importe) zaehlten mit
    links = {1: _link(1, {"Bob": _mem(10, GRASS_A), "Ash": _mem(11, FIRE)}, route=3),
             2: _link(2, {"Bob": _mem(20, NORMAL_A)}, state="pending", route=4)}
    t = _tracker(links)
    assert not t._has_type_clash_with_linked("Ash_1", GRASS_B, "wild", route=5, edition=33)
    assert not t._has_type_clash_with_linked("Ash_1", NORMAL_B, "wild", route=5, edition=33)


def test_kein_clash_wenn_eigene_gruppe_schon_voll():
    # Ash hat Route 5 schon (idx 1) → neuer Encounter faellt in idx 2
    links = {1: _link(1, {"Bob": _mem(10, GRASS_A), "Ash": _mem(11, FIRE)}, route=5),
             2: _link(2, {"Bob": _mem(20, NORMAL_A)}, state="pending", route=5, idx=2)}
    t = _tracker(links)
    assert not t._has_type_clash_with_linked("Ash_1", GRASS_B, "wild", route=5, edition=33)
    assert t._has_type_clash_with_linked("Ash_1", NORMAL_B, "wild", route=5, edition=33)


def test_kein_clash_mit_manueller_gruppe_oder_anderer_gen():
    links = {1: _link(1, {"Bob": _mem(10, GRASS_A)}, state="pending", route=5, manual=True),
             2: _link(2, {"Bob": _mem(20, NORMAL_A)}, state="pending", route=5,
                      edition_gen=4)}
    t = _tracker(links)
    assert not t._has_type_clash_with_linked("Ash_1", GRASS_B, "wild", route=5, edition=33)
    assert not t._has_type_clash_with_linked("Ash_1", NORMAL_B, "wild", route=5, edition=33)


def test_clash_ohne_route_kein_treffer():
    links = {1: _link(1, {"Bob": _mem(10, GRASS_A)}, state="pending", route=5)}
    assert not _tracker(links)._has_type_clash_with_linked("Ash_1", GRASS_B, "wild")


# ---------------------------------------------------------------- Retry-Grund

def test_retry_marker_typ_clash(monkeypatch):
    links = {1: _link(1, {"Bob": _mem(10, GRASS_A)}, state="pending", route=5)}
    t = _tracker(links)
    t.nuz.update({"rule_run_start_on_ball": False, "rule_same_species_retry": False})
    t.pokedex_db = SimpleNamespace(has_encounter_on_route=lambda *a: False)
    monkeypatch.setattr(t, "_has_balls", lambda *a: True)
    _balls, is_first, _shiny, marker = t._check_nuzlocke_flags(
        "Ash_1", 33, 5, GRASS_B, False, "wild")
    assert marker == RETRY_TYPE_CLASH
    assert not is_first


def test_anzeige_retry_grund():
    assert determine_status({"is_dupes_skip": RETRY_DUPE}) == "dupes"
    assert determine_status({"is_dupes_skip": RETRY_TYPE_CLASH}) == "type_retry"
    assert STATUS_TEXT["type_retry"] == "Retry (Ersttyp)"
    assert determine_status({"is_dupes_skip": RETRY_TYPE_CLASH, "dead": 1}) == "dead"


# ---------------------------------------------------------------- Review-Fixes

def test_pv_kollision_owner_root_trennt():
    # Gleiche PV 10 bei Bob (failed) und Ash (gueltig): Ash-Team darf nicht
    # Bobs Gruppen-Zustand erben
    links = {1: _link(1, {"Bob": _mem(10, GRASS_A, "not_caught")}, state="failed"),
             2: _link(2, {"Ash": _mem(10, GRASS_A)}),
             3: _link(3, {"Ash": _mem(20, GRASS_B)})}
    team = [_mon(GRASS_A, 10), _mon(GRASS_B, 20)]
    assert first_type_dupe_slots(team, NUZ, links=links, owner_root="Ash") == {1}


def test_party_blocked_slots_nutzt_player_names():
    links = {1: _link(1, {"Bob": _mem(10, GRASS_A)}, state="failed"),
             2: _link(2, {"Ash": _mem(10, GRASS_A)}),
             3: _link(3, {"Ash": _mem(20, GRASS_B)})}
    mx = SimpleNamespace(
        nuz={"rule_single_type_per_team": True, "run_mode": "coop"}, pl={"your_name": "X"},
        snapshot_soullink_links=lambda: links, snapshot_dead_pvs=lambda: {},
        snapshot_player_names=lambda: {2: "Ash"})
    assert party_blocked_slots(mx, 2, [_mon(GRASS_A, 10), _mon(GRASS_B, 20)]) == {1}


def test_snapshot_soullink_links_kopiert_members():
    import threading
    from backend.classes.munchlax import Munchlax
    mx = Munchlax.__new__(Munchlax)
    mx.state_lock = threading.Lock()
    mx.soullink_links = {1: _link(1, {"Ash": _mem(10, GRASS_A)})}
    snap = mx.snapshot_soullink_links()
    mx.soullink_links[1]["members"]["Bob"] = _mem(11, FIRE)
    mx.soullink_links[1]["members"]["Ash"]["outcome"] = "dead"
    assert list(snap[1]["members"]) == ["Ash"]
    assert snap[1]["members"]["Ash"]["outcome"] == "caught"


def test_refresh_party_rule_consumers_nur_bei_aenderung():
    import asyncio
    import threading
    from backend.classes.munchlax import Munchlax
    mx = Munchlax.__new__(Munchlax)
    mx.state_lock = threading.Lock()
    mx.logger = __import__("logging").getLogger("test")
    mx.nuz = {"rule_single_type_per_team": True, "run_mode": "coop"}
    mx.pl = {"your_name": "Ash"}
    mx.obs = None
    mx.sorted_teams = {1: [_mon(GRASS_A, 10), _mon(GRASS_B, 20)]}
    mx.editions = {1: 34}
    mx.dead_pvs = {}
    mx.player_names = {1: "Ash"}
    mx.soullink_links = {1: _link(1, {"Ash": _mem(10, GRASS_A)}),
                         2: _link(2, {"Ash": _mem(20, GRASS_B)})}
    calls = []

    async def broadcast(reason=""):
        calls.append(reason)
    mx.force_overlay_broadcast = broadcast
    mx.snapshot_player_names = lambda: dict(mx.player_names)
    asyncio.run(mx._refresh_party_rule_consumers("t1"))
    assert len(calls) == 1 and mx._last_blocked_slots == {1: frozenset({1})}
    asyncio.run(mx._refresh_party_rule_consumers("t2"))
    assert len(calls) == 1  # unveraendert → kein Broadcast
    mx.soullink_links[1]["state"] = "failed"
    asyncio.run(mx._refresh_party_rule_consumers("t3"))
    assert len(calls) == 2 and mx._last_blocked_slots == {1: frozenset({0})}
