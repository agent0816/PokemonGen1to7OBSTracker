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


# --- group_soullink -------------------------------------------------------

def test_group_soullink_basic():
    encs = [
        _enc(owner="Ash_1", dexnr=4, route=100, link_group_id=1, outcome="caught"),
        _enc(owner="Misty_2", dexnr=7, route=100, link_group_id=1, outcome="caught"),
        _enc(owner="Ash_1", dexnr=16, route=101, link_group_id=2, outcome="not_caught"),
        _enc(owner="Misty_2", dexnr=1, route=101, link_group_id=2, outcome="caught"),
        _enc(owner="Ash_1", dexnr=4, route=102, link_group_id=None),  # ungelinkt -> raus
    ]
    groups = ev.group_soullink(encs, SPECIES_DE, expected_count=2)
    assert len(groups) == 2
    g1 = next(g for g in groups if g["link_group_id"] == 1)
    assert [m["slot"] for m in g1["members"]] == [1, 2]
    assert g1["agg_status"] == "complete"
    g2 = next(g for g in groups if g["link_group_id"] == 2)
    assert g2["agg_status"] == "failed"          # einer verpasst


def test_group_soullink_pending_when_member_missing():
    encs = [_enc(owner="Ash_1", dexnr=4, route=100, link_group_id=5, outcome="caught")]
    groups = ev.group_soullink(encs, SPECIES_DE, expected_count=2)
    assert len(groups) == 1
    assert groups[0]["agg_status"] == "pending"   # nur 1 von 2


def test_group_soullink_members_sorted_by_slot():
    encs = [
        _enc(owner="Misty_2", dexnr=7, route=100, link_group_id=1),
        _enc(owner="Ash_1", dexnr=4, route=100, link_group_id=1),
    ]
    groups = ev.group_soullink(encs, SPECIES_DE, expected_count=2)
    assert [m["slot"] for m in groups[0]["members"]] == [1, 2]
