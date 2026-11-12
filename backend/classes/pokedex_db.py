import json
import sqlite3
import threading
import traceback
from datetime import datetime, timezone
from pathlib import Path

from backend.bag_decoder import BagItem, catching_ball_ids
from backend.classes.Pokemon import Pokemon
from backend.encounter_edit import merge_encounter_edit, parse_locks
from backend.logging_setup import get_logger


CURRENT_SCHEMA_VERSION = 13

# rule_flags-Praefix: "Ball-freie Tasche im Run gesehen" (Ball-Gate scharf).
# Voller Flag-Name = Praefix + erste 19 Zeichen des Run-Start-Zeitstempels,
# damit ein Marker nie in einen spaeteren Run hinueberreicht.
BALL_FREE_FLAG_PREFIX = "ball_free_seen@"

# Werte der Spalte encounters.is_dupes_skip (Retry-Marker, truthy = Retry).
# 2 unterscheidet den Ersttyp-Clause-Retry vom Dupes-Retry ohne Schema-Bump;
# alle Schreibpfade nutzen int(...), der Wert bleibt erhalten.
RETRY_DUPE = 1
RETRY_TYPE_CLASH = 2
# Marker fuer Legacy-Gift/Fossil/Static/Egg/Token-Encounter aus Sessions vor
# der Migration auf route=map_header_id. Diese Zeilen hatten alle route=0
# und wuerden nach dem Fix sonst mit den frisch geloggten Wilds auf MAPSEC=0
# kollidieren bzw. das has_encounter_with_method_at_route-Dedupe umgehen
# (Doppel-Starter-Risiko). -999 signalisiert "Location unbekannt".
LEGACY_GIFT_ROUTE_MARKER = -999


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

    def connect(self, timeout: float = 5.0, quiet: bool = False):
        with self.access_lock:
            try:
                self.session_path.mkdir(parents=True, exist_ok=True)
                # check_same_thread=False: DB-Zugriffe laufen aus mehreren Executor-
                # Threads (alter_teams, _finalize_run). Serialisierung erfolgt über
                # ``self.access_lock``.
                self.connection = sqlite3.connect(
                    self.db_path, check_same_thread=False, timeout=timeout)
                self.connection.row_factory = sqlite3.Row
                self._migrate()
                # quiet: Kurzverbindungen (Arceus-Persistenz) nicht pro Write loggen.
                (self.logger.debug if quiet else self.logger.info)(
                    f"PokedexDB verbunden: {self.db_path}")
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
        if current_version < 6:
            self._apply_v6(cursor)
        if current_version < 7:
            self._apply_v7(cursor)
        if current_version < 8:
            self._apply_v8(cursor)
        if current_version < 9:
            self._apply_v9(cursor)
        if current_version < 10:
            self._apply_v10(cursor)
        if current_version < 11:
            self._apply_v11(cursor)
        if current_version < 12:
            self._apply_v12(cursor)
        if current_version < 13:
            self._apply_v13(cursor)

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

    def _apply_v6(self, cursor):
        # Migration: Legacy-Gift-Encounter (route=0, method != wild/starter) aus
        # Sessions vor der route=map_header_id-Umstellung auf einen Marker-Wert
        # umschreiben. Ohne diese Migration wuerde ein alter Starter mit
        # route=0 den has_encounter_with_method_at_route-Check umgehen (der
        # sucht jetzt nach route=<map_header_id>) UND gleichzeitig fuer
        # potentielle Wild-Route=0-Encounter fake-belegen. -999 haelt die
        # Historie erhalten, macht die Zeile aber fuer Dedupe unerreichbar
        # (Location war zum Log-Zeitpunkt sowieso schon verloren).
        cursor.execute(
            "UPDATE encounters SET route = ? "
            "WHERE route = 0 AND method NOT IN ('wild', 'starter')",
            (LEGACY_GIFT_ROUTE_MARKER,),
        )
        migrated = cursor.rowcount
        cursor.execute(
            "INSERT INTO schema_version (version, applied_at) VALUES (?, ?)",
            (6, datetime.now(timezone.utc).isoformat()),
        )
        self.logger.info(
            f"PokedexDB Schema v6 angewendet (legacy gift-route migration): "
            f"{migrated} Zeilen auf route={LEGACY_GIFT_ROUTE_MARKER} umgeschrieben"
        )

    def _apply_v7(self, cursor):
        # Legacy-Bag-Rows aus Sessions vor der build_owner-Umstellung nuken.
        # Frueher schrieb update_bag den owner als plain str(player_id)
        # ('1', '2', ...); seit dem Nuzlocke-Ball-Fix ist der Owner
        # build_owner-Format 'your_name_slot' ('Bothhaft_1'). Rohe Nummern
        # bleiben aber in der DB und erzeugen im BagMenu Doppel-Sections
        # (Screenshot Bothhaft-Session 2026-09-16). Rein-numerische Owner
        # koennen nur legacy sein — build_owner haengt IMMER '_slot' an,
        # selbst bei leerem your_name (dann '_1').
        # Strikt rein-numerisch: owner beginnt mit Ziffer UND enthaelt
        # keinen Nicht-Ziffer-Character. Fangt "1", "42", ignoriert
        # "Bothhaft_1" (Underscore) und "1abc" (Buchstaben).
        cursor.execute(
            "DELETE FROM bag_inventory "
            "WHERE owner GLOB '[0-9]*' AND owner NOT GLOB '*[^0-9]*'"
        )
        inv_deleted = cursor.rowcount
        cursor.execute(
            "DELETE FROM bag_first_seen "
            "WHERE owner GLOB '[0-9]*' AND owner NOT GLOB '*[^0-9]*'"
        )
        fs_deleted = cursor.rowcount
        cursor.execute(
            "INSERT INTO schema_version (version, applied_at) VALUES (?, ?)",
            (7, datetime.now(timezone.utc).isoformat()),
        )
        self.logger.info(
            f"PokedexDB Schema v7 angewendet (legacy plain-numeric bag-owner cleanup): "
            f"bag_inventory={inv_deleted}, bag_first_seen={fs_deleted}"
        )

    def _apply_v8(self, cursor):
        # Gen-4-Wild-Encounter: route war bis 2026-10-28 die rohe map_id
        # (met_location des Gegners ist im Kampf 0, bizhawk fiel auf die
        # map_id zurueck). Richtig ist die Section (= met_location-Namespace)
        # -> per Decomp-LUT umrechnen. Nur method='wild': Gifts tragen meist
        # schon die met_location; Gift-Zeilen mit map_id-Fallback (route 0)
        # sind davon nicht trennbar und bleiben — process_gift_encounter prueft
        # beim Dedupe deshalb Section UND alte map_id. Einmalig ueber
        # schema_version; unbekannte map_ids/Mystery Zone bleiben unveraendert.
        from backend import bh_pointers
        missing = [ed for ed in range(41, 46) if not bh_pointers.has_map_sections(ed)]
        if missing:
            # LUT nicht geladen (Datei fehlt/kaputt): v8 NICHT eintragen, damit
            # der naechste Start erneut migriert statt dauerhaft zu ueberspringen.
            self.logger.error(
                f"PokedexDB Schema v8 uebersprungen: mapid_section_gen4.yml ohne "
                f"Tabelle fuer edition={missing} — Retry beim naechsten Start"
            )
            return
        cursor.execute(
            "SELECT rowid, edition, route FROM encounters "
            "WHERE edition BETWEEN 41 AND 45 AND method = 'wild'"
        )
        rows = cursor.fetchall()
        migrated = 0
        unknown = 0
        for rowid, edition, route in rows:
            section = bh_pointers.get_map_section(int(edition), int(route))
            if not section:  # unbekannt oder Mystery Zone (0) wie bizhawk.py
                unknown += 1
                continue
            if section != route:
                cursor.execute(
                    "UPDATE encounters SET route = ? WHERE rowid = ?",
                    (section, rowid),
                )
                migrated += 1
        cursor.execute(
            "INSERT INTO schema_version (version, applied_at) VALUES (?, ?)",
            (8, datetime.now(timezone.utc).isoformat()),
        )
        self.logger.info(
            f"PokedexDB Schema v8 angewendet (Gen-4-Wild-route map_id -> Section): "
            f"{migrated} von {len(rows)} Zeilen umgeschrieben, {unknown} unbekannte map_id"
        )

    def _apply_v9(self, cursor):
        # Run-weite Regel-Marker pro (owner, edition), z.B.
        # 'player_used_dragon_rage' fuer die Drachenwut-Klausel. Wird bei
        # Run-Ende (Munchlax._finalize_run) und Session-Reset geleert.
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS rule_flags (
                owner   TEXT NOT NULL,
                edition TEXT NOT NULL,
                flag    TEXT NOT NULL,
                set_at  TEXT NOT NULL,
                PRIMARY KEY (owner, edition, flag)
            )
        """)
        cursor.execute(
            "INSERT INTO schema_version (version, applied_at) VALUES (?, ?)",
            (9, datetime.now(timezone.utc).isoformat()),
        )
        self.logger.info("PokedexDB Schema v9 angewendet (rule_flags)")

    def _apply_v10(self, cursor):
        # Geschlecht + Form pro Encounter (Sprites im Encounter-Screen).
        # Idempotent: Spalten nur anlegen, wenn sie fehlen (Tests spielen
        # Migrationen erneut ab).
        cursor.execute("PRAGMA table_info(encounters)")
        columns = {row[1] for row in cursor.fetchall()}
        if "female" not in columns:
            cursor.execute(
                "ALTER TABLE encounters ADD COLUMN female INTEGER NOT NULL DEFAULT 0"
            )
        if "form" not in columns:
            cursor.execute(
                "ALTER TABLE encounters ADD COLUMN form TEXT NOT NULL DEFAULT ''"
            )

        # Backfill alter Zeilen:
        # 1) Pokemon liegt in der pokemon-Tabelle (gefangen/erhalten) mit
        #    gleicher PV und gleicher Spezies -> female + form von dort.
        #    pokemon.owner ist lokal str(player_slot), encounters.owner
        #    build_owner-Format 'name_<slot>' -> beide Varianten abgleichen.
        # 2) sonst (Gen 3–5): female wie die Decoder aus PV + gender_lut,
        #    form bleibt '' (ohne Pokemon-Bytes nicht rekonstruierbar).
        # Gen 6/7 speichern das Geschlecht explizit (Byte 0x1D), nicht aus der
        # PV — die PV-Formel waere dort falsch, und pokemon.female wurde vor
        # dem Decoder-Fix ebenfalls per PV berechnet. Fuer Gen 6/7 deshalb nur
        # die Form uebernehmen, female bleibt Default (0).
        try:
            from backend.pokedecoder import gender_lut
        except Exception as err:
            gender_lut = {}
            self.logger.warning(
                f"PokedexDB Schema v10: gender_lut nicht ladbar ({type(err).__name__},{err}) "
                f"— PV-Backfill fuer Geschlecht entfaellt"
            )
        cursor.execute(
            "SELECT rowid, personality, owner, edition, dexnr FROM encounters"
        )
        rows = cursor.fetchall()
        from_pokedex = 0
        from_pv = 0
        for rowid, personality, owner, edition, dexnr in rows:
            try:
                gen = int(edition) // 10
            except (TypeError, ValueError):
                gen = 0
            owner_variants = [owner]
            slot = PokedexDB.owner_slot(owner)
            if slot is not None:
                owner_variants.append(str(slot))
            placeholders = ",".join("?" for _ in owner_variants)
            cursor.execute(
                f"SELECT owner, dexnr, female, form FROM pokemon "
                f"WHERE personality = ? AND owner IN ({placeholders})",
                [int(personality)] + owner_variants,
            )
            matches = {r[0]: r for r in cursor.fetchall()}
            match = next((matches[o] for o in owner_variants if o in matches), None)
            if match is not None and str(match[1]) == str(dexnr):
                female_db = int(bool(match[2])) if gen < 6 else 0
                cursor.execute(
                    "UPDATE encounters SET female = ?, form = ? WHERE rowid = ?",
                    (female_db, match[3] or "", rowid),
                )
                from_pokedex += 1
                continue
            try:
                dex_int = int(dexnr)
            except (TypeError, ValueError):
                continue
            if 3 <= gen <= 5 and dex_int in gender_lut:
                female = int(personality) % 256 < gender_lut[dex_int]
                if female:
                    cursor.execute(
                        "UPDATE encounters SET female = 1 WHERE rowid = ?",
                        (rowid,),
                    )
                    from_pv += 1

        cursor.execute(
            "INSERT INTO schema_version (version, applied_at) VALUES (?, ?)",
            (10, datetime.now(timezone.utc).isoformat()),
        )
        self.logger.info(
            f"PokedexDB Schema v10 angewendet (encounters female + form): "
            f"{from_pokedex} von {len(rows)} Zeilen aus pokemon-Tabelle nachgefuellt, "
            f"{from_pv} weitere per PV als weiblich markiert"
        )

    def _apply_v11(self, cursor):
        # Manuelle Encounter-/Link-/Tod-Korrektur: Sperr-Liste + Soft-Delete,
        # Link-Metadaten fuer Persistenz, neue Tabelle soullink_deaths.
        # Idempotent: Spalten nur anlegen, wenn sie fehlen.
        def add_columns(table: str, specs: dict[str, str]) -> None:
            cursor.execute(f"PRAGMA table_info({table})")
            existing = {row[1] for row in cursor.fetchall()}
            for name, ddl in specs.items():
                if name not in existing:
                    cursor.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")

        add_columns("encounters", {
            "manual_fields": "TEXT DEFAULT ''",
            "deleted": "INTEGER DEFAULT 0",
        })
        add_columns("soullink_links", {
            "expected_owners": "TEXT DEFAULT '[]'",
            "manual": "INTEGER DEFAULT 0",
            # Ersttyp-Kompensation schon gutgeschrieben (Arceus-Guard gegen
            # doppelte Token nach Server-Neustart).
            "compensation_paid": "INTEGER DEFAULT 0",
        })
        add_columns("soullink_link_members", {
            "dexnr": "INTEGER",
            "method": "TEXT DEFAULT 'wild'",
            "manual": "INTEGER DEFAULT 0",
        })
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS soullink_deaths (
                personality INTEGER NOT NULL,
                owner       TEXT    NOT NULL,
                edition     INTEGER,
                cause       TEXT,
                timestamp   REAL,
                link_id     INTEGER,
                partners    TEXT DEFAULT '[]',
                dead        INTEGER DEFAULT 1,
                manual      INTEGER DEFAULT 0,
                PRIMARY KEY (personality, owner)
            )
        """)
        cursor.execute(
            "INSERT INTO schema_version (version, applied_at) VALUES (?, ?)",
            (11, datetime.now(timezone.utc).isoformat()),
        )
        self.logger.info(
            "PokedexDB Schema v11 angewendet (manual_fields, deleted, "
            "Link-Metadaten, soullink_deaths)"
        )

    def _apply_v12(self, cursor):
        # Tot-Markierung pro Encounter (Nuzlocke/Soullink). Klebt: die
        # Automatik setzt nur 0→1, zurueck geht es nur manuell (Sperre "dead").
        cursor.execute("PRAGMA table_info(encounters)")
        if "dead" not in {row[1] for row in cursor.fetchall()}:
            cursor.execute("ALTER TABLE encounters ADD COLUMN dead INTEGER DEFAULT 0")
        cursor.execute(
            "INSERT INTO schema_version (version, applied_at) VALUES (?, ?)",
            (12, datetime.now(timezone.utc).isoformat()),
        )
        self.logger.info("PokedexDB Schema v12 angewendet (encounters.dead)")

    def _apply_v13(self, cursor):
        # Keine Tabellenaenderung: v13 markiert nur, AB WANN das Ball-Gate eine
        # ball-freie Tasche im Run verlangt (rule_flags BALL_FREE_FLAG_PREFIX).
        # Runs, die vor diesem Zeitpunkt gestartet sind, behalten die alte
        # Semantik — sonst ginge bei laufenden Runs das Gate nach dem Update zu.
        cursor.execute(
            "INSERT INTO schema_version (version, applied_at) VALUES (?, ?)",
            (13, datetime.now(timezone.utc).isoformat()),
        )
        self.logger.info("PokedexDB Schema v13 angewendet (Ball-Gate scharf erst "
                         "nach ball-freier Tasche)")

    # -- Regel-Marker (Schema v9) ----------------------------------------------

    @_serialized
    def set_rule_flag(self, owner: str, edition, flag: str) -> bool:
        """Setzt einen Run-weiten Regel-Marker (idempotent). True bei Erfolg."""
        if self.connection is None:
            return False
        try:
            edition_str = str(edition) if edition is not None else ""
            self.connection.execute(
                "INSERT OR IGNORE INTO rule_flags (owner, edition, flag, set_at) "
                "VALUES (?, ?, ?, ?)",
                (owner, edition_str, flag, datetime.now(timezone.utc).isoformat()),
            )
            self.connection.commit()
            return True
        except Exception as err:
            self.logger.error(f"set_rule_flag failed: {type(err)},{err}")
            self.logger.error(f"{traceback.format_exc()}")
            return False

    @_serialized
    def has_rule_flag(self, owner: str, edition, flag: str) -> bool:
        if self.connection is None:
            return False
        try:
            edition_str = str(edition) if edition is not None else ""
            cursor = self.connection.execute(
                "SELECT 1 FROM rule_flags WHERE owner = ? AND edition = ? AND flag = ? LIMIT 1",
                (owner, edition_str, flag),
            )
            return cursor.fetchone() is not None
        except Exception as err:
            self.logger.error(f"has_rule_flag failed: {type(err)},{err} — gebe True zurueck")
            self.logger.error(f"{traceback.format_exc()}")
            # Sichere Richtung: im Zweifel gilt der Marker als gesetzt (bei der
            # Drachenwut-Klausel heisst das: Tod zaehlt).
            return True

    @_serialized
    def reset_all_data(self) -> bool:
        """Löscht ALLE Inhalte der Content-Tabellen. Schema + schema_version
        bleiben erhalten. File wird nicht gelöscht — der Aufrufer kann direkt
        weiterarbeiten, ohne connect neu aufzurufen.

        Rückgabe True bei Erfolg. Nach Erfolg fährt sqlite ein VACUUM, damit
        die Datei-Groesse tatsaechlich schrumpft (INSERT-heavy Sessions
        hinterlassen sonst leere Pages).
        """
        if self.connection is None:
            return False
        tables = (
            "soullink_link_members",
            "soullink_links",
            "soullink_deaths",
            "encounters",
            "bag_first_seen",
            "bag_inventory",
            "pokemon",
        )
        try:
            cursor = self.connection.cursor()
            for tbl in tables:
                cursor.execute(f"DELETE FROM {tbl}")
            # Ball-Gate-Marker ueberleben den Reset: der Run (active.txt)
            # laeuft weiter, und hat der Spieler schon Baelle, kaeme nie wieder
            # eine ball-freie Tasche → Gate bliebe fuer immer zu. Marker sind
            # per Run-Start-Zeitstempel gebunden, wirken also nicht in Folge-Runs.
            cursor.execute(
                "DELETE FROM rule_flags WHERE flag NOT LIKE ?",
                (f"{BALL_FREE_FLAG_PREFIX}%",),
            )
            self.connection.commit()
            # VACUUM darf nicht in einer Transaction laufen — isolation_level
            # kurzzeitig auf None schalten, dann restaurieren.
            prev_isolation = self.connection.isolation_level
            self.connection.isolation_level = None
            try:
                cursor.execute("VACUUM")
            finally:
                self.connection.isolation_level = prev_isolation
            self.logger.info(f"PokedexDB reset_all_data: {len(tables)} Tabellen geleert + VACUUM")
            return True
        except Exception as err:
            self.logger.error(f"reset_all_data failed: {type(err)},{err}")
            self.logger.error(f"{traceback.format_exc()}")
            return False

    @staticmethod
    def build_owner(your_name: str, client_id: str) -> str:
        return f"{your_name}_{client_id}"

    @staticmethod
    def owner_root(owner: str) -> str:
        """Extrahiert Root-Namen aus build_owner-Format 'name_<player_slot>'.

        Encounter/DB-Layer verwendet build_owner mit Player-Slot-Suffix, damit
        derselbe Spieler mehrere BizHawk-Instanzen unterscheiden kann. Soullink-
        Konfiguration (expected_owners) und Overlay-Team-Matching arbeiten
        dagegen mit reinen Spielernamen ohne Slot. owner_root strippt den
        letzten '_<int>'-Suffix; ist keiner vorhanden, wird der String
        unverändert zurückgegeben. Rein-numerische Owner (Legacy-Team-DB nutzt
        str(player_id) direkt) bleiben ebenfalls unverändert.
        """
        if not owner:
            return owner
        idx = owner.rfind("_")
        if idx <= 0 or idx == len(owner) - 1:
            return owner
        suffix = owner[idx + 1:]
        if not suffix.isdigit():
            return owner
        return owner[:idx]

    @staticmethod
    def owner_slot(owner: str) -> int | None:
        """Komplement zu ``owner_root``: liefert den Slot-Int aus
        ``name_<player_slot>``, None wenn kein numerischer Suffix.

        Nutzen: Konsumenten wie bagmenu brauchen aus einem DB-owner-String
        den player_id, um munchlax.rando_tm_moves[pid] zu lookupen. int(owner)
        geht nicht, weil der Owner ``Bothhaft_1`` lautet (siehe
        ``feedback_owner_slot_extract``).
        """
        if not owner:
            return None
        idx = owner.rfind("_")
        if idx <= 0 or idx == len(owner) - 1:
            return None
        suffix = owner[idx + 1:]
        if not suffix.isdigit():
            return None
        try:
            return int(suffix)
        except ValueError:
            return None

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
    def get_lvls_by_personalities(self, exp_by_pv: dict[int, int | None]) -> dict[int, int]:
        """Liefert pro PV das zuletzt gespeicherte Level (last_seen DESC).

        Wird vom Munchlax beim Box-Refresh genutzt, um das aus EXP berechnete
        Box-Level durch das echte Team-Level zu ersetzen — sofern dasselbe
        Pokemon (per PV) schon mal im Team war UND seine EXP seitdem gleich
        geblieben ist. Sonst (z.B. Level im Pokemon-Editor geändert) wäre das
        DB-Level veraltet. Gen 1/2 hat keine PV und landet ohnehin nie in der
        pokemon.db (siehe upsert_pokemon).
        """
        if self.connection is None or not exp_by_pv:
            return {}
        result: dict[int, int] = {}
        try:
            cursor = self.connection.cursor()
            for pv, exp in exp_by_pv.items():
                cursor.execute(
                    "SELECT lvl, exp FROM pokemon WHERE personality = ? "
                    "AND lvl IS NOT NULL ORDER BY last_seen DESC LIMIT 1",
                    (int(pv),),
                )
                row = cursor.fetchone()
                if row and row["lvl"] is not None and exp is not None and row["exp"] == exp:
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
    def mark_bag_ball_free(self, owner: str, edition, since_ts: str) -> bool | None:
        """Merkt sich: im Run (since_ts) wurde eine Tasche OHNE Fang-Ball gesehen.

        Idempotent (INSERT OR IGNORE) — set_at bleibt der ERSTE ball-freie
        Read, has_catching_balls zaehlt nur Baelle ab dort. True = neu gesetzt,
        False = existierte schon, None = nicht geschrieben (keine DB/Fehler) —
        der Aufrufer darf dann nicht als "erledigt" cachen.
        """
        if self.connection is None or not since_ts:
            return None
        try:
            edition_str = str(edition) if edition is not None else ""
            cursor = self.connection.execute(
                "INSERT OR IGNORE INTO rule_flags (owner, edition, flag, set_at) "
                "VALUES (?, ?, ?, ?)",
                (owner, edition_str, f"{BALL_FREE_FLAG_PREFIX}{str(since_ts)[:19]}",
                 datetime.now(timezone.utc).isoformat()),
            )
            self.connection.commit()
            return cursor.rowcount > 0
        except Exception as err:
            self.logger.error(f"mark_bag_ball_free failed: {type(err)},{err}")
            self.logger.error(f"{traceback.format_exc()}")
            return None

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
                         outcome: str = "unknown",
                         female: bool = False,
                         form: str = "") -> bool:
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
                    method, outcome, timestamp, female, form
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    int(personality), owner, int(edition), int(route),
                    int(dexnr), int(lvl), int(shiny),
                    int(is_first), int(is_shiny_override),
                    int(is_dupes_skip), int(has_balls),
                    method, outcome, now, int(bool(female)), form or "",
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
                                  outcome: str, automatic: bool = True) -> bool:
        """Setzt das outcome. ``automatic=True`` respektiert die Sperre
        ``outcome`` in ``manual_fields`` (kein Update, False)."""
        if self.connection is None:
            return False
        try:
            cursor = self.connection.cursor()
            if automatic:
                cursor.execute(
                    "SELECT manual_fields FROM encounters "
                    "WHERE personality = ? AND owner = ?",
                    (int(personality), owner),
                )
                row = cursor.fetchone()
                if row is not None and "outcome" in parse_locks(row[0]):
                    self.logger.info(
                        f"update_encounter_outcome: pv={personality} owner={owner} "
                        f"outcome={outcome} ignoriert (manuell gesperrt)"
                    )
                    return False
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
    def mark_encounter_dead(self, personality: int, owner: str,
                            automatic: bool = True) -> bool:
        """Setzt dead=1 (nur 0→1). ``automatic=True`` respektiert die Sperre
        ``dead`` (manuell lebendig). True nur, wenn sich etwas geaendert hat."""
        if self.connection is None:
            return False
        try:
            cursor = self.connection.cursor()
            cursor.execute(
                "SELECT manual_fields, dead FROM encounters "
                "WHERE personality = ? AND owner = ?",
                (int(personality), owner),
            )
            row = cursor.fetchone()
            if row is None:
                # Ohne Encounter-Zeile kein Ausgrauen (z.B. Pokemon vor Run-Start)
                self.logger.debug(
                    f"mark_encounter_dead: pv={personality} owner={owner} ohne Encounter")
                return False
            if row[1]:
                return False
            if automatic and "dead" in parse_locks(row[0]):
                self.logger.info(
                    f"mark_encounter_dead: pv={personality} owner={owner} "
                    f"ignoriert (manuell lebendig)"
                )
                return False
            cursor.execute(
                "UPDATE encounters SET dead = 1 WHERE personality = ? AND owner = ?",
                (int(personality), owner),
            )
            self.connection.commit()
            return cursor.rowcount > 0
        except Exception as err:
            self.logger.error(f"mark_encounter_dead failed: {type(err)},{err}")
            self.logger.error(f"{traceback.format_exc()}")
            return False

    @_serialized
    def sync_encounter(self, enc: dict) -> bool:
        """INSERT OR REPLACE für Remote-Encounters. Überschreibt bei PK-Kollision."""
        if self.connection is None:
            return False
        try:
            cursor = self.connection.cursor()
            # Clients vor Schema v10/v11 senden female/form/manual_fields/deleted
            # nicht mit — dann die vorhandenen Werte der Zeile behalten statt sie
            # per REPLACE auf den Default zurückzusetzen.
            # Tot klebt: eine Zeile mit dead=1 wird nur zurueckgesetzt, wenn
            # der Payload die Sperre "dead" traegt (manuell lebendig).
            if not enc.get("dead") and "dead" not in parse_locks(enc.get("manual_fields")):
                cursor.execute(
                    "SELECT dead FROM encounters WHERE personality = ? AND owner = ?",
                    (int(enc["personality"]), enc["owner"]),
                )
                row = cursor.fetchone()
                if row is not None and row[0]:
                    enc = dict(enc, dead=1)
            if any(k not in enc for k in ("female", "form", "manual_fields", "deleted", "dead")):
                cursor.execute(
                    "SELECT female, form, manual_fields, deleted, dead FROM encounters "
                    "WHERE personality = ? AND owner = ?",
                    (int(enc["personality"]), enc["owner"]),
                )
                existing = cursor.fetchone()
                if existing is not None:
                    enc = dict(enc)
                    enc.setdefault("female", existing[0])
                    enc.setdefault("form", existing[1])
                    enc.setdefault("manual_fields", existing[2])
                    enc.setdefault("deleted", existing[3])
                    enc.setdefault("dead", existing[4])
            cursor.execute(
                """
                INSERT OR REPLACE INTO encounters (
                    personality, owner, edition, route, dexnr, lvl, shiny,
                    is_first, is_shiny_override, is_dupes_skip, has_balls,
                    method, outcome, timestamp, female, form,
                    manual_fields, deleted, dead
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    int(enc["personality"]), enc["owner"], int(enc["edition"]),
                    int(enc["route"]), int(enc["dexnr"]), int(enc["lvl"]),
                    int(enc.get("shiny", 0)),
                    int(enc.get("is_first", 0)), int(enc.get("is_shiny_override", 0)),
                    int(enc.get("is_dupes_skip", 0)), int(enc.get("has_balls", 0)),
                    enc.get("method", "wild"), enc.get("outcome", "unknown"),
                    enc.get("timestamp", ""),
                    int(bool(enc.get("female", 0))), str(enc.get("form") or ""),
                    str(enc.get("manual_fields") or ""),
                    int(bool(enc.get("deleted", 0))),
                    int(bool(enc.get("dead", 0))),
                ),
            )
            self.connection.commit()
            return cursor.rowcount > 0
        except Exception as err:
            self.logger.error(f"sync_encounter failed: {type(err)},{err}")
            self.logger.error(f"{traceback.format_exc()}")
            return False

    @_serialized
    def encounter_import_context(self, owner: str, edition: int) -> tuple[set, set]:
        """Fuer den Import: alle PVs mit Encounter-Zeile dieses Owners (auch
        geloeschte, damit sie nicht wieder auftauchen) und Wild-Routen, die
        schon einen First-Encounter haben (gleiche Filter wie
        has_encounter_on_route, ohne has_balls)."""
        if self.connection is None:
            return set(), set()
        try:
            cursor = self.connection.cursor()
            cursor.execute("SELECT personality FROM encounters WHERE owner = ?", (owner,))
            pvs = {row[0] for row in cursor.fetchall()}
            cursor.execute(
                "SELECT DISTINCT route FROM encounters WHERE owner = ? AND edition = ? "
                "AND is_first = 1 AND method = 'wild' AND deleted = 0",
                (owner, int(edition)),
            )
            routes = {row[0] for row in cursor.fetchall()}
            return pvs, routes
        except Exception as err:
            self.logger.error(f"encounter_import_context failed: {type(err)},{err}")
            self.logger.error(f"{traceback.format_exc()}")
            return set(), set()

    @_serialized
    def insert_imported_encounters(self, encounters: list[dict]) -> list[dict]:
        """INSERT OR IGNORE fuer importierte Encounter — bestehende Zeilen
        (auch geloeschte) werden nie ueberschrieben. Gibt die tatsaechlich
        eingefuegten zurueck."""
        if self.connection is None:
            return []
        inserted: list[dict] = []
        try:
            cursor = self.connection.cursor()
            for enc in encounters:
                cursor.execute(
                    """
                    INSERT OR IGNORE INTO encounters (
                        personality, owner, edition, route, dexnr, lvl, shiny,
                        is_first, is_shiny_override, is_dupes_skip, has_balls,
                        method, outcome, timestamp, female, form,
                        manual_fields, deleted, dead
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        int(enc["personality"]), enc["owner"], int(enc["edition"]),
                        int(enc["route"]), int(enc["dexnr"]), int(enc["lvl"]),
                        int(enc.get("shiny", 0)),
                        int(enc.get("is_first", 0)), int(enc.get("is_shiny_override", 0)),
                        int(enc.get("is_dupes_skip", 0)), int(enc.get("has_balls", 0)),
                        enc.get("method", "wild"), enc.get("outcome", "caught"),
                        enc.get("timestamp", ""),
                        int(bool(enc.get("female", 0))), str(enc.get("form") or ""),
                        "", 0, 0,
                    ),
                )
                if cursor.rowcount > 0:
                    inserted.append(enc)
            self.connection.commit()
        except Exception as err:
            self.connection.rollback()
            self.logger.error(f"insert_imported_encounters failed: {type(err)},{err}")
            self.logger.error(f"{traceback.format_exc()}")
            return []
        return inserted

    @_serialized
    def get_encounter(self, personality: int, owner: str) -> dict | None:
        """Eine Encounter-Zeile (inkl. deleted) als dict, sonst None."""
        if self.connection is None:
            return None
        try:
            cursor = self.connection.cursor()
            cursor.execute(
                "SELECT * FROM encounters WHERE personality = ? AND owner = ?",
                (int(personality), owner),
            )
            row = cursor.fetchone()
            return dict(row) if row is not None else None
        except Exception as err:
            self.logger.error(f"get_encounter failed: {type(err)},{err}")
            self.logger.error(f"{traceback.format_exc()}")
            return None

    @_serialized
    def find_encounter_by_root(self, personality: int, owner_root: str) -> dict | None:
        """Encounter zu ``personality`` mit passendem Root-Owner ('name' zu
        'name_<slot>'). Für Server-Meldungen, deren Owner keinen Slot trägt
        (Soullink-Death-Broadcast). Nicht gelöschte Zeilen bevorzugt."""
        if self.connection is None:
            return None
        try:
            cursor = self.connection.cursor()
            cursor.execute("SELECT * FROM encounters WHERE personality = ?",
                           (int(personality),))
            rows = [dict(r) for r in cursor.fetchall()
                    if self.owner_root(r["owner"]) == owner_root]
            rows.sort(key=lambda r: bool(r.get("deleted")))
            return rows[0] if rows else None
        except Exception as err:
            self.logger.error(f"find_encounter_by_root failed: {type(err)},{err}")
            self.logger.error(f"{traceback.format_exc()}")
            return None

    @_serialized
    def apply_encounter_edit(self, personality: int, owner: str, changes: dict,
                             unlock: list[str] | None = None,
                             delete: bool | None = None) -> dict | None:
        """Manuelle Korrektur: Zeile laden, mergen, zurückschreiben.
        Gibt die neue Zeile zurück (None bei Fehler / unbekannter Zeile).
        Verschachtelte @_serialized-Aufrufe sind ok (access_lock ist ein RLock)."""
        enc = self.get_encounter(personality, owner)
        if enc is None:
            return None
        merged = merge_encounter_edit(enc, changes, unlock, delete)
        if not self.sync_encounter(merged):
            return None
        return self.get_encounter(personality, owner)

    @_serialized
    def rebind_encounter(self, owner: str, old_pv: int, new_pv: int) -> bool:
        """Pseudo-PV eines manuellen Encounters durch die echte PV ersetzen
        (Auto-Bindung). False, wenn es die alte Zeile nicht gibt oder unter der
        neuen PV schon ein Encounter liegt."""
        if self.connection is None:
            return False
        try:
            cursor = self.connection.cursor()
            cursor.execute("SELECT 1 FROM encounters WHERE personality = ? AND owner = ?",
                           (int(new_pv), owner))
            if cursor.fetchone() is not None:
                self.logger.info(f"rebind_encounter: pv={new_pv} owner={owner} existiert bereits")
                return False
            cursor.execute("UPDATE encounters SET personality = ? WHERE personality = ? AND owner = ?",
                           (int(new_pv), int(old_pv), owner))
            self.connection.commit()
            ok = cursor.rowcount > 0
            self.logger.info(f"rebind_encounter: owner={owner} {old_pv} -> {new_pv} ok={ok}")
            return ok
        except Exception as err:
            self.logger.error(f"rebind_encounter failed: {type(err)},{err}")
            self.logger.error(f"{traceback.format_exc()}")
            return False

    @_serialized
    def load_all_encounters(self, raise_errors: bool = False) -> list[dict]:
        """Alle Zeilen inkl. deleted (Arceus-Cache)."""
        if self.connection is None:
            if raise_errors:
                raise RuntimeError("PokedexDB nicht verbunden")
            return []
        try:
            cursor = self.connection.cursor()
            cursor.execute("SELECT * FROM encounters ORDER BY timestamp")
            return [dict(row) for row in cursor.fetchall()]
        except Exception as err:
            self.logger.error(f"load_all_encounters failed: {type(err)},{err}")
            self.logger.error(f"{traceback.format_exc()}")
            if raise_errors:
                raise
            return []

    # -- Soullink-Links / -Tode persistieren (Schema v11) ---------------------

    @_serialized
    def save_link(self, link: dict) -> bool:
        """Upsert einer Link-Gruppe; Members werden komplett ersetzt."""
        if self.connection is None:
            return False
        try:
            now = datetime.now(timezone.utc).isoformat()
            link_id = int(link["link_id"])
            cursor = self.connection.cursor()
            cursor.execute(
                "SELECT created_at FROM soullink_links WHERE link_id = ?",
                (link_id,),
            )
            row = cursor.fetchone()
            created_at = row[0] if row is not None else now
            cursor.execute(
                """
                INSERT OR REPLACE INTO soullink_links (
                    link_id, route, edition_gen, link_index, state,
                    created_at, updated_at, expected_owners, manual,
                    compensation_paid
                ) VALUES (?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    link_id, int(link.get("route", 0)),
                    int(link.get("edition_gen", 0)),
                    int(link.get("link_index", 1)),
                    link.get("state", "pending"), created_at, now,
                    json.dumps(list(link.get("expected_owners") or [])),
                    int(bool(link.get("manual", 0))),
                    int(bool(link.get("compensation_paid", False))),
                ),
            )
            cursor.execute(
                "DELETE FROM soullink_link_members WHERE link_id = ?", (link_id,)
            )
            for owner, member in (link.get("members") or {}).items():
                dexnr = member.get("dexnr")
                cursor.execute(
                    """
                    INSERT INTO soullink_link_members (
                        link_id, owner, personality, edition, outcome,
                        dexnr, method, manual
                    ) VALUES (?,?,?,?,?,?,?,?)
                    """,
                    (
                        link_id, owner, int(member["personality"]),
                        int(member.get("edition") or 0),
                        member.get("outcome", "unknown"),
                        int(dexnr) if dexnr is not None else None,
                        member.get("method", "wild"),
                        int(bool(member.get("manual", 0))),
                    ),
                )
            self.connection.commit()
            return True
        except Exception as err:
            self.logger.error(f"save_link failed: {type(err)},{err}")
            self.logger.error(f"{traceback.format_exc()}")
            return False

    @_serialized
    def delete_link(self, link_id: int) -> bool:
        if self.connection is None:
            return False
        try:
            cursor = self.connection.cursor()
            cursor.execute(
                "DELETE FROM soullink_link_members WHERE link_id = ?", (int(link_id),)
            )
            cursor.execute(
                "DELETE FROM soullink_links WHERE link_id = ?", (int(link_id),)
            )
            self.connection.commit()
            return True
        except Exception as err:
            self.logger.error(f"delete_link failed: {type(err)},{err}")
            self.logger.error(f"{traceback.format_exc()}")
            return False

    @_serialized
    def load_links(self, raise_errors: bool = False) -> dict[int, dict]:
        """Links in der Struktur von ``Arceus.soullink_links``."""
        if self.connection is None:
            if raise_errors:
                raise RuntimeError("PokedexDB nicht verbunden")
            return {}
        try:
            cursor = self.connection.cursor()
            cursor.execute("SELECT * FROM soullink_links ORDER BY link_id")
            links: dict[int, dict] = {}
            for row in cursor.fetchall():
                try:
                    expected = json.loads(row["expected_owners"] or "[]")
                except (TypeError, ValueError):
                    expected = []
                links[row["link_id"]] = {
                    "link_id": row["link_id"],
                    "route": row["route"],
                    "edition_gen": row["edition_gen"],
                    "link_index": row["link_index"],
                    "state": row["state"],
                    "expected_owners": expected,
                    "manual": int(row["manual"] or 0),
                    "members": {},
                }
                if "compensation_paid" in row.keys() and row["compensation_paid"]:
                    links[row["link_id"]]["compensation_paid"] = True
            cursor.execute("SELECT * FROM soullink_link_members")
            for row in cursor.fetchall():
                link = links.get(row["link_id"])
                if link is None:
                    continue
                link["members"][row["owner"]] = {
                    "personality": row["personality"],
                    "edition": row["edition"],
                    "outcome": row["outcome"],
                    "dexnr": row["dexnr"],
                    "method": row["method"] or "wild",
                    "manual": int(row["manual"] or 0),
                }
            return links
        except Exception as err:
            self.logger.error(f"load_links failed: {type(err)},{err}")
            self.logger.error(f"{traceback.format_exc()}")
            if raise_errors:
                raise
            return {}

    @_serialized
    def clear_links(self) -> bool:
        if self.connection is None:
            return False
        try:
            self.connection.execute("DELETE FROM soullink_link_members")
            self.connection.execute("DELETE FROM soullink_links")
            self.connection.commit()
            return True
        except Exception as err:
            self.logger.error(f"clear_links failed: {type(err)},{err}")
            self.logger.error(f"{traceback.format_exc()}")
            return False

    @_serialized
    def save_death(self, death: dict) -> bool:
        if self.connection is None:
            return False
        try:
            self.connection.execute(
                """
                INSERT OR REPLACE INTO soullink_deaths (
                    personality, owner, edition, cause, timestamp,
                    link_id, partners, dead, manual
                ) VALUES (?,?,?,?,?,?,?,?,?)
                """,
                (
                    int(death["personality"]), death["owner"],
                    death.get("edition"), death.get("cause"),
                    death.get("timestamp"), death.get("link_id"),
                    json.dumps(list(death.get("partners") or [])),
                    int(bool(death.get("dead", 1))),
                    int(bool(death.get("manual", 0))),
                ),
            )
            self.connection.commit()
            return True
        except Exception as err:
            self.logger.error(f"save_death failed: {type(err)},{err}")
            self.logger.error(f"{traceback.format_exc()}")
            return False

    @_serialized
    def delete_death(self, personality: int, owner: str) -> bool:
        if self.connection is None:
            return False
        try:
            self.connection.execute(
                "DELETE FROM soullink_deaths WHERE personality = ? AND owner = ?",
                (int(personality), owner),
            )
            self.connection.commit()
            return True
        except Exception as err:
            self.logger.error(f"delete_death failed: {type(err)},{err}")
            self.logger.error(f"{traceback.format_exc()}")
            return False

    @_serialized
    def load_deaths(self, raise_errors: bool = False) -> dict[tuple[int, str], dict]:
        """Tode in der Struktur von ``Arceus.soullink_deaths``."""
        if self.connection is None:
            if raise_errors:
                raise RuntimeError("PokedexDB nicht verbunden")
            return {}
        try:
            cursor = self.connection.cursor()
            cursor.execute("SELECT * FROM soullink_deaths")
            deaths: dict[tuple[int, str], dict] = {}
            for row in cursor.fetchall():
                try:
                    partners = json.loads(row["partners"] or "[]")
                except (TypeError, ValueError):
                    partners = []
                deaths[(row["personality"], row["owner"])] = {
                    "personality": row["personality"],
                    "owner": row["owner"],
                    "edition": row["edition"],
                    "cause": row["cause"],
                    "timestamp": row["timestamp"],
                    "partners": partners,
                    "link_id": row["link_id"],
                    "dead": int(row["dead"]) if row["dead"] is not None else 1,
                    "manual": int(row["manual"] or 0),
                }
            return deaths
        except Exception as err:
            self.logger.error(f"load_deaths failed: {type(err)},{err}")
            self.logger.error(f"{traceback.format_exc()}")
            if raise_errors:
                raise
            return {}

    @_serialized
    def clear_deaths(self) -> bool:
        if self.connection is None:
            return False
        try:
            self.connection.execute("DELETE FROM soullink_deaths")
            self.connection.commit()
            return True
        except Exception as err:
            self.logger.error(f"clear_deaths failed: {type(err)},{err}")
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
                "FROM encounters WHERE owner = ? AND deleted = 0"
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
        Manuell geloeschte Zeilen (deleted=1) zaehlen bewusst mit — sonst
        wuerde nach Loeschen des Starters das naechste Team-Wachstum
        erneut als Starter erkannt.
        """
        if self.connection is None:
            return False
        try:
            cursor = self.connection.cursor()
            cursor.execute(
                "SELECT 1 FROM encounters "
                "WHERE owner = ? AND edition = ? LIMIT 1",
                (owner, int(edition)),
            )
            return cursor.fetchone() is not None
        except Exception as err:
            self.logger.error(f"has_any_encounter failed: {type(err)},{err}")
            self.logger.error(f"{traceback.format_exc()}")
            return False

    @_serialized
    def has_encounter_with_method_at_route(self, owner: str, edition: int,
                                             route: int, method: str,
                                             dexnr: int | None = None) -> bool:
        """True wenn (owner, edition, route, method) bereits einen Encounter hat.

        Dedupe-Basis für non-repeatable Gift-/Static-/Fossil-Encounter: das
        Gift-YAML sagt "einmal pro Spieler an dieser Map", das ausnutzen wir
        über die Kombination (edition, route, method). route enthält für
        Gift-Encounter jetzt die map_header_id (statt 0), damit
        unterschiedliche Gift-Locations sich nicht gegenseitig blockieren.
        Ohne diesen Check würde jeder neue PV am selben Ort erneut als
        derselbe Gift eingebucht (Bug: Wild-Pokemon fielen durch, weil
        _read_encounter_data nicht gefeuert hat).

        ``dexnr`` (optional) engt auf eine Species ein — Static-Kaempfe
        teilen sich die MAPSEC (Kraftwerk: Lektrobal + Zapdos), deshalb
        dedupliziert der Static-Pfad pro (Ort, Species).
        """
        if self.connection is None:
            return False
        try:
            cursor = self.connection.cursor()
            sql = ("SELECT 1 FROM encounters "
                   "WHERE owner = ? AND edition = ? AND route = ? AND method = ? "
                   "  AND deleted = 0 ")
            params: tuple = (owner, int(edition), int(route), method)
            if dexnr is not None:
                sql += "  AND dexnr = ? "
                params += (int(dexnr),)
            cursor.execute(sql + "LIMIT 1", params)
            return cursor.fetchone() is not None
        except Exception as err:
            self.logger.error(f"has_encounter_with_method_at_route failed: {type(err)},{err}")
            self.logger.error(f"{traceback.format_exc()}")
            return False

    @_serialized
    def has_encounter_on_route(self, owner: str, edition: int,
                               route: int) -> bool:
        """True wenn eine Wild-Route bereits einen First-Encounter hat.

        Filtert:
          - `is_first = 1`: dupes-Skips und Retry-Zeilen (is_first=0) zaehlen
            NICHT als Route-belegt, die Route bleibt fuer den echten
            First-Encounter frei.
          - `has_balls = 1`: nur Route-Belegungen nach Run-Start.
          - `method = 'wild'`: Gift/Fossil/Static/Egg/Token belegen KEINE
            Wild-Route. Dies ist notwendig, seit process_gift_encounter die
            `map_header_id` in der `route`-Spalte speichert (statt 0) — der
            Zahlenraum ueberlappt mit Gen-3-MAPSEC-IDs (z.B. mapLayoutId 58
            = Prof.-Birch-Lab-Starter vs MAPSEC 58 = Safari-Zone). Ohne den
            method-Filter wuerde ein Starter-Log den Wild-Route-Slot der
            Safari-Zone belegen (oder umgekehrt).
        """
        if self.connection is None:
            return False
        try:
            cursor = self.connection.cursor()
            cursor.execute(
                "SELECT 1 FROM encounters "
                "WHERE owner = ? AND edition = ? AND route = ? "
                "  AND has_balls = 1 AND is_first = 1 "
                "  AND method = 'wild' AND deleted = 0 "
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
            # pokemon.owner ist str(player_slot) (Munchlax._persist_teams),
            # Aufrufer reichen den Encounter-Owner 'name_<slot>' → beide
            # Varianten abfragen. Slots sind sessionweit eindeutig (P1–P4).
            owner_variants = [owner]
            slot = PokedexDB.owner_slot(owner)
            if slot is not None:
                owner_variants.append(str(slot))
            owner_placeholders = ",".join("?" for _ in owner_variants)
            placeholders = ",".join("?" for _ in family_members)
            cursor = self.connection.cursor()
            cursor.execute(
                f"SELECT 1 FROM pokemon "
                f"WHERE owner IN ({owner_placeholders}) AND dexnr IN ({placeholders}) "
                f"LIMIT 1",
                owner_variants + [str(d) for d in family_members],
            )
            return cursor.fetchone() is not None
        except Exception as err:
            self.logger.error(f"is_species_family_caught failed: {type(err)},{err}")
            self.logger.error(f"{traceback.format_exc()}")
            return False

    @_serialized
    def has_catching_balls(self, owner: str, edition,
                           since_ts: str | None = None) -> bool:
        """Prüft ob der Spieler JEMALS einen Ball besessen hat (bag_first_seen).

        Bestimmt ob der Nuzlocke-Run gestartet hat — einmal True, bleibt True.

        Geprueft wird per Item-ID (catching_ball_ids) ueber alle Pockets statt
        ueber die 'baelle'-Pocket: Gen 1 hat nur eine Tasche, Gen 5–7 legen
        Baelle in 'items' ab — dort gab es nie eine 'baelle'-Pocket.

        since_ts (Start des aktiven Runs, run_meta 'YYYY-MM-DDTHH:MM:SSZ'):
        nur Baelle ab Run-Start zaehlen. bag_first_seen ueberlebt Run-Ende
        (INSERT OR IGNORE), deshalb zusaetzlich der aktuelle Bestand
        (bag_inventory.last_seen) — ein Ball, der schon im Vor-Run gesehen
        wurde und jetzt wieder in der Tasche liegt, oeffnet das Gate auch.
        Verglichen werden die ersten 19 Zeichen (Formate: ISO mit +00:00
        vs. ...Z, beide UTC).

        Scharf-Schwelle (Schema v13, Testrunde 2026-11-10): mit since_ts zaehlen
        Baelle erst, nachdem im Run eine ball-freie Tasche gesehen wurde
        (mark_bag_ball_free). Smaragd/Feuerrot laden am Titelbild/Menue den
        ALTEN Spielstand samt Baellen und Party — "Neues Spiel" darueber haette
        das Gate sonst sofort geoeffnet. Ein neues Spiel startet immer mit
        leerer Tasche, der Marker entsteht also im normalen Ablauf. Runs, die
        vor v13 gestartet sind, behalten die alte Semantik (kein Marker noetig).
        """
        if self.connection is None:
            # Geschlossene DB = Gate zu; vorher still (Live-Test 2026-11-04)
            self.logger.warning(f"has_catching_balls: keine DB-Verbindung "
                                f"(owner={owner}, edition={edition}) → Gate zu")
            return False
        ball_ids = catching_ball_ids(edition)
        if not ball_ids:
            return False
        try:
            edition_str = str(edition) if edition is not None else ""
            placeholders = ",".join("?" * len(ball_ids))
            cursor = self.connection.cursor()
            ids = sorted(ball_ids)
            if not since_ts:
                cursor.execute(
                    "SELECT 1 FROM bag_first_seen "
                    f"WHERE owner = ? AND edition = ? AND item_id IN ({placeholders}) "
                    "LIMIT 1",
                    (owner, edition_str, *ids),
                )
                return cursor.fetchone() is not None
            since = str(since_ts)[:19]
            cursor.execute(
                "SELECT applied_at FROM schema_version WHERE version = 13")
            row = cursor.fetchone()
            v13_at = str(row[0])[:19] if row and row[0] else ""
            if v13_at and since >= v13_at:
                cursor.execute(
                    "SELECT set_at FROM rule_flags "
                    "WHERE owner = ? AND edition = ? AND flag = ? LIMIT 1",
                    (owner, edition_str, f"{BALL_FREE_FLAG_PREFIX}{since}"),
                )
                armed = cursor.fetchone()
                if armed is None:
                    return False
                since = max(since, str(armed[0])[:19])
            cursor.execute(
                "SELECT 1 FROM bag_first_seen "
                f"WHERE owner = ? AND edition = ? AND item_id IN ({placeholders}) "
                "AND substr(first_seen, 1, 19) >= ? "
                "UNION ALL "
                "SELECT 1 FROM bag_inventory "
                f"WHERE owner = ? AND edition = ? AND item_id IN ({placeholders}) "
                "AND qty > 0 AND substr(last_seen, 1, 19) >= ? "
                "LIMIT 1",
                (owner, edition_str, *ids, since, owner, edition_str, *ids, since),
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
            query = "SELECT * FROM encounters WHERE owner = ? AND deleted = 0"
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
