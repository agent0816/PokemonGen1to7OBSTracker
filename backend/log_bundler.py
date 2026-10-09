"""Support-Bundle-Builder: packt Logs, sanitized Session-Config und aktive
Run-Meta in ein ZIP unter ``./support_bundles/``.

Reine Funktionen, keine Klasse. Netz-unabhängig: produziert und bereinigt nur
lokale Dateien. Der Upload-Transport läuft über ``log_bundle_transfer`` und die
Munchlax/Arceus-Message-Pipeline.

Zip-Layout:
  logs/<alle logs/*.log*>
  config/<yml-datei>   (Secret-Keys maskiert)
  active_run/run_meta.yml
  active_run/p<slot>/randomizer.log  (sofern vorhanden)
  bundle_meta.json
"""
from __future__ import annotations

import json
import os
import secrets
import threading
import time
import traceback
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from backend.logging_setup import get_logger

logger = get_logger("log_bundler", log_file="logs/log_bundler.log")

SUPPORT_BUNDLES_DIR = Path("./support_bundles")
LOGS_DIR = Path("./logs")

SECRET_KEYS = frozenset({
    "password",
    "websocket_password",
    "obs_password",
    "server_password",
    "token",
    "api_key",
    "secret",
})

REDACTED = "***redacted***"

# Serialisiert parallele Build-Aufrufe (MainMenu-Button + SettingsMenu-Dev-
# Button + Host-Broadcast-Response) damit kein ZipFile(..,"w") doppelt die
# gleiche Datei truncatet und keine zwei Bundles identisch benannt werden.
_BUILD_LOCK = threading.Lock()


def _redact(value: Any) -> Any:
    """Rekursiv Secret-Keys in Dicts/Listen maskieren."""
    if isinstance(value, dict):
        out: dict = {}
        for k, v in value.items():
            if isinstance(k, str) and k.lower() in SECRET_KEYS:
                out[k] = REDACTED
            else:
                out[k] = _redact(v)
        return out
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


def sanitize_yaml(path: Path) -> dict | list | None:
    """Lädt YAML und maskiert bekannte Secret-Keys. None bei Lesefehler."""
    try:
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return _redact(data) if data is not None else None
    except Exception as err:
        logger.warning(f"sanitize_yaml({path}) fehlgeschlagen: {err}")
        return None


def _safe_player_name(name: str) -> str:
    """Dateiname-sicherer Player-Name (keine Pfad-Trenner, keine Leerzeichen)."""
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in (name or ""))
    return safe or "unknown"


def _active_run_meta(session_path: Path) -> dict | None:
    try:
        from backend.controller.run_manager import RunManager
        rm = RunManager(session_path)
        return rm.get_active_run()
    except Exception as err:
        logger.warning(f"RunManager-Lookup fehlgeschlagen: {err}")
        return None


def _app_version() -> str:
    try:
        from version import VERSION
        return str(VERSION)
    except Exception:
        return "unknown"


def build_bundle(session_path: Path | str,
                 player_name: str,
                 is_host: bool,
                 extra_meta: dict | None = None) -> Path:
    """Erstellt ein Support-Bundle-Zip und liefert dessen Pfad zurück.

    Serialisiert via Modul-Lock gegen Parallel-Aufrufe; eindeutiger Suffix
    im Dateinamen gegen Overwrite in derselben Sekunde. Bei Exception wird
    die halbe Datei entfernt, der Fehler propagiert.
    """
    SUPPORT_BUNDLES_DIR.mkdir(parents=True, exist_ok=True)
    session_path = Path(str(session_path))
    safe_name = _safe_player_name(player_name)

    with _BUILD_LOCK:
        ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        suffix = secrets.token_hex(3)
        bundle_path = SUPPORT_BUNDLES_DIR / f"bundle_{ts}_{safe_name}_{suffix}.zip"
        logger.info(
            f"Build-Start: {bundle_path} (session={session_path.name}, host={is_host})"
        )

        added_logs = 0
        added_config = 0
        added_run_files = 0

        try:
            with zipfile.ZipFile(bundle_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
                # 1) Logs
                if LOGS_DIR.exists():
                    try:
                        log_entries = sorted(LOGS_DIR.iterdir())
                    except OSError as err:
                        logger.warning(f"logs/ iterdir failed: {err}")
                        log_entries = []
                    for entry in log_entries:
                        try:
                            if not entry.is_file():
                                continue
                            name = entry.name
                            if ".log" not in name:
                                continue
                            zf.write(entry, arcname=f"logs/{name}")
                            added_logs += 1
                        except Exception as err:
                            logger.warning(f"log skipped {entry.name}: {err}")
                else:
                    logger.warning(f"Logs-Verzeichnis fehlt: {LOGS_DIR}")

                # 2) Sanitized Session-Config
                if session_path.exists() and session_path.is_dir():
                    try:
                        yml_entries = sorted(session_path.glob("*.yml"))
                    except OSError as err:
                        logger.warning(f"session glob failed: {err}")
                        yml_entries = []
                    for yml in yml_entries:
                        data = sanitize_yaml(yml)
                        if data is None:
                            continue
                        try:
                            payload = yaml.safe_dump(data, allow_unicode=True, sort_keys=True)
                            zf.writestr(f"config/{yml.name}", payload)
                            added_config += 1
                        except Exception as err:
                            logger.warning(f"config write failed {yml.name}: {err}")
                else:
                    logger.warning(f"Session-Pfad existiert nicht: {session_path}")

                # 3) Aktive Run-Meta + randomizer.log (ohne ROM)
                active = _active_run_meta(session_path)
                if active and active.get("_path"):
                    run_dir = Path(active["_path"])
                    if run_dir.exists():
                        meta_file = run_dir / "run_meta.yml"
                        if meta_file.exists():
                            try:
                                zf.write(meta_file, arcname="active_run/run_meta.yml")
                                added_run_files += 1
                            except Exception as err:
                                logger.warning(f"run_meta write failed: {err}")
                        try:
                            sub_entries = sorted(run_dir.iterdir())
                        except OSError as err:
                            logger.warning(f"run_dir iterdir failed: {err}")
                            sub_entries = []
                        for subdir in sub_entries:
                            try:
                                if not subdir.is_dir() or not subdir.name.startswith("p"):
                                    continue
                                log = subdir / "randomizer.log"
                                if log.exists():
                                    zf.write(log, arcname=f"active_run/{subdir.name}/randomizer.log")
                                    added_run_files += 1
                            except Exception as err:
                                logger.warning(
                                    f"rando log write failed ({subdir.name}): {err}"
                                )

                # 4) Bundle-Meta
                meta: dict = {
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "player_name": player_name or "",
                    "is_host": bool(is_host),
                    "app_version": _app_version(),
                    "session_name": session_path.name,
                    "session_path_exists": bool(session_path.is_dir()),
                    "added_logs": added_logs,
                    "added_config_files": added_config,
                    "added_run_files": added_run_files,
                    "config_missing": added_config == 0,
                }
                if extra_meta:
                    meta["extra"] = extra_meta
                zf.writestr("bundle_meta.json", json.dumps(meta, indent=2, ensure_ascii=False))
        except Exception:
            # Halbe Zip-Datei entfernen damit Cleanup nicht erst nach 30 Tagen greift.
            try:
                bundle_path.unlink(missing_ok=True)  # type: ignore[call-arg]
            except Exception as rm_err:
                logger.warning(
                    f"build_bundle: Entfernen der halben Zip-Datei fehlgeschlagen: {rm_err}"
                )
            logger.error(
                f"build_bundle failed: {traceback.format_exc()}"
            )
            raise

        size = bundle_path.stat().st_size
        logger.info(
            f"Build-Fertig: {bundle_path.name} size={size} bytes "
            f"logs={added_logs} config={added_config} run={added_run_files}"
        )
        return bundle_path


def cleanup_old_bundles(max_age_days: int = 30) -> int:
    """Löscht support_bundles/*.zip älter als max_age_days. Returns Anzahl gelöscht.

    Zusätzlich werden ALLE ``.incoming_*.part``-Reste (von abgebrochenen/
    gecrashten Uploads der Vorsession) unconditional entfernt — zu diesem
    Zeitpunkt (Prozess-Start) laufen definitiv keine aktiven Uploads.
    Sonst wuerden stale Teildateien bis zu 30 Tage gegen die 2-GiB-Quota
    zaehlen (R5 WARN cave).
    """
    if not SUPPORT_BUNDLES_DIR.exists():
        return 0
    cutoff = time.time() - (max_age_days * 86400)
    deleted = 0
    for entry in SUPPORT_BUNDLES_DIR.iterdir():
        if not entry.is_file():
            continue
        name = entry.name
        if name.startswith(".incoming_"):
            # Unconditional-Cleanup: Reste eines fruehen Shutdowns.
            try:
                entry.unlink()
                deleted += 1
                logger.info(f"Cleanup: part-Rest entfernt {name}")
            except Exception as err:
                logger.warning(
                    f"Cleanup part-Rest fehlgeschlagen {name}: {err}"
                )
            continue
        if not name.endswith(".zip"):
            continue
        try:
            if entry.stat().st_mtime < cutoff:
                entry.unlink()
                deleted += 1
                logger.info(f"Cleanup: alt gelöscht {name}")
        except Exception as err:
            logger.warning(f"Cleanup fehlgeschlagen für {name}: {err}\n{traceback.format_exc()}")
    if deleted:
        logger.info(f"Cleanup: insgesamt {deleted} Datei(en) gelöscht (>{max_age_days}d/Resten)")
    return deleted
