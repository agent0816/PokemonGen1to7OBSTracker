import asyncio
import os
import subprocess
import traceback
from backend.logging_setup import get_logger


class ConnectionController:
    def __init__(self, arceus, bizhawk, citra, bizhawk_instances, munchlax, obs_websocket, bh, pl, overlay_server=None):
        self.arceus = arceus
        self.bizhawk = bizhawk
        self.citra = citra
        self.bizhawk_instances = bizhawk_instances
        self.munchlax = munchlax
        self.obs_websocket = obs_websocket
        self.bh = bh
        self.pl = pl
        self.overlay_server = overlay_server

        self.logger = get_logger(__name__, './logs/connection_controller.log')

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

    def start_server(self) -> asyncio.Task:
        """Startet den Arceus-Server, falls noch nicht gestartet."""
        if not self.arceus.server:
            task = asyncio.create_task(self.arceus.start())
            self.logger.info("Arceus-Server wird gestartet.")
            return task

    def stop_server(self):
        """Trennt den Client und stoppt den Arceus-Server."""
        asyncio.create_task(self.munchlax.disconnect())
        asyncio.create_task(self.arceus.stop())
        self.logger.info("Arceus-Server wird gestoppt.")

    # --- Munchlax (Client) ---

    def connect_client(self) -> asyncio.Task | None:
        """Verbindet den Munchlax-Client, falls noch nicht verbunden."""
        self.logger.debug(f"Munchlax-Status vor Connect: is_connected={self.munchlax.is_connected}, host={self.munchlax.host}, port={self.munchlax.port}")
        if not self.munchlax.is_connected:
            task = asyncio.create_task(self.munchlax.connect())
            self.logger.info("Munchlax-Client wird verbunden.")
            # Twitch-Extension-Push parallel starten. Start() prüft intern auf
            # enabled=True und schluckt Fehler — kein Guard nötig.
            tw_client = getattr(self.munchlax, "twitch_ext_client", None)
            if tw_client:
                asyncio.create_task(tw_client.start())
            return task

    def disconnect_client(self):
        """Trennt den Munchlax-Client, falls verbunden."""
        if self.munchlax.is_connected:
            asyncio.create_task(self.munchlax.disconnect())
            self.logger.info("Munchlax-Client wird getrennt.")
        tw_client = getattr(self.munchlax, "twitch_ext_client", None)
        if tw_client:
            asyncio.create_task(tw_client.stop())

    # --- BizHawk ---

    def start_bizhawk(self):
        """Startet den BizHawk-Server und spawnt Emulator-Prozesse für lokale Spieler."""
        try:
            self.logger.debug(f"Bizhawk-Status: server={self.bizhawk.server is not None}, port={self.bizhawk.port}, path={self.bh.get('path')}")
            if not self.bizhawk.server:
                asyncio.create_task(self.bizhawk.start(self.munchlax))

            for i in range(self.pl["player_count"]):
                if not self.pl[f"remote_{i+1}"]:
                    env = os.environ.copy()
                    env["TRACKER_PLAYER"] = str(i + 1)
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
            asyncio.create_task(self.bizhawk.stop()),
            asyncio.create_task(self.obs_websocket.disconnect()),
            asyncio.create_task(self.munchlax.disconnect()),
            asyncio.create_task(self.arceus.stop()),
        ]
        if self.overlay_server:
            tasks.append(asyncio.create_task(self.overlay_server.stop()))
        tw_client = getattr(self.munchlax, "twitch_ext_client", None)
        if tw_client:
            tasks.append(asyncio.create_task(tw_client.stop()))
        asyncio.create_task(asyncio.wait(tasks, timeout=3))
        self.logger.info("Alle Verbindungen werden getrennt.")
