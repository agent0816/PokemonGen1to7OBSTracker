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
        # Highlight an den ScreenManager koppeln: wechselt der Screen
        # programmatisch (z.B. Update-Check -> SessionMenu, Wipe-Banner ->
        # NuzlockeMenu), zieht die NavBar-Markierung nach. Ohne das bleibt sie
        # auf dem beim Bauen aktiven Item (active:True im item_list) stehen.
        self.screen_manager.bind(current=self._on_screen_current)
        # Initial angleichen: current ist beim Bind-Zeitpunkt oft schon gesetzt
        # (Screens.__init__ navigiert vor dem NavBar-Bau).
        self.sync_to_screen(self.screen_manager.current)

    def _on_screen_current(self, screen_manager, screen_name: str) -> None:
        self.sync_to_screen(screen_name)

    def sync_to_screen(self, screen_name: str) -> None:
        # active direkt setzen (nicht set_active_item): das dispatcht kein
        # on_switch_tabs und setzt daher current nicht erneut -> kein Loop.
        # Screens ohne NavBar-Eintrag (z.B. Update) lassen alle Items inaktiv.
        for child in self.children:
            if isinstance(child, TrackerNavigationItem):
                child.active = (child.screen_name == screen_name)

    def on_switch_tabs(
        self,
        item: TrackerNavigationItem,
        item_icon: str,
        item_display_name: str,
    ):
        self.screen_manager.current = item.screen_name