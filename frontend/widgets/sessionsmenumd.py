from kivymd.uix.button import MDIconButton
from kivymd.uix.card import MDCard
from kivymd.uix.label import MDLabel
from kivymd.uix.label import MDIcon
from kivymd.uix.menu import MDDropdownMenu
from kivymd.uix.gridlayout import MDGridLayout
from kivymd.uix.relativelayout import MDRelativeLayout
from kivymd.uix.screen import MDScreen
from kivymd.uix.scrollview import MDScrollView

from kivy.properties import BooleanProperty

class AddSessionCard(MDCard):
    def __init__(self, session_menu, **kwargs):
        super().__init__(**kwargs)
        self.session_menu = session_menu
        layout = MDRelativeLayout()

        icon_button = MDIcon(
            icon="plus",
            pos_hint={"center_y": .5, "center_x": .5}
        )

        layout.add_widget(icon_button)

        self.add_widget(layout)

    def on_press(self):
        pass

class SessionCard(MDCard):
    def __init__(self, session_name, session_menu, **kwargs):
        super().__init__(**kwargs)
        self.session_name = session_name
        self.session_menu = session_menu

        self.is_selected = BooleanProperty(False)
        
        self.size_hint_y = None
        self.size = (1, "40dp")

        layout = MDRelativeLayout()

        self.icon_button = MDIconButton(
            icon="dots-vertical",
            pos_hint={"top": 1, "right": 1}
        )
        self.session_label = MDLabel(
            text=self.session_name,
            pos_hint={"center_y": .5, "left": 1},
            padding=("10dp", 0),
            adaptive_size=True,
        )

        menu_items = [
            {
                "text":"Select",
                "on_release": self.menu_select,
            }
        ]
        self.menu = MDDropdownMenu(
            caller=self.icon_button, items=menu_items
        )

        self.icon_button.on_release = self.menu.open

        layout.add_widget(self.session_label)
        layout.add_widget(self.icon_button)

        self.add_widget(layout)

    def on_press(self, *args):
        if self.style == "filled":
            self.is_selected = False
        else:
            self.is_selected = True
        self.change_selected_status()
        self.session_menu.change_selected_session(self)

    def menu_select(self, *args):
        self.trigger_action(duration=0)
        self.menu.dismiss()

    def change_selected_status(self):
        item = self.menu.children[0].children[0].children[0]
        if self.is_selected:
            self.style = "filled"
            item.text = "Deselect"
        else:
            self.style = "outlined"
            item.text = "Select"

class SessionMenu(MDScreen):
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
        self.selected_session = None

        scroll_view = MDScrollView(do_scroll_x=False)

        grid_layout = MDGridLayout(
            cols=3,
            padding="12dp",
            spacing="12dp",
            adaptive_height=True
        )

        for session_name in self.session_list_names:
            session_card = SessionCard(session_name, self, style="outlined")
            grid_layout.add_widget(session_card)

        add_session_card = AddSessionCard(self, style="outlined")

        grid_layout.add_widget(add_session_card)
        scroll_view.add_widget(grid_layout)

        self.add_widget(scroll_view)
    
    def change_selected_session(self, session_card):
        if self.selected_session is not None:
            self.selected_session.is_selected = False
            self.selected_session.change_selected_status()
        if session_card.is_selected:
            self.selected_session = session_card
        else:
            self.selected_session = None
