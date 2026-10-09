import os
import sys
import traceback
from datetime import datetime

# Bei kompilierter Onefile-EXE liegt das cwd nicht zwingend neben der EXE.
# Damit relative Pfade zu backend/data, backend/config und logs portabel funktionieren,
# wechseln wir ins Verzeichnis der EXE.
if "__compiled__" in dir():
    os.chdir(os.path.dirname(os.path.abspath(sys.argv[0])))

import initialize_tree as init
init.init_logging_folder()

CRASH_LOG = 'logs/crash_report.log'
ASYNCIO_LOG = 'logs/asyncio_errors.log'


def _crash_excepthook(exc_type, exc_value, exc_tb):
    with open(CRASH_LOG, 'a', encoding='utf-8') as f:
        f.write(f"\n{'='*60}\n[{datetime.now().isoformat()}] UNHANDLED EXCEPTION\n")
        traceback.print_exception(exc_type, exc_value, exc_tb, file=f)
    sys.__excepthook__(exc_type, exc_value, exc_tb)


sys.excepthook = _crash_excepthook


def _asyncio_exception_handler(loop, context):
    # Asyncio-Exceptions landen in eigener Datei, damit sie nicht das
    # Crash-Popup triggern — "silent task death" und Netz-Timeouts sind oft
    # nicht fatal und sollten den User nicht bei jedem Neustart anspringen.
    exc = context.get('exception')
    try:
        with open(ASYNCIO_LOG, 'a', encoding='utf-8') as f:
            f.write(f"\n{'='*60}\n[{datetime.now().isoformat()}] ASYNCIO EXCEPTION\n")
            f.write(f"Message: {context.get('message', 'n/a')}\n")
            if exc:
                traceback.print_exception(type(exc), exc, exc.__traceback__, file=f)
    except OSError as err:
        # I/O-Fehler im Handler darf den Loop-Exception-Handler nicht
        # zusaetzlich zerreissen. Nur loggen.
        logger.error(f"Konnte Asyncio-Exception nicht in {ASYNCIO_LOG} schreiben: {err}")
    logger.error(f"Asyncio exception: {context.get('message')}")


from backend.logging_setup import setup_root_logger, get_logger
setup_root_logger()
logger = get_logger('main')

# 30-Tage-Cleanup alter Support-Bundles beim Start. Nicht blockierend bei Fehlern.
try:
    from backend.log_bundler import cleanup_old_bundles
    _removed = cleanup_old_bundles(max_age_days=30)
    if _removed:
        logger.info(f"Support-Bundle-Cleanup: {_removed} Datei(en) entfernt")
except Exception as err:
    logger.warning(f"Support-Bundle-Cleanup fehlgeschlagen: {err}")

os.environ['KIVY_LOG_MODE'] = 'PYTHON'
from kivy.config import Config
Config.read("backend/kivy_config/gui.ini")
import frontend.app as FEApp
import asyncio
import logging
for logger_name, log in logging.Logger.manager.loggerDict.items():
    if isinstance(log, logging.Logger):
        if log.level == logging.NOTSET:
            log.setLevel(logging.INFO)


async def _run_app(app):
    # Event-Loop-Latency-Monitor startet VOR dem App-Lauf, damit auch die
    # ersten Boot-Phasen (Config-Load, Sprite-Pull, Server-Autostart)
    # vermessen werden. Diagnose fuer BizHawk-Lag: wenn der Kivy-Thread
    # einen Click-Handler lange ausfuehrt, blockiert das den asyncio-Loop
    # und damit auch comm.socketServerResponse() in der Lua.
    from backend.event_loop_monitor import (
        start_event_loop_monitor,
        stop_event_loop_monitor,
    )
    start_event_loop_monitor("kivy")
    try:
        await app.async_run()
    finally:
        # Nur den Kivy-Monitor stoppen — der BH-Monitor laeuft im
        # BH-Loop und wird mit BizhawkThread.shutdown() mitgecancelled.
        await stop_event_loop_monitor("kivy")


def main():
    app = FEApp.TrackerApp()
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.set_exception_handler(_asyncio_exception_handler)
    loop.run_until_complete(_run_app(app))


if __name__ == '__main__':
    init.init_config_folder()
    main()