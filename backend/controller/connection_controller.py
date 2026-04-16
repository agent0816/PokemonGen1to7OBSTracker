import asyncio
import os
import subprocess
import sys
import logging
import traceback


class ConnectionController:
    def __init__(self, arceus, bizhawk, citra, bizhawk_instances, munchlax, obs_websocket, bh, pl):
        self.arceus = arceus
        self.bizhawk = bizhawk
        self.citra = citra
        self.bizhawk_instances = bizhawk_instances
        self.munchlax = munchlax
        self.obs_websocket = obs_websocket
        self.bh = bh
        self.pl = pl

        self.logger = self._init_logging()

    def _init_logging(self):
        logger = logging.getLogger(__name__)
        logger.setLevel(logging.INFO)

        logging_formatter = logging.Formatter('[%(asctime)s] %(levelname)s: %(message)s')

        file_handler = logging.FileHandler('./logs/connection_controller.log', 'w')
        file_handler.setFormatter(logging_formatter)
        logger.addHandler(file_handler)

        stream_handler = logging.StreamHandler(sys.stdout)
        stream_handler.setFormatter(logging_formatter)
        logger.addHandler(stream_handler)

        return logger

    # --- OBS ---

    def connect_obs(self) -> asyncio.Task:
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
        if not self.munchlax.is_connected:
            task = asyncio.create_task(self.munchlax.connect())
            self.logger.info("Munchlax-Client wird verbunden.")
            return task

    def disconnect_client(self):
        """Trennt den Munchlax-Client, falls verbunden."""
        if self.munchlax.is_connected:
            asyncio.create_task(self.munchlax.disconnect())
            self.logger.info("Munchlax-Client wird getrennt.")

    # --- BizHawk ---

    def start_bizhawk(self):
        """Startet den BizHawk-Server und spawnt Emulator-Prozesse für lokale Spieler."""
        try:
            if not self.bizhawk.server:
                asyncio.create_task(self.bizhawk.start(self.munchlax))

            for i in range(self.pl["player_count"]):
                if not self.pl[f"remote_{i+1}"]:
                    process = subprocess.Popen([
                        self.bh["path"],
                        f'--lua={os.path.abspath(f"./backend/lua/Player{i+1}.lua")}',
                        f'--socket_ip={self.bh["host"]}',
                        f'--socket_port={self.bh["port"]}',
                    ])
                    self.bizhawk_instances.append(process)
                    self.logger.info(f"BizHawk-Prozess für Spieler {i+1} gestartet (PID {process.pid}).")
        except Exception as err:
            self.logger.error(f"Fehler beim Starten von BizHawk: {type(err)}, {err}")
            self.logger.error(traceback.format_exc())

    # --- Alle Verbindungen ---

    def disconnect_all(self):
        """Trennt alle aktiven Verbindungen (BizHawk, OBS, Munchlax, Arceus)."""
        tasks = [
            asyncio.create_task(self.bizhawk.stop()),
            asyncio.create_task(self.obs_websocket.disconnect()),
            asyncio.create_task(self.munchlax.disconnect()),
            asyncio.create_task(self.arceus.stop()),
        ]
        asyncio.create_task(asyncio.wait(tasks, timeout=3))
        self.logger.info("Alle Verbindungen werden getrennt.")
