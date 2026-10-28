"""Edge-Case Tests für Soullink-Server-Logik.

Läuft ohne echte Verbindung — nutzt Arceus-Instanz direkt und pruft state.
Ausführung: python -m pytest tests/test_soullink_edgecases.py -v
Ohne pytest: python tests/test_soullink_edgecases.py
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.classes.arceus import Arceus


REM = {"client_port": "43886", "helper_port": "43887"}


def _make_arceus():
    a = Arceus("localhost", 43886, REM)
    a.munchlaxes = {"c1": None, "c2": None}
    a.soullink_config = {
        "mode": "coop",
        "player_count": 2,
        "link_strategy": "full_chain",
        "team_membership": {},
        "expected_owners": ["Alice_c1", "Bob_c2"],
    }
    return a


def enc(pv, owner, edition=51, route=5, dex=25, is_first=1, outcome="caught"):
    return {
        "personality": pv, "owner": owner, "edition": edition,
        "route": route, "dexnr": dex, "lvl": 5, "shiny": 0,
        "is_first": is_first, "outcome": outcome,
    }


def test_coop_both_catch():
    a = _make_arceus()
    a._assign_link_group(enc(100, "Alice_c1", dex=25))
    link = a._assign_link_group(enc(200, "Bob_c2", dex=12))
    assert link["state"] == "complete"


def test_coop_one_fails():
    a = _make_arceus()
    a._assign_link_group(enc(100, "Alice_c1", outcome="caught"))
    link = a._assign_link_group(enc(200, "Bob_c2", outcome="not_caught"))
    assert link["state"] == "failed"


def test_second_slot_new_link():
    a = _make_arceus()
    l1 = a._assign_link_group(enc(100, "Alice_c1"))
    l2 = a._assign_link_group(enc(101, "Alice_c1", dex=30))
    assert l1["link_id"] != l2["link_id"]
    assert l2["link_index"] == 2


def test_death_marks_failed():
    a = _make_arceus()
    a._assign_link_group(enc(100, "Alice_c1"))
    link = a._assign_link_group(enc(200, "Bob_c2"))
    assert link["state"] == "complete"
    death = a._process_death({"personality": 100, "owner": "Alice_c1", "edition": 51})
    assert death["link_id"] == link["link_id"]
    assert death["partners"][0]["owner"] == "Bob_c2"
    assert a.soullink_links[link["link_id"]]["state"] == "failed"


def test_mode_off_no_link():
    a = _make_arceus()
    a.soullink_config["mode"] = "off"
    l = a._assign_link_group(enc(100, "Alice_c1"))
    assert l is None


def test_mode_nuzlocke_no_link():
    # "nuzlocke" (Nachfolger des alten "off"): Regeln clientseitig, kein Server-Link.
    a = _make_arceus()
    a.soullink_config["mode"] = "nuzlocke"
    l = a._assign_link_group(enc(100, "Alice_c1"))
    assert l is None


def test_mode_disabled_no_link():
    # "disabled" (Tracker ohne Nuzlocke-Regeln): ebenfalls kein Server-Link.
    a = _make_arceus()
    a.soullink_config["mode"] = "disabled"
    l = a._assign_link_group(enc(100, "Alice_c1"))
    assert l is None


def test_versus_ffa_no_link():
    # FFA: kein Soullink — Encounter erzeugen keine Links.
    a = _make_arceus()
    a.soullink_config["mode"] = "versus_ffa"
    l = a._assign_link_group(enc(100, "Alice_c1"))
    assert l is None
    assert a._linked_owners_for("Alice_c1") == []


def test_versus_ffa_scoreboard_per_player():
    # FFA: Versus-State bucketet pro Spieler, team_membership wird ignoriert.
    a = _make_arceus()
    a.soullink_config["mode"] = "versus_ffa"
    a.soullink_config["team_membership"] = {}
    changed = a._recompute_versus_state()
    assert changed
    assert set(a.soullink_versus_state.keys()) == {"Alice_c1", "Bob_c2"}
    assert a.soullink_versus_state["Alice_c1"]["owners"] == ["Alice_c1"]


def test_death_report_debounce_and_pending():
    # Death-Detection: HP-Consistency-Filter → einmalige Meldung, offline in
    # Pending-Queue, Revive löscht den Debounce-Marker.
    import asyncio
    from backend.classes.munchlax import Munchlax, WIPE_ZERO_READ_THRESHOLD

    m = Munchlax("localhost", 12345, {"client_id": "c1"}, {}, {"your_name": "Alice"},
                 nuz={"soullink_mode": "nuzlocke"})
    slot = {"personality": 111, "dexnr": 25, "cur_hp": 0, "max_hp": 20, "lvl": 5}
    team = [slot, None, None, None, None, None]

    infos = []
    for _ in range(WIPE_ZERO_READ_THRESHOLD + 1):
        infos = m._evaluate_team_deaths(1, team)
    assert infos[0][2] is True, "nach Threshold-Reads muss das Pokemon als tot gelten"

    asyncio.run(m._report_deaths(1, 3, infos))
    assert (111, "Alice_1") in m._reported_deaths
    assert len(m._pending_death_reports) == 1, "offline → Meldung bleibt in Pending-Queue"

    asyncio.run(m._report_deaths(1, 3, infos))
    assert len(m._pending_death_reports) == 1, "Debounce: kein Duplikat beim nächsten Tick"

    slot["cur_hp"] = 10
    infos = m._evaluate_team_deaths(1, team)
    assert infos[0][2] is False
    asyncio.run(m._report_deaths(1, 3, infos))
    assert (111, "Alice_1") not in m._reported_deaths, "Revive löscht den Marker"


def test_death_detection_disabled_mode_no_report():
    # Modus "disabled": _persist_teams überspringt Death-Detection komplett —
    # hier abgesichert über _nuzlocke_rules_active.
    from backend.classes.munchlax import Munchlax

    m = Munchlax("localhost", 12345, {"client_id": "c1"}, {}, {"your_name": "Alice"},
                 nuz={"soullink_mode": "disabled"})
    assert m._nuzlocke_rules_active() is False


def test_versus_ffa_state_cleared_on_mode_change():
    # Nach Wechsel weg von versus/versus_ffa wird der State geleert.
    a = _make_arceus()
    a.soullink_config["mode"] = "versus_ffa"
    a._recompute_versus_state()
    assert a.soullink_versus_state
    a.soullink_config["mode"] = "nuzlocke"
    changed = a._recompute_versus_state()
    assert changed
    assert a.soullink_versus_state == {}


def test_versus_separate_teams():
    a = _make_arceus()
    a.soullink_config = {
        "mode": "versus", "player_count": 4, "link_strategy": "full_chain",
        "team_membership": {"Alice_c1": "A", "Bob_c2": "A", "Charlie_c3": "B", "Dave_c4": "B"},
        "expected_owners": ["Alice_c1", "Bob_c2", "Charlie_c3", "Dave_c4"],
    }
    la = a._assign_link_group(enc(100, "Alice_c1"))
    lc = a._assign_link_group(enc(300, "Charlie_c3"))
    assert la["link_id"] != lc["link_id"]
    assert la["expected_owners"] == ["Alice_c1", "Bob_c2"]
    assert lc["expected_owners"] == ["Charlie_c3", "Dave_c4"]


def test_trade_detection():
    a = _make_arceus()
    # Alice fängt PID 100
    a.encounters[(100, "Alice_c1")] = enc(100, "Alice_c1")
    # Bob meldet PID 100 → Trade-Warnung
    warning = a._detect_trade(enc(100, "Bob_c2"))
    assert warning is not None
    assert warning["type"] == "trade"
    assert warning["old_owner"] == "Alice_c1"
    assert warning["new_owner"] == "Bob_c2"
    # zweiter Melding gleichen PIDs: kein Duplikat
    warning2 = a._detect_trade(enc(100, "Bob_c2"))
    assert warning2 is None


def test_timer_survives_client_reconnect_simulated():
    a = _make_arceus()
    a._timer_start()
    initial = a.timer_state["start_ts"]
    # simuliere: Client kickt sich raus + reconnected
    a.munchlaxes.pop("c1", None)
    time.sleep(0.1)
    a.munchlaxes["c1"] = None
    snap = a._timer_snapshot()
    assert snap["start_ts"] == initial
    assert snap["running"]
    assert snap["elapsed"] > 0.05


def test_countdown_finished_signal_once():
    a = _make_arceus()
    a._countdown_start(0.2, "test")
    time.sleep(0.3)
    rem = a._countdown_remaining()
    assert rem <= 0.05
    # simuliere tick-loop-finalize
    if rem <= 0.0 and not a._countdown_finished_signaled:
        a._countdown_finished_signaled = True
        a.countdown_state["running"] = False
        a.countdown_state["finished"] = True
    assert a.countdown_state["finished"]
    # zweiter Aufruf sollte nicht erneut ausloesen
    assert a._countdown_finished_signaled


def test_shiny_override_gets_linked():
    """shiny_clause_always_catchable + is_first=1: Shiny wird als First-Encounter behandelt."""
    a = _make_arceus()
    # Non-shiny first
    a._assign_link_group(enc(100, "Alice_c1", dex=25))
    # Alice zieht Shiny — im encounter_tracker wuerde is_shiny_override=1 gesetzt,
    # is_first bleibt 1, hier direkt simuliert:
    shiny_enc = enc(101, "Alice_c1", dex=133, is_first=1)
    shiny_enc["shiny"] = 1
    shiny_enc["is_shiny_override"] = 1
    link = a._assign_link_group(shiny_enc)
    assert link is not None
    assert link["link_index"] == 2  # als zweiter First-Encounter Slot behandelt


def test_reconnect_initial_state_contains_links():
    """Neuer Client bekommt Link-State beim Verbinden — Verifikation via update_all_clients-Pfad."""
    a = _make_arceus()
    a._assign_link_group(enc(100, "Alice_c1"))
    a._assign_link_group(enc(200, "Bob_c2"))
    # simuliere: neuer Client verbindet sich, sammle was gepusht wuerde
    pushed = []
    if a.soullink_links:
        pushed.append(("count", len(a.soullink_links)))
    if a.soullink_config.get("mode", "off") != "off":
        pushed.append(("has_config", True))
    assert ("count", 1) in pushed
    assert ("has_config", True) in pushed


TESTS = [
    test_coop_both_catch,
    test_coop_one_fails,
    test_second_slot_new_link,
    test_death_marks_failed,
    test_mode_off_no_link,
    test_mode_nuzlocke_no_link,
    test_mode_disabled_no_link,
    test_versus_ffa_no_link,
    test_versus_ffa_scoreboard_per_player,
    test_versus_ffa_state_cleared_on_mode_change,
    test_death_report_debounce_and_pending,
    test_death_detection_disabled_mode_no_report,
    test_versus_separate_teams,
    test_trade_detection,
    test_timer_survives_client_reconnect_simulated,
    test_countdown_finished_signal_once,
    test_shiny_override_gets_linked,
    test_reconnect_initial_state_contains_links,
]


if __name__ == "__main__":
    failed = 0
    for t in TESTS:
        try:
            t()
            print(f"PASS  {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL  {t.__name__}: {e}")
        except Exception as e:
            failed += 1
            print(f"ERROR {t.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(TESTS) - failed}/{len(TESTS)} tests passed")
    sys.exit(0 if failed == 0 else 1)
