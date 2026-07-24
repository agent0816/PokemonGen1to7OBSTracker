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
from kivy.uix.spinner import Spinner
from kivy.uix.togglebutton import ToggleButton
from backend.classes.obs import OBS
from backend.classes.pokedex_db import PokedexDB
from backend.controller.settings_controller import SettingsController
from backend.sprite_repo import clone_sprite_repo, pull_sprite_repo, is_sprite_repo, get_repo_root_from_subpath, apply_sprite_paths
from backend.team_id import slug_team_id
from frontend.widgets.mainmenu import TrainerBox
from frontend.widgets.sprite_setup_popup import SpriteSetupPopup
import frontend.UIFactory as UI
import tkinter.filedialog as fd
from backend.logging_setup import get_logger, set_console_level, get_console_level

logger = get_logger(__name__, 'logs/frontend.log')

class SettingsMenu(Screen):
    def __init__(self, arceus, bizhawk, munchlax, obs_websocket, overlay_server, externalIPv4, externalIPv6, configsave, sp, rem, obs, bh, pl, rnd, ov, nuz, app_version, **kwargs):
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
        self.scrollview = ScrollSettings(self, arceus, bizhawk, munchlax, obs_websocket, overlay_server, externalIPv4, externalIPv6, configsave, sp, rem, obs, bh, pl, rnd, ov, nuz)

        settings_buttons = [
            ("Sprite\nPfade", 'sprite'),
            ("Bizhawk", 'bizhawk'),
            ("OBS", 'obs'),
            ("Browser\nOverlay", 'overlay'),
            ("Remote", 'remote'),
            ("Spieler", 'player'),
            ("Randomizer", 'randomizer'),
            ("Nuzlocke", 'nuzlocke'),
            ("Logging", 'logging'),
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

    def on_enter(self, *args):
        # Run-Historie beim Öffnen des Settings-Screens neu einlesen.
        try:
            self.scrollview._refresh_run_history()
        except Exception as err:
            logger.debug(f"refresh_run_history on_enter failed: {err}")

    def jump_to(self, scrollview, jump_id):
        scroll_max_height = scrollview.children[0].height
        new_scrollheight = scrollview.ids[jump_id].y
        
        if jump_id == "sprite":
            scrolling = 1
        elif jump_id in ("player", "randomizer", "nuzlocke", "logging"):
            scrolling = 0
        else:
            scrolling = new_scrollheight / scroll_max_height

        scrollview.scroll_y = scrolling

class ScrollSettings(ScrollView):
    def __init__(self, settingsscreen, arceus, bizhawk, munchlax, obs_websocket, overlay_server, externalIPv4, externalIPv6, configsave, sp, rem, obs, bh, pl, rnd, ov, nuz, **kwargs):
        super().__init__(**kwargs)

        self.rnd = rnd
        self.ov = ov
        self.nuz = nuz
        self.overlay_server = overlay_server
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

        self.controller = SettingsController(configsave, sp, rem, obs, bh, pl, rnd, arceus, bizhawk, munchlax, obs_websocket, ov, overlay_server, nuz)

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

        overlay_box = BoxLayout(orientation='vertical', size_hint_y=None, spacing="20dp")
        overlay_box.bind(minimum_height=overlay_box.setter('height'))
        self.ids["overlay"] = weakref.proxy(overlay_box)

        ueberschrift_overlay = Label(text="Browser Overlay", size_hint=(1, None), size=(0, "20dp"), font_size="20sp")
        overlay_box.add_widget(ueberschrift_overlay)

        UI.create_label_and_Textbox(overlay_box, self.ids,
                            label_text='Port', text_size_hint=(.1, 1), is_port=True,
                            text_box_id='overlay_port', text_validate_function=self.save_changes)

        layout_grid = GridLayout(cols=4, size_hint_y=None, size=(0, "30dp"), padding=("5dp", 0), spacing="5dp")
        layout_grid.add_widget(Label(text="Team-Layout", size_hint_x=.2))
        layout_spinner = Spinner(
            text=self.ov.get('layout', 'horizontal'),
            values=('horizontal', 'vertical', '2x3', '3x2'),
            size_hint_x=.3,
        )
        layout_spinner.bind(text=lambda inst, val: self._on_layout_changed(val))
        self.ids["overlay_layout"] = weakref.proxy(layout_spinner)
        layout_grid.add_widget(layout_spinner)
        layout_grid.add_widget(Label(text="Badge-Layout", size_hint_x=.2))
        badge_layout_spinner = Spinner(
            text=self.ov.get('badge_layout', 'horizontal'),
            values=('horizontal', 'vertical', '2x4', '4x2', '4x4'),
            size_hint_x=.3,
        )
        badge_layout_spinner.bind(text=lambda inst, val: self._on_layout_changed(val))
        self.ids["overlay_badge_layout"] = weakref.proxy(badge_layout_spinner)
        layout_grid.add_widget(badge_layout_spinner)
        overlay_box.add_widget(layout_grid)

        anim_duration_grid = GridLayout(cols=2, size_hint_y=None, size=(0, "30dp"), padding=("5dp", 0), spacing="5dp")
        anim_duration_grid.add_widget(Label(text="Animations-\ndauer (ms)", size_hint_x=.4))
        anim_duration_spinner = Spinner(
            text=str(self.ov.get('animation_duration_ms', 300)),
            values=[str(v) for v in range(300, 801, 100)],
            size_hint_x=.3,
        )
        anim_duration_spinner.bind(text=lambda inst, val: self.save_changes())
        self.ids["animation_duration_ms"] = weakref.proxy(anim_duration_spinner)
        anim_duration_grid.add_widget(anim_duration_spinner)
        overlay_box.add_widget(anim_duration_grid)

        self._build_overlay_buttons()
        box.add_widget(overlay_box)

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

        # Ziel-Server-IP maskiert (potenziell öffentliche IP eines Mitspielers — Stream-Leak-Schutz)
        UI.create_label_and_Textbox(remote_box, self.ids,
                            label_text='IP-Adresse', password=True, reveal_toggle=True,
                            text_box_id='ip_server',text_validate_function=self.save_changes)
        
        UI.create_label_and_Textbox(remote_box, self.ids, 
                            label_text='Port', text_size_hint=(.1,1), is_port=True,
                            text_box_id='port_server',text_validate_function=self.save_changes)
        
        grid=GridLayout(cols=2,size_hint_y=None, spacing="20dp")
        grid.bind(minimum_height=grid.setter('height')) #type: ignore
        
        grid.add_widget(Label(text="Deine öffentliche\nIpv4-Adresse", size_hint=(.5,None), size=(0,"30dp")))
        grid.add_widget(self._make_masked_ip_widget(self.externalIPv4, ref_id="ip"))
        grid.add_widget(Label(text="Deine öffentliche\nIpv6-Adresse", size_hint=(.5,None), size=(0,"30dp")))
        grid.add_widget(self._make_masked_ip_widget(self.externalIPv6, ref_id="ipv6"))
        
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

        # --- Run-Historie ---
        run_history_header = Label(text="Run-Historie", size_hint=(1, None),
                                    size=(0, "20dp"), font_size="18sp")
        randomizer_box.add_widget(run_history_header)

        active_run_row = BoxLayout(orientation='horizontal', size_hint_y=None,
                                     size=(0, "30dp"), padding=("5dp", 0), spacing="10dp")
        active_run_label = Label(text="(kein aktiver Run)", size_hint_x=.7)
        self.ids["active_run_label"] = weakref.proxy(active_run_label)
        active_run_row.add_widget(active_run_label)
        end_run_button = Button(text="Run manuell beenden", size_hint_x=.3,
                                 on_press=lambda inst: self._end_run_manual())
        end_run_button.disabled = True
        self.ids["end_run_button"] = weakref.proxy(end_run_button)
        active_run_row.add_widget(end_run_button)
        randomizer_box.add_widget(active_run_row)

        run_list_box = BoxLayout(orientation='vertical', size_hint_y=None, spacing="4dp")
        run_list_box.bind(minimum_height=run_list_box.setter('height'))  # type: ignore
        self.ids["run_list_box"] = weakref.proxy(run_list_box)
        randomizer_box.add_widget(run_list_box)

        refresh_row = BoxLayout(orientation='horizontal', size_hint_y=None,
                                  size=(0, "30dp"), padding=("5dp", 0))
        refresh_button = Button(text="Run-Historie aktualisieren", size_hint=(1, 1),
                                 on_press=lambda inst: self._refresh_run_history())
        refresh_row.add_widget(refresh_button)
        randomizer_box.add_widget(refresh_row)

        box.add_widget(randomizer_box)

        logging_box = BoxLayout(orientation='vertical', size_hint_y=None, spacing="20dp")
        logging_box.bind(minimum_height=logging_box.setter('height'))
        self.ids["logging"] = weakref.proxy(logging_box)

        ueberschrift_logging = Label(text="Logging", size_hint=(1, None), size=(0, "20dp"), font_size="20sp")
        logging_box.add_widget(ueberschrift_logging)

        log_level_box = BoxLayout(orientation='horizontal', size_hint_y=None, size=(0, "30dp"), padding=("5dp", 0), spacing="5dp")
        log_level_box.add_widget(Label(text="Konsolen\nLog-Level", size_hint=(.2, 1)))
        log_level_spinner = Spinner(
            text=get_console_level(),
            values=('DEBUG', 'INFO', 'WARNING', 'ERROR'),
            size_hint=(.2, 1),
        )
        log_level_spinner.bind(text=lambda inst, val: set_console_level(val))
        self.ids["log_level"] = weakref.proxy(log_level_spinner)
        log_level_box.add_widget(log_level_spinner)
        log_level_box.add_widget(Label(size_hint_x=.6))
        logging_box.add_widget(log_level_box)

        box.add_widget(logging_box)

        # --- Nuzlocke ---
        nuzlocke_box = BoxLayout(orientation='vertical', size_hint_y=None, spacing="20dp")
        nuzlocke_box.bind(minimum_height=nuzlocke_box.setter('height'))
        self.ids["nuzlocke"] = weakref.proxy(nuzlocke_box)

        ueberschrift_nuzlocke = Label(text="Nuzlocke", size_hint=(1, None), size=(0, "20dp"), font_size="20sp")
        nuzlocke_box.add_widget(ueberschrift_nuzlocke)

        nuzlocke_checks = [
            ("nuz_enabled", "Nuzlocke aktiviert"),
            ("nuz_shiny_clause", "Shiny-Clause"),
            ("nuz_dupes_clause", "Dupes-Clause (inkl. Entwicklungen)"),
            ("nuz_gifts_additional", "Geschenke sind zusätzlich"),
            ("nuz_fossils_repeatable", "Fossile mehrfach einlösbar"),
            ("nuz_static_separate", "Statische Encounters separat"),
        ]
        for check_id, label_text in nuzlocke_checks:
            row = BoxLayout(orientation='horizontal', size_hint_y=None, size=(0, "30dp"), padding=("5dp", 0))
            row.add_widget(Label(text=label_text, size_hint_x=.8))
            cb = CheckBox(size_hint_x=.2)
            self.ids[check_id] = weakref.proxy(cb)
            row.add_widget(cb)
            nuzlocke_box.add_widget(row)

        box.add_widget(nuzlocke_box)

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

            obs_paket_button = Button(text="OBS-PC Paket erstellen", size_hint=(None, None),
                size=("200dp", "30dp"), pos_hint={"center_x": .5},
                on_press=lambda inst: self.create_obs_helper_package())
            self.ids["obs_paket_button"] = weakref.proxy(obs_paket_button)
            float_box.add_widget(obs_paket_button)

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
        rc = RandomizerController(self.rnd, self.pl, configsave=self.configsave)
        success, error = rc.open_gui()
        if not success:
            box = BoxLayout(orientation='vertical')
            box.add_widget(Label(text=error))
            btn = Button(text='OK', size_hint=(.5, .4), pos_hint={'center_x': .5})
            box.add_widget(btn)
            popup = Popup(title='Fehler', content=box, size_hint=(None, None), size=(500, 200))
            btn.bind(on_release=popup.dismiss)
            popup.open()

    def _get_run_manager(self):
        from backend.controller.run_manager import RunManager
        if self.configsave is None:
            return None
        try:
            # str() nötig: configsave ist MutableString (frontend.app), Path()
            # akzeptiert das nicht direkt. Kein Caching — Session-Wechsel
            # mutiert configsave in-place.
            return RunManager(str(self.configsave))
        except Exception as err:
            logger.error(f"RunManager-Init im Settings-UI fehlgeschlagen: {err}")
            return None

    def _refresh_run_history(self):
        rm = self._get_run_manager()
        run_list_box = self.ids.get("run_list_box")
        active_label = self.ids.get("active_run_label")
        end_button = self.ids.get("end_run_button")
        if run_list_box is None or active_label is None or end_button is None:
            return
        run_list_box.clear_widgets()
        if rm is None:
            active_label.text = "(Session-Pfad fehlt)"
            end_button.disabled = True
            return

        active = rm.get_active_run()
        if active is None:
            active_label.text = "(kein aktiver Run)"
            end_button.disabled = True
        else:
            slots = [str(r.get("player_slot")) for r in (active.get("roms") or [])]
            active_label.text = (
                f"Aktiv: {active.get('run_id', '?')} — "
                f"Slots {','.join(slots) or '-'} — seit {active.get('start_ts', '?')}"
            )
            end_button.disabled = False

        runs = rm.list_runs()
        # Neueste zuerst
        for entry in reversed(runs):
            run_list_box.add_widget(self._build_run_row(entry))

    def _build_run_row(self, entry: dict):
        row = BoxLayout(orientation='vertical', size_hint_y=None, spacing="2dp",
                         padding=("5dp", "4dp"))
        row.bind(minimum_height=row.setter('height'))  # type: ignore

        run_id = entry.get("run_id", "?")
        idx = entry.get("run_index", "?")
        reason = entry.get("end_reason") or "(aktiv)"
        start = entry.get("start_ts", "?")
        end = entry.get("end_ts") or "-"
        dur = entry.get("duration_seconds")
        dur_txt = f"{dur}s" if isinstance(dur, int) else "-"
        wipe = entry.get("team_wipe") or {}
        wipe_txt = f" wipe={wipe.get('player_id')}" if wipe.get("player_id") else ""

        header = Label(
            text=f"#{idx} {run_id} — {reason} — {start} → {end} ({dur_txt}){wipe_txt}",
            size_hint_y=None, height="24dp", halign="left", valign="middle",
        )
        header.bind(size=lambda inst, val: setattr(inst, 'text_size', val))
        row.add_widget(header)

        roms = entry.get("roms") or []
        for rom_entry in roms:
            slot = rom_entry.get("player_slot")
            subdir_name = rom_entry.get("subdir") or f"p{slot}"
            run_path = entry.get("_path") or ""
            slot_dir = os.path.join(run_path, subdir_name)
            rom_path = os.path.join(slot_dir, rom_entry.get("rom_file") or "")
            log_path = os.path.join(slot_dir, rom_entry.get("log_file") or "randomizer.log")

            slot_row = BoxLayout(orientation='horizontal', size_hint_y=None,
                                    size=(0, "26dp"), spacing="6dp")
            slot_row.add_widget(Label(text=f"Slot {slot}", size_hint_x=.15))
            slot_row.add_widget(Button(
                text="Log öffnen", size_hint_x=.25,
                on_press=lambda inst, p=log_path: self._open_path(p),
            ))
            slot_row.add_widget(Button(
                text="ROM-Pfad kopieren", size_hint_x=.3,
                on_press=lambda inst, p=rom_path: self._copy_to_clipboard(p),
            ))
            slot_row.add_widget(Button(
                text="Ordner öffnen", size_hint_x=.3,
                on_press=lambda inst, p=slot_dir: self._open_path(p),
            ))
            row.add_widget(slot_row)

        return row

    def _open_path(self, path: str):
        if not path or not os.path.exists(path):
            self._info_popup("Fehler", f"Pfad nicht gefunden:\n{path}")
            return
        try:
            os.startfile(path)  # type: ignore[attr-defined]
        except Exception as err:
            logger.error(f"os.startfile({path}) failed: {err}")
            self._info_popup("Fehler", f"Konnte Pfad nicht öffnen:\n{err}")

    def _copy_to_clipboard(self, text: str):
        Clipboard.copy(text or "")
        self._info_popup("Kopiert", text or "(leer)")

    def _info_popup(self, title: str, text: str):
        box = BoxLayout(orientation='vertical')
        box.add_widget(Label(text=text))
        btn = Button(text='OK', size_hint=(.5, .4), pos_hint={'center_x': .5})
        box.add_widget(btn)
        popup = Popup(title=title, content=box, size_hint=(None, None), size=(500, 200))
        btn.bind(on_release=popup.dismiss)
        popup.open()

    def _end_run_manual(self):
        if self.munchlax is None:
            self._info_popup("Fehler", "Munchlax nicht verfügbar.")
            return

        confirm_box = BoxLayout(orientation='vertical', spacing="10dp")
        confirm_box.add_widget(Label(text="Aktiven Run wirklich beenden?"))
        btn_row = BoxLayout(orientation='horizontal', size_hint_y=.4, spacing="10dp")
        yes_btn = Button(text="Ja, beenden")
        no_btn = Button(text="Abbrechen")
        btn_row.add_widget(yes_btn)
        btn_row.add_widget(no_btn)
        confirm_box.add_widget(btn_row)
        popup = Popup(title="Run beenden", content=confirm_box,
                        size_hint=(None, None), size=(500, 200))

        def _yes(*_):
            popup.dismiss()
            asyncio.create_task(self._do_end_run_manual())
        yes_btn.bind(on_release=_yes)
        no_btn.bind(on_release=lambda *_: popup.dismiss())
        popup.open()

    async def _do_end_run_manual(self):
        # Blockierendes I/O (SQLite VACUUM INTO, shutil.copy2) im Executor —
        # analog zum bestehenden _do_update_sprites-Muster.
        loop = asyncio.get_event_loop()
        try:
            run_id = await loop.run_in_executor(
                None, self.munchlax.end_run_manual, "manual")
        except Exception as err:
            logger.error(f"end_run_manual failed: {err}")
            import traceback
            logger.error(traceback.format_exc())
            self._info_popup("Fehler", f"Run beenden fehlgeschlagen:\n{err}")
            return
        if run_id:
            self._info_popup("Run beendet", f"Run {run_id} als 'manual' abgeschlossen.")
        else:
            self._info_popup("Hinweis", "Kein aktiver Run vorhanden.")
        self._refresh_run_history()

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

    def _make_masked_ip_widget(self, value: str, ref_id: str) -> BoxLayout:
        """Zeigt IP maskiert an; 'Anzeigen'-Button deckt auf. Verhindert Stream-Leaks."""
        row = BoxLayout(orientation='horizontal', size_hint=(.5, None), size=(0, "30dp"), spacing="5dp")
        label = Label(
            text="••••••••" if value else "—",
            markup=True, on_ref_press=self.clipboard, size_hint_x=.65,
        )
        toggle = Button(text="Anzeigen", size_hint_x=.35)
        state = {"revealed": False}

        def _toggle(_btn):
            state["revealed"] = not state["revealed"]
            if state["revealed"] and value:
                label.text = f"[ref={ref_id}]{value}[/ref]"
                toggle.text = "Verbergen"
            else:
                label.text = "••••••••" if value else "—"
                toggle.text = "Anzeigen"

        toggle.bind(on_press=_toggle)
        row.add_widget(label)
        row.add_widget(toggle)
        return row

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

        self.ausklapp_button_zeigen_oder_verstecken(self.ids.game_sprites_check, initializing=True)
        self.obs_2_pcs_setup(self.ids.obs_sprites_check, initializing=True)

        if sp['obs_2_pc']:
            self.ids.common_obs_path.text = sp['common_obs_path']
            self.ids.items_obs_path.text = sp['items_obs_path']
            self.ids.badges_obs_path.text = sp['badges_obs_path']

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

        ov = self.controller.load_overlay()
        self.ids["overlay_port"].text = ov.get('port', '43888')
        self.ids["overlay_layout"].text = ov.get('layout', 'horizontal')
        self.ids["overlay_badge_layout"].text = ov.get('badge_layout', 'horizontal')
        self.ids["animation_duration_ms"].text = str(ov.get('animation_duration_ms', 300))
        self._update_overlay_links()

        nuz = self.controller.load_nuzlocke()
        self.ids["nuz_enabled"].state = 'down' if nuz.get('enabled', False) else 'normal'
        self.ids["nuz_shiny_clause"].state = 'down' if nuz.get('shiny_clause', True) else 'normal'
        self.ids["nuz_dupes_clause"].state = 'down' if nuz.get('dupes_clause', True) else 'normal'
        self.ids["nuz_gifts_additional"].state = 'down' if nuz.get('gifts_are_additional', True) else 'normal'
        self.ids["nuz_fossils_repeatable"].state = 'down' if nuz.get('fossils_repeatable', True) else 'normal'
        self.ids["nuz_static_separate"].state = 'down' if nuz.get('static_encounters_separate', True) else 'normal'

    def _build_overlay_buttons(self):
        overlay_box = self.ids["overlay"]
        if "overlay_link_grid" in self.ids:
            overlay_box.remove_widget(self.ids["overlay_link_grid"])

        port = self.ids["overlay_port"].text if "overlay_port" in self.ids else self.ov.get('port', '43888')
        layout = self.ids["overlay_layout"].text if "overlay_layout" in self.ids else self.ov.get('layout', 'horizontal')
        badge_layout = self.ids["overlay_badge_layout"].text if "overlay_badge_layout" in self.ids else self.ov.get('badge_layout', 'horizontal')
        player_count = self.pl.get('player_count', 1)

        # Container haelt Player-Grid + Team-Grid getrennt. Kivy GridLayout fuellt
        # Zellen strikt nach Einfuegereihenfolge — Player- und Team-Cells duerfen
        # nicht in dieselbe Grid haengen, sonst rutschen sie in dieselbe Zeile
        # (z.B. Solo mit 1 Spieler + 1 Team-Cell → beide nebeneinander statt untereinander).
        container = BoxLayout(orientation='vertical', size_hint_y=None, spacing="10dp", padding=("0dp", "10dp"))
        container.bind(minimum_height=container.setter('height'))
        self.ids["overlay_link_grid"] = weakref.proxy(container)

        player_grid = GridLayout(cols=1, size_hint_y=None, spacing="10dp")
        player_grid.bind(minimum_height=player_grid.setter('height'))

        team_layout_param = f"?layout={layout}" if layout != 'horizontal' else ""
        badge_layout_param = f"?layout={badge_layout}" if badge_layout != 'horizontal' else ""
        for p in range(1, player_count + 1):
            team_url = f"http://localhost:{port}/player/{p}{team_layout_param}"
            badge_url = f"http://localhost:{port}/player/{p}/badges{badge_layout_param}"

            player_cell = BoxLayout(orientation='horizontal', size_hint_y=None, height="40dp", spacing="10dp")
            player_cell.add_widget(Label(text=f"Spieler {p}", size_hint_x=.3))
            player_cell.add_widget(Button(text="Pokemon", size_hint_x=.35,
                on_press=lambda inst, url=team_url: self._copy_overlay_url(url)))
            player_cell.add_widget(Button(text="Badges", size_hint_x=.35,
                on_press=lambda inst, url=badge_url: self._copy_overlay_url(url)))
            player_grid.add_widget(player_cell)

        container.add_widget(player_grid)

        # Team-Badge-URLs (AND ueber Team-Owner + Region-Gruppierung).
        # Bei aktivem Soullink: distinct team_ids aus soullink_team_membership.
        # Sonst: 1 impliziter Solo-Team-Slug fuer den lokalen Owner.
        team_ids = self._resolve_team_ids()
        if team_ids:
            team_header = Label(text="Team-Overlays", size_hint_y=None, height="30dp", bold=True)
            container.add_widget(team_header)
            team_grid = GridLayout(cols=1, size_hint_y=None, spacing="10dp")
            team_grid.bind(minimum_height=team_grid.setter('height'))
            for team_id, team_label in team_ids:
                team_badge_url = f"http://localhost:{port}/team/{team_id}/badges{badge_layout_param}"
                team_cell = BoxLayout(orientation='horizontal', size_hint_y=None, height="40dp", spacing="10dp")
                team_cell.add_widget(Label(text=team_label, size_hint_x=.3))
                team_cell.add_widget(Label(text="", size_hint_x=.35))
                team_cell.add_widget(Button(text="Team-Badges", size_hint_x=.35,
                    on_press=lambda inst, url=team_badge_url: self._copy_overlay_url(url)))
                team_grid.add_widget(team_cell)
            container.add_widget(team_grid)

        overlay_box.add_widget(container)

    def _resolve_team_ids(self) -> list[tuple[str, str]]:
        """Liefert [(team_id_slug, label)] fuer den Team-Badge-URL-Bereich.

        - Soullink coop/versus: distinct team_ids aus soullink_team_membership,
          Label = raw team_id (vor Slug).
        - versus_ffa: pro expected_owner ein Team, Label = owner-Name.
        - Off/Nuzlocke: 1 impliziter Solo-Team-Slug (lokaler Owner), Label = "Team".
        """
        mode = (self.nuz.get("soullink_mode") or "off").lower()
        if mode == "coop":
            team_map = self.nuz.get("soullink_team_membership", {}) or {}
            if team_map:
                distinct: dict[str, str] = {}
                for _owner, raw_team in team_map.items():
                    if raw_team is None:
                        continue
                    slug = slug_team_id(raw_team)
                    if slug not in distinct:
                        distinct[slug] = str(raw_team)
                return [(slug, f"Team {label}") for slug, label in distinct.items()]
            # Coop-Fallback spiegelt arceus._effective_team_membership:
            # bei leerem team_membership (NuzlockeMenu befuellt es nur fuer versus)
            # alle expected_owners in ein Bucket "coop". Slug muss literal "coop"
            # sein, sonst URL-Mismatch zum Server.
            expected = self.nuz.get("soullink_expected_owners", []) or []
            if expected:
                return [("coop", "Team (Coop)")]
            return []
        if mode == "versus":
            team_map = self.nuz.get("soullink_team_membership", {}) or {}
            distinct: dict[str, str] = {}
            for _owner, raw_team in team_map.items():
                if raw_team is None:
                    continue
                slug = slug_team_id(raw_team)
                if slug not in distinct:
                    distinct[slug] = str(raw_team)
            return [(slug, f"Team {label}") for slug, label in distinct.items()]
        if mode == "versus_ffa":
            expected = self.nuz.get("soullink_expected_owners", []) or []
            return [(slug_team_id(o), f"Team {o}") for o in expected if o]
        owner = self._local_owner_string()
        return [(slug_team_id(owner), "Team (Solo)")]

    def _local_owner_string(self) -> str:
        your_name = self.pl.get('your_name', '') if self.pl else ''
        client_id = str(self.rem.get('client_id', 0)) if self.rem else '0'
        return PokedexDB.build_owner(your_name, client_id)

    def _on_layout_changed(self, value):
        self.save_changes()

    def _copy_overlay_url(self, url):
        Clipboard.copy(url)
        box = BoxLayout(orientation='vertical')
        box.add_widget(Label(text=url))
        btn = Button(text='OK', size_hint=(.5, .4), pos_hint={'center_x': .5})
        box.add_widget(btn)
        popup = Popup(title='Link kopiert', content=box, size_hint=(None, None), size=(400, 150))
        btn.bind(on_press=popup.dismiss)
        popup.open()

    def _update_overlay_links(self):
        self._build_overlay_buttons()

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

        # Overlay-Einstellungen sammeln
        duration_ms = int(self.ids["animation_duration_ms"].text)
        self.controller.save_overlay({
            'port': self.ids["overlay_port"].text,
            'layout': self.ids["overlay_layout"].text,
            'badge_layout': self.ids["overlay_badge_layout"].text,
            'animation_duration_ms': duration_ms,
        })
        self.controller.save_sprites({
            'obs_animation_duration_ms': duration_ms,
        })
        self._update_overlay_links()

        # Nuzlocke-Einstellungen sammeln
        self.controller.save_nuzlocke({
            'enabled': self.ids["nuz_enabled"].state == 'down',
            'shiny_clause': self.ids["nuz_shiny_clause"].state == 'down',
            'dupes_clause': self.ids["nuz_dupes_clause"].state == 'down',
            'gifts_are_additional': self.ids["nuz_gifts_additional"].state == 'down',
            'fossils_repeatable': self.ids["nuz_fossils_repeatable"].state == 'down',
            'static_encounters_separate': self.ids["nuz_static_separate"].state == 'down',
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
