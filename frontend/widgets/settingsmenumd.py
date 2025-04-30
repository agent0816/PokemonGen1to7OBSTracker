from kivymd.uix.screen import MDScreen
from kivymd.uix.label import MDLabel

class SettingsMenu(MDScreen):
    def __init__(self, arceus, bizhawk, munchlax, obs_websocket, externalIPv4, externalIPv6, configsave, sp, rem, obs, bh, pl, app_version, **kwargs):
        super().__init__(**kwargs)

        self.name = "SettingsMenu"

        self.add_widget(MDLabel(text="SettingsMenu"))
