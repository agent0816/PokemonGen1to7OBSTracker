from kivy.app import App
from kivy.properties import StringProperty
from kivy.uix.screenmanager import Screen
from kivy.uix.screenmanager import ScreenManager
from kivy.uix.screenmanager import FadeTransition

class Screens(ScreenManager):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.transition = FadeTransition()

class SettingsMenu(Screen):
    pass

class MainMenu(Screen):
    pass

class TrackerApp(App):
    pass