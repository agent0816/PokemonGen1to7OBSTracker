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


def _create_rotating_handler(log_file: str, max_bytes: int, backup_count: int,
                              formatter: logging.Formatter) -> RotatingFileHandler:
    fh = RotatingFileHandler(log_file, mode='a', maxBytes=max_bytes,
                              backupCount=backup_count, encoding='utf-8')
    if os.path.exists(log_file) and os.path.getsize(log_file) > 0:
        fh.doRollover()
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
