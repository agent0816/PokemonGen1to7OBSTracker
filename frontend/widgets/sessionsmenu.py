import asyncio
from kivy.app import App
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.label import Label
from kivy.uix.screenmanager import Screen

class SessionMenu(Screen):
    def __init__(self, app_version, **kwargs):
        super().__init__(**kwargs)

        self.name = "SessionMenu"

        box = BoxLayout(orientation="vertical")
        header_box = BoxLayout(orientation='horizontal', size_hint_y=0.15, padding=(0,"10dp"))
        
        logo = Label(text='Logo', size_hint=(.15,1))
        header_box.add_widget(logo)

        header_box.add_widget(Label(text=f"Version {app_version}",size_hint_x=.85))

        box.add_widget(header_box)

        session_box = BoxLayout(orientation='horizontal', size_hint_y=0.85)

        button_box = BoxLayout(orientation='vertical', size_hint_x=0.15)



        session_box.add_widget(button_box)

        box.add_widget(session_box)

        self.add_widget(box)


if __name__ == "__main__":
    class TestApp(App):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)

        def build(self):
            return SessionMenu('0.6')
    
    app = TestApp()
    asyncio.run(app.async_run())