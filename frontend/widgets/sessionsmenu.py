import asyncio
from dataclasses import dataclass
import weakref
from kivy.app import App
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.label import Label
from kivy.uix.popup import Popup
from kivy.uix.screenmanager import Screen
from kivy.uix.screenmanager import ScreenManager
from kivy.uix.scrollview import ScrollView
from kivy.uix.textinput import TextInput
from kivy.uix.togglebutton import ToggleButton

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
        if not self.name_text_box.text:
            self.error_label.text = "Der Name darf nicht leer sein."
        elif name not in self.sessionmenu.session_list_names:
            self.sessionmenu.newest_session_name = name
        
            self.dismiss()
        else:
            self.error_label.text = "Dieser Session-Name existiert bereits."

class SessionOnboardingPopup(Popup):
    def __init__(self, sessionmenu, **kwargs):
        super().__init__(**kwargs)
        self.sessionmenu = sessionmenu
        self.canceled = False

        self.title = "Einstellungen der neuen Session"
        self.size_hint = (0.8, 0.6)

        self.screen_number = 1
        self.last_screen_number = 2

        self.screen = BoxLayout(orientation="vertical")

        layout = BoxLayout(orientation="vertical")

        btn_layout = BoxLayout(size_hint_y=None, height="50dp", spacing="5dp")
        self.btn_prev = Button(text="Zurück", on_press=self.on_next)
        self.btn_next = Button(text="Weiter", on_press=self.on_next)
        btn_no = Button(text="Abbrechen", on_press=self.on_cancel)

        btn_layout.add_widget(self.btn_prev)
        btn_layout.add_widget(self.btn_next)
        btn_layout.add_widget(btn_no)

        layout.add_widget(self.screen)

        layout.add_widget(btn_layout)

        self.content = layout

        self.change_screen(self.btn_next, init=True)

    def create_name_screen(self):
        self.screen.add_widget(Label(text="Wie möchtest du heißen?", size_hint=(1, .3)))

        name_text_box = TextInput(size_hint=(.6,None), size=(0, "30dp"), pos_hint={'center_x':0.5}, multiline=False, write_tab=False)
        self.ids["name_text"] = weakref.proxy(name_text_box)
        self.screen.add_widget(name_text_box)
        
        self.error_label = Label(color=(1,0,0,1), text = " ", size_hint=(1, .3))
        self.screen.add_widget(self.error_label)

    def create_select_game_screen(self):
        self.screen.add_widget(Label(text="Bitte wähle das Spiel", size_hint=(1, .3)))

        game_box = BoxLayout(orientation='vertical')

        self.screen.add_widget(game_box)

        select_box = BoxLayout(orientation='horizontal')

        gen_screen = GameSelectionScreen()

        gens = [f"Gen {i}" for i in range (1, 8)]

        for gen in gens:
            toggle_selector = ToggleButton(text=gen, group="gen_selection", allow_no_selection=False)
            toggle_selector.bind(on_press=lambda instance: self.select_gen(instance, gen_screen))
            self.ids[gen] = weakref.proxy(toggle_selector)
            select_box.add_widget(toggle_selector)

        self.ids['Gen 1'].state = 'down'

        game_box.add_widget(select_box)

        game_box.add_widget(gen_screen)

        self.error_label = Label(color=(1,0,0,1), text = " ", size_hint=(1, .3))
        self.screen.add_widget(self.error_label)
    
    def select_gen(self, instance, screenmanager):
        screenmanager.current = instance.text

    def on_next(self, instance):
        validated = self.validate_inputs()

        if validated:
            self.change_screen(instance)
        else:
            self.error_label.text = "Bitte eine Angabe machen."
    
    def on_confirmation(self, instance):
        self.dismiss()

    def validate_inputs(self):
        if self.screen_number == 1:
            if self.ids["name_text"].text:
                return True
            return False
        if self.screen_number == 2:
            return True

    def change_screen(self, instance, init=False):
        new_screen = 1 if instance.text == "Weiter" else -1
        self.screen.clear_widgets()
        if not init:
            self.screen_number += new_screen
        if self.screen_number == 1:
            self.btn_prev.disabled = True
            self.create_name_screen()
        if self.screen_number == 2:
            self.create_select_game_screen()
        if self.screen_number == self.last_screen_number:
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

class GameSelectionScreen(ScreenManager):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)

        self.games={
            'Gen 1':['Rot', 'Blau','Gelb'],
            'Gen 2':['Silber','Gold','Kristall'],
            'Gen 3':['Rubin', 'Saphir','Smaragd','Feuerrot', 'Blattgrün']
            ,'Gen 4':['Diamant', 'Perl','Platin','Herzgold', 'Seelensilber'],
            'Gen 5':['Schwarz','Weiß','Schwarz 2', 'Weiß 2'],
            'Gen 6':['X','Y','Alpha Saphir', 'Omega Rubin'],
            'Gen 7':['Sonne' ,'Mond', 'Ultra Sonne', 'Ultra Mond']
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
            toggle = ToggleButton(text=game, group="games")
            box.add_widget(toggle)
        self.add_widget(box)

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
        if not init and children:
            self.session_box.clear_widgets()
        
        if len(self.session_list) == 0:
            self.session_box.add_widget(self.empty_label)

        for session in self.session_list:
            session_selector = ToggleButton(text=session, group="sessions", size_hint_y=None, size=(0, "30dp"))
            self.session_box.add_widget(session_selector)

class SessionMenu(Screen):
    def __init__(self, session_list_names, app_version, **kwargs):
        super().__init__(**kwargs)

        self.name = "SessionMenu"

        self.session_list_names = session_list_names
        self.newest_session_name = None
        self.session_dict = {}

        for session in self.session_list_names:
            self.create_session(session, {}, init=True)

        box = BoxLayout(orientation="vertical")
        header_box = BoxLayout(orientation='horizontal', size_hint_y=0.15, padding=(0,"10dp"))
        
        logo = Label(text='Logo', size_hint=(.15,1))
        header_box.add_widget(logo)

        header_box.add_widget(Label(text=f"Version {app_version}",size_hint_x=.85))

        box.add_widget(header_box)

        session_box = BoxLayout(orientation='horizontal', size_hint_y=0.85)

        button_box = BoxLayout(orientation='vertical', size_hint_x=0.15)

        standard_config_button = Button(text="Standard \nEinstellungen")
        button_box.add_widget(standard_config_button)

        new_session_button = Button(text="neue Session", on_press=self.create_session_button)
        button_box.add_widget(new_session_button)

        select_session = Button(text="Session\nbearbeiten")
        button_box.add_widget(select_session)

        delete_session = Button(text="Session\nlöschen")
        button_box.add_widget(delete_session)

        session_box.add_widget(button_box)
        
        session_list_box = SessionList(self.session_list_names, size_hint_x=0.85)
        self.session_list_box = session_list_box

        session_box.add_widget(session_list_box)

        box.add_widget(session_box)

        self.add_widget(box)

    def create_session(self, name, configuration, init=False):
        self.session_dict[name] = Session(name, {}) if init else Session(name, configuration)

        if not init:
            self.session_list_names.append(name)
            self.session_list_box.update_session_box()

    def create_session_button(self, instance):
        popup = CreateSessionPopup(self)
        popup.bind(on_dismiss=self.onboarding_session)
        popup.open()

    def onboarding_session(self, instance):
        if self.newest_session_name:
            popup = SessionOnboardingPopup(self)
            popup.bind(on_dismiss=lambda popup: self.write_session(popup))
            popup.open()
            
    def write_session(self, popup):
        if not popup.canceled:
            self.create_session(self.newest_session_name)
        self.newest_session_name = None

@dataclass
class Session():
    name: str
    settings: dict

if __name__ == "__main__":
    class TestApp(App):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)

        def build(self):
            session_list = ['erste','zweite','dritte']
            return SessionMenu(session_list, '0.6')
    
    app = TestApp()
    asyncio.run(app.async_run())