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
from kivy.config import Config
from kivy.uix.label import Label
from kivy.uix.togglebutton import ToggleButton
from kivy.uix.textinput import TextInput
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
        self.add_widget(MainMenu())
        self.add_widget(SettingsMenu())
        self.current = "MainMenu"

class MainMenu(Screen):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.name = "MainMenu"
        frame = BoxLayout(orientation='vertical')
        control_frame = BoxLayout(orientation='horizontal')

        logo_settings = BoxLayout(orientation='vertical', size_hint=(.3,1))
        logo = Label(text='Logo')
        logo_settings.add_widget(logo)

        settings = Button(text='Einstellungen', on_press=self.switch_to_settings)
        logo_settings.add_widget(settings)
        control_frame.add_widget(logo_settings)

        connections = BoxLayout(orientation='vertical', size_hint=(.7,1))
        server_client = Button(text='Server/Client')
        connections.add_widget(server_client)
        obs_connect = Button(text='OBS verbinden etc.')
        connections.add_widget(obs_connect)
        emulator = Button(text='Emulatorstarten etc.')
        connections.add_widget(emulator)

        control_frame.add_widget(connections)

        showing_frame = BoxLayout(orientation='horizontal')
        sort_layout = BoxLayout(orientation='vertical', spacing="20dp", padding=("5dp",0))

        ueberschrift_sortierung = Label(text="Sortierung",halign='left', size_hint_x=None, width=sort_layout.width)
        sort_layout.add_widget(ueberschrift_sortierung)

        sorts = (("DexNr.","dexnr"), ("Team", "team"), ("Level","lvl"), ("Route","route"))
        for text, id in sorts:
            toggler = ToggleButton(text=text, group="sort", allow_no_selection=False, on_press=self.save_changes)
            self.ids[id] = weakref.proxy(toggler)
            sort_layout.add_widget(toggler)
        
        showing_frame.add_widget(sort_layout)

        checkmarks = (("Orden anzeigen", "badges_check"),("Namen anzeigen", "names_check"),("Items anziegen", "items_check"),("animierte Sprites", "animated_check"))
        show_layout = GridLayout(cols=2)
        for text, id in checkmarks:
            anchor = AnchorLayout(anchor_x='right', size_hint_x=0.5)
            checkbox = CheckBox(size_hint=(None, None), size=("20dp","20dp"), on_press=self.save_changes)
            self.ids[id] = weakref.proxy(checkbox)

            anchor.add_widget(checkbox)
            show_layout.add_widget(anchor)

            label = Label(text=text)
            show_layout.add_widget(label)

        showing_frame.add_widget(show_layout)
        frame.add_widget(control_frame)
        frame.add_widget(showing_frame)
        self.add_widget(frame)
        
        global sp
        self.init_config(sp)

    def init_config(self, sp):
        self.ids.animated_check.state = 'down' if sp['animated'] else 'normal'
        self.ids.names_check.state = 'down' if sp['show_nicknames'] else 'normal'
        self.ids.items_check.state = 'down' if sp['show_items'] else 'normal'
        self.ids.badges_check.state = 'down' if sp['show_badges'] else 'normal'
        self.ids[sp['order']].state = 'down'

    def save_changes(self, instance):
        sorts = {"DexNr.":"dexnr", "Team": "team", "Level":"lvl", "Route":"route"}
        for button in ToggleButton.get_widgets("sort"):
            if button.state == 'down':
                sp['order'] = sorts[button.text]

        sp['animated'] = self.ids.animated_check.state == 'down'
        sp['show_nicknames'] = self.ids.names_check.state == 'down'
        sp['show_items'] = self.ids.items_check.state == 'down'
        sp['show_badges'] = self.ids.badges_check.state == 'down'
        client.conf = sp
        client.change_order()
        asyncio.create_task(client.redraw_obs())
        with open(f"{configsave}sprites.yml", 'w') as file:
            yaml.dump(sp, file)

    def switch_to_settings(self, instance):
        self.manager.current = "SettingsMenu"

class SettingsMenu(Screen):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.name = "SettingsMenu"

        layout = GridLayout(cols=2)
        box = BoxLayout(orientation="vertical", size_hint=(0.15, 1), pos_hint={"top": 0})
        
        logo = Label(text='Logo')
        box.add_widget(logo)
        
        main_menu_button = Button(text="Hauptmenü", on_press=lambda instance: setattr(self.manager, 'current', "MainMenu"))
        box.add_widget(main_menu_button)

        settings_buttons = [
            ("Sprite\nPfade", 'sprite'),
            ("Bizhawk", 'bizhawk'),
            ("OBS", 'obs'),
            ("Remote", 'remote'),
            ("Spieler", 'player')
        ]

        for text, screen_name in settings_buttons:
            button = ToggleButton(group="settings", text=text, allow_no_selection=False, on_press=self.callback_screen_change(screen_name))
            print(screen_name)
            box.add_widget(button)

        layout.add_widget(box)
        layout.add_widget(Screen())

        self.add_widget(layout)

    def callback_screen_change(self, screen_name):
        def callback(instance):
            self.changesettingscreen(screen_name)
        return callback
    
    def changesettingscreen(self, setting):
        if len(self.children[0].children) > 1:
            curScreen = self.children[0].children[0]
            if type(curScreen) is not Screen:
                type(curScreen).save_changes(curScreen)
            self.children[0].remove_widget(self.children[0].children[0])

        widget = self.checkSettingScreen(setting)

        self.children[0].add_widget(widget)

    def checkSettingScreen(self, setting):
        if setting == 'sprite':
            widget = SpriteSettings()
        elif setting == 'games':
            widget = SpritesGames()
        elif setting == 'bizhawk':
            widget = BizhawkSettings()
        elif setting == 'obs':
            widget = OBSSettings()
        elif setting == 'remote':
            widget = RemoteSettings()
        elif setting == 'player':
            widget = PlayerSettings()
        return widget # type: ignore

class SpriteSettings(Screen):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        paths = [('Dateipfad Pokemon','common_path'),('Dateipfad Items', 'items_path'),('Dateipfad Orden', 'badges_path')]
        outerlayout = BoxLayout(orientation='vertical')
        for text, path in paths:
            grid1 = GridLayout(cols=2)
            grid1.add_widget(Label(text=text, size_hint=(.3, 1)))
            box1 = BoxLayout(orientation='horizontal')
            anchor1 = AnchorLayout(anchor_x='left', size_hint=(.7, 1))
            text_input = TextInput(size_hint=(1, None), size=("20dp", "30dp"), multiline=False, write_tab=False, on_text_validate=lambda instance: self.save_changes())
            anchor1.add_widget(text_input)
            box1.add_widget(anchor1)
            button1 = Button(text='Durchsuchen', size_hint=(.2, None), size=("20dp", "30dp"), pos_hint={"center_x":.5, "center_y": .5}, on_press=lambda instance: App.get_running_app().browse(self.ids['common_path'], self, 'directory'))
            box1.add_widget(button1)
            grid1.add_widget(box1)
            outerlayout.add_widget(grid1)

        # Add to ids
            self.ids[path] = weakref.proxy(text_input)

        # Second GridLayout
        grid2 = GridLayout(cols=2)
        box2 = BoxLayout(orientation='vertical')
        grid3 = GridLayout(cols=2)
        anchor2 = AnchorLayout(anchor_x='left', size_hint=(.1, .5))
        checkbox = CheckBox(size_hint=(None, None), size=("20dp", "20dp"), on_press=lambda instance: self.save_changes())
        anchor2.add_widget(checkbox)
        grid3.add_widget(anchor2)
        anchor3 = AnchorLayout(anchor_x='left', size_hint=(1, 1))
        anchor3.add_widget(Label(size_hint=(1, .5), text='Sprites jedes Spiels einzeln festlegen'))
        grid3.add_widget(anchor3)
        box2.add_widget(grid3)
        anchor4 = AnchorLayout(anchor_x='left', size_hint=(1, 1))
        button2 = Button(text='Spritepfade der einzelnen Games', size_hint=(1, 1), on_press=lambda instance: self.parent.parent.changesettingscreen('games'))
        anchor4.add_widget(button2)
        box2.add_widget(anchor4)
        grid2.add_widget(box2)
        outerlayout.add_widget(grid2)

        # Add to ids
        self.ids['game_sprites_check'] = weakref.proxy(checkbox)
        self.ids['game_sprites'] = weakref.proxy(button2)
        
        self.add_widget(outerlayout)

        self.ids.common_path.text = sp['common_path']
        self.ids.items_path.text = sp['items_path']
        self.ids.badges_path.text = sp['badges_path']
        self.ids.game_sprites_check.state = 'down' if not sp['single_path_check'] else 'normal'

    def save_changes(self):
        sp['common_path'] = self.ids.common_path.text
        sp['single_path_check'] = not self.ids.game_sprites_check.state == 'down'
        sp['items_path'] = self.ids.items_path.text
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

    def build(self):
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

        return Screens()

    def exit_check(self, *args, **kwargs):
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