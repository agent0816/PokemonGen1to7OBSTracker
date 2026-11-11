"""Config-Backup vor Update (Testrunde 2026-11-10)."""
import sqlite3
import zipfile

import pytest

from backend import config_backup as cb


def _make_config(base):
    cfg = base / "config"
    sess = cfg / "default"
    run_slot = sess / "runs" / "run_001_x" / "p1"
    run_slot.mkdir(parents=True)
    (cfg / "app_state.yml").write_text("ui: md\n", encoding="utf-8")
    (sess / "player.yml").write_text("player_count: 2\n", encoding="utf-8")
    (run_slot / "randomizer.log").write_text("Random Seed: 123\n", encoding="utf-8")
    (run_slot / "rom.nds").write_bytes(b"\0" * 1024)
    # Offene DB mit Daten — VACUUM INTO muss trotzdem konsistent kopieren
    conn = sqlite3.connect(sess / "pokemon.db")
    conn.execute("CREATE TABLE t (v INTEGER)")
    conn.execute("INSERT INTO t VALUES (42)")
    conn.commit()
    return cfg, conn


def test_backup_enthaelt_yamls_und_db_ohne_rom(tmp_path):
    cfg, conn = _make_config(tmp_path)
    try:
        target = cb.create_config_backup(cfg, tmp_path / "bak", keep=5)
    finally:
        conn.close()
    assert target.name.startswith("config_") and target.suffix == ".zip"
    with zipfile.ZipFile(target) as zf:
        names = set(zf.namelist())
        assert "config/app_state.yml" in names
        assert "config/default/player.yml" in names
        assert "config/default/runs/run_001_x/p1/randomizer.log" in names
        assert not any(n.endswith("rom.nds") for n in names)
        zf.extract("config/default/pokemon.db", tmp_path / "out")
    db = sqlite3.connect(tmp_path / "out" / "config" / "default" / "pokemon.db")
    try:
        assert db.execute("SELECT v FROM t").fetchone() == (42,)
    finally:
        db.close()
    assert not list((tmp_path / "bak").glob("*.partial"))


def test_retention_behaelt_neueste(tmp_path):
    bak = tmp_path / "bak"
    bak.mkdir()
    for i in range(7):
        (bak / f"config_2026010{i}_000000.zip").write_bytes(b"x")
    (bak / "fremd.zip").write_bytes(b"x")
    removed = cb.prune_backups(bak, keep=3)
    assert len(removed) == 4
    left = sorted(p.name for p in bak.glob("config_*.zip"))
    assert left == ["config_20260104_000000.zip", "config_20260105_000000.zip",
                    "config_20260106_000000.zip"]
    assert (bak / "fremd.zip").exists()


def test_fehlender_config_ordner_wirft(tmp_path):
    with pytest.raises(FileNotFoundError):
        cb.create_config_backup(tmp_path / "gibtsnicht", tmp_path / "bak")


def test_fehler_hinterlaesst_kein_partial(tmp_path, monkeypatch):
    cfg, conn = _make_config(tmp_path)
    conn.close()
    monkeypatch.setattr(cb, "_snapshot_sqlite", lambda src, dst: False)
    with pytest.raises(OSError):
        cb.create_config_backup(cfg, tmp_path / "bak")
    assert list((tmp_path / "bak").iterdir()) == []
