import os
import subprocess
import sys
import weakref
import yaml
import asyncio
from kivy.clock import Clock
from kivy.properties import OptionProperty
from kivy.uix.anchorlayout import AnchorLayout
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.checkbox import CheckBox
from kivy.uix.gridlayout import GridLayout
from kivy.uix.label import Label
from kivy.uix.screenmanager import Screen
from kivy.uix.togglebutton import ToggleButton
import logging

logger = logging.getLogger(__name__)
logging_formatter = logging.Formatter('[%(asctime)s] %(levelname)s: %(message)s')

file_handler = logging.FileHandler('logs/frontend.log', 'w')
file_handler.setFormatter(logging_formatter)
logger.addHandler(file_handler)

stream_handler = logging.StreamHandler(sys.stdout)
stream_handler.setFormatter(logging_formatter)
logger.addHandler(stream_handler)

class ConnectionStatusLabel(Label):
    reconnect_status = OptionProperty('disconnected', options=['connected', 'reconnecting', 'disconnected'])

    def on_reconnect_status(self, instance, value):
        if value == 'connected':
            self.text = "Connected"
            self.color = (0, 1, 0, 1)  # Green
        elif value == 'reconnecting':
            self.text = "Reconnecting..."
            self.color = (1, 1, 0, 1)  # Yellow
        else:
            self.text = "Disconnected"
            self.color = (1, 0, 0, 1)  # Red

    def poll_backend_status(self, dt, connection_status):
        self.reconnect_status = connection_status

class MainMenu(Screen):
    def __init__(self, arceus, bizhawk, bizhawk_instances, munchlax, obs_websocket, configsave, sp, rem, obs, bh, pl, **kwargs):
        self.arceus = arceus
        self.bizhawk = bizhawk
        self.bizhawk_instances = bizhawk_instances
        self.munchlax = munchlax
        self.obs_websocket = obs_websocket
        self.configsave = configsave
        self.sp = sp
        self.rem = rem
        self.obs = obs
        self.bh = bh
        self.pl = pl
        self.connectors = set()

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
        
        server_client_box = BoxLayout(orientation='horizontal')

        server_client_button = Button(text='Server/Client', on_press=self.launchserver)
        self.ids["server_client_button"] = weakref.proxy(server_client_button)

        server_status_box = BoxLayout(orientation='vertical')
        status_label = Label(text="Status Server")

        server_status_box.add_widget(status_label)

        server_or_client_box = BoxLayout(orientation= 'horizontal')

        server_or_client_label = Label(text="Server?")
        server_or_client_box.add_widget(server_or_client_label)

        server_or_client_check = CheckBox(on_press=lambda instance: self.toggle_server_client(instance, server_client_button))
        self.ids["start_server"] = weakref.proxy(server_or_client_check)
        server_or_client_box.add_widget(server_or_client_check)

        server_status_box.add_widget(server_or_client_box)

        server_client_box.add_widget(server_client_button)
        server_client_box.add_widget(server_status_box)
        connections.add_widget(server_client_box)
        
        obs_connect = Button(text="OBS verbinden", on_press=self.toggle_obs)
        connections.add_widget(obs_connect)
        
        emulator = Button(text='Bizhawk starten', on_press=self.launchbh)
        connections.add_widget(emulator)

        control_frame.add_widget(connections)

        showing_frame = BoxLayout(orientation='horizontal')
        sort_layout = BoxLayout(orientation='vertical', spacing="20dp", padding=("5dp",0))

        ueberschrift_sortierung = Label(text="Sortierung",halign='left', size_hint_x=None, width=sort_layout.width, font_size="20sp")
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
        
        self.init_config(self.sp, self.rem)

    def toggle_obs(self, instance):
        if instance.text == "OBS verbinden":
            self.connectOBS()
            instance.text = "OBS trennen"
        elif instance.text == "OBS trennen":
            self.disconnectOBS()
            instance.text = "OBS verbinden"
    
    def connectOBS(self,*args):
        if not self.obs_websocket.is_connected:
            task = asyncio.create_task(self.obs_websocket.load_obsws())
            self.connectors.add(task)
            asyncio.gather(*self.connectors)
            logger.info("OBS connected.")

    def disconnectOBS(self, *args):
        if self.obs_websocket.is_connected:
            task = asyncio.create_task(self.obs_websocket.disconnect())
            self.connectors.add(task)
            asyncio.gather(*self.connectors)
            logger.info("OBS disconnected.")

    def launchbh(self, instance):
        instance.disabled = True
        if not self.bizhawk.server:
            task = asyncio.create_task(self.bizhawk.start(self.munchlax))
            self.connectors.add(task)
            asyncio.gather(*self.connectors)
        for i in range(self.pl['player_count']):
            if not self.pl[f'remote_{i+1}']:
                process = subprocess.Popen([self.bh['path'], f'--lua={os.path.abspath(f"./backend/Player{i+1}.lua")}', f'--socket_ip={self.bh["host"]}', f'--socket_port={self.bh["port"]}'])
                self.bizhawk_instances.append(process)

        def enable_button(button):
            button.disabled = False
        Clock.schedule_once(lambda dt: enable_button(instance), 5)

    def toggle_server_client(self, instance, button, initializing=False):
        if instance.state == "down":
            button.text = "Server starten"
            button.unbind(on_press=self.connect_client)
            button.bind(on_press=self.launchserver)
        else:
            button.text = "Client starten"
            button.unbind(on_press=self.launchserver)
            button.bind(on_press=self.connect_client)
        if not initializing:
            self.save_changes(instance)
    
    def connect_client(self, *args):
        logger.info("connect_client")
        if not self.munchlax.is_connected:
            task = asyncio.create_task(self.munchlax.connect())
            self.connectors.add(task)
            asyncio.gather(*self.connectors)
            logger.info(self.munchlax.is_connected)

    def launchserver(self,*args):
        if not self.arceus.server:
            task = asyncio.create_task(self.arceus.start())
            self.connectors.add(task)
            asyncio.gather(*self.connectors)
        self.connect_client()

    def init_config(self, sp, rem):
        self.ids.animated_check.state = 'down' if sp['animated'] else 'normal'
        self.ids.names_check.state = 'down' if sp['show_nicknames'] else 'normal'
        self.ids.items_check.state = 'down' if sp['show_items'] else 'normal'
        self.ids.badges_check.state = 'down' if sp['show_badges'] else 'normal'
        self.ids[sp['order']].state = 'down'
        self.ids['start_server'].state = "down" if rem['start_server'] else 'normal'
        self.toggle_server_client(self.ids['start_server'], self.ids['server_client_button'], initializing = True)

    def save_changes(self, instance):
        sorts = {"DexNr.":"dexnr", "Team": "team", "Level":"lvl", "Route":"route"}
        for button in ToggleButton.get_widgets("sort"):
            if button.state == 'down':
                self.sp['order'] = sorts[button.text]

        self.sp['animated'] = self.ids.animated_check.state == 'down'
        self.sp['show_nicknames'] = self.ids.names_check.state == 'down'
        self.sp['show_items'] = self.ids.items_check.state == 'down'
        self.sp['show_badges'] = self.ids.badges_check.state == 'down'
        # self.conf = self.sp
        self.munchlax.change_order()
        asyncio.create_task(self.obs_websocket.redraw_obs())
        with open(f"{self.configsave}sprites.yml", 'w') as file:
            yaml.dump(self.sp, file)

        self.rem['start_server'] = self.ids['start_server'].state == "down"
        with open(f"{self.configsave}player.yml", 'w') as file:
            yaml.dump(self.pl, file)

    def switch_to_settings(self, instance):
        self.manager.current = "SettingsMenu"