"""Tests fuer backend/pokedex_view.py (Pokedex-Filter/Formatierung ohne UI)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.pokedex_view import (  # noqa: E402
    edition_to_gen, filter_rows, format_row, item_name, location_name, row_slot, species_name,
)

SPECIES = {0: "Ei", 25: "Pikachu", 4: "Glumanda"}


def _rows() -> list[dict]:
    return [
        {"dexnr": 25, "nickname": "Blitz", "edition": "Rot", "owner": 1, "shiny": 1},
        {"dexnr": 4, "nickname": "", "edition": "Blau", "owner": 2, "shiny": 0},
        {"dexnr": "egg", "nickname": "", "edition": "Gold", "owner": 1, "shiny": 0},
    ]


def test_species_name() -> None:
    assert species_name("egg", SPECIES) == "Ei"
    assert species_name("25", SPECIES) == "Pikachu"
    assert species_name("999", SPECIES) == "#999"
    assert species_name("abc", SPECIES) == "#abc"
    assert species_name("egg", {}) == "Ei"


def test_filter_shiny_and_slot() -> None:
    assert len(filter_rows(_rows(), "", "", True, SPECIES)) == 1
    assert len(filter_rows(_rows(), "", "1", False, SPECIES)) == 2
    assert filter_rows(_rows(), "", "3", False, SPECIES) == []


def test_row_slot_formate() -> None:
    # int-owner (Legacy-Team-DB)
    assert row_slot({"owner": 1}) == 1
    # build_owner-Format name_<slot>
    assert row_slot({"owner": "Bothhaft_2"}) == 2
    assert row_slot({"owner": "Name_mit_3"}) == 3
    # numerischer String
    assert row_slot({"owner": "4"}) == 4
    # kein Slot
    assert row_slot({"owner": "Bothhaft"}) is None
    assert row_slot({"owner": None}) is None
    assert row_slot({}) is None


def test_filter_slot_build_owner() -> None:
    rows = [
        {"dexnr": 25, "owner": "Alice_1"},
        {"dexnr": 4, "owner": "Bob_2"},
        {"dexnr": 7, "owner": "Alice_1"},
    ]
    assert [r["dexnr"] for r in filter_rows(rows, "", "1", False, SPECIES)] == [25, 7]
    assert [r["dexnr"] for r in filter_rows(rows, "", "2", False, SPECIES)] == [4]
    # nicht-numerischer Slot -> Filter aus
    assert len(filter_rows(rows, "", "x", False, SPECIES)) == 3


def test_filter_query() -> None:
    assert [r["dexnr"] for r in filter_rows(_rows(), "pikachu", "", False, SPECIES)] == [25]
    assert [r["dexnr"] for r in filter_rows(_rows(), "blitz", "", False, SPECIES)] == [25]
    assert [r["dexnr"] for r in filter_rows(_rows(), "blau", "", False, SPECIES)] == [4]
    assert filter_rows(_rows(), "nichts", "", False, SPECIES) == []


def test_filter_combination() -> None:
    assert len(filter_rows(_rows(), "pika", "1", True, SPECIES)) == 1
    assert filter_rows(_rows(), "pika", "2", True, SPECIES) == []


def test_format_row() -> None:
    row = {"dexnr": 25, "nickname": "Blitz", "lvl": 12, "shiny": 1, "edition": "Rot",
           "owner": 1, "item": "Beere", "route": -1, "personality": 0xABC,
           "last_seen": "2026-04-17T13:23:35+00:00"}
    cols = format_row(row, SPECIES)
    assert len(cols) == 11
    assert cols[1] == "Pikachu"
    assert cols[4] == "*"
    assert cols[8] == "Starter"
    assert cols[9] == "00000ABC"
    assert cols[10] == "2026-04-17 13:23"


def test_format_row_leer() -> None:
    cols = format_row({"dexnr": 4}, SPECIES)
    assert cols[4] == "" and cols[8] == "" and cols[9] == "" and cols[10] == ""


def test_edition_to_gen() -> None:
    assert edition_to_gen(33) == 3
    assert edition_to_gen(43) == 4
    assert edition_to_gen(52) == 5
    assert edition_to_gen(61) == 6
    assert edition_to_gen(72) == 7
    assert edition_to_gen(1) == 0


def test_item_name() -> None:
    assert item_name(0, 33) == ""
    assert item_name(None, 33) == ""
    assert item_name("", 33) == ""
    assert item_name(4, 33) == "Poke Ball"
    assert item_name(999999, 33) == "#999999"
    # Decoder speichert Slug-Strings -> aufhübschen statt "" (war Bug: Item-"–")
    assert item_name("abc", 33) == "Abc"
    assert item_name("oran-berry", 33) == "Oran Berry"
    assert item_name("kings-rock", 33) == "Kings Rock"


def test_location_name() -> None:
    assert location_name(-1, 33) == "Starter"
    assert location_name(16, 33) == "Route 101"
    assert location_name(999999, 33) == "Route 999999"
    assert location_name(None, 33) == "Route None"
