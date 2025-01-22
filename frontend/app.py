import asyncio
from dataclasses import dataclass
import sys
import subprocess
import yaml
import requests
import logging
from frontend.widgets.mainmenu import MainMenu
from frontend.widgets.sessionsmenu import SessionMenu
from frontend.widgets.settingsmenu import SettingsMenu
from frontend.widgets.updatemenu import Update
from kivy.app import App
from kivy.core.window import Window

# from kivy.logger import Logger
# from kivy.logger import LOG_LEVELS
# Logger.setLevel(logging.INFO)
from kivy.uix.screenmanager import FadeTransition
from kivy.uix.screenmanager import ScreenManager
from backend.classes.arceus import Arceus
from backend.classes.bizhawk import Bizhawk
from backend.classes.citrahandler import CitraHandler
from backend.classes.munchlax import Munchlax
from backend.classes.obs import OBS

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
logging_formatter = logging.Formatter("[%(asctime)s] %(levelname)s: %(message)s")

file_handler = logging.FileHandler("logs/frontend.log", "w")
file_handler.setFormatter(logging_formatter)
logger.addHandler(file_handler)

stream_handler = logging.StreamHandler(sys.stdout)
stream_handler.setFormatter(logging_formatter)
logger.addHandler(stream_handler)

APP_NAME = "PokemonOBSTracker"
APP_VERSION = "0.7.5"


class Screens(ScreenManager):
    def __init__(
        self,
        arceus,
        bizhawk,
        citra,
        bizhawk_instances,
        munchlax,
        obs_websocket,
        externalIPv4,
        externalIPv6,
        configsave,
        sp,
        rem,
        obs,
        bh,
        pl,
        session_list,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.transition = FadeTransition()
        update_menu = Update(APP_NAME, APP_VERSION)
        self.add_widget(update_menu)
        main_menu = MainMenu(
            arceus,
            bizhawk,
            citra,
            bizhawk_instances,
            munchlax,
            obs_websocket,
            configsave,
            sp,
            rem,
            obs,
            bh,
            pl,
            APP_VERSION,
        )
        self.add_widget(main_menu)
        settings_menu = SettingsMenu(
            arceus,
            bizhawk,
            munchlax,
            obs_websocket,
            externalIPv4,
            externalIPv6,
            configsave,
            sp,
            rem,
            obs,
            bh,
            pl,
            APP_VERSION,
        )
        self.add_widget(settings_menu)
        session_menu = SessionMenu(session_list, main_menu, settings_menu,configsave, sp, rem, obs, bh, pl, APP_VERSION)
        self.add_widget(session_menu)
        self.current = "Update"
        update_menu.check_for_update()

@dataclass
class MutableString(object):
    text: str

    def __repr__(self) -> str:
        return self.text
    def __str__(self) -> str:
        return self.text

class TrackerApp(App):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        Window.bind(on_request_close=self.exit_check)

    def build(self):
        try:
            self.externalIPv4 = requests.get("https://ipinfo.io/ip", timeout=1).text
        except requests.exceptions.Timeout:
            self.externalIPv4 = ""
        command = "(Get-NetIPAddress -AddressFamily IPv6 | Where-Object -Property PrefixOrigin -eq 'Dhcp').IPAddress"
        process = subprocess.Popen(
            ["powershell.exe", command],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        stdout, stderr = process.communicate()
        self.externalIPv6 = stdout.decode()
        self.configsave = MutableString("backend/config/default/")
        self.bh = {}
        with open(f"{self.configsave}bh_config.yml") as file:
            self.bh = yaml.safe_load(file)
        self.obs = {}
        with open(f"{self.configsave}obs_config.yml") as file:
            self.obs = yaml.safe_load(file)
        self.sp = {}
        with open(f"{self.configsave}sprites.yml") as file:
            self.sp = yaml.safe_load(file)
        self.pl = {}
        with open(f"{self.configsave}player.yml") as file:
            self.pl = yaml.safe_load(file)
        self.rem = {}
        with open(f"{self.configsave}remote.yml") as file:
            self.rem = yaml.safe_load(file)
        self.session_list = []
        with open(f"{self.configsave}../session_list.yml") as file:
            self.session_list = yaml.safe_load(file)

        self.arceus = Arceus("", self.rem["client_port"], self.rem)
        self.bizhawk = Bizhawk(self.bh["host"], self.bh["port"], self.bh)
        self.bizhawk_instances = []

        self.citra = CitraHandler()

        ip_to_connect = (
            "127.0.0.1" if self.rem["start_server"] else self.rem["server_ip_adresse"]
        )
        port_to_connect = (
            self.rem["client_port"]
            if self.rem["start_server"]
            else self.rem["server_port"]
        )
        self.munchlax = Munchlax(ip_to_connect, port_to_connect, self.rem, self.sp, self.pl)
        self.obs_websocket = OBS(
            self.obs["host"],
            self.obs["port"],
            self.obs["password"],
            self.munchlax,
            self.sp,
            self.obs,
        )

        arguments = [
            self.arceus,
            self.bizhawk,
            self.citra,
            self.bizhawk_instances,
            self.munchlax,
            self.obs_websocket,
            self.externalIPv4,
            self.externalIPv6,
            self.configsave,
            self.sp,
            self.rem,
            self.obs,
            self.bh,
            self.pl,
            self.session_list,
        ]

        return Screens(*arguments)

    def exit_check(self, *args, **kwargs):
        self.save_config(f"{self.configsave}bh_config.yml", self.bh)
        self.save_config(f"{self.configsave}obs_config.yml", self.obs)
        self.save_config(f"{self.configsave}sprites.yml", self.sp)
        self.save_config(f"{self.configsave}player.yml", self.pl)
        self.save_config(f"{self.configsave}remote.yml", self.rem)

        for bizhawk in self.bizhawk_instances:
            bizhawk.terminate()
        tasks = [
            asyncio.create_task(self.arceus.stop()),
            asyncio.create_task(self.citra.stop()),
            asyncio.create_task(self.bizhawk.stop()),
            asyncio.create_task(self.obs_websocket.disconnect()),
            asyncio.create_task(self.munchlax.disconnect()),
        ]
        asyncio.create_task(asyncio.wait(tasks, timeout=3))

    def save_config(self, path, setting):
        with open(path, "w") as file:
            yaml.dump(setting, file)
