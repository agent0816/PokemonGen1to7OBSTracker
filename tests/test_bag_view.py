"""Tests für die reine Bag-Anzeige-Logik (backend/bag_view.py)."""
from backend import bag_view as bv

# Edition 34 = Feuerrot (Gen 3). items3.yml: 1=master-ball, 3=great-ball, 15=burn-heal.


def _row(owner="Ash_1", edition="34", pocket="baelle", item_id=1, qty=1):
    return {"owner": owner, "edition": edition, "pocket": pocket,
            "item_id": item_id, "qty": qty, "last_seen": "", "first_seen": ""}


# --- Item-Namen -----------------------------------------------------------

def test_item_slug_und_name():
    assert bv.item_slug("34", 1) == "master-ball"
    assert bv.item_display_name("34", 1) == "Master Ball"
    assert bv.item_display_name("34", 15) == "Burn Heal"
    assert bv.item_display_name("34", 999999) == "#999999"   # unbekannt


def test_is_ball():
    assert bv.is_ball("34", 1) is True          # master-ball
    assert bv.is_ball("34", 3) is True          # great-ball
    assert bv.is_ball("34", 15) is False        # burn-heal
    assert bv.is_ball("34", 999999) is False    # unbekannt


def test_edition_to_gen_und_name():
    assert bv.edition_to_gen("34") == 3
    assert bv.edition_to_gen("61") == 6
    assert bv.edition_to_gen("xx") is None
    assert bv.edition_name("34") == "Feuerrot"
    assert bv.edition_name("99") is None


def test_pretty_iso_safe_int():
    assert bv.pretty_iso("2026-04-17T13:23:35+00:00") == "2026-04-17 13:23"
    assert bv.pretty_iso(None) == ""
    assert bv.safe_int("12") == 12
    assert bv.safe_int("x") == 0


def test_pocket_key_from_label():
    assert bv.pocket_key_from_label("Bälle") == "baelle"
    assert bv.pocket_key_from_label("TM/VM") == "tmvm"
    assert bv.pocket_key_from_label("Quatsch") is None


# --- Filter ---------------------------------------------------------------

def test_filter_player():
    rows = [_row(owner="Ash_1"), _row(owner="Misty_2")]
    out = bv.filter_bag_rows(rows, player_filter="Misty_2")
    assert len(out) == 1 and out[0]["owner"] == "Misty_2"


def test_filter_pocket_und_balls():
    rows = [
        _row(pocket="baelle", item_id=1),       # master-ball
        _row(pocket="medizin", item_id=15),      # burn-heal
    ]
    assert len(bv.filter_bag_rows(rows, pocket_key="baelle")) == 1
    only_balls = bv.filter_bag_rows(rows, balls_only=True)
    assert len(only_balls) == 1 and only_balls[0]["item_id"] == 1


def test_filter_query():
    rows = [_row(item_id=1), _row(pocket="medizin", item_id=15)]
    out = bv.filter_bag_rows(rows, query="burn")
    assert len(out) == 1 and out[0]["item_id"] == 15


def test_filter_sort_order():
    # Pocket-Reihenfolge: medizin(2) vor baelle(3)? _POCKET_ORDER: items, medizin, baelle...
    rows = [
        _row(pocket="baelle", item_id=1),
        _row(pocket="medizin", item_id=15),
    ]
    out = bv.filter_bag_rows(rows)
    assert [r["pocket"] for r in out] == ["medizin", "baelle"]


# --- Pocket-Labels --------------------------------------------------------

def test_pocket_labels_for():
    rows = [_row(edition="34", pocket="baelle")]
    labels = bv.pocket_labels_for(rows, "alle")
    assert labels[0] == "alle"
    assert "Bälle" in labels                     # Gen 3 hat baelle-Pocket
    assert bv.pocket_labels_for([], "alle") == ["alle"]


# --- Gruppierung ----------------------------------------------------------

def test_group_bag_sections_und_pocket_order():
    rows = [
        _row(owner="Ash_1", edition="34", pocket="baelle", item_id=1),
        _row(owner="Ash_1", edition="34", pocket="medizin", item_id=15),
        _row(owner="Misty_2", edition="34", pocket="baelle", item_id=3),
    ]
    sections = bv.group_bag(bv.filter_bag_rows(rows))
    assert len(sections) == 2
    s0 = sections[0]
    assert s0["owner"] == "Ash_1" and s0["edition"] == "34"
    assert s0["edition_name"] == "Feuerrot"
    # Pockets in Spielmenü-Reihenfolge: medizin vor baelle
    assert [p["key"] for p in s0["pockets"]] == ["medizin", "baelle"]
    assert s0["pockets"][0]["items"][0]["item_id"] == 15
