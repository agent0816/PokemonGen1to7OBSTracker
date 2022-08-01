import os
import subprocess
import yaml
from kivy.app import App
from kivy.config import Config
from kivy.uix.screenmanager import Screen
from kivy.uix.screenmanager import ScreenManager
from kivy.uix.screenmanager import FadeTransition

bh = yaml.safe_load(open('backend/config/bh_config.yml'))
obs = yaml.safe_load(open('backend/config/obs_config.yml'))
sp = yaml.safe_load(open('backend/config/sprites.yml'))
cc = yaml.safe_load(open('backend/config/common_config.yml'))

class Screens(ScreenManager):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.transition = FadeTransition()

class MainMenu(Screen):
    def launchbh(self):
        pass
        # subprocess.Popen([bh['path'], f'--lua={os.path.abspath("./backend/obsautomation.lua")}', f'--socket_ip={bh["host"]}', f'--socket_port={bh["port"]}'])

    def launchserver(self):
        pass

class SettingsMenu(Screen):   
    def changesettingscreen(self, settings):
        if len(self.children[0].children) > 1:
            self.children[0].remove_widget(self.children[0].children[0])

        widget = self.checkSettingScreen(settings)

        self.children[0].add_widget(widget)

    def checkSettingScreen(self, settings):
        if settings == 'sprite':
            widget = SpriteSettings()
        elif settings == 'bizhawk':
            widget = BizhawkSettings()
        elif settings == 'obs':
            widget = OBSSettings()
        elif settings == 'remote':
            widget = RemoteSettings()
        elif settings == 'player':
            widget = PlayerSettings()
        return widget


class SpriteSettings(Screen):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.children[0].children[4].text = sp['path']
        self.children[0].children[2].state = 'down' if sp['animated'] else 'normal'
        for i in range(4):
            if i == ['route', 'lvl', 'team', 'dexnr'].index(sp['order']):
                self.children[0].children[0].children[i].state = 'down'

    def save_changes(self):
        sp['path'] = self.children[0].children[4].text
        sp['animated'] = self.children[0].children[2].state == 'down'
        for i in range(4):
            if self.children[0].children[0].children[i].state == 'down':
                sp['order'] = ['route', 'lvl', 'team', 'dexnr'][i]

        with open('backend/config/sprites.yml', 'w') as file:
            yaml.dump(sp, file)



class BizhawkSettings(Screen):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.children[0].children[4].text = bh['path']
        self.children[0].children[2].text = bh['host']
        self.children[0].children[0].text = bh['port']

    def save_changes(self):
        bh['path'] = self.children[0].children[4].text
        bh['host'] = self.children[0].children[2].text
        bh['port'] = self.children[0].children[0].text
        
        with open('backend/config/bh_config.yml', 'w') as file:
            yaml.dump(bh, file)

class OBSSettings(Screen):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.children[0].children[4].text = obs['password']
        self.children[0].children[2].text = obs['host']
        self.children[0].children[0].text = obs['port']

    def save_changes(self):
        obs['password'] = self.children[0].children[4].text
        obs['host'] = self.children[0].children[2].text
        obs['port'] = self.children[0].children[0].text
        
        with open('backend/config/obs_config.yml', 'w') as file:
            yaml.dump(obs, file)

class RemoteSettings(Screen):
    pass

class PlayerSettings(Screen):
    pass

class TrackerApp(App):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        Config.set('graphics', 'resizable', 0)
        Config.set('graphics', 'width', "600")
        Config.set('graphics', 'height', "600")

