"""Friedhof-Ansicht: Filter, Soullink-Gruppen, Texte, CSV."""
import csv

from backend import graveyard_view as gv

SPECIES = {25: "Pikachu", 280: "Trasla", 0: "Ei"}


def _g(i, owner="web", ts=10, link=None, **kw):
    d = {"id": i, "session": "Projekt A", "run_id": "run_001", "personality": i,
         "owner_name": owner, "ts": ts, "link_id": link, "edition": 32,
         "dexnr": 25, "partners": []}
    d.update(kw)
    return d


def test_filter_und_optionen():
    graves = [_g(1, "web"), _g(2, "Steffen", edition=34), _g(3, "web", edition=34)]
    assert [g["id"] for g in gv.filter_graves(graves, owner="web")] == [1, 3]
    assert [g["id"] for g in gv.filter_graves(graves, edition=34)] == [2, 3]
    assert gv.filter_options(graves) == {"owners": ["Steffen", "web"], "editions": [32, 34]}


def test_soullink_gruppen_in_todesreihenfolge():
    graves = [_g(1, ts=30), _g(2, "web", ts=10, link=5), _g(3, "Steffen", ts=12, link=5,
                                                               cause="soullink_partner")]
    groups = gv.group_graves(graves)
    assert [[g["id"] for g in grp["graves"]] for grp in groups] == [[2, 3], [1]]
    assert groups[0]["link_id"] == 5 and groups[1]["link_id"] is None


def test_grave_row_texte():
    r = gv.grave_row(_g(1, nickname="Blitz", level=17, route=-1, cause="soullink_partner",
                        shiny=1), SPECIES)
    assert r["title"] == "Blitz (Pikachu)"
    assert r["level"] == "Lv. 17" and r["place"] == "Starter"
    assert r["cause"] == "Soullink-Partner" and r["counted"] is False and r["shiny"] is True
    assert gv.grave_row(_g(2, dexnr=None), SPECIES)["title"] == "?"
    assert gv.cause_label(None) == "unbekannt" and gv.cause_label("xyz") == "xyz"


def test_csv_export(tmp_path):
    graves = [_g(2, "Steffen", ts=20, nickname="Tra", dexnr=280, partners=["web"]),
              _g(1, "web", ts=10, level=5)]
    path = gv.export_csv(graves, tmp_path / "out" / "f.csv", SPECIES)
    raw = path.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")   # BOM für Excel
    with open(path, encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.reader(fh, delimiter=";"))
    header = rows[0]
    assert header[0] == "Datum" and "Pokémon" in header
    col = {name: header.index(name) for name in ("Spieler", "Pokémon", "Partner", "Level")}
    assert rows[1][col["Spieler"]] == "web" and rows[1][col["Level"]] == "5"
    assert rows[2][col["Pokémon"]] == "Trasla" and rows[2][col["Partner"]] == "web"
    assert gv.default_export_name("Projekt A/B").startswith("friedhof_Projekt_A_B_")
