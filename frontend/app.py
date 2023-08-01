import os
import sys
import subprocess
import weakref
import yaml
import asyncio
import requests
from kivy.app import App
from kivy.core.clipboard import Clipboard
from kivy.core.window import Window
from kivy.config import Config
from kivy.uix.anchorlayout import AnchorLayout
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.checkbox import CheckBox
from kivy.uix.screenmanager import FadeTransition
from kivy.uix.gridlayout import GridLayout
from kivy.uix.label import Label
from kivy.uix.screenmanager import Screen
from kivy.uix.screenmanager import ScreenManager
from kivy.uix.scrollview import ScrollView
from kivy.uix.togglebutton import ToggleButton
from kivy.uix.textinput import TextInput
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

    def launchbh(self):
        global bizConnector
        if rem['start_server']:
            self.connect_client()
            bizConnector = asyncio.create_task(client.pass_bh_to_server(("127.0.0.1", rem['server_port']), bh['port']))
        else:
            bizConnector = asyncio.create_task(client.pass_bh_to_server((rem['server_ip_adresse'], rem['client_port']), bh['port']))
        global connectors
        asyncio.gather(*connectors)
        for i in range(pl['player_count']):
            if not pl[f'remote_{i+1}']:
                subprocess.Popen([bh['path'], f'--lua={os.path.abspath(f"./backend/Player{i+1}.lua")}', f'--socket_ip={bh["host"]}', f'--socket_port={bh["port"]}'])

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

    def launchserver(self,*args):
        global connector
        global connectors
        if not connector:
            connector = asyncio.create_task(server.main(port=rem['server_port']))
        try:
            asyncio.gather(*connectors)
        except asyncio.CancelledError as async_err:
            logger.error(f"async_Fehler: {async_err}")

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

        box = BoxLayout(orientation="vertical")
        header_box = BoxLayout(orientation='horizontal', size_hint_y=0.15, padding=(0,"10dp"))
        
        logo = Label(text='Logo', size_hint=(.15,1))
        header_box.add_widget(logo)

        header_box.add_widget(Label(size_hint_x=.7))
        
        main_menu_button = Button(text="Hauptmenü",size_hint_x=.15, on_press=lambda instance: setattr(self.manager, 'current', "MainMenu"))
        header_box.add_widget(main_menu_button)

        box.add_widget(header_box)

        layout = GridLayout(cols=2, size_hint_y=.85)

        button_box = BoxLayout(orientation="vertical", size_hint=(0.15, 1), pos_hint={"top": 0})
        scrollview = ScrollSettings()

        settings_buttons = [
            ("Sprite\nPfade", 'sprite'),
            ("Bizhawk", 'bizhawk'),
            ("OBS", 'obs'),
            ("Remote", 'remote'),
            ("Spieler", 'player')
        ]

        for text, screen_name in settings_buttons:
            button = Button(text=text)
            button_box.add_widget(button)

        layout.add_widget(button_box)
        layout.add_widget(scrollview)

        box.add_widget(layout)
        self.add_widget(box)

class ScrollSettings(ScrollView):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.games={
            'Rot und Blau':'gen1_red','Gelb':'gen1_yellow',
            'Silber':'gen2_silver','Gold':'gen2_gold','Kristall':'gen2_crystal',
            'Rubin und Saphir':'gen3_ruby','Smaragd':'gen3_emerald', 'Feuerrot und\nBlattgrün':'gen3_firered',
            'Diamant und Perl':'gen4_diamond','Platin':'gen4_platinum','Herzgold und\nSeelensilber':'gen4_heartgold',
            'Schwarz und Weiß (2)':'gen5_black',
            'X und Y':'gen6_x','Alpha Saphir und\nOmega Rubin':'gen6_alphasapphire',
            'Sonne und Mond':'gen7_sun','Ultra Sonne und\nUltra Mond':'gen7_usun'
        }
        
        box = BoxLayout(orientation='vertical',size_hint_y=None, spacing="20dp")
        box.bind(minimum_height=box.setter('height')) # type: ignore


        sprite_box = BoxLayout(orientation='vertical',size_hint_y=None, spacing="20dp")
        sprite_box.bind(minimum_height=sprite_box.setter('height')) # type: ignore
        self.ids["sprite"] = weakref.proxy(sprite_box)

        ueberschrift_sprites = Label(text="Sprites", size_hint=(1, None), size=(0,"20dp"), font_size="20sp")
        sprite_box.add_widget(ueberschrift_sprites)

        UI.create_text_and_browse_button(sprite_box,self.ids,
                                box_id_name='common_path_box',
                                label_text='Dateipfad Sprites',
                                text_id_name="common_path", text_validate_function=None,
                                browse_function=self.browse)

        float_box = BoxLayout(orientation='vertical', size_hint_y=None, height=0)
        float_box.bind(minimum_height=float_box.setter('height')) # type: ignore

        game_sprites_bool_box = BoxLayout(orientation='horizontal', size_hint_y=None, size=(0,"30dp"))

        game_sprites_label_einzeln = Label(text="Sprites jedes Spiels einzeln festlegen:", size_hint_x=.7)
        game_sprites_bool_box.add_widget(game_sprites_label_einzeln)
        
        game_sprites_checkbox = CheckBox(size_hint_x=.2, on_press=lambda instance: self.ausklapp_button_zeigen_oder_verstecken(instance))
        self.ids["game_sprites_check"] = weakref.proxy(game_sprites_checkbox)
        game_sprites_bool_box.add_widget(game_sprites_checkbox)

        game_sprites_ausklappen = ToggleButton(text=">",size_hint_x=.1, on_press=lambda instance: self.game_sprites_ausklappen(instance, float_box))
        self.ids["games_ausklappen"] = weakref.proxy(game_sprites_ausklappen)
        game_sprites_bool_box.add_widget(game_sprites_ausklappen)

        sprite_box.add_widget(game_sprites_bool_box)

        sprite_box.add_widget(float_box)

        UI.create_text_and_browse_button(sprite_box,self.ids,
                                box_id_name='items_path_box',
                                label_text='Dateipfad Items',
                                text_id_name="items_path", text_validate_function=None,
                                browse_function=self.browse)
        
        UI.create_text_and_browse_button(sprite_box,self.ids,
                                box_id_name='badges_path_box',
                                label_text='Dateipfad Orden',
                                text_id_name="badges_path", text_validate_function=None,
                                browse_function=self.browse)

        box.add_widget(sprite_box)

        bizhawk_box = BoxLayout(orientation='vertical',size_hint_y=None, spacing="20dp")
        bizhawk_box.bind(minimum_height=bizhawk_box.setter('height')) # type: ignore

        ueberschrift_bizhawk = Label(text="Bizhawk", size_hint=(1, None), size=(0,"20dp"), font_size="20sp")
        bizhawk_box.add_widget(ueberschrift_bizhawk)

        UI.create_text_and_browse_button(bizhawk_box, self.ids,
                                box_id_name='bizhawk_path_box',
                                label_text='Pfad der\nEmuHawk.exe',
                                text_id_name="bizhawk_exe", text_validate_function=None,
                                browse_function=self.browse, browse_modus='file')
        
        UI.create_label_and_Textbox(bizhawk_box, self.ids, 
                            label_text='Port',text_size_hint=(.1,1), is_port=True,
                            text_box_id='bizhawk_port',text_validate_function=self.save_changes)

        box.add_widget(bizhawk_box)

        obs_box = BoxLayout(orientation='vertical',size_hint_y=None, spacing="20dp")
        obs_box.bind(minimum_height=obs_box.setter('height')) # type: ignore
        self.ids["obs"] = weakref.proxy(obs_box)

        ueberschrift_obs = Label(text="OBS Websocket", size_hint=(1, None), size=(0,"20dp"), font_size="20sp")
        obs_box.add_widget(ueberschrift_obs)

        UI.create_label_and_Textbox(obs_box, self.ids, 
                            label_text='IP-Adresse', 
                            text_box_id='obs_host',text_validate_function=self.save_changes)
        
        UI.create_label_and_Textbox(obs_box, self.ids, 
                            label_text='Port', text_size_hint=(.1,1), is_port=True,
                            text_box_id='obs_port',text_validate_function=self.save_changes)
        
        UI.create_label_and_Textbox(obs_box, self.ids, 
                            label_text='Passwort', password=True,
                            text_box_id='obs_password',text_validate_function=self.save_changes)

        box.add_widget(obs_box)

        remote_box = BoxLayout(orientation='vertical',size_hint_y=None, spacing="20dp")
        remote_box.bind(minimum_height=remote_box.setter('height')) # type: ignore
        self.ids["remote"] = weakref.proxy(remote_box)

        ueberschrift_remote = Label(text="Remote Einstellungen", size_hint=(1, None), size=(0,"20dp"), font_size="20sp")
        remote_box.add_widget(ueberschrift_remote)

        ueberschrift_server = Label(text="Server Einstellungen", size_hint=(.4, None), size=(0,"20dp"), font_size="17sp")
        remote_box.add_widget(ueberschrift_server)

        UI.create_label_and_Textbox(remote_box, self.ids, 
                            label_text='Host-Port', text_size_hint=(.1,1), is_port=True,
                            text_box_id='port_client',text_validate_function=self.save_changes)
        
        ueberschrift_client = Label(text="Client Einstellungen", size_hint=(.4, None), size=(0,"20dp"), font_size="17sp")
        remote_box.add_widget(ueberschrift_client)

        UI.create_label_and_Textbox(remote_box, self.ids, 
                            label_text='IP-Adresse', 
                            text_box_id='ip_server',text_validate_function=self.save_changes)
        
        UI.create_label_and_Textbox(remote_box, self.ids, 
                            label_text='Port', text_size_hint=(.1,1), is_port=True,
                            text_box_id='port_server',text_validate_function=self.save_changes)
        
        grid=GridLayout(cols=2,size_hint_y=None, spacing="20dp")
        grid.bind(minimum_height=grid.setter('height')) #type: ignore
        
        grid.add_widget(Label(text="Deine öffentliche\nIpv4-Adresse", size_hint=(.5,None), size=(0,"30dp")))
        grid.add_widget(Label(on_ref_press=self.clipboard,text=f"[ref=ip]{externalIPv4}[/ref]", size_hint=(.5,None), size=(0,"30dp"), markup=True))
        grid.add_widget(Label(text="Deine öffentliche\nIpv6-Adresse", size_hint=(.5,None), size=(0,"30dp")))
        grid.add_widget(Label(on_ref_press=self.clipboard,text=f"[ref=ipv6]{externalIPv6}[/ref]", size=(0,"30dp"), size_hint=(.5,None),markup=True))
        
        remote_box.add_widget(grid)
        box.add_widget(remote_box)

        player_box = BoxLayout(orientation='vertical',size_hint_y=None, spacing="30dp")
        player_box.bind(minimum_height=player_box.setter('height')) # type: ignore

        ueberschrift_player = Label(text="Spieler", size_hint=(1, None), size=(0,"20dp"), font_size="20sp")
        player_box.add_widget(ueberschrift_player)

        player_count_box = BoxLayout(orientation='horizontal', size=(0, "30dp"), spacing="20dp")
        player_count_box.bind(minimum_height=player_count_box.setter('height')) # type: ignore

        player_count_label = Label(text="Spieleranzahl", size_hint=(.2,None), size=(0,"30dp"))
        player_count_box.add_widget(player_count_label)

        checkboxes_box = BoxLayout(size_hint_x=.7, size_hint_y=None, size=(0,"30dp"))
        for i in range(1, 5):
            checkbox = CheckBox(group='player_count', pos_hint={"center_y": .5}, size_hint=(None, None), size=("20dp", "20dp"))
            checkbox.bind(on_press=lambda instance, player_count=i: self.change_player_count(player_count, player_box)) # type: ignore
            self.ids[f"player_count_{i}"] = weakref.proxy(checkbox)
            checkboxes_box.add_widget(checkbox)

            label = Label(text=str(i), pos_hint={"center_y": .5}, size_hint=(None, None), size=("20dp", "20dp"))
            checkboxes_box.add_widget(label)

        player_count_box.add_widget(checkboxes_box)

        player_settings_ausklappen = ToggleButton(text=">",size_hint_x=.1, size_hint_y=None, size=(0,"30dp"), on_press=lambda instance: self.player_ausklappen(instance, player_box))
        self.ids["player_settings_ausklappen"] = weakref.proxy(player_settings_ausklappen)
        player_count_box.add_widget(player_settings_ausklappen)

        player_box.add_widget(player_count_box)

        box.add_widget(player_box)
        
        self.add_widget(box)

        self.load_config()
    
    def set_game_sprites(self, sprite_box):
        game_sprites_box = BoxLayout(orientation='vertical', size_hint_y=None, spacing="20dp")
        game_sprites_box.bind(minimum_height=game_sprites_box.setter('height')) # type: ignore
        self.ids["game_sprites"] = weakref.proxy(game_sprites_box)
        for text, id in self.games.items():
            UI.create_text_and_browse_button(game_sprites_box,self.ids,
                                    box_size_hint_y=None,
                                    label_text=text,
                                    text_id_name=id, text_validate_function=None,
                                    browse_function=self.browse)
        
        sprite_box.add_widget(game_sprites_box)

    def game_sprites_ausklappen(self, instance, sprite_box):
        if instance.state == "down":
            instance.text = "^"
            self.set_game_sprites(sprite_box)
            self.load_game_sprites_config()
        else:
            instance.text = ">"
            games = self.ids["game_sprites"]
            sprite_box.remove_widget(games)

    def ausklapp_button_zeigen_oder_verstecken(self, instance, initializing=False):
        ausklappbutton = self.ids["games_ausklappen"]
        if not initializing:
            self.save_changes(instance)
        if instance.state == 'down':
            ausklappbutton.disabled = False
            ausklappbutton.opacity = 1
        else:
            ausklappbutton.disabled = True
            ausklappbutton.opacity = 0
            if ausklappbutton.state == "down":
                self.game_sprites_ausklappen(ausklappbutton, self.ids["sprite"])
    
    def change_player_count(self, player_count, player_box):
        pl["player_count"] = player_count
        if self.ids["player_settings_ausklappen"].state =='down':
            player_box.remove_widget(self.ids["player_settings"])
            self.add_player_checkBoxes(player_box)
            # self.pressCheckBoxes()
        
        with open(f"{configsave}player.yml", 'w') as file:
            yaml.dump(pl, file)
    
    def player_ausklappen(self, instance, player_box):
        if instance.state == "down":
            instance.text = "^"
            self.add_player_checkBoxes(player_box)
            # self.pressCheckBoxes()
        else:
            instance.text = ">"
            player = self.ids["player_settings"]
            player_box.remove_widget(player)
    
    def add_player_checkBoxes(self, player_box, begin=1):
        player_settings_box = GridLayout(cols=2, size_hint_y=None, spacing="20dp")
        player_settings_box.bind(minimum_height=player_settings_box.setter('height')) # type: ignore
        self.ids["player_settings"] = weakref.proxy(player_settings_box)
        for i in range(begin, pl['player_count'] + 1):
            idLabel = f"label_spieler_{i}"
            label = Label(text=f"Spieler {i}")
            player_settings_box.add_widget(label)
            self.ids[idLabel] = weakref.proxy(label)

            idBox = f"box_spieler_{i}"
            box = BoxLayout(orientation="horizontal")
            player_settings_box.add_widget(box)
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
        
        player_box.add_widget(player_settings_box)
        self.pressCheckBoxes()

    def pressCheckBoxes(self):
        for i in range(1, pl['player_count'] + 1):
            if pl[f"remote_{i}"]:
                self.ids[f"remote_player_{i}"].state = "down"
            if pl[f"obs_{i}"]:
                self.ids[f"obs_player_{i}"].state = "down"
    
    def toggle_obs(self, widgets):
        for obs in widgets.ids:
            ObsCheckBox = widgets.ids[obs]
            ObsCheckBox.disabled = widgets.state != 'down'
            if 'state' in dir(ObsCheckBox):
                ObsCheckBox.state = 'normal'
        self.pressCheckBoxes()
        self.save_changes()
    
    def clipboard(self, instance):
        result = (instance.text).split(']')[1].split('[')[0]
        Clipboard.copy(result)

    def browse(self, widget, modus):
        if modus == 'file':
            path = fd.askopenfilename()
        else:
            path = fd.askdirectory()
        if path:
            if self.ids["games_ausklappen"].state == 'down':
                games = [self.ids[id] for text, id in self.games.items()]
            else:
                games=[]
            stripped_path = path.replace(sp['common_path'], "", 1) if widget in games else path
            widget.text = stripped_path
            self.save_changes()
    
    def load_config(self):
        self.ids.common_path.text = sp['common_path']
        self.ids.items_path.text = sp['items_path']
        self.ids.badges_path.text = sp['badges_path']
        self.ids.game_sprites_check.state = 'down' if not sp['single_path_check'] else 'normal'
        self.ausklapp_button_zeigen_oder_verstecken(self.ids.game_sprites_check, initializing=True)

        self.ids.bizhawk_exe.text = bh['path']
        self.ids.bizhawk_port.text = bh['port']

        self.ids["obs_password"].text = obs['password']
        self.ids["obs_host"].text = obs['host']
        self.ids["obs_port"].text = obs['port']

        self.ids["ip_server"].text = rem[f'server_ip_adresse']
        self.ids["port_client"].text = rem[f'client_port']
        self.ids['port_server'].text = rem[f'server_port']

        self.ids[f"player_count_{pl['player_count']}"].state = "down"

    def load_game_sprites_config(self):
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
        self.ids.gen6_x.text = sp['x']
        self.ids.gen6_alphasapphire.text = sp['alphasapphire']
        self.ids.gen7_sun.text = sp['sun']
        self.ids.gen7_usun.text = sp['usun']

    def save_changes(self, *args):
        sp['common_path'] = self.ids.common_path.text
        sp['single_path_check'] = not self.ids.game_sprites_check.state == 'down'
        sp['items_path'] = self.ids.items_path.text
        sp['badges_path'] = self.ids.badges_path.text
        client.conf = sp
        asyncio.create_task(client.redraw_obs())
        with open(f"{configsave}sprites.yml", 'w') as file:
            yaml.dump(sp, file)

        if self.ids["games_ausklappen"].state == 'down':
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
            sp['x'] = self.ids.gen6_x.text
            sp['alphasapphire'] = self.ids.gen6_alphasapphire.text
            sp['sun'] = self.ids.gen7_sun.text
            sp['usun'] = self.ids.gen7_usun.text

        with open(f"{configsave}sprites.yml", 'w') as file:
            yaml.dump(sp, file)

        bh['path'] = self.ids.bizhawk_exe.text
        bh['port'] = self.ids.bizhawk_port.text
        
        with open(f"{configsave}bh_config.yml", 'w') as file:
            yaml.dump(bh, file)

        obs['password'] = self.ids["obs_password"].text
        obs['host'] = self.ids["obs_host"].text
        obs['port'] = self.ids["obs_port"].text
        
        with open(f"{configsave}obs_config.yml", 'w') as file:
            yaml.dump(obs, file)

        # rem['start_server'] = self.ids['main_Yes'].state == "down"
        rem['client_port'] = self.ids['port_client'].text

        rem['server_ip_adresse'] = self.ids['ip_server'].text
        rem['server_port'] = self.ids['port_server'].text

        with open(f"{configsave}remote.yml", 'w') as file:
            yaml.dump(rem, file)

        if self.ids["player_settings_ausklappen"].state == 'down':
            for i in range(1, pl['player_count'] + 1):
                pl[f"remote_{i}"] = self.ids[f"remote_player_{i}"].state == "down"
                pl[f"remote_{i}"] = False
                pl[f"obs_{i}"] = self.ids[f"obs_player_{i}"].state == "down"
                pl[f"obs_{i}"] = False

        with open(f"{configsave}player.yml", 'w') as file:
            yaml.dump(pl, file)

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

        UI.create_label_and_Textbox(grid, self.ids, text_box_id="obs_password", label_text = 'Passwort\nWebsocket', text_validate_function=self.save_changes, password=True)
        UI.create_label_and_Textbox(grid, self.ids, text_box_id="obs_host", label_text = 'IP-Adresse\nWebsocket', text_validate_function=self.save_changes)
        UI.create_label_and_Textbox(grid, self.ids, text_box_id="obs_port", label_text = 'Port des\nWebsocket', text_validate_function=self.save_changes)

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

        UI.create_label_and_Textbox(serverGrid, self.ids, text_box_id="ip_server", label_text='IP-Adresse:', label_size_hint=(.3,1), text_validate_function=self.save_changes)
        self.ids["ip_server"].text = rem[f'server_ip_adresse']
        self.ids["port_client"].text = rem[f'client_port']

        UI.create_label_and_Textbox(serverGrid, self.ids, text_box_id="port_client", label_text='Port:', label_size_hint=(.3,1), text_validate_function=self.save_changes)

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

        UI.create_label_and_Textbox(serverGrid, self.ids, text_box_id="port_server", label_size_hint=(.3,1), label_text='Port:', text_validate_function=self.save_changes)
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