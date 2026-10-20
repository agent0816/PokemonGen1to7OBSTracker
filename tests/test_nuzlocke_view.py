"""Tests fuer backend/nuzlocke_view.py — reine Nuzlocke/Soullink-UI-Logik."""

import backend.nuzlocke_view as nv


# ---------------------------------------------------------------- normalize_mode
def test_normalize_mode_legacy_off_to_nuzlocke():
    assert nv.normalize_mode("off") == "nuzlocke"
    assert nv.normalize_mode("") == "nuzlocke"
    assert nv.normalize_mode(None) == "nuzlocke"


def test_normalize_mode_unknown_falls_back():
    assert nv.normalize_mode("quatsch") == "nuzlocke"


def test_normalize_mode_known_passes_through():
    for key in ("nuzlocke", "coop", "versus", "versus_ffa", "disabled"):
        assert nv.normalize_mode(key) == key


# ------------------------------------------------------------------- rule_value
def test_rule_value_uses_initialize_tree_defaults():
    # Divergenz-Fix: diese zwei Keys wichen im Klassik-Widget vom Backend ab.
    # Kanonische Quelle ist jetzt initialize_tree -> True bzw. False.
    assert nv.rule_value({}, "rule_first_type_clause_static_exception") is True
    assert nv.rule_value({}, "rule_same_species_retry") is False


def test_rule_value_nuz_overrides_default():
    assert nv.rule_value({"rule_token_rule": True}, "rule_token_rule") is True
    assert nv.rule_value({"rule_run_start_on_ball": False},
                         "rule_run_start_on_ball") is False


def test_rule_checkboxes_keys_have_defaults():
    for key, _label in nv.RULE_CHECKBOXES:
        assert key in nv.RULE_DEFAULTS, f"{key} fehlt in RULE_DEFAULTS"


# --------------------------------------------------------------- clamp_player_count
def test_clamp_player_count():
    assert nv.clamp_player_count("3") == 3
    assert nv.clamp_player_count(0) == 1
    assert nv.clamp_player_count(9) == 4
    assert nv.clamp_player_count("bloed") == 2


# ----------------------------------------------------------------- collect_config
def test_collect_config_dedups_owners():
    nuz = {"rule_token_rule": True, "foo": 1}
    cfg = nv.collect_config("nuzlocke",
                            [("Alice", "A"), ("Bob", "B"), ("Alice", "C")], nuz)
    assert cfg["expected_owners"] == ["Alice", "Bob"]
    assert cfg["player_count"] == 2
    assert cfg["link_strategy"] == "full_chain"
    # nur rule_*-Keys landen in rules
    assert cfg["rules"] == {"rule_token_rule": True}


def test_collect_config_team_map_only_in_versus():
    rows = [("Alice", "A"), ("Bob", "B")]
    assert nv.collect_config("nuzlocke", rows, {})["team_membership"] == {}
    versus = nv.collect_config("versus", rows, {})["team_membership"]
    assert versus == {"Alice": "A", "Bob": "B"}


def test_collect_config_strips_and_skips_empty():
    cfg = nv.collect_config("nuzlocke", [("  Alice  ", "A"), ("", "B")], {})
    assert cfg["expected_owners"] == ["Alice"]


# ------------------------------------------------------------ connected_owner_names
def test_connected_owner_names_sorted_by_player_id():
    player_names = {2: "Bob", 1: "Alice"}
    out = nv.connected_owner_names(player_names, {}, None)
    assert out == ["Alice", "Bob"]


def test_connected_owner_names_fallback_and_self():
    player_names = {1: "Alice"}
    client_names = {"cid": "Carol"}
    out = nv.connected_owner_names(player_names, client_names, "Dave")
    assert out == ["Alice", "Carol", "Dave"]


def test_connected_owner_names_dedups_and_handles_str_pid():
    player_names = {"1": "Alice", "2": "Alice"}
    out = nv.connected_owner_names(player_names, {}, "Alice")
    assert out == ["Alice"]


# -------------------------------------------------------------- merge_owner_rows
def test_merge_owner_rows_keeps_existing_even_if_empty():
    rows = nv.merge_owner_rows(
        count=2, existing_owners=["", "Bob"], existing_teams=["A", "B"],
        expected_owners=["Alice", "Bob"], team_map={}, mode_value="nuzlocke")
    # i<existing -> behalten (leer bleibt leer, kein Rehydrate aus expected)
    assert [o for o, _ in rows] == ["", "Bob"]


def test_merge_owner_rows_fills_from_expected():
    rows = nv.merge_owner_rows(
        count=3, existing_owners=["Alice"], existing_teams=["A"],
        expected_owners=["Alice", "Bob", "Carol"], team_map={},
        mode_value="nuzlocke")
    assert [o for o, _ in rows] == ["Alice", "Bob", "Carol"]


def test_merge_owner_rows_post_hoc_dedup():
    rows = nv.merge_owner_rows(
        count=2, existing_owners=["Alice"], existing_teams=["A"],
        expected_owners=["Alice", "Alice"], team_map={}, mode_value="nuzlocke")
    assert [o for o, _ in rows] == ["Alice", ""]


def test_merge_owner_rows_team_from_map():
    rows = nv.merge_owner_rows(
        count=2, existing_owners=[], existing_teams=[],
        expected_owners=["Alice", "Bob"],
        team_map={"Alice": "C", "Bob": "D"}, mode_value="versus")
    assert rows == [("Alice", "C"), ("Bob", "D")]


# ------------------------------------------------------------- client_verify_diff
def test_client_verify_diff_matched_missing_extra():
    expected_pairs = [(0, "Alice"), (2, "Bob")]
    connected = ["Alice", "Carol"]
    matched, missing, extra = nv.client_verify_diff(expected_pairs, connected)
    assert matched == ["Alice"]
    assert missing == [(2, "Bob")]
    assert extra == ["Carol"]


def test_client_verify_diff_preserves_original_index():
    # Row 1 bewusst leer -> faellt aus expected_pairs, Index von Bob bleibt 2.
    expected_pairs = [(0, "Alice"), (2, "Bob")]
    _matched, missing, _extra = nv.client_verify_diff(expected_pairs, [])
    assert missing == [(0, "Alice"), (2, "Bob")]
