"""Tests für backend/evolution_info.py (Entwicklungs-Texte aus evolutions_gen*.yml)."""
from backend.evolution_info import evolution_text, load_evolutions
from backend.pokedex_view import load_species_de

SP = load_species_de()


def test_gen1_level_und_tausch():
    assert evolution_text(1, 1, SP) == "Entwickelt sich ab Lv. 16 zu Bisaknosp"
    assert evolution_text(64, 1, SP).startswith("Entwickelt sich durch Tausch zu ")


def test_gen1_evoli_nur_steine():
    targets = [r["to"] for r in load_evolutions(1)[133]]
    assert targets == [134, 135, 136]
    text = evolution_text(133, 1, SP)
    assert "Feuerstein zu" in text and "; mit Donnerstein zu" in text


def test_gen2_evoli_freundschaft_tag_nacht():
    rules = {r["to"]: r for r in load_evolutions(2)[133]}
    assert rules[196]["trigger"] == "happiness" and rules[196]["time"] == "day"
    assert rules[197]["trigger"] == "happiness" and rules[197]["time"] == "night"
    assert f"durch Freundschaft (Tag) zu {SP[196]}" in evolution_text(133, 2, SP)


def test_gen2_golbat_freundschaft():
    assert [r["to"] for r in load_evolutions(2)[42]] == [169]
    assert evolution_text(42, 2, SP).startswith("Entwickelt sich durch Freundschaft zu ")


def test_gen4_evoli_laubeon_ort():
    rules = {r["to"]: r for r in load_evolutions(4)[133]}
    assert rules[470].get("location") is True
    assert "durch Level-Up an besonderem Ort zu " + SP[470] in evolution_text(133, 4, SP)


def test_feebas_schoenheit_gen3_4():
    for gen in (3, 4):
        assert "durch Schönheit zu" in evolution_text(349, gen, SP)
    assert "durch Tausch (mit" in evolution_text(349, 5, SP)


def test_kein_evolution_und_unbekannt():
    assert evolution_text(3, 1, SP) == "Entwickelt sich nicht weiter"
    assert evolution_text(25, 0, SP) == ""
    assert evolution_text(0, 1, SP) == ""
    assert evolution_text(300, 1, SP) == ""  # Dex ausserhalb der Gen


def test_gen_filter_keine_spaetere_entwicklung():
    # Pikachu -> Raichu per Stein, Pichu erst ab Gen 2
    assert 172 not in load_evolutions(1)
    assert 172 in load_evolutions(2)


def test_rando_gleiche_anzahl_paart_nach_index():
    text = evolution_text(133, 1, SP, rando_targets=[1, 4, 7])
    assert text.count(";") == 2
    assert text.startswith("Entwickelt sich mit Wasserstein zu " + SP[1])
    assert f"mit Feuerstein zu {SP[7]}" in text


def test_rando_andere_anzahl_nutzt_erste_methode():
    text = evolution_text(1, 1, SP, rando_targets=[4, 7])
    assert text == (f"Entwickelt sich zu {SP[4]}, {SP[7]} "
                    "(Methode wie Original: ab Lv. 16)")


def test_rando_leer_und_ohne_original():
    assert evolution_text(1, 1, SP, rando_targets=[]) == "Entwickelt sich nicht weiter"
    assert evolution_text(3, 1, SP, rando_targets=[4]) == f"Entwickelt sich zu {SP[4]}"
