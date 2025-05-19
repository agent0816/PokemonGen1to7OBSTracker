from kivymd.uix.boxlayout import MDBoxLayout
from kivymd.uix.button import MDButton
from kivymd.uix.button import MDIconButton
from kivymd.uix.screen import MDScreen
from kivymd.uix.label import MDLabel

from backend.classes.obs import OBS

from kivy.animation import Animation
from kivy.core.window import Window
from kivy.uix.button import Button
from kivy.uix.scrollview import ScrollView
from kivy.uix.stencilview import StencilView

class MainMenu(MDScreen):
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

        self.transitioned=False

        self.box = MDBoxLayout(orientation="horizontal")

        self.left_part = StencilView(size_hint_x=0.2)
        self.left_part.add_widget(MDLabel(text="linke Seite"))

        self.middle_button = MDIconButton(
            icon="chevron-left",
            size_hint_x=.05,
            size_hint_y=None,
            height=self.height,
            on_press=self.toggle_menu
        )

        self.right_part = ScrollView(size_hint_x=.75,do_scroll_y=False, do_scroll_x=True)

        self.scroll_box = MDBoxLayout(orientation="horizontal", size_hint_x=None)
        self.scroll_box.bind(minimum_width=self.scroll_box.setter('width'))
        self.right_part.add_widget(self.scroll_box)
        
        for i in range(20):
            self.scroll_box.add_widget(Button(text=f"{i}", size_hint_x=None, width="100dp"))

        self._orig_menu_hint = self.left_part.size_hint_x

        self.box.add_widget(self.left_part)
        self.box.add_widget(self.middle_button)
        self.box.add_widget(self.right_part)
        self.add_widget(self.box)

    def toggle_menu(self, *args):
        collapsed = self.left_part.size_hint_x > 0

        new_hint = 0 if collapsed else self._orig_menu_hint
        new_icon = "chevron-right" if collapsed else "chevron-left"

        anim = Animation(size_hint_x=new_hint, d=0.3)
        anim.start(self.left_part)

        def on_complete(animation, widget):
            self.middle_button.icon = new_icon
            print(self.left_part.size_hint_x)
        anim.bind(on_complete=on_complete)

