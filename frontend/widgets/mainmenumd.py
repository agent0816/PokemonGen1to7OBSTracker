from kivymd.theming import ThemableBehavior
from kivymd.uix.behaviors import RectangularRippleBehavior
from kivymd.uix.boxlayout import MDBoxLayout
from kivymd.uix.button import MDButton
from kivymd.uix.button import MDIconButton
from kivymd.uix.button import MDButtonIcon
from kivymd.uix.label import MDIcon
from kivymd.uix.label import MDLabel
from kivymd.uix.screen import MDScreen

from backend.classes.obs import OBS

from kivy.animation import Animation
from kivy.uix.behaviors import ButtonBehavior
from kivy.clock import Clock
from kivy.core.window import Window
from kivy.properties import NumericProperty
from kivy.uix.button import Button
from kivy.uix.scrollview import ScrollView
from kivy.uix.stencilview import StencilView

class MainMenu(MDScreen):
    COLLAPSE_THRESHOLD = 600
    window_width = NumericProperty(Window.width)
    window_height = NumericProperty(Window.height)
    
    def __init__(self,arceus,bizhawk,citra,bizhawk_instances,munchlax,obs_websocket,configsave,sp,rem,obs,bh,pl,app_version,**kwargs):
        super().__init__(**kwargs)
        self.name = "MainMenu"
        self.arceus = arceus
        self.bizhawk = bizhawk
        self.citra = citra
        self.bizhawk_instances = bizhawk_instances
        self.munchlax = munchlax
        self.obs_websocket: OBS = obs_websocket
        self.configsave = configsave
        self.sp = sp
        self.rem = rem
        self.obs = obs
        self.bh = bh
        self.pl = pl
        self.selected_session = ""
        self.app_version = app_version
        self.connectors = set()

        self.box = MDBoxLayout(orientation="horizontal")

        orig_menu_hint = .2
        self.initial_w = Window.width * orig_menu_hint

        self.left_part = StencilView(size_hint_x=None, width = self.initial_w)

        self.control_menu = ControlMenu()
        self.left_part.add_widget(self.control_menu)

        self.middle_icon = MDButtonIcon(icon="chevron-left")

        self.middle_button = MDButton(
            self.middle_icon,
            style="filled",
            on_press=self.toggle_menu,
            ripple_effect=False,
        )

        self.middle_button.theme_width = "Custom"
        self.middle_button.width = 24

        self.right_part = ScrollView(do_scroll_y=False, do_scroll_x=True)

        self.scroll_box = TrainerMenu(orientation="horizontal", size_hint_x=None)
        self.scroll_box.bind(minimum_width=self.scroll_box.setter('width'))
        self.right_part.add_widget(self.scroll_box)

        self.box.add_widget(self.left_part)
        self.box.add_widget(self.middle_button)
        self.box.add_widget(self.right_part)
        self.add_widget(self.box)

        Window.bind(on_resize=self._on_window_resize)
        Window.bind(on_maximize=self._on_max_restore)
        Window.bind(on_restore=self._on_max_restore)

    def on_pre_enter(self):
        Clock.schedule_once(lambda dt: setattr(self.middle_button, "radius", [10,10,10,10]), .05)
        Clock.schedule_once(lambda dt: setattr(self.middle_button, "height", self.height), .05)
        self.middle_button._button_icon.x = 2
    
    def _on_max_restore(self, window):
        self.on_window_width()

    def _on_window_resize(self, window, width, height):
        self.window_width = width
        self.window_height = height
    
    def on_window_width(self, *args):
        if (self.width < self.COLLAPSE_THRESHOLD and self.left_part.width > 0) or (self.width >= self.COLLAPSE_THRESHOLD and self.left_part.width == 0):
            self.toggle_menu()
        
    def on_window_height(self, *args):
        Clock.schedule_once(lambda dt: setattr(self.middle_button, "height", self.height), .05)

    def toggle_menu(self, *args):
        collapsed = self.left_part.width > 0

        new_hint = 0 if collapsed else self.initial_w
        new_icon = "chevron-right" if collapsed else "chevron-left"

        anim = Animation(width=new_hint, d=0.3)
        anim.start(self.left_part)

        def on_complete(animation, widget):
            self.middle_icon.icon = new_icon
        anim.bind(on_complete=on_complete)

class ControlMenu(MDBoxLayout):
    def __init__(self, *args, **kwargs):
        super().__init__(**kwargs)



class TrainerMenu(MDBoxLayout):
    def __init__(self, *args, **kwargs):
        super().__init__(**kwargs)

class TrainerBox(MDBoxLayout):
    def __init__(self, *args, **kwargs):
        super().__init__(**kwargs)

class PokemonBox(MDBoxLayout):
    def __init__(self, *args, **kwargs):
        super().__init__(**kwargs)