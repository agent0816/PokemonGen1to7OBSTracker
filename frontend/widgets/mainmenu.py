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
import backend.server as server
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
        
        global sp
        global rem
        self.init_config(sp, rem)

    def toggle_obs(self, instance):
        if instance.text == "OBS verbinden":
            self.connectOBS()
            instance.text = "OBS trennen"
        elif instance.text == "OBS trennen":
            self.disconnectOBS()
            instance.text = "OBS verbinden"
    
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

    def launchbh(self, instance):
        global bizConnector
        instance.disabled = True
        if rem['start_server']:
            self.connect_bh_client()
            bizConnector = asyncio.create_task(client.pass_bh_to_server(("127.0.0.1", rem['server_port']), bh['port']))
        else:
            bizConnector = asyncio.create_task(client.pass_bh_to_server((rem['server_ip_adresse'], rem['client_port']), bh['port']))
        global connectors
        asyncio.gather(*connectors)
        for i in range(pl['player_count']):
            if not pl[f'remote_{i+1}']:
                subprocess.Popen([bh['path'], f'--lua={os.path.abspath(f"./backend/Player{i+1}.lua")}', f'--socket_ip={bh["host"]}', f'--socket_port={bh["port"]}'])

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

    def connect_bh_client(self):
        global clientConnector
        global connectors
        if not clientConnector:
            clientConnector = asyncio.create_task(client.connect_client("127.0.0.1", rem["server_port"]))
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

        rem['start_server'] = self.ids['start_server'].state == "down"
        with open(f"{configsave}player.yml", 'w') as file:
            yaml.dump(pl, file)

    def switch_to_settings(self, instance):
        self.manager.current = "SettingsMenu"