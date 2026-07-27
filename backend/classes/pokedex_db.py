import json
import sqlite3
import threading
import traceback
from datetime import datetime, timezone
from pathlib import Path

from backend.bag_decoder import BagItem
from backend.classes.Pokemon import Pokemon
from backend.logging_setup import get_logger


CURRENT_SCHEMA_VERSION = 5


def _serialized(func):
    """Serialisiert PokedexDB-Methoden über ``self.access_lock``. RLock, damit
    verschachtelte Aufrufe (z.B. ``upsert_team`` → ``upsert_pokemon``) nicht
    deadlocken."""
    def wrapper(self, *args, **kwargs):
        with self.access_lock:
            return func(self, *args, **kwargs)
    wrapper.__name__ = func.__name__
    wrapper.__doc__ = func.__doc__
    return wrapper


class PokedexDB:
    def __init__(self, session_path):
        self.session_path = Path(session_path)
        self.db_path = self.session_path / "pokemon.db"
        self.connection: sqlite3.Connection | None = None
        # RLock serialisiert Zugriffe auf ``self.connection`` zwischen mehreren
        # Worker-Threads. Notwendig, seit ``Munchlax._finalize_run`` per
        # ``run_in_executor`` aus dem UI-Callback läuft und damit parallel zu
        # ``alter_teams``-Executor-Calls treffen kann. Wer außerhalb dieser
        # Klasse direkt auf ``self.connection`` zugreift, muss den Lock manuell
        # halten (``with pokedex_db.access_lock:``).
        self.access_lock = threading.RLock()
        self.logger = get_logger(__name__, './logs/pokedex_db.log')

    def connect(self):
        with self.access_lock:
            try:
                self.session_path.mkdir(parents=True, exist_ok=True)
                # check_same_thread=False: DB-Zugriffe laufen aus mehreren Executor-
                # Threads (alter_teams, _finalize_run). Serialisierung erfolgt über
                # ``self.access_lock``.
                self.connection = sqlite3.connect(self.db_path, check_same_thread=False)
                self.connection.row_factory = sqlite3.Row
                self._migrate()
                self.logger.info(f"PokedexDB verbunden: {self.db_path}")
            except Exception as err:
                self.logger.error(f"PokedexDB connect failed: {type(err)},{err}")
                self.logger.error(f"{traceback.format_exc()}")
                self.connection = None

    def close(self):
        with self.access_lock:
            if self.connection:
                try:
                    self.connection.close()
                except Exception as err:
                    self.logger.warning(f"PokedexDB close failed: {type(err)},{err}")
                self.connection = None

    def _migrate(self):
        assert self.connection is not None
        cursor = self.connection.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS schema_version (
                version    INTEGER PRIMARY KEY,
                applied_at TEXT    NOT NULL
            )
        """)
        cursor.execute("SELECT COALESCE(MAX(version), 0) FROM schema_version")
        current_version = cursor.fetchone()[0]

        if current_version < 1:
            self._apply_v1(cursor)
        if current_version < 2:
            self._apply_v2(cursor)
        if current_version < 3:
            self._apply_v3(cursor)
        if current_version < 4:
            self._apply_v4(cursor)
        if current_version < 5:
            self._apply_v5(cursor)

        self.connection.commit()

    def _apply_v1(self, cursor):
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS pokemon (
                personality  INTEGER NOT NULL,
                owner        TEXT    NOT NULL,
                edition      TEXT,
                dexnr        TEXT,
                nickname     TEXT,
                lvl          INTEGER,
                shiny        INTEGER,
                female       INTEGER,
                form         TEXT,
                item         TEXT,
                route        INTEGER,
                nature       INTEGER,
                ability      INTEGER,
                ivs          TEXT,
                evs          TEXT,
                moves        TEXT,
                exp          INTEGER,
                first_seen   TEXT,
                last_seen    TEXT,
                PRIMARY KEY (personality, owner)
            )
        """)
        cursor.execute(
            "INSERT INTO schema_version (version, applied_at) VALUES (?, ?)",
            (1, datetime.now(timezone.utc).isoformat()),
        )
        self.logger.info("PokedexDB Schema v1 angewendet")

    def _apply_v2(self, cursor):
        # bag_inventory: aktueller Bestand pro Pocket. Wird bei jedem Bag-Read
        # vollstaendig fuer (owner, edition, pocket) ueberschrieben — verlorene
        # Items verschwinden also auch wieder.
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS bag_inventory (
                owner       TEXT    NOT NULL,
                edition     TEXT    NOT NULL,
                pocket      TEXT    NOT NULL,
                item_id     INTEGER NOT NULL,
                qty         INTEGER NOT NULL,
                last_seen   TEXT    NOT NULL,
                PRIMARY KEY (owner, edition, pocket, item_id)
            )
        """)
        # bag_first_seen: zeitstempel des ersten Auftretens pro Item-ID. Wird
        # NIE geloescht oder ueberschrieben — Treiber fuer die Nuzlocke-Regel
        # "Run startet bei Erhalt jeder Ball-Art".
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS bag_first_seen (
                owner       TEXT    NOT NULL,
                edition     TEXT    NOT NULL,
                item_id     INTEGER NOT NULL,
                pocket      TEXT,
                first_seen  TEXT    NOT NULL,
                PRIMARY KEY (owner, edition, item_id)
            )
        """)
        cursor.execute(
            "INSERT INTO schema_version (version, applied_at) VALUES (?, ?)",
            (2, datetime.now(timezone.utc).isoformat()),
        )
        self.logger.info("PokedexDB Schema v2 angewendet (bag_inventory + bag_first_seen)")

    def _apply_v3(self, cursor):
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS encounters (
                personality INTEGER NOT NULL,
                owner       TEXT    NOT NULL,
                edition     INTEGER NOT NULL,
                route       INTEGER NOT NULL,
                dexnr       INTEGER NOT NULL,
                lvl         INTEGER,
                shiny       INTEGER NOT NULL DEFAULT 0,
                is_first    INTEGER NOT NULL DEFAULT 0,
                is_shiny_override INTEGER NOT NULL DEFAULT 0,
                is_dupes_skip     INTEGER NOT NULL DEFAULT 0,
                has_balls   INTEGER NOT NULL DEFAULT 0,
                timestamp   TEXT    NOT NULL,
                PRIMARY KEY (personality, owner)
            )
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_encounters_route
            ON encounters (owner, edition, route)
        """)
        cursor.execute(
            "INSERT INTO schema_version (version, applied_at) VALUES (?, ?)",
            (3, datetime.now(timezone.utc).isoformat()),
        )
        self.logger.info("PokedexDB Schema v3 angewendet (encounters)")

    def _apply_v4(self, cursor):
        cursor.execute("ALTER TABLE encounters ADD COLUMN method TEXT DEFAULT 'wild'")
        cursor.execute("ALTER TABLE encounters ADD COLUMN outcome TEXT DEFAULT 'unknown'")
        cursor.execute(
            "INSERT INTO schema_version (version, applied_at) VALUES (?, ?)",
            (4, datetime.now(timezone.utc).isoformat()),
        )
        self.logger.info("PokedexDB Schema v4 angewendet (method + outcome)")

    def _apply_v5(self, cursor):
        # Soullink-Verlinkung: encounters bekommt link_group_id + link_index,
        # zwei neue Tabellen halten die Group-Metadaten und die Member-Zuordnung.
        cursor.execute("ALTER TABLE encounters ADD COLUMN link_group_id INTEGER")
        cursor.execute("ALTER TABLE encounters ADD COLUMN link_index INTEGER")
        # soullink_links: eine Zeile pro Route/Encounter-Slot der Gruppe.
        # state = pending | complete | failed. edition_gen 3..7.
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS soullink_links (
                link_id      INTEGER PRIMARY KEY AUTOINCREMENT,
                route        INTEGER NOT NULL,
                edition_gen  INTEGER NOT NULL,
                link_index   INTEGER NOT NULL,
                state        TEXT    NOT NULL DEFAULT 'pending',
                created_at   TEXT    NOT NULL,
                updated_at   TEXT    NOT NULL
            )
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_soullink_links_route
            ON soullink_links (edition_gen, route, link_index)
        """)
        # soullink_link_members: welcher Owner (Spieler) mit welchem PID zur Gruppe gehört.
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS soullink_link_members (
                link_id     INTEGER NOT NULL,
                owner       TEXT    NOT NULL,
                personality INTEGER NOT NULL,
                edition     INTEGER NOT NULL,
                outcome     TEXT    NOT NULL DEFAULT 'unknown',
                PRIMARY KEY (link_id, owner),
                FOREIGN KEY (link_id) REFERENCES soullink_links (link_id)
            )
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_soullink_members_owner
            ON soullink_link_members (owner, personality)
        """)
        cursor.execute(
            "INSERT INTO schema_version (version, applied_at) VALUES (?, ?)",
            (5, datetime.now(timezone.utc).isoformat()),
        )
        self.logger.info("PokedexDB Schema v5 angewendet (soullink_links + members)")

    @staticmethod
    def build_owner(your_name: str, client_id: str) -> str:
        return f"{your_name}_{client_id}"

    @_serialized
    def upsert_pokemon(self, owner: str, edition, pokemon: Pokemon) -> str:
        """Gibt 'inserted', 'updated' oder 'skipped' zurück."""
        if self.connection is None:
            return "skipped"
        if not hasattr(pokemon, "personality") or pokemon.personality is None:
            return "skipped"
        if pokemon.dexnr == 0:
            return "skipped"
        try:
            now = datetime.now(timezone.utc).isoformat()
            cursor = self.connection.cursor()
            cursor.execute(
                """
                INSERT INTO pokemon (
                    personality, owner, edition, dexnr, nickname, lvl,
                    shiny, female, form, item, route, nature, ability,
                    ivs, evs, moves, exp, first_seen, last_seen
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(personality, owner) DO UPDATE SET
                    edition   = excluded.edition,
                    dexnr     = excluded.dexnr,
                    nickname  = excluded.nickname,
                    lvl       = excluded.lvl,
                    shiny     = excluded.shiny,
                    female    = excluded.female,
                    form      = excluded.form,
                    item      = excluded.item,
                    route     = excluded.route,
                    nature    = excluded.nature,
                    ability   = excluded.ability,
                    ivs       = excluded.ivs,
                    evs       = excluded.evs,
                    moves     = excluded.moves,
                    exp       = excluded.exp,
                    last_seen = excluded.last_seen
                """,
                (
                    int(pokemon.personality),
                    owner,
                    str(edition) if edition is not None else None,
                    str(pokemon.dexnr),
                    pokemon.nickname,
                    pokemon.lvl,
                    int(bool(pokemon.shiny)),
                    int(bool(pokemon.female)),
                    pokemon.form,
                    str(pokemon.item) if pokemon.item is not None else None,
                    getattr(pokemon, "route", None),
                    getattr(pokemon, "nature", None),
                    getattr(pokemon, "ability", None),
                    json.dumps(getattr(pokemon, "ivs", None)),
                    json.dumps(getattr(pokemon, "evs", None)),
                    json.dumps(getattr(pokemon, "moves", None)),
                    getattr(pokemon, "experience_points", None),
                    now,
                    now,
                ),
            )
            self.connection.commit()
            changes = cursor.connection.total_changes
            if cursor.rowcount > 0:
                last_id = cursor.lastrowid
                cursor.execute(
                    "SELECT first_seen, last_seen FROM pokemon "
                    "WHERE personality = ? AND owner = ?",
                    (int(pokemon.personality), owner),
                )
                row = cursor.fetchone()
                if row and row["first_seen"] == row["last_seen"]:
                    return "inserted"
                return "updated"
            return "skipped"
        except Exception as err:
            self.logger.error(f"upsert_pokemon failed: {type(err)},{err}")
            self.logger.error(f"{traceback.format_exc()}")
            return "skipped"

    @_serialized
    def upsert_team(self, owner: str, edition, team) -> tuple[int, list]:
        """Gibt (written_count, new_personalities) zurück.

        new_personalities enthält PVs von Pokemon die NEU in die DB kamen
        (INSERT, nicht UPDATE) — für Gift-Encounter-Erkennung.
        """
        written = 0
        new_pvs = []
        for pokemon in team:
            result = self.upsert_pokemon(owner, edition, pokemon)
            if result != "skipped":
                written += 1
            if result == "inserted":
                pv = getattr(pokemon, "personality", None)
                if pv is not None:
                    new_pvs.append(int(pv))
        return written, new_pvs

    @_serialized
    def get_lvls_by_personalities(self, personalities: list[int]) -> dict[int, int]:
        """Liefert pro PV das zuletzt gespeicherte Level (last_seen DESC).

        Wird vom Munchlax beim Box-Refresh genutzt, um die XP-Approximation
        des Decoders durch echte Team-Level zu ersetzen — sofern dasselbe
        Pokemon (per PV) schon mal im Team war. Gen 1/2 hat keine PV und
        landet ohnehin nie in der pokemon.db (siehe upsert_pokemon).
        """
        if self.connection is None or not personalities:
            return {}
        result: dict[int, int] = {}
        try:
            cursor = self.connection.cursor()
            for pv in set(personalities):
                cursor.execute(
                    "SELECT lvl FROM pokemon WHERE personality = ? "
                    "AND lvl IS NOT NULL ORDER BY last_seen DESC LIMIT 1",
                    (int(pv),),
                )
                row = cursor.fetchone()
                if row and row["lvl"] is not None:
                    result[int(pv)] = row["lvl"]
            return result
        except Exception as err:
            self.logger.warning(f"get_lvls_by_personalities failed: {type(err)},{err}")
            self.logger.warning(f"{traceback.format_exc()}")
            return result

    @_serialized
    def get_all(self, owner: str | None = None, edition=None, shiny_only: bool = False):
        if self.connection is None:
            return []
        try:
            query = "SELECT * FROM pokemon WHERE 1=1"
            params: list = []
            if owner is not None:
                query += " AND owner = ?"
                params.append(owner)
            if edition is not None:
                query += " AND edition = ?"
                params.append(str(edition))
            if shiny_only:
                query += " AND shiny = 1"
            query += " ORDER BY last_seen DESC"
            cursor = self.connection.cursor()
            cursor.execute(query, params)
            return [dict(row) for row in cursor.fetchall()]
        except Exception as err:
            self.logger.error(f"get_all failed: {type(err)},{err}")
            self.logger.error(f"{traceback.format_exc()}")
            return []

    # -- Bag (Schema v2) -----------------------------------------------------

    @_serialized
    def upsert_bag_pocket(self, owner: str, edition, pocket: str,
                          items: list[BagItem]) -> bool:
        """Synchronisiert eine Pocket vollstaendig: ersetzt den bisherigen
        Bestand fuer (owner, edition, pocket) durch die uebergebene Liste
        und pflegt parallel bag_first_seen (INSERT OR IGNORE pro Item).

        Items mit qty == 0 werden uebersprungen (im Speicher koennen leere
        Slots als id=0/qty=0 erscheinen — die filtert der Decoder zwar schon,
        aber wir sind hier defensiv).
        """
        if self.connection is None:
            return False
        try:
            now = datetime.now(timezone.utc).isoformat()
            edition_str = str(edition) if edition is not None else ""
            cursor = self.connection.cursor()
            cursor.execute(
                "DELETE FROM bag_inventory WHERE owner = ? AND edition = ? AND pocket = ?",
                (owner, edition_str, pocket),
            )
            for it in items:
                if it.id == 0 or it.qty == 0:
                    continue
                cursor.execute(
                    """
                    INSERT INTO bag_inventory (owner, edition, pocket, item_id, qty, last_seen)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(owner, edition, pocket, item_id) DO UPDATE SET
                        qty       = excluded.qty,
                        last_seen = excluded.last_seen
                    """,
                    (owner, edition_str, pocket, int(it.id), int(it.qty), now),
                )
                cursor.execute(
                    """
                    INSERT OR IGNORE INTO bag_first_seen
                        (owner, edition, item_id, pocket, first_seen)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (owner, edition_str, int(it.id), pocket, now),
                )
            self.connection.commit()
            return True
        except Exception as err:
            self.logger.error(f"upsert_bag_pocket failed: {type(err)},{err}")
            self.logger.error(f"{traceback.format_exc()}")
            return False

    @_serialized
    def get_bag_inventory(self, owner: str, edition=None,
                          pocket: str | None = None) -> list[dict]:
        if self.connection is None:
            return []
        try:
            query = "SELECT * FROM bag_inventory WHERE owner = ?"
            params: list = [owner]
            if edition is not None:
                query += " AND edition = ?"
                params.append(str(edition))
            if pocket is not None:
                query += " AND pocket = ?"
                params.append(pocket)
            query += " ORDER BY pocket, item_id"
            cursor = self.connection.cursor()
            cursor.execute(query, params)
            return [dict(row) for row in cursor.fetchall()]
        except Exception as err:
            self.logger.error(f"get_bag_inventory failed: {type(err)},{err}")
            self.logger.error(f"{traceback.format_exc()}")
            return []

    @_serialized
    def get_bag_first_seen(self, owner: str, edition=None) -> dict[int, str]:
        """Liefert {item_id: first_seen_iso} fuer (owner, edition).

        Wird vom Nuzlocke-Regelmodul genutzt, um zu pruefen, ob ein bestimmter
        Ball/Item schonmal in der Tasche war.
        """
        if self.connection is None:
            return {}
        try:
            query = "SELECT item_id, first_seen FROM bag_first_seen WHERE owner = ?"
            params: list = [owner]
            if edition is not None:
                query += " AND edition = ?"
                params.append(str(edition))
            cursor = self.connection.cursor()
            cursor.execute(query, params)
            return {int(row["item_id"]): row["first_seen"]
                    for row in cursor.fetchall()}
        except Exception as err:
            self.logger.error(f"get_bag_first_seen failed: {type(err)},{err}")
            self.logger.error(f"{traceback.format_exc()}")
            return {}

    # -- Encounters (Schema v3) ------------------------------------------------

    @_serialized
    def insert_encounter(self, personality: int, owner: str, edition: int,
                         route: int, dexnr: int, lvl: int, shiny: bool,
                         is_first: bool, is_shiny_override: bool,
                         is_dupes_skip: bool, has_balls: bool,
                         method: str = "wild",
                         outcome: str = "unknown") -> bool:
        if self.connection is None:
            return False
        try:
            now = datetime.now(timezone.utc).isoformat()
            cursor = self.connection.cursor()
            cursor.execute(
                """
                INSERT OR IGNORE INTO encounters (
                    personality, owner, edition, route, dexnr, lvl, shiny,
                    is_first, is_shiny_override, is_dupes_skip, has_balls,
                    method, outcome, timestamp
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    int(personality), owner, int(edition), int(route),
                    int(dexnr), int(lvl), int(shiny),
                    int(is_first), int(is_shiny_override),
                    int(is_dupes_skip), int(has_balls),
                    method, outcome, now,
                ),
            )
            self.connection.commit()
            return cursor.rowcount > 0
        except Exception as err:
            self.logger.error(f"insert_encounter failed: {type(err)},{err}")
            self.logger.error(f"{traceback.format_exc()}")
            return False

    @_serialized
    def update_encounter_outcome(self, personality: int, owner: str,
                                  outcome: str) -> bool:
        if self.connection is None:
            return False
        try:
            cursor = self.connection.cursor()
            cursor.execute(
                "UPDATE encounters SET outcome = ? "
                "WHERE personality = ? AND owner = ?",
                (outcome, int(personality), owner),
            )
            self.connection.commit()
            return cursor.rowcount > 0
        except Exception as err:
            self.logger.error(f"update_encounter_outcome failed: {type(err)},{err}")
            self.logger.error(f"{traceback.format_exc()}")
            return False

    @_serialized
    def sync_encounter(self, enc: dict) -> bool:
        """INSERT OR REPLACE für Remote-Encounters. Überschreibt bei PK-Kollision."""
        if self.connection is None:
            return False
        try:
            cursor = self.connection.cursor()
            cursor.execute(
                """
                INSERT OR REPLACE INTO encounters (
                    personality, owner, edition, route, dexnr, lvl, shiny,
                    is_first, is_shiny_override, is_dupes_skip, has_balls,
                    method, outcome, timestamp
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    int(enc["personality"]), enc["owner"], int(enc["edition"]),
                    int(enc["route"]), int(enc["dexnr"]), int(enc["lvl"]),
                    int(enc.get("shiny", 0)),
                    int(enc.get("is_first", 0)), int(enc.get("is_shiny_override", 0)),
                    int(enc.get("is_dupes_skip", 0)), int(enc.get("has_balls", 0)),
                    enc.get("method", "wild"), enc.get("outcome", "unknown"),
                    enc.get("timestamp", ""),
                ),
            )
            self.connection.commit()
            return cursor.rowcount > 0
        except Exception as err:
            self.logger.error(f"sync_encounter failed: {type(err)},{err}")
            self.logger.error(f"{traceback.format_exc()}")
            return False

    @_serialized
    def get_route_status(self, owner: str, edition=None) -> list[dict]:
        if self.connection is None:
            return []
        try:
            query = (
                "SELECT route, method, outcome, dexnr, lvl, shiny, "
                "is_first, is_dupes_skip, has_balls, timestamp "
                "FROM encounters WHERE owner = ?"
            )
            params: list = [owner]
            if edition is not None:
                query += " AND edition = ?"
                params.append(int(edition))
            query += " ORDER BY route, timestamp"
            cursor = self.connection.cursor()
            cursor.execute(query, params)
            return [dict(row) for row in cursor.fetchall()]
        except Exception as err:
            self.logger.error(f"get_route_status failed: {type(err)},{err}")
            self.logger.error(f"{traceback.format_exc()}")
            return []

    @_serialized
    def has_any_encounter(self, owner: str, edition: int) -> bool:
        """True wenn owner+edition mindestens einen Encounter-Eintrag hat.

        Wird fuer Starter-Detection genutzt: erst wenn diese Kombi noch
        komplett leer ist, wird ein Team-Wachstum als Starter interpretiert.
        Verhindert Doppel-Starter bei spaeteren Team-Aenderungen.
        """
        if self.connection is None:
            return False
        try:
            cursor = self.connection.cursor()
            cursor.execute(
                "SELECT 1 FROM encounters WHERE owner = ? AND edition = ? LIMIT 1",
                (owner, int(edition)),
            )
            return cursor.fetchone() is not None
        except Exception as err:
            self.logger.error(f"has_any_encounter failed: {type(err)},{err}")
            self.logger.error(f"{traceback.format_exc()}")
            return False

    @_serialized
    def has_encounter_on_route(self, owner: str, edition: int,
                               route: int) -> bool:
        """True wenn Route bereits einen First-Encounter hat (Nuzlocke-Zwecke).

        Filtert `is_first = 1`: dupes-Skips und andere Retry-Zeilen
        (is_first=0) zählen NICHT als Route-belegt — die Route bleibt für den
        echten First-Encounter frei.
        """
        if self.connection is None:
            return False
        try:
            cursor = self.connection.cursor()
            cursor.execute(
                "SELECT 1 FROM encounters "
                "WHERE owner = ? AND edition = ? AND route = ? "
                "  AND has_balls = 1 AND is_first = 1 "
                "LIMIT 1",
                (owner, int(edition), int(route)),
            )
            return cursor.fetchone() is not None
        except Exception as err:
            self.logger.error(f"has_encounter_on_route failed: {type(err)},{err}")
            self.logger.error(f"{traceback.format_exc()}")
            return False

    @_serialized
    def is_species_family_caught(self, owner: str,
                                  family_members: list[int]) -> bool:
        if self.connection is None or not family_members:
            return False
        try:
            placeholders = ",".join("?" for _ in family_members)
            cursor = self.connection.cursor()
            cursor.execute(
                f"SELECT 1 FROM pokemon "
                f"WHERE owner = ? AND dexnr IN ({placeholders}) "
                f"LIMIT 1",
                [owner] + [str(d) for d in family_members],
            )
            return cursor.fetchone() is not None
        except Exception as err:
            self.logger.error(f"is_species_family_caught failed: {type(err)},{err}")
            self.logger.error(f"{traceback.format_exc()}")
            return False

    @_serialized
    def has_catching_balls(self, owner: str, edition) -> bool:
        """Prüft ob der Spieler JEMALS einen Ball besessen hat (bag_first_seen).

        Bestimmt ob der Nuzlocke-Run gestartet hat — einmal True, bleibt True.
        """
        if self.connection is None:
            return False
        try:
            edition_str = str(edition) if edition is not None else ""
            cursor = self.connection.cursor()
            cursor.execute(
                "SELECT 1 FROM bag_first_seen "
                "WHERE owner = ? AND edition = ? AND pocket = 'baelle' "
                "LIMIT 1",
                (owner, edition_str),
            )
            return cursor.fetchone() is not None
        except Exception as err:
            self.logger.error(f"has_catching_balls failed: {type(err)},{err}")
            self.logger.error(f"{traceback.format_exc()}")
            return False

    @_serialized
    def get_encounters(self, owner: str, edition=None) -> list[dict]:
        if self.connection is None:
            return []
        try:
            query = "SELECT * FROM encounters WHERE owner = ?"
            params: list = [owner]
            if edition is not None:
                query += " AND edition = ?"
                params.append(int(edition))
            query += " ORDER BY timestamp DESC"
            cursor = self.connection.cursor()
            cursor.execute(query, params)
            return [dict(row) for row in cursor.fetchall()]
        except Exception as err:
            self.logger.error(f"get_encounters failed: {type(err)},{err}")
            self.logger.error(f"{traceback.format_exc()}")
            return []
