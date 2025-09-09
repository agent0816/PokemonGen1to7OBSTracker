from kivymd.theming import ThemableBehavior
from kivymd.uix.behaviors import RectangularRippleBehavior
from kivymd.uix.boxlayout import MDBoxLayout
from kivymd.uix.button import MDButton
from kivymd.uix.button import MDIconButton
from kivymd.uix.button import MDButtonIcon
from kivymd.uix.button import MDButtonText
from kivymd.uix.divider import MDDivider
from kivymd.uix.label import MDIcon
from kivymd.uix.label import MDLabel
from kivymd.uix.screen import MDScreen
from kivymd.uix.segmentedbutton import MDSegmentedButton
from kivymd.uix.segmentedbutton import MDSegmentedButtonItem
from kivymd.uix.segmentedbutton import MDSegmentButtonLabel
from kivymd.uix.selectioncontrol import MDCheckbox

from backend.classes.obs import OBS

from kivy.animation import Animation
from kivy.uix.behaviors import ButtonBehavior
from kivy.clock import Clock
from kivy.core.window import Window
from kivy.properties import NumericProperty
from kivy.properties import StringProperty
from kivy.uix.button import Button
from kivy.uix.label import Label
from kivy.uix.scrollview import ScrollView
from kivy.uix.stencilview import StencilView

class MainMenu(MDScreen):
    COLLAPSE_THRESHOLD = 700
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

        self.control_menu = ControlMenu(self.app_version, orientation='vertical', theme_width="Custom", theme_height="Custom", width=self.initial_w, height=self.height)
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

        self.scroll_box = TrainerMenu(orientation="horizontal", adaptive_width=True)
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
    def __init__(self, app_version, *args, **kwargs):
        super().__init__(**kwargs)
        self.app_version = app_version

        self.padding = "4dp"
        self.spacing = "4dp"

        self.pos_hint = {"center": (0,.5)}

        logo_settings = MDBoxLayout(theme_width="Custom", orientation="vertical", adaptive_height=True, size_hint_x=1)
        logo = MDLabel(text=f"Logo\nVersion {self.app_version}", adaptive_height=True)
        logo_settings.add_widget(logo)

        self.add_widget(logo_settings)

        start_server_button = MDButton(MDButtonText(text="Server starten", pos_hint={"center": (.5, .5)}), theme_width = "Custom", size_hint_x=1)

        self.add_widget(start_server_button)
        
        self.add_widget(MDDivider())

        client = MDSegmentedButtonItem(MDSegmentButtonLabel(text="Client"))
        server = MDSegmentedButtonItem(MDSegmentButtonLabel(text="Server"))
        client_or_server = MDSegmentedButton(server, client)

        self.add_widget(client_or_server)

        self.add_widget(MDDivider())
        
        connect_obs_button = MDButton(MDButtonText(text="OBS verbinden", pos_hint={"center": (.5, .5)}), theme_width = "Custom", size_hint_x=1 )

        self.add_widget(connect_obs_button)

        self.add_widget(MDDivider())

        self.show_names = CheckItem(text="Namen anzeigen")
        self.add_widget(self.show_names)

        self.show_badges = CheckItem(text="Orden anzeigen")
        self.add_widget(self.show_badges)

        self.show_items = CheckItem(text="Items anzeigen")
        self.add_widget(self.show_items)

        self.animated_sprites = CheckItem(text="animierte Sprites")
        self.add_widget(self.animated_sprites)

        self.add_widget(MDDivider())

        connect_emulator_button = MDButton(MDButtonText(text="Emulator starten", pos_hint={"center": (.5, .5)}), theme_width = "Custom", size_hint_x=1 )

        self.add_widget(connect_emulator_button)

        self.add_widget(MDDivider())

        sorting_label = MDLabel(text="Sortierung", halign="center", adaptive_height=True)

        self.add_widget(sorting_label)

        # dexnr = MDSegmentedButtonItem(MDSegmentButtonLabel(text="Dex Nr."))
        team = MDSegmentedButtonItem(MDSegmentButtonLabel(text="Team"))
        # level = MDSegmentedButtonItem(MDSegmentButtonLabel(text="Level"))
        route = MDSegmentedButtonItem(MDSegmentButtonLabel(text="Route"))

        sorting = MDSegmentedButton(team, route)

        self.add_widget(sorting)

        self.add_widget(MDDivider())

        self.save_ram_bizhawk = CheckItem(text="saveRAM automatisch")
        self.add_widget(self.save_ram_bizhawk)

class CheckItem(MDBoxLayout):
    text = StringProperty()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.adaptive_height = True
        self.widgets = [
            MDCheckbox(),
            MDLabel(
                text=self.text,
                adaptive_height=True,
                halign="center",
            ),
        ]

class TrainerMenu(MDBoxLayout):
    def __init__(self, *args, **kwargs):
        super().__init__(**kwargs)

class TrainerBox(MDBoxLayout):
    def __init__(self, *args, **kwargs):
        super().__init__(**kwargs)

class PokemonBox(MDBoxLayout):
    def __init__(self, *args, **kwargs):
        super().__init__(**kwargs)