"""Tests fuer SnapshotManager und Randomizer-Run-Remap.

Deckt: create/list/restore/delete, BizHawk-Save-Pfad-Resolver (5 Fallbacks),
Slot+Generation-Auflösung, Run-basierten SaveRAM-Remap, safe_filename,
_system_from_rom. Enthält Integration-Test gegen echte test_bg-Session-
Struktur (kopiert die relevanten Meta-Files nach tmp_path, damit keine
Session-Daten verändert werden).

Läuft ohne echte BizHawk-Instanz — verwendet Fake-Pfade + Fake-Munchlax.

Ausführung:
    .venv/py3115/Scripts/python.exe -m pytest tests/test_snapshot_manager.py -v
"""
from __future__ import annotations

import shutil
import sys
import sqlite3
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.snapshot_manager import (  # noqa: E402
    SnapshotManager,
    _safe_filename,
    _system_from_rom,
)
from backend.controller.run_manager import RunManager  # noqa: E402


# ---------- Fixtures ----------

@pytest.fixture(autouse=True)
def _ensure_logs_dir(tmp_path: Path, monkeypatch):
    """SnapshotManager/RunManager nutzen `get_logger('./logs/...')` mit
    RotatingFileHandler. Ohne existierenden `logs/`-Ordner unter cwd knallt
    der Handler beim ersten Log-Aufruf. Jede Test-Session cwd → tmp_path
    und legt logs/ an."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "logs").mkdir(exist_ok=True)


class FakeMunchlax:
    """Minimales Munchlax-Stub — deckt nur die Attribute, die SnapshotManager
    tatsaechlich anfasst."""
    def __init__(self, pl=None, editions=None, badges=None, player_names=None):
        self.pl = pl or {"player_count": 1, "remote_1": False}
        self.editions = editions or {1: 31}  # 31 = Blattgruen (Gen 3)
        self.badges = badges or {}
        self.player_names = player_names or {1: "test1"}
        self.citra = None  # kein Citra-Zugriff im BizHawk-Pfad

    def close_pokedex_db(self):
        pass


@pytest.fixture
def session_dir(tmp_path: Path) -> Path:
    """Isolierte Session mit leerer pokemon.db + nuzlocke.yml."""
    sess = tmp_path / "session_x"
    sess.mkdir()
    # leere DB anlegen (SQLite-Header)
    con = sqlite3.connect(sess / "pokemon.db")
    con.execute("CREATE TABLE marker (v TEXT)")
    con.execute("INSERT INTO marker VALUES ('original')")
    con.commit()
    con.close()
    (sess / "nuzlocke.yml").write_text("enabled: true\n", encoding="utf-8")
    return sess


@pytest.fixture
def bh_env(tmp_path: Path) -> dict:
    """Fake BizHawk-Ordner mit GBA/SaveRAM-Struktur + Dummy-ROM + SaveRAM."""
    bh_dir = tmp_path / "Bizhawk"
    (bh_dir / "GBA" / "SaveRAM").mkdir(parents=True)
    rom = tmp_path / "roms" / "Pokemon Blattgruen (D).gba"
    rom.parent.mkdir(parents=True)
    rom.write_bytes(b"\x00" * 32)
    save = bh_dir / "GBA" / "SaveRAM" / "Pokemon Blattgruen (D).SaveRAM"
    save.write_bytes(b"SAVE-ORIGINAL")
    return {
        "bh_dir": bh_dir,
        "rom": rom,
        "save": save,
        "bh_config": {
            "path": str(bh_dir / "EmuHawk.exe"),  # .exe → parent wird bh_dir
            "rom_path": str(rom),
        },
    }


# ---------- Reine Helfer ----------

def test_safe_filename_strips_special():
    assert _safe_filename("hallo welt/?!") == "hallo_welt"
    assert _safe_filename("") == "snap"
    assert _safe_filename("____") == "snap"
    assert _safe_filename("abc-123_x") == "abc-123_x"


def test_system_from_rom_extensions():
    assert _system_from_rom(Path("x.gba")) == "GBA"
    assert _system_from_rom(Path("X.NDS")) == "NDS"
    assert _system_from_rom(Path("z.z64")) == "N64"
    assert _system_from_rom(Path("y.unknown")) is None
    assert _system_from_rom(None) is None


# ---------- backup_root ----------

def test_backup_root_default(session_dir: Path, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    sm = SnapshotManager(session_dir, "sess_a")
    assert sm.backup_root == tmp_path / "backup-saves" / "sess_a"
    assert sm.backup_root.exists()


def test_backup_root_override_from_nuzlocke(session_dir: Path, tmp_path):
    override = tmp_path / "custom_backup"
    (session_dir / "nuzlocke.yml").write_text(
        f"snapshot_backup_path: {override.as_posix()}\n", encoding="utf-8"
    )
    sm = SnapshotManager(session_dir, "sess_a")
    assert sm.backup_root == override
    assert sm.backup_root.exists()


# ---------- BizHawk-Save-Pfad-Resolver (5 Fallbacks) ----------

def test_resolve_bh_save_explicit_save_path(session_dir, bh_env, tmp_path):
    explicit = tmp_path / "manual.SaveRAM"
    explicit.write_bytes(b"MANUAL")
    cfg = dict(bh_env["bh_config"], save_path=str(explicit))
    sm = SnapshotManager(session_dir, "s", bh_config=cfg)
    assert sm._resolve_bizhawk_save_path() == explicit


def test_resolve_bh_save_standard_layout(session_dir, bh_env):
    sm = SnapshotManager(session_dir, "s", bh_config=bh_env["bh_config"])
    assert sm._resolve_bizhawk_save_path() == bh_env["save"]


def test_resolve_bh_save_save_dir(session_dir, bh_env, tmp_path):
    # standard-layout SaveRAM wegloeschen, save_dir setzen
    bh_env["save"].unlink()
    alt_dir = tmp_path / "alt_saves"
    alt_dir.mkdir()
    save = alt_dir / "Pokemon Blattgruen (D).SaveRAM"
    save.write_bytes(b"ALT")
    cfg = dict(bh_env["bh_config"], save_dir=str(alt_dir))
    sm = SnapshotManager(session_dir, "s", bh_config=cfg)
    assert sm._resolve_bizhawk_save_path() == save


def test_resolve_bh_save_next_to_rom(session_dir, bh_env):
    bh_env["save"].unlink()
    save = bh_env["rom"].with_suffix(".SaveRAM")
    save.write_bytes(b"NEXT-TO-ROM")
    sm = SnapshotManager(session_dir, "s", bh_config=bh_env["bh_config"])
    assert sm._resolve_bizhawk_save_path() == save


def test_resolve_bh_save_saveram_subfolder_next_to_rom(session_dir, bh_env):
    bh_env["save"].unlink()
    sub = bh_env["rom"].parent / "SaveRAM"
    sub.mkdir()
    save = sub / f"{bh_env['rom'].stem}.SaveRAM"
    save.write_bytes(b"SUB")
    sm = SnapshotManager(session_dir, "s", bh_config=bh_env["bh_config"])
    assert sm._resolve_bizhawk_save_path() == save


def test_resolve_bh_save_none_when_nothing_matches(session_dir, tmp_path):
    # Nur EmuHawk-Pfad ohne rom_path — kein Save auffindbar (aktuelles test_bg-Setup!)
    cfg = {"path": str(tmp_path / "Bizhawk" / "EmuHawk.exe")}
    sm = SnapshotManager(session_dir, "s", bh_config=cfg)
    assert sm._resolve_bizhawk_save_path() is None


def test_bizhawk_working_dir_from_exe(session_dir, bh_env):
    sm = SnapshotManager(session_dir, "s", bh_config=bh_env["bh_config"])
    assert sm._bizhawk_working_dir() == bh_env["bh_dir"]


def test_bizhawk_working_dir_from_bh_dir_key(session_dir, tmp_path):
    bh_dir = tmp_path / "BH"
    bh_dir.mkdir()
    sm = SnapshotManager(session_dir, "s", bh_config={"bh_dir": str(bh_dir)})
    assert sm._bizhawk_working_dir() == bh_dir


# ---------- Slot + Generation ----------

def test_local_slot_and_gen_single_local(session_dir, bh_env):
    mun = FakeMunchlax(pl={"player_count": 1, "remote_1": False},
                       editions={1: 31})
    sm = SnapshotManager(session_dir, "s", bh_config=bh_env["bh_config"], munchlax=mun)
    slot, gen = sm._resolve_local_slot_and_gen()
    assert (slot, gen) == (1, 3)


def test_local_slot_skips_remote(session_dir, bh_env):
    mun = FakeMunchlax(
        pl={"player_count": 3, "remote_1": True, "remote_2": True, "remote_3": False},
        editions={3: 52},  # Gen 5
    )
    sm = SnapshotManager(session_dir, "s", bh_config=bh_env["bh_config"], munchlax=mun)
    slot, gen = sm._resolve_local_slot_and_gen()
    assert (slot, gen) == (3, 5)


def test_local_slot_no_munchlax(session_dir, bh_env):
    sm = SnapshotManager(session_dir, "s", bh_config=bh_env["bh_config"])
    assert sm._resolve_local_slot_and_gen() == (None, None)


def test_local_slot_all_remote(session_dir, bh_env):
    mun = FakeMunchlax(pl={"player_count": 2, "remote_1": True, "remote_2": True})
    sm = SnapshotManager(session_dir, "s", bh_config=bh_env["bh_config"], munchlax=mun)
    assert sm._resolve_local_slot_and_gen() == (None, None)


# ---------- create ----------

def test_create_writes_db_and_meta(session_dir, bh_env, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    mun = FakeMunchlax(editions={1: 31}, badges={1: [True, False]},
                       player_names={1: "test1"})
    sm = SnapshotManager(session_dir, "sess_a",
                          bh_config=bh_env["bh_config"], munchlax=mun)
    snap_id = sm.create(label="unit")
    assert snap_id and snap_id.startswith("unit_")
    snap_dir = sm.backup_root / snap_id
    assert (snap_dir / "pokemon.db").exists()
    assert (snap_dir / "snapshot.yml").exists()
    meta = yaml.safe_load((snap_dir / "snapshot.yml").read_text(encoding="utf-8"))
    assert meta["label"] == "unit"
    assert meta["session"] == "sess_a"
    assert meta["db_backup"] == "pokemon.db"
    assert meta["errors"] == []
    # Team-Snapshot uebernommen
    assert meta["team_snapshot"]["player_names"]["1"] == "test1"


def test_create_captures_bh_saveram(session_dir, bh_env, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    mun = FakeMunchlax(editions={1: 31})
    sm = SnapshotManager(session_dir, "sess_a",
                          bh_config=bh_env["bh_config"], munchlax=mun)
    snap_id = sm.create(label="withsave")
    snap_dir = sm.backup_root / snap_id
    meta = yaml.safe_load((snap_dir / "snapshot.yml").read_text(encoding="utf-8"))
    assert len(meta["save_backups"]) == 1
    entry = meta["save_backups"][0]
    assert entry["type"] == "bizhawk_saveram"
    assert entry["rom_basename"] == "Pokemon Blattgruen (D)"
    assert entry["player_slot"] == 1
    assert entry["generation"] == 3
    # Backup-Datei liegt tatsaechlich vor + hat Original-Inhalt
    assert (snap_dir / entry["backup_file"]).read_bytes() == b"SAVE-ORIGINAL"


def test_create_no_save_when_no_candidate_at_all(session_dir, tmp_path, monkeypatch):
    """Weder aktiver Run noch bh_config.rom_path noch rnd.rom_path → nur DB."""
    monkeypatch.chdir(tmp_path)
    cfg = {"path": str(tmp_path / "Bizhawk" / "EmuHawk.exe")}
    mun = FakeMunchlax(editions={1: 31})
    sm = SnapshotManager(session_dir, "sess_a", bh_config=cfg, munchlax=mun)
    snap_id = sm.create(label="nosave")
    meta = yaml.safe_load((sm.backup_root / snap_id / "snapshot.yml").read_text(encoding="utf-8"))
    assert meta["save_backups"] == []
    assert meta["db_backup"] == "pokemon.db"


def test_create_finds_saveram_via_active_run(session_dir, tmp_path, monkeypatch):
    """Fix-Regression: bh_config hat KEIN rom_path (test_bg-Realitaet), aber
    aktiver Randomizer-Run existiert → SaveRAM wird ueber Run-ROM gefunden."""
    monkeypatch.chdir(tmp_path)
    bh_dir = tmp_path / "Bizhawk"
    (bh_dir / "GBA" / "SaveRAM").mkdir(parents=True)
    cfg = {"path": str(bh_dir / "EmuHawk.exe")}  # bewusst KEIN rom_path

    # Original-ROM als Randomizer-Input + Run anlegen
    input_rom = tmp_path / "input.gba"
    input_rom.write_bytes(b"\x00" * 32)
    run = _make_run(session_dir, input_rom, slots=(1,))
    run_rom = Path(run["rom_targets"][0]["rom_path"])
    run_rom.write_bytes(b"\x00" * 32)  # Randomizer-JAR-Simulation
    # SaveRAM unter Run-ROM-Basename (BH-Standard)
    (bh_dir / "GBA" / "SaveRAM" / f"{run_rom.stem}.SaveRAM").write_bytes(b"RUN-SAVE")

    mun = FakeMunchlax(editions={1: 31})
    sm = SnapshotManager(session_dir, "sess_a", bh_config=cfg, munchlax=mun)
    snap_id = sm.create(label="viarun")
    meta = yaml.safe_load((sm.backup_root / snap_id / "snapshot.yml").read_text(encoding="utf-8"))
    assert len(meta["save_backups"]) == 1
    assert meta["save_backups"][0]["rom_basename"] == run_rom.stem
    backup_file = sm.backup_root / snap_id / meta["save_backups"][0]["backup_file"]
    assert backup_file.read_bytes() == b"RUN-SAVE"


def test_create_finds_saveram_via_rnd_fallback(session_dir, tmp_path, monkeypatch):
    """Kein aktiver Run, kein bh_config.rom_path, aber rnd.rom_path gesetzt →
    Original-Input-ROM wird als Kandidat genutzt."""
    monkeypatch.chdir(tmp_path)
    bh_dir = tmp_path / "Bizhawk"
    (bh_dir / "GBA" / "SaveRAM").mkdir(parents=True)
    rom = tmp_path / "OriginalRnd.gba"
    rom.write_bytes(b"\x00" * 32)
    (bh_dir / "GBA" / "SaveRAM" / "OriginalRnd.SaveRAM").write_bytes(b"RND-SAVE")

    cfg = {"path": str(bh_dir / "EmuHawk.exe")}
    rnd = {"rom_path": str(rom)}
    mun = FakeMunchlax(editions={1: 31})
    sm = SnapshotManager(session_dir, "sess_a", bh_config=cfg, munchlax=mun, rnd=rnd)
    snap_id = sm.create(label="rnd")
    meta = yaml.safe_load((sm.backup_root / snap_id / "snapshot.yml").read_text(encoding="utf-8"))
    assert len(meta["save_backups"]) == 1
    assert meta["save_backups"][0]["rom_basename"] == "OriginalRnd"


def test_candidate_roms_priority_active_run_first(session_dir, tmp_path, monkeypatch):
    """Aktive Run-ROM hat Vorrang vor bh_config.rom_path und rnd.rom_path."""
    monkeypatch.chdir(tmp_path)
    input_rom = tmp_path / "input.gba"
    input_rom.write_bytes(b"\x00" * 32)
    bh_rom = tmp_path / "bh.gba"
    bh_rom.write_bytes(b"\x00" * 32)
    rnd_rom = tmp_path / "rnd.gba"
    rnd_rom.write_bytes(b"\x00" * 32)
    run = _make_run(session_dir, input_rom, slots=(1,))
    run_rom = Path(run["rom_targets"][0]["rom_path"])
    run_rom.write_bytes(b"\x00" * 32)

    mun = FakeMunchlax(editions={1: 31})
    sm = SnapshotManager(session_dir, "sess_a",
                          bh_config={"rom_path": str(bh_rom)},
                          munchlax=mun, rnd={"rom_path": str(rnd_rom)})
    candidates = sm._candidate_roms()
    assert candidates[0] == run_rom
    assert candidates[1] == bh_rom
    assert candidates[2] == rnd_rom


def test_active_run_rom_missing_file_falls_through(session_dir, tmp_path, monkeypatch):
    """Aktiver Run existiert, aber ROM-Datei fehlt (Randomizer nicht gelaufen)
    → aktive Run-ROM wird nicht als Kandidat gemeldet."""
    monkeypatch.chdir(tmp_path)
    input_rom = tmp_path / "input.gba"
    input_rom.write_bytes(b"\x00" * 32)
    _make_run(session_dir, input_rom, slots=(1,))  # run_rom nicht angelegt
    mun = FakeMunchlax(editions={1: 31})
    sm = SnapshotManager(session_dir, "sess_a",
                          bh_config={"rom_path": str(input_rom)}, munchlax=mun)
    candidates = sm._candidate_roms()
    assert candidates == [input_rom]


# ---------- list + delete ----------

def test_list_returns_newest_first(session_dir, bh_env, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    sm = SnapshotManager(session_dir, "sess_a", bh_config=bh_env["bh_config"])
    a = sm.create(label="a")
    b = sm.create(label="b")
    entries = sm.list()
    assert len(entries) == 2
    # sortiert absteigend nach Ordner-Namen → jüngeres label vorne
    ids = [e["id"] for e in entries]
    assert a in ids and b in ids


def test_delete_removes_dir(session_dir, bh_env, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    sm = SnapshotManager(session_dir, "sess_a", bh_config=bh_env["bh_config"])
    snap_id = sm.create(label="del")
    assert (sm.backup_root / snap_id).exists()
    assert sm.delete(snap_id) is True
    assert not (sm.backup_root / snap_id).exists()
    assert sm.delete("nonexistent") is False


# ---------- restore roundtrip ohne Randomizer-Run ----------

def test_restore_roundtrip(session_dir, bh_env, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    mun = FakeMunchlax(editions={1: 31})
    sm = SnapshotManager(session_dir, "sess_a",
                          bh_config=bh_env["bh_config"], munchlax=mun)
    snap_id = sm.create(label="rt")

    # DB und SaveRAM manipulieren
    con = sqlite3.connect(session_dir / "pokemon.db")
    con.execute("UPDATE marker SET v='modified'")
    con.commit()
    con.close()
    bh_env["save"].write_bytes(b"SAVE-MODIFIED")

    assert sm.restore(snap_id) is True

    # DB zurueckgesetzt
    con = sqlite3.connect(session_dir / "pokemon.db")
    row = con.execute("SELECT v FROM marker").fetchone()
    con.close()
    assert row[0] == "original"
    # SaveRAM zurueckgesetzt am original_path (kein aktiver Run → kein Remap)
    assert bh_env["save"].read_bytes() == b"SAVE-ORIGINAL"


def test_restore_missing_snapshot_returns_false(session_dir, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    sm = SnapshotManager(session_dir, "sess_a")
    assert sm.restore("does_not_exist") is False


# ---------- Randomizer-Run-Remap ----------

def _make_run(session_path: Path, rom_source: Path, slots=(1,)) -> dict:
    rm = RunManager(session_path)
    return rm.create_run(rom_source, randomizer_settings_path=None,
                          player_slots=list(slots))


def test_remap_to_active_run_rom_gen3(session_dir, bh_env, tmp_path, monkeypatch):
    """Restore mit aktivem Run → SaveRAM landet unter neuem ROM-Basename."""
    monkeypatch.chdir(tmp_path)
    mun = FakeMunchlax(editions={1: 31})
    sm = SnapshotManager(session_dir, "sess_a",
                          bh_config=bh_env["bh_config"], munchlax=mun)
    snap_id = sm.create(label="pre-run")

    # Neuen Randomizer-Run "simulieren": rom.gba als Ziel-ROM
    run = _make_run(session_dir, bh_env["rom"], slots=(1,))
    # Ziel-ROM-Datei erzeugen (RunManager legt nur Ordner an — ROM-Datei
    # normalerweise vom Randomizer-JAR geschrieben). Hier Fake:
    target_rom = Path(run["rom_targets"][0]["rom_path"])
    target_rom.write_bytes(b"\x00" * 32)

    # Restore laufen lassen
    assert sm.restore(snap_id) is True

    # Neuer Save-Pfad: <bh_dir>/GBA/SaveRAM/<new-basename>.SaveRAM
    expected = bh_env["bh_dir"] / "GBA" / "SaveRAM" / f"{target_rom.stem}.SaveRAM"
    assert expected.exists()
    assert expected.read_bytes() == b"SAVE-ORIGINAL"


def test_remap_skips_gen6_gen7(session_dir, bh_env, tmp_path, monkeypatch):
    """Nur Gen 1-5 werden remapped — Gen 6/7 fallen auf original_path."""
    monkeypatch.chdir(tmp_path)
    mun = FakeMunchlax(editions={1: 61})  # Gen 6
    sm = SnapshotManager(session_dir, "sess_a",
                          bh_config=bh_env["bh_config"], munchlax=mun)
    snap_id = sm.create(label="gen6")

    # Aktiven Run anlegen (theoretisch waere Remap moeglich)
    run = _make_run(session_dir, bh_env["rom"], slots=(1,))
    Path(run["rom_targets"][0]["rom_path"]).write_bytes(b"\x00" * 32)

    bh_env["save"].write_bytes(b"CHANGED")
    assert sm.restore(snap_id) is True
    # original_path bekommt Save zurueck, kein Remap
    assert bh_env["save"].read_bytes() == b"SAVE-ORIGINAL"


def test_remap_no_active_run_uses_original_path(session_dir, bh_env, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    mun = FakeMunchlax(editions={1: 31})
    sm = SnapshotManager(session_dir, "sess_a",
                          bh_config=bh_env["bh_config"], munchlax=mun)
    snap_id = sm.create(label="orig")
    bh_env["save"].write_bytes(b"CHANGED")
    assert sm.restore(snap_id) is True
    assert bh_env["save"].read_bytes() == b"SAVE-ORIGINAL"


def test_resolve_target_saveram_path_returns_none_when_slot_missing(
        session_dir, bh_env, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    mun = FakeMunchlax(editions={1: 31})
    sm = SnapshotManager(session_dir, "sess_a",
                          bh_config=bh_env["bh_config"], munchlax=mun)
    _make_run(session_dir, bh_env["rom"], slots=(1,))
    entry = {"type": "bizhawk_saveram", "generation": 3, "player_slot": 99}
    assert sm._resolve_target_saveram_path(entry) is None


# ---------- Integration: test_bg-Realstruktur ----------

TEST_BG = Path(__file__).resolve().parents[1] / "backend" / "config" / "test_bg"


def _copy_session_meta_only(src: Path, dst: Path) -> None:
    """Kopiert Session-YAMLs + runs/-Meta (ohne pokemon.db und ohne rom.gba)
    in tmp — Snapshot-Tests sollen keine echten Session-Files anfassen."""
    dst.mkdir(parents=True, exist_ok=True)
    for yml in src.glob("*.yml"):
        shutil.copy2(yml, dst / yml.name)
    runs_src = src / "runs"
    if not runs_src.exists():
        return
    runs_dst = dst / "runs"
    runs_dst.mkdir()
    for entry in runs_src.iterdir():
        if entry.is_file():
            shutil.copy2(entry, runs_dst / entry.name)
        elif entry.is_dir():
            (runs_dst / entry.name).mkdir()
            for sub in entry.iterdir():
                if sub.is_file():
                    shutil.copy2(sub, runs_dst / entry.name / sub.name)
                elif sub.is_dir():
                    (runs_dst / entry.name / sub.name).mkdir()
                    for f in sub.iterdir():
                        if f.is_file():
                            # rom.gba nicht mitkopieren (gross, unnoetig)
                            if f.name == "rom.gba":
                                (runs_dst / entry.name / sub.name / f.name).write_bytes(b"\x00" * 16)
                            else:
                                shutil.copy2(f, runs_dst / entry.name / sub.name / f.name)


@pytest.mark.skipif(not TEST_BG.exists(),
                     reason="Session test_bg nicht vorhanden")
def test_integration_test_bg_active_run_remap(tmp_path, monkeypatch):
    """End-to-end mit echter test_bg-Struktur: nach Create+Restore mit aktivem
    Run soll SaveRAM auf p1/rom.gba-Basename gemappt werden."""
    monkeypatch.chdir(tmp_path)
    sess = tmp_path / "test_bg"
    _copy_session_meta_only(TEST_BG, sess)

    # Fake pokemon.db + fake BH-Struktur (rom_path zeigt auf run-ROM,
    # damit _resolve_bizhawk_save_path den Save findet).
    con = sqlite3.connect(sess / "pokemon.db")
    con.execute("CREATE TABLE t (v INTEGER)")
    con.commit()
    con.close()

    bh_dir = tmp_path / "Bizhawk"
    (bh_dir / "GBA" / "SaveRAM").mkdir(parents=True)
    # Original-ROM (rom_path in bh_config) + SaveRAM
    orig_rom = tmp_path / "roms" / "OriginalBG.gba"
    orig_rom.parent.mkdir(parents=True)
    orig_rom.write_bytes(b"\x00" * 32)
    orig_save = bh_dir / "GBA" / "SaveRAM" / "OriginalBG.SaveRAM"
    orig_save.write_bytes(b"BG-SAVE")

    bh_config = {
        "path": str(bh_dir / "EmuHawk.exe"),
        "rom_path": str(orig_rom),
    }
    mun = FakeMunchlax(
        pl={"player_count": 1, "remote_1": False},
        editions={1: 31},
        player_names={1: "test1"},
    )
    sm = SnapshotManager(sess, "test_bg", bh_config=bh_config, munchlax=mun)
    snap_id = sm.create(label="int")
    assert snap_id

    # Aktiver Run in test_bg zeigt auf p1/rom.gba — RunManager liest den
    assert sm.restore(snap_id) is True

    # ROM.gba-basename = "rom" → Save an <bh>/GBA/SaveRAM/rom.SaveRAM
    remapped = bh_dir / "GBA" / "SaveRAM" / "rom.SaveRAM"
    assert remapped.exists(), f"Remap fehlt: {remapped}"
    assert remapped.read_bytes() == b"BG-SAVE"


@pytest.mark.skipif(not TEST_BG.exists(),
                     reason="Session test_bg nicht vorhanden")
def test_integration_test_bg_realcase_no_bh_rom_path(tmp_path, monkeypatch):
    """Reproduziert echten test_bg-Zustand: bh_config hat NUR path (EmuHawk.exe),
    KEIN rom_path. Fix soll SaveRAM ueber aktive Run-ROM auffinden."""
    monkeypatch.chdir(tmp_path)
    sess = tmp_path / "test_bg"
    _copy_session_meta_only(TEST_BG, sess)

    con = sqlite3.connect(sess / "pokemon.db")
    con.execute("CREATE TABLE t (v INTEGER)")
    con.commit()
    con.close()

    # BH-Struktur: SaveRAM unter Run-ROM-Basename ("rom") — Kopie der
    # ursprünglichen SaveRAM auf die vom Randomizer erzeugte ROM.
    bh_dir = tmp_path / "Bizhawk"
    (bh_dir / "GBA" / "SaveRAM").mkdir(parents=True)
    (bh_dir / "GBA" / "SaveRAM" / "rom.SaveRAM").write_bytes(b"BG-RUN-SAVE")

    # bh_config wie in echtem test_bg — ohne rom_path
    bh_config = {"path": str(bh_dir / "EmuHawk.exe")}
    mun = FakeMunchlax(
        pl={"player_count": 1, "remote_1": False},
        editions={1: 31},
        player_names={1: "test1"},
    )
    sm = SnapshotManager(sess, "test_bg", bh_config=bh_config, munchlax=mun)
    snap_id = sm.create(label="real")
    assert snap_id

    meta = yaml.safe_load((sm.backup_root / snap_id / "snapshot.yml").read_text(encoding="utf-8"))
    assert len(meta["save_backups"]) == 1, "SaveRAM sollte gefunden werden"
    assert meta["save_backups"][0]["rom_basename"] == "rom"
    backup = sm.backup_root / snap_id / meta["save_backups"][0]["backup_file"]
    assert backup.read_bytes() == b"BG-RUN-SAVE"


@pytest.mark.skipif(not TEST_BG.exists(),
                     reason="Session test_bg nicht vorhanden")
def test_integration_test_bg_run_manager_reads_active():
    """Sanity: RunManager kann test_bg's aktiven Run lesen."""
    rm = RunManager(str(TEST_BG))
    active = rm.get_active_run()
    assert active is not None
    assert active["run_id"] == "run_001_20260728T143320Z"
    paths = rm.get_run_paths(active["run_id"])
    assert 1 in paths["roms"]
    assert paths["roms"][1]["rom"].endswith("rom.gba")
