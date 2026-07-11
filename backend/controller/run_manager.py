"""RunManager — Randomizer-Run-Historie pro Session.

Eine Session besteht aus 1..N Runs. Jeder Run hat einen eigenen Ordner unter
``<session>/runs/run_<NNN>_<ISO-ts>/`` und enthält:

- ``run_meta.yml``        — Metadaten (Start/Ende/Grund/Team-Wipe/Hashes/roms-Liste)
- ``settings.rnqs``       — Kopie der Randomizer-Settings (falls vorhanden)
- ``encounters.sqlite``   — DB-Snapshot bei Finalize (VACUUM INTO)
- ``p<slot>/``            — je lokalem Player-Slot ein Unterordner mit:
    - ``rom.<ext>``       — randomisierte ROM (Extension aus Input-ROM)
    - ``randomizer.log``  — vom Randomizer erzeugtes Log

Ein Marker ``runs/active.txt`` enthält die run_id des aktiven Runs. Beim
Finalize wird der Marker entfernt.
"""
from __future__ import annotations

import hashlib
import shutil
import sqlite3
import traceback
from datetime import datetime, timezone
from pathlib import Path

import yaml

from backend.logging_setup import get_logger


ACTIVE_MARKER = "active.txt"
META_FILENAME = "run_meta.yml"
DB_SNAPSHOT_FILENAME = "encounters.sqlite"
LOG_FILENAME = "randomizer.log"
SETTINGS_FILENAME = "settings.rnqs"


class RunManager:
    def __init__(self, session_path):
        # session_path kann str, Path oder MutableString (frontend.app) sein —
        # bewusst über str() normalisieren, damit Path(...) nicht auf einem
        # nicht-PathLike-Wrapper wie MutableString scheitert.
        self.session_path = Path(str(session_path))
        self.runs_root = self.session_path / "runs"
        self.runs_root.mkdir(parents=True, exist_ok=True)
        self.logger = get_logger(__name__, './logs/run_manager.log')

    # ---------- Public API ----------

    def list_runs(self) -> list[dict]:
        """Alle Runs, sortiert nach run_index aufsteigend."""
        entries: list[dict] = []
        if not self.runs_root.exists():
            return entries
        for sub in self.runs_root.iterdir():
            if not sub.is_dir():
                continue
            meta = self._read_meta(sub)
            if meta is None:
                continue
            meta["_path"] = str(sub)
            entries.append(meta)
        entries.sort(key=lambda m: m.get("run_index", 0))
        return entries

    def get_active_run(self) -> dict | None:
        run_id = self._read_active_marker()
        if not run_id:
            return None
        run_dir = self.runs_root / run_id
        meta = self._read_meta(run_dir)
        if meta is None:
            self.logger.warning(f"Active-Marker zeigt auf fehlenden Run: {run_id}")
            return None
        meta["_path"] = str(run_dir)
        return meta

    def create_run(self, rom_source: str | Path,
                   randomizer_settings_path: str | Path | None = None,
                   player_slots: list[int] | None = None) -> dict | None:
        """Legt neuen Run an mit einem Ziel-ROM pro lokalem Player-Slot.

        Layout:
          run_dir/
            run_meta.yml
            settings.rnqs
            p<slot>/
              rom.<ext>
              randomizer.log

        Meta-Feld ``roms`` ist eine Liste ``[{player_slot, subdir, rom_file,
        log_file}, ...]``. Die Rückgabe enthält zusätzlich ``rom_targets`` =
        list[{player_slot, rom_path, log_path, subdir}] für den
        RandomizerController.

        ``player_slots`` default ``[1]`` — Single-Player.

        Ist bereits ein Run aktiv, muss der Aufrufer vorher ``finalize_active_run``
        rufen — hier wird keine implizite Finalisierung durchgeführt.
        """
        try:
            existing_active = self._read_active_marker()
            if existing_active:
                self.logger.error(
                    f"create_run: aktiver Run {existing_active} muss zuerst finalisiert werden")
                return None

            slots = list(player_slots) if player_slots else [1]
            if not slots:
                self.logger.error("create_run: player_slots leer")
                return None

            rom_src = Path(str(rom_source))
            ext = rom_src.suffix.lower() or ".rom"
            run_index = self._next_run_index()
            ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            run_id = f"run_{run_index:03d}_{ts}"
            run_dir = self.runs_root / run_id
            run_dir.mkdir(parents=True, exist_ok=False)

            settings_rel = None
            settings_sha = None
            if randomizer_settings_path:
                settings_src = Path(str(randomizer_settings_path))
                if settings_src.exists():
                    settings_dst = run_dir / SETTINGS_FILENAME
                    try:
                        shutil.copy2(settings_src, settings_dst)
                        settings_rel = SETTINGS_FILENAME
                        settings_sha = _sha256_file(settings_dst)
                    except Exception as err:
                        self.logger.warning(f"settings copy failed: {err}")
                else:
                    self.logger.info(f"settings-Pfad existiert nicht: {settings_src}")

            roms_meta: list[dict] = []
            rom_targets: list[dict] = []
            for slot in slots:
                subdir_name = f"p{slot}"
                subdir = run_dir / subdir_name
                subdir.mkdir(parents=True, exist_ok=True)
                rom_file = f"rom{ext}"
                log_file = LOG_FILENAME
                roms_meta.append({
                    "player_slot": slot,
                    "subdir": subdir_name,
                    "rom_file": rom_file,
                    "log_file": log_file,
                })
                rom_targets.append({
                    "player_slot": slot,
                    "subdir": str(subdir),
                    "rom_path": str(subdir / rom_file),
                    "log_path": str(subdir / log_file),
                })

            meta = {
                "run_id": run_id,
                "run_index": run_index,
                "start_ts": _iso_now(),
                "end_ts": None,
                "end_reason": None,
                "duration_seconds": None,
                "rom_source": str(rom_src),
                "roms": roms_meta,
                "randomizer_settings": settings_rel,
                "randomizer_settings_sha256": settings_sha,
                "team_wipe": {"player_id": None, "timestamp": None},
                "encounters_snapshot": None,
                "final_teams": None,
                "notes": "",
            }
            self._write_meta(run_dir, meta)
            self._write_active_marker(run_id)
            self.logger.info(
                f"Run angelegt: {run_id} (source={rom_src.name}, slots={slots})")

            result = dict(meta)
            result["_path"] = str(run_dir)
            result["rom_targets"] = rom_targets
            return result
        except Exception as err:
            self.logger.error(f"create_run failed: {type(err).__name__},{err}")
            self.logger.error(traceback.format_exc())
            return None

    def finalize_active_run(self, reason: str,
                            wipe_player_id: str | int | None = None,
                            pokedex_db_path: str | Path | None = None,
                            final_teams: dict | None = None) -> str | None:
        """Schließt aktiven Run ab. Rückgabe: run_id oder None falls kein aktiver
        Run existiert / Fehler."""
        run_id = self._read_active_marker()
        if not run_id:
            self.logger.info("finalize_active_run: kein aktiver Run")
            return None
        run_dir = self.runs_root / run_id
        meta = self._read_meta(run_dir)
        if meta is None:
            self.logger.warning(f"finalize: Meta fehlt für {run_id}, Marker wird entfernt")
            self._clear_active_marker()
            return None

        try:
            end_ts = _iso_now()
            meta["end_ts"] = end_ts
            meta["end_reason"] = reason
            meta["duration_seconds"] = _duration_seconds(meta.get("start_ts"), end_ts)
            if wipe_player_id is not None:
                meta["team_wipe"] = {
                    "player_id": str(wipe_player_id),
                    "timestamp": end_ts,
                }
            if final_teams is not None:
                meta["final_teams"] = final_teams

            if pokedex_db_path:
                snap_ok = self._snapshot_db(pokedex_db_path, run_dir / DB_SNAPSHOT_FILENAME)
                if snap_ok:
                    meta["encounters_snapshot"] = DB_SNAPSHOT_FILENAME

            self._write_meta(run_dir, meta)
            self._clear_active_marker()
            self.logger.info(
                f"Run finalisiert: {run_id} reason={reason} "
                f"wipe_player={wipe_player_id} snapshot={meta.get('encounters_snapshot')}"
            )
            return run_id
        except Exception as err:
            self.logger.error(f"finalize_active_run failed: {type(err).__name__},{err}")
            self.logger.error(traceback.format_exc())
            return None

    def get_run_paths(self, run_id: str) -> dict:
        """Liefert alle relevanten Pfade eines Runs.

        ``roms`` ist ein dict ``{player_slot: {rom, log, dir}}`` — leer wenn kein
        ROM angelegt wurde.
        """
        run_dir = self.runs_root / run_id
        meta = self._read_meta(run_dir) or {}
        roms: dict[int, dict] = {}
        for entry in meta.get("roms", []) or []:
            try:
                slot = int(entry.get("player_slot"))
            except (TypeError, ValueError):
                continue
            subdir = run_dir / (entry.get("subdir") or f"p{slot}")
            roms[slot] = {
                "dir": str(subdir),
                "rom": str(subdir / (entry.get("rom_file") or "")),
                "log": str(subdir / (entry.get("log_file") or LOG_FILENAME)),
            }
        return {
            "dir": str(run_dir),
            "roms": roms,
            "settings": str(run_dir / SETTINGS_FILENAME),
            "encounters": str(run_dir / DB_SNAPSHOT_FILENAME),
            "meta": str(run_dir / META_FILENAME),
        }

    # ---------- Interna ----------

    def _next_run_index(self) -> int:
        max_idx = 0
        for sub in self.runs_root.iterdir():
            if not sub.is_dir():
                continue
            meta = self._read_meta(sub)
            if meta is None:
                continue
            idx = meta.get("run_index")
            if isinstance(idx, int) and idx > max_idx:
                max_idx = idx
        return max_idx + 1

    def _read_meta(self, run_dir: Path) -> dict | None:
        meta_path = run_dir / META_FILENAME
        if not meta_path.exists():
            return None
        try:
            with open(meta_path, encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            if not isinstance(data, dict):
                return None
            return data
        except Exception as err:
            self.logger.warning(f"read_meta {meta_path} fehlgeschlagen: {err}")
            return None

    def _write_meta(self, run_dir: Path, meta: dict) -> None:
        meta_path = run_dir / META_FILENAME
        clean = {k: v for k, v in meta.items() if not k.startswith("_")}
        with open(meta_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(clean, f, allow_unicode=True, sort_keys=False)

    def _read_active_marker(self) -> str | None:
        marker = self.runs_root / ACTIVE_MARKER
        if not marker.exists():
            return None
        try:
            run_id = marker.read_text(encoding="utf-8").strip()
            return run_id or None
        except Exception as err:
            self.logger.warning(f"active-Marker lesen fehlgeschlagen: {err}")
            return None

    def _write_active_marker(self, run_id: str) -> None:
        marker = self.runs_root / ACTIVE_MARKER
        marker.write_text(run_id, encoding="utf-8")

    def _clear_active_marker(self) -> None:
        marker = self.runs_root / ACTIVE_MARKER
        try:
            if marker.exists():
                marker.unlink()
        except Exception as err:
            self.logger.warning(f"active-Marker löschen fehlgeschlagen: {err}")

    def _snapshot_db(self, src_path: str | Path, dst_path: Path) -> bool:
        """SQLite-Snapshot via ``VACUUM INTO`` (konsistent, auch bei offener
        Verbindung). Fällt auf ``shutil.copy2`` zurück wenn VACUUM INTO nicht
        unterstützt wird."""
        src = Path(str(src_path))
        if not src.exists():
            self.logger.info(f"DB-Snapshot übersprungen — Quelle fehlt: {src}")
            return False
        if dst_path.exists():
            try:
                dst_path.unlink()
            except Exception as err:
                self.logger.warning(f"altes DB-Snapshot-File nicht löschbar: {err}")
                return False
        try:
            conn = sqlite3.connect(str(src))
            try:
                dst_escaped = str(dst_path).replace("'", "''")
                conn.execute(f"VACUUM INTO '{dst_escaped}'")
            finally:
                conn.close()
            self.logger.info(f"DB-Snapshot geschrieben: {dst_path}")
            return True
        except sqlite3.OperationalError as err:
            self.logger.warning(f"VACUUM INTO fehlgeschlagen ({err}) — nutze copy2")
            try:
                shutil.copy2(src, dst_path)
                return True
            except Exception as fallback_err:
                self.logger.error(f"copy2-Fallback fehlgeschlagen: {fallback_err}")
                return False
        except Exception as err:
            self.logger.error(f"DB-Snapshot failed: {type(err).__name__},{err}")
            self.logger.error(traceback.format_exc())
            return False


def _iso_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _duration_seconds(start_ts: str | None, end_ts: str | None) -> int | None:
    if not start_ts or not end_ts:
        return None
    try:
        fmt = "%Y-%m-%dT%H:%M:%SZ"
        start = datetime.strptime(start_ts, fmt).replace(tzinfo=timezone.utc)
        end = datetime.strptime(end_ts, fmt).replace(tzinfo=timezone.utc)
        return int((end - start).total_seconds())
    except Exception:
        return None


def _sha256_file(path: Path) -> str | None:
    try:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
    except Exception:
        return None
