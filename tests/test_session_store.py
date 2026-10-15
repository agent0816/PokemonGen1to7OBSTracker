"""Tests fuer backend/session_store.py (Session-Datei-Operationen ohne UI).

Deckt: Pfad-Ableitung, Roundtrip aller acht YAMLs, In-place-Update (Referenzen
bleiben erhalten), optionale Dateien alter Sessions, nuz-Stale-Key-Fix,
Anlegen aus Template ohne Template-Mutation, Löschen inkl. runs/, session_list.

Ausführung:
    .venv/py3115/Scripts/python.exe -m pytest tests/test_session_store.py -v
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend import session_store  # noqa: E402
from backend.session_store import SessionConfigs  # noqa: E402


def _configs(tag: str = "a") -> SessionConfigs:
    return SessionConfigs(
        sp={"common_path": f"sprites_{tag}"},
        bh={"port": 43885, "path": f"bh_{tag}.exe"},
        obs={"host": "localhost", "password": ""},
        rem={"client_port": 43886, "start_server": True},
        pl={"your_name": f"Ash_{tag}", "session_game": "Rot", "player_count": 1},
        rnd={"seed": tag},
        ov={"port": 43888},
        nuz={"mode": "solo", "rule_dupes_clause": True, "soullink_preset": "x"},
    )


@pytest.fixture
def config_root(tmp_path: Path) -> str:
    """Struktur wie backend/config/: default/ + session_list.yml daneben."""
    default = tmp_path / "default"
    session_store.write_session_files(f"{default}/", _configs("default"), create=True)
    (tmp_path / "session_list.yml").write_text(yaml.dump([]))
    return f"{default}/"


def test_session_path_ersetzt_default_und_leerzeichen(config_root: str):
    path = session_store.session_path(config_root, "Mein Run")
    assert path.endswith("Mein_Run/")
    assert "default" not in path


def test_roundtrip_alle_acht_dateien(config_root: str):
    for filename in session_store.SESSION_FILES:
        assert Path(f"{config_root}{filename}").exists(), filename
    loaded = session_store.read_session(config_root)
    assert loaded == _configs("default")


def test_load_aktualisiert_in_place_und_behaelt_referenzen(config_root: str):
    live = _configs("live")
    refs = {attr: getattr(live, attr) for attr in session_store.SESSION_FILES.values()}
    session_store.load_session_into(config_root, live)
    for attr, ref in refs.items():
        assert getattr(live, attr) is ref, f"{attr}: Referenz ersetzt"
    assert live.pl["your_name"] == "Ash_default"
    assert live.rnd["seed"] == "default"


def test_optionale_dateien_fehlen_dicts_bleiben(config_root: str):
    for filename in session_store.OPTIONAL_FILES:
        Path(f"{config_root}{filename}").unlink()
    live = _configs("live")
    session_store.load_session_into(config_root, live)
    assert live.rnd == {"seed": "live"}
    assert live.ov == {"port": 43888}
    # Ohne nuzlocke.yml kein Stale-Key-Clear (Bestandsverhalten)
    assert live.nuz["rule_dupes_clause"] is True


def test_nuz_stale_keys_werden_entfernt(config_root: str):
    Path(f"{config_root}nuzlocke.yml").write_text(yaml.dump({"mode": "coop"}))
    live = _configs("live")
    session_store.load_session_into(config_root, live)
    assert live.nuz == {"mode": "coop"}


def test_nuz_stale_keys_auch_bei_leerer_yaml(config_root: str):
    Path(f"{config_root}nuzlocke.yml").write_text("")
    live = _configs("live")
    session_store.load_session_into(config_root, live)
    assert "rule_dupes_clause" not in live.nuz
    assert "soullink_preset" not in live.nuz
    assert live.nuz["mode"] == "solo"


def test_create_session_mutiert_template_nicht(config_root: str):
    template = _configs("tpl")
    new_path = session_store.create_session(config_root, "Run 2", template,
                                            player_name="Misty", session_game="Gold")
    assert template.pl["your_name"] == "Ash_tpl"
    assert template.pl["session_game"] == "Rot"
    created = session_store.read_session(new_path)
    assert created.pl["your_name"] == "Misty"
    assert created.pl["session_game"] == "Gold"
    assert created.nuz == template.nuz


def test_create_session_ueberschreibt_bestehende_nicht(config_root: str):
    session_store.create_session(config_root, "Run", _configs("1"))
    with pytest.raises(FileExistsError):
        session_store.create_session(config_root, "Run", _configs("2"))
    assert session_store.read_session(session_store.session_path(config_root, "Run")).rnd["seed"] == "1"


def test_delete_entfernt_ordner_mit_runs(config_root: str):
    path = session_store.create_session(config_root, "Weg", _configs())
    run_dir = Path(path) / "runs" / "run_001_x" / "p1"
    run_dir.mkdir(parents=True)
    (run_dir / "rom.gba").write_bytes(b"\x00" * 16)
    (Path(path) / "pokemon.db").write_bytes(b"")
    session_store.delete_session_dir(path)
    assert not Path(path).exists()


def test_delete_fehlender_ordner_ist_noop(config_root: str):
    session_store.delete_session_dir(session_store.session_path(config_root, "gibtsnicht"))


def test_save_session_list(config_root: str):
    session_store.save_session_list(config_root, ["A", "B c"])
    list_file = Path(config_root).parent / "session_list.yml"
    assert yaml.safe_load(list_file.read_text()) == ["A", "B c"]
