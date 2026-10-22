"""
Gemeinsamer Backend-Start/-Shutdown für alle Frontend-Varianten.

`TrackerCoreMixin` kapselt alles, was nicht UI ist: Session-Configs laden,
Backend-Objekte + isolierte Threads in der richtigen Reihenfolge starten,
Sprite-Helper-Listener anwerfen und den Shutdown sauber durchziehen.
Die Kivy-App (`frontend.app.TrackerApp`) und die KivyMD-App mischen das ein
und bauen nur noch ihre eigenen Screens/Popups.

Die Attribute landen bewusst direkt auf der App-Instanz (`self.munchlax`,
`self.bizhawk_thread`, …), weil Widgets per `App.get_running_app()` darauf
zugreifen (bh_dispatch.py, mainmenu.py, settingsmenu.py, nuzlockemenu.py).
"""

import asyncio
import os
import subprocess
import traceback
from dataclasses import dataclass
from typing import Any

import requests
import yaml
from kivy.clock import Clock
from kivy.core.window import Window

from backend.logging_setup import get_logger

# Die Backend-Klassen (Arceus/Munchlax/Bizhawk/OBS/…) werden bewusst NICHT auf
# Modulebene importiert: ihr Import kostet ~1,2s und würde beim Start vor dem
# ersten Fenster-Frame laufen (schwarzer Screen). Sie werden lokal in
# `_start_backend` importiert, das die KivyMD-App erst hinter dem Splash aufruft.

logger = get_logger(__name__, 'logs/frontend.log')


@dataclass
class MutableString(object):
    text: str

    def __repr__(self) -> str:
        return self.text
    def __str__(self) -> str:
        return self.text


class TrackerCoreMixin:
    """Backend-Lebenszyklus für Kivy-/KivyMD-App. Vor `App` bzw. `MDApp` erben."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        Window.bind(on_request_close=self.exit_check)

    # --- Start --------------------------------------------------------------

    def init_core(self) -> None:
        """Aus `build()` aufrufen: Configs + Backend in fester Reihenfolge.

        Die öffentlichen IPs werden hier NICHT mehr geladen (ipinfo-HTTP +
        PowerShell blockieren den Main-Thread ~1–2s). Sie starten mit leeren
        Defaults; die App lädt sie danach nach:
          - klassische UI blockierend via `load_external_ips_sync()` (build),
          - KivyMD-UI nicht-blockierend via `load_external_ips_async()`
            während der Splash-Boot-Sequenz (Phase F3).
        """
        self.externalIPv4 = ""
        self.externalIPv6 = ""
        self._load_session_configs()
        self._start_backend()

    def _fetch_external_ips(self) -> tuple[str, str]:
        """Blockierende Ermittlung der öffentlichen IPv4/IPv6. Reiner Worker
        ohne Seiteneffekt auf `self` — läuft sync (Klassik) oder im Executor
        (MD). Fehler → leerer String, damit der Boot nicht scheitert."""
        try:
            v4 = requests.get("https://ipinfo.io/ip", timeout=1).text
        except requests.exceptions.Timeout:
            v4 = ""
        except Exception as err:
            logger.warning(f"Öffentliche IPv4 konnte nicht ermittelt werden: {err}")
            v4 = ""
        v6 = ""
        process = None
        try:
            command = "(Get-NetIPAddress -AddressFamily IPv6 | Where-Object -Property PrefixOrigin -eq 'Dhcp').IPAddress"
            # CREATE_NO_WINDOW: kein kurz aufblitzendes Konsolenfenster.
            # timeout: Get-NetIPAddress kann bei Netzproblemen hängen — ohne
            # Timeout bliebe beim App-Schließen während des Boots ein nicht-
            # daemon Executor-Thread hängen (Interpreter joint ihn beim Exit).
            process = subprocess.Popen(
                ["powershell.exe", command],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            stdout, _stderr = process.communicate(timeout=5)
            v6 = stdout.decode()
        except subprocess.TimeoutExpired:
            logger.warning("PowerShell-IPv6-Abfrage lief in den Timeout — abgebrochen.")
            try:
                process.kill()
                process.communicate()
            except Exception:
                pass
            v6 = ""
        except Exception as err:
            logger.warning(f"Öffentliche IPv6 konnte nicht ermittelt werden: {err}")
            v6 = ""
        return v4, v6

    def load_external_ips_sync(self) -> None:
        """Blockierend laden (klassische UI: muss vor Screen-Konstruktion stehen)."""
        self.externalIPv4, self.externalIPv6 = self._fetch_external_ips()

    async def load_external_ips_async(self) -> tuple[str, str]:
        """Nicht-blockierend laden (KivyMD-Boot): Worker im Executor, Ergebnis
        auf die App schreiben und zurückgeben. Das Nachziehen des Settings-
        Screens erledigt der Aufrufer (hält den Mixin UI-frei)."""
        loop = asyncio.get_running_loop()
        try:
            v4, v6 = await loop.run_in_executor(None, self._fetch_external_ips)
        except Exception as err:
            logger.error(f"load_external_ips_async fehlgeschlagen: {err}")
            v4, v6 = "", ""
        self.externalIPv4, self.externalIPv6 = v4, v6
        return v4, v6

    def _load_session_configs(self) -> None:
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

    def _start_backend(self) -> None:
        # Lokale Imports (siehe Modul-Kopf): Backend-Klassen erst hier laden,
        # damit ihr Import nicht den Start vor dem ersten Frame blockiert.
        from backend.arceus_thread import ArceusThread
        from backend.bizhawk_thread import BizhawkThread
        from backend.classes.arceus import Arceus
        from backend.classes.bizhawk import Bizhawk
        from backend.classes.citrahandler import CitraHandler
        from backend.classes.munchlax import Munchlax
        from backend.classes.obs import OBS
        from backend.classes.overlay_server import OverlayServer
        from backend.munchlax_thread import MunchlaxThread

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

    def screen_arguments(self) -> list:
        """Positions-Argumente für den ScreenManager (Reihenfolge = Screens.__init__)."""
        return [
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

    # --- Sprites ------------------------------------------------------------

    def start_sprite_auto_pull(self) -> None:
        """Sprite-Repo im Hintergrund pullen, falls `common_path` ein Sprite-Repo ist."""
        from backend.sprite_repo import is_sprite_repo, get_repo_root_from_subpath
        repo_root = get_repo_root_from_subpath(self.sp['common_path'])
        if repo_root and is_sprite_repo(repo_root):
            asyncio.create_task(self._auto_pull_sprites(repo_root))

    def start_sprite_helper_listener(self) -> None:
        """Helper-Listener für obs_2_pc starten (no-op, wenn nicht konfiguriert)."""
        if not self.sp.get('obs_2_pc'):
            return
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

    # --- Shutdown -----------------------------------------------------------

    def _spawn_relaunch_process(self) -> None:
        """Startet einen frischen App-Prozess mit denselben Argumenten.

        Source-Run: ``sys.argv[0]`` ist das Skript (``main.py``) und
        ``sys.executable`` der Interpreter → ``[python, main.py, …]``.
        Nuitka-Onefile: ``sys.argv[0]`` ist die PERSISTENTE Launcher-EXE, die
        direkt wieder gestartet wird. ``sys.executable`` zeigt dort auf die ins
        Temp entpackte Binary, die der Bootstrapper beim Beenden wegräumt — die
        darf NICHT als Startpfad dienen. Zusätzlich werden die
        ``NUITKA_ONEFILE*``-Env-Variablen aus der Kind-Env entfernt, damit das
        Kind eine frische Onefile-Umgebung bekommt (sonst zielte u.a. der
        Splash-Dismiss auf die alte Parent-PID). ``TRACKER_UI`` wird ebenfalls
        entfernt: der Relaunch dient dem UI-Wechsel, und ein gesetztes
        ``TRACKER_UI`` würde (es hat Vorrang vor app_state) den gerade
        gewählten Modus wieder überschreiben.

        Der Onefile-Relaunch ist bis zum Build-Test (Phase F5) unbelegt; Fehler
        behandelt der Aufrufer. Der Kindprozess soll unseren überleben, daher
        kein Warten/Schließen des ``Popen``-Handles.
        """
        import sys
        is_frozen = getattr(sys, "frozen", False) or globals().get("__compiled__") is not None
        cmd = list(sys.argv) if is_frozen else [sys.executable, *sys.argv]
        env = os.environ.copy()
        for key in [k for k in env if k.startswith("NUITKA_ONEFILE")]:
            env.pop(key, None)
        env.pop("TRACKER_UI", None)
        logger.info(f"Relaunch für UI-Wechsel: {cmd}")
        subprocess.Popen(cmd, env=env)

    def relaunch(self) -> None:
        """App neu starten (für den UI-Modus-Wechsel).

        ``App.stop()`` allein löst ``Window.on_request_close``/``exit_check``
        NICHT aus — ein reiner Stop+Popen würde Config-Änderungen verlieren und
        den neuen Prozess auf belegte Ports (Overlay/Arceus/BizHawk) sowie noch
        laufende BizHawk-Instanzen treffen. Deshalb erst der volle Shutdown
        (Configs speichern, Server/Threads stoppen, Ports freigeben), dann im
        done-callback der frische Prozess und App-Stop.
        """
        # Re-Entrancy: der Shutdown dauert im Worst-Case bis ~27s, das Fenster
        # bleibt derweil bedienbar. Ein zweiter Klick auf "Jetzt neu starten"
        # würde sonst einen zweiten Shutdown + zweiten Kindprozess auslösen.
        if getattr(self, "_shutting_down", False):
            logger.info("Relaunch ignoriert — Shutdown/Relaunch läuft bereits.")
            return
        # Backend noch nicht gestartet (Fenster noch im Splash): nichts zu
        # sichern/stoppen — direkt neu starten.
        if getattr(self, "bizhawk_thread", None) is None:
            self._shutting_down = True
            try:
                self._spawn_relaunch_process()
            except Exception as err:
                logger.error(f"Relaunch fehlgeschlagen: {err}\n{traceback.format_exc()}")
                self._shutting_down = False  # erneuter Versuch bleibt möglich
                return
            self._safe_app_stop()
            return

        def _after_shutdown(clean: bool) -> None:
            if not clean:
                logger.warning("Relaunch trotz unvollständigem Backend-Shutdown — "
                               "der neue Prozess könnte auf belegte Ports treffen.")
            try:
                self._spawn_relaunch_process()
            except Exception as err:
                logger.error(f"Relaunch-Prozess konnte nicht gestartet werden: "
                             f"{err}\n{traceback.format_exc()}")
            self._safe_app_stop()

        self._shutdown_backend(on_complete=_after_shutdown)

    def _safe_app_stop(self) -> None:
        from kivy.app import App
        app = App.get_running_app()
        if app is None:
            return
        try:
            app.stop()
        except Exception as err:
            logger.error(f"App.stop() fehlgeschlagen: {err}\n{traceback.format_exc()}")

    def exit_check(self, *args, **kwargs):
        # Das Backend entsteht erst in init_core() (MD: in _populate, nach dem
        # ersten Frame). Schließt der User das Fenster noch im Splash — oder
        # schlägt _populate fehl — sind configsave/bizhawk_thread/… noch nicht
        # gesetzt. Dann gibt es nichts zu speichern oder zu stoppen: Schließen
        # sauber durchlassen (False = kein Veto), statt mit AttributeError zu
        # crashen.
        if getattr(self, "bizhawk_thread", None) is None:
            return False
        # Läuft bereits ein Shutdown/Relaunch, keinen zweiten starten. Fenster
        # offen halten (Veto True) — der laufende Vorgang beendet die App selbst
        # (Relaunch via _safe_app_stop; normaler Close-Shutdown lässt den Loop
        # auslaufen). Verhindert den RuntimeError aus isolated_thread bei
        # submit_coro auf einen schon schließenden Thread.
        if getattr(self, "_shutting_down", False):
            return True
        self._shutdown_backend()

    def _shutdown_backend(self, on_complete=None) -> None:
        """Configs speichern + Backend (Server/Threads/BizHawk) herunterfahren.

        Gemeinsam genutzt von ``exit_check`` (Fenster schließen) und
        ``relaunch`` (UI-Wechsel). ``on_complete`` läuft im done-callback des
        Shutdown-Tasks — also erst, wenn alle Ports/Threads freigegeben sind
        (z.B. um danach einen neuen Prozess zu starten und die App zu beenden).
        """
        if getattr(self, "_shutting_down", False):
            logger.info("Shutdown läuft bereits — zweiter Aufruf ignoriert.")
            return
        self._shutting_down = True
        try:
            # Noch laufende Boot-Tasks (z.B. der Update-Check) abbrechen, damit
            # der Loop nicht auf sie wartet. Der Executor-Thread selbst wird
            # dadurch nicht beendet — dafür sorgt der Socket-Timeout im
            # Update-Worker.
            for task in list(getattr(self, "_boot_tasks", ())):
                task.cancel()
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
        except Exception as err:
            # Wirft ein Schritt VOR dem create_task (z.B. save_config bei
            # gesperrter Datei, submit_coro bei bereits schließendem Thread),
            # entsteht kein Shutdown-Task und der done-callback feuert nie.
            # Dann Flag zurücksetzen, damit das Fenster nicht dauerhaft via
            # exit_check-Veto hängt, und — falls ein Relaunch wartet — ihn mit
            # clean=False fortsetzen, statt die App bedienungslos stehenzulassen.
            logger.error(f"Shutdown-Start fehlgeschlagen: {err}\n{traceback.format_exc()}")
            self._shutting_down = False
            if on_complete is not None:
                try:
                    on_complete(False)
                except Exception as cb_err:
                    logger.error(f"Shutdown on_complete (Fehlerpfad) fehlgeschlagen: "
                                 f"{cb_err}\n{traceback.format_exc()}")
            return

        def _done(task) -> None:
            self._log_shutdown_result(task)
            if on_complete is not None:
                # Vollständigkeit aus dem Shutdown-Task ziehen (True = alle
                # Server/Threads sauber gestoppt). Bei Cancel/Exception als
                # unvollständig werten.
                clean = False
                try:
                    if not task.cancelled() and task.exception() is None:
                        clean = bool(task.result())
                except Exception:
                    clean = False
                try:
                    on_complete(clean)
                except Exception as err:
                    logger.error(f"Shutdown on_complete fehlgeschlagen: "
                                 f"{err}\n{traceback.format_exc()}")

        shutdown_task.add_done_callback(_done)

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

        # Vollständigkeit melden (für relaunch relevant, damit es eine
        # Port-Kollision beim neuen Prozess erkennen/loggen kann): alle
        # Stop-/Disconnect-Awaitables erledigt UND keine Isolations-Threads
        # mehr am Leben. Beim reinen Fenster-Close ist der Rückgabewert
        # bedeutungslos (der Prozess endet ohnehin).
        def _task_clean(t) -> bool:
            if not t.done() or t.cancelled():
                return False
            try:
                return t.exception() is None
            except Exception:
                return False
        clean = all(_task_clean(t) for t in tasks)
        for thread_owner in (self.munchlax_thread, self.bizhawk_thread, self.arceus_thread):
            inner = getattr(thread_owner, "_thread", None)
            try:
                if inner is not None and inner.is_alive():
                    clean = False
            except Exception:
                pass
        return clean

    def _log_shutdown_result(self, task):
        try:
            exc = task.exception()
        except asyncio.CancelledError:
            return
        except Exception:
            exc = None
        if exc is not None:
            logger.error(f"Shutdown-Task Exception: {type(exc).__name__}: {exc}")

    def save_config(self, path, setting):
        with open(path, "w") as file:
            yaml.dump(setting, file)
