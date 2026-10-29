from logging.handlers import RotatingFileHandler
import logging
import os
import sys
import yaml

_LOG_SETTINGS_PATH = 'backend/config/log_settings.yml'
_root_initialized = False
# Registry aller Modul-Logger, die via get_logger() gebaut wurden. Ermoeglicht
# der GUI, alle bekannten Module aufzulisten und deren Level live umzustellen.
# Wert: der zugehoerige RotatingFileHandler (fuer .setLevel-Live-Apply).
_module_handlers: dict[str, RotatingFileHandler] = {}
# Log-Dateien, die in diesem Prozess schon beim Start rotiert wurden. Mehrere
# Module teilen sich eine Datei (z.B. logs/frontend.log) — ein spaeter (lazy)
# importiertes Modul darf die Datei nicht erneut rotieren, waehrend andere
# Handler sie offen halten (Windows: WinError 32 beim os.rename).
_rolled_over_files: set[str] = set()

LEVEL_MAP = {
    'DEBUG': logging.DEBUG,
    'INFO': logging.INFO,
    'WARNING': logging.WARNING,
    'ERROR': logging.ERROR,
}


def _load_settings() -> dict:
    try:
        if os.path.exists(_LOG_SETTINGS_PATH):
            with open(_LOG_SETTINGS_PATH, encoding='utf-8') as f:
                return yaml.safe_load(f) or {}
    except Exception:
        pass
    return {}


def _save_settings(settings: dict):
    os.makedirs(os.path.dirname(_LOG_SETTINGS_PATH), exist_ok=True)
    with open(_LOG_SETTINGS_PATH, 'w', encoding='utf-8') as f:
        yaml.dump(settings, f, sort_keys=True)


def _load_console_level() -> int:
    settings = _load_settings()
    level_name = settings.get('console_level', 'INFO')
    return LEVEL_MAP.get(level_name, logging.INFO)


def _save_console_level(level_name: str):
    settings = _load_settings()
    settings['console_level'] = level_name
    _save_settings(settings)


def _load_module_levels() -> dict:
    """Modul-spezifische File-Log-Level aus log_settings.yml.

    Format::

        module_levels:
          overlay_server: DEBUG
          munchlax: INFO
    """
    settings = _load_settings()
    return settings.get('module_levels', {}) or {}


def _module_key(logger_name: str) -> str:
    """Modul-Kurzname (letztes Segment) fuer YAML-Lookup.

    ``get_logger`` bekommt typischerweise ``__name__`` (z.B.
    ``backend.classes.overlay_server``). Der YAML-Key ist der Kurzname
    (``overlay_server``), passt zu den Log-File-Namen.
    """
    return logger_name.rsplit('.', 1)[-1]


class _FastRotatingFileHandler(RotatingFileHandler):
    """RotatingFileHandler ohne Dateisystem-Abfrage pro Log-Zeile.

    Die Stdlib prüft in ``shouldRollover`` bei JEDEM Record per
    ``os.path.exists`` + ``os.path.isfile``, ob die Logdatei eine reguläre
    Datei ist (bpo-45401). Unter Windows kostete das in langsamen UI-Frames
    16–55 % der Zeit (Perf-Messung 2026-10-28). Die Datei-Art ändert sich zur
    Laufzeit nicht, daher wird sie hier einmal geprüft und gecacht; der
    Größen-Check bleibt unverändert.
    """

    def __init__(self, *args, **kwargs) -> None:
        self._regular_file: bool | None = None
        super().__init__(*args, **kwargs)

    def shouldRollover(self, record: logging.LogRecord) -> bool:
        if self._regular_file is None:
            self._regular_file = not (os.path.exists(self.baseFilename)
                                      and not os.path.isfile(self.baseFilename))
        # Nie etwas anderes als reguläre Dateien rotieren (z.B. /dev/null, NUL)
        if not self._regular_file:
            return False
        if self.stream is None:  # delay war gesetzt
            self.stream = self._open()
        if self.maxBytes > 0:
            msg = f"{self.format(record)}\n"
            self.stream.seek(0, 2)  # Windows: nicht POSIX-konformes Append-Verhalten
            if self.stream.tell() + len(msg) >= self.maxBytes:
                return True
        return False


def _resolve_log_path(log_file: str) -> str:
    """Log-Pfad, optional umgeleitet in ``TRACKER_PYTEST_LOG_DIR`` (nur Dateiname bleibt).

    Fuer pytest (tests/conftest.py): Tests schreiben sonst in die echten
    logs/*.log und rotieren sie beim ersten Oeffnen — das verfaelscht bzw.
    verschiebt die Logs eines parallel laufenden Trackers. Eigener Name, weil
    TRACKER_LOG_DIR bereits fuer die Lua-Logs der BizHawk-Kindprozesse steht.
    """
    override = os.environ.get("TRACKER_PYTEST_LOG_DIR")
    if not override:
        return log_file
    try:
        os.makedirs(override, exist_ok=True)
    except OSError as err:
        # Logging-Setup darf den Aufrufer nie crashen — dann eben der Originalpfad.
        sys.stderr.write(f"[logging_setup] Log-Override {override} unbrauchbar: {err}\n")
        return log_file
    return os.path.join(override, os.path.basename(log_file))


def _create_rotating_handler(log_file: str, max_bytes: int, backup_count: int,
                              formatter: logging.Formatter) -> RotatingFileHandler:
    log_file = _resolve_log_path(log_file)
    fh = _FastRotatingFileHandler(log_file, mode='a', maxBytes=max_bytes,
                                  backupCount=backup_count, encoding='utf-8')
    log_key = os.path.normcase(os.path.abspath(log_file))
    if log_key not in _rolled_over_files:
        _rolled_over_files.add(log_key)
        if os.path.exists(log_file) and os.path.getsize(log_file) > 0:
            # Rollover ist best-effort: der os.rename kann prozessübergreifend
            # fehlschlagen (Windows WinError 32 — Datei von einer anderen
            # Instanz/einem anderen Prozess offen; auch Antivirus). Ein
            # Logging-Setup-Fehler darf NIEMALS den Aufrufer crashen — der
            # Handler wurde bereits im Append-Modus geöffnet, also einfach
            # ohne frische Rotation weiterschreiben (zuvor propagierte die
            # Exception bis in PokedexDB.__init__ und killte den Reload-Task).
            try:
                fh.doRollover()
            except OSError as err:
                sys.stderr.write(
                    f"[logging_setup] Rollover für {log_file} übersprungen: {err}\n"
                )
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(formatter)
    return fh


def setup_root_logger(console_level: int = None):
    global _root_initialized
    if _root_initialized:
        return

    if console_level is None:
        console_level = _load_console_level()

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)

    root_formatter = logging.Formatter('[%(asctime)s] %(name)s %(levelname)s: %(message)s')

    fh = _create_rotating_handler('logs/main.log', max_bytes=5 * 1024 * 1024,
                                   backup_count=5, formatter=root_formatter)
    root.addHandler(fh)

    sh = logging.StreamHandler(sys.stdout)
    sh.setLevel(console_level)
    sh.setFormatter(root_formatter)
    root.addHandler(sh)

    _root_initialized = True


def set_console_level(level_name: str):
    level = LEVEL_MAP.get(level_name, logging.INFO)
    root = logging.getLogger()
    for handler in root.handlers:
        if isinstance(handler, logging.StreamHandler) and not isinstance(handler, logging.FileHandler):
            handler.setLevel(level)
    _save_console_level(level_name)


def get_console_level() -> str:
    root = logging.getLogger()
    for handler in root.handlers:
        if isinstance(handler, logging.StreamHandler) and not isinstance(handler, logging.FileHandler):
            for name, val in LEVEL_MAP.items():
                if handler.level == val:
                    return name
    try:
        if os.path.exists(_LOG_SETTINGS_PATH):
            with open(_LOG_SETTINGS_PATH, encoding='utf-8') as f:
                settings = yaml.safe_load(f) or {}
            return settings.get('console_level', 'INFO')
    except Exception:
        pass
    return 'INFO'


def get_logger(name: str, log_file: str = None) -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    logger.setLevel(logging.DEBUG)
    if log_file:
        formatter = logging.Formatter('[%(asctime)s] %(levelname)s: %(message)s')
        fh = _create_rotating_handler(log_file, max_bytes=2 * 1024 * 1024,
                                       backup_count=3, formatter=formatter)
        # Modul-spezifisches Override: wenn log_settings.yml einen Eintrag hat,
        # den File-Handler auf diesen Level setzen (statt Default DEBUG). Der
        # Logger selbst bleibt bei DEBUG, damit spaetere set_module_level()-
        # Calls den File-Handler dynamisch hoch- oder runterschalten koennen
        # ohne dass der Logger dazwischenfunkt.
        module_key = _module_key(name)
        level_name = _load_module_levels().get(module_key)
        if level_name and level_name in LEVEL_MAP:
            fh.setLevel(LEVEL_MAP[level_name])
        logger.addHandler(fh)
        _module_handlers[module_key] = fh
    logger.propagate = True
    return logger


def list_known_modules() -> list[str]:
    """Alle Modul-Kurznamen mit registriertem File-Handler.

    Wird von der GUI genutzt (Dev-Section unter Settings): sobald ein Modul
    seinen Logger via ``get_logger(__name__, './logs/xyz.log')`` gebaut hat,
    taucht es hier auf und kann per Spinner umgestellt werden.
    """
    return sorted(_module_handlers.keys())


def get_module_level(module_key: str) -> str:
    """Aktueller Log-Level des File-Handlers fuer ``module_key``.

    Falls das Modul noch keinen Handler registriert hat (get_logger noch nicht
    aufgerufen), Fallback auf YAML-Wert oder 'DEBUG'.
    """
    fh = _module_handlers.get(module_key)
    if fh is not None:
        for name, val in LEVEL_MAP.items():
            if fh.level == val:
                return name
    yaml_level = _load_module_levels().get(module_key)
    if yaml_level in LEVEL_MAP:
        return yaml_level
    return 'DEBUG'


def set_module_level(module_key: str, level_name: str):
    """Setzt File-Log-Level fuer ein Modul live + persistiert nach YAML.

    Live-Apply funktioniert nur wenn der Handler bereits registriert ist
    (Modul-Logger via get_logger gebaut). Ansonsten wird nur der YAML-Wert
    gesetzt und beim naechsten Prozess-Start beim get_logger-Init angewendet.
    """
    level = LEVEL_MAP.get(level_name)
    if level is None:
        return
    fh = _module_handlers.get(module_key)
    if fh is not None:
        fh.setLevel(level)
    settings = _load_settings()
    module_levels = dict(settings.get('module_levels', {}) or {})
    module_levels[module_key] = level_name
    settings['module_levels'] = module_levels
    _save_settings(settings)
