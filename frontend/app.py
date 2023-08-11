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
logging_formatter = logging.Formatter('[%(asctime)s] %(levelname)s: %(message)s')

file_handler = logging.FileHandler('logs/frontend.log', 'w')
file_handler.setFormatter(logging_formatter)
logger.addHandler(file_handler)

stream_handler = logging.StreamHandler(sys.stdout)
stream_handler.setFormatter(logging_formatter)
logger.addHandler(stream_handler)


Config.read('gui.ini')
connector = None
OBSconnector = None
clientConnector = None
bizConnector = None
connectors = []

class Screens(ScreenManager):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.transition = FadeTransition()
        self.add_widget(MainMenu(connector, OBSconnector, clientConnector, bizConnector, connectors, configsave, sp, rem, obs, bh, pl))
        self.add_widget(SettingsMenu(externalIPv4, externalIPv6, configsave, sp, rem, obs, bh, pl))
        self.current = "MainMenu"

class TrackerApp(App):  
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        Window.bind(on_request_close=self.exit_check)
        Config.set('graphics', 'resizable', 1)
        Config.set('graphics', 'width', "600")
        Config.set('graphics', 'height', "400")
        Config.set('input', 'mouse', 'mouse,multitouch_on_demand')

    def build(self):
        global externalIPv4
        try:
            externalIPv4 = requests.get('https://ifconfig.me/ip', timeout=1).text
        except requests.exceptions.Timeout:
            externalIPv4 = ''
        global externalIPv6
        command = "(Get-NetIPAddress -AddressFamily IPv6 | Where-Object -Property PrefixOrigin -eq \'Dhcp\').IPAddress"
        process = subprocess.Popen(["powershell.exe",command], stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=True)
        stdout, stderr = process.communicate()
        externalIPv6 = stdout.decode()
        global configsave
        configsave = 'backend/config/'
        global bh
        bh = {}
        with open(f"{configsave}bh_config.yml") as file:
            bh = yaml.safe_load(file)
        global obs
        obs = {}
        with open(f"{configsave}obs_config.yml") as file:
            obs = yaml.safe_load(file)
        global sp
        sp = {}
        with open(f"{configsave}sprites.yml") as file:
            sp = yaml.safe_load(file)
        client.conf = sp
        global pl
        pl = {}
        with open(f"{configsave}player.yml") as file:
            pl = yaml.safe_load(file)
        global rem
        rem = {}
        with(open(f"{configsave}remote.yml")) as file:
            rem = yaml.safe_load(file)

        return Screens()

    def exit_check(self, *args, **kwargs):
        self.save_config(f"{configsave}bh_config.yml", bh)
        self.save_config(f"{configsave}obs_config.yml", obs)
        self.save_config(f"{configsave}sprites.yml", sp)
        self.save_config(f"{configsave}player.yml", pl)
        self.save_config(f"{configsave}remote.yml", rem)

    def save_config(self, path, setting):
        with open(path, 'w') as file:
            yaml.dump(setting, file)