import sys
import weakref
import yaml
import asyncio
from kivy.core.clipboard import Clipboard
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.checkbox import CheckBox
from kivy.uix.gridlayout import GridLayout
from kivy.uix.label import Label
from kivy.uix.popup import Popup
from kivy.uix.screenmanager import Screen
from kivy.uix.scrollview import ScrollView
from kivy.uix.togglebutton import ToggleButton
from backend.classes.obs import OBS
from frontend.widgets.mainmenu import TrainerBox
import frontend.UIFactory as UI
import tkinter.filedialog as fd
import logging

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
logging_formatter = logging.Formatter('[%(asctime)s] %(levelname)s: %(message)s')

file_handler = logging.FileHandler('logs/frontend.log', 'w')
file_handler.setFormatter(logging_formatter)
logger.addHandler(file_handler)

stream_handler = logging.StreamHandler(sys.stdout)
stream_handler.setFormatter(logging_formatter)
logger.addHandler(stream_handler)

class SettingsMenu(Screen):
    def __init__(self, arceus, bizhawk, munchlax, obs_websocket, externalIPv4, externalIPv6, configsave, sp, rem, obs, bh, pl, app_version, **kwargs):
        super().__init__(**kwargs)

        self.name = "SettingsMenu"
        self.selected_session = "default"

        box = BoxLayout(orientation="vertical")
        header_box = BoxLayout(orientation='horizontal', size_hint_y=0.15, padding=(0,"10dp"))
        
        logo = Label(text='Logo', size_hint=(.15,1))
        header_box.add_widget(logo)

        self.head_label = Label(text=f"Version {app_version} | Session: {self.selected_session}",size_hint_x=.7)
        header_box.add_widget(self.head_label)
        
        main_menu_button = Button(text="Hauptmenü",size_hint_x=.15, on_press=self.back_to_menu)
        header_box.add_widget(main_menu_button)

        box.add_widget(header_box)

        layout = GridLayout(cols=2, size_hint_y=.85)

        button_box = BoxLayout(orientation="vertical", size_hint=(0.15, 1), pos_hint={"top": 0})
        self.scrollview = ScrollSettings(self, arceus, bizhawk, munchlax, obs_websocket, externalIPv4, externalIPv6, configsave, sp, rem, obs, bh, pl)

        settings_buttons = [
            ("Sprite\nPfade", 'sprite'),
            ("Bizhawk", 'bizhawk'),
            ("OBS", 'obs'),
            ("Remote", 'remote'),
            ("Spieler", 'player')
        ]

        for text, screen_name in settings_buttons:
            button = Button(text=text)
            button.bind(on_press=lambda instance, jump_id=screen_name: self.jump_to(self.scrollview, jump_id)) #type: ignore
            button_box.add_widget(button)

        layout.add_widget(button_box)
        layout.add_widget(self.scrollview)

        box.add_widget(layout)
        self.add_widget(box)

    def back_to_menu(self, instance):
        if self.is_session_selected:
            self.manager.current = "MainMenu"
        else:
            self.manager.current = "SessionMenu"

        self.scrollview.save_changes()

    def jump_to(self, scrollview, jump_id):
        scroll_max_height = scrollview.children[0].height
        new_scrollheight = scrollview.ids[jump_id].y
        
        if jump_id == "sprite":
            scrolling = 1
        elif jump_id == "player":
            scrolling = 0
        else:
            scrolling = new_scrollheight / scroll_max_height

        scrollview.scroll_y = scrolling

class ScrollSettings(ScrollView):
    def __init__(self, settingsscreen, arceus, bizhawk, munchlax, obs_websocket, externalIPv4, externalIPv6, configsave, sp, rem, obs, bh, pl, **kwargs):
        super().__init__(**kwargs)
        
        self.settingsscreen = settingsscreen
        self.arceus = arceus
        self.bizhawk = bizhawk
        self.munchlax = munchlax
        self.obs_websocket: OBS = obs_websocket
        self.externalIPv4 = externalIPv4
        self.externalIPv6 = externalIPv6
        self.configsave = configsave
        self.sp = sp
        self.rem = rem
        self.obs = obs
        self.bh = bh
        self.pl = pl
        
        self.games={
            'Rot und Blau':'gen1_red','Gelb':'gen1_yellow',
            'Silber':'gen2_silver','Gold':'gen2_gold','Kristall':'gen2_crystal',
            'Rubin und Saphir':'gen3_ruby','Smaragd':'gen3_emerald', 'Feuerrot und\nBlattgrün':'gen3_firered',
            'Diamant und Perl':'gen4_diamond','Platin':'gen4_platinum','Herzgold und\nSeelensilber':'gen4_heartgold',
            'Schwarz und Weiß (2)':'gen5_black',
            'X und Y':'gen6_x','Alpha Saphir und\nOmega Rubin':'gen6_alphasapphire',
            'Sonne und Mond':'gen7_sun','Ultra Sonne und\nUltra Mond':'gen7_usun'
        }

        box = BoxLayout(orientation='vertical',size_hint_y=None, spacing="20dp", padding=[0, "30dp"])
        box.bind(minimum_height=box.setter('height')) # type: ignore
        self.ids["settings_box"] = weakref.proxy(box)

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

        float_box = BoxLayout(orientation='vertical', size_hint_y=None, height=0)
        float_box.bind(minimum_height=float_box.setter('height')) # type: ignore
        self.ids["game_sprite_paths"] = weakref.proxy(float_box)

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

        float_box_obs = BoxLayout(orientation='vertical', size_hint_y=None, height=0, spacing="20dp")
        float_box_obs.bind(minimum_height=float_box_obs.setter('height')) # type: ignore
        self.ids["obs_sprites_box"] = weakref.proxy(float_box_obs)

        obs_sprites_bool_box = BoxLayout(orientation='horizontal', size_hint_y=None, size=(0,"30dp"))

        obs_sprites_label_einzeln = Label(text="Hast du ein Streamsetup mit zwei PCs?", size_hint_x=.7)
        obs_sprites_bool_box.add_widget(obs_sprites_label_einzeln)

        obs_sprites_checkbox = CheckBox(size_hint_x=.2, on_press=lambda instance: self.obs_2_pcs_setup(instance))
        self.ids["obs_sprites_check"] = weakref.proxy(obs_sprites_checkbox)
        obs_sprites_bool_box.add_widget(obs_sprites_checkbox)

        obs_sprites_ausklappen = ToggleButton(text=">",size_hint_x=.1, on_press=lambda instance: self.obs_sprites_ausklappen(instance, float_box_obs))
        self.ids["obs_games_ausklappen"] = weakref.proxy(obs_sprites_ausklappen)
        obs_sprites_bool_box.add_widget(obs_sprites_ausklappen)

        sprite_box.add_widget(obs_sprites_bool_box)

        sprite_box.add_widget(float_box_obs)

        box.add_widget(sprite_box)

        bizhawk_box = BoxLayout(orientation='vertical',size_hint_y=None, spacing="20dp")
        bizhawk_box.bind(minimum_height=bizhawk_box.setter('height')) # type: ignore
        self.ids["bizhawk"] = weakref.proxy(bizhawk_box)

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
        grid.add_widget(Label(on_ref_press=self.clipboard,text=f"[ref=ip]{self.externalIPv4}[/ref]", size_hint=(.5,None), size=(0,"30dp"), markup=True))
        grid.add_widget(Label(text="Deine öffentliche\nIpv6-Adresse", size_hint=(.5,None), size=(0,"30dp")))
        grid.add_widget(Label(on_ref_press=self.clipboard,text=f"[ref=ipv6]{self.externalIPv6}[/ref]", size=(0,"30dp"), size_hint=(.5,None),markup=True))
        
        remote_box.add_widget(grid)
        box.add_widget(remote_box)

        player_box = BoxLayout(orientation='vertical',size_hint_y=None, spacing="30dp")
        player_box.bind(minimum_height=player_box.setter('height')) # type: ignore
        self.ids["player"] = weakref.proxy(player_box)

        ueberschrift_player = Label(text="Spieler", size_hint=(1, None), size=(0,"20dp"), font_size="20sp")
        player_box.add_widget(ueberschrift_player)

        UI.create_label_and_Textbox(player_box, self.ids, 
                            label_text='Anzeigename', 
                            text_box_id='your_name',text_validate_function=self.save_changes)

        session_game_box = BoxLayout(orientation='horizontal', size=(0, "30dp"), spacing="20dp")
        session_game_box.bind(minimum_height=session_game_box.setter('height')) # type: ignore

        player_box.add_widget(session_game_box)

        session_game_grid=GridLayout(cols=2,size_hint_y=None, spacing="20dp")
        session_game_grid.bind(minimum_height=session_game_grid.setter('height'))

        session_game_label = Label(text="Spiel der Session", size_hint=(.2,None), size=(0,"30dp"))
        session_game_grid.add_widget(session_game_label)

        session_game = Label(text="", size_hint=(.8,None), size=(0,"30dp"))
        self.ids["session_game"] = weakref.proxy(session_game)
        session_game_grid.add_widget(session_game)

        player_box.add_widget(session_game_grid)

        player_count_box = BoxLayout(orientation='horizontal', size=(0, "30dp"), spacing="20dp")
        player_count_box.bind(minimum_height=player_count_box.setter('height')) # type: ignore

        player_count_label = Label(text="Spieleranzahl", size_hint=(.2,None), size=(0,"30dp"))
        player_count_box.add_widget(player_count_label)

        checkboxes_box = BoxLayout(size_hint_x=.7, size_hint_y=None, size=(0,"30dp"))
        for i in range(1, 5):
            checkbox = CheckBox(group='player_count', pos_hint={"center_y": .5}, size_hint=(None, None), size=("20dp", "20dp"), allow_no_selection=False)
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

        self.obs_2_pcs_setup(obs_sprites_checkbox, initializing=True)

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

    def set_obs_sprites(self, sprite_box):
        obs_sprites_box = BoxLayout(orientation='vertical', size_hint_y=None, spacing="20dp")
        obs_sprites_box.bind(minimum_height=obs_sprites_box.setter('height')) # type: ignore
        self.ids["obs_sprites"] = weakref.proxy(obs_sprites_box)

        game_sprites_label_einzeln = Label(text="Sprites jedes Spiels einzeln festlegen:", size_hint_x=.7)
        obs_sprites_box.add_widget(game_sprites_label_einzeln)

        for text, id in self.games.items():
            UI.create_text_and_browse_button(obs_sprites_box,self.ids,
                                    box_size_hint_y=None,
                                    label_text=f"{text} OBS",
                                    text_id_name=f"{id}_obs", text_validate_function=None,
                                    browse_function=self.browse)
        
        sprite_box.add_widget(obs_sprites_box)

    def game_sprites_ausklappen(self, instance, sprite_box):
        if instance.state == "down":
            instance.text = "^"
            self.set_game_sprites(sprite_box)
            self.load_game_sprites_config()
        else:
            instance.text = ">"
            games = self.ids["game_sprites"]
            sprite_box.remove_widget(games)

    def obs_sprites_ausklappen(self, instance, obs_sprite_box):
        if instance.state == "down":
            instance.text = "^"
            self.set_obs_sprites(obs_sprite_box)
            self.load_obs_sprites_config()
        else:
            instance.text = ">"
            games = self.ids["obs_sprites"]
            obs_sprite_box.remove_widget(games)

    def ausklapp_button_zeigen_oder_verstecken(self, instance, initializing=False):
        ausklappbutton = self.ids["games_ausklappen"]
        if instance.state == 'down':
            ausklappbutton.disabled = False
            ausklappbutton.opacity = 1
        else:
            if ausklappbutton.state == 'down':
                ausklappbutton.state = 'normal'
                self.game_sprites_ausklappen(ausklappbutton, self.ids["game_sprite_paths"])
            ausklappbutton.disabled = True
            ausklappbutton.opacity = 0
        if not initializing:
            self.save_changes(instance)

    def obs_ausklapp_button_zeigen_oder_verstecken(self, instance, initializing=False):
        ausklappbutton_obs = self.ids["obs_games_ausklappen"]
        if instance.state == 'down':
            ausklappbutton_obs.disabled = False
            ausklappbutton_obs.opacity = 1
        else:
            ausklappbutton_obs.disabled = True
            ausklappbutton_obs.opacity = 0
            if ausklappbutton_obs.state == "down":
                self.obs_sprites_ausklappen(ausklappbutton_obs, self.ids["obs_sprites_box"])

    def obs_2_pcs_setup(self, instance, initializing=False):
        float_box = self.ids["obs_sprites_box"]
        if (instance.state == 'down') or (self.sp['obs_2_pc'] and initializing):

            UI.create_text_and_browse_button(float_box,self.ids,
                                    box_id_name='common_obs_path_box', 
                                    label_text='Dateipfad Sprites OBS',
                                    text_id_name="common_obs_path", text_validate_function=None,
                                    browse_function=self.browse)

            UI.create_text_and_browse_button(float_box,self.ids,
                                    box_id_name='items_obs_path_box',
                                    label_text='Dateipfad Items OBS',
                                    text_id_name="items_obs_path", text_validate_function=None,
                                    browse_function=self.browse)
            
            UI.create_text_and_browse_button(float_box,self.ids,
                                    box_id_name='badges_obs_path_box',
                                    label_text='Dateipfad Orden OBS',
                                    text_id_name="badges_obs_path", text_validate_function=None,
                                    browse_function=self.browse)

            self.ids.common_obs_path.text = self.sp['common_path']
            self.ids.items_obs_path.text = self.sp['items_path']
            self.ids.badges_obs_path.text = self.sp['badges_path']

        else:
            children = float_box.children.copy()
            for child in children:
                float_box.remove_widget(child)
            ausklappbutton_obs = self.ids["obs_games_ausklappen"]
            if ausklappbutton_obs.state == 'down':
                ausklappbutton_obs.state = 'normal'
                self.obs_sprites_ausklappen(ausklappbutton_obs, float_box)

        self.obs_ausklapp_button_zeigen_oder_verstecken(instance)

        if not initializing:
            self.save_changes()
    
    def change_player_count(self, player_count, player_box):
        self.pl["player_count"] = player_count
        if self.ids["player_settings_ausklappen"].state =='down':
            player_box.remove_widget(self.ids["player_settings"])
            self.add_player_checkBoxes(player_box)
        
        self.save_changes()
    
    def player_ausklappen(self, instance, player_box):
        if instance.state == "down":
            instance.text = "^"
            self.add_player_checkBoxes(player_box)
        else:
            instance.text = ">"
            player = self.ids["player_settings"]
            player_box.remove_widget(player)
    
    def add_player_checkBoxes(self, player_box, begin=1):
        player_settings_box = BoxLayout(orientation='vertical',size_hint_y=None, spacing="20dp")
        player_settings_box.bind(minimum_height=player_settings_box.setter('height')) # type: ignore
        self.ids["player_settings"] = weakref.proxy(player_settings_box)
        for i in range(begin, self.pl['player_count'] + 1):
            idBox = f"box_spieler_{i}"
            box = BoxLayout(orientation="horizontal",size_hint_y=None, size=(0,"30dp"))
            self.ids[idBox] = weakref.proxy(box)

            idLabel = f"label_spieler_{i}"
            label = Label(text=f"Spieler {i}", size_hint=(.4,1))
            self.ids[idLabel] = weakref.proxy(label)

            box.add_widget(label)

            idRemote = f"remote_player_{i}"
            idRemoteLabel = f"remote_label_{i}"
            idOBS = f"obs_player_{i}"
            idOBSLabel = f"obs_label_{i}"

            UI.create_label_and_checkboxes(box, self.ids, 
                                            checkbox_id_name=idRemote,checkbox_on_press=self.check_player_for_citra, checkbox_active=self.pl[f"remote_{i}"],
                                            checkbox_pos_hint={'center_x':.5, 'center_y':.5},
                                            label_id_name=idRemoteLabel, label_text="remote")

            UI.create_label_and_checkboxes(box, self.ids, 
                                            checkbox_id_name=idOBS,
                                            # checkbox_on_press=self.toggle_obs,
                                            checkbox_active=self.pl[f"obs_{i}"], checkbox_disabled=not self.pl[f"remote_{i}"],
                                            label_id_name=idOBSLabel, label_text="OBS", label_size=["40dp", "20dp"])
            self.ids[idRemote].ids[idOBS] = self.ids[idOBS]
            self.ids[idRemoteLabel].ids[idOBSLabel] = self.ids[idOBSLabel]

            player_settings_box.add_widget(box)
        
        player_box.add_widget(player_settings_box)
        self.pressCheckBoxes()

    def pressCheckBoxes(self):
        for i in range(1, self.pl['player_count'] + 1):
            if self.pl[f"remote_{i}"]:
                self.ids[f"remote_player_{i}"].state = "down"
            if self.pl[f"obs_{i}"]:
                self.ids[f"obs_player_{i}"].state = "down"
    
    def toggle_obs(self, widgets):
        for obs in widgets.ids:
            ObsCheckBox = widgets.ids[obs]
            ObsCheckBox.disabled = widgets.state != 'down'
            if 'state' in dir(ObsCheckBox):
                ObsCheckBox.state = 'normal'
        self.save_changes()

    def check_player_for_citra(self, widget):
        if self.munchlax.pl["session_game"] in ['X','Y','Omega Rubin','Alpha Saphir','Sonne', 'Mond','Ultra Sonne', 'Ultra Mond']:
            for i in range(1, self.pl['player_count'] + 1):
                remote_button = self.ids[f"remote_player_{i}"]
                if remote_button.state != "down" and remote_button != widget:
                    remote_button.state = 'down'
                    self.toggle_obs(remote_button)
        self.toggle_obs(widget)

    def hide_extras(self):
        buttons = [self.ids["player_settings_ausklappen"], self.ids["games_ausklappen"], self.ids["obs_games_ausklappen"]]
        check_button = self.ids["obs_sprites_check"]
        if (check_button.state == 'down' and not self.ids["obs_sprites_box"].children) or (check_button.state != 'down' and self.ids["obs_sprites_box"].children):
            self.obs_2_pcs_setup(check_button)
        for button in buttons:
            was_disabled = button.disabled
            if button.state == 'down':
                button.disabled = False
                button.trigger_action(0)
                button.state = 'normal'
                button.disabled = was_disabled 

    def clipboard(self, instance, *args):
        result = (instance.text).split(']')[1].split('[')[0]
        Clipboard.copy(result)
        box = BoxLayout(orientation='vertical')
        box.add_widget(Label(text=result))
        btn = Button(text='OK',size_hint=(.5,.4),pos_hint={'center_x':.5})
        box.add_widget(btn)

        popup = Popup(title='Kopiervorgang erfolgreich', content=box, size_hint=(None, None), size=(400, 150))

        btn.bind(on_press=popup.dismiss)
        popup.open()

    def browse(self, widget, modus):
        if modus == 'file':
            path = fd.askopenfilename()
        else:
            path = fd.askdirectory()
        if path:
            if self.ids["games_ausklappen"].state == 'down':
                games = [self.ids[id] for text, id in self.games.items()]
                common_path = self.sp['common_path']
            elif self.ids["obs_games_ausklappen"].state == 'down':
                games = [self.ids[f"{id}_obs"] for text, id in self.games.items()]
                common_path = self.sp['common_obs_path']
            else:
                games=[]
                common_path = self.sp['common_path']
            stripped_path = path.replace(common_path, "", 1) if widget in games else path
            widget.text = stripped_path
            self.save_changes()
    
    def load_config(self):
        self.ids.common_path.text = self.sp['common_path']
        self.ids.items_path.text = self.sp['items_path']
        self.ids.badges_path.text = self.sp['badges_path']
        self.ids.game_sprites_check.state = 'down' if not self.sp['single_path_check'] else 'normal'
        self.ids.obs_sprites_check.state = 'down' if self.sp['obs_2_pc'] else 'normal'

        if self.sp['obs_2_pc']:
            self.ids.common_obs_path.text = self.sp['common_obs_path']
            self.ids.items_obs_path.text = self.sp['items_obs_path']
            self.ids.badges_obs_path.text = self.sp['badges_obs_path']

        self.ausklapp_button_zeigen_oder_verstecken(self.ids.game_sprites_check, initializing=True)
        self.obs_ausklapp_button_zeigen_oder_verstecken(self.ids.obs_sprites_check, initializing=True)

        self.ids.bizhawk_exe.text = self.bh['path']
        self.ids.bizhawk_port.text = self.bh['port']

        self.ids["obs_password"].text = self.obs['password']
        self.ids["obs_host"].text = self.obs['host']
        self.ids["obs_port"].text = self.obs['port']

        self.ids["ip_server"].text = self.rem[f'server_ip_adresse']
        self.ids["port_client"].text = self.rem[f'client_port']
        self.ids['port_server'].text = self.rem[f'server_port']

        self.ids["your_name"].text = self.pl.get('your_name', '')
        self.ids["session_game"].text = self.pl.get('session_game', '')
        self.ids[f"player_count_{self.pl['player_count']}"].state = "down"

    def load_game_sprites_config(self):
        self.ids.gen1_red.text = self.sp['red']
        self.ids.gen1_yellow.text = self.sp['yellow']
        self.ids.gen2_silver.text = self.sp['silver']
        self.ids.gen2_gold.text = self.sp['gold']
        self.ids.gen2_crystal.text = self.sp['crystal']
        self.ids.gen3_ruby.text = self.sp['ruby']
        self.ids.gen3_emerald.text = self.sp['emerald']
        self.ids.gen3_firered.text = self.sp['firered']
        self.ids.gen4_diamond.text = self.sp['diamond']
        self.ids.gen4_platinum.text = self.sp['platinum']
        self.ids.gen4_heartgold.text = self.sp['heartgold']
        self.ids.gen5_black.text = self.sp['black']
        self.ids.gen6_x.text = self.sp['x']
        self.ids.gen6_alphasapphire.text = self.sp['alphasapphire']
        self.ids.gen7_sun.text = self.sp['sun']
        self.ids.gen7_usun.text = self.sp['usun']

    def load_obs_sprites_config(self):
        self.ids.gen1_red_obs.text = self.sp['red_obs']
        self.ids.gen1_yellow_obs.text = self.sp['yellow_obs']
        self.ids.gen2_silver_obs.text = self.sp['silver_obs']
        self.ids.gen2_gold_obs.text = self.sp['gold_obs']
        self.ids.gen2_crystal_obs.text = self.sp['crystal_obs']
        self.ids.gen3_ruby_obs.text = self.sp['ruby_obs']
        self.ids.gen3_emerald_obs.text = self.sp['emerald_obs']
        self.ids.gen3_firered_obs.text = self.sp['firered_obs']
        self.ids.gen4_diamond_obs.text = self.sp['diamond_obs']
        self.ids.gen4_platinum_obs.text = self.sp['platinum_obs']
        self.ids.gen4_heartgold_obs.text = self.sp['heartgold_obs']
        self.ids.gen5_black_obs.text = self.sp['black_obs']
        self.ids.gen6_x_obs.text = self.sp['x_obs']
        self.ids.gen6_alphasapphire_obs.text = self.sp['alphasapphire_obs']
        self.ids.gen7_sun_obs.text = self.sp['sun_obs']
        self.ids.gen7_usun_obs.text = self.sp['usun_obs']

    def save_changes(self, *args):
        self.sp['common_path'] = self.ids.common_path.text
        self.sp['single_path_check'] = not self.ids.game_sprites_check.state == 'down'
        self.sp['items_path'] = self.ids.items_path.text
        self.sp['badges_path'] = self.ids.badges_path.text
        self.sp['obs_2_pc'] = self.ids.obs_sprites_check.state == 'down'
        if self.sp['obs_2_pc']:
            self.sp['common_obs_path'] = self.ids.common_obs_path.text
            self.sp['items_obs_path'] = self.ids.items_obs_path.text
            self.sp['badges_obs_path'] = self.ids.badges_obs_path.text

        if self.ids["games_ausklappen"].state == 'down':
            self.sp['red'] = self.ids.gen1_red.text
            self.sp['yellow'] = self.ids.gen1_yellow.text
            self.sp['silver'] = self.ids.gen2_silver.text
            self.sp['gold'] = self.ids.gen2_gold.text
            self.sp['crystal'] = self.ids.gen2_crystal.text
            self.sp['ruby'] = self.ids.gen3_ruby.text
            self.sp['emerald'] = self.ids.gen3_emerald.text
            self.sp['firered'] = self.ids.gen3_firered.text
            self.sp['diamond'] = self.ids.gen4_diamond.text
            self.sp['platinum'] = self.ids.gen4_platinum.text
            self.sp['heartgold'] = self.ids.gen4_heartgold.text
            self.sp['black'] = self.ids.gen5_black.text
            self.sp['x'] = self.ids.gen6_x.text
            self.sp['alphasapphire'] = self.ids.gen6_alphasapphire.text
            self.sp['sun'] = self.ids.gen7_sun.text
            self.sp['usun'] = self.ids.gen7_usun.text

        if self.ids["obs_games_ausklappen"].state == 'down':
            self.sp['red_obs'] = self.ids.gen1_red_obs.text
            self.sp['yellow_obs'] = self.ids.gen1_yellow_obs.text
            self.sp['silver_obs'] = self.ids.gen2_silver_obs.text
            self.sp['gold_obs'] = self.ids.gen2_gold_obs.text
            self.sp['crystal_obs'] = self.ids.gen2_crystal_obs.text
            self.sp['ruby_obs'] = self.ids.gen3_ruby_obs.text
            self.sp['emerald_obs'] = self.ids.gen3_emerald_obs.text
            self.sp['firered_obs'] = self.ids.gen3_firered_obs.text
            self.sp['diamond_obs'] = self.ids.gen4_diamond_obs.text
            self.sp['platinum_obs'] = self.ids.gen4_platinum_obs.text
            self.sp['heartgold_obs'] = self.ids.gen4_heartgold_obs.text
            self.sp['black_obs'] = self.ids.gen5_black_obs.text
            self.sp['x_obs'] = self.ids.gen6_x_obs.text
            self.sp['alphasapphire_obs'] = self.ids.gen6_alphasapphire_obs.text
            self.sp['sun_obs'] = self.ids.gen7_sun_obs.text
            self.sp['usun_obs'] = self.ids.gen7_usun_obs.text
        
        asyncio.create_task(self.obs_websocket.redraw_obs())

        with open(f"{self.configsave}sprites.yml", 'w') as file:
            yaml.dump(self.sp, file)

        self.bh['path'] = self.ids.bizhawk_exe.text
        self.bh['port'] = self.ids.bizhawk_port.text

        self.update_bizhawk()
        
        with open(f"{self.configsave}bh_config.yml", 'w') as file:
            yaml.dump(self.bh, file)

        self.obs['password'] = self.ids["obs_password"].text
        self.obs['host'] = self.ids["obs_host"].text
        self.obs['port'] = self.ids["obs_port"].text

        self.update_obs_websocket()
        
        with open(f"{self.configsave}obs_config.yml", 'w') as file:
            yaml.dump(self.obs, file)

        self.rem['client_port'] = self.ids['port_client'].text

        self.rem['server_ip_adresse'] = self.ids['ip_server'].text
        self.rem['server_port'] = self.ids['port_server'].text

        self.update_arceus()

        self.update_munchlax()

        with open(f"{self.configsave}remote.yml", 'w') as file:
            yaml.dump(self.rem, file)

        self.pl["your_name"] = self.ids["your_name"].text

        if self.ids["player_settings_ausklappen"].state == 'down':
            for i in range(1, self.pl['player_count'] + 1):
                self.pl[f"remote_{i}"] = self.ids[f"remote_player_{i}"].state == "down"
                self.pl[f"obs_{i}"] = self.ids[f"obs_player_{i}"].state == "down"

        with open(f"{self.configsave}player.yml", 'w') as file:
            yaml.dump(self.pl, file)

        main_menu = self.settingsscreen.manager.get_screen("MainMenu")
        main_menu.update_munchlax_connection_circle()

        # self.update_trainer_boxes()

    def update_bizhawk(self):
        if not self.bizhawk.server:
            self.bizhawk.port = self.bh['port']

    def update_obs_websocket(self):
        if not self.obs_websocket.ws:
            self.obs_websocket.password = self.obs['password']
            self.obs_websocket.host = self.obs['host']
            self.obs_websocket.port = self.obs['port']

    def update_arceus(self):
        if not self.arceus.server:
            self.arceus.port = self.rem['client_port']

    def update_munchlax(self):
        if not self.munchlax.is_connected:
            self.munchlax.host = '127.0.0.1' if self.rem["start_server"] else self.rem["server_ip_adresse"]
            self.munchlax.port = self.rem["client_port"] if self.rem["start_server"] else self.rem["server_port"]

    def update_connections(self):
        self.update_bizhawk()
        self.update_obs_websocket()
        self.update_arceus()
        self.update_munchlax()

    def update_trainer_boxes(self):
        main_menu = self.settingsscreen.manager.get_screen("MainMenu")
        trainer_box_box = main_menu.ids["trainer_box_1"].parent

        for player in range(2,5):
            if f"trainer_box_{player}" in main_menu.ids:
                trainer_box_box.remove_widget(main_menu.ids[f"trainer_box_{player}"])
                del main_menu.ids[f"trainer_box_{player}"]

        color_lut = {1:"1a4d9a7f", 2:"9a671a7f", 3:"9a9a1a7f", 4:"1a9a1a7f"}

        for player in range(2,self.pl['player_count'] + 1):
            trainer = TrainerBox(player, self.munchlax, self.obs_websocket, main_menu, color_lut[player], orientation='vertical', size_hint=(None, 1), size=("140dp",0))
            trainer_box_box.add_widget(trainer)
