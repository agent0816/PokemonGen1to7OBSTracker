"""Schema v8: Gen-4-Wild-Encounter-route von roher map_id auf die Section
(met_location-Namespace) umschreiben."""
from backend.classes.pokedex_db import PokedexDB


def _insert(cur, pv, edition, route, method):
    cur.execute(
        "INSERT INTO encounters (personality, owner, edition, route, dexnr, "
        "timestamp, method) VALUES (?, 'Test_1', ?, ?, 1, 'x', ?)",
        (pv, edition, route, method),
    )


def test_v8_rechnet_gen4_wild_um(tmp_path):
    db = PokedexDB(tmp_path)
    db.connect()
    cur = db.connection.cursor()
    _insert(cur, 1, 42, 342, "wild")    # Perl Route 201 (live 2026-10-28) -> 16
    _insert(cur, 2, 44, 33, "wild")     # HG Route 29 -> 177
    _insert(cur, 3, 42, 342, "gift")    # Gift: schon met_location, unangetastet
    _insert(cur, 4, 34, 342, "wild")    # Gen 3: unangetastet
    _insert(cur, 5, 43, 99999, "wild")  # unbekannte map_id bleibt
    # v8 erneut ausfuehren (connect hat sie auf der leeren DB schon angewendet).
    # Ab v8 alles entfernen — _migrate laeuft nur ab MAX(version) (v9 ist idempotent).
    cur.execute("DELETE FROM schema_version WHERE version >= 8")
    db._migrate()
    routes = dict(db.connection.execute(
        "SELECT personality, route FROM encounters").fetchall())
    assert routes == {1: 16, 2: 177, 3: 342, 4: 342, 5: 99999}
    versions = [r[0] for r in db.connection.execute(
        "SELECT version FROM schema_version").fetchall()]
    assert versions.count(8) == 1
