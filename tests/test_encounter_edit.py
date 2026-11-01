"""Manuelle Encounter-Korrektur: Merge-/Lock-Funktionen (backend/encounter_edit.py)
und Schema v11 in PokedexDB (Sperren, Soft-Delete, Link-/Tod-Persistenz)."""
from backend.classes.pokedex_db import CURRENT_SCHEMA_VERSION, PokedexDB
from backend.encounter_edit import (
    ALL_LOCKABLE, EDITABLE_FIELDS, format_locks, is_locked, keep_locked,
    merge_encounter_edit, new_manual_personality, parse_locks,
)


def _enc(**kw) -> dict:
    base = {
        "personality": 1, "owner": "Ash_1", "edition": 31, "route": 5,
        "dexnr": 25, "lvl": 5, "shiny": 0, "is_first": 1, "is_shiny_override": 0,
        "is_dupes_skip": 0, "has_balls": 1, "method": "wild",
        "outcome": "caught", "timestamp": "t", "female": 0, "form": "",
    }
    base.update(kw)
    return base


# -- reine Funktionen ---------------------------------------------------------

def test_parse_format_locks():
    assert parse_locks(None) == set()
    assert parse_locks("") == set()
    assert parse_locks("outcome, lvl,unbekannt") == {"outcome", "lvl"}
    assert format_locks({"outcome", "dexnr", "quatsch"}) == "dexnr,outcome"


def test_merge_setzt_und_sperrt():
    enc = _enc()
    out = merge_encounter_edit(enc, {"outcome": "not_caught", "lvl": 7, "unbekannt": 1})
    assert enc["outcome"] == "caught"  # Original unveraendert
    assert out["outcome"] == "not_caught" and out["lvl"] == 7
    assert "unbekannt" not in out
    assert parse_locks(out["manual_fields"]) == {"outcome", "lvl"}
    assert is_locked(out, "outcome") and not is_locked(out, "dexnr")


def test_merge_unlock_behaelt_wert():
    out = merge_encounter_edit(_enc(), {"outcome": "dead", "lvl": 9})
    out2 = merge_encounter_edit(out, {}, unlock=["outcome", "nichts"])
    assert out2["outcome"] == "dead"
    assert parse_locks(out2["manual_fields"]) == {"lvl"}


def test_merge_delete_und_undelete():
    out = merge_encounter_edit(_enc(), {}, delete=True)
    assert out["deleted"] == 1 and "deleted" in parse_locks(out["manual_fields"])
    back = merge_encounter_edit(out, {}, delete=False)
    assert back["deleted"] == 0 and "deleted" not in parse_locks(back["manual_fields"])


def test_keep_locked():
    old = merge_encounter_edit(_enc(), {"outcome": "not_caught"}, delete=True)
    new = _enc(outcome="caught", lvl=50)
    out = keep_locked(old, new)
    assert out["outcome"] == "not_caught"
    assert out["deleted"] == 1
    assert out["lvl"] == 50  # ungesperrt -> neuer Wert
    assert out["manual_fields"] == old["manual_fields"]


def test_new_manual_personality_negativ():
    for _ in range(50):
        pv = new_manual_personality()
        assert -(2 ** 31) <= pv <= -1


def test_feldlisten():
    assert set(ALL_LOCKABLE) == set(EDITABLE_FIELDS) | {"link", "deleted"}


# -- DB -----------------------------------------------------------------------

def _db(tmp_path) -> PokedexDB:
    db = PokedexDB(tmp_path)
    db.connect()
    assert db.connection is not None
    return db


def _cols(db, table):
    return {r[1] for r in db.connection.execute(f"PRAGMA table_info({table})")}


def test_v11_spalten_und_tabelle(tmp_path):
    db = _db(tmp_path)
    assert CURRENT_SCHEMA_VERSION >= 11
    assert {"manual_fields", "deleted"} <= _cols(db, "encounters")
    assert {"expected_owners", "manual"} <= _cols(db, "soullink_links")
    assert {"dexnr", "method", "manual"} <= _cols(db, "soullink_link_members")
    assert {"personality", "owner", "partners", "dead", "manual"} <= _cols(
        db, "soullink_deaths")


def test_v11_migration_von_v10(tmp_path):
    db = _db(tmp_path)
    assert db.sync_encounter(_enc())
    conn = db.connection
    for col in ("manual_fields", "deleted"):
        conn.execute(f"ALTER TABLE encounters DROP COLUMN {col}")
    for col in ("expected_owners", "manual"):
        conn.execute(f"ALTER TABLE soullink_links DROP COLUMN {col}")
    for col in ("dexnr", "method", "manual"):
        conn.execute(f"ALTER TABLE soullink_link_members DROP COLUMN {col}")
    conn.execute("DROP TABLE soullink_deaths")
    conn.execute("DELETE FROM schema_version WHERE version >= 11")
    conn.commit()
    db._migrate()
    assert {"manual_fields", "deleted"} <= _cols(db, "encounters")
    assert {"dexnr", "method", "manual"} <= _cols(db, "soullink_link_members")
    enc = db.get_encounter(1, "Ash_1")
    assert enc["deleted"] == 0
    # idempotent
    conn.execute("DELETE FROM schema_version WHERE version >= 11")
    db._migrate()
    versions = [r[0] for r in conn.execute("SELECT version FROM schema_version")]
    assert versions.count(11) == 1


def test_automatik_ignoriert_gesperrtes_outcome(tmp_path):
    db = _db(tmp_path)
    db.sync_encounter(_enc())
    assert db.update_encounter_outcome(1, "Ash_1", "dead") is True
    db.apply_encounter_edit(1, "Ash_1", {"outcome": "caught"})
    assert db.update_encounter_outcome(1, "Ash_1", "not_caught") is False
    assert db.get_encounter(1, "Ash_1")["outcome"] == "caught"
    # manueller Pfad schreibt trotzdem
    assert db.update_encounter_outcome(1, "Ash_1", "dead", automatic=False) is True
    assert db.get_encounter(1, "Ash_1")["outcome"] == "dead"
    # nach Unlock darf die Automatik wieder
    db.apply_encounter_edit(1, "Ash_1", {}, unlock=["outcome"])
    assert db.update_encounter_outcome(1, "Ash_1", "caught") is True


def test_sync_ohne_manual_fields_behaelt_sperren(tmp_path):
    db = _db(tmp_path)
    db.sync_encounter(_enc())
    db.apply_encounter_edit(1, "Ash_1", {"lvl": 9}, delete=True)
    # Payload ohne manual_fields/deleted (Alt-Client)
    assert db.sync_encounter(_enc(lvl=12))
    row = db.get_encounter(1, "Ash_1")
    assert row["manual_fields"] == "deleted,lvl"
    assert row["deleted"] == 1
    # Payload mit expliziten Keys ist maßgeblich
    assert db.sync_encounter(_enc(manual_fields="", deleted=0))
    row = db.get_encounter(1, "Ash_1")
    assert row["manual_fields"] == "" and row["deleted"] == 0


def test_apply_encounter_edit_roundtrip(tmp_path):
    db = _db(tmp_path)
    db.sync_encounter(_enc())
    out = db.apply_encounter_edit(1, "Ash_1", {"dexnr": 133, "female": 1, "form": "-x"})
    assert out["dexnr"] == 133 and out["female"] == 1 and out["form"] == "-x"
    assert out["manual_fields"] == "dexnr,female,form"
    assert db.apply_encounter_edit(99, "Ash_1", {"lvl": 1}) is None
    assert len(db.load_all_encounters()) == 1


def test_deleted_wird_von_abfragen_ignoriert(tmp_path):
    db = _db(tmp_path)
    db.sync_encounter(_enc())
    assert db.has_encounter_on_route("Ash_1", 31, 5)
    assert db.has_any_encounter("Ash_1", 31)
    assert len(db.get_encounters("Ash_1")) == 1
    assert len(db.get_route_status("Ash_1")) == 1
    db.apply_encounter_edit(1, "Ash_1", {}, delete=True)
    assert not db.has_encounter_on_route("Ash_1", 31, 5)
    # Starter-Detection: geloeschte Zeile zaehlt weiter als "hat Encounter"
    assert db.has_any_encounter("Ash_1", 31)
    assert db.get_encounters("Ash_1") == []
    assert db.get_route_status("Ash_1") == []
    assert len(db.load_all_encounters()) == 1  # Zeile bleibt als Sperre

    db.sync_encounter(_enc(personality=2, method="gift", route=7))
    assert db.has_encounter_with_method_at_route("Ash_1", 31, 7, "gift")
    db.apply_encounter_edit(2, "Ash_1", {}, delete=True)
    assert not db.has_encounter_with_method_at_route("Ash_1", 31, 7, "gift")


def _link() -> dict:
    return {
        "link_id": 3, "route": 5, "edition_gen": 3, "link_index": 1,
        "state": "pending", "expected_owners": ["Ash", "Misty"], "manual": 1,
        "members": {
            "Ash": {"personality": 1, "edition": 31, "outcome": "caught",
                    "dexnr": 25, "method": "wild"},
            "Misty": {"personality": -5, "edition": 31, "outcome": "unknown",
                      "dexnr": 7, "method": "gift", "manual": 1},
        },
    }


def test_link_roundtrip(tmp_path):
    db = _db(tmp_path)
    link = _link()
    assert db.save_link(link)
    loaded = db.load_links()
    assert set(loaded) == {3}
    got = loaded[3]
    for key in ("link_id", "route", "edition_gen", "link_index", "state",
                "expected_owners", "manual"):
        assert got[key] == link[key]
    assert got["members"]["Ash"]["dexnr"] == 25
    assert got["members"]["Misty"]["personality"] == -5
    assert got["members"]["Misty"]["method"] == "gift"
    assert got["members"]["Misty"]["manual"] == 1
    # Upsert ersetzt Members komplett
    del link["members"]["Misty"]
    link["state"] = "failed"
    assert db.save_link(link)
    got = db.load_links()[3]
    assert list(got["members"]) == ["Ash"] and got["state"] == "failed"
    assert db.delete_link(3)
    assert db.load_links() == {}
    db.save_link(_link())
    assert db.clear_links()
    assert db.load_links() == {}


def test_death_roundtrip_und_reset(tmp_path):
    db = _db(tmp_path)
    death = {
        "personality": 1, "owner": "Ash", "edition": 31, "cause": "faint",
        "timestamp": 12.5, "partners": [{"owner": "Misty", "personality": -5}],
        "link_id": 3,
    }
    assert db.save_death(death)
    assert db.save_death({"personality": 2, "owner": "Ash", "dead": 0, "manual": 1})
    loaded = db.load_deaths()
    assert set(loaded) == {(1, "Ash"), (2, "Ash")}
    assert loaded[(1, "Ash")]["partners"] == death["partners"]
    assert loaded[(1, "Ash")]["dead"] == 1 and loaded[(1, "Ash")]["link_id"] == 3
    assert loaded[(2, "Ash")]["dead"] == 0 and loaded[(2, "Ash")]["manual"] == 1
    assert db.delete_death(2, "Ash")
    assert set(db.load_deaths()) == {(1, "Ash")}
    assert db.save_death({"personality": 9, "owner": "Ash"})
    assert db.clear_deaths()
    assert db.load_deaths() == {}
    db.save_death(death)
    db.save_link(_link())
    assert db.reset_all_data()
    assert db.load_deaths() == {}
    assert db.load_links() == {}


def test_v12_dead_spalte_und_migration(tmp_path):
    db = _db(tmp_path)
    assert CURRENT_SCHEMA_VERSION == 12
    assert "dead" in _cols(db, "encounters")
    assert db.sync_encounter(_enc())
    conn = db.connection
    conn.execute("ALTER TABLE encounters DROP COLUMN dead")
    conn.execute("DELETE FROM schema_version WHERE version >= 12")
    conn.commit()
    db._migrate()
    assert db.get_encounter(1, "Ash_1")["dead"] == 0


def test_tot_klebt_und_sperre_blockt_automatik(tmp_path):
    db = _db(tmp_path)
    db.sync_encounter(_enc())
    assert db.mark_encounter_dead(1, "Ash_1") is True
    assert db.mark_encounter_dead(1, "Ash_1") is False  # schon tot
    # Remote-Sync ohne dead-Key behaelt den Tod
    db.sync_encounter(_enc())
    assert db.get_encounter(1, "Ash_1")["dead"] == 1
    # manuell lebendig → Sperre, Automatik setzt nicht erneut
    db.apply_encounter_edit(1, "Ash_1", {"dead": 0})
    assert db.get_encounter(1, "Ash_1")["dead"] == 0
    assert db.mark_encounter_dead(1, "Ash_1") is False
    assert db.mark_encounter_dead(1, "Ash_1", automatic=False) is True


def test_sync_ohne_sperre_belebt_nicht_wieder(tmp_path):
    db = _db(tmp_path)
    db.sync_encounter(_enc())
    db.mark_encounter_dead(1, "Ash_1")
    db.sync_encounter(dict(_enc(), dead=0, manual_fields=""))
    assert db.get_encounter(1, "Ash_1")["dead"] == 1
    # mit Sperre (manuell lebendig vom Server) darf es zurueck
    db.sync_encounter(dict(_enc(), dead=0, manual_fields="dead"))
    assert db.get_encounter(1, "Ash_1")["dead"] == 0
