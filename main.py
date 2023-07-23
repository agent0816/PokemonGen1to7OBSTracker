from pathlib import Path
import initialize_tree as init
init.init_logging_folder()
import logging
logging.basicConfig(level=logging.DEBUG, filename='logs/main.log',format='[%(asctime)s] %(levelname)s: %(message)s', filemode='w', encoding='utf-8')
import frontend.app as FEApp
import asyncio

def main():
    app = FEApp.TrackerApp()
    asyncio.run(app.async_run())

if __name__ == '__main__':
    init.init_config_folder()
    main()