"""TM-Typ-Sprites fuer Randomizer-Logs deutscher ROMs (Testrunde 2026-11-02)."""
import glob
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.tm_type_resolver import move_type, resolve_tm_hm_sprite


@pytest.mark.parametrize("name,typ", [
    ("SOLARSTRAHL", "grass"),     # Gen 3 ROM-Name
    ("PFUND", "normal"),          # alter Name (heute Klaps)
    ("GIFTBLICK", "normal"),      # Glare, alter Name (heute Schlangenblick)
    ("ROLLENTAUSCH", "psychic"),  # Role Play, alter Name
    ("FUSSKICK", "fighting"),     # ROM ohne ß
    ("Fußkick", "fighting"),      # heutige Schreibweise
    ("KOSMIK-KRAFT", "psychic"),
    ("SCHÄDELWUMME", "normal"),
    ("Flammenwurf", "fire"),      # moves_de.yml (Gen 4+)
    ("Flamethrower", "fire"),     # englisch unveraendert
])
def test_move_type_de(name, typ):
    assert move_type(name) == typ


def test_sprite_slug_aus_rando_mapping():
    assert resolve_tm_hm_sprite(31, "tm22", {22: "SOLARSTRAHL"}) == "tm-grass"
    assert resolve_tm_hm_sprite(31, "tm22", {22: "GIBTSNICHT"}) == "tm22"


LOGS = sorted(glob.glob(str(ROOT / "tools" / "rando_logs" / "*.log")))


@pytest.mark.skipif(not LOGS, reason="tools/rando_logs fehlt (gitignored)")
def test_alle_rando_logs_vollstaendig_aufgeloest():
    from backend.controller.randomizer_log_parser import RandomizerLogParser
    for path in LOGS:
        data = RandomizerLogParser().parse(path)
        names = list(data.tm_moves.values()) + list(data.hm_moves.values())
        assert names, path
        missing = [n for n in names if not move_type(n)]
        assert missing == [], (Path(path).name, missing)


def test_rom_namen_kollidieren_nicht_mit_modernen_namen():
    # ROM-Override gilt global ohne Gen-Kontext: ein ROM-Name darf nie auf eine
    # andere Attacke zeigen als der gleichlautende moderne Name.
    import yaml
    from backend.tm_type_resolver import _norm_de
    data = ROOT / "backend" / "data"
    modern = yaml.safe_load((data / "moves_de.yml").read_text(encoding="utf-8"))
    rom = yaml.safe_load((data / "moves_de_rom.yml").read_text(encoding="utf-8"))
    modern_ids: dict[str, set] = {}
    for mid, name in modern.items():
        modern_ids.setdefault(_norm_de(name), set()).add(int(mid))
    for table in rom.values():
        for name, mid in table.items():
            ids = modern_ids.get(_norm_de(name))
            assert ids is None or int(mid) in ids, (name, mid, ids)
