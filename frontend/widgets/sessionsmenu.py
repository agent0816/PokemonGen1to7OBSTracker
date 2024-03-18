import asyncio
from dataclasses import dataclass
from kivy.app import App
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.label import Label
from kivy.uix.popup import Popup
from kivy.uix.screenmanager import Screen
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
        layout.add_widget(Label(text="Der Name der Session?"))
        
        name_text_box = TextInput(size_hint=(.6,1), multiline=False, write_tab=False)
        layout.add_widget(name_text_box)
        self.name_text_box = name_text_box

        btn_layout = BoxLayout(size_hint_y=None, height="50dp", spacing="5dp")
        btn_yes = Button(text="Erstellen", on_press=self.on_yes)
        btn_no = Button(text="Abbrechen", on_press=self.dismiss)

        btn_layout.add_widget(btn_yes)
        btn_layout.add_widget(btn_no)
        layout.add_widget(btn_layout)

        self.content = layout

    def on_yes(self, instance):
        self.sessionmenu.create_session(self.name_text_box.text)
        
        self.dismiss()

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
        self.session_dict = {}

        for session in self.session_list_names:
            self.create_session(session, init=True)

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

    def create_session(self, name, init=False):
        self.session_dict[name] = Session(name, {}) if init else Session(name, {})

        if not init:
            self.session_list_names.append(name)
            self.session_list_box.update_session_box()

    def create_session_button(self, instance):
        popup = CreateSessionPopup(self)
        popup.open()

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