import os
import subprocess
import weakref
import yaml
from kivy.app import App
from kivy.uix.anchorlayout import AnchorLayout
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.checkbox import CheckBox
from kivy.uix.gridlayout import GridLayout
from kivy.uix.textinput import TextInput
from kivy.config import Config
Config.read('gui.ini')
from kivy.uix.label import Label
from kivy.uix.screenmanager import Screen
from kivy.uix.screenmanager import ScreenManager
from kivy.uix.screenmanager import FadeTransition
from kivy.core.window import Window
import backend.server as server
import backend.pokedecoder
import threading

class Screens(ScreenManager):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.transition = FadeTransition()

class MainMenu(Screen):
    def launchbh(self):
        for i in range(pl['player_count']):
            if not pl[f'remote_{i+1}']:
                subprocess.Popen([bh['path'], f'--lua={os.path.abspath(f"./backend/Player{i+1}.lua")}', f'--socket_ip={bh["host"]}', f'--socket_port={bh["port"]}'])

    def launchserver(self):
        if not connector.is_alive():
            connector.start()

class SettingsMenu(Screen):   
    def changesettingscreen(self, settings):
        if len(self.children[0].children) > 1:
            curScreen = self.children[0].children[0]
            if type(curScreen) is not Screen:
                type(curScreen).save_changes(curScreen)
            self.children[0].remove_widget(self.children[0].children[0])

        widget = self.checkSettingScreen(settings)

        self.children[0].add_widget(widget)

    def checkSettingScreen(self, settings):
        if settings == 'sprite':
            widget = SpriteSettings()
        elif settings == 'games':
            widget = SpritesGames()
        elif settings == 'bizhawk':
            widget = BizhawkSettings()
        elif settings == 'obs':
            widget = OBSSettings()
        elif settings == 'remote':
            widget = RemoteSettings()
        elif settings == 'player':
            widget = PlayerSettings()
        return widget # type: ignore

class SpriteSettings(Screen):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.ids.common_path.text = sp['common_path']
        self.ids.animated_check.state = 'down' if sp['animated'] else 'normal'
        self.ids.game_sprites_check.state = 'down' if sp['single_path_check'] else 'normal'
        self.sprite_paths_setting(initializing=True)
        for i in range(4):
            if i == ['route', 'lvl', 'team', 'dexnr'].index(sp['order']):
                self.ids.sortierung.children[i].state = 'down'

    def sprite_paths_setting(self, initializing=False):
        if self.ids.game_sprites_check.state == 'normal':
            self.ids.game_sprites.opacity = 0
            self.ids.game_sprites.disabled = True
        else:
            self.ids.game_sprites.opacity = 1
            self.ids.game_sprites.disabled = False
        if not initializing:
            self.save_changes()

    def save_changes(self):
        sp['common_path'] = self.ids.common_path.text
        sp['single_path_check'] = self.ids.game_sprites_check.state == 'down'
        sp['animated'] = self.ids.animated_check.state == 'down'
        for i in range(4):
            if self.ids.sortierung.children[i].state == 'down':
                sp['order'] = ['route', 'lvl', 'team', 'dexnr'][i]
                backend.pokedecoder.setorder(sp['order'])
        server.spriteconf = sp
        server.update = True
        with open(f"{configsave}sprites.yml", 'w') as file:
            yaml.dump(sp, file)

class SpritesGames(Screen):
    def __init__(self,**kwargs):
        super().__init__(**kwargs)
        self.ids.gen1_red.text = sp['red']
        self.ids.gen1_yellow.text = sp['yellow']
        self.ids.gen2_silver.text = sp['silver']
        self.ids.gen2_gold.text = sp['gold']
        self.ids.gen2_crystal.text = sp['crystal']
        self.ids.gen3_ruby.text = sp['ruby']
        self.ids.gen3_emerald.text = sp['emerald']
        self.ids.gen3_firered.text = sp['firered']
        self.ids.gen4_diamond.text = sp['diamond']
        self.ids.gen4_platinum.text = sp['platinum']
        self.ids.gen4_heartgold.text = sp['heartgold']
        self.ids.gen5_black.text = sp['black']

    def save_changes(self):
        sp['red'] = self.ids.gen1_red.text
        sp['yellow'] = self.ids.gen1_yellow.text
        sp['silver'] = self.ids.gen2_silver.text
        sp['gold'] = self.ids.gen2_gold.text
        sp['crystal'] = self.ids.gen2_crystal.text
        sp['ruby'] = self.ids.gen3_ruby.text
        sp['emerald'] = self.ids.gen3_emerald.text
        sp['firered'] = self.ids.gen3_firered.text
        sp['diamond'] = self.ids.gen4_diamond.text
        sp['platinum'] = self.ids.gen4_platinum.text
        sp['heartgold'] = self.ids.gen4_heartgold.text
        sp['black'] = self.ids.gen5_black.text

        with open(f"{configsave}sprites.yml", 'w') as file:
            yaml.dump(sp, file)

class BizhawkSettings(Screen):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.ids.bizhawk_exe.text = bh['path']
        self.ids.bizhawk_host.text = bh['host']
        self.ids.bizhawk_port.text = bh['port']

    def save_changes(self):
        bh['path'] = self.ids.bizhawk_exe.text
        bh['host'] = self.ids.bizhawk_host.text
        bh['port'] = self.ids.bizhawk_port.text
        
        with open(f"{configsave}bh_config.yml", 'w') as file:
            yaml.dump(bh, file)

class OBSSettings(Screen):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        grid: GridLayout = self.ids["obs_settings"]

        grid.add_widget(Label(text='Passwort\nWebsocket', size_hint=(.2,1)))
        anchorPasswort=AnchorLayout(anchor_x='left')
        grid.add_widget(anchorPasswort)
        passwortInput = TextInput(size_hint=(1,None), size=("20dp","40dp"), password=True, multiline=False, write_tab=False)
        passwortInput.bind(on_text_validate=self.save_changes) #type: ignore
        self.ids["obs_password"] = weakref.proxy(passwortInput)
        anchorPasswort.add_widget(passwortInput)

        grid.add_widget(Label(text='IP-Adresse\nWebsocket', size_hint=(.2,1)))
        anchorHost=AnchorLayout(anchor_x='left')
        grid.add_widget(anchorHost)
        hostInput = TextInput(size_hint=(1,None), size=("20dp","40dp"), multiline=False, write_tab=False)
        hostInput.bind(on_text_validate=self.save_changes) #type: ignore
        self.ids["obs_host"] = weakref.proxy(hostInput)
        anchorHost.add_widget(hostInput)

        grid.add_widget(Label(text='Port des\nWebsocket',size_hint=(.2,1)))
        anchorPort=AnchorLayout(anchor_x='left')
        grid.add_widget(anchorPort)
        portInput = TextInput(size_hint=(1,None), size=("20dp","40dp"), multiline=False, write_tab=False)
        portInput.bind(on_text_validate=self.save_changes) #type: ignore
        self.ids["obs_port"] = weakref.proxy(portInput)
        anchorPort.add_widget(portInput)

        self.ids["obs_password"].text = obs['password']
        self.ids["obs_host"].text = obs['host']
        self.ids["obs_port"].text = obs['port']

    def save_changes(self):
        obs['password'] = self.ids["obs_password"].text
        obs['host'] = self.ids["obs_host"].text
        obs['port'] = self.ids["obs_port"].text
        
        with open(f"{configsave}obs_config.yml", 'w') as file:
            yaml.dump(obs, file)       

class RemoteSettings(Screen):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        grid: GridLayout = self.ids["remote_settings"]
        grid.add_widget(Label(text="Deine öffentliche\nIpv4-Adresse"))
        grid.add_widget(Label(text=externalIPv4))
        grid.add_widget(Label(text="Deine öffentliche\nIpv6-Adresse"))
        grid.add_widget(Label(text=externalIPv6))

    def save_changes(self):
        pass

class PlayerSettings(Screen):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.addCheckBoxes()
        self.pressCheckBoxes()

    def changeScreen(self, player):
        self.removeCheckBoxes()
        pl['player_count'] = player
        server.SPIELERANZAHL = player
        self.addCheckBoxes()
        self.pressCheckBoxes()
        self.save_changes()

    def save_changes(self, *args):
        for i in range(1, pl['player_count'] + 1):
            try:
                pl[f"remote_{i}"] = self.ids[f"remote_player_{i}"].state == "down"
            except KeyError:
                pl[f"remote_{i}"] = False
            try:
                pl[f"obs_{i}"] = self.ids[f"obs_player_{i}"].state == "down"
            except KeyError:
                pl[f"obs_{i}"] = False

            with open(f"{configsave}player.yml", 'w') as file:
                yaml.dump(pl, file)

    def pressCheckBoxes(self):
        self.ids[f"player_count_{pl['player_count']}"].state = "down"
        for i in range(1, pl['player_count'] + 1):
            if pl[f"remote_{i}"]:
                self.ids[f"remote_player_{i}"].state = "down"
            if pl[f"obs_{i}"]:
                self.ids[f"obs_player_{i}"].state = "down"
    
    def removeCheckBoxes(self):
        for i in range(1, pl['player_count'] + 1):
            self.children[0].remove_widget(self.ids[f"label_spieler_{i}"])
            self.children[0].remove_widget(self.ids[f"box_spieler_{i}"])

    def addCheckBoxes(self):
        for i in range(1, pl['player_count'] + 1):
            idLabel = f"label_spieler_{i}"
            label = Label(text=f"Spieler {i}")
            self.children[0].add_widget(label)
            self.ids[idLabel] = weakref.proxy(label)

            idBox = f"box_spieler_{i}"
            box = BoxLayout(orientation="horizontal")
            self.children[0].add_widget(box)
            self.ids[idBox] = weakref.proxy(box)

            idRemote = f"remote_player_{i}"
            idRemoteLabel = f"remote_label_{i}"
            idOBS = f"obs_player_{i}"
            idOBSLabel = f"obs_label_{i}"
            checkRemote = CheckBox(on_press=self.save_changes,active=pl[f"remote_{i}"], pos_hint={"center_y": .5}, size_hint=[None, None], size=["20dp", "20dp"])
            checkRemote.bind(on_press=self.save_changes) # type: ignore
            checkRemoteLabel = Label(text="remote", pos_hint={"center_y": .5}, size_hint=[None, None], size=["60dp", "20dp"])
            checkOBS = CheckBox(on_press=self.save_changes,active=pl[f"obs_{i}"], pos_hint={"center_y": .5}, size_hint=[None, None], size=["20dp", "20dp"])
            checkOBS.bind(on_press=self.save_changes)  # type: ignore
            checkOBSLabel = Label(text="OBS", pos_hint={"center_y": .5}, size_hint=[None, None], size=["40dp", "20dp"])

            self.ids[idBox].add_widget(checkRemote)
            self.ids[idRemote] = weakref.proxy(checkRemote)
            self.ids[idBox].add_widget(checkRemoteLabel)
            self.ids[idRemoteLabel] = weakref.proxy(checkRemoteLabel)
            self.ids[idBox].add_widget(checkOBS)
            self.ids[idOBS] = weakref.proxy(checkOBS)
            self.ids[idBox].add_widget(checkOBSLabel)
            self.ids[idOBSLabel] = weakref.proxy(checkOBSLabel)

class TrackerApp(App):  
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        Window.bind(on_request_close=self.exit_check)
        Config.set('graphics', 'resizable', 1)
        Config.set('graphics', 'width', "600")
        Config.set('graphics', 'height', "400")
        Config.set('input', 'mouse', 'mouse,multitouch_on_demand')

    def on_start(self):
        global externalIPv4
        externalIPv4 = os.popen('curl -s -4 ifconfig.co/').readline().split('\n')[0]
        global externalIPv6
        externalIPv6 = os.popen('curl -s -6 ifconfig.co/').readline().split('\n')[0]
        global connector
        connector = threading.Thread(target=server.main, args=(), daemon=True)
        global configsave
        configsave = 'backend/config/'
        global bh
        if os.path.exists(f"{configsave}bh_config.yml"):
            with open(f"{configsave}bh_config.yml") as file:
                bh = yaml.safe_load(file)
        else:
            bh['host'] = "127.45.45.45" #type: ignore
            bh['path'] = "" #type: ignore
            bh['port'] = "43885" #type: ignore
        global obs
        if os.path.exists(f"{configsave}obs_config.yml"):
            with open(f"{configsave}obs_config.yml") as file:
                obs = yaml.safe_load(file)
        else:
            obs['host'] = "localhost" #type: ignore
            obs['port'] = "4455" #type: ignore
            obs['password'] = "" #type: ignore
        global sp
        if os.path.exists(f"{configsave}sprites.yml"):
            with open(f"{configsave}sprites.yml") as file:
                sp = yaml.safe_load(file)
        else:
            sp['animated'] = False #type: ignore
            sp['black'] = "" #type: ignore
            sp['common_path'] = "" #type: ignore
            sp['crystal'] = "" #type: ignore
            sp['diamond'] = "" #type: ignore
            sp['edition_override'] = "" #type: ignore
            sp['emerald'] = "" #type: ignore
            sp['firered'] = "" #type: ignore
            sp['gold'] = "" #type: ignore
            sp['heartgold'] = "" #type: ignore
            sp['order'] = "lvl" #type: ignore
            sp['platinum'] = "" #type: ignore
            sp['red'] = "" #type: ignore
            sp['ruby'] = "" #type: ignore
            sp['show_nicknames'] = False #type: ignore
            sp['silver'] = "" #type: ignore
            sp['single_path_check'] = True #type: ignore
            sp['yellow'] = "" #type: ignore
        global pl
        if os.path.exists(f"{configsave}player.yml"):
            with open(f"{configsave}player.yml") as file:
                pl = yaml.safe_load(file)
        else:
            pl['player_count'] = 2 #type: ignore
            for i in range(1,5):
                pl[f'obs_{i}'] = False #type: ignore
                pl[f'remote_{i}'] = False #type: ignore
        global cc
        if os.path.exists(f"{configsave}common_config.yml"):
            with open(f"{configsave}common_config.yml") as file:
                cc = yaml.safe_load(file)
        else:
            pass

    def exit_check(self, *args):
        self.save_config(f"{configsave}bh_config.yml", bh)
        self.save_config(f"{configsave}obs_config.yml", obs)
        self.save_config(f"{configsave}sprites.yml", sp)
        self.save_config(f"{configsave}player.yml", pl)
        self.save_config(f"{configsave}common_config.yml", cc)

    def save_config(self, path, setting):
        with open(path, 'w') as file:
            yaml.dump(setting, file)