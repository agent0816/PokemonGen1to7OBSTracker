"""Tests für die Gen-4 map_id → Section-LUT (bh_pointers.get_map_section).

Schützt das YAML-Schlüsselformat: Würden die Keys versehentlich als Strings
geladen (statt int), lieferte .get() still None und das Kartenfeature zeigte
ohne Fehlermeldung keinen Marker. Die konkreten Werte sind decomp-verifiziert
(pret/pokeplatinum): Index 16 == Route 201, map_id 411 == Twinleaf (live-RE).
"""
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
os.chdir(PROJECT_ROOT)

import backend.bh_pointers as bh_pointers  # noqa: E402


def test_platin_twinleaf():
    # map_id 411 == Twinleaf Town; mapLabelTextID/met_location == 1.
    assert bh_pointers.get_map_section(43, 411) == 1


def test_platin_route201():
    # map_id 342 == Route 201; Section == 16 == encounter_locations_gen4.yml-Key.
    assert bh_pointers.get_map_section(43, 342) == 16


def test_keys_sind_int():
    # Keys müssen int sein (sonst still None). Stichprobe über die Tabelle.
    table = bh_pointers._MAPID_SECTIONS.get(43)
    assert table, "Platin-Tabelle (ed 43) fehlt"
    assert all(isinstance(k, int) for k in table), "map_id-Keys müssen int sein"
    assert all(isinstance(v, int) for v in table.values()), "Section-Werte müssen int sein"


def test_unbekannte_edition_und_mapid():
    # Keine Tabelle für Gen 3 (31) bzw. unbekannte map_id → None, kein Marker.
    assert bh_pointers.get_map_section(31, 411) is None
    assert bh_pointers.get_map_section(43, 99999) is None


def test_diamant_perl_twinleaf():
    # pokediamond sMapHeaders: map_id 411 == MAP_TWINLEAF_TOWN → Section 1.
    assert bh_pointers.get_map_section(41, 411) == 1
    assert bh_pointers.get_map_section(42, 411) == 1


def test_hgss_new_bark():
    # pokeheartgold: MAP_NEW_BARK == 60, .mapsec MAPSEC_NEW_BARK_TOWN == 126.
    assert bh_pointers.get_map_section(44, 60) == 126
    assert bh_pointers.get_map_section(45, 60) == 126
