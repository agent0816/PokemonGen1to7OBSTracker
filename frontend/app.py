import asyncio
import os
from dataclasses import dataclass
import subprocess
import yaml
import requests
from frontend.widgets.bagmenu import BagMenu
from frontend.widgets.boxmenu import BoxMenu
from frontend.widgets.encountermenu import EncounterMenu
from frontend.widgets.mainmenu import MainMenu
from frontend.widgets.pokedexmenu import PokedexMenu
from frontend.widgets.pokemon_detail import PokemonDetailScreen
from frontend.widgets.sessionsmenu import SessionMenu
from frontend.widgets.settingsmenu import SettingsMenu
from frontend.widgets.nuzlockemenu import NuzlockeMenu
from frontend.widgets.updatemenu import Update
from kivy.app import App
from kivy.clock import Clock
from kivy.core.window import Window

# from kivy.logger import Logger
# from kivy.logger import LOG_LEVELS
# Logger.setLevel(logging.INFO)
from kivy.uix.screenmanager import FadeTransition
from kivy.uix.screenmanager import ScreenManager
from backend.classes.arceus import Arceus
from backend.classes.bizhawk import Bizhawk
from backend.classes.citrahandler import CitraHandler
from backend.classes.munchlax import Munchlax
from backend.classes.obs import OBS
from backend.classes.overlay_server import OverlayServer
from backend.logging_setup import get_logger

logger = get_logger(__name__, 'logs/frontend.log')

from version import VERSION

APP_NAME = "PokemonOBSTracker"
APP_VERSION = VERSION


class Screens(ScreenManager):
    def __init__(self,arceus,bizhawk,citra,bizhawk_instances,munchlax,obs_websocket,overlay_server,externalIPv4,externalIPv6,configsave,sp,rem,obs,bh,pl,rnd,ov,nuz,session_list,**kwargs,):
        super().__init__(**kwargs)
        self.transition = FadeTransition()
        update_menu = Update(APP_NAME, APP_VERSION)
        self.add_widget(update_menu)
        main_menu = MainMenu(
            arceus,
            bizhawk,
            citra,
            bizhawk_instances,
            munchlax,
            obs_websocket,
            overlay_server,
            configsave,
            sp,
            rem,
            obs,
            bh,
            pl,
            rnd,
            ov,
            APP_VERSION,
        )
        self.add_widget(main_menu)
        settings_menu = SettingsMenu(arceus,bizhawk,munchlax,obs_websocket,overlay_server,externalIPv4,externalIPv6,configsave,sp,rem,obs,bh,pl,rnd,ov,nuz,APP_VERSION,)
        self.add_widget(settings_menu)
        session_menu = SessionMenu(session_list, main_menu, settings_menu,configsave, sp, rem, obs, bh, pl, rnd, ov, nuz, APP_VERSION)
        self.add_widget(session_menu)
        pokedex_menu = PokedexMenu(configsave, APP_VERSION)
        self.add_widget(pokedex_menu)
        box_menu = BoxMenu(bizhawk, citra, munchlax, obs_websocket, pl)
        self.add_widget(box_menu)
        bag_menu = BagMenu(configsave, pl, sp, bizhawk, citra, APP_VERSION)
        self.add_widget(bag_menu)
        encounter_menu = EncounterMenu(configsave, pl, APP_VERSION)
        self.add_widget(encounter_menu)
        nuzlocke_menu = NuzlockeMenu(munchlax, configsave, nuz, rem=rem, bh=bh, bizhawk=bizhawk)
        self.add_widget(nuzlocke_menu)
        pokemon_detail = PokemonDetailScreen(obs_websocket)
        self.add_widget(pokemon_detail)

        # Total-Wipe-Banner-Callback registrieren (Task #17)
        from frontend.widgets.wipe_banner import TotalWipeBanner
        def _open_wipe_banner(_violation):
            def _pick_other():
                self.current = "NuzlockeMenu"
            TotalWipeBanner(munchlax, configsave, bh=bh,
                              on_pick_other=_pick_other,
                              bizhawk=bizhawk, rnd=rnd, pl=pl, rem=rem).open()
        munchlax.on_total_wipe_callback = lambda v: _open_wipe_banner(v)

        # Slot-Kollision: Server hat declared_player_ids abgelehnt weil ein anderer
        # Client denselben Netz-Slot belegt. Popup zeigt betroffene Slots + Konkurrenz.
        # Danach ist der Client bereits disconnected (siehe munchlax.slot_collision-Handler),
        # User muss in Settings die remote_N-Flags anpassen und manuell neu verbinden.
        from kivy.uix.popup import Popup
        from kivy.uix.boxlayout import BoxLayout
        from kivy.uix.label import Label
        from kivy.uix.button import Button
        def _open_slot_collision_popup(requested, conflicts):
            box = BoxLayout(orientation="vertical", padding="10dp", spacing="8dp")
            conflict_text = "\n".join(
                f"- Slot(s) {slots}: bereits belegt von \"{name}\""
                for name, slots in (conflicts or {}).items()
            ) or "(keine Details)"
            box.add_widget(Label(
                text=(
                    f"Verbindung abgelehnt: Netz-Slot bereits vergeben.\n\n"
                    f"Angefordert: {requested}\n{conflict_text}\n\n"
                    f"In Settings unter \"Spieleranzahl\" die remote_N-Flags "
                    f"anpassen, dann neu verbinden."
                ),
                halign="left", valign="top",
            ))
            popup = Popup(title="Slot-Kollision", content=box,
                          size_hint=(0.7, 0.5), auto_dismiss=False)
            close_btn = Button(text="OK", size_hint_y=None, height="40dp",
                                on_press=lambda *_: popup.dismiss())
            box.add_widget(close_btn)
            popup.open()
        munchlax.on_slot_collision_callback = (
            lambda req, conf: Clock.schedule_once(
                lambda dt: _open_slot_collision_popup(req, conf), 0
            )
        )
        self.current = "Update"
        update_menu.check_for_update()

@dataclass
class MutableString(object):
    text: str

    def __repr__(self) -> str:
        return self.text
    def __str__(self) -> str:
        return self.text

class TrackerApp(App):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        Window.bind(on_request_close=self.exit_check)

    def build(self):
        try:
            self.externalIPv4 = requests.get("https://ipinfo.io/ip", timeout=1).text
        except requests.exceptions.Timeout:
            self.externalIPv4 = ""
        command = "(Get-NetIPAddress -AddressFamily IPv6 | Where-Object -Property PrefixOrigin -eq 'Dhcp').IPAddress"
        process = subprocess.Popen(
            ["powershell.exe", command],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        stdout, stderr = process.communicate()
        self.externalIPv6 = stdout.decode()
        self.configsave = MutableString("backend/config/default/")
        self.bh = {}
        with open(f"{self.configsave}bh_config.yml") as file:
            self.bh = yaml.safe_load(file)
        self.obs = {}
        with open(f"{self.configsave}obs_config.yml") as file:
            self.obs = yaml.safe_load(file)
        self.sp = {}
        with open(f"{self.configsave}sprites.yml") as file:
            self.sp = yaml.safe_load(file)
        self.pl = {}
        with open(f"{self.configsave}player.yml") as file:
            self.pl = yaml.safe_load(file)
        self.rem = {}
        with open(f"{self.configsave}remote.yml") as file:
            self.rem = yaml.safe_load(file)
        self.rnd = {}
        with open(f"{self.configsave}randomizer.yml") as file:
            self.rnd = yaml.safe_load(file)
        self.ov = {}
        ov_path = f"{self.configsave}overlay.yml"
        if os.path.exists(ov_path):
            with open(ov_path) as file:
                self.ov = yaml.safe_load(file) or {}
        self.nuz = {}
        nuz_path = f"{self.configsave}nuzlocke.yml"
        if os.path.exists(nuz_path):
            with open(nuz_path) as file:
                self.nuz = yaml.safe_load(file) or {}
        self.session_list = []
        with open(f"{self.configsave}../session_list.yml") as file:
            self.session_list = yaml.safe_load(file)

        self.arceus = Arceus("", self.rem["client_port"], self.rem)
        self.bizhawk = Bizhawk(self.bh["host"], self.bh["port"], self.bh)
        self.bizhawk_instances = []

        self.citra = CitraHandler()

        ip_to_connect = (
            "127.0.0.1" if self.rem["start_server"] else self.rem["server_ip_adresse"]
        )
        port_to_connect = (
            self.rem["client_port"]
            if self.rem["start_server"]
            else self.rem["server_port"]
        )
        self.munchlax = Munchlax(ip_to_connect, port_to_connect, self.rem, self.sp, self.pl, self.configsave, self.nuz)
        # client_id wurde ggf. frisch generiert (Default 0 in initialize_tree) —
        # sofort persistieren, damit auch bei hartem Exit (Crash/Task-Kill) die
        # ID stabil bleibt. exit_check läuft nur bei sauberem Window-Close.
        self.save_config(f"{self.configsave}remote.yml", self.rem)
        self.obs_websocket = OBS(
            self.obs["host"],
            self.obs["port"],
            self.obs["password"],
            self.munchlax,
            self.sp,
            self.obs,
        )

        self.overlay_server = OverlayServer(self.munchlax, self.sp, self.obs, self.ov)
        self.munchlax.overlay_server = self.overlay_server

        arguments = [
            self.arceus,
            self.bizhawk,
            self.citra,
            self.bizhawk_instances,
            self.munchlax,
            self.obs_websocket,
            self.overlay_server,
            self.externalIPv4,
            self.externalIPv6,
            self.configsave,
            self.sp,
            self.rem,
            self.obs,
            self.bh,
            self.pl,
            self.rnd,
            self.ov,
            self.nuz,
            self.session_list,
        ]

        crash_log = 'logs/crash_report.log'
        if os.path.exists(crash_log) and os.path.getsize(crash_log) > 0:
            from frontend.widgets.mainmenu import CrashReportPopup
            Clock.schedule_once(lambda dt: CrashReportPopup(crash_log).open(), 2)

        if not self.sp.get('common_path'):
            from frontend.widgets.sprite_setup_popup import SpriteSetupPopup
            def _save_sprites():
                self.save_config(f"{self.configsave}sprites.yml", self.sp)
            Clock.schedule_once(lambda dt: SpriteSetupPopup(self.sp, self.configsave, save_callback=_save_sprites).open(), 3)
        else:
            from backend.sprite_repo import is_sprite_repo, pull_sprite_repo, get_repo_root_from_subpath
            repo_root = get_repo_root_from_subpath(self.sp['common_path'])
            if repo_root and is_sprite_repo(repo_root):
                asyncio.create_task(self._auto_pull_sprites(repo_root))

        if self.sp.get('obs_2_pc'):
            def _save_sprites_from_helper():
                self.save_config(f"{self.configsave}sprites.yml", self.sp)
            asyncio.create_task(self.arceus.start_helper_listener(
                self.sp, self.configsave, save_callback=_save_sprites_from_helper))

        return Screens(*arguments)

    def exit_check(self, *args, **kwargs):
        self.save_config(f"{self.configsave}bh_config.yml", self.bh)
        self.save_config(f"{self.configsave}obs_config.yml", self.obs)
        self.save_config(f"{self.configsave}sprites.yml", self.sp)
        self.save_config(f"{self.configsave}player.yml", self.pl)
        self.save_config(f"{self.configsave}remote.yml", self.rem)
        self.save_config(f"{self.configsave}randomizer.yml", self.rnd)
        self.save_config(f"{self.configsave}overlay.yml", self.ov)
        self.save_config(f"{self.configsave}nuzlocke.yml", self.nuz)

        for bizhawk in self.bizhawk_instances:
            bizhawk.terminate()
        tasks = [
            asyncio.create_task(self.arceus.stop()),
            asyncio.create_task(self.citra.stop()),
            asyncio.create_task(self.bizhawk.stop()),
            asyncio.create_task(self.obs_websocket.disconnect()),
            asyncio.create_task(self.munchlax.disconnect()),
            asyncio.create_task(self.overlay_server.stop()),
        ]
        asyncio.create_task(asyncio.wait(tasks, timeout=3))

    async def _auto_pull_sprites(self, repo_root: str):
        try:
            from backend.sprite_repo import pull_sprite_repo
            from frontend.widgets.toast import show_toast
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(None, pull_sprite_repo, repo_root)
            # Nur bei tatsächlichem Update Toast — kein Rauschen bei jedem Start.
            if result.success and result.updated:
                show_toast("Sprites aktualisiert", level='success')
        except Exception as err:
            logger.error(f"Auto-Pull der Sprites fehlgeschlagen: {err}")

    def save_config(self, path, setting):
        with open(path, "w") as file:
            yaml.dump(setting, file)
