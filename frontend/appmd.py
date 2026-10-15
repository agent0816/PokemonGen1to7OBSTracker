"""
KivyMD-Variante der Tracker-App (im Aufbau, Branch feature/kivymd-ui).

Aktivierung: Umgebungsvariable ``TRACKER_UI=md`` vor ``python main.py``.
Ohne Variable startet weiterhin die klassische Kivy-UI (``frontend.app``).

Backend-Start/-Shutdown kommt komplett aus ``TrackerCoreMixin`` — hier lebt
nur UI. Screens werden schrittweise migriert.
"""

import traceback

from kivy.clock import Clock
from kivy.uix.screenmanager import FadeTransition, ScreenManager
from kivymd.app import MDApp
from kivymd.uix.boxlayout import MDBoxLayout

from backend.logging_setup import get_logger
from frontend.app_core import TrackerCoreMixin
from frontend.widgets.mainmenumd import MainMenu
from frontend.widgets.md_common import show_snackbar
from frontend.widgets.navbar import TrackerNavigationBar
from frontend.widgets.sessionsmenumd import SessionMenu
from frontend.widgets.settingsmenumd import SettingsMenu
from frontend.widgets.statusbarmd import StatusBarMD
from frontend.widgets.updatemenu import Update
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

        update_menu = Update(APP_NAME, APP_VERSION)
        self.add_widget(update_menu)

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

        # Sicherheits-Callbacks (Total-Wipe, Wipe-Dismissed, Slot-Kollision) wie
        # in der klassischen UI verdrahten. Banner/Popup vorerst klassisch, bis
        # wipe_banner in Phase E auf MDDialog umgestellt ist.
        self._active_wipe_banner = None
        self._wire_safety_callbacks(munchlax, configsave, bh, bizhawk, rnd, pl, rem)

        # Start auf dem Update-Screen; check_for_update() navigiert danach selbst
        # weiter (SessionMenu bei keinem/fehlgeschlagenem Update, MainMenu bei Abbruch).
        self.current = "Update"
        update_menu.check_for_update()

    def _wire_safety_callbacks(self, munchlax, configsave, bh, bizhawk, rnd, pl, rem) -> None:
        from frontend.widgets.wipe_banner import TotalWipeBanner

        def _open_wipe_banner(violation):
            # Reentrancy-Guard: solange ein Banner offen ist, keine weiteren
            # öffnen (mehrfache Broadcasts derselben Wipe-Session sonst → n
            # gestapelte Popups).
            logger.info(
                f"_open_wipe_banner ausgeloest: type={violation.get('type')} "
                f"subject={violation.get('subject')} "
                f"active={self._active_wipe_banner is not None}"
            )
            existing = self._active_wipe_banner
            if existing is not None and getattr(existing, "_is_open", False):
                logger.info(
                    f"_open_wipe_banner skip (Banner bereits offen): "
                    f"neue violation type={violation.get('type')} "
                    f"subject={violation.get('subject')}"
                )
                return

            def _pick_other():
                # NuzlockeMenu existiert in der MD-UI noch nicht (Phase E).
                if self.has_screen("NuzlockeMenu"):
                    self.current = "NuzlockeMenu"
                else:
                    logger.info("_open_wipe_banner: NuzlockeMenu in MD-UI noch nicht verfügbar")
                    show_snackbar("Ersatz-Pokemon wählen ist in der MD-Vorschau noch nicht verfügbar.",
                                  level='info', duration=6.0)

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

        # Clock-wrap: Munchlax ruft Callback aus seinem eigenen Thread (Phase 3).
        munchlax.on_total_wipe_callback = (
            lambda v: Clock.schedule_once(lambda dt: _open_wipe_banner(v), 0)
        )

        # Wipe-Dismissed: anderes Team-Mitglied hat sein Wipe-Popup per Cancel
        # geschlossen (coop/versus). Unser offenes Popup ebenfalls schließen.
        def _dismiss_wipe_banner(_subject_pid):
            banner = self._active_wipe_banner
            if banner is not None and getattr(banner, "_is_open", False):
                banner.dismiss()
        munchlax.on_wipe_dismissed_callback = (
            lambda pid: Clock.schedule_once(lambda dt: _dismiss_wipe_banner(pid), 0)
        )

        # Slot-Kollision: Server hat declared_player_ids abgelehnt, weil ein
        # anderer Client denselben Netz-Slot belegt. Client ist danach bereits
        # disconnected; User muss remote_N-Flags anpassen und neu verbinden.
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
        # Globale Fußzeile mit Verbindungsstatus (holt sich die App selbst)
        self.status_bar = StatusBarMD()
        self.add_widget(self.status_bar)


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
