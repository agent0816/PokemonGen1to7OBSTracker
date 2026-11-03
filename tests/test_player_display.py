"""Anzeigenamen statt "Spieler N" (Testrunde 2026-11-03)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.player_display import (
    labels_for_owners, labels_for_slots, owner_label, owner_name, owner_slot, slot_label,
)


def test_owner_teile():
    assert owner_slot("Stephan_1") == 1 and owner_name("Stephan_1") == "Stephan"
    assert owner_slot("Max_Mustermann_3") == 3 and owner_name("Max_Mustermann_3") == "Max_Mustermann"
    assert owner_slot("2") == 2 and owner_name("2") == ""
    assert owner_slot(None) is None


def test_slot_clientname_ohne_nummer():
    names = {1: "Stephan", 2: "Weberack", 3: "Destroyer"}
    assert slot_label(2, names) == "Weberack"
    assert labels_for_slots([1, 2, 3], names) == {1: "Stephan", 2: "Weberack", 3: "Destroyer"}


def test_mehrere_lokale_bizhawks_mit_nummer():
    names = {1: "Stephan", 2: "Stephan", 3: "Bob"}
    assert labels_for_slots([1, 2, 3], names) == {1: "Stephan (1)", 2: "Stephan (2)", 3: "Bob"}
    # auch wenn nur ein Slot abgefragt wird
    assert slot_label(2, names) == "Stephan (2)"


def test_fallbacks():
    assert slot_label(4, {}) == "Spieler 4"
    assert slot_label("3", {"3": "Bob"}) == "Bob"  # String-Keys aus Pickle/YAML


def test_owner_labels():
    names = {1: "Stephan"}
    labels = labels_for_owners(["Stephan_1", "Bob_2", "3"], names)
    assert labels == {"Stephan_1": "Stephan", "Bob_2": "Bob", "3": "Spieler 3"}
    # gleicher Name auf zwei Slots (getrennter Client nur ueber Owner bekannt)
    assert labels_for_owners(["Stephan_1", "Stephan_2"], {}) == {
        "Stephan_1": "Stephan (1)", "Stephan_2": "Stephan (2)"}
    # Duplikat gegen bekannten, nicht gelisteten Slot
    assert owner_label("Stephan_1", {1: "Stephan", 2: "Stephan"}) == "Stephan (1)"
