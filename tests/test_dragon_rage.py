"""DragonRageTracker: K.O.-Erkennung durch gegnerische Drachenwut."""

import pytest

from backend.dragon_rage import (
    DragonRageTracker, MOVE_DRAGON_RAGE as DR, parse_gen3_battle_results,
)

TACKLE = 33


def _tracker(player=0, enemy=0, hp=None):
    t = DragonRageTracker()
    t.on_battle_start()
    t.on_tick(player, enemy, {1: 20, 2: 30} if hp is None else hp)
    return t


def test_wechsel_auf_drachenwut_mit_ko_im_selben_tick():
    t = _tracker(enemy=TACKLE)
    res = t.on_tick(TACKLE, DR, {1: 0, 2: 30})
    assert res.dragon_rage_kos == [1]


def test_drachenwut_bleibt_aktiv_ueber_mehrere_zuege():
    t = _tracker(enemy=TACKLE, hp={1: 60})
    assert t.on_tick(0, DR, {1: 20}).dragon_rage_kos == []
    # Gegner setzt erneut Drachenwut ein — Wert bleibt 82, K.O. zaehlt trotzdem
    assert t.on_tick(0, DR, {1: 0}).dragon_rage_kos == [1]


def test_rest_82_aus_vorkampf_vergibt_nicht():
    t = _tracker(enemy=DR)
    assert t.on_tick(0, DR, {1: 0, 2: 30}).dragon_rage_kos == []


def test_anderer_angriff_beendet_drachenwut():
    t = _tracker(enemy=TACKLE)
    t.on_tick(0, DR, {1: 20, 2: 30})
    assert t.on_tick(0, TACKLE, {1: 0, 2: 30}).dragon_rage_kos == []


def test_ko_ohne_vorherige_hp_zaehlt_nicht():
    t = _tracker(enemy=TACKLE, hp={})
    assert t.on_tick(0, DR, {1: 0}).dragon_rage_kos == []


def test_spieler_setzt_drachenwut_ein():
    t = _tracker(player=TACKLE)
    assert t.on_tick(DR, 0, {1: 20}).player_used_dragon_rage is True
    # kein zweites Event, solange der Wert stehen bleibt
    assert t.on_tick(DR, 0, {1: 20}).player_used_dragon_rage is False


def test_spieler_rest_82_ist_kein_event():
    t = _tracker(player=DR)
    assert t.on_tick(DR, 0, {1: 20}).player_used_dragon_rage is False


def test_neuer_kampf_setzt_baseline_neu():
    t = _tracker(enemy=TACKLE)
    t.on_tick(0, DR, {1: 20})
    t.on_battle_start()
    t.on_tick(0, DR, {1: 20})          # Baseline im neuen Kampf
    assert t.on_tick(0, DR, {1: 0}).dragon_rage_kos == []


def test_parse_gen3_battle_results():
    data = bytearray(0x26)
    data[0x22:0x24] = (TACKLE).to_bytes(2, "little")
    data[0x24:0x26] = (DR).to_bytes(2, "little")
    assert parse_gen3_battle_results(bytes(data)) == (TACKLE, DR)
    with pytest.raises(ValueError):
        parse_gen3_battle_results(b"\x00" * 4)


def test_battle_end_beendet_drachenwut_kontext():
    t = _tracker(enemy=TACKLE)
    t.on_tick(0, DR, {1: 20, 2: 30})
    t.on_battle_end()
    assert t.on_tick(0, DR, {1: 0, 2: 30}).dragon_rage_kos == []
