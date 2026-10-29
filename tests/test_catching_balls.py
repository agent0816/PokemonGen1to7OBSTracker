"""Nuzlocke-Ballerkennung: Fang-Baelle per Item-ID, Pocket-unabhaengig."""

from backend.bag_decoder import BagItem, catching_ball_ids
from backend.bag_view import is_ball
from backend.classes.pokedex_db import PokedexDB


def _db(tmp_path) -> PokedexDB:
    db = PokedexDB(tmp_path)
    db.connect()
    return db


def test_ball_ids_ohne_falsche_ball_slugs():
    # smoke-ball/light-ball/gs-ball (Gen 2), iron-ball (Gen 4+), PLA-Poke-Ball (1640)
    assert 106 not in catching_ball_ids(22)
    assert 163 not in catching_ball_ids(22)
    assert 115 not in catching_ball_ids(22)
    assert 278 not in catching_ball_ids(41)
    assert 1640 not in catching_ball_ids(71)
    # echte Baelle
    assert 5 in catching_ball_ids(21)      # Gen 2 Pokeball
    assert 4 in catching_ball_ids(11)      # Gen 1 Pokeball
    assert 851 in catching_ball_ids(73)    # Ultraball (Beast Ball)
    # Safari-Ball zaehlt (Randomizer), Park-/Sport-Ball nicht
    assert 8 in catching_ball_ids(11)
    assert 5 in catching_ball_ids(33)
    assert 499 not in catching_ball_ids(44)
    assert 500 not in catching_ball_ids(44)
    assert catching_ball_ids(None) == frozenset()


def test_is_ball_per_id():
    assert is_ball("41", 4) is True
    assert is_ball("41", 278) is False


def test_gen5_ball_in_items_pocket_startet_run(tmp_path):
    db = _db(tmp_path)
    assert db.has_catching_balls("Ash_1", 51) is False
    db.upsert_bag_pocket("Ash_1", 51, "items", [BagItem(id=4, qty=5)])
    assert db.has_catching_balls("Ash_1", 51) is True


def test_gen1_tasche_ball_startet_run(tmp_path):
    db = _db(tmp_path)
    db.upsert_bag_pocket("Ash_1", 11, "tasche", [BagItem(id=4, qty=5)])
    assert db.has_catching_balls("Ash_1", 11) is True


def test_kugelrauch_startet_keinen_run(tmp_path):
    db = _db(tmp_path)
    db.upsert_bag_pocket("Ash_1", 22, "items", [BagItem(id=106, qty=1)])
    assert db.has_catching_balls("Ash_1", 22) is False


def test_run_mode_migration_initialize_tree():
    from initialize_tree import migrate_run_mode_key
    nuz = {"soullink_mode": "coop"}
    assert migrate_run_mode_key(nuz) is True
    assert nuz == {"run_mode": "coop"}
    nuz = {"soullink_mode": "coop", "run_mode": "versus"}
    assert migrate_run_mode_key(nuz) is True
    assert nuz == {"run_mode": "versus"}
    assert migrate_run_mode_key({"run_mode": "nuzlocke"}) is False


def test_run_mode_migration_session_store(tmp_path):
    from backend.session_store import SESSION_FILES, SessionConfigs, load_session_into
    (tmp_path / "nuzlocke.yml").write_text("soullink_mode: disabled\n")
    for name in SESSION_FILES:
        if name != "nuzlocke.yml":
            (tmp_path / name).write_text("{}\n")
    cfg = SessionConfigs(**{attr: {} for attr in SESSION_FILES.values()})
    cfg.nuz["run_mode"] = "coop"  # Rest aus voriger Session
    load_session_into(f"{tmp_path}/", cfg)
    assert cfg.nuz == {"run_mode": "disabled"}
