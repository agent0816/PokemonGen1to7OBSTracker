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


def _crash_excepthook(exc_type, exc_value, exc_tb):
    with open(CRASH_LOG, 'a', encoding='utf-8') as f:
        f.write(f"\n{'='*60}\n[{datetime.now().isoformat()}] UNHANDLED EXCEPTION\n")
        traceback.print_exception(exc_type, exc_value, exc_tb, file=f)
    sys.__excepthook__(exc_type, exc_value, exc_tb)


sys.excepthook = _crash_excepthook


def _asyncio_exception_handler(loop, context):
    exc = context.get('exception')
    with open(CRASH_LOG, 'a', encoding='utf-8') as f:
        f.write(f"\n{'='*60}\n[{datetime.now().isoformat()}] ASYNCIO EXCEPTION\n")
        f.write(f"Message: {context.get('message', 'n/a')}\n")
        if exc:
            traceback.print_exception(type(exc), exc, exc.__traceback__, file=f)
    logger.error(f"Asyncio exception: {context.get('message')}")


from backend.logging_setup import setup_root_logger, get_logger
setup_root_logger()
logger = get_logger('main')

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


def main():
    app = FEApp.TrackerApp()
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.set_exception_handler(_asyncio_exception_handler)
    loop.run_until_complete(app.async_run())


if __name__ == '__main__':
    init.init_config_folder()
    main()