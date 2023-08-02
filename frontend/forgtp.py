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
            button.bind(on_press=lambda instance, jump_id=screen_name: self.jump_to(scrollview, jump_id)) #type: ignore
            button_box.add_widget(button)

        layout.add_widget(button_box)
        layout.add_widget(scrollview)

        box.add_widget(layout)
        self.add_widget(box)

    def jump_to(self, scrollview, jump_id):
        scroll_max_height = scrollview.height
        new_scrollheight = scrollview.ids[jump_id].y
        
        scrolling = new_scrollheight / scroll_max_height

        print(f"{scroll_max_height=}")
        print(f"{new_scrollheight=}")
        print(f"{scrolling=}")

        scrollview.scroll_y = scrolling

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
        
        box = BoxLayout(orientation='vertical',size_hint_y=None, spacing="20dp", padding=[0, "30dp"])
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
        grid.add_widget(Label(on_ref_press=self.clipboard,text=f"[ref=ip]{externalIPv4}[/ref]", size_hint=(.5,None), size=(0,"30dp"), markup=True))
        grid.add_widget(Label(text="Deine öffentliche\nIpv6-Adresse", size_hint=(.5,None), size=(0,"30dp")))
        grid.add_widget(Label(on_ref_press=self.clipboard,text=f"[ref=ipv6]{externalIPv6}[/ref]", size=(0,"30dp"), size_hint=(.5,None),markup=True))
        
        remote_box.add_widget(grid)
        box.add_widget(remote_box)

        player_box = BoxLayout(orientation='vertical',size_hint_y=None, spacing="30dp")
        player_box.bind(minimum_height=player_box.setter('height')) # type: ignore
        self.ids["player"] = weakref.proxy(player_box)

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