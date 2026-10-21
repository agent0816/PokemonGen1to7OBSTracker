import traceback
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

# from kivy.logger import Logger
# from kivy.logger import LOG_LEVELS
# Logger.setLevel(logging.INFO)
from kivy.uix.screenmanager import FadeTransition
from kivy.uix.screenmanager import ScreenManager
from frontend.app_core import TrackerCoreMixin
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
        nuzlocke_menu = NuzlockeMenu(munchlax, configsave, nuz, rem=rem, bh=bh, bizhawk=bizhawk, rnd=rnd)
        self.add_widget(nuzlocke_menu)
        pokemon_detail = PokemonDetailScreen(obs_websocket)
        self.add_widget(pokemon_detail)

        # Total-Wipe-Banner-Callback registrieren (Task #17)
        from frontend.widgets.wipe_banner import TotalWipeBanner
        self._active_wipe_banner = None
        def _open_wipe_banner(violation):
            # Reentrancy-Guard: solange ein Banner offen ist, keine weiteren
            # öffnen (mehrfache Broadcasts derselben Wipe-Session sonst → n
            # gestapelte Popups). Wenn User dismissed, wird die Referenz
            # freigegeben und ein neuer Wipe kann wieder ein Banner öffnen.
            logger.info(
                f"_open_wipe_banner ausgeloest: type={violation.get('type')} "
                f"subject={violation.get('subject')} "
                f"active={self._active_wipe_banner is not None}"
            )
            existing = self._active_wipe_banner
            if existing is not None and getattr(existing, "_is_open", False):
                # Log so dass Multi-Team-Wipes im Versus-Modus nachvollziehbar
                # sind: eine zweite Violation (z.B. anderes Team) wird stumm
                # verworfen, weil das erste Banner noch offen ist. User muss
                # das erste Popup dismissen, um das zweite zu sehen.
                logger.info(
                    f"_open_wipe_banner skip (Banner bereits offen): "
                    f"neue violation type={violation.get('type')} "
                    f"subject={violation.get('subject')}"
                )
                return
            def _pick_other():
                self.current = "NuzlockeMenu"
            try:
                banner = TotalWipeBanner(munchlax, configsave, bh=bh,
                                  on_pick_other=_pick_other,
                                  bizhawk=bizhawk, rnd=rnd, pl=pl, rem=rem,
                                  violation=violation)
            except Exception as err:
                logger.error(f"_open_wipe_banner: Banner-Konstruktor fehlgeschlagen: {err}")
                logger.error(traceback.format_exc())
                return
            banner._is_open = True
            def _on_dismiss(*_):
                banner._is_open = False
                if self._active_wipe_banner is banner:
                    self._active_wipe_banner = None
                logger.info("_open_wipe_banner: Banner dismissed")
            banner.bind(on_dismiss=_on_dismiss)
            self._active_wipe_banner = banner
            try:
                banner.open()
                logger.info("_open_wipe_banner: banner.open() erfolgreich aufgerufen")
            except Exception as err:
                logger.error(f"_open_wipe_banner: banner.open() failed: {err}")
                logger.error(traceback.format_exc())
        # Clock-wrap: Munchlax ruft Callback aus seinem eigenen Thread
        # (Phase 3). Clock.schedule_once ist thread-safe und bringt die
        # UI-Mutation (Popup.open) sicher auf den Kivy-Main-Thread.
        munchlax.on_total_wipe_callback = (
            lambda v: Clock.schedule_once(lambda dt: _open_wipe_banner(v), 0)
        )

        # Wipe-Dismissed-Callback: anderer Team-Mitglied hat sein Wipe-Popup
        # per Cancel geschlossen (nur coop/versus). Wenn unser Popup noch
        # offen ist, ebenfalls schliessen — konsistente Team-UI.
        def _dismiss_wipe_banner(_subject_pid):
            banner = self._active_wipe_banner
            if banner is not None and getattr(banner, "_is_open", False):
                banner.dismiss()
        munchlax.on_wipe_dismissed_callback = (
            lambda pid: Clock.schedule_once(lambda dt: _dismiss_wipe_banner(pid), 0)
        )

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

class TrackerApp(TrackerCoreMixin, App):
    def build(self):
        # Backend-Start (Configs, Threads, Server) liegt in TrackerCoreMixin,
        # damit die KivyMD-App exakt denselben Ablauf nutzt.
        self.init_core()
        # Klassische UI: öffentliche IPs blockierend laden, bevor die Screens
        # gebaut werden (sie bekommen die Werte als Konstruktor-Argumente).
        # Die KivyMD-UI lädt sie stattdessen asynchron während des Splash-Boots.
        self.load_external_ips_sync()

        crash_log = 'logs/crash_report.log'
        from frontend.widgets.mainmenu import CrashReportPopup, should_show_crash_popup
        if should_show_crash_popup(crash_log):
            Clock.schedule_once(
                lambda dt: CrashReportPopup(
                    crash_log,
                    configsave=self.configsave,
                    pl=self.pl,
                    rem=self.rem,
                ).open(),
                2,
            )

        if not self.sp.get('common_path'):
            from frontend.widgets.sprite_setup_popup import SpriteSetupPopup
            def _save_sprites():
                self.save_config(f"{self.configsave}sprites.yml", self.sp)
            Clock.schedule_once(lambda dt: SpriteSetupPopup(self.sp, self.configsave, save_callback=_save_sprites).open(), 3)
        else:
            self.start_sprite_auto_pull()

        self.start_sprite_helper_listener()

        return Screens(*self.screen_arguments())
