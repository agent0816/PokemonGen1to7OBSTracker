"""SnapshotManager — DB- + Emulator-Save-Snapshots pro Session."""
from __future__ import annotations
_ = """

Ein Snapshot enthaelt:
- ``pokemon.db`` — Kopie der aktiven SQLite-DB
- Emulator-Save (Best-Effort) — je nach verfuegbaren Emulator-Pfaden
- ``snapshot.yml`` — Metadaten (label, timestamp, session, badges pro Player, …)

Speicherort:
- Default: ``<tracker-cwd>/backup-saves/<session_name>/<label>_<ISO-ts>/``
- Override via Session-Config-Feld ``snapshot_backup_path``.

Emulator-Save-Ermittlung (Best-Effort):
- **BizHawk**: pro Config-Feld ``bh_save_path`` (manuell) oder abgeleitet von
  ``bh_config.rom_path``. Randomizer-ROMs: SaveRAM-Dateiname =
  ``<ROM-Basename>.SaveRAM``. Ordner-Base ist Emulator-Config-abhaengig und
  muss vom User via ``bh_save_path`` gesetzt werden.
- **Citra/Azahar**: Title-ID via ``citra.process_list()``, glob-Suche unter
  ``%APPDATA%\\Roaming\\Citra\\`` bzw. ``\\Azahar\\`` — sdmc/Nintendo 3DS/
  <ID0>/00000000000000000000000000000000/title/<tid_high>/<tid_low>/data/00000001/

Bei Auto-Detection-Fehler: nur DB-Snapshot, Warning ins Log.
"""

import os
import shutil
import traceback
from datetime import datetime, timezone
from pathlib import Path

import yaml

from backend.logging_setup import get_logger

logger = get_logger(__name__, './logs/snapshot_manager.log')


class SnapshotManager:
    def __init__(self, session_path: str | Path, session_name: str,
                 bh_config: dict | None = None, munchlax=None):
        self.session_path = Path(session_path)
        self.session_name = session_name or self.session_path.name
        self.bh_config = bh_config or {}
        self.munchlax = munchlax  # optional: für Citra/Azahar-Zugriff
        self.backup_root = self._resolve_backup_root()

    def _resolve_backup_root(self) -> Path:
        override = None
        try:
            nuz_path = self.session_path / "nuzlocke.yml"
            if nuz_path.exists():
                with open(nuz_path, encoding="utf-8") as f:
                    nuz = yaml.safe_load(f) or {}
                override = nuz.get("snapshot_backup_path")
        except Exception as err:
            logger.debug(f"backup_root override read failed: {err}")
        if override:
            root = Path(override)
        else:
            root = Path.cwd() / "backup-saves" / self.session_name
        root.mkdir(parents=True, exist_ok=True)
        return root

    # ---------- API ----------

    def create(self, label: str = "manual") -> str | None:
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")
        safe_label = _safe_filename(label or "snap")
        snap_id = f"{safe_label}_{ts}"
        snap_dir = self.backup_root / snap_id
        try:
            snap_dir.mkdir(parents=True, exist_ok=False)
        except FileExistsError:
            logger.error(f"Snapshot-Ordner existiert bereits: {snap_dir}")
            return None

        meta = {
            "id": snap_id,
            "label": label,
            "timestamp": ts,
            "session": self.session_name,
            "db_backup": None,
            "save_backups": [],
            "errors": [],
        }

        # DB kopieren
        db_src = self.session_path / "pokemon.db"
        if db_src.exists():
            db_dst = snap_dir / "pokemon.db"
            try:
                shutil.copy2(db_src, db_dst)
                meta["db_backup"] = "pokemon.db"
                logger.info(f"Snapshot DB kopiert: {db_src} -> {db_dst}")
            except Exception as err:
                msg = f"DB-Kopie fehlgeschlagen: {err}"
                logger.error(msg)
                meta["errors"].append(msg)
        else:
            meta["errors"].append(f"DB-Datei fehlt: {db_src}")

        # Emulator-Saves (Best-Effort)
        try:
            saves = self._collect_emulator_saves(snap_dir)
            meta["save_backups"].extend(saves)
        except Exception as err:
            msg = f"Emulator-Save-Backup fehlgeschlagen: {err}"
            logger.error(msg)
            logger.error(traceback.format_exc())
            meta["errors"].append(msg)

        # Team-Snapshot aus munchlax (falls verfügbar)
        try:
            meta["team_snapshot"] = self._collect_team_snapshot()
        except Exception as err:
            logger.debug(f"team_snapshot skipped: {err}")

        with open(snap_dir / "snapshot.yml", "w", encoding="utf-8") as f:
            yaml.dump(meta, f, allow_unicode=True)
        logger.info(f"Snapshot erstellt: {snap_id} (Saves: {len(meta['save_backups'])})")
        return snap_id

    def list(self) -> list[dict]:
        entries = []
        if not self.backup_root.exists():
            return entries
        for sub in sorted(self.backup_root.iterdir(), reverse=True):
            if not sub.is_dir():
                continue
            meta_path = sub / "snapshot.yml"
            if not meta_path.exists():
                continue
            try:
                with open(meta_path, encoding="utf-8") as f:
                    meta = yaml.safe_load(f) or {}
                meta["_path"] = str(sub)
                entries.append(meta)
            except Exception as err:
                logger.warning(f"snapshot.yml laden fehlgeschlagen {meta_path}: {err}")
        return entries

    def restore(self, snap_id: str) -> bool:
        snap_dir = self.backup_root / snap_id
        meta_path = snap_dir / "snapshot.yml"
        if not meta_path.exists():
            logger.error(f"Snapshot fehlt: {snap_id}")
            return False
        try:
            with open(meta_path, encoding="utf-8") as f:
                meta = yaml.safe_load(f) or {}
        except Exception as err:
            logger.error(f"snapshot.yml laden fehlgeschlagen: {err}")
            return False

        # DB zurückschreiben — Munchlax sollte VOR restore-Aufruf DB geschlossen haben
        db_src = snap_dir / "pokemon.db"
        db_dst = self.session_path / "pokemon.db"
        if db_src.exists():
            try:
                shutil.copy2(db_src, db_dst)
                logger.info(f"DB restore: {db_src} -> {db_dst}")
            except Exception as err:
                logger.error(f"DB restore fehlgeschlagen: {err}")
                return False

        # Emulator-Saves zurückschreiben (Best-Effort)
        for save in meta.get("save_backups", []):
            src = snap_dir / save.get("backup_file", "")
            dst_path = save.get("original_path")
            if not dst_path or not src.exists():
                continue
            try:
                shutil.copy2(src, dst_path)
                logger.info(f"Save restore: {src} -> {dst_path}")
            except Exception as err:
                logger.warning(f"Save restore fehlgeschlagen ({dst_path}): {err}")
        return True

    def delete(self, snap_id: str) -> bool:
        snap_dir = self.backup_root / snap_id
        if not snap_dir.exists():
            return False
        try:
            shutil.rmtree(snap_dir)
            logger.info(f"Snapshot geloescht: {snap_id}")
            return True
        except Exception as err:
            logger.error(f"Snapshot loeschen fehlgeschlagen: {err}")
            return False

    # ---------- Emulator-Save-Detection ----------

    def _collect_emulator_saves(self, snap_dir: Path) -> list[dict]:
        saves = []
        # BizHawk SaveRAM (falls Pfad konfiguriert oder aus rom_path ableitbar)
        bh_path = self._resolve_bizhawk_save_path()
        if bh_path and bh_path.exists():
            dst = snap_dir / f"bizhawk_{bh_path.name}"
            try:
                shutil.copy2(bh_path, dst)
                saves.append({
                    "type": "bizhawk_saveram",
                    "original_path": str(bh_path),
                    "backup_file": dst.name,
                })
                logger.info(f"BizHawk SaveRAM gesichert: {bh_path}")
            except Exception as err:
                logger.warning(f"BizHawk SaveRAM-Kopie fehlgeschlagen: {err}")

        # Citra/Azahar Save
        citra_saves = self._resolve_citra_save_paths()
        for entry in citra_saves:
            src = entry["path"]
            if not src.exists():
                continue
            dst = snap_dir / f"citra_{entry['title_id']}_{src.name}"
            try:
                shutil.copy2(src, dst)
                saves.append({
                    "type": "citra_save",
                    "title_id": entry["title_id"],
                    "original_path": str(src),
                    "backup_file": dst.name,
                })
                logger.info(f"Citra Save gesichert: {src}")
            except Exception as err:
                logger.warning(f"Citra Save-Kopie fehlgeschlagen: {err}")
        return saves

    def _resolve_bizhawk_save_path(self) -> Path | None:
        # (1) Explizit gesetzter Save-Pfad hat Vorrang
        explicit = self.bh_config.get("save_path")
        if explicit:
            p = Path(str(explicit))
            if p.exists():
                return p

        rom_path = self.bh_config.get("rom_path")
        save_dir = self.bh_config.get("save_dir")
        bh_dir = self._bizhawk_working_dir()
        rom = Path(str(rom_path)) if rom_path else None
        basename = rom.stem if rom else None
        system = _system_from_rom(rom) if rom else None

        # (2) BizHawk-Standard: <BH-Dir>/<System>/SaveRAM/<basename>.SaveRAM
        if bh_dir and system and basename:
            candidate = bh_dir / system / "SaveRAM" / f"{basename}.SaveRAM"
            if candidate.exists():
                return candidate
        # (3) Save-Ordner explizit gesetzt
        if save_dir and basename:
            candidate = Path(str(save_dir)) / f"{basename}.SaveRAM"
            if candidate.exists():
                return candidate
        # (4) Save neben der ROM
        if rom:
            candidate = rom.with_suffix(".SaveRAM")
            if candidate.exists():
                return candidate
            # (5) Save-Ordner unterhalb des ROM-Verzeichnisses
            candidate = rom.parent / "SaveRAM" / f"{rom.stem}.SaveRAM"
            if candidate.exists():
                return candidate
        return None

    def _bizhawk_working_dir(self) -> Path | None:
        """Working-Directory des BizHawk aus bh_config ableiten.

        `bh_config.path` ist historisch der Pfad zur EmuHawk.exe ODER zum
        BizHawk-Ordner. Falls .exe: nimm parent.
        """
        raw = self.bh_config.get("bh_dir") or self.bh_config.get("path")
        if not raw:
            return None
        p = Path(str(raw))
        if p.suffix.lower() == ".exe":
            p = p.parent
        return p if p.exists() else None

    def _resolve_citra_save_paths(self) -> list[dict]:
        """Ermittelt Save-Pfade fuer aktuell laufende Citra/Azahar-Titel."""
        results = []
        if self.munchlax is None:
            return results
        citra = getattr(self.munchlax, "citra", None)
        if citra is None:
            # Fallback: über die App an CitraHandler kommen — hier optional lassen
            return results
        title_ids = []
        try:
            processes = citra.process_list() if hasattr(citra, "process_list") else []
            for proc in processes:
                if isinstance(proc, (list, tuple)) and len(proc) >= 1:
                    title_ids.append(f"{int(proc[0]):016X}")
                elif isinstance(proc, dict):
                    tid = proc.get("title_id")
                    if tid is not None:
                        title_ids.append(f"{int(tid):016X}")
        except Exception as err:
            logger.debug(f"citra.process_list failed: {err}")

        if not title_ids:
            return results

        candidate_roots = []
        appdata = os.environ.get("APPDATA")
        if appdata:
            candidate_roots.append(Path(appdata) / "Citra" / "sdmc" / "Nintendo 3DS")
            candidate_roots.append(Path(appdata) / "Azahar" / "sdmc" / "Nintendo 3DS")

        for tid in title_ids:
            high = tid[:8].lower()
            low = tid[8:].lower()
            for root in candidate_roots:
                if not root.exists():
                    continue
                # ID0-Ordner ist zufaellig — per Glob suchen
                for id0_dir in root.iterdir():
                    if not id0_dir.is_dir():
                        continue
                    save_root = (id0_dir / "00000000000000000000000000000000" /
                                 "title" / high / low / "data" / "00000001")
                    if save_root.exists():
                        for f in save_root.iterdir():
                            if f.is_file():
                                results.append({"title_id": tid, "path": f})
        return results

    def _collect_team_snapshot(self) -> dict:
        if self.munchlax is None:
            return {}
        try:
            editions = dict(getattr(self.munchlax, "editions", {}) or {})
            badges = dict(getattr(self.munchlax, "badges", {}) or {})
            player_names = dict(getattr(self.munchlax, "player_names", {}) or {})
            return {
                "editions": {str(k): v for k, v in editions.items()},
                "badges": {str(k): v for k, v in badges.items()},
                "player_names": {str(k): v for k, v in player_names.items()},
            }
        except Exception as err:
            logger.debug(f"team snapshot failed: {err}")
            return {}


def _safe_filename(s: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in s).strip("_") or "snap"


_ROM_EXT_TO_SYSTEM = {
    ".gb": "GB",
    ".gbc": "GBC",
    ".gba": "GBA",
    ".nds": "NDS",
    ".n64": "N64",
    ".z64": "N64",
    ".v64": "N64",
    ".sms": "SMS",
    ".gg": "GG",
    ".smd": "GEN",
    ".md": "GEN",
    ".bin": "GEN",
}


def _system_from_rom(rom: "Path | None") -> str | None:
    if rom is None:
        return None
    return _ROM_EXT_TO_SYSTEM.get(rom.suffix.lower())
