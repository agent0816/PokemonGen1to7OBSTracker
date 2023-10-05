import os
import subprocess
import sys
import weakref
import yaml
import asyncio
from kivy.clock import Clock
from kivy.graphics import Color
from kivy.graphics import Rectangle
from kivy.uix.anchorlayout import AnchorLayout
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.checkbox import CheckBox
from kivy.uix.gridlayout import GridLayout
from kivy.uix.image import Image
from kivy.uix.label import Label
from kivy.uix.popup import Popup
from kivy.uix.screenmanager import Screen
from kivy.uix.scrollview import ScrollView
from kivy.uix.togglebutton import ToggleButton
from frontend.widgets.connectionstatus import ObjectConnectionStatusCircle
from frontend.widgets.connectionstatus import ValueConnectionStatusCircle
from backend.classes.Pokemon import Pokemon
from backend.classes.obs import OBS
import frontend.UIFactory as UI
import logging

logger = logging.getLogger(__name__)
logging_formatter = logging.Formatter('[%(asctime)s] %(levelname)s: %(message)s')

file_handler = logging.FileHandler('logs/frontend.log', 'w')
file_handler.setFormatter(logging_formatter)
logger.addHandler(file_handler)

stream_handler = logging.StreamHandler(sys.stdout)
stream_handler.setFormatter(logging_formatter)
logger.addHandler(stream_handler)

class BizhawkSavePopup(Popup):
    def __init__(self, bizhawk_instances, bizhawk_button, bizhawk, **kwargs):
        super().__init__(**kwargs)
        self.bizhawk_instances = bizhawk_instances
        self.bizhawk_button = bizhawk_button
        self.bizhawk = bizhawk
        
        self.title = 'Speichern nicht vergessen!'
        self.size_hint = (0.8, 0.4)


        layout = BoxLayout(orientation='vertical')
        layout.add_widget(Label(text='Hast du gespeichert??'))

        btn_layout = BoxLayout(size_hint_y=None, height="50dp", spacing="5dp")
        btn_yes = Button(text='Ja', on_press=self.on_yes)
        btn_no = Button(text='Nein', on_press=self.dismiss)
        
        btn_layout.add_widget(btn_yes)
        btn_layout.add_widget(btn_no)
        layout.add_widget(btn_layout)

        self.content = layout

    def on_yes(self, instance):
        asyncio.create_task(self.bizhawk.stop_and_terminate(self.bizhawk_instances))

        self.bizhawk_button.text = "Bizhawk starten"
        self.dismiss()

class MainMenu(Screen):
    def __init__(self, arceus, bizhawk, bizhawk_instances, munchlax, obs_websocket, configsave, sp, rem, obs, bh, pl, **kwargs):
        self.arceus = arceus
        self.bizhawk = bizhawk
        self.bizhawk_instances = bizhawk_instances
        self.munchlax = munchlax
        self.obs_websocket: OBS = obs_websocket
        self.configsave = configsave
        self.sp = sp
        self.rem = rem
        self.obs = obs
        self.bh = bh
        self.pl = pl
        self.connectors = set()

        super().__init__(**kwargs)
        self.name = "MainMenu"
        
        frame = BoxLayout(orientation='horizontal')
        click_frame = BoxLayout(orientation='vertical')
        
        control_frame = self.create_control_frame()
        showing_frame = self.create_showing_frame()
        
        click_frame.add_widget(control_frame)
        click_frame.add_widget(showing_frame)

        frame.add_widget(click_frame)

        pokemon_frame = self.create_pokemon_frame()

        frame.add_widget(pokemon_frame)
        self.add_widget(frame)
        
        self.init_config(self.sp, self.rem)

    def create_control_frame(self):
        control_frame = BoxLayout(orientation='horizontal')

        logo_settings = BoxLayout(orientation='vertical', size_hint=(.3,1))
        logo = Label(text='Logo')
        logo_settings.add_widget(logo)

        settings = Button(text='Einstellungen', on_press=self.switch_to_settings)
        logo_settings.add_widget(settings)
        control_frame.add_widget(logo_settings)

        connections = BoxLayout(orientation='horizontal')

        buttons_box = BoxLayout(orientation='vertical')
        self.ids["buttons_box"] = weakref.proxy(buttons_box)

        server_client_button = Button(text='Server/Client', on_press=self.launchserver)
        self.ids["server_client_button"] = weakref.proxy(server_client_button)
        buttons_box.add_widget(server_client_button)

        obs_connect = Button(text="OBS verbinden", on_press=self.toggle_obs)
        buttons_box.add_widget(obs_connect)
        
        emulator = Button(text='Bizhawk starten', on_press=self.launchbh)
        buttons_box.add_widget(emulator)

        connections.add_widget(buttons_box)

        status_box = BoxLayout(orientation='vertical')

        server_status_box = BoxLayout(orientation='vertical')
        status_label = Label(text="Status Server")

        server_status_box.add_widget(status_label)

        server_box = BoxLayout(orientation='horizontal')

        server_or_client_label = Label(text="Server?")
        server_box.add_widget(server_or_client_label)

        server_or_client_check = CheckBox(on_press=lambda instance: self.toggle_server_client(instance, server_client_button))
        self.ids["start_server"] = weakref.proxy(server_or_client_check)

        server_box.add_widget(server_or_client_check)
        
        UI.create_connection_status(server_box, ObjectConnectionStatusCircle, self.arceus, ids = self.ids, id="arceus_status")

        server_status_box.add_widget(server_box)

        status_box.add_widget(server_status_box)

        obs_box = BoxLayout(orientation='horizontal')

        obs_box.add_widget(Label(text='OBS status:'))
        UI.create_connection_status(obs_box, ObjectConnectionStatusCircle, self.obs_websocket)

        status_box.add_widget(obs_box)

        # verbundene Munchlaxes

        munchlax_box = BoxLayout(orientation = 'vertical')
        munchlax_box_label = Label(text = 'Client Status')
        munchlax_box.add_widget(munchlax_box_label)

        munchlax_status_box = BoxLayout(orientation='horizontal')
        self.ids["munchlax_status_box"] = weakref.proxy(munchlax_status_box)
        UI.create_connection_status_with_labels(munchlax_status_box, ObjectConnectionStatusCircle, self.munchlax.client_id[:3], self.munchlax, ids=self.ids, id=self.munchlax.client_id)

        Clock.schedule_interval(lambda instance:self.change_munchlax_status(munchlax_status_box), 1)

        munchlax_box.add_widget(munchlax_status_box)

        status_box.add_widget(munchlax_box)

        # lokale Bizhawks und Bizhawk-server

        bizhawk_box = BoxLayout(orientation='vertical')

        bizhawk_box.add_widget(Label(text='Bizhawk Status'))
        bizhawk_status_box = BoxLayout(orientation='horizontal')

        UI.create_connection_status_with_labels(bizhawk_status_box, ObjectConnectionStatusCircle, "Server" , self.bizhawk)

        Clock.schedule_interval(lambda instance:self.change_bizhawk_status(bizhawk_status_box), 1)
        
        bizhawk_box.add_widget(bizhawk_status_box)

        status_box.add_widget(bizhawk_box)

        connections.add_widget(status_box)

        control_frame.add_widget(connections)

        return control_frame
    
    def create_showing_frame(self):
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

        return showing_frame
    
    def create_pokemon_frame(self):
        pokemon_frame = ScrollView(do_scroll_y=False, do_scroll_x=True)
        
        box = BoxLayout(orientation='horizontal',size_hint_x=None, spacing="20dp", padding=(0, "20dp"))
        box.bind(minimum_width=box.setter('width')) # type: ignore

        color_lut = {1:"1a4d9a7f", 2:"9a671a7f", 3:"9a9a1a7f", 4:"1a9a1a7f"}
        items2 = yaml.safe_load(open('backend/data/items2.yml'))
        team = [Pokemon(6, False, lvl=5, nickname="Stephan", item="master-ball") for i in range(6)]

        print(team)

        for player in range(1, self.pl['player_count'] + 1):
            trainer = TrainerBox(player, self.munchlax, self.obs_websocket, color_lut[player], orientation='vertical', size_hint=(None, 1), size=("130dp",0))
            box.add_widget(trainer)
        
        pokemon_frame.add_widget(box)
        return pokemon_frame
    
    def change_munchlax_status(self, box):
        if self.rem["start_server"]:
            for client_id in self.arceus.munchlax_status:
                if client_id not in self.ids:
                    UI.create_connection_status_with_labels(box, ValueConnectionStatusCircle, client_id[:3], client_id, self.arceus.munchlax_status, ids=self.ids, id=client_id)
        else:
            if self.munchlax.is_connected and self.ids["server_client_button"].text != "Client beenden":
                self.ids["server_client_button"].text = "Client beenden"
    
    def change_bizhawk_status(self, box):
        for client_id in self.bizhawk.bizhawks_status:
                if client_id not in self.ids:
                    UI.create_connection_status_with_labels(box, ValueConnectionStatusCircle, client_id, client_id, self.bizhawk.bizhawks_status, ids=self.ids, id=client_id)

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
            logger.info("OBS connected.")

    def disconnectOBS(self, *args):
        if self.obs_websocket.is_connected:
            task = asyncio.create_task(self.obs_websocket.disconnect())
            self.connectors.add(task)
            logger.info("OBS disconnected.")

    def launchbh(self, instance):
        instance.disabled = True
        if instance.text == "Bizhawk starten":
            if not self.bizhawk.server:
                task = asyncio.create_task(self.bizhawk.start(self.munchlax))
                self.connectors.add(task)
            for i in range(self.pl['player_count']):
                if not self.pl[f'remote_{i+1}']:
                    process = subprocess.Popen([self.bh['path'], f'--lua={os.path.abspath(f"./backend/lua/Player{i+1}.lua")}', f'--socket_ip={self.bh["host"]}', f'--socket_port={self.bh["port"]}'])
                    self.bizhawk_instances.append(process)
            instance.text = "Bizhawk beenden"
        elif instance.text == "Bizhawk beenden":
            popup = BizhawkSavePopup(self.bizhawk_instances, instance, self.bizhawk)
            popup.open()

        def enable_button(button):
            button.disabled = False
        Clock.schedule_once(lambda dt: enable_button(instance), 5)

    def toggle_server_client(self, instance, button, initializing=False):
        if instance.state == "down":
            button.text = "Server starten"
            button.unbind(on_press=self.connect_client)
            button.bind(on_press=self.launchserver)
            self.ids["arceus_status"].opacity = 1

            status_clearing = Button(text='Clients bereinigen', on_press=self.clear_clients)
            self.ids["status_clearing"] = weakref.proxy(status_clearing)
            self.ids["buttons_box"].add_widget(status_clearing)
        else:
            button.text = "Client starten"
            button.unbind(on_press=self.launchserver)
            button.bind(on_press=self.connect_client)
            self.ids["arceus_status"].opacity = 0
            if not initializing:
                self.ids["buttons_box"].remove_widget(self.ids["status_clearing"])
            if self.rem["start_server"]:
                asyncio.create_task(self.munchlax.disconnect())
                asyncio.create_task(self.arceus.stop())
        if not initializing:
            self.save_changes(instance)
    
    def clear_clients(self, instance):
        box = self.ids["munchlax_status_box"]
        id_list = [id for id in self.arceus.munchlaxes]
        if not self.munchlax.is_connected:
            id_list.append(self.munchlax.client_id)

        widgets_zu_entfernen = []
        ids_to_remove = []
        for id in box.ids:
            if id not in id_list:
                widgets_zu_entfernen.append(box.ids[id])
                ids_to_remove.append(id)

        for widget in widgets_zu_entfernen:
            box.remove_widget(widget)

        for id in ids_to_remove:
            del box.ids[id]
    
    def connect_client(self,instance, *args):
        if instance.text.endswith("starten"):
            if not self.munchlax.is_connected:
                task = asyncio.create_task(self.munchlax.connect())
                self.connectors.add(task)
            if instance.text == "Client starten":
                instance.text = "Client beenden"
        elif instance.text == "Client beenden":
            if self.munchlax.is_connected:
                asyncio.create_task(self.munchlax.disconnect())
            instance.text = "Client starten"

    def launchserver(self, instance,*args):
        if instance.text == "Server starten":
            if not self.arceus.server:
                asyncio.create_task(self.arceus.start())
            self.connect_client(instance)
            instance.text = "Server beenden"
        elif instance.text == "Server beenden":
            if self.arceus.server:
                asyncio.create_task(self.munchlax.disconnect())
                asyncio.create_task(self.arceus.stop())
            instance.text = "Server starten"


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
        
        ip_to_connect = '127.0.0.1' if self.rem["start_server"] else self.rem["server_ip_adresse"]
        port_to_connect = self.rem["client_port"] if self.rem["start_server"] else self.rem["server_port"]
        
        self.munchlax.host = ip_to_connect
        self.munchlax.port = port_to_connect

        with open(f"{self.configsave}remote.yml", 'w') as file:
            yaml.dump(self.rem, file)

    def start_or_end_arceus(self, instance):
        if instance.state == 'down':
            if not self.rem["start_server"]:
                asyncio.create_task(self.munchlax.disconnect())
                asyncio.create_task(self.arceus.stop())
    
    def switch_to_settings(self, instance):
        self.manager.current = "SettingsMenu"

class TrainerBox(BoxLayout):
    def __init__(self, player_id, munchlax, obs_websocket, color, **kwargs):
        super().__init__(**kwargs)

        self.player_id = player_id
        self.munchlax = munchlax
        self.obs_websocket: OBS = obs_websocket
        self.pokemon_boxes = []

        decimal_color = (int(color[0:2], 16) / 255, int(color[2:4], 16) / 255, int(color[4:6], 16) / 255, int(color[6:8], 16) / 255)

        with self.canvas.before: #type: ignore
            Color(*decimal_color)
            self.rect = Rectangle(size=self.size, pos=self.pos)
        
        self.bind(size=self._update_rect, pos=self._update_rect) # type: ignore

        self.add_widget(Label(text=f"Spieler {self.player_id}"))

        for pokemon in range(6):
            pokemon_box = self.create_pokemon_box()
            self.pokemon_boxes.append(pokemon_box)

            self.add_widget(pokemon_box)

    def _update_rect(self, instance, value):
        self.rect.pos = instance.pos
        self.rect.size = instance.size
    
    def create_pokemon_box(self):
        pokemon_box = BoxLayout(orientation='vertical')

        sprite_box = BoxLayout(orientation='horizontal')

        item_and_lvl_box = BoxLayout(orientation='vertical')

        level = Label(text="lvl. 5")
        pokemon_box.ids["Level"] = weakref.proxy(level)
        item_and_lvl_box.add_widget(level)

        item_text = Label(text="kein Item")
        pokemon_box.ids["Item_Name"] = weakref.proxy(item_text)
        item_and_lvl_box.add_widget(item_text)

        item_image = Image(source="../sprites/sprites/items/ability-capsule.png")
        pokemon_box.ids["Item_Image"] = weakref.proxy(item_image)
        item_and_lvl_box.add_widget(item_image)

        sprite_box.add_widget(item_and_lvl_box)
        sprite = Image(source="../sprites/sprites/pokemon/versions/generation-iv/heartgold-soulsilver/6.png")
        pokemon_box.ids["Sprite"] = weakref.proxy(sprite)

        sprite_box.add_widget(sprite)

        pokemon_box.add_widget(sprite_box)

        nickname = Label(text="Pokemon")
        pokemon_box.ids["Nickname"] = weakref.proxy(nickname)
        pokemon_box.add_widget(nickname)
        
        return pokemon_box