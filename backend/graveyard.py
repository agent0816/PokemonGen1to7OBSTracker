"""App-weiter Friedhof: alle Pokémon-Tode über Sessions, Runs und Resets hinweg.

Wunsch 2026-11-11 (Friedhof am Ende eines YouTube-Projekts + Todescounter):
- Eigene SQLite ``backend/config/graveyard.db``, NICHT session-gebunden.
  Überlebt Session-Reset, Run-Ende, Snapshot-Laden und Session-Löschen.
- Jede Instanz (Host + Clients) schreibt ihren eigenen Friedhof (lokale
  Erkennung + ``soullink_death``-Broadcast des Servers), idempotent über
  UNIQUE(session, personality, owner_name) (run_id nur Metadatum).
- Todescounter pro Spieler und Session: Einträge der Session seit dem letzten
  manuellen Session-Reset, ohne Soullink-Partner-Tode (``soullink_partner``)
  und ohne manuell wiederbelebte (``removed=1``).

Kivy-frei und blockierend → aus asyncio per ``run_in_executor`` aufrufen.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
import traceback
from pathlib import Path

from backend.logging_setup import get_logger

logger = get_logger(__name__, 'logs/graveyard.log')

DEFAULT_GRAVEYARD_PATH = Path('backend/config/graveyard.db')
# Override (Tests: tests/conftest.py → Temp-Verzeichnis)
GRAVEYARD_ENV = 'TRACKER_GRAVEYARD_DB'


def graveyard_path() -> Path:
    import os
    return Path(os.environ.get(GRAVEYARD_ENV) or DEFAULT_GRAVEYARD_PATH)
GRAVEYARD_FILENAME = 'graveyard.db'
SCHEMA_VERSION = 1

# Tod durch Soullink-Partner zählt nicht (Counter-Regel des Users)
NOT_COUNTED_CAUSES = ('soullink_partner',)

# Felder, die beim Upsert nur gefüllt werden, wenn noch leer (Wert zum
# ersten Erfassungszeitpunkt = Zustand beim Tod bleibt stehen).
_FILL_FIELDS = ('owner_slot', 'edition', 'dexnr', 'form', 'female', 'shiny',
                'nickname', 'level', 'route', 'method', 'team')
# Felder, die die Server-Meldung (authoritative) überschreibt
_AUTH_FIELDS = ('cause', 'link_id', 'partners')


class GraveyardDB:
    def __init__(self, path: Path | str = DEFAULT_GRAVEYARD_PATH):
        self.path = Path(path)
        self._lock = threading.Lock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(str(self.path), check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        with self._lock:
            cur = self.connection.cursor()
            cur.execute("CREATE TABLE IF NOT EXISTS schema_version "
                        "(version INTEGER PRIMARY KEY, applied_at REAL)")
            # Unveröffentlichter Dev-Stand (UNIQUE mit run_id, ohne recorded_at):
            # beiseitelegen statt löschen, Tabelle neu anlegen.
            cols = {r[1] for r in cur.execute("PRAGMA table_info(graves)").fetchall()}
            if cols and "recorded_at" not in cols:
                cur.execute("ALTER TABLE graves RENAME TO graves_dev_alt")
                cur.execute("DROP INDEX IF EXISTS idx_graves_session")
                logger.warning("Friedhof: altes Dev-Schema nach graves_dev_alt verschoben")
            cur.execute("""
                CREATE TABLE IF NOT EXISTS graves (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    session     TEXT    NOT NULL,
                    run_id      TEXT    NOT NULL DEFAULT '',
                    personality INTEGER NOT NULL,
                    owner_name  TEXT    NOT NULL,
                    owner_slot  INTEGER,
                    ts          REAL    NOT NULL,
                    recorded_at REAL    NOT NULL,
                    edition     INTEGER,
                    dexnr       INTEGER,
                    form        TEXT,
                    female      INTEGER,
                    shiny       INTEGER,
                    nickname    TEXT,
                    level       INTEGER,
                    route       INTEGER,
                    method      TEXT,
                    cause       TEXT,
                    link_id     INTEGER,
                    partners    TEXT,
                    team        TEXT,
                    removed     INTEGER NOT NULL DEFAULT 0,
                    UNIQUE (session, personality, owner_name)
                )
            """)
            cur.execute("CREATE INDEX IF NOT EXISTS idx_graves_session "
                        "ON graves (session, owner_name)")
            cur.execute("""
                CREATE TABLE IF NOT EXISTS counter_resets (
                    session  TEXT NOT NULL,
                    reset_ts REAL NOT NULL
                )
            """)
            cur.execute("INSERT OR IGNORE INTO schema_version (version, applied_at) "
                        "VALUES (?, ?)", (SCHEMA_VERSION, time.time()))
            self.connection.commit()

    def close(self) -> None:
        with self._lock:
            try:
                self.connection.close()
            except Exception:
                pass

    # ------------------------------------------------------------ Schreiben

    def record_death(self, entry: dict, authoritative: bool = False) -> bool:
        """Upsert eines Todes. ``entry`` braucht session, personality, owner_name.

        Schlüssel: (session, personality, owner_name) — run_id ist nur
        Metadatum (erste bekannte gewinnt), sonst entstünden bei Run-Wechsel,
        Snapshot-Laden oder Meldung vor Run-Start Doppel-Einträge.

        authoritative=True (Server-Broadcast/-Snapshot): cause/link_id/partners
        überschreiben die lokale Erstmeldung, und nur der Server kann einen
        manuell wiederbelebten Eintrag (removed=1) wieder aktivieren — dann
        mit neuem ``recorded_at`` (zählt ab jetzt). Lokale Neuerkennung (z.B.
        nach Session-Reset) belebt eine manuelle Korrektur nicht wieder.

        ``ts`` = Todeszeitpunkt (Anzeige, ggf. Server-Uhr); ``recorded_at`` =
        lokale Uhr beim Eintragen → Counter-Vergleich mit dem lokalen
        Reset-Zeitpunkt (kein Uhren-Skew zwischen Host und Client).

        Rückgabe True, wenn sich der Counter-relevante Zustand geändert hat
        (neuer Eintrag oder removed 1→0 oder Ursache geändert).
        """
        session = str(entry.get('session') or '')
        owner_name = str(entry.get('owner_name') or '')
        pv = entry.get('personality')
        if not session or not owner_name or pv is None:
            logger.warning(f"record_death: unvollständig, verworfen: {entry!r}")
            return False
        run_id = str(entry.get('run_id') or '')
        partners = entry.get('partners')
        values = {
            'session': session, 'run_id': run_id, 'personality': int(pv),
            'owner_name': owner_name,
            'owner_slot': _int_or_none(entry.get('owner_slot')),
            'ts': float(entry.get('ts') or time.time()),
            'recorded_at': time.time(),
            'edition': _int_or_none(entry.get('edition')),
            'dexnr': _int_or_none(entry.get('dexnr')),
            'form': entry.get('form') if entry.get('form') not in (None, '') else None,
            'female': _bool_int_or_none(entry.get('female')),
            'shiny': _bool_int_or_none(entry.get('shiny')),
            'nickname': entry.get('nickname') or None,
            'level': _int_or_none(entry.get('level')),
            'route': _int_or_none(entry.get('route')),
            'method': entry.get('method') or None,
            'cause': entry.get('cause') or None,
            'link_id': _int_or_none(entry.get('link_id')),
            'partners': json.dumps(list(partners)) if partners else None,
            'team': entry.get('team') or None,
        }
        cols = list(values.keys())
        auth = 1 if authoritative else 0
        updates = [
            f"removed = CASE WHEN {auth} = 1 THEN 0 ELSE graves.removed END",
            f"recorded_at = CASE WHEN {auth} = 1 AND graves.removed = 1 "
            f"THEN excluded.recorded_at ELSE graves.recorded_at END",
            "run_id = CASE WHEN graves.run_id = '' THEN excluded.run_id ELSE graves.run_id END",
        ]
        updates += [f"{c} = COALESCE(graves.{c}, excluded.{c})" for c in _FILL_FIELDS]
        if authoritative:
            updates += [f"{c} = COALESCE(excluded.{c}, graves.{c})" for c in _AUTH_FIELDS]
        else:
            updates += [f"{c} = COALESCE(graves.{c}, excluded.{c})" for c in _AUTH_FIELDS]
        sql = (f"INSERT INTO graves ({', '.join(cols)}) "
               f"VALUES ({', '.join('?' for _ in cols)}) "
               f"ON CONFLICT(session, personality, owner_name) "
               f"DO UPDATE SET {', '.join(updates)}")
        key = (session, int(pv), owner_name)
        try:
            with self._lock:
                before = self._row_state(key)
                self.connection.execute(sql, [values[c] for c in cols])
                self.connection.commit()
                after = self._row_state(key)
        except Exception as err:
            logger.error(f"record_death failed: {type(err).__name__}: {err}\n{traceback.format_exc()}")
            return False
        changed = before != after
        if changed:
            logger.info(
                f"Friedhof: {'neu' if before is None else 'aktualisiert'} "
                f"session={session!r} run={run_id or '-'} owner={owner_name} "
                f"slot={values['owner_slot']} pv={pv} dex={values['dexnr']} "
                f"cause={after[1] if after else None}"
            )
        return changed

    def _row_state(self, key: tuple) -> tuple | None:
        row = self.connection.execute(
            "SELECT removed, cause FROM graves WHERE session = ? "
            "AND personality = ? AND owner_name = ?", key).fetchone()
        return (row[0], row[1]) if row is not None else None

    def has_grave(self, session: str, personality: int, owner_name: str) -> bool:
        """Eintrag existiert (auch removed) UND ist schon angereichert (dexnr
        bekannt) → Enrichment-Reads können entfallen. Lücken aus einer frühen
        Erstmeldung füllt der nächste volle Aufbau per COALESCE."""
        try:
            with self._lock:
                row = self.connection.execute(
                    "SELECT dexnr FROM graves WHERE session = ? AND personality = ? "
                    "AND owner_name = ?", (session, int(personality), owner_name)).fetchone()
                return row is not None and row[0] is not None
        except Exception as err:
            logger.error(f"has_grave failed: {type(err).__name__}: {err}")
            return False

    def mark_removed(self, session: str, personality: int, owner_name: str) -> bool:
        """Manuell „lebendig" (Fehlalarm-Korrektur): Eintrag zählt nicht mehr."""
        try:
            with self._lock:
                cur = self.connection.execute(
                    "UPDATE graves SET removed = 1 WHERE session = ? "
                    "AND personality = ? AND owner_name = ? AND removed = 0",
                    (session, int(personality), owner_name))
                self.connection.commit()
                changed = cur.rowcount > 0
        except Exception as err:
            logger.error(f"mark_removed failed: {type(err).__name__}: {err}\n{traceback.format_exc()}")
            return False
        if changed:
            logger.info(f"Friedhof: entfernt (manuell lebendig) session={session!r} "
                        f"owner={owner_name} pv={personality}")
        return changed

    def reset_counter(self, session: str, ts: float | None = None) -> None:
        """Manueller Session-Reset: Counter der Session ab jetzt bei 0.
        Einträge selbst bleiben (Friedhof)."""
        try:
            with self._lock:
                self.connection.execute(
                    "INSERT INTO counter_resets (session, reset_ts) VALUES (?, ?)",
                    (session, float(ts if ts is not None else time.time())))
                self.connection.commit()
            logger.info(f"Friedhof: Todescounter der Session {session!r} zurückgesetzt")
        except Exception as err:
            logger.error(f"reset_counter failed: {type(err).__name__}: {err}\n{traceback.format_exc()}")

    # ------------------------------------------------------------ Lesen

    def death_counts(self, session: str) -> dict[str, int]:
        """{owner_name: Anzahl} für den Todescounter der Session."""
        placeholders = ', '.join('?' for _ in NOT_COUNTED_CAUSES)
        try:
            with self._lock:
                rows = self.connection.execute(
                    f"SELECT owner_name, COUNT(*) FROM graves "
                    f"WHERE session = ? AND removed = 0 "
                    f"AND COALESCE(cause, '') NOT IN ({placeholders}) "
                    f"AND recorded_at > (SELECT COALESCE(MAX(reset_ts), 0) FROM counter_resets "
                    f"WHERE session = ?) "
                    f"GROUP BY owner_name",
                    (session, *NOT_COUNTED_CAUSES, session)).fetchall()
        except Exception as err:
            logger.error(f"death_counts failed: {type(err).__name__}: {err}\n{traceback.format_exc()}")
            return {}
        return {row[0]: int(row[1]) for row in rows}

    def list_graves(self, session: str | None = None,
                    include_removed: bool = False) -> list[dict]:
        """Friedhof-Einträge (älteste zuerst), optional pro Session."""
        where, params = [], []
        if session is not None:
            where.append("session = ?")
            params.append(session)
        if not include_removed:
            where.append("removed = 0")
        sql = "SELECT * FROM graves"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY ts, id"
        try:
            with self._lock:
                rows = self.connection.execute(sql, params).fetchall()
        except Exception as err:
            logger.error(f"list_graves failed: {type(err).__name__}: {err}\n{traceback.format_exc()}")
            return []
        out = []
        for row in rows:
            d = dict(row)
            try:
                d['partners'] = json.loads(d['partners']) if d.get('partners') else []
            except (TypeError, ValueError):
                d['partners'] = []
            out.append(d)
        return out

    def sessions(self) -> list[str]:
        try:
            with self._lock:
                rows = self.connection.execute(
                    "SELECT DISTINCT session FROM graves ORDER BY session").fetchall()
        except Exception as err:
            logger.error(f"sessions failed: {type(err).__name__}: {err}")
            return []
        return [r[0] for r in rows]


def _int_or_none(value) -> int | None:
    if value is None or value == '':
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _bool_int_or_none(value) -> int | None:
    if value is None:
        return None
    return 1 if bool(value) else 0
