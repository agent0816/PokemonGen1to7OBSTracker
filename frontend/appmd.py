"""
KivyMD-Variante der Tracker-App (im Aufbau, Branch feature/kivymd-ui).

Aktivierung: Umgebungsvariable ``TRACKER_UI=md`` vor ``python main.py``.
Ohne Variable startet weiterhin die klassische Kivy-UI (``frontend.app``).

Backend-Start/-Shutdown kommt komplett aus ``TrackerCoreMixin`` — hier lebt
nur UI. Screens werden schrittweise migriert.
"""

import asyncio
import traceback

from kivy.clock import Clock
from kivy.metrics import dp
from kivy.uix.screenmanager import FadeTransition, ScreenManager
from kivymd.app import MDApp
from kivymd.uix.boxlayout import MDBoxLayout

from backend import app_state
from backend.logging_setup import get_logger
from frontend.app_core import TrackerCoreMixin
from frontend.widgets.md_common import show_snackbar
from frontend.widgets.navrail import TrackerNavigationRail
from frontend.widgets.splashmd import SplashScreen
from frontend.widgets.statusbarmd import StatusBarMD
from version import VERSION

# Die eigentlichen Screen-Module (MainMenu/Settings/Pokedex/…/PokemonDetail)
# werden NICHT auf Modulebene importiert: ihr Import kostet ~2,6s (v.a.
# pokemon_detailmd mit seinen YAML-LUTs) und liefe beim Start vor dem ersten
# Frame = langer Schwarz-Screen. Sie werden lokal in `Screens.__init__`
# importiert, das erst hinter dem Splash (nach dem ersten Frame) läuft.

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
        # Lokale Imports (siehe Modul-Kopf): schwere Screen-Module erst hier
        # laden, nach dem ersten Frame hinter dem Splash.
        from frontend.widgets.bagmenumd import BagMenu
        from frontend.widgets.boxmenumd import BoxMenu
        from frontend.widgets.encountermenumd import EncounterMenu
        from frontend.widgets.mainmenumd import MainMenu
        from frontend.widgets.mapmenumd import MapMenu
        from frontend.widgets.nuzlockemenu_md import NuzlockeMenu
        from frontend.widgets.onboarding_md import OnboardingScreen
        from frontend.widgets.pokedexmenumd import PokedexMenu
        from frontend.widgets.pokemon_detailmd import PokemonDetailMD
        from frontend.widgets.sessionsmenumd import SessionMenu
        from frontend.widgets.settingsmenumd import SettingsMenu
        from frontend.widgets.updatemenu import Update

        self.transition = FadeTransition()
        # ScreenManager ist ein FloatLayout und positioniert Screens ohne
        # pos_hint nur beim current-Wechsel (real_add_widget: screen.pos =
        # self.pos). Hier wird SessionMenu schon in __init__ current, bevor der
        # Manager seine Position bekommt (eingebettet rechts neben der Rail) —
        # der Screen bliebe sonst bei (0,0) und renderte über der Rail. Daher
        # den aktiven Screen an die Manager-Position koppeln.
        self.bind(pos=self._align_active_screen)

        # Ladescreen (Phase F3): sofort sichtbar, während die Boot-Sequenz die
        # blockierenden Schritte (öffentliche IPs, Update-Check) im Executor
        # abarbeitet. Zuerst hinzugefügt, damit er der Start-Screen sein kann.
        self.splash = SplashScreen()
        self.add_widget(self.splash)

        self.update_menu = Update(APP_NAME, APP_VERSION)
        self.add_widget(self.update_menu)
        # Strong-Refs auf laufende Boot-Tasks (sonst GC-Risiko, siehe
        # feedback_async_task_strong_ref).
        self._boot_tasks: set = set()

        self.add_widget(SessionMenu(
            session_list, configsave, sp, rem, obs, bh, pl, rnd, ov, nuz, APP_VERSION,
            on_session_changed=self._on_session_changed,
        ))
        # MainMenu-MD voll verdrahtet (Phase C), SettingsMenu-MD (Phase D).
        self.main_menu = MainMenu(arceus, bizhawk, citra, bizhawk_instances, munchlax, obs_websocket,
                                  overlay_server, configsave, sp, rem, obs, bh, pl, rnd, ov, APP_VERSION)
        self.add_widget(self.main_menu)
        self.settings_menu = SettingsMenu(arceus, bizhawk, munchlax, obs_websocket, overlay_server,
                                          externalIPv4, externalIPv6, configsave, sp, rem, obs, bh, pl,
                                          rnd, ov, nuz, APP_VERSION)
        self.add_widget(self.settings_menu)
        self.pokedex_menu = PokedexMenu(configsave, obs_websocket, APP_VERSION, pl)
        self.add_widget(self.pokedex_menu)
        self.encounter_menu = EncounterMenu(configsave, pl, nuz, APP_VERSION)
        self.add_widget(self.encounter_menu)
        self.box_menu = BoxMenu(bizhawk, citra, munchlax, obs_websocket, pl)
        self.add_widget(self.box_menu)
        self.bag_menu = BagMenu(configsave, pl, sp, bizhawk, citra, APP_VERSION)
        self.add_widget(self.bag_menu)
        self.map_menu = MapMenu(munchlax, obs_websocket, pl, configsave)
        self.add_widget(self.map_menu)
        self.nuzlocke_menu = NuzlockeMenu(munchlax, configsave, nuz, rem, bh, bizhawk, rnd)
        self.add_widget(self.nuzlocke_menu)
        # Detail-Drilldown (kein Rail-Ziel): aus Pokedex per Karten-Klick erreicht,
        # navigiert über return_screen zurück. Box/Main nutzen ihn später mit.
        self.pokemon_detail = PokemonDetailMD(obs_websocket)
        self.add_widget(self.pokemon_detail)

        # First-Startup-Walkthrough (Phase F4, kein Rail-Ziel). Wird nur beim
        # allerersten Start angesteuert (app_state.onboarding_done), schreibt in
        # die Standard-Session und setzt am Ende den Marker.
        self.onboarding = OnboardingScreen(configsave, sp, pl, bh, obs, APP_VERSION,
                                           on_complete=self._finish_onboarding)
        self.add_widget(self.onboarding)

        # Sicherheits-Callbacks (Total-Wipe, Wipe-Dismissed, Slot-Kollision) wie
        # in der klassischen UI verdrahten. Banner + Slot-Kollision sind seit
        # Phase E MDDialog (wipe_banner_md / md_common.confirm_dialog).
        self._active_wipe_banner = None
        self._wire_safety_callbacks(munchlax, configsave, bh, bizhawk, rnd, pl, rem)

        # Start auf dem Ladescreen; die Boot-Sequenz lädt IPs + Update-Check
        # nicht-blockierend und navigiert danach weiter (Update-Popup bzw.
        # SessionMenu/MainMenu). Erst nach erstem Frame starten (Clock,0), damit
        # der Splash garantiert sichtbar ist, bevor die Arbeit losläuft.
        self.current = "Splash"
        Clock.schedule_once(lambda dt: self._start_boot_sequence(), 0)

    def _start_boot_sequence(self) -> None:
        task = asyncio.create_task(self._boot_sequence())
        self._boot_tasks.add(task)
        task.add_done_callback(self._on_boot_task_done)

    def _on_boot_task_done(self, task) -> None:
        self._boot_tasks.discard(task)
        if task.cancelled():
            return
        err = task.exception()
        if err is not None:
            logger.error(f"Boot-Sequenz-Task mit Fehler beendet: {err}", exc_info=err)

    async def _boot_sequence(self) -> None:
        """Nicht-blockierender Start: öffentliche IPs + Update-Check, danach
        Navigation weg vom Splash."""
        app = MDApp.get_running_app()
        try:
            self.splash.set_status("Lade Netzwerk-Infos …")
            if app is not None:
                v4, v6 = await app.load_external_ips_async()
                # Settings-Screen hier (im UI-Layer) nachziehen, der Mixin
                # bleibt UI-frei (Review-Hinweis F3).
                try:
                    self.settings_menu.set_public_ips(v4, v6)
                except Exception as err:
                    logger.error(f"set_public_ips fehlgeschlagen: {err}")
                    logger.error(traceback.format_exc())
        except Exception as err:
            logger.error(f"IP-Laden im Boot fehlgeschlagen: {err}")
            logger.error(traceback.format_exc())
        # Beim allerersten Start nach dem Update-Check in den Walkthrough statt
        # ins SessionMenu (app_state.onboarding_done, Phase F4).
        try:
            first_run = not app_state.is_onboarding_done()
        except Exception as err:
            logger.error(f"Onboarding-Status nicht lesbar: {err}")
            first_run = False
        no_update_target = "Onboarding" if first_run else "SessionMenu"
        try:
            self.splash.set_status("Suche nach Updates …")
            await self.update_menu.check_for_update_async(no_update_target=no_update_target)
        except Exception as err:
            logger.error(f"Update-Check im Boot fehlgeschlagen: {err}")
            logger.error(traceback.format_exc())
            # Notfall-Navigation, nur wenn der User nicht schon weg ist (Rail
            # ist während Splash gesperrt, aber doppelte/überschreibende Nav
            # trotzdem vermeiden).
            if self.current == "Splash":
                self.current = no_update_target

    def _finish_onboarding(self) -> None:
        """Vom OnboardingScreen am Ende/Überspringen gerufen: Marker setzen und
        ins Hauptmenü. Session-Hook triggern, damit die Screens die frisch
        gesetzten Standard-Session-Werte übernehmen."""
        try:
            app_state.set_onboarding_done(True)
        except Exception as err:
            logger.error(f"onboarding_done konnte nicht gesetzt werden: {err}")
            logger.error(traceback.format_exc())
        try:
            self._on_session_changed(None)
        except Exception as err:
            logger.error(f"Session-Refresh nach Onboarding fehlgeschlagen: {err}")
            logger.error(traceback.format_exc())
        self.current = "MainMenu"

    def _align_active_screen(self, _manager, pos) -> None:
        # Aktiven Screen an die (neue) Manager-Position heften. FloatLayout lässt
        # pos-hint-lose Kinder beim Relayout unangetastet, daher hier explizit.
        if self.current_screen is not None:
            self.current_screen.pos = pos

    def _wire_safety_callbacks(self, munchlax, configsave, bh, bizhawk, rnd, pl, rem) -> None:
        from frontend.widgets.wipe_banner_md import TotalWipeBannerMD

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
                # NuzlockeMenu ist seit Phase E ein Rail-Ziel.
                if self.has_screen("NuzlockeMenu"):
                    self.current = "NuzlockeMenu"
                else:
                    logger.info("_open_wipe_banner: NuzlockeMenu nicht verfügbar")
                    show_snackbar("Ersatz-Pokemon wählen ist noch nicht verfügbar.",
                                  level='info', duration=6.0)

            def _on_dismiss():
                if self._active_wipe_banner is banner:
                    self._active_wipe_banner = None
                logger.info("_open_wipe_banner: Banner dismissed")

            try:
                banner = TotalWipeBannerMD(munchlax, configsave, bh=bh,
                                           on_pick_other=_pick_other,
                                           bizhawk=bizhawk, rnd=rnd, pl=pl, rem=rem,
                                           violation=violation, on_dismiss=_on_dismiss)
            except Exception as err:
                logger.error(f"_open_wipe_banner: Banner-Konstruktor fehlgeschlagen: {err}")
                logger.error(traceback.format_exc())
                return
            self._active_wipe_banner = banner
            try:
                banner.open()
                logger.info("_open_wipe_banner: banner.open() erfolgreich aufgerufen")
            except Exception as err:
                # Reentrancy-Guard wieder freigeben, sonst blockiert ein
                # fehlgeschlagenes open() alle weiteren Wipe-Banner.
                if self._active_wipe_banner is banner:
                    self._active_wipe_banner = None
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
        from frontend.widgets.md_common import confirm_dialog

        def _open_slot_collision_popup(requested, conflicts):
            conflict_text = "\n".join(
                f"- Slot(s) {slots}: bereits belegt von \"{name}\""
                for name, slots in (conflicts or {}).items()
            ) or "(keine Details)"
            confirm_dialog(
                "Slot-Kollision",
                f"Verbindung abgelehnt: Netz-Slot bereits vergeben.\n\n"
                f"Angefordert: {requested}\n{conflict_text}\n\n"
                f"In Settings unter \"Spieleranzahl\" die remote_N-Flags anpassen, "
                f"dann neu verbinden.",
                on_confirm=lambda: None, confirm_text="OK", cancel_text=None)
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
        try:
            self.main_menu.load_from_session()
        except Exception as err:
            logger.error(f"MainMenu.load_from_session fehlgeschlagen: {err}")
            logger.error(traceback.format_exc())
        try:
            self.pokedex_menu.load_from_session()
        except Exception as err:
            logger.error(f"PokedexMenu.load_from_session fehlgeschlagen: {err}")
            logger.error(traceback.format_exc())
        try:
            self.encounter_menu.load_from_session()
        except Exception as err:
            logger.error(f"EncounterMenu.load_from_session fehlgeschlagen: {err}")
            logger.error(traceback.format_exc())
        try:
            self.box_menu.load_from_session()
        except Exception as err:
            logger.error(f"BoxMenu.load_from_session fehlgeschlagen: {err}")
            logger.error(traceback.format_exc())
        try:
            self.bag_menu.load_from_session()
        except Exception as err:
            logger.error(f"BagMenu.load_from_session fehlgeschlagen: {err}")
            logger.error(traceback.format_exc())
        try:
            self.map_menu.load_from_session()
        except Exception as err:
            logger.error(f"MapMenu.load_from_session fehlgeschlagen: {err}")
            logger.error(traceback.format_exc())
        try:
            self.settings_menu.load_from_session()
        except Exception as err:
            logger.error(f"SettingsMenu.load_from_session fehlgeschlagen: {err}")
            logger.error(traceback.format_exc())
        try:
            self.nuzlocke_menu.load_from_session()
        except Exception as err:
            logger.error(f"NuzlockeMenu.load_from_session fehlgeschlagen: {err}")
            logger.error(traceback.format_exc())


class MainWindow(MDBoxLayout):
    def __init__(self, *screen_arguments, **kwargs) -> None:
        super().__init__(**kwargs)
        self.orientation = "vertical"
        self.screen_manager = Screens(*screen_arguments)

        # Linke NavigationRail statt Bottom-NavBar: skaliert auf die ab Phase B
        # dazukommenden Screens (Pokedex/Box/Bag/Encounter/Nuzlocke). Neue Ziele
        # werden hier als weiterer Eintrag ergänzt.
        navigation_list: list[dict] = [
            {"icon": "view-list", "display_name": "Sessions", "screen_name": "SessionMenu"},
            {"icon": "home-variant-outline", "display_name": "Main", "screen_name": "MainMenu"},
            {"icon": "book-open-variant", "display_name": "Pokedex", "screen_name": "PokedexMenu"},
            {"icon": "clipboard-list-outline", "display_name": "Encounter", "screen_name": "EncounterMenu"},
            {"icon": "archive-outline", "display_name": "Box", "screen_name": "BoxMenu"},
            {"icon": "bag-personal-outline", "display_name": "Tasche", "screen_name": "BagMenu"},
            {"icon": "map-outline", "display_name": "Karte", "screen_name": "MapMenu"},
            {"icon": "skull-outline", "display_name": "Nuzlocke", "screen_name": "NuzlockeMenu"},
            {"icon": "cog", "display_name": "Settings", "screen_name": "SettingsMenu"},
        ]
        self.navrail = TrackerNavigationRail(
            self.screen_manager, navigation_list, nav_guard=self._nav_guard,
            size_hint_x=None, width=dp(88),
        )

        # Rumpf: Rail links, ScreenManager rechts. Status-Leiste als Fußzeile
        # darunter über die volle Breite.
        body = MDBoxLayout(orientation="horizontal")
        body.add_widget(self.navrail)
        body.add_widget(self.screen_manager)

        self.status_bar = StatusBarMD()
        self.add_widget(body)
        self.add_widget(self.status_bar)

    def _nav_guard(self, current: str, target: str) -> bool:
        """Rail-Navigations-Guard: ein Wechsel zum SessionMenu bei laufender
        Verbindung/BizHawk läuft über den sicheren Pfad von MainMenu
        (Save-Dialog → disconnect_all → save → Nav), statt direkt zu springen.
        Gibt False zurück (Guard übernimmt die Navigation selbst)."""
        # Während Ladescreen/Onboarding keine Rail-Navigation zulassen — die
        # Boot-Sequenz navigiert selbst weg (Nav-Race, Review F3), und der
        # Walkthrough soll über seine eigenen Buttons verlassen werden, damit
        # der onboarding_done-Marker gesetzt wird (sonst Dauer-Schleife).
        if current in ("Splash", "Onboarding"):
            return False
        if target == "SessionMenu" and current not in ("SessionMenu", "Update"):
            main_menu = getattr(self.screen_manager, "main_menu", None)
            if (main_menu is not None and hasattr(main_menu, "_needs_safe_switch")
                    and main_menu._needs_safe_switch()):
                main_menu.change_session()
                return False
        return True


class TrackerAppMD(TrackerCoreMixin, MDApp):
    def build(self):
        self.title = _window_title(None)
        self.theme_cls.theme_style_switch_animation = True
        self.theme_cls.theme_style = "Dark"
        self.theme_cls.primary_palette = "Orange"

        # Sofort einen minimalen Root mit Splash zurückgeben, damit der erste
        # Frame schnell rendert (statt langem Schwarz-Screen). Der teure Rest
        # — Backend-Start (init_core) und das Konstruieren aller Screens samt
        # deren Imports — läuft erst nach dem ersten Frame in `_populate`,
        # hinter dem sichtbaren Splash.
        self._root_container = MDBoxLayout(orientation="vertical")
        self._boot_splash = SplashScreen()
        self._root_container.add_widget(self._boot_splash)
        # Zwei Frames warten: der erste Clock-Tick liegt noch vor dem ersten
        # on_draw, d.h. der Splash ist evtl. noch nicht gezeichnet. Erst im
        # zweiten Frame ist er sicher sichtbar — dann darf _populate den
        # nativen Nuitka-Splash abbauen und blockierend laden, ohne dass
        # dazwischen ein leeres Fenster aufblitzt.
        Clock.schedule_once(lambda dt: Clock.schedule_once(self._populate, 0), 0)
        return self._root_container

    def _populate(self, _dt):
        # Der eigene Splash ist jetzt sichtbar (erster Frame ist durch) — den
        # nativen Nuitka-Onefile-Splash abbauen (no-op im Dev-Lauf).
        from backend.nuitka_splash import dismiss_splash
        dismiss_splash()
        try:
            self.init_core()
            logger.info("KivyMD-UI gestartet (TRACKER_UI=md)")

            # Crash-Popup als MD-Variante (crash_popup_md); das Sprite-Setup
            # nutzt ebenfalls die MD-Variante (SpriteSetupPopupMD, s. unten).
            crash_log = 'logs/crash_report.log'
            from frontend.widgets.crash_popup_md import CrashReportPopupMD, should_show_crash_popup
            if should_show_crash_popup(crash_log):
                Clock.schedule_once(
                    lambda dt: CrashReportPopupMD(
                        crash_log,
                        configsave=self.configsave,
                        pl=self.pl,
                        rem=self.rem,
                    ).open(),
                    2,
                )

            if self.sp.get('common_path'):
                self.start_sprite_auto_pull()
            elif app_state.is_onboarding_done():
                # Sprites fehlen, aber kein Onboarding mehr (Bestands-Session):
                # klassisches Setup-Popup. Beim Erststart NICHT öffnen — der
                # Walkthrough richtet die Sprites selbst ein, sonst stünden zwei
                # konkurrierende Clone-Pfade auf demselben sp-Dict.
                from frontend.widgets.sprite_setup_popupmd import SpriteSetupPopupMD
                def _save_sprites():
                    self.save_config(f"{self.configsave}sprites.yml", self.sp)
                Clock.schedule_once(lambda dt: SpriteSetupPopupMD(self.sp, self.configsave, save_callback=_save_sprites).open(), 3)

            self.start_sprite_helper_listener()

            # Volles Fenster bauen (konstruiert alle Screens, startet die
            # Boot-Sequenz) und den Boot-Splash ersetzen.
            main_window = MainWindow(*self.screen_arguments())
            self._root_container.clear_widgets()
            self._root_container.add_widget(main_window)
        except Exception as err:
            logger.error(f"App-Start (_populate) fehlgeschlagen: {err}")
            logger.error(traceback.format_exc())
            # Das Backend (Threads, Server) kann zu diesem Zeitpunkt schon
            # laufen. Es bleibt bewusst stehen, damit kein halber Teardown
            # mitten im Fehlerpfad passiert. Der Splash zeigt den Fehler; das
            # Schließen des Fensters läuft dann sauber über exit_check
            # (bizhawk_thread ist gesetzt → voller Shutdown).
            try:
                self._boot_splash.set_status("Startfehler — siehe Log.")
            except Exception:
                pass
