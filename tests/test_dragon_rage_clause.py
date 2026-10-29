"""Drachenwut-Klausel in Munchlax: Filter vor Wipe-Check/Death-Report + rule_flags."""

import asyncio

from backend.classes.munchlax import Munchlax
from backend.classes.pokedex_db import PokedexDB
from backend.dragon_rage import DRAGON_RAGE_FLAG

PV = 111
EDITION = 34


def _munchlax(tmp_path, **nuz):
    m = Munchlax("localhost", 12345, {"client_id": "c1"}, {}, {"your_name": "Alice"},
                 nuz={"run_mode": "nuzlocke", "rule_dragon_rage_clause": True,
                      "rule_dragon_rage_clause_badge_limit": 2, **nuz})
    db = PokedexDB(tmp_path)
    db.connect()
    m.pokedex_db = db
    m.dragon_rage_supported[1] = True
    return m


def _team(badges=0, nickname="Glumanda"):
    slot = {"personality": PV, "dexnr": 4, "cur_hp": 0, "max_hp": 20, "lvl": 5,
            "nickname": nickname}
    return [slot, None, None, None, None, None, badges, EDITION]


def _apply(m, team, is_dead=True):
    if not is_dead:
        team[0]["cur_hp"] = 10  # "lebt" = bekannte HP > 0
    return asyncio.run(m._apply_dragon_rage_clause(1, EDITION, team, [(PV, 4, is_dead)]))


def test_hp_none_hebt_vergebung_nicht_auf(tmp_path):
    # Review R5: unvollstaendiger Read (cur_hp None) ist kein "lebt"
    m = _munchlax(tmp_path)
    asyncio.run(m.note_dragon_rage_ko(1, PV))
    _apply(m, _team())
    m._dragon_rage_kos[(1, PV)] -= 2 * m.DRAGON_RAGE_HOLD_S + 1
    team = _team()
    team[0]["cur_hp"] = None
    asyncio.run(m._apply_dragon_rage_clause(1, EDITION, team, [(PV, 4, False)]))
    assert (1, PV) in m._forgiven_deaths
    assert (1, PV) in m._dragon_rage_kos
    assert _apply(m, _team())[0][2] is False


def test_ko_durch_drachenwut_wird_vergeben(tmp_path):
    m = _munchlax(tmp_path)
    seen = []
    m.on_death_forgiven_callback = seen.append
    asyncio.run(m.note_dragon_rage_ko(1, PV))
    assert _apply(m, _team())[0][2] is False
    assert seen and seen[0]["nickname"] == "Glumanda" and seen[0]["limit"] == 2
    # bleibt vergeben, solange HP 0 — Callback nur einmal
    assert _apply(m, _team())[0][2] is False
    assert len(seen) == 1


def test_genug_orden_tod_zaehlt(tmp_path):
    m = _munchlax(tmp_path)
    asyncio.run(m.note_dragon_rage_ko(1, PV))
    assert _apply(m, _team(badges=0b11))[0][2] is True


def test_selbst_eingesetzt_tod_zaehlt(tmp_path):
    m = _munchlax(tmp_path)
    m.pokedex_db.set_rule_flag("Alice_1", EDITION, DRAGON_RAGE_FLAG)
    asyncio.run(m.note_dragon_rage_ko(1, PV))
    assert _apply(m, _team())[0][2] is True


def test_regel_aus_tod_zaehlt(tmp_path):
    m = _munchlax(tmp_path, rule_dragon_rage_clause=False)
    asyncio.run(m.note_dragon_rage_ko(1, PV))
    assert _apply(m, _team())[0][2] is True


def test_ohne_ko_meldung_wartet_dann_zaehlt(tmp_path):
    m = _munchlax(tmp_path)
    assert _apply(m, _team())[0][2] is False, "Wartezeit auf BH-KO-Meldung"
    m._dragon_rage_hold[(1, PV)] -= m.DRAGON_RAGE_HOLD_S + 1
    assert _apply(m, _team())[0][2] is True


def test_nicht_unterstuetzte_edition_ohne_wartezeit(tmp_path):
    m = _munchlax(tmp_path)
    m.dragon_rage_supported[1] = False
    assert _apply(m, _team())[0][2] is True


def test_revive_loescht_vergebung(tmp_path):
    m = _munchlax(tmp_path)
    asyncio.run(m.note_dragon_rage_ko(1, PV))
    _apply(m, _team())
    _apply(m, _team(), is_dead=False)
    assert (1, PV) not in m._forgiven_deaths
    # frische KO-Meldung ueberlebt einen "lebt"-Tick (aelterer Broadcast) ...
    assert (1, PV) in m._dragon_rage_kos
    # ... und verfaellt erst nach 2x Hold-Zeit
    m._dragon_rage_kos[(1, PV)] -= 2 * m.DRAGON_RAGE_HOLD_S + 1
    _apply(m, _team(), is_dead=False)
    assert (1, PV) not in m._dragon_rage_kos


def test_ko_meldung_vor_altem_broadcast_bleibt_gueltig(tmp_path):
    # BH meldet K.O., Munchlax sieht zuerst noch einen alten Stand mit HP > 0
    m = _munchlax(tmp_path)
    asyncio.run(m.note_dragon_rage_ko(1, PV))
    _apply(m, _team(), is_dead=False)
    assert _apply(m, _team())[0][2] is False


def test_record_player_dragon_rage_setzt_flag(tmp_path):
    m = _munchlax(tmp_path, rule_run_start_on_ball=False)
    asyncio.run(m.record_player_dragon_rage(1, EDITION))
    assert m.pokedex_db.has_rule_flag("Alice_1", EDITION, DRAGON_RAGE_FLAG)


def test_rule_flags_reset(tmp_path):
    db = PokedexDB(tmp_path)
    db.connect()
    assert db.set_rule_flag("Alice_1", EDITION, DRAGON_RAGE_FLAG)
    assert db.set_rule_flag("Alice_1", EDITION, DRAGON_RAGE_FLAG), "idempotent"
    assert db.has_rule_flag("Alice_1", EDITION, DRAGON_RAGE_FLAG)
    assert not db.has_rule_flag("Alice_1", 35, DRAGON_RAGE_FLAG)
    assert db.reset_all_data()
    assert not db.has_rule_flag("Alice_1", EDITION, DRAGON_RAGE_FLAG)


def test_vergeben_bleibt_vergeben_nach_neuem_orden(tmp_path):
    # Review KRITISCH: Orden-Limit erreicht, Pokemon liegt noch mit 0 HP im Team
    m = _munchlax(tmp_path)
    asyncio.run(m.note_dragon_rage_ko(1, PV))
    assert _apply(m, _team())[0][2] is False
    assert _apply(m, _team(badges=0b11))[0][2] is False
    m.nuz["rule_dragon_rage_clause"] = False
    assert _apply(m, _team())[0][2] is False


def _ohne_pv(m):
    asyncio.run(m._apply_dragon_rage_clause(1, EDITION, _team(), [(222, 1, False)]))


def test_team_austritt_raeumt_nach_ablauf_auf(tmp_path):
    m = _munchlax(tmp_path)
    asyncio.run(m.note_dragon_rage_ko(1, PV))
    _apply(m, _team())
    m._dragon_rage_hold[(1, PV)] = 0.0  # Hold-Eintrag soll mit aufgeraeumt werden
    _ohne_pv(m)
    assert (1, PV) in m._forgiven_deaths, "erster Fehl-Tick loescht nichts"
    m._dragon_rage_absent[(1, PV)] -= 2 * m.DRAGON_RAGE_HOLD_S + 1
    _ohne_pv(m)
    assert (1, PV) in m._forgiven_deaths, "Vergebung ueberlebt den Austritt (R4)"
    assert (1, PV) not in m._dragon_rage_kos
    assert (1, PV) not in m._dragon_rage_hold
    assert (1, PV) not in m._dragon_rage_absent


def test_pv_fehlt_kurz_bleibt_vergeben(tmp_path):
    # Review R2: ein Tick mit unvollstaendig gelesenem Slot
    m = _munchlax(tmp_path)
    asyncio.run(m.note_dragon_rage_ko(1, PV))
    _apply(m, _team())
    _ohne_pv(m)
    assert _apply(m, _team())[0][2] is False
    assert (1, PV) not in m._dragon_rage_absent


def test_abgelaufener_hold_startet_nicht_neu(tmp_path):
    m = _munchlax(tmp_path)
    _apply(m, _team())
    m._dragon_rage_hold[(1, PV)] -= m.DRAGON_RAGE_HOLD_S + 1
    assert _apply(m, _team())[0][2] is True
    assert _apply(m, _team())[0][2] is True, "kein neuer Hold im Folgetick"


def test_leeres_team_haelt_vergebung(tmp_path):
    # Review R3: Soft-Reset/Titelmenue -> leeres Team ist kein Austritt
    m = _munchlax(tmp_path)
    asyncio.run(m.note_dragon_rage_ko(1, PV))
    _apply(m, _team())
    asyncio.run(m._apply_dragon_rage_clause(1, EDITION, _team(), []))
    assert (1, PV) not in m._dragon_rage_absent
    assert _apply(m, _team())[0][2] is False


def test_box_umweg_bleibt_vergeben(tmp_path):
    # Review R4: vergeben, >6 s in der Box, mit 0 HP zurueck ins Team
    m = _munchlax(tmp_path)
    asyncio.run(m.note_dragon_rage_ko(1, PV))
    _apply(m, _team())
    _ohne_pv(m)
    m._dragon_rage_absent[(1, PV)] -= 2 * m.DRAGON_RAGE_HOLD_S + 1
    _ohne_pv(m)
    assert _apply(m, _team())[0][2] is False


def test_has_rule_flag_db_fehler_ist_sicher(tmp_path):
    db = PokedexDB(tmp_path)
    db.connect()
    db.connection.execute("DROP TABLE rule_flags")
    assert db.has_rule_flag("Alice_1", EDITION, DRAGON_RAGE_FLAG) is True
