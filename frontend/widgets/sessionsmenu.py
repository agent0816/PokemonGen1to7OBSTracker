import asyncio
from kivy.app import App
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.label import Label
from kivy.uix.screenmanager import Screen
from kivy.uix.scrollview import ScrollView
from kivy.uix.togglebutton import ToggleButton

class SessionList(ScrollView):
    def __init__(self, session_list,  **kwargs):
        super().__init__(**kwargs)

        self.session_list = session_list

        session_box = BoxLayout(orientation='vertical', size_hint_y=None)
        session_box.bind(minimum_height=session_box.setter('height'))

        if len(self.session_list) == 0:
            session_box.add_widget(Label(text="Bitte neue Session erstellen.", size_hint_y=None, size=(0, "30dp")))

        for session in self.session_list:
            session_selector = ToggleButton(text=session, group="sessions", size_hint_y=None, size=(0, "30dp"))
            session_box.add_widget(session_selector)

        self.add_widget(session_box)

class SessionMenu(Screen):
    def __init__(self, session_list, app_version, **kwargs):
        super().__init__(**kwargs)

        self.name = "SessionMenu"

        self.session_list = session_list

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

        new_session_button = Button(text="neue Session")
        button_box.add_widget(new_session_button)

        select_session = Button(text="Session\nbearbeiten")
        button_box.add_widget(select_session)

        delete_session = Button(text="Session\nlöschen")
        button_box.add_widget(delete_session)

        session_box.add_widget(button_box)
        
        session_list_box = SessionList(self.session_list, size_hint_x=0.85)

        session_box.add_widget(session_list_box)

        box.add_widget(session_box)

        self.add_widget(box)


if __name__ == "__main__":
    class TestApp(App):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)

        def build(self):
            session_list = ['erste', 'zweite', 'dritte']
            return SessionMenu(session_list, '0.6')
    
    app = TestApp()
    asyncio.run(app.async_run())