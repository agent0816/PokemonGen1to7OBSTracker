from pathlib import Path
import logging
logging.basicConfig(level=logging.INFO, filename='logs/server.log',format='[%(asctime)s] %(levelname)s: %(message)s', filemode='w', encoding='utf-8')
import frontend.app as FEApp
import asyncio
import initialize_tree as init

def main():
    app = FEApp.TrackerApp()
    asyncio.run(app.async_run())

if __name__ == '__main__':
    init.init_logging_folder()
    init.init_config_folder()
    main()