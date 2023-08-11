import sys
import subprocess
import yaml
import requests
from frontend.widgets.mainmenu import MainMenu
from frontend.widgets.settingsmenu import SettingsMenu
from kivy.app import App
from kivy.core.window import Window
from kivy.config import Config
from kivy.uix.screenmanager import FadeTransition
from kivy.uix.screenmanager import ScreenManager
import backend.munchlax as client
import logging

logger = logging.getLogger(__name__)
logging_formatter = logging.Formatter("[%(asctime)s] %(levelname)s: %(message)s")

file_handler = logging.FileHandler("logs/frontend.log", "w")
file_handler.setFormatter(logging_formatter)
logger.addHandler(file_handler)

stream_handler = logging.StreamHandler(sys.stdout)
stream_handler.setFormatter(logging_formatter)
logger.addHandler(stream_handler)


Config.read("gui.ini")


class Screens(ScreenManager):
    def __init__(
        self,
        connector,
        OBSconnector,
        clientConnector,
        bizConnector,
        connectors,
        externalIPv4,
        externalIPv6,
        configsave,
        sp,
        rem,
        obs,
        bh,
        pl,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.transition = FadeTransition()
        self.add_widget(
            MainMenu(
                connector,
                OBSconnector,
                clientConnector,
                bizConnector,
                connectors,
                configsave,
                sp,
                rem,
                obs,
                bh,
                pl,
            )
        )
        self.add_widget(
            SettingsMenu(externalIPv4, externalIPv6, configsave, sp, rem, obs, bh, pl)
        )
        self.current = "MainMenu"


class TrackerApp(App):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        Window.bind(on_request_close=self.exit_check)
        Config.set("graphics", "resizable", 1)
        Config.set("graphics", "width", "600")
        Config.set("graphics", "height", "400")
        Config.set("input", "mouse", "mouse,multitouch_on_demand")
        self.connector = None
        self.OBSconnector = None
        self.clientConnector = None
        self.bizConnector = None
        self.connectors = []

    def build(self):
        try:
            self.externalIPv4 = requests.get("https://ifconfig.me/ip", timeout=1).text
        except requests.exceptions.Timeout:
            self.externalIPv4 = ""
        command = "(Get-NetIPAddress -AddressFamily IPv6 | Where-Object -Property PrefixOrigin -eq 'Dhcp').IPAddress"
        process = subprocess.Popen(
            ["powershell.exe", command],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=True,
        )
        stdout, stderr = process.communicate()
        self.externalIPv6 = stdout.decode()
        self.configsave = "backend/config/"
        self.bh = {}
        with open(f"{self.configsave}bh_config.yml") as file:
            self.bh = yaml.safe_load(file)
        self.obs = {}
        with open(f"{self.configsave}obs_config.yml") as file:
            self.obs = yaml.safe_load(file)
        self.sp = {}
        with open(f"{self.configsave}sprites.yml") as file:
            self.sp = yaml.safe_load(file)
        client.conf = self.sp
        self.pl = {}
        with open(f"{self.configsave}player.yml") as file:
            self.pl = yaml.safe_load(file)
        self.rem = {}
        with open(f"{self.configsave}remote.yml") as file:
            self.rem = yaml.safe_load(file)

        arguments = [
            self.connector,
            self.OBSconnector,
            self.clientConnector,
            self.bizConnector,
            self.connectors,
            self.externalIPv4,
            self.externalIPv6,
            self.configsave,
            self.sp,
            self.rem,
            self.obs,
            self.bh,
            self.pl,
        ]

        return Screens(*arguments)

    def exit_check(self, *args, **kwargs):
        self.save_config(f"{self.configsave}bh_config.yml", self.bh)
        self.save_config(f"{self.configsave}obs_config.yml", self.obs)
        self.save_config(f"{self.configsave}sprites.yml", self.sp)
        self.save_config(f"{self.configsave}player.yml", self.pl)
        self.save_config(f"{self.configsave}remote.yml", self.rem)

    def save_config(self, path, setting):
        with open(path, "w") as file:
            yaml.dump(setting, file)
