import asyncio
import os
import weakref
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
from backend.controller.settings_controller import SettingsController
from backend.sprite_repo import clone_sprite_repo, pull_sprite_repo, is_sprite_repo, get_repo_root_from_subpath, apply_sprite_paths
from frontend.widgets.mainmenu import TrainerBox
from frontend.widgets.sprite_setup_popup import SpriteSetupPopup
import frontend.UIFactory as UI
import tkinter.filedialog as fd
from backend.logging_setup import get_logger

logger = get_logger(__name__, 'logs/frontend.log')

class SettingsMenu(Screen):
    def __init__(self, arceus, bizhawk, munchlax, obs_websocket, externalIPv4, externalIPv6, configsave, sp, rem, obs, bh, pl, rnd, app_version, **kwargs):
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
        self.scrollview = ScrollSettings(self, arceus, bizhawk, munchlax, obs_websocket, externalIPv4, externalIPv6, configsave, sp, rem, obs, bh, pl, rnd)

        settings_buttons = [
            ("Sprite\nPfade", 'sprite'),
            ("Bizhawk", 'bizhawk'),
            ("OBS", 'obs'),
            ("Remote", 'remote'),
            ("Spieler", 'player'),
            ("Randomizer", 'randomizer'),
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
        elif jump_id in ("player", "randomizer"):
            scrolling = 0
        else:
            scrolling = new_scrollheight / scroll_max_height

        scrollview.scroll_y = scrolling

class ScrollSettings(ScrollView):
    def __init__(self, settingsscreen, arceus, bizhawk, munchlax, obs_websocket, externalIPv4, externalIPv6, configsave, sp, rem, obs, bh, pl, rnd, **kwargs):
        super().__init__(**kwargs)

        self.rnd = rnd
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

        self.controller = SettingsController(configsave, sp, rem, obs, bh, pl, rnd, arceus, bizhawk, munchlax, obs_websocket)

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

        sprite_repo_buttons = BoxLayout(orientation='horizontal', size_hint=(None, None),
            size=("420dp", "30dp"), pos_hint={"center_x": .5}, spacing="20dp")
        btn_download_sprites = Button(text="Sprites herunterladen", size_hint=(1, 1),
            on_press=lambda inst: self.download_sprites())
        sprite_repo_buttons.add_widget(btn_download_sprites)
        btn_update_sprites = Button(text="Sprites aktualisieren", size_hint=(1, 1),
            on_press=lambda inst: self.update_sprites())
        sprite_repo_buttons.add_widget(btn_update_sprites)
        sprite_box.add_widget(sprite_repo_buttons)

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

        obs_paket_button = Button(text="OBS-PC Paket erstellen", size_hint=(None, None),
            size=("200dp", "30dp"), pos_hint={"center_x": .5},
            on_press=lambda inst: self.create_obs_helper_package())
        sprite_box.add_widget(obs_paket_button)

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

        UI.create_label_and_Textbox(remote_box, self.ids,
                            label_text='Helper-Port\n(OBS-PC)', text_size_hint=(.1,1), is_port=True,
                            text_box_id='helper_port',text_validate_function=self.save_changes)

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

        randomizer_box = BoxLayout(orientation='vertical', size_hint_y=None, spacing="20dp")
        randomizer_box.bind(minimum_height=randomizer_box.setter('height'))  # type: ignore
        self.ids["randomizer"] = weakref.proxy(randomizer_box)

        ueberschrift_randomizer = Label(text="Randomizer", size_hint=(1, None), size=(0, "20dp"), font_size="20sp")
        randomizer_box.add_widget(ueberschrift_randomizer)

        UI.create_text_and_browse_button(randomizer_box, self.ids,
            box_id_name='jar_path_box', label_text='Pfad zur\nPokeRandoZX.jar',
            text_id_name="jar_path", text_validate_function=None,
            browse_function=self.browse, browse_modus='file')

        UI.create_text_and_browse_button(randomizer_box, self.ids,
            box_id_name='java_path_box', label_text='Java-Pfad\n(leer = auto)',
            text_id_name="java_path", text_validate_function=None,
            browse_function=self.browse, browse_modus='file')

        UI.create_text_and_browse_button(randomizer_box, self.ids,
            box_id_name='settings_rnqs_path_box', label_text='Einstellungsdatei\n(.rnqs)',
            text_id_name="settings_rnqs_path", text_validate_function=None,
            browse_function=self.browse, browse_modus='file')

        rnd_button_row = BoxLayout(orientation='horizontal', size_hint=(None, None),
            size=("420dp", "30dp"), pos_hint={"center_x": .5}, spacing="20dp")
        open_gui_button = Button(text="Randomizer-GUI öffnen", size_hint=(1, 1),
            on_press=lambda inst: self.open_randomizer_gui())
        rnd_button_row.add_widget(open_gui_button)
        import_log_button = Button(text="Log importieren", size_hint=(1, 1),
            on_press=lambda inst: self.import_randomizer_log())
        rnd_button_row.add_widget(import_log_button)
        randomizer_box.add_widget(rnd_button_row)

        UI.create_text_and_browse_button(randomizer_box, self.ids,
            box_id_name='rom_path_box', label_text='ROM-Datei',
            text_id_name="rom_path", text_validate_function=None,
            browse_function=self.browse, browse_modus='file')

        UI.create_text_and_browse_button(randomizer_box, self.ids,
            box_id_name='output_path_box', label_text='Ausgabe-Ordner\n(leer = ROM-Ordner)',
            text_id_name="output_path", text_validate_function=None,
            browse_function=self.browse)

        box.add_widget(randomizer_box)

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

    def open_randomizer_gui(self):
        from backend.controller.randomizer_controller import RandomizerController
        self.save_changes()
        rc = RandomizerController(self.rnd, self.pl)
        success, error = rc.open_gui()
        if not success:
            box = BoxLayout(orientation='vertical')
            box.add_widget(Label(text=error))
            btn = Button(text='OK', size_hint=(.5, .4), pos_hint={'center_x': .5})
            box.add_widget(btn)
            popup = Popup(title='Fehler', content=box, size_hint=(None, None), size=(500, 200))
            btn.bind(on_release=popup.dismiss)
            popup.open()

    def import_randomizer_log(self):
        log_path = fd.askopenfilename(
            title="Randomizer-Log importieren",
            filetypes=[("Log-Dateien", "*.log"), ("Alle Dateien", "*.*")]
        )
        if not log_path:
            return
        main_menu = self.settingsscreen.manager.get_screen("MainMenu")
        success, msg = main_menu.randomizer.parse_log(log_path)
        if success:
            main_menu._sync_rando_to_munchlax()
        box = BoxLayout(orientation='vertical')
        box.add_widget(Label(text=msg))
        btn = Button(text='OK', size_hint=(.5, .4), pos_hint={'center_x': .5})
        box.add_widget(btn)
        title = 'Log importiert' if success else 'Fehler'
        popup = Popup(title=title, content=box, size_hint=(None, None), size=(500, 200))
        btn.bind(on_release=popup.dismiss)
        popup.open()

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

    def create_obs_helper_package(self):
        import shutil
        import yaml as _yaml
        target = fd.askdirectory(title="Zielordner für OBS-PC Paket wählen")
        if not target:
            return

        helper_dir = os.path.join(target, "sprite_helper")
        os.makedirs(helper_dir, exist_ok=True)

        helper_exe_source = os.path.join("utils", "sprite_helper.exe")
        pull_exe_source = os.path.join("utils", "pull_task.exe")

        copied_files = []
        for src in (helper_exe_source, pull_exe_source):
            if os.path.exists(src):
                shutil.copy2(src, helper_dir)
                copied_files.append(os.path.basename(src))

        if not copied_files:
            box = BoxLayout(orientation='vertical')
            box.add_widget(Label(text="Helper-EXE nicht gefunden unter utils/.\nBitte zuerst kompilieren."))
            btn = Button(text='OK', size_hint=(.5, .4), pos_hint={'center_x': .5})
            box.add_widget(btn)
            popup = Popup(title='Fehler', content=box, size_hint=(None, None), size=(500, 200))
            btn.bind(on_release=popup.dismiss)
            popup.open()
            return

        helper_port = int(self.arceus.rem.get('helper_port', int(self.arceus.port) + 1))
        config = {
            "repo_url": "https://github.com/agent0816/sprites.git",
            "tracker_ip": self.externalIPv4,
            "tracker_port": helper_port,
        }
        config_path = os.path.join(helper_dir, "config.yml")
        with open(config_path, 'w') as f:
            _yaml.dump(config, f)

        box = BoxLayout(orientation='vertical')
        box.add_widget(Label(text=f"Paket erstellt in:\n{helper_dir}\n\nDateien: {', '.join(copied_files)}, config.yml"))
        btn = Button(text='OK', size_hint=(.5, .4), pos_hint={'center_x': .5})
        box.add_widget(btn)
        popup = Popup(title='OBS-PC Paket erstellt', content=box, size_hint=(None, None), size=(500, 200))
        btn.bind(on_release=popup.dismiss)
        popup.open()

    def download_sprites(self):
        popup = SpriteSetupPopup(
            sp=self.sp,
            configsave=self.configsave,
            save_callback=self._after_sprite_download
        )
        popup.open()

    def _after_sprite_download(self):
        self.ids.common_path.text = self.sp['common_path']
        self.ids.items_path.text = self.sp['items_path']
        self.ids.badges_path.text = self.sp['badges_path']
        if self.sp.get('obs_2_pc'):
            self.ids.common_obs_path.text = self.sp['common_obs_path']
            self.ids.items_obs_path.text = self.sp['items_obs_path']
            self.ids.badges_obs_path.text = self.sp['badges_obs_path']
        self.save_changes()

    def update_sprites(self):
        repo_root = get_repo_root_from_subpath(self.sp.get('common_path', ''))
        if not repo_root or not is_sprite_repo(repo_root):
            box = BoxLayout(orientation='vertical')
            box.add_widget(Label(text="Kein Sprite-Repository gefunden.\nBitte zuerst herunterladen."))
            btn = Button(text='OK', size_hint=(.5, .4), pos_hint={'center_x': .5})
            box.add_widget(btn)
            popup = Popup(title='Fehler', content=box, size_hint=(None, None), size=(500, 200))
            btn.bind(on_release=popup.dismiss)
            popup.open()
            return
        asyncio.create_task(self._do_update_sprites(repo_root))

    async def _do_update_sprites(self, repo_root: str):
        loop = asyncio.get_event_loop()
        success = await loop.run_in_executor(None, pull_sprite_repo, repo_root)
        box = BoxLayout(orientation='vertical')
        if success:
            msg = "Sprites erfolgreich aktualisiert!"
            title = "Aktualisierung erfolgreich"
        else:
            msg = "Fehler beim Aktualisieren. Siehe Log."
            title = "Fehler"
        box.add_widget(Label(text=msg))
        btn = Button(text='OK', size_hint=(.5, .4), pos_hint={'center_x': .5})
        box.add_widget(btn)
        popup = Popup(title=title, content=box, size_hint=(None, None), size=(500, 200))
        btn.bind(on_release=popup.dismiss)
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
        sp = self.controller.load_sprites()
        self.ids.common_path.text = sp['common_path']
        self.ids.items_path.text = sp['items_path']
        self.ids.badges_path.text = sp['badges_path']
        self.ids.game_sprites_check.state = 'down' if not sp['single_path_check'] else 'normal'
        self.ids.obs_sprites_check.state = 'down' if sp['obs_2_pc'] else 'normal'

        if sp['obs_2_pc']:
            self.ids.common_obs_path.text = sp['common_obs_path']
            self.ids.items_obs_path.text = sp['items_obs_path']
            self.ids.badges_obs_path.text = sp['badges_obs_path']

        self.ausklapp_button_zeigen_oder_verstecken(self.ids.game_sprites_check, initializing=True)
        self.obs_ausklapp_button_zeigen_oder_verstecken(self.ids.obs_sprites_check, initializing=True)

        bh = self.controller.load_bizhawk()
        self.ids.bizhawk_exe.text = bh['path']
        self.ids.bizhawk_port.text = bh['port']

        obs = self.controller.load_obs()
        self.ids["obs_password"].text = obs['password']
        self.ids["obs_host"].text = obs['host']
        self.ids["obs_port"].text = obs['port']

        rem = self.controller.load_remote()
        self.ids["ip_server"].text = rem['server_ip_adresse']
        self.ids["port_client"].text = rem['client_port']
        self.ids["helper_port"].text = rem.get('helper_port', '43887')
        self.ids['port_server'].text = rem['server_port']

        pl = self.controller.load_player()
        self.ids["your_name"].text = pl.get('your_name', '')
        self.ids["session_game"].text = pl.get('session_game', '')
        self.ids[f"player_count_{pl['player_count']}"].state = "down"

        rnd = self.controller.load_randomizer()
        self.ids.jar_path.text = rnd.get('jar_path', '')
        self.ids.java_path.text = rnd.get('java_path', '')
        self.ids.settings_rnqs_path.text = rnd.get('settings_path', '')
        self.ids.rom_path.text = rnd.get('rom_path', '')
        self.ids.output_path.text = rnd.get('output_path', '')

    def load_game_sprites_config(self):
        sp = self.controller.load_sprites()
        for _, game_id in self.games.items():
            sp_key = game_id.split('_', 1)[1]  # z.B. 'gen3_firered' → 'firered'
            self.ids[game_id].text = sp[sp_key]

    def load_obs_sprites_config(self):
        sp = self.controller.load_sprites()
        for _, game_id in self.games.items():
            sp_key = game_id.split('_', 1)[1] + '_obs'  # z.B. 'gen3_firered' → 'firered_obs'
            self.ids[f"{game_id}_obs"].text = sp[sp_key]

    def save_changes(self, *args):
        # Sprite-Einstellungen sammeln
        sprite_values = {
            'common_path': self.ids.common_path.text,
            'items_path': self.ids.items_path.text,
            'badges_path': self.ids.badges_path.text,
            'single_path_check': not self.ids.game_sprites_check.state == 'down',
            'obs_2_pc': self.ids.obs_sprites_check.state == 'down',
        }
        if sprite_values['obs_2_pc']:
            sprite_values['common_obs_path'] = self.ids.common_obs_path.text
            sprite_values['items_obs_path'] = self.ids.items_obs_path.text
            sprite_values['badges_obs_path'] = self.ids.badges_obs_path.text

        if self.ids["games_ausklappen"].state == 'down':
            for _, game_id in self.games.items():
                sp_key = game_id.split('_', 1)[1]  # z.B. 'gen3_firered' → 'firered'
                sprite_values[sp_key] = self.ids[game_id].text

        if self.ids["obs_games_ausklappen"].state == 'down':
            for _, game_id in self.games.items():
                sp_key = game_id.split('_', 1)[1] + '_obs'  # z.B. 'gen3_firered' → 'firered_obs'
                sprite_values[sp_key] = self.ids[f"{game_id}_obs"].text

        self.controller.save_sprites(sprite_values)

        # BizHawk-Einstellungen sammeln
        self.controller.save_bizhawk({
            'path': self.ids.bizhawk_exe.text,
            'port': self.ids.bizhawk_port.text,
        })

        # OBS-Einstellungen sammeln
        self.controller.save_obs({
            'password': self.ids["obs_password"].text,
            'host': self.ids["obs_host"].text,
            'port': self.ids["obs_port"].text,
        })

        # Remote-Einstellungen sammeln
        self.controller.save_remote({
            'client_port': self.ids['port_client'].text,
            'helper_port': self.ids['helper_port'].text,
            'server_ip_adresse': self.ids['ip_server'].text,
            'server_port': self.ids['port_server'].text,
        })

        # Spieler-Einstellungen sammeln
        player_values = {'your_name': self.ids["your_name"].text}
        if self.ids["player_settings_ausklappen"].state == 'down':
            for i in range(1, self.pl['player_count'] + 1):
                player_values[f"remote_{i}"] = self.ids[f"remote_player_{i}"].state == "down"
                player_values[f"obs_{i}"] = self.ids[f"obs_player_{i}"].state == "down"
        self.controller.save_player(player_values)

        # Randomizer-Einstellungen sammeln
        self.controller.save_randomizer({
            'jar_path': self.ids.jar_path.text,
            'java_path': self.ids.java_path.text,
            'settings_path': self.ids.settings_rnqs_path.text,
            'rom_path': self.ids.rom_path.text,
            'output_path': self.ids.output_path.text,
        })

        # UI-Aktualisierungen (bleiben in der View)
        main_menu = self.settingsscreen.manager.get_screen("MainMenu")
        main_menu.update_munchlax_connection_circle()
        self.update_trainer_boxes()

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
