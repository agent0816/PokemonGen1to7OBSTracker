import asyncio
import os
import traceback
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
from backend.arceus_thread import ArceusThread
from backend.bizhawk_thread import BizhawkThread
from backend.munchlax_thread import MunchlaxThread
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
        # Isolierter Thread + eigener asyncio-Loop fuer den Arceus-Server.
        # Alle Arceus-Coros (start, stop, start_helper_listener,
        # handle_munchlax, check_heartbeats, timer_tick_loop) laufen von jetzt
        # an auf dem ArceusLoop. Kivy-Code postet sie via
        # connection_controller._submit_arc.
        self.arceus_thread = ArceusThread()
        self.arceus_thread.start()

        ip_to_connect = (
            "127.0.0.1" if self.rem["start_server"] else self.rem["server_ip_adresse"]
        )
        port_to_connect = (
            self.rem["client_port"]
            if self.rem["start_server"]
            else self.rem["server_port"]
        )
        # Reihenfolge Phase 3: Munchlax + MunchlaxThread VOR BizhawkThread.
        # BH-Dispatcher (`_dispatch_on_munchlax`) soll Encounter-/Team-Coros
        # auf dem MunchlaxLoop landen lassen, nicht auf Kivy. Dafuer muss
        # der MunchlaxThread laufen BEVOR `self.bizhawk.set_munchlax_loop`
        # ihn als Target einhaengt. Munchlax selbst wird mit
        # `set_kivy_loop` zusaetzlich das OBS/Overlay-Target bekommen.
        self.munchlax = Munchlax(ip_to_connect, port_to_connect, self.rem, self.sp, self.pl, self.configsave, self.nuz)
        self.munchlax_thread = MunchlaxThread()
        self.munchlax_thread.start()
        # get_running_loop() statt get_event_loop(): Kivy's async_run garantiert
        # einen aktiven Loop in build(); get_event_loop ist in Python 3.12+
        # deprecated ausserhalb von Coroutines.
        self.munchlax.set_kivy_loop(asyncio.get_running_loop())
        # Backend-Thread-Ref: Munchlax-interne submit_cross_thread
        # /dispatch_cross_thread brauchen den Thread, um Fremd-Thread-Coros
        # (CitraHandler aus Kivy) auf den eigenen Loop zu posten, ohne
        # kivy.app importieren zu muessen.
        self.munchlax.set_own_thread(self.munchlax_thread)

        self.bizhawk = Bizhawk(self.bh["host"], self.bh["port"], self.bh)
        # Isolierter Thread + eigener asyncio-Loop fuer den BizHawk-Server
        # (siehe commit f78a4ec).
        self.bizhawk_thread = BizhawkThread()
        self.bizhawk_thread.start()
        # BH-Target ist der MunchlaxLoop (nicht mehr Kivy): BH dispatcht
        # Encounter-/Teams-Sync via `_dispatch_on_munchlax` auf diesen Loop,
        # Munchlax ruft dann OBS/Overlay via `_dispatch_on_kivy` ins
        # Kivy-Loop. Zusaetzlich das Thread-Objekt uebergeben, damit BH-
        # Dispatches das `_closing`-Shutdown-Window-Guard des Threads
        # abfragen (R3 WARN).
        self.bizhawk.set_munchlax_loop(self.munchlax_thread.loop)
        self.bizhawk.set_munchlax_thread(self.munchlax_thread)
        self.bizhawk_instances = []

        self.citra = CitraHandler()
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
            from backend.sprite_repo import is_sprite_repo, pull_sprite_repo, get_repo_root_from_subpath
            repo_root = get_repo_root_from_subpath(self.sp['common_path'])
            if repo_root and is_sprite_repo(repo_root):
                asyncio.create_task(self._auto_pull_sprites(repo_root))

        if self.sp.get('obs_2_pc'):
            # save_callback laeuft im arceus-Loop (_handle_helper). sp selbst
            # wird NICHT dort mutiert — stattdessen uebergibt _handle_helper
            # die Payload hier an den Callback, der Mutation + YAML-Write
            # atomar auf den Kivy-Loop verschiebt (Clock.schedule_once).
            # Damit lesen Kivy-Code (UI, save_config) und arceus-Code das
            # Config-Dict nie gleichzeitig.
            def _save_sprites_from_helper(payload: dict):
                def _do(_dt):
                    try:
                        self.sp.update(payload)
                        self.save_config(
                            f"{self.configsave}sprites.yml", self.sp
                        )
                    except Exception as err:
                        logger.error(
                            f"_save_sprites_from_helper failed: "
                            f"{type(err).__name__}: {err}"
                        )
                Clock.schedule_once(_do, 0)
            self.arceus_thread.submit_coro_logged(
                self.arceus.start_helper_listener(
                    self.sp, self.configsave,
                    save_callback=_save_sprites_from_helper),
                name='arceus.start_helper_listener',
            )

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
        # Server-Stops leben auf ihren eigenen Loops — via submit_coro posten
        # und als Futures in die wait()-Liste packen, damit der Shutdown-Grace
        # beide Close-Zeiten mitabwartet.
        bh_stop_fut = self.bizhawk_thread.submit_coro(self.bizhawk.stop())
        bh_stop_awaitable = asyncio.wrap_future(bh_stop_fut)
        arc_stop_fut = self.arceus_thread.submit_coro(self.arceus.stop())
        arc_stop_awaitable = asyncio.wrap_future(arc_stop_fut)
        # Munchlax lebt seit Phase 3 auf eigenem Loop. disconnect() muss
        # dort laufen (sendet client_disconnect-Message vom Munchlax-Loop,
        # wartet auf writer.wait_closed). Reihenfolge: Munchlax zuerst in
        # der Awaitable-Liste, damit sein disconnect-Broadcast raus ist
        # bevor Arceus die Server-Sockets zerreisst.
        mun_disc_fut = self.munchlax_thread.submit_coro(self.munchlax.disconnect())
        mun_disc_awaitable = asyncio.wrap_future(mun_disc_fut)
        tasks = [
            mun_disc_awaitable,
            arc_stop_awaitable,
            asyncio.create_task(self.citra.stop()),
            bh_stop_awaitable,
            asyncio.create_task(self.obs_websocket.disconnect()),
            asyncio.create_task(self.overlay_server.stop()),
        ]
        # Shutdown-Flow kapseln: wait() + Thread-Shutdowns in einem Task,
        # done_callback loggt Exceptions. Ersetzt das alte Fire-and-Forget-
        # create_task-Paar, bei dem Shutdown-Fehler schweigend verschwanden.
        shutdown_task = asyncio.create_task(
            self._run_shutdown(tasks, bh_stop_awaitable, arc_stop_awaitable, mun_disc_awaitable)
        )
        shutdown_task.add_done_callback(self._log_shutdown_result)

    async def _run_shutdown(self, tasks, bh_stop_awaitable, arc_stop_awaitable, mun_disc_awaitable):
        # Worst-Case-Latenz: 3s (asyncio.wait) + 3*3s (mun_disc/bh/arc
        # wait_for) + 3*5s (Thread.join im Executor) = max ~27s bis das
        # Fenster verschwindet, falls alle Pfade voll in ihre Timeouts
        # laufen. Im Normalfall landen alle unter 500ms. Timeouts sind
        # absichtlich grosszuegig, damit haengende Netz-Sockets
        # (writer.wait_closed) nicht gewaltsam abgerissen werden.
        try:
            await asyncio.wait(tasks, timeout=3)
        except Exception as err:
            logger.error(f"Shutdown-wait fehlgeschlagen: {err}")
        for awt, name in [(mun_disc_awaitable, 'munchlax'),
                          (bh_stop_awaitable, 'bizhawk'),
                          (arc_stop_awaitable, 'arceus')]:
            try:
                await asyncio.wait_for(asyncio.shield(awt), timeout=3)
            except asyncio.TimeoutError:
                logger.warning(f"{name}.stop() Timeout — Thread wird trotzdem gestoppt.")
            except Exception as err:
                logger.warning(f"{name}.stop() Fehler ignoriert fuer Shutdown: {err}")
        # Reihenfolge der Thread-Shutdowns: Munchlax zuerst (BH-Dispatcher
        # zielt auf dessen Loop — nach seinem Shutdown prueft BH is_closed
        # und loggt nur, statt zu crashen), dann BH, dann Arceus.
        try:
            await self.munchlax_thread.shutdown()
        except Exception as err:
            logger.error(f"MunchlaxThread-Shutdown fehlgeschlagen: {err}")
        try:
            await self.bizhawk_thread.shutdown()
        except Exception as err:
            logger.error(f"BizhawkThread-Shutdown fehlgeschlagen: {err}")
        try:
            await self.arceus_thread.shutdown()
        except Exception as err:
            logger.error(f"ArceusThread-Shutdown fehlgeschlagen: {err}")

    def _log_shutdown_result(self, task):
        try:
            exc = task.exception()
        except asyncio.CancelledError:
            return
        except Exception:
            exc = None
        if exc is not None:
            logger.error(f"Shutdown-Task Exception: {type(exc).__name__}: {exc}")

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
