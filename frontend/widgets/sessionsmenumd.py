from kivymd.uix.screen import MDScreen
from kivymd.uix.label import MDLabel

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

        self.add_widget(MDLabel(text="Sessions"))