import os
import sys
import subprocess
import weakref
import yaml
import asyncio
from kivy.app import App
from kivy.core.clipboard import Clipboard
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.checkbox import CheckBox
from kivy.uix.gridlayout import GridLayout
from kivy.config import Config
from kivy.uix.label import Label
from kivy.uix.screenmanager import Screen
from kivy.uix.screenmanager import ScreenManager
from kivy.uix.screenmanager import FadeTransition
from kivy.core.window import Window
import frontend.UIFactory as UI
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
bizConnector = None
connectors = []

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
            self.hide_widget("items_path")
            self.hide_widget("items_browse")
        else:
            self.show_widget("items_path")
            self.show_widget("items_browse")

        if self.ids["badges_check"].state == 'normal':
            self.hide_widget("badges_path")
            self.hide_widget("badges_browse")
        else:
            self.show_widget("badges_path")
            self.show_widget("badges_browse")
        
        if not initializing:
            self.save_changes()

    def show_widget(self, widgetname):
        self.ids[widgetname].opacity = 1
        self.ids[widgetname].disabled = False

    def hide_widget(self, widgetname):
        self.ids[widgetname].opacity = 0
        self.ids[widgetname].disabled = True

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
        asyncio.create_task(client.redraw_obs())
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
        global bizConnector
        if rem['start_server']:
            RemoteSettings.connect_BClient()
            bizConnector = asyncio.create_task(client.pass_bh_to_server(("127.0.0.1", rem['server_port']), bh['port']))
        else:
            bizConnector = asyncio.create_task(client.pass_bh_to_server((rem['server_ip_adresse'], rem['client_port']), bh['port']))
        global connectors
        asyncio.gather(*connectors)
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

        UI.create_label_and_Textbox(grid, self.ids, "obs_password", label_text = 'Passwort\nWebsocket', text_validate_function=self.save_changes, password=True)
        UI.create_label_and_Textbox(grid, self.ids, "obs_host", label_text = 'IP-Adresse\nWebsocket', text_validate_function=self.save_changes)
        UI.create_label_and_Textbox(grid, self.ids, "obs_port", label_text = 'Port des\nWebsocket', text_validate_function=self.save_changes)

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
        global connectors
        if not OBSconnector:
            OBSconnector = asyncio.create_task(client.load_obsws(obs['host'], obs['port'], obs['password']))
            connectors.append(OBSconnector)
        asyncio.gather(*connectors)

    def disconnectOBS(self, *args):
        global OBSconnector
        if OBSconnector:
            asyncio.create_task(client.ws.disconnect())
            OBSconnector = None
            logger.info("OBS disconnected.")

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

        UI.create_label_and_Textbox(serverGrid, self.ids, "ip_server", label_text='IP-Adresse:', label_size_hint=(.3,1), text_validate_function=self.save_changes)
        self.ids["ip_server"].text = rem[f'server_ip_adresse']

        UI.create_label_and_Textbox(serverGrid, self.ids, "port_client", label_text='Port:', label_size_hint=(.3,1), text_validate_function=self.save_changes)
        self.ids["port_client"].text = rem[f'client_port']

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

        UI.create_label_and_Textbox(serverGrid, self.ids, "port_server", label_size_hint=(.3,1), label_text='Port:', text_validate_function=self.save_changes)
        self.ids['port_server'].text = rem[f'server_port']

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
        rem['start_server'] = self.ids['main_Yes'].state == "down"
        try:
            if rem['start_server']:
                rem['client_port'] = self.ids['port_client'].text
            else:
                rem['server_ip_adresse'] = self.ids['ip_server'].text
                rem['server_port'] = self.ids['port_server'].text
        except KeyError as err:
            pass

        with open(f"{configsave}remote.yml", 'w') as file:
            yaml.dump(rem, file)

    def launchserver(self,*args):
        global connector
        global connectors
        if not connector:
            connector = asyncio.create_task(server.main(port=rem['server_port']))
        try:
            asyncio.gather(*connectors)
        except asyncio.CancelledError as async_err:
            logger.error(f"async_Fehler: {async_err}")

    @classmethod
    def connect_BClient(cls, *args):
        global clientConnector
        global connectors
        if not clientConnector:
            clientConnector = asyncio.create_task(client.connect_client("127.0.0.1", rem["server_port"]))
            connectors.append(clientConnector)
        try:
            asyncio.gather(*connectors)
        except asyncio.CancelledError as async_err:
            logger.error(f"async_Fehler: {async_err}")

    def connect_client(self, *args):
        global clientConnector
        global connector
        global connectors
        if client.bizServer:
            logger.info(f"lokale bizhawk-Verbindung auf Port {bh['port']} aufghoben")
            client.bizServer.close()
            client.bizServer = None
            asyncio.create_task(client.disconnect_all_local_connections())
        if connector and server.server:
            logger.info(f"laufender server beendet auf Port {rem['server_port']}")
            server.server.close()
            server.server = None
            connector = None
        if not clientConnector:
            clientConnector = asyncio.create_task(client.connect_client(rem["server_ip_adresse"], rem["client_port"]))
            connectors.append(clientConnector)
        try:
            asyncio.gather(*connectors)
        except asyncio.CancelledError as async_err:
            logger.error(f"async_Fehler: {async_err}")

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

            UI.create_label_and_checkboxes(box, self.ids, 
                                            checkbox_id_name=idRemote,checkbox_on_press=self.toggle_obs, checkbox_active=pl[f"remote_{i}"],
                                            label_id_name=idRemoteLabel, label_text="remote")

            UI.create_label_and_checkboxes(box, self.ids, 
                                            checkbox_id_name=idOBS,checkbox_on_press=self.toggle_obs, checkbox_active=pl[f"obs_{i}"], checkbox_disabled=not pl[f"remote_{i}"],
                                            label_id_name=idOBSLabel, label_text="OBS", label_size=["40dp", "20dp"])
            self.ids[idRemote].ids[idOBS] = self.ids[idOBS]
            self.ids[idRemoteLabel].ids[idOBSLabel] = self.ids[idOBSLabel]

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