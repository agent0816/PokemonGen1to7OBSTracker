"""
KivyMD-Variante der Tracker-App (im Aufbau, Branch feature/kivymd-ui).

Aktivierung: Umgebungsvariable ``TRACKER_UI=md`` vor ``python main.py``.
Ohne Variable startet weiterhin die klassische Kivy-UI (``frontend.app``).

Backend-Start/-Shutdown kommt komplett aus ``TrackerCoreMixin`` — hier lebt
nur UI. Screens werden schrittweise migriert.
"""

from kivy.clock import Clock
from kivy.uix.screenmanager import FadeTransition, ScreenManager
from kivymd.app import MDApp
from kivymd.uix.boxlayout import MDBoxLayout

from backend.logging_setup import get_logger
from frontend.app_core import TrackerCoreMixin
from frontend.widgets.mainmenumd import MainMenu
from frontend.widgets.navbar import TrackerNavigationBar
from frontend.widgets.sessionsmenumd import SessionMenu
from frontend.widgets.settingsmenumd import SettingsMenu
from version import VERSION

logger = get_logger(__name__, 'logs/frontend.log')

APP_NAME = "PokemonOBSTracker"
APP_VERSION = VERSION


def _window_title(session_name: str | None) -> str:
    session = session_name or "Standard-Einstellungen"
    return f"{APP_NAME} {APP_VERSION} (KivyMD-Vorschau) | Session: {session}"


class Screens(ScreenManager):
    def __init__(self, arceus, bizhawk, citra, bizhawk_instances, munchlax, obs_websocket, overlay_server,
                 externalIPv4, externalIPv6, configsave, sp, rem, obs, bh, pl, rnd, ov, nuz, session_list,
                 **kwargs) -> None:
        super().__init__(**kwargs)
        self.transition = FadeTransition()

        self.add_widget(SessionMenu(
            session_list, configsave, sp, rem, obs, bh, pl, rnd, ov, nuz, APP_VERSION,
            on_session_changed=self._on_session_changed,
        ))
        # MainMenu-/SettingsMenu-MD haben noch die Prototyp-Signatur (Stand 0.7.11);
        # MainMenu ist rein optisch, SettingsMenu leer.
        self.main_menu = MainMenu(arceus, bizhawk, citra, bizhawk_instances, munchlax, obs_websocket,
                                  configsave, sp, rem, obs, bh, pl, APP_VERSION)
        self.add_widget(self.main_menu)
        self.add_widget(SettingsMenu(arceus, bizhawk, munchlax, obs_websocket, externalIPv4, externalIPv6,
                                     configsave, sp, rem, obs, bh, pl, APP_VERSION))
        self.current = "MainMenu"

    def _on_session_changed(self, session_name: str | None) -> None:
        # Screens, die Session-Werte anzeigen, hier nachladen (siehe Skill
        # menu-session-wechsel-refresh). MainMenu-/SettingsMenu-MD zeigen
        # noch keine Session-Werte.
        app = MDApp.get_running_app()
        if app is not None:
            app.title = _window_title(session_name)


class MainWindow(MDBoxLayout):
    def __init__(self, *screen_arguments, **kwargs) -> None:
        super().__init__(**kwargs)
        self.orientation = "vertical"
        self.screen_manager = Screens(*screen_arguments)

        navigation_list: list[dict] = [
            {"icon": "view-list", "display_name": "Sessions", "screen_name": "SessionMenu"},
            {"icon": "home-variant-outline", "display_name": "Main", "screen_name": "MainMenu", "active": True},
            {"icon": "cog", "display_name": "Settings", "screen_name": "SettingsMenu"},
        ]
        self.navbar = TrackerNavigationBar(self.screen_manager, navigation_list)

        self.add_widget(self.navbar)
        self.add_widget(self.screen_manager)


class TrackerAppMD(TrackerCoreMixin, MDApp):
    def build(self):
        self.title = _window_title(None)
        self.theme_cls.theme_style_switch_animation = True
        self.theme_cls.theme_style = "Dark"
        self.theme_cls.primary_palette = "Orange"

        self.init_core()
        logger.info("KivyMD-UI gestartet (TRACKER_UI=md)")

        # Crash-Popup + Sprite-Setup vorerst als klassische Kivy-Popups
        # (laufen auch unter MDApp); MD-Varianten folgen mit der Migration.
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

        return MainWindow(*self.screen_arguments())
