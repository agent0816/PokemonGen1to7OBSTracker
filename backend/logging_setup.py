from logging.handlers import RotatingFileHandler
import logging
import os
import sys
import yaml

_LOG_SETTINGS_PATH = 'backend/config/log_settings.yml'
_root_initialized = False

LEVEL_MAP = {
    'DEBUG': logging.DEBUG,
    'INFO': logging.INFO,
    'WARNING': logging.WARNING,
    'ERROR': logging.ERROR,
}


def _load_console_level() -> int:
    try:
        if os.path.exists(_LOG_SETTINGS_PATH):
            with open(_LOG_SETTINGS_PATH, encoding='utf-8') as f:
                settings = yaml.safe_load(f) or {}
            level_name = settings.get('console_level', 'INFO')
            return LEVEL_MAP.get(level_name, logging.INFO)
    except Exception:
        pass
    return logging.INFO


def _save_console_level(level_name: str):
    os.makedirs(os.path.dirname(_LOG_SETTINGS_PATH), exist_ok=True)
    with open(_LOG_SETTINGS_PATH, 'w', encoding='utf-8') as f:
        yaml.dump({'console_level': level_name}, f)


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
        logger.addHandler(fh)
    logger.propagate = True
    return logger
