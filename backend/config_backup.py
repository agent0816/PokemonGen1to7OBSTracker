"""Config-Backup als Zip vor einem Update (Testrunde 2026-11-10).

Sichert ``backend/config/`` nach ``config_backups/config_<ts>.zip`` (neben der
EXE, bewusst NICHT unter ``backend/config/`` — dort räumt
``initialize_tree.init_config_folder`` unbekannte Dateien weg).

- SQLite-DBs (``pokemon.db``, ``runs/**/encounters.sqlite``) werden per
  ``VACUUM INTO`` in eine Temp-Datei kopiert → konsistent auch bei offener
  Verbindung (Muster ``RunManager._snapshot_db``).
- ROM-Dateien werden ausgelassen (User-Entscheidung 2026-11-11): Größe (NDS
  bis 256 MB pro Slot/Run). ``randomizer.log`` (Seed + Settings String) und
  ``settings.rnqs`` bleiben drin → ROM notfalls per UPR-GUI "Premade Seed"
  neu erzeugbar.
- Keine Redaction: das Zip bleibt lokal beim User.

Kivy-frei, blockierend → aus der UI per ``run_in_executor`` aufrufen.
"""
import os
import shutil
import sqlite3
import tempfile
import time
import traceback
import zipfile
from pathlib import Path

from backend.logging_setup import get_logger

logger = get_logger(__name__, 'logs/config_backup.log')

DEFAULT_CONFIG_DIR = Path('backend/config')
DEFAULT_BACKUP_DIR = Path('config_backups')
BACKUP_PREFIX = 'config_'
DEFAULT_KEEP = 5

ROM_SUFFIXES = frozenset({'.gb', '.gbc', '.gba', '.nds', '.3ds', '.cia', '.cxi', '.cci'})
DB_SUFFIXES = frozenset({'.db', '.sqlite', '.sqlite3'})
# Begleitdateien offener SQLite-DBs: Inhalt steckt im VACUUM-Snapshot
DB_SIDECAR_SUFFIXES = ('-wal', '-shm', '-journal')


def _is_rom(path: Path) -> bool:
    return path.suffix.lower() in ROM_SUFFIXES


def _vacuum_into(src: Path, dst: Path) -> None:
    conn = sqlite3.connect(str(src))
    try:
        dst_escaped = str(dst).replace("'", "''")
        conn.execute(f"VACUUM INTO '{dst_escaped}'")
    finally:
        conn.close()


def _snapshot_sqlite(src: Path, dst: Path) -> bool:
    """VACUUM INTO dst. True bei Erfolg.

    OperationalError (z.B. "database is locked"): einmal nach 1 s wiederholen,
    dann Fehlschlag — KEIN copy2, das würde bei WAL-DBs die -wal-Datei
    verlieren und ein inkonsistentes Backup als Erfolg melden. copy2 nur bei
    sonstigem DatabaseError (DB korrupt/kein SQLite): Rohkopie ist dann das
    Beste, was es gibt.
    """
    try:
        try:
            _vacuum_into(src, dst)
        except sqlite3.OperationalError as err:
            logger.warning(f"VACUUM INTO für {src} fehlgeschlagen ({err}) — Retry in 1 s")
            time.sleep(1.0)
            dst.unlink(missing_ok=True)
            _vacuum_into(src, dst)
        return True
    except sqlite3.OperationalError as err:
        logger.error(f"VACUUM INTO für {src} auch im Retry fehlgeschlagen: {err}")
        return False
    except sqlite3.DatabaseError as err:
        logger.warning(f"{src} ist keine lesbare SQLite-DB ({err}) — Rohkopie per copy2")
        dst.unlink(missing_ok=True)
        try:
            shutil.copy2(src, dst)
            return True
        except Exception as copy_err:
            logger.error(f"copy2 für {src} fehlgeschlagen: {copy_err}")
            return False


def prune_backups(backup_dir: Path, keep: int) -> list[Path]:
    """Löscht alle bis auf die neuesten ``keep`` config_*.zip. Rückgabe: gelöscht."""
    keep = max(1, int(keep))
    zips = sorted(Path(backup_dir).glob(f'{BACKUP_PREFIX}*.zip'),
                  key=lambda p: p.name, reverse=True)
    removed = []
    for old in zips[keep:]:
        try:
            old.unlink()
            removed.append(old)
        except Exception as err:
            logger.warning(f"Altes Backup nicht löschbar: {old} ({err})")
    return removed


def create_config_backup(config_dir: Path | str = DEFAULT_CONFIG_DIR,
                         backup_dir: Path | str = DEFAULT_BACKUP_DIR,
                         keep: int = DEFAULT_KEEP) -> Path:
    """Zippt config_dir nach backup_dir/config_<ts>.zip und hält ``keep`` Zips.

    Wirft bei Fehler (Aufrufer fragt dann, ob das Update trotzdem laufen soll);
    eine halb geschriebene Datei wird vorher entfernt.
    """
    config_dir = Path(config_dir)
    backup_dir = Path(backup_dir)
    if not config_dir.is_dir():
        raise FileNotFoundError(f"Config-Ordner fehlt: {config_dir}")
    backup_dir.mkdir(parents=True, exist_ok=True)

    ts = time.strftime('%Y%m%d_%H%M%S')
    target = backup_dir / f'{BACKUP_PREFIX}{ts}.zip'
    n = 1
    while target.exists():  # zwei Backups in derselben Sekunde
        target = backup_dir / f'{BACKUP_PREFIX}{ts}_{n}.zip'
        n += 1
    partial = target.with_name(target.name + '.partial')

    files = dbs = skipped_roms = 0
    started = time.monotonic()
    try:
        with tempfile.TemporaryDirectory(prefix='cfgbak_') as tmp, \
                zipfile.ZipFile(partial, 'w', compression=zipfile.ZIP_DEFLATED) as zf:
            tmp_dir = Path(tmp)
            for root, _dirs, names in os.walk(config_dir):
                for name in sorted(names):
                    src = Path(root) / name
                    arcname = Path('config') / src.relative_to(config_dir)
                    if _is_rom(src):
                        skipped_roms += 1
                        continue
                    if name.endswith(DB_SIDECAR_SUFFIXES):
                        continue
                    if src.suffix.lower() in DB_SUFFIXES:
                        snap = tmp_dir / f'db_{dbs}{src.suffix}'
                        if _snapshot_sqlite(src, snap):
                            zf.write(snap, arcname.as_posix())
                            snap.unlink(missing_ok=True)
                            dbs += 1
                        else:
                            raise OSError(f"DB-Snapshot fehlgeschlagen: {src}")
                        continue
                    zf.write(src, arcname.as_posix())
                    files += 1
        partial.replace(target)
    except BaseException:
        try:
            partial.unlink(missing_ok=True)
        except Exception:
            pass
        logger.error(f"Config-Backup fehlgeschlagen:\n{traceback.format_exc()}")
        raise

    removed = prune_backups(backup_dir, keep)
    size_mb = target.stat().st_size / (1024 * 1024)
    logger.info(
        f"Config-Backup geschrieben: {target.resolve()} ({size_mb:.1f} MB, "
        f"{files} Dateien, {dbs} DBs, {skipped_roms} ROMs ausgelassen, "
        f"{len(removed)} alte gelöscht, {time.monotonic() - started:.1f}s)"
    )
    return target
