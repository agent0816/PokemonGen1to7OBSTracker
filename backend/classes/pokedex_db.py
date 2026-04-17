import json
import logging
import sqlite3
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

from backend.classes.Pokemon import Pokemon


CURRENT_SCHEMA_VERSION = 1


class PokedexDB:
    def __init__(self, session_path):
        self.session_path = Path(session_path)
        self.db_path = self.session_path / "pokemon.db"
        self.connection: sqlite3.Connection | None = None
        self.logger = self.init_logging()

    def init_logging(self):
        logger = logging.getLogger(__name__)
        logger.setLevel(logging.INFO)

        logging_formatter = logging.Formatter('[%(asctime)s] %(levelname)s: %(message)s')

        file_handler = logging.FileHandler('./logs/pokedex_db.log', 'w')
        file_handler.setFormatter(logging_formatter)
        logger.addHandler(file_handler)

        stream_handler = logging.StreamHandler(sys.stdout)
        stream_handler.setFormatter(logging_formatter)
        logger.addHandler(stream_handler)

        return logger

    def connect(self):
        try:
            self.session_path.mkdir(parents=True, exist_ok=True)
            # check_same_thread=False: Zugriffe laufen über run_in_executor und werden
            # vom Caller serialisiert (siehe Munchlax.alter_teams).
            self.connection = sqlite3.connect(self.db_path, check_same_thread=False)
            self.connection.row_factory = sqlite3.Row
            self._migrate()
            self.logger.info(f"PokedexDB verbunden: {self.db_path}")
        except Exception as err:
            self.logger.error(f"PokedexDB connect failed: {type(err)},{err}")
            self.logger.error(f"{traceback.format_exc()}")
            self.connection = None

    def close(self):
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

    @staticmethod
    def build_owner(your_name: str, client_id: str) -> str:
        return f"{your_name}_{client_id}"

    def upsert_pokemon(self, owner: str, edition, pokemon: Pokemon) -> bool:
        if self.connection is None:
            return False
        # Gen 1/2 haben keine personality — werden übersprungen.
        if not hasattr(pokemon, "personality") or pokemon.personality is None:
            return False
        # Leere Teamslots (dexnr == 0) nicht speichern; Eier (dexnr == 'egg') sehr wohl.
        if pokemon.dexnr == 0:
            return False
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
            return True
        except Exception as err:
            self.logger.error(f"upsert_pokemon failed: {type(err)},{err}")
            self.logger.error(f"{traceback.format_exc()}")
            return False

    def upsert_team(self, owner: str, edition, team) -> int:
        written = 0
        for pokemon in team:
            if self.upsert_pokemon(owner, edition, pokemon):
                written += 1
        return written

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
