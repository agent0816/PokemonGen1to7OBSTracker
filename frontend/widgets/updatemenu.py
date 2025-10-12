import asyncio
import ctypes
import logging
import os
import sys
from kivy.uix.button import Button
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.label import Label
from kivy.uix.popup import Popup
from kivy.uix.progressbar import ProgressBar
from kivy.uix.screenmanager import Screen
from pyupdater.client import Client
from client_config import ClientConfig

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
logging_formatter = logging.Formatter('[%(asctime)s] %(levelname)s: %(message)s')

file_handler = logging.FileHandler('logs/frontend.log', 'w')
file_handler.setFormatter(logging_formatter)
logger.addHandler(file_handler)

stream_handler = logging.StreamHandler(sys.stdout)
stream_handler.setFormatter(logging_formatter)
logger.addHandler(stream_handler)

class Update(Screen):
    def __init__(self, app_name, app_version, **kwargs):
        super().__init__(**kwargs)
        self.app_name = app_name
        self.app_version = app_version
        self.name = "Update"

    def check_for_update(self):
        client = Client(ClientConfig(), refresh=True)
        client.add_progress_hook(self.print_status_info)
        app_update = client.update_check(self.app_name, self.app_version)
        logger.info(f"{app_update=}")
        if app_update:
            self.show_update_popup(app_update)
        else:
            self.parent.current = "SessionMenu"

    def show_update_popup(self, app_update):
        box = BoxLayout(orientation='vertical')
        text = 'Update verfügbar!'

        box.add_widget(Label(text=text))

        self.progress_bar = ProgressBar(max=100)
        box.add_widget(self.progress_bar)

        btn_layout = BoxLayout()
        download_btn = Button(text='Download')
        download_btn.bind(on_press=lambda x: self.download_update(app_update)) # type: ignore
        btn_layout.add_widget(download_btn)

        cancel_btn = Button(text='Abbrechen')
        cancel_btn.bind(on_press=self.cancel_update) # type: ignore
        btn_layout.add_widget(cancel_btn)

        box.add_widget(btn_layout)

        self.popup = Popup(title='Update verfügbar!', content=box,
                           size_hint=(None, None), size=(600, 300), auto_dismiss=False)
        self.popup.open()

    async def async_download_update(self, app_update):
        
            logger.info("Download wird gestartet.")
            await asyncio.get_event_loop().run_in_executor(None, app_update.download)
            if app_update.is_downloaded():
                logger.info("Update wird entpackt")
                extracted_files = await asyncio.get_event_loop().run_in_executor(None, app_update.win_extract)
                logger.info("Starten des Updaters als Administrator")
                app_path = os.path.abspath(os.getcwd())
                update_path = app_update.update_folder
                updater_path = os.path.join(app_path, "backend", "updater", "updater.exe")
                args = f'--app-path "{app_path}" --update-path "{update_path}" --extracted-files "{extracted_files}"'
                ctypes.windll.shell32.ShellExecuteW(None, "runas", updater_path, args, None, 1)
                logger.info(f"{args=}")
                sys.exit(0)

    def download_update(self, app_update, admin = False):
        asyncio.ensure_future(self.async_download_update(app_update))
    
    def cancel_update(self, instance):
        self.popup.dismiss()
        self.parent.current = "MainMenu"

    def print_status_info(self, info):
        percent_complete = info.get(u'percent_complete')
        # Aktualisieren des ProgressBar-Werts
        self.progress_bar.value = float(percent_complete)