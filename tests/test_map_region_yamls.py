"""Datentests für die Kartenfeature-YAMLs unter backend/data/maps/.

Schützt gegen kaputte Generator-Ausgaben: jede Region-Datei muss ein vorhandenes
Kartenbild referenzieren und jeder Eintrag genau eine Geometrie-Form tragen
(px_rects, cells oder x/y), sonst wirft MapMenu._build_hotspots zur Laufzeit.
"""
import glob
import os

import yaml

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAPS = os.path.join(PROJECT_ROOT, "backend", "data", "maps")
IMAGES = os.path.join(PROJECT_ROOT, "backend", "ressources", "maps")


def _regions():
    for path in sorted(glob.glob(os.path.join(MAPS, "*.yml"))):
        with open(path, encoding="utf-8") as fh:
            yield os.path.basename(path), yaml.safe_load(fh)


def test_bild_und_geometrie():
    for name, data in _regions():
        assert os.path.isfile(os.path.join(IMAGES, data["image"])), f"{name}: Bild fehlt"
        for key, entry in data["entries"].items():
            assert isinstance(key, int), f"{name}: Key {key!r} ist kein int"
            assert entry.get("px_rects") or entry.get("cells") or "x" in entry, \
                f"{name}:{key} ohne Geometrie"
            pokemon = entry.get("pokemon")
            assert pokemon is None or isinstance(pokemon, list), \
                f"{name}:{key} pokemon ist keine Liste"
            for ed, srcs in (entry.get("pokemon_by_game") or {}).items():
                assert isinstance(ed, int) and isinstance(srcs, list), \
                    f"{name}:{key} pokemon_by_game[{ed!r}] ungültig"


def test_pokemon_by_game_ueberschreibt():
    from frontend.widgets.mapmenumd import MapMenu
    data = yaml.safe_load(open(os.path.join(MAPS, "sinnoh_dppt.yml"), encoding="utf-8"))
    dp = MapMenu._build_hotspots(data, 41)
    pt = MapMenu._build_hotspots(data, 43)
    # Starter: D/P am See der Wahrheit (76), Platin auf Route 201 (16).
    assert "gift" in dp[76]["pokemon"] and "gift" not in pt[76]["pokemon"]
    assert "gift" not in dp[16]["pokemon"] and "gift" in pt[16]["pokemon"]
    # Ohne Edition greift das gemeinsame pokemon-Feld.
    assert MapMenu._build_hotspots(data)[16]["pokemon"] == pt[16]["pokemon"]


def test_hgss_stichproben():
    data = yaml.safe_load(open(os.path.join(MAPS, "johto_kanto_hgss.yml"), encoding="utf-8"))
    e = data["entries"]
    # MAPSEC 126 == met_location Neuborkia (PKHeX gen4), 177 == Route 29.
    assert e[126]["label"] == "Neuborkia"
    assert e[177]["label"] == "Route 29"
    # Knofensa-Turm (204) kommt über worldMapX/Y, hat Wild-Pokemon.
    assert "land" in e[204]["pokemon"]
