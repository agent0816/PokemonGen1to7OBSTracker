import asyncio
import shutil
import sys
import logging
import traceback
from pathlib import Path


class RandomizerController:
    def __init__(self, rnd: dict, pl: dict):
        self.rnd = rnd
        self.pl = pl
        self.logger = self._init_logging()
        self._process: asyncio.subprocess.Process | None = None

    def _init_logging(self) -> logging.Logger:
        logger = logging.getLogger(__name__)
        logger.setLevel(logging.INFO)

        logging_formatter = logging.Formatter('[%(asctime)s] %(levelname)s: %(message)s')

        file_handler = logging.FileHandler('./logs/randomizer_controller.log', 'w')
        file_handler.setFormatter(logging_formatter)
        logger.addHandler(file_handler)

        stream_handler = logging.StreamHandler(sys.stdout)
        stream_handler.setFormatter(logging_formatter)
        logger.addHandler(stream_handler)

        return logger

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

    def _build_command(self) -> tuple[list[str], str, str | None]:
        """Gibt (args, cwd, fehler) zurück."""
        base_args, jar_dir, error = self._java_base_args()
        if error:
            return [], "", error

        settings = self.rnd.get("settings_path", "")
        if not settings or not Path(settings).exists():
            return [], "", "Einstellungsdatei (.rnqs) nicht gefunden. Bitte in den Einstellungen angeben."

        rom = self.rnd.get("rom_path", "")
        if not rom or not Path(rom).exists():
            return [], "", "Input-ROM nicht gefunden. Bitte den ROM-Pfad in den Einstellungen angeben."

        output_dir = self.rnd.get("output_path", "") or str(Path(rom).parent)
        rom_stem = Path(rom).stem
        output = str(Path(output_dir) / f"{rom_stem}_randomized")

        args = base_args + ["cli", "-i", rom, "-o", output, "-s", settings, "-l"]

        return args, jar_dir, None

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

    async def randomize(self) -> tuple[bool, str]:
        args, cwd, error = self._build_command()
        if error:
            self.logger.error(f"Validierungsfehler: {error}")
            return False, error

        try:
            self.logger.info(f"Starte Randomisierung: {' '.join(args)}")
            self._process = await asyncio.create_subprocess_exec(
                *args,
                cwd=cwd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await self._process.communicate()

            if self._process.returncode == 0:
                msg = "Randomisierung erfolgreich abgeschlossen!"
                self.logger.info(msg)
                if stdout:
                    self.logger.info(f"stdout: {stdout.decode(errors='replace').strip()}")
                return True, msg
            else:
                error_msg = stderr.decode(errors="replace").strip()
                if not error_msg:
                    error_msg = stdout.decode(errors="replace").strip()
                msg = f"Randomisierung fehlgeschlagen (Exit-Code {self._process.returncode}):\n{error_msg}"
                self.logger.error(msg)
                return False, msg
        except FileNotFoundError as err:
            msg = f"Konnte Prozess nicht starten: {err}"
            self.logger.error(msg)
            self.logger.error(traceback.format_exc())
            return False, msg
        except Exception as err:
            msg = f"Unerwarteter Fehler bei der Randomisierung: {type(err).__name__}, {err}"
            self.logger.error(msg)
            self.logger.error(traceback.format_exc())
            return False, msg
        finally:
            self._process = None
