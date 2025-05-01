from kivymd.uix.screen import MDScreen
from kivymd.uix.label import MDLabel

from backend.classes.obs import OBS

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
