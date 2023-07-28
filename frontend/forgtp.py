import os
import sys
import subprocess
import weakref
import yaml
import asyncio
from kivy.app import App
from kivy.core.clipboard import Clipboard
from kivy.core.window import Window
from kivy.config import Config
from kivy.uix.anchorlayout import AnchorLayout
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.checkbox import CheckBox
from kivy.uix.screenmanager import FadeTransition
from kivy.uix.floatlayout import FloatLayout
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
        
        sprite_box = BoxLayout(orientation='vertical',size_hint_y=None, spacing="20dp")
        sprite_box.bind(minimum_height=sprite_box.setter('height')) # type: ignore
        self.ids["sprite"] = weakref.proxy(sprite_box)

        ueberschrift = Label(text="Sprites", size_hint=(1, None), size=(0,"20dp"), font_size="20sp")
        sprite_box.add_widget(ueberschrift)

        UI.create_text_and_browse_button(sprite_box,self.ids,
                                box_id_name='common_path_box',
                                label_text='Dateipfad Sprites',
                                text_id_name="common_path", text_validate_function=None,
                                browse_function=lambda instance: self.create_callback(self.ids["common_path"]))

        float_box = BoxLayout(orientation='vertical', size_hint_y=None, height=0)
        float_box.bind(minimum_height=float_box.setter('height')) # type: ignore

        game_sprites_bool_box = BoxLayout(orientation='horizontal', size_hint_y=None, size=(0,"30dp"))
        game_sprites_ausklappen = ToggleButton(text=">",size_hint_x=.1, on_press=lambda instance: self.game_sprites_ausklappen(instance, float_box))
        self.ids["games_ausklappen"] = weakref.proxy(game_sprites_ausklappen)
        game_sprites_bool_box.add_widget(game_sprites_ausklappen)

        game_sprites_label_einzeln = Label(text="Sprites jedes Spiels einzeln festlegen:", size_hint_x=.7)
        game_sprites_bool_box.add_widget(game_sprites_label_einzeln)
        
        game_sprites_checkbox = CheckBox(size_hint_x=.2, on_press=lambda instance: self.ausklapp_button_zeigen_oder_verstecken(instance))
        self.ids["game_sprites_check"] = weakref.proxy(game_sprites_checkbox)
        game_sprites_bool_box.add_widget(game_sprites_checkbox)

        sprite_box.add_widget(game_sprites_bool_box)

        sprite_box.add_widget(float_box)

        UI.create_text_and_browse_button(sprite_box,self.ids,
                                box_id_name='items_path_box',
                                label_text='Dateipfad Items',
                                text_id_name="items_path", text_validate_function=None,
                                browse_function=lambda instance: self.create_callback(self.ids["items_path"]))
        
        UI.create_text_and_browse_button(sprite_box,self.ids,
                                box_id_name='badges_path_box',
                                label_text='Dateipfad Orden',
                                text_id_name="badges_path", text_validate_function=None,
                                browse_function=lambda instance: self.create_callback(self.ids["badges_path"]))

        self.add_widget(sprite_box)
        self.load_config()

    def create_callback(self, widget):
        def callback(instance):
            TrackerApp.browse(widget,self, instance)
        return callback
    
    def set_game_sprites(self, sprite_box):
        game_sprites_box = BoxLayout(orientation='vertical', size_hint_y=None, spacing="20dp")
        game_sprites_box.bind(minimum_height=game_sprites_box.setter('height')) # type: ignore
        self.ids["game_sprites"] = weakref.proxy(game_sprites_box)
        for text, id in self.games.items():
            UI.create_text_and_browse_button(game_sprites_box,self.ids,
                                    box_size_hint_y=None,
                                    label_text=text,
                                    text_id_name=id, text_validate_function=None,
                                    browse_function=lambda instance: self.create_callback(self.ids[id]))
        
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
        self.save_changes(instance)

    def ausklapp_button_zeigen_oder_verstecken(self, instance):
        ausklappbutton = self.ids["games_ausklappen"]
        self.save_changes(instance)
        if instance.state == 'down':
            ausklappbutton.disabled = False
            ausklappbutton.opacity = 1
        else:
            ausklappbutton.disabled = True
            ausklappbutton.opacity = 0
            if ausklappbutton.state == "down":
                self.game_sprites_ausklappen(ausklappbutton, self.ids["sprite"])

    def load_config(self):
        self.ids.common_path.text = sp['common_path']
        self.ids.items_path.text = sp['items_path']
        self.ids.badges_path.text = sp['badges_path']
        self.ids.game_sprites_check.state = 'down' if not sp['single_path_check'] else 'normal'
        self.ausklapp_button_zeigen_oder_verstecken(self.ids.game_sprites_check)

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

    @classmethod
    def browse(cls, widget, instance, *args):
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