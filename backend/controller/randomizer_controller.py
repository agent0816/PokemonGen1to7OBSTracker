import asyncio
import shutil
import traceback
from pathlib import Path

from backend.controller.randomizer_log_parser import RandomizerLogParser, RandomizerLogData
from backend.controller.run_manager import RunManager
from backend.logging_setup import get_logger


class RandomizerController:
    def __init__(self, rnd: dict, pl: dict, configsave=None):
        self.rnd = rnd
        self.pl = pl
        # configsave ist eine langlebige Referenz (MutableString aus frontend.app),
        # deren .text bei Session-Wechsel in-place mutiert wird. Nicht einfrieren
        # (`Path(configsave)` schlägt sonst mit TypeError fehl UND würde alte
        # Session-Pfade festhalten). Immer lazy in `_get_run_manager` auflösen.
        self.configsave = configsave
        self.logger = get_logger(__name__, './logs/randomizer_controller.log')
        self._process: asyncio.subprocess.Process | None = None
        self.log_parser = RandomizerLogParser()

    def _find_java(self) -> str | None:
        if self.rnd.get("java_path"):
            java = Path(self.rnd["java_path"])
            if java.exists():
                return str(java)
        return shutil.which("java")

    def _java_base_args(self) -> tuple[list[str], str, str | None]:
        """Baut Java-Basisargumente wie der launcher_WINDOWS.bat. Gibt (args, jar_dir, fehler) zurück."""
        java = self._find_java()
        if not java:
            return [], "", "Java wurde nicht gefunden. Bitte Java installieren oder den Pfad in den Einstellungen setzen."

        jar = self.rnd.get("jar_path", "")
        if not jar or not Path(jar).exists():
            return [], "", "Randomizer-JAR nicht gefunden. Bitte den Pfad in den Einstellungen setzen."

        jar_dir = str(Path(jar).parent)

        args = [java, "-Xmx4608M", "-jar", jar]
        return args, jar_dir, None

    def _local_player_slots(self) -> list[int]:
        """Liest lokale Spieler-Slots aus pl: 1..player_count, exkl. remote_i=True."""
        count = int(self.pl.get("player_count", 1) or 1)
        slots: list[int] = []
        for slot in range(1, count + 1):
            if self.pl.get(f"remote_{slot}", False):
                continue
            slots.append(slot)
        if not slots:
            # Alle Slots remote → trotzdem mindestens Slot 1 vorsehen, damit ein
            # ROM erzeugt wird (Host kann später verteilen).
            self.logger.info("Alle player_count-Slots sind remote — nutze Slot 1 als Fallback")
            slots = [1]
        return slots

    def _get_run_manager(self) -> RunManager | None:
        if self.configsave is None:
            self.logger.error("configsave nicht gesetzt — RunManager nicht verfügbar")
            return None
        # Kein Cache: bei Session-Wechsel mutiert configsave in-place, wir wollen
        # bei jedem Aufruf frisch auf den aktuellen Session-Pfad zeigen.
        try:
            return RunManager(str(self.configsave))
        except Exception as err:
            self.logger.error(f"RunManager-Init fehlgeschlagen: {type(err).__name__},{err}")
            self.logger.error(traceback.format_exc())
            return None

    def _validate_paths(self) -> tuple[str, str, str, str | None]:
        """Prüft Settings- und ROM-Pfad. Rückgabe (settings, rom, jar_dir_or_empty, error)."""
        settings = self.rnd.get("settings_path", "")
        if not settings or not Path(settings).exists():
            return "", "", "", "Einstellungsdatei (.rnqs) nicht gefunden. Bitte in den Einstellungen angeben."

        rom = self.rnd.get("rom_path", "")
        if not rom or not Path(rom).exists():
            return "", "", "", "Input-ROM nicht gefunden. Bitte den ROM-Pfad in den Einstellungen angeben."

        base_args, jar_dir, error = self._java_base_args()
        if error:
            return "", "", "", error
        return settings, rom, jar_dir, None

    def open_gui(self) -> tuple[bool, str]:
        """Startet die Randomizer-GUI (wie launcher_WINDOWS.bat ohne cli-Flag)."""
        import subprocess
        base_args, jar_dir, error = self._java_base_args()
        if error:
            return False, error
        try:
            subprocess.Popen(base_args + ["please-use-the-launcher"], cwd=jar_dir)
            return True, ""
        except Exception as err:
            msg = f"Konnte Randomizer nicht starten: {err}"
            self.logger.error(msg)
            self.logger.error(traceback.format_exc())
            return False, msg

    def find_log_path(self) -> str | None:
        """Für alten Import-Log-Button: sucht das jüngste Log in output_path oder
        neben dem Input-ROM."""
        rom = self.rnd.get("rom_path", "")
        if not rom:
            return None
        output_dir = self.rnd.get("output_path", "") or str(Path(rom).parent)
        rom_stem = Path(rom).stem
        pattern = f"{rom_stem}_randomized*.log"
        matches = sorted(Path(output_dir).glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)
        if matches:
            return str(matches[0])
        return None

    def parse_log(self, log_path: str = None) -> tuple[bool, str]:
        if not log_path:
            log_path = self.find_log_path()
        if not log_path:
            return False, "Keine Log-Datei gefunden."
        data = self.log_parser.parse(log_path)
        if data:
            return True, f"Log geparst: {len(data.pokemon)} Pokemon, {len(data.trainers)} Trainer, {len(data.wild_areas)} Wild-Gebiete"
        return False, "Fehler beim Parsen der Log-Datei."

    def get_log_data(self) -> RandomizerLogData | None:
        return self.log_parser.get_data()

    async def _randomize_one(self, base_args: list[str], jar_dir: str,
                              input_rom: str, settings: str,
                              rom_target: Path, log_target: Path,
                              slot: int) -> tuple[bool, str]:
        """Führt genau einen Randomizer-Aufruf aus und verschiebt das erzeugte
        Log-File nach ``log_target``."""
        output_arg = str(rom_target.with_suffix(""))
        args = base_args + ["cli", "-i", input_rom, "-o", output_arg, "-s", settings, "-l"]
        self.logger.info(f"[slot {slot}] Starte Randomisierung: {' '.join(args)}")
        try:
            self._process = await asyncio.create_subprocess_exec(
                *args,
                cwd=jar_dir,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await self._process.communicate()
            returncode = self._process.returncode
        finally:
            self._process = None

        if returncode != 0:
            error_msg = stderr.decode(errors="replace").strip() or stdout.decode(errors="replace").strip()
            msg = f"Slot {slot}: Randomisierung fehlgeschlagen (Exit-Code {returncode}):\n{error_msg}"
            self.logger.error(msg)
            return False, msg

        if stdout:
            self.logger.info(f"[slot {slot}] stdout: {stdout.decode(errors='replace').strip()}")

        # Log-Datei umbenennen — Randomizer produziert <output_arg>.log oder ähnliches
        produced_log = Path(f"{output_arg}.log")
        if not produced_log.exists():
            # Fallback: irgendein .log im Ziel-Ordner
            candidates = list(rom_target.parent.glob("*.log"))
            if candidates:
                produced_log = candidates[0]
        if produced_log.exists() and produced_log != log_target:
            try:
                if log_target.exists():
                    log_target.unlink()
                produced_log.rename(log_target)
                self.logger.info(f"[slot {slot}] Log verschoben: {produced_log.name} -> {log_target.name}")
            except Exception as err:
                self.logger.warning(f"[slot {slot}] Log-Move fehlgeschlagen: {err}")
        elif not produced_log.exists():
            self.logger.warning(f"[slot {slot}] Kein Log-File gefunden bei {output_arg}.log")

        return True, f"Slot {slot}: OK"

    async def randomize(self) -> tuple[bool, str, list[dict]]:
        """Erzeugt pro lokalem Spieler-Slot ein randomisiertes ROM im aktiven
        Run-Ordner. Läuft ein Run bereits, wird er als
        ``superseded_by_new_randomize`` finalisiert. Bei Fehler in einem Slot
        wird der Run trotzdem angelegt, aber als Ergebnis False zurückgegeben.

        Rückgabe: (success, summary, slot_dirs) — slot_dirs enthält pro
        erfolgreich randomisiertem Slot ``{"slot": int, "dir": str}`` (absoluter
        Ordner-Pfad ohne Datei), damit die UI Copy-Buttons zeigen kann."""
        settings, input_rom, jar_dir, error = self._validate_paths()
        if error:
            self.logger.error(f"Validierungsfehler: {error}")
            return False, error, []

        rm = self._get_run_manager()
        if rm is None:
            return False, "Session-Pfad nicht gesetzt — RunManager nicht verfügbar. Session initialisieren.", []

        # Alten aktiven Run finalizen (superseded)
        active = rm.get_active_run()
        if active is not None:
            rm.finalize_active_run(reason="superseded_by_new_randomize")

        slots = self._local_player_slots()
        run = rm.create_run(input_rom, settings, player_slots=slots)
        if run is None:
            return False, "Konnte neuen Run nicht anlegen — siehe run_manager.log.", []

        base_args, _, _ = self._java_base_args()
        results: list[str] = []
        slot_dirs: list[dict] = []
        all_ok = True
        for target in run["rom_targets"]:
            slot = target["player_slot"]
            # Absolute Pfade erzwingen — Randomizer läuft mit cwd=jar_dir und würde
            # relative Ziele sonst unter jar_dir/... suchen ("path not writable").
            rom_target = Path(target["rom_path"]).resolve()
            log_target = Path(target["log_path"]).resolve()
            try:
                ok, msg = await self._randomize_one(
                    base_args, jar_dir, input_rom, settings,
                    rom_target, log_target, slot)
            except FileNotFoundError as err:
                ok = False
                msg = f"Slot {slot}: Konnte Prozess nicht starten: {err}"
                self.logger.error(msg)
                self.logger.error(traceback.format_exc())
            except Exception as err:
                ok = False
                msg = f"Slot {slot}: Unerwarteter Fehler: {type(err).__name__}, {err}"
                self.logger.error(msg)
                self.logger.error(traceback.format_exc())
            if not ok:
                all_ok = False
            else:
                slot_dirs.append({"slot": slot, "dir": str(rom_target.parent)})
            results.append(msg)

        run_id = run["run_id"]
        header = "Randomisierung abgeschlossen" if all_ok else "Randomisierung mit Fehlern"
        summary = f"{header} (Run {run_id}):\n" + "\n".join(results)

        # Log parsen — ersten erfolgreichen Slot nehmen
        for target in run["rom_targets"]:
            log_path = Path(target["log_path"]).resolve()
            if log_path.exists():
                log_ok, log_msg = self.parse_log(str(log_path))
                if log_ok:
                    summary += f"\n{log_msg}"
                break

        if all_ok:
            self.logger.info(summary)
        else:
            self.logger.error(summary)
        return all_ok, summary, slot_dirs
