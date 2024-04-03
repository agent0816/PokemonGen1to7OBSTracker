from pathlib import Path
import pathvalidate
import weakref
import yaml
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.floatlayout import FloatLayout
from kivy.uix.image import Image
from kivy.uix.label import Label
from kivy.uix.popup import Popup
from kivy.uix.screenmanager import CardTransition
from kivy.uix.screenmanager import FadeTransition
from kivy.uix.screenmanager import Screen
from kivy.uix.screenmanager import ScreenManager
from kivy.uix.scrollview import ScrollView
from kivy.uix.textinput import TextInput
from kivy.uix.togglebutton import ToggleButton

class DeleteSessionPopup(Popup):
    def __init__(self, session_menu, session_name, **kwargs):
        super().__init__(**kwargs)        
        self.session_menu = session_menu
        self.session_name = session_name
        
        self.title = "Session löschen!"
        self.size_hint = (0.8, 0.4)

        layout = BoxLayout(orientation="vertical")
        layout.add_widget(Label(text=f"Möchtest du Session '{self.session_name}' löschen?"))

        btn_layout = BoxLayout(size_hint_y=None, height="50dp", spacing="5dp")
        btn_yes = Button(text="Ja", on_press=self.on_yes)
        btn_no = Button(text="Nein", on_press=self.dismiss)

        btn_layout.add_widget(btn_yes)
        btn_layout.add_widget(btn_no)
        layout.add_widget(btn_layout)

        self.content = layout

    def on_yes(self, instance):
        self.session_menu.session_list_names.remove(self.session_name)
        self.session_menu.session_list_box.update_session_box()

        directory_to_delete = self.session_menu.configsave.text.replace("default", self.session_name).replace(" ", "_")

        path_to_delete = Path(directory_to_delete)

        if path_to_delete.exists():
            for entry in path_to_delete.iterdir():
                if entry.is_file():
                    entry.unlink()

            path_to_delete.rmdir()

        self.dismiss()

class CreateSessionPopup(Popup):
    def __init__(self, sessionmenu, **kwargs):
        super().__init__(**kwargs)
        self.sessionmenu = sessionmenu

        self.title = "Erstellen einer neuen Session"
        self.size_hint = (0.8, 0.4)

        layout = BoxLayout(orientation="vertical")
        layout.add_widget(Label(text="Der Name der Session?", size_hint=(1, .3)))

        name_text_box = TextInput(size_hint=(.6,None), size=(0, "30dp"), pos_hint={'center_x':0.5}, multiline=False, write_tab=False)
        layout.add_widget(name_text_box)
        self.name_text_box = name_text_box

        self.error_label = Label(color=(1,0,0,1), text = " ", size_hint=(1, .3))
        layout.add_widget(self.error_label)

        btn_layout = BoxLayout(size_hint_y=None, height="50dp", spacing="5dp")
        btn_yes = Button(text="Erstellen", on_press=self.on_yes)
        btn_no = Button(text="Abbrechen", on_press=self.dismiss)

        btn_layout.add_widget(btn_yes)
        btn_layout.add_widget(btn_no)
        layout.add_widget(btn_layout)

        self.content = layout

    def on_yes(self, instance):
        name = self.name_text_box.text
        if not name:
            self.error_label.text = "Der Name darf nicht leer sein."
        elif not pathvalidate.is_valid_filename(name):
            self.error_label.text = f"Der Name enthält unerlaubte Zeichen. Vorschlag: {pathvalidate.sanitize_filename(name)}"
        elif name in self.sessionmenu.session_list_names:
            self.error_label.text = "Dieser Session-Name existiert bereits."
        else:
            self.sessionmenu.newest_session_name = name
        
            self.dismiss()

class SessionOnboardingPopup(Popup):
    def __init__(self, sessionmenu, screenmanager, **kwargs):
        super().__init__(**kwargs)
        self.sessionmenu = sessionmenu
        self.canceled = False

        self.title = "Einstellungen der neuen Session"
        self.size_hint = (0.8, 0.6)

        self.screenmanager: ScreenManager = screenmanager
        self.screen_number = 0
        self.last_screen_number = len(screenmanager.screens) - 1

        layout = BoxLayout(orientation="vertical")

        btn_layout = BoxLayout(size_hint_y=None, height="50dp", spacing="5dp")
        self.btn_prev = Button(text="Zurück", on_press=self.on_previous)
        self.btn_next = Button(text="Weiter", on_press=self.on_next)
        btn_no = Button(text="Abbrechen", on_press=self.on_cancel)

        btn_layout.add_widget(self.btn_prev)
        btn_layout.add_widget(self.btn_next)
        btn_layout.add_widget(btn_no)

        layout.add_widget(self.screenmanager)

        layout.add_widget(btn_layout)

        self.content = layout

        self.change_screen(self.btn_next, init=True)

    def on_previous(self, instance):
        self.screen_number -= 1
        self.change_screen(instance)

    def on_next(self, instance):
        current_screen = self.screenmanager.screens[self.screen_number]
        screen_is_valid = self.validate_inputs(current_screen)
        if screen_is_valid:
            self.screen_number += 1
            current_screen.error_label.text = ""
            self.change_screen(instance)
        else:
            current_screen.error_label.text = "Bitte eine Angabe machen! Es darf kein '_' enthalten sein!"
    
    def validate_inputs(self, current_screen):
        return current_screen.is_valid()

    def on_confirmation(self, instance):
        current_screen = self.screenmanager.screens[self.screen_number]
        screen_is_valid = self.validate_inputs(current_screen)
        if screen_is_valid:
            self.sessionmenu.pl["your_name"] = self.screenmanager.ids["name_text"].text
            selected_game = ""
            toggle_buttons = ToggleButton.get_widgets("games")
            for button in toggle_buttons:
                if button.state == "down":
                    selected_game = button.text
            del toggle_buttons
            self.sessionmenu.pl["session_game"] = selected_game
            self.dismiss()
        else:
            current_screen.error_label.text = "Bitte eine Angabe machen!"

    def change_screen(self, instance, init=False):
        if not init:
            self.screenmanager.current = self.screenmanager.screens[self.screen_number].name
        if self.screenmanager.current.startswith("first"):
            self.btn_prev.disabled = True
        if self.screenmanager.current.startswith("last"):
            self.btn_prev.disabled = False
            instance.text = "Bestätigen"
            instance.unbind(on_press=self.on_next)
            instance.bind(on_press=self.on_confirmation)
        if self.screen_number == self.last_screen_number - 1:
            self.btn_next.text = "Weiter"
            self.btn_next.unbind(on_press=self.on_confirmation)
            self.btn_next.bind(on_press=self.on_next)

    def on_cancel(self, instance):
        self.canceled = True
        self.dismiss()

class SessionOnboardingScreen(ScreenManager):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)

        self.transition = FadeTransition()
        layout_first_name, is_valid_fist_name = self.create_name_screen()
        first_name = OnboardingScreen("first_name", layout_first_name)
        first_name.set_is_valid(is_valid_fist_name)
        self.add_widget(first_name)

        layout_last_game_selection, is_valid_last_game_selection = self.create_select_game_screen()
        last_game_selection = OnboardingScreen("last_game_selection", layout_last_game_selection)
        last_game_selection.set_is_valid(is_valid_last_game_selection)
        self.add_widget(last_game_selection)
        self.current = "first_name"

    def select_gen(self, instance, screenmanager):
        old_screen_number = int(screenmanager.current.split(" ")[1])
        new_screen_number = int(instance.text.split(" ")[1])
        if new_screen_number < old_screen_number:
            screenmanager.transition.mode = "pop"
        else:
            screenmanager.transition.mode = "push"
        screenmanager.current = instance.text

    def create_name_screen(self):
        screen = BoxLayout(orientation="vertical")
        
        screen.add_widget(Label(text="Wie möchtest du heißen?", size_hint=(1, .3)))

        name_text_box = TextInput(size_hint=(.6,None), size=(0, "30dp"), pos_hint={'center_x':0.5}, multiline=False, write_tab=False)
        self.ids["name_text"] = weakref.proxy(name_text_box)
        screen.add_widget(name_text_box)
        
        error_label = Label(color=(1,0,0,1), text = " ", size_hint=(1, .3))
        screen.ids["error_label"] = weakref.proxy(error_label)
        screen.add_widget(error_label)

        def validation_callback(text_to_validate=name_text_box):
            if text_to_validate.text and "_" not in text_to_validate.text:
                return True
            return False

        return screen, validation_callback

    def create_select_game_screen(self):
        screen_layout = BoxLayout(orientation="vertical")
        
        screen_layout.add_widget(Label(text="Bitte wähle das Spiel", size_hint=(1, .3)))

        game_box = BoxLayout(orientation='vertical')

        screen_layout.add_widget(game_box)

        select_box = BoxLayout(orientation='horizontal')

        gen_screen = GameSelectionScreen()

        gens = [f"Gen {i}" for i in range (1, 8)]

        for gen in gens:
            toggle_selector = ToggleButton(text=gen, group="gen_selection", allow_no_selection=False, size_hint=(1, 0.5))
            toggle_selector.bind(on_press=lambda instance: self.select_gen(instance, gen_screen))
            self.ids[gen] = weakref.proxy(toggle_selector)
            select_box.add_widget(toggle_selector)

        self.ids['Gen 1'].state = 'down'

        game_box.add_widget(select_box)

        game_box.add_widget(gen_screen)

        error_label = Label(color=(1,0,0,1), text = " ", size_hint=(1, .3))
        screen_layout.ids["error_label"] = weakref.proxy(error_label)
        screen_layout.add_widget(error_label)

        def validation_callback():
            result = False
            toggle_buttons = ToggleButton.get_widgets("games")
            for button in toggle_buttons:
                if button.state =="down":
                    result = True
            del toggle_buttons
            return result

        return screen_layout, validation_callback

class OnboardingScreen(Screen):
    def __init__(self, name, layout, **kwargs):
        super().__init__(**kwargs)

        self.name = name
        self.add_widget(layout)

        self.error_label = layout.ids["error_label"]

        self._is_valid = self.default_is_valid

    def set_is_valid(self, funtion_callback):
        self._is_valid = funtion_callback

    def is_valid(self):
        return self._is_valid()

    def default_is_valid(self):
        return True

class GameSelectionScreen(ScreenManager):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)

        self.transition = CardTransition()
        self.games={
            'Gen 1':[('Rot', 'Rot'), ('Blau', 'Blau'),('Gelb', 'Gelb')],
            'Gen 2':[('Silber', 'Silber'),('Gold', 'Gold'),('Kristall', 'Kristall')],
            'Gen 3':[('Rubin', 'Rubin'), ('Saphir', 'Saphir'),('Smaragd', 'Smaragd'),('Feuerrot', 'Feuerrot'), ('Blattgrün', 'Blattgruen')]
            ,'Gen 4':[('Diamant', 'Diamant'), ('Perl', 'Perl'),('Platin', 'Platin'),('Herzgold', 'Herzgold'), ('Seelensilber', 'Seelensilber')],
            'Gen 5':[('Schwarz', 'Schwarz'),('Weiß', 'Weiss'),('Schwarz 2', 'Schwarz2'), ('Weiß 2', 'Weiss2')],
            'Gen 6':[('X', 'X'),('Y', 'Y'),('Alpha Saphir', 'Alpha_Saphir'), ('Omega Rubin', 'Omega_Rubin')],
            'Gen 7':[('Sonne', 'Sonne') ,('Mond', 'Mond'), ('Ultra Sonne', 'Ultrasonne'), ('Ultra Mond', 'Ultramond')]
        }
        for gen, gameslist in self.games.items():
            self.add_widget(Gen_Screen(gen, gameslist))

        self.current = 'Gen 1'

class Gen_Screen(Screen):
    def __init__(self, name, games, **kwargs):
        super().__init__(**kwargs)
        self.name = name
        box = BoxLayout(orientation='horizontal')
        for game in games:
            toggle = ToggleButton(group="games", text=game[0])
            # toggle = ToggleImageButton(f"backend/ressources/{game[1]}.png", f"backend/ressources/{game[1]}_down.png", group="games")
            box.add_widget(toggle)
        self.add_widget(box)

class ToggleImageButton(BoxLayout):
    def __init__(self, image_normal, image_down, group=None, **kwargs):
        super().__init__(**kwargs)
        
        layout = FloatLayout(size_hint=(None, None), size=("200dp", "200dp"))

        self.image_normal = Image(source=image_normal, opacity=1, fit_mode="contain", size_hint=(1,1))
        self.image_down = Image(source=image_down, opacity=0, fit_mode="contain", size_hint=(1,1))  # Anfangs unsichtbar
        
        layout.add_widget(self.image_normal)
        layout.add_widget(self.image_down)
        
        self.toggle_button = ToggleButton(size_hint=(1, 1), background_color=(0, 0, 0, 0), group=group)
        self.toggle_button.bind(state=self.on_toggle_state)
        layout.add_widget(self.toggle_button)

        self.add_widget(layout)
        
    def on_toggle_state(self, instance, value):
        if value == 'normal':
            self.image_normal.opacity = 1
            self.image_down.opacity = 0
        else:
            self.image_normal.opacity = 0
            self.image_down.opacity = 1

class SessionList(ScrollView):
    def __init__(self, session_list,  **kwargs):
        super().__init__(**kwargs)

        self.session_list = session_list

        self.empty_label = Label(text="Bitte neue Session erstellen.", size_hint_y=None, size=(0, "30dp"))

        self.session_box = BoxLayout(orientation='vertical', size_hint_y=None)
        self.session_box.bind(minimum_height=self.session_box.setter('height'))

        self.update_session_box(init=True)

        self.add_widget(self.session_box)

    def update_session_box(self, init=False):
        children = self.session_box.children
        for child in children:
            if child.state == 'down':
                child.state = 'normal'
        if not init and children:
            self.session_box.clear_widgets()
        
        if len(self.session_list) == 0:
            self.session_box.add_widget(self.empty_label)

        for session in self.session_list:
            session_selector = ToggleButton(text=session, group="sessions", size_hint_y=None, size=(0, "30dp"))
            self.session_box.add_widget(session_selector)

class SessionMenu(Screen):
    def __init__(self, session_list_names, main_menu, settings_menu, configsave, sp, rem, obs, bh, pl, app_version, **kwargs):
        super().__init__(**kwargs)

        self.name = "SessionMenu"
        self.main_menu = main_menu
        self.settings_menu = settings_menu
        self.configsave = configsave
        self.default_config = configsave.text
        self.sp = sp
        self.rem = rem
        self.obs = obs
        self.bh = bh
        self.pl = pl
        self.app_version = app_version

        self.session_list_names = session_list_names
        self.newest_session_name = None
        self.session_dict = {}

        box = BoxLayout(orientation="vertical")
        header_box = BoxLayout(orientation='horizontal', size_hint_y=0.15, padding=(0,"10dp"))
        
        logo = Label(text='Logo', size_hint=(.15,1))
        header_box.add_widget(logo)

        header_box.add_widget(Label(text=f"Version {app_version}",size_hint_x=.85))

        box.add_widget(header_box)

        session_box = BoxLayout(orientation='horizontal', size_hint_y=0.85)

        button_box = BoxLayout(orientation='vertical', size_hint_x=0.15)

        standard_config_button = Button(text="Standard \nEinstellungen", on_press=self.go_to_default_settings)
        button_box.add_widget(standard_config_button)

        new_session_button = Button(text="neue Session", on_press=self.create_session_button)
        button_box.add_widget(new_session_button)

        select_session = Button(text="Session\nauswählen", on_press=self.select_session)
        button_box.add_widget(select_session)

        delete_session = Button(text="Session\nlöschen", on_press=self.delete_session)
        button_box.add_widget(delete_session)

        session_box.add_widget(button_box)
        
        session_list_box = SessionList(self.session_list_names, size_hint_x=0.85)
        self.session_list_box = session_list_box

        session_box.add_widget(session_list_box)

        box.add_widget(session_box)

        self.add_widget(box)

    def go_to_default_settings(self, instance):
        self.select_session(instance, default=True)
        self.settings_menu.scrollview.ids["your_name"].disabled = True
        self.manager.current = "SettingsMenu"

    def create_session_button(self, instance):
        popup = CreateSessionPopup(self)
        popup.bind(on_dismiss=self.onboarding_session)
        popup.open()

    def onboarding_session(self, instance):
        self.default_player_name = self.pl["your_name"]
        self.default_selected_game = self.pl["session_game"]
        if self.newest_session_name:
            popup = SessionOnboardingPopup(self, SessionOnboardingScreen())
            popup.bind(on_dismiss=lambda popup: self.write_session(popup))
            popup.open()
            
    def write_session(self, popup):
        if not popup.canceled:
            self.session_list_names.append(self.newest_session_name)
            self.session_list_box.update_session_box()
            self.save_new_session()
            self.save_session_list()
        self.pl["your_name"] = self.default_player_name
        self.pl["session_game"] = self.default_selected_game
        self.newest_session_name = None

    def save_new_session(self):
        new_session_name = self.newest_session_name.replace(" ", "_")
        new_config = self.configsave.text.replace("default", new_session_name)
        new_session = Path(f"{new_config}")
        new_session.mkdir()

        with open(f"{new_session}/sprites.yml", 'w') as file:
            yaml.dump(self.sp, file)

        with open(f"{new_session}/bh_config.yml", 'w') as file:
            yaml.dump(self.bh, file)

        with open(f"{new_session}/obs_config.yml", 'w') as file:
            yaml.dump(self.obs, file)

        with open(f"{new_session}/remote.yml", 'w') as file:
            yaml.dump(self.rem, file)

        with open(f"{new_session}/player.yml", 'w') as file:
            yaml.dump(self.pl, file)

    def select_session(self, instance, default=False):
        if not default:
            selected_session = self.get_selected_session()
        else:
            selected_session = "default"

        if selected_session and Path(self.configsave.text).exists():
            self.main_menu.selected_session = selected_session

            self.settings_menu.selected_session = selected_session
            self.settings_menu.head_label.text = f"Version {self.app_version} | Session: {selected_session}"

            selected_session = selected_session.replace(" ", "_")
            if not default:
                self.configsave.text = self.configsave.text.replace("default", selected_session)
                self.settings_menu.is_session_selected = True
                self.manager.current = "MainMenu"
            else:
                self.configsave.text = self.default_config
                self.settings_menu.is_session_selected = False

            self.load_session_config()

            self.settings_menu.scrollview.ids["your_name"].disabled = False
            self.settings_menu.scrollview.load_config()
            self.settings_menu.scrollview.update_trainer_boxes()
            self.settings_menu.scrollview.update_connections()
            self.main_menu.update_munchlax_connection_circle()
            self.main_menu.init_config()

    def load_session_config(self, default=False):
        if default:
            self.configsave.text = self.default_config
        with open(f"{self.configsave}sprites.yml", 'r') as file:
            new_sp = yaml.safe_load(file)
            
        for key, value in new_sp.items():
            self.sp[key] = value

        with open(f"{self.configsave}bh_config.yml", 'r') as file:
            new_bh = yaml.safe_load(file)

        for key, value in new_bh.items():
            self.bh[key] = value

        with open(f"{self.configsave}obs_config.yml", 'r') as file:
            new_obs = yaml.safe_load(file)

        for key, value in new_obs.items():
            self.obs[key] = value

        with open(f"{self.configsave}remote.yml", 'r') as file:
            new_rem = yaml.safe_load(file)

        for key, value in new_rem.items():
            self.rem[key] = value

        with open(f"{self.configsave}player.yml", 'r') as file:
            new_pl = yaml.safe_load(file)

        for key, value in new_pl.items():
            self.pl[key] = value

    def delete_session(self, instance):
        session_to_delete = self.get_selected_session()
        if session_to_delete:
            popup = DeleteSessionPopup(self, session_to_delete, on_dismiss=lambda instance: self.save_session_list())
            popup.open()

    def get_selected_session(self):
        toggle_widgets = ToggleButton.get_widgets("sessions")
        result = None
        for button in toggle_widgets:
            if button.state == "down":
                result = button.text
        del toggle_widgets
        return result

    def save_session_list(self):
        new_config = self.default_config.replace("default/", "")
        with open(f"{new_config}/session_list.yml", "w") as file:
            yaml.dump(self.session_list_names, file)
