from kivy.properties import StringProperty

from kivymd.uix.navigationbar import MDNavigationBar
from kivymd.uix.navigationbar import MDNavigationItem
from kivymd.uix.navigationbar import MDNavigationItemLabel
from kivymd.uix.navigationbar import MDNavigationItemIcon

class TrackerNavigationItem(MDNavigationItem):
    icon = StringProperty()
    display_name = StringProperty()
    screen_name = StringProperty()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.add_widget(MDNavigationItemIcon(icon=self.icon))
        self.add_widget(MDNavigationItemLabel(text=self.display_name))

class TrackerNavigationBar(MDNavigationBar):
    def __init__(self, screen_manager, item_list: list[dict], *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.screen_manager = screen_manager
        for item in item_list:
            self.add_widget(TrackerNavigationItem(**item))

    def on_switch_tabs(
        self,
        item: TrackerNavigationItem,
        item_icon: str,
        item_display_name: str,
    ):
        self.screen_manager.current = item.screen_name