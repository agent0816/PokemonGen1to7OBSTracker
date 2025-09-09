import initialize_tree as init
init.init_logging_folder()
import logging
logging.basicConfig(level=logging.INFO, filename='logs/main.log',format='[%(asctime)s] %(levelname)s: %(message)s', filemode='w', encoding='utf-8')
from kivy.config import Config
Config.read("backend/kivy_config/gui.ini")
import frontend.appmd as FEApp
# import frontend.app as FEApp
import asyncio
for logger_name, logger in logging.Logger.manager.loggerDict.items():
    if isinstance(logger, logging.Logger):
        if logger.level == logging.NOTSET:
            logger.setLevel(logging.INFO)

def main():
    app = FEApp.TrackerApp()
    asyncio.run(app.async_run())

if __name__ == '__main__':
    init.init_config_folder()
    main()