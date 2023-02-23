import os
import sys
import subprocess
import weakref
import yaml
import asyncio
from kivy.app import App
from kivy.core.clipboard import Clipboard
from kivy.uix.anchorlayout import AnchorLayout
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.checkbox import CheckBox
from kivy.uix.gridlayout import GridLayout
from kivy.uix.textinput import TextInput
from kivy.config import Config
from kivy.uix.label import Label
from kivy.uix.screenmanager import Screen
from kivy.uix.screenmanager import ScreenManager
from kivy.uix.screenmanager import FadeTransition
from kivy.core.window import Window
import backend.server as server
import backend.munchlax as client
import tkinter.filedialog as fd
import logging

logger = logging.getLogger(__name__)
logger.addHandler(logging.StreamHandler(sys.stdout))
Config.read('gui.ini')
connector = None
OBSconnector = None
clientConnector = None

class Screens(ScreenManager):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.transition = FadeTransition()

class MainMenu(Screen):
    pass

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
        self.ids.items_path.text = sp['items_path']
        self.ids.badges_path.text = sp['badges_path']
        self.ids.animated_check.state = 'down' if sp['animated'] else 'normal'
        self.ids.game_sprites_check.state = 'down' if not sp['single_path_check'] else 'normal'
        self.ids.names_check.state = 'down' if sp['show_nicknames'] else 'normal'
        self.ids.items_check.state = 'down' if sp['show_items'] else 'normal'
        self.ids.badges_check.state = 'down' if sp['show_badges'] else 'normal'
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
        
        if self.ids["items_check"].state == 'normal':
            self.ids["items_path"].opacity = 0
            self.ids["items_path"].disabled = True
            self.ids["items_browse"].opacity = 0
            self.ids["items_browse"].disabled = True
        else:
            self.ids["items_path"].opacity = 1
            self.ids["items_path"].disabled = False
            self.ids["items_browse"].opacity = 1
            self.ids["items_browse"].disabled = False

        if self.ids["badges_check"].state == 'normal':
            self.ids["badges_path"].opacity = 0
            self.ids["badges_path"].disabled = True
            self.ids["badges_browse"].opacity = 0
            self.ids["badges_browse"].disabled = True
        else:
            self.ids["badges_path"].opacity = 1
            self.ids["badges_path"].disabled = False
            self.ids["badges_browse"].opacity = 1
            self.ids["badges_browse"].disabled = False
        
        if not initializing:
            self.save_changes()

    def save_changes(self):
        sp['common_path'] = self.ids.common_path.text
        sp['single_path_check'] = not self.ids.game_sprites_check.state == 'down'
        sp['animated'] = self.ids.animated_check.state == 'down'
        sp['show_nicknames'] = self.ids.names_check.state == 'down'
        for i in range(4):
            if self.ids["sortierung"].children[i].state == "down":
                sp['order'] = ['route', 'lvl', 'team', 'dexnr'][i]
        sp['show_items'] = self.ids.items_check.state == 'down'
        sp['items_path'] = self.ids.items_path.text
        sp['show_badges'] = self.ids.badges_check.state == 'down'
        sp['badges_path'] = self.ids.badges_path.text
        client.conf = sp
        client.change_order()
        asyncio.ensure_future(client.redraw_obs())
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
    #    self.ids.bizhawk_host.text = bh['host']
        self.ids.bizhawk_port.text = bh['port']

    def launchbh(self):
        if rem['start_server']:
            RemoteSettings.connect_BClient()
        asyncio.ensure_future(client.pass_bh_to_server((rem['server_ip_adresse'], rem['client_port']), bh['port']))
        for i in range(pl['player_count']):
            if not pl[f'remote_{i+1}']:
                subprocess.Popen([bh['path'], f'--lua={os.path.abspath(f"./backend/Player{i+1}.lua")}', f'--socket_ip={bh["host"]}', f'--socket_port={bh["port"]}'])

    def save_changes(self):
        bh['path'] = self.ids.bizhawk_exe.text
    #    bh['host'] = self.ids.bizhawk_host.text
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

    def save_changes(self, *args):
        obs['password'] = self.ids["obs_password"].text
        obs['host'] = self.ids["obs_host"].text
        obs['port'] = self.ids["obs_port"].text
        
        with open(f"{configsave}obs_config.yml", 'w') as file:
            yaml.dump(obs, file)

    def connectOBS(self,*args):
        global OBSconnector
        if not OBSconnector:
            OBSconnector = asyncio.ensure_future(client.load_obsws(obs['host'], obs['port'], obs['password']))
        if clientConnector:
            asyncio.gather(clientConnector, OBSconnector)

    def disconnectOBS(self, *args):
        global OBSconnector
        if OBSconnector:
            asyncio.ensure_future(client.ws.disconnect())
            OBSconnector = None

class RemoteSettings(Screen):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        grid: GridLayout = self.ids["remote_settings"]
        grid.add_widget(Label(text="Deine öffentliche\nIpv4-Adresse", size_hint=(1,.5)))
        grid.add_widget(Label(on_ref_press=self.clipboard,text=f"[ref=ip]{externalIPv4}[/ref]", size_hint=(1,.5), markup=True))
        grid.add_widget(Label(text="Deine öffentliche\nIpv6-Adresse", size_hint=(1,.5)))
        grid.add_widget(Label(on_ref_press=self.clipboard,text=f"[ref=ipv6]{externalIPv6}[/ref]", size_hint=(1,.5),markup=True))
        
        grid.add_widget(Label(text="Hauptspieler?", size_hint=(1,.5)))
        mainPlayerBox = BoxLayout(orientation='horizontal', pos_hint={"center_x": .5,"center_y": .5})
        yesCheck = CheckBox(pos_hint={"center_x": .5,"center_y": .5}, size_hint=(None, None), size=("20dp","20dp"),on_press=self.change_Screen)
        self.ids['main_Yes'] = weakref.proxy(yesCheck)
        mainPlayerBox.add_widget(yesCheck)
        grid.add_widget(mainPlayerBox)

        if not rem['start_server']:
            yesCheck.state = "normal"
            self.set_server_info_widgets()
        else:
            yesCheck.state = "down"
            self.set_main_server_info()

    def set_server_info_widgets(self):
        box = self.ids["remote_settings_box"]
        grid = self.ids["remote_settings"]
        connectionLabel = Label(text = f'Server Verbindung', size_hint=(1,.5))
        self.ids["server_label"] = weakref.proxy(connectionLabel)
        grid.add_widget(connectionLabel)
        serverGrid = GridLayout(cols=2)
        self.ids["server_grid"] = weakref.proxy(serverGrid)
        serverGrid.add_widget(Label(text='IP-Adresse:', size_hint=(.3,1)))
        anchorIPText = AnchorLayout(anchor_x='left')
        IPText = TextInput(text=rem[f'server_ip_adresse'],on_text_validate=self.save_changes,size_hint=(1,None), size=("20dp","40dp"), multiline=False, write_tab=False)
        self.ids['ip_server'] = weakref.proxy(IPText)
        anchorIPText.add_widget(IPText)
        serverGrid.add_widget(anchorIPText)
        serverGrid.add_widget(Label(text='Port:', size_hint=(.3,1)))
        anchorPortText = AnchorLayout(anchor_x='left')
        PortText = TextInput(text=rem[f'client_port'],on_text_validate=self.save_changes,size_hint=(1,None), size=("20dp","40dp"), multiline=False, write_tab=False)
        self.ids['port_client'] = weakref.proxy(PortText)
        anchorPortText.add_widget(PortText)
        serverGrid.add_widget(anchorPortText)
        grid.add_widget(serverGrid)

        connectClient = Button(text="Verbinde Client", size_hint=(.5,.2), pos_hint={"center_x": .5}, on_press=self.connect_client)
        self.ids["connect_Client"] = weakref.proxy(connectClient)
        box.add_widget(connectClient)

    def remove_server_info_widgets(self):
        grid = self.ids["remote_settings"]
        grid.remove_widget(self.ids["server_label"])
        grid.remove_widget(self.ids["server_grid"])
        self.children[0].remove_widget(self.ids["connect_Client"])

    def set_main_server_info(self):
        grid = self.ids["remote_settings"]
        serverGrid = GridLayout(cols=2)
        self.ids["client_grid"] = weakref.proxy(serverGrid)
        serverGrid.add_widget(Label(text='Port:', size_hint=(.3,1)))
        anchorPortText = AnchorLayout(anchor_x='left')
        PortText = TextInput(text=rem[f'server_port'],on_text_validate=self.save_changes,size_hint=(1,None), size=("20dp","40dp"), multiline=False, write_tab=False)
        self.ids['port_server'] = weakref.proxy(PortText)
        anchorPortText.add_widget(PortText)
        serverGrid.add_widget(anchorPortText)
        grid.add_widget(serverGrid)

        startServer = Button(text="Start Server", on_press=self.launchserver)
        self.ids["start_Server"] = weakref.proxy(startServer)
        grid.add_widget(startServer)

    def remove_main_server_info(self):
        grid = self.ids["remote_settings"]
        grid.remove_widget(self.ids["start_Server"])
        grid.remove_widget(self.ids["client_grid"])
    
    def change_Screen(self, *args):
        if self.ids["main_Yes"].state == "down":
            self.remove_server_info_widgets()
            self.set_main_server_info()
        else:
            self.remove_main_server_info()
            self.set_server_info_widgets()
        self.save_changes()
    
    def clipboard(self,*args):
        result = (args[0].text).split(']')[1].split('[')[0]
        Clipboard.copy(result)

    def save_changes(self, *args):
        try:
            rem['server_ip_adresse'] = self.ids['ip_server'].text
            rem['server_port'] = self.ids['port_server'].text
        except KeyError:
            pass
        try:
            rem['client_port'] = self.ids['port_client'].text
        except KeyError:
            pass
        rem['start_server'] = self.ids['main_Yes'].state == "down"

        with open(f"{configsave}remote.yml", 'w') as file:
            yaml.dump(rem, file)

    def launchserver(self,*args):
        global connector
        if not connector:
            connector = asyncio.ensure_future(server.main(port=rem['server_port']))

    @classmethod
    def connect_BClient(cls, *args):
        global clientConnector
        if not clientConnector:
            clientConnector = asyncio.ensure_future(client.connect_client(rem["server_ip_adresse"], rem["client_port"]))
        if OBSconnector:
            asyncio.gather(clientConnector, OBSconnector)

    def connect_client(self, *args):
        global clientConnector
        if not clientConnector:
            clientConnector = asyncio.ensure_future(client.connect_client(rem["server_ip_adresse"], rem["client_port"]))
        if OBSconnector:
            asyncio.gather(clientConnector, OBSconnector)

class PlayerSettings(Screen):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.addCheckBoxes()
        self.pressCheckBoxes()

    def changeScreen(self, player):
        self.removeCheckBoxes()
        pl['player_count'] = player
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
            checkRemote = CheckBox(on_press=self.toggle_obs,active=pl[f"remote_{i}"], pos_hint={"center_y": .5}, size_hint=[None, None], size=["20dp", "20dp"])
            checkRemoteLabel = Label(text="remote", pos_hint={"center_y": .5}, size_hint=[None, None], size=["60dp", "20dp"])
            checkOBS = CheckBox(on_press=self.save_changes,active=pl[f"obs_{i}"], disabled=not pl[f"remote_{i}"], pos_hint={"center_y": .5}, size_hint=[None, None], size=["20dp", "20dp"])
            checkOBSLabel = Label(text="OBS", pos_hint={"center_y": .5}, size_hint=[None, None], size=["40dp", "20dp"])

            self.ids[idBox].add_widget(checkRemote)
            self.ids[idRemote] = weakref.proxy(checkRemote)
            self.ids[idBox].add_widget(checkRemoteLabel)
            self.ids[idRemoteLabel] = weakref.proxy(checkRemoteLabel)
            self.ids[idBox].add_widget(checkOBS)
            self.ids[idOBS] = weakref.proxy(checkOBS)
            checkRemote.ids[idOBS] = weakref.proxy(checkOBS)
            self.ids[idBox].add_widget(checkOBSLabel)
            self.ids[idOBSLabel] = weakref.proxy(checkOBSLabel)
            checkRemote.ids[idOBSLabel] = weakref.proxy(checkOBSLabel)

    def toggle_obs(self, widgets):
        for obs in widgets.ids:
            ObsCheckBox = widgets.ids[obs]
            ObsCheckBox.disabled = widgets.state != 'down'
            if 'state' in dir(ObsCheckBox):
                ObsCheckBox.state = 'normal'
        self.save_changes()
        self.pressCheckBoxes()

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
        externalIPv4 = os.popen('curl -s -4 -m 1 ifconfig.co/').readline().split('\n')[0]
        global externalIPv6
        externalIPv6 = os.popen('curl -s -6 -m 1 ifconfig.co/').readline().split('\n')[0]
        global configsave
        configsave = 'backend/config/'
        global bh
        bh = {}
        if os.path.exists(f"{configsave}bh_config.yml"):
            with open(f"{configsave}bh_config.yml") as file:
                bh = yaml.safe_load(file)
        else:
            bh['host'] = "127.0.0.1" #type: ignore
            bh['path'] = "" #type: ignore
            bh['port'] = "43885" #type: ignore
        global obs
        obs = {}
        if os.path.exists(f"{configsave}obs_config.yml"):
            with open(f"{configsave}obs_config.yml") as file:
                obs = yaml.safe_load(file)
        else:
            obs['host'] = "localhost" #type: ignore
            obs['port'] = "4455" #type: ignore
            obs['password'] = "" #type: ignore
        global sp
        sp = {}
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
            sp['show_badges'] = False #type: ignore
            sp['show_items'] = False #type: ignore
            sp['show_nicknames'] = False #type: ignore
            sp['silver'] = "" #type: ignore
            sp['single_path_check'] = True #type: ignore
            sp['yellow'] = "" #type: ignore
        client.conf = sp
        global pl
        pl = {}
        if os.path.exists(f"{configsave}player.yml"):
            with open(f"{configsave}player.yml") as file:
                pl = yaml.safe_load(file)
        else:
            pl['player_count'] = 2 #type: ignore
            for i in range(1,5):
                pl[f'obs_{i}'] = False #type: ignore
                pl[f'remote_{i}'] = False #type: ignore
        global rem
        rem = {}
        if os.path.exists(f"{configsave}remote.yml"):
            with(open(f"{configsave}remote.yml")) as file:
                rem = yaml.safe_load(file)
        else:
            rem["client_port"] = '43886'
            rem["server_ip_adresse"] = '0.0.0.0'
            rem["server_port"] = '43885'
            rem["start_server"] = False

    def exit_check(self, *args):
        self.save_config(f"{configsave}bh_config.yml", bh)
        self.save_config(f"{configsave}obs_config.yml", obs)
        self.save_config(f"{configsave}sprites.yml", sp)
        self.save_config(f"{configsave}player.yml", pl)
        self.save_config(f"{configsave}remote.yml", rem)

    def browse(self, widget, instance, *args):
        if args[0] == 'file':
            path = fd.askopenfilename()
        else:
            path = fd.askdirectory()
        if path:
            widget.text = path
            instance.save_changes()

    def save_config(self, path, setting):
        with open(path, 'w') as file:
            yaml.dump(setting, file)