import asyncio
import os
import subprocess
import traceback
from backend.logging_setup import get_logger


class ConnectionController:
    def __init__(self, arceus, bizhawk, citra, bizhawk_instances, munchlax, obs_websocket, bh, pl, overlay_server=None, bizhawk_thread=None, arceus_thread=None, munchlax_thread=None):
        self.arceus = arceus
        self.bizhawk = bizhawk
        self.citra = citra
        self.bizhawk_instances = bizhawk_instances
        self.munchlax = munchlax
        self.obs_websocket = obs_websocket
        self.bh = bh
        self.pl = pl
        self.overlay_server = overlay_server
        # Cross-thread-Submitter fuer BH-Server-Coros (start/stop). Darf
        # None sein — Fallback-Pfad weiter unten nutzt dann asyncio.create_task,
        # was mit dem Kivy-Loop-Server (vor Phase 3a) rueckwaerts-kompatibel ist.
        self.bizhawk_thread = bizhawk_thread
        # Analog zu bizhawk_thread: Arceus-Server + check_heartbeats +
        # timer_tick_loop laufen seit Phase 1 der Loop-Isolation auf dem
        # ArceusLoop. start()/stop()/disconnect_all muss die Coros via
        # submit_coro dorthin schieben, sonst versuchen die Watchdog-Tasks
        # auf dem Kivy-Loop zu laufen und blockieren bei GUI-Lag.
        self.arceus_thread = arceus_thread
        # Munchlax-Thread (Phase 3 der Loop-Isolation): connect/disconnect
        # und alle Netz-/Teams-Coros laufen auf dem MunchlaxLoop. Von Kivy
        # aus muss der Submit cross-thread passieren, sonst bindet
        # asyncio.open_connection an den Kivy-Loop.
        self.munchlax_thread = munchlax_thread

        self.logger = get_logger(__name__, './logs/connection_controller.log')

    def _submit_on_thread(self, thread, coro, name: str = ""):
        """Schickt eine Coro auf einen isolierten Loop und gibt ein awaitable zurueck.

        Fehlt der Thread (Legacy-/Testkonstruktion), fallback auf
        asyncio.create_task — der Caller kann das Resultat genauso awaiten.
        Bei disconnect_all werden die Awaitables in asyncio.wait geschoben;
        das triggert die Exception-Delivery und verhindert "never retrieved"-
        Warnungen.
        """
        if thread is not None:
            fut = thread.submit_coro(coro)
            awaitable = asyncio.wrap_future(fut)
        else:
            awaitable = asyncio.create_task(coro)
        if name:
            def _log(f):
                # CancelledError ist ab Python 3.8 BaseException-Subklasse,
                # `except Exception` wuerde ihn verfehlen und als "coro never
                # awaited" durchschlagen. Shutdown-Cancel ist erwartet → INFO.
                if f.cancelled():
                    self.logger.info(f"_submit_on_thread({name}) wurde gecancelt.")
                    return
                try:
                    exc = f.exception()
                except asyncio.CancelledError:
                    self.logger.info(
                        f"_submit_on_thread({name}) wurde gecancelt (exception())."
                    )
                    return
                except Exception:
                    exc = None
                if exc is not None:
                    self.logger.error(
                        f"_submit_on_thread({name}) failed: {type(exc).__name__}: {exc}"
                    )
            awaitable.add_done_callback(_log)
        return awaitable

    def _submit_bh(self, coro, name: str = ""):
        return self._submit_on_thread(self.bizhawk_thread, coro, name=name)

    def _submit_arc(self, coro, name: str = ""):
        return self._submit_on_thread(self.arceus_thread, coro, name=name)

    def _submit_mun(self, coro, name: str = ""):
        return self._submit_on_thread(self.munchlax_thread, coro, name=name)

    # --- OBS ---

    def connect_obs(self) -> asyncio.Task:
        self.logger.debug(f"OBS-Status vor Connect: is_connected={self.obs_websocket.is_connected}")
        task = asyncio.create_task(self.obs_websocket.load_obsws())
        self.logger.info("OBS Verbindung wird aufgebaut.")
        return task

    def disconnect_obs(self) -> asyncio.Task:
        task = asyncio.create_task(self.obs_websocket.disconnect())
        self.logger.info("OBS Verbindung wird getrennt.")
        return task

    # --- Arceus (Server) ---

    def start_server(self):
        """Startet den Arceus-Server, falls noch nicht gestartet."""
        if not self.arceus.server:
            awaitable = self._submit_arc(self.arceus.start(), name='arceus.start')
            self.logger.info("Arceus-Server wird gestartet.")
            return awaitable

    def stop_server(self):
        """Trennt den Client und stoppt den Arceus-Server."""
        self._submit_mun(self.munchlax.disconnect(), name='munchlax.disconnect')
        self._submit_arc(self.arceus.stop(), name='arceus.stop')
        self.logger.info("Arceus-Server wird gestoppt.")

    # --- Munchlax (Client) ---

    def connect_client(self):
        """Verbindet den Munchlax-Client, falls noch nicht verbunden."""
        self.logger.debug(f"Munchlax-Status vor Connect: is_connected={self.munchlax.is_connected}, host={self.munchlax.host}, port={self.munchlax.port}")
        if not self.munchlax.is_connected:
            awaitable = self._submit_mun(self.munchlax.connect(), name='munchlax.connect')
            self.logger.info("Munchlax-Client wird verbunden.")
            return awaitable

    def disconnect_client(self):
        """Trennt den Munchlax-Client. Idempotent — bricht auch waehrend
        connect-Retry oder _auto_reconnect-Backoff ab (R4 WARN).

        Rationale: `is_connected` ist waehrend der ConnectionRefused-
        Retry-Phase (bis 1s) und waehrend _auto_reconnect-Backoff False.
        Ein frueherer Guard `if self.munchlax.is_connected` hat User-
        Disconnect in genau diesen Phasen stillschweigend verworfen,
        anschliessend konnte die Verbindung doch zustande kommen und
        der User hatte eine Verbindung, die er nicht wollte.
        """
        self._submit_mun(self.munchlax.disconnect(), name='munchlax.disconnect')
        self.logger.info("Munchlax-Client wird getrennt.")

    def request_logs_from_all(self):
        """Host-only: broadcastet log_bundle_request an alle verbundenen
        Clients. Jeder Client packt lokal und streamt per upload_log_bundle
        zurueck."""
        return self._submit_arc(
            self.arceus.broadcast_log_bundle_request(),
            name='arceus.broadcast_log_bundle_request',
        )

    # --- BizHawk ---

    def start_bizhawk(self):
        """Startet den BizHawk-Server und spawnt Emulator-Prozesse für lokale Spieler."""
        try:
            self.logger.debug(f"Bizhawk-Status: server={self.bizhawk.server is not None}, port={self.bizhawk.port}, path={self.bh.get('path')}")
            if not self.bizhawk.server:
                self._submit_bh(self.bizhawk.start(self.munchlax), name='bizhawk.start')

            log_dir = os.path.abspath("./logs")
            for i in range(self.pl["player_count"]):
                if not self.pl[f"remote_{i+1}"]:
                    env = os.environ.copy()
                    env["TRACKER_PLAYER"] = str(i + 1)
                    env["TRACKER_LOG_DIR"] = log_dir
                    process = subprocess.Popen([
                        self.bh["path"],
                        f'--lua={os.path.abspath("./backend/lua/tracker.lua")}',
                        f'--socket_ip={self.bh["host"]}',
                        f'--socket_port={self.bh["port"]}',
                    ], env=env)
                    self.bizhawk_instances.append(process)
                    self.logger.info(f"BizHawk-Prozess für Spieler {i+1} gestartet (PID {process.pid}).")
        except Exception as err:
            self.logger.error(f"Fehler beim Starten von BizHawk: {type(err)}, {err}")
            self.logger.error(traceback.format_exc())

    # --- Overlay ---

    def start_overlay(self) -> asyncio.Task:
        task = asyncio.create_task(self.overlay_server.start())
        self.logger.info("Overlay-Server wird gestartet.")
        return task

    def stop_overlay(self) -> asyncio.Task:
        task = asyncio.create_task(self.overlay_server.stop())
        self.logger.info("Overlay-Server wird gestoppt.")
        return task

    # --- Alle Verbindungen ---

    def disconnect_all(self):
        """Trennt alle aktiven Verbindungen (BizHawk, OBS, Munchlax, Arceus, Overlay)."""
        tasks = [
            self._submit_bh(self.bizhawk.stop(), name='bizhawk.stop'),
            asyncio.create_task(self.obs_websocket.disconnect()),
            self._submit_mun(self.munchlax.disconnect(), name='munchlax.disconnect'),
            self._submit_arc(self.arceus.stop(), name='arceus.stop'),
        ]
        if self.overlay_server:
            tasks.append(asyncio.create_task(self.overlay_server.stop()))
        asyncio.create_task(asyncio.wait(tasks, timeout=3))
        self.logger.info("Alle Verbindungen werden getrennt.")
