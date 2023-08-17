import asyncio
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
from backend.arceus import Arceus
from backend.bizhawk import Bizhawk
from backend.munchlax import Munchlax
from backend.obs import OBS
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
        arceus,
        bizhawk,
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
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.transition = FadeTransition()
        self.add_widget(
            MainMenu(arceus, bizhawk, bizhawk_instances, munchlax, obs_websocket, configsave, sp, rem, obs, bh, pl)
        )
        self.add_widget(
            SettingsMenu(arceus, bizhawk, munchlax, obs_websocket, externalIPv4, externalIPv6, configsave, sp, rem, obs, bh, pl)
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
        self.pl = {}
        with open(f"{self.configsave}player.yml") as file:
            self.pl = yaml.safe_load(file)
        self.rem = {}
        with open(f"{self.configsave}remote.yml") as file:
            self.rem = yaml.safe_load(file)

        self.arceus = Arceus("", self.rem["client_port"])
        self.bizhawk = Bizhawk(self.bh["host"], self.bh["port"])
        self.bizhawk_instances = []
        
        ip_to_connect = '127.0.0.1' if self.rem["start_server"] else self.rem["server_ip_adresse"]
        port_to_connect = self.rem["client_port"] if self.rem["start_server"] else self.rem["server_port"]
        self.munchlax = Munchlax(
            ip_to_connect, port_to_connect, self.sp
        )
        self.obs_websocket = OBS(
            self.obs["host"],
            self.obs["port"],
            self.obs["password"],
            self.munchlax,
            self.sp,
        )

        arguments = [
            self.arceus,
            self.bizhawk,
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
        asyncio.create_task(asyncio.wait([self.bizhawk.stop(), self.obs_websocket.disconnect(), self.munchlax.disconnect(), self.arceus.stop()]))

    def save_config(self, path, setting):
        with open(path, "w") as file:
            yaml.dump(setting, file)
