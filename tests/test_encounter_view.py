"""Tests für die reine Encounter-Anzeige-Logik (backend/encounter_view.py)."""
from backend import encounter_view as ev
from backend.classes.pokedex_db import LEGACY_GIFT_ROUTE_MARKER

SPECIES_DE = {1: "Bisasam", 4: "Glumanda", 7: "Schiggy", 16: "Taubsi", 0: "Ei"}


def _enc(**kw):
    base = {
        "owner": "Ash_1", "edition": 34, "route": 100, "dexnr": 4,
        "lvl": 5, "shiny": 0, "method": "wild", "outcome": "caught",
        "is_dupes_skip": 0, "link_group_id": None,
    }
    base.update(kw)
    return base


# --- is_soullink ----------------------------------------------------------

def test_is_run_modes():
    assert ev.is_soullink({"run_mode": "coop"}) is True
    assert ev.is_soullink({"run_mode": "versus"}) is True
    assert ev.is_soullink({"run_mode": "nuzlocke"}) is False
    assert ev.is_soullink({"run_mode": "versus_ffa"}) is False
    assert ev.is_soullink({"run_mode": "disabled"}) is False
    assert ev.is_soullink({}) is False          # Default = nuzlocke
    assert ev.is_soullink(None) is False


# --- determine_status -----------------------------------------------------

def test_determine_status():
    assert ev.determine_status(_enc(outcome="caught")) == "caught"
    assert ev.determine_status(_enc(outcome="not_caught")) == "not_caught"
    assert ev.determine_status(_enc(is_dupes_skip=1)) == "dupes"   # dupes schlägt outcome
    assert ev.determine_status(_enc(outcome="quatsch")) == "unknown"


# --- build_encounter_rows -------------------------------------------------

def test_build_rows_basic_and_counts():
    encs = [_enc(route=100, dexnr=4), _enc(route=101, dexnr=7)]
    rows, enc_count, open_count = ev.build_encounter_rows(encs, SPECIES_DE)
    assert enc_count == 2
    assert open_count == 0
    assert {r["species"] for r in rows} == {"Glumanda", "Schiggy"}


def test_build_rows_player_filter():
    encs = [_enc(owner="Ash_1"), _enc(owner="Misty_2")]
    rows, enc_count, _ = ev.build_encounter_rows(encs, SPECIES_DE, player_filter="Misty_2")
    assert enc_count == 1
    assert rows[0]["owner"] == "Misty_2"


def test_build_rows_edition_filter_adds_open_routes():
    # Feuerrot (34): echte Route plus offene Routen aus der LUT
    encs = [_enc(edition=34, route=89)]  # 89 = eine bekannte FRLG-MAPSEC
    rows, enc_count, open_count = ev.build_encounter_rows(
        encs, SPECIES_DE, edition_filter=34)
    assert enc_count == 1
    assert open_count > 0                      # LUT liefert offene Routen
    assert any(r["is_open"] for r in rows)
    # Fremde Edition rausgefiltert
    encs2 = encs + [_enc(edition=43, route=200)]
    _, enc_count2, _ = ev.build_encounter_rows(encs2, SPECIES_DE, edition_filter=34)
    assert enc_count2 == 1


def test_build_rows_query():
    encs = [_enc(dexnr=4), _enc(dexnr=7)]
    rows, enc_count, _ = ev.build_encounter_rows(encs, SPECIES_DE, query="schig")
    assert enc_count == 1
    assert rows[0]["species"] == "Schiggy"


def test_build_rows_sort_buckets():
    encs = [
        _enc(route=50, dexnr=4),
        _enc(route=-1, dexnr=1),                       # Starter -> oben
        _enc(route=LEGACY_GIFT_ROUTE_MARKER, dexnr=7),  # Legacy-Marker -> unten
    ]
    rows, _, _ = ev.build_encounter_rows(encs, SPECIES_DE)
    assert rows[0]["route"] == -1
    assert rows[-1]["route"] == LEGACY_GIFT_ROUTE_MARKER
    assert rows[1]["route"] == 50


# --- aggregate_link_status ------------------------------------------------

def test_aggregate_link_status():
    # Nicht alle Mitglieder da -> pending
    assert ev.aggregate_link_status(["caught"], 2) == "pending"
    # Einer offen -> pending
    assert ev.aggregate_link_status(["caught", "unknown"], 2) == "pending"
    # Alle gefangen/erhalten -> complete
    assert ev.aggregate_link_status(["caught", "obtained"], 2) == "complete"
    # Einer verpasst -> failed
    assert ev.aggregate_link_status(["caught", "not_caught"], 2) == "failed"


# --- build_encounter_rows: deleted / locks ---------------------------------

def test_build_rows_deleted_gefiltert_und_mit_show_deleted():
    encs = [_enc(route=100, personality=1), _enc(route=101, personality=2, deleted=1)]
    rows, enc_count, _ = ev.build_encounter_rows(encs, SPECIES_DE)
    assert enc_count == 1 and rows[0]["route"] == 100
    rows, enc_count, _ = ev.build_encounter_rows(encs, SPECIES_DE, show_deleted=True)
    assert enc_count == 2
    assert [r["deleted"] for r in rows] == [False, True]


def test_build_rows_deleted_zaehlt_nicht_als_getroffene_route():
    encs = [_enc(edition=34, route=89, deleted=1)]
    rows, enc_count, open_count = ev.build_encounter_rows(encs, SPECIES_DE, edition_filter=34)
    assert enc_count == 0
    assert any(r["route"] == 89 and r["is_open"] for r in rows)


def test_build_rows_locks_und_personality():
    encs = [_enc(personality=77, manual_fields="outcome,lvl", is_first=1)]
    rows, _, _ = ev.build_encounter_rows(encs, SPECIES_DE)
    r = rows[0]
    assert r["personality"] == 77
    assert r["locks"] == {"outcome", "lvl"}
    assert r["manual_fields"] == "outcome,lvl"
    assert r["is_first"] == 1 and r["deleted"] is False


def test_build_rows_open_placeholder_hat_leere_felder():
    rows, _, _ = ev.build_encounter_rows([], SPECIES_DE, edition_filter=34)
    ph = next(r for r in rows if r["is_open"])
    assert ph["personality"] is None and ph["locks"] == set()
    assert ph["manual_fields"] == "" and ph["deleted"] is False


# --- group_soullink -------------------------------------------------------

def _link(link_id=1, route=100, members=None, state="pending", **kw):
    link = {"link_id": link_id, "route": route, "edition_gen": 3, "link_index": 0,
            "state": state, "expected_owners": ["Ash", "Misty"], "members": members or {}}
    link.update(kw)
    return link


def _mem(personality, outcome="caught", dexnr=4, edition=34, **kw):
    mem = {"personality": personality, "edition": edition, "outcome": outcome,
           "dexnr": dexnr, "method": "wild"}
    mem.update(kw)
    return mem


def test_group_soullink_zwei_mitglieder():
    encs = [
        _enc(owner="Ash_1", personality=10, dexnr=4, route=100),
        _enc(owner="Misty_2", personality=20, dexnr=7, route=100),
    ]
    links = {1: _link(1, 100, {"Ash": _mem(10), "Misty": _mem(20)}, state="complete")}
    groups = ev.group_soullink(links, encs, SPECIES_DE, expected_count=2)
    assert len(groups) == 1
    g = groups[0]
    assert g["link_id"] == 1 and g["link_group_id"] == 1
    assert g["agg_status"] == "complete"
    assert [m["slot"] for m in g["members"]] == [1, 2]
    assert [m["species"] for m in g["members"]] == ["Glumanda", "Schiggy"]
    assert g["members"][0]["owner"] == "Ash_1" and g["members"][0]["root_owner"] == "Ash"
    assert g["expected_owners"] == ["Ash", "Misty"] and g["manual"] is False


def test_group_soullink_dead_status_und_locks():
    encs = [_enc(owner="Ash_1", personality=10, manual_fields="outcome")]
    links = {1: _link(1, 100, {"Ash": _mem(10, outcome="dead", manual=1)})}
    m = ev.group_soullink(links, encs, SPECIES_DE)[0]["members"][0]
    assert m["status"] == "dead"
    assert m["locks"] == {"outcome"} and m["member_manual"] is True


def test_group_soullink_mitglied_ohne_encounter():
    links = {1: _link(1, 100, {"Ash": _mem(10, outcome="not_caught", dexnr=7),
                               "Misty": _mem(20, outcome="komisch")})}
    g = ev.group_soullink(links, [], SPECIES_DE)[0]
    by_root = {m["root_owner"]: m for m in g["members"]}
    assert by_root["Ash"]["slot"] is None and by_root["Ash"]["owner"] == "Ash"
    assert by_root["Ash"]["species"] == "Schiggy"
    assert by_root["Ash"]["status"] == "not_caught"
    assert by_root["Misty"]["status"] == "unknown"


def test_group_soullink_geloeschter_encounter_wird_ignoriert():
    encs = [_enc(owner="Ash_1", personality=10, dexnr=4, deleted=1)]
    links = {1: _link(1, 100, {"Ash": _mem(10, dexnr=7)})}
    m = ev.group_soullink(links, encs, SPECIES_DE)[0]["members"][0]
    assert m["slot"] is None and m["species"] == "Schiggy"


def test_group_soullink_pv_und_owner_muessen_passen():
    encs = [_enc(owner="Misty_2", personality=10, dexnr=4)]   # falscher Owner
    links = {1: _link(1, 100, {"Ash": _mem(10, dexnr=7)})}
    m = ev.group_soullink(links, encs, SPECIES_DE)[0]["members"][0]
    assert m["slot"] is None and m["species"] == "Schiggy"


def test_group_soullink_agg_fallback_bei_unbekanntem_state():
    encs = [_enc(owner="Ash_1", personality=10, outcome="caught")]
    links = {1: _link(1, 100, {"Ash": _mem(10)}, state="komisch")}
    assert ev.group_soullink(links, encs, SPECIES_DE, expected_count=2)[0]["agg_status"] == "pending"
    assert ev.group_soullink(links, encs, SPECIES_DE, expected_count=1)[0]["agg_status"] == "complete"


def test_group_soullink_edition_filter_und_query():
    encs = [
        _enc(owner="Ash_1", personality=10, edition=34, dexnr=4),
        _enc(owner="Ash_1", personality=11, edition=43, dexnr=7, route=200),
    ]
    links = {1: _link(1, 100, {"Ash": _mem(10)}),
             2: _link(2, 200, {"Ash": _mem(11, edition=43)})}
    groups = ev.group_soullink(links, encs, SPECIES_DE, edition_filter=43)
    assert [g["link_id"] for g in groups] == [2]
    groups = ev.group_soullink(links, encs, SPECIES_DE, query="glum")
    assert [g["link_id"] for g in groups] == [1]


def test_group_soullink_sortierung_nach_route():
    links = {1: _link(1, 300, {}), 2: _link(2, -1, {}), 3: _link(3, 50, {})}
    groups = ev.group_soullink(links, [], SPECIES_DE)
    assert [g["route"] for g in groups] == [-1, 50, 300]


def test_group_soullink_members_sorted_by_slot():
    encs = [_enc(owner="Misty_2", personality=20, dexnr=7),
            _enc(owner="Ash_1", personality=10, dexnr=4)]
    links = {1: _link(1, 100, {"Misty": _mem(20), "Ash": _mem(10)})}
    groups = ev.group_soullink(links, encs, SPECIES_DE)
    assert [m["slot"] for m in groups[0]["members"]] == [1, 2]


def test_build_rows_outcome_dead_female_raw():
    encs = [_enc(outcome="not_caught", dead=1, female=None),
            _enc(route=101, outcome="caught", female=1)]
    rows, _, _ = ev.build_encounter_rows(encs, SPECIES_DE)
    assert rows[0]["outcome"] == "not_caught" and rows[0]["dead"] is True
    assert rows[0]["female_raw"] is None
    assert rows[1]["outcome"] == "caught" and rows[1]["dead"] is False
    assert rows[1]["female_raw"] == 1
    ph = next(r for r in ev.build_encounter_rows([], SPECIES_DE, edition_filter=34)[0]
              if r["is_open"])
    assert ph["outcome"] == "unknown" and ph["dead"] is False


def test_unlinked_encounters_ohne_gruppe_geloeschte_und_is_first():
    encs = [
        _enc(owner="Ash_1", personality=10, is_first=1),                # gruppiert
        _enc(owner="Misty_2", personality=20, is_first=0),              # ohne Gruppe, is_first egal
        _enc(owner="Ash_1", personality=30, deleted=1),                 # geloescht
        _enc(owner="Ash_1", personality=20),                            # PV gleich, anderer Root
        _enc(owner="Ash_1", personality=-5),                            # Freitext-PV
    ]
    links = {1: _link(1, 100, {"Ash": _mem(10), "Misty": _mem(99)}, state="pending")}
    result = ev.unlinked_encounters(links, encs)
    assert [(e["owner"], e["personality"]) for e in result] == [
        ("Misty_2", 20), ("Ash_1", 20), ("Ash_1", -5)]
    assert ev.unlinked_encounters({}, encs[:1]) == encs[:1]


def test_link_targets_filtert_owner_und_vorhandene_mitglieder():
    encs = [_enc(owner="Ash_1", personality=10, route=100),
            _enc(owner="Misty_2", personality=20, route=100)]
    links = {
        1: _link(1, 100, {"Ash": _mem(10), "Misty": _mem(20)}, state="complete"),
        2: _link(2, 101, {"Misty": _mem(21)}, state="pending"),      # Ash fehlt noch
        3: _link(3, 102, {"Ash": _mem(30)}, state="pending"),        # Ash schon drin
    }
    groups = ev.group_soullink(links, encs, SPECIES_DE)
    targets = ev.link_targets(groups, "Ash")
    assert [t[1] for t in targets] == [2]
    assert "Route 101" in targets[0][0] and "Misty" in targets[0][0]
    assert ev.link_targets(groups, "Ash", exclude_link_id=2) == []
    assert ev.link_targets(groups, "Fremd") == []


def test_aggregate_link_status_dead_wins():
    from backend.encounter_view import aggregate_link_status
    assert aggregate_link_status(["caught", "dead"], 2) == "dead"
    assert aggregate_link_status(["dead"], 2) == "dead"
    assert aggregate_link_status(["caught", "not_caught"], 2) == "failed"
