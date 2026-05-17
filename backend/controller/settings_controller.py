import asyncio
import traceback
import yaml
from backend.logging_setup import get_logger


class SettingsController:
    def __init__(self, configsave, sp, rem, obs, bh, pl, rnd, arceus, bizhawk, munchlax, obs_websocket, ov=None, overlay_server=None):
        self.configsave = configsave
        self.sp = sp
        self.rem = rem
        self.obs = obs
        self.bh = bh
        self.pl = pl
        self.rnd = rnd
        self.ov = ov or {}
        self.arceus = arceus
        self.bizhawk = bizhawk
        self.munchlax = munchlax
        self.obs_websocket = obs_websocket
        self.overlay_server = overlay_server

        self.logger = get_logger(__name__, './logs/settings_controller.log')

    # --- Load-Methoden: geben aktuelle Config-Werte zurück, damit die View ihre Felder befüllen kann ---

    def load_sprites(self) -> dict:
        return self.sp.copy()

    def load_bizhawk(self) -> dict:
        return self.bh.copy()

    def load_obs(self) -> dict:
        return self.obs.copy()

    def load_remote(self) -> dict:
        return self.rem.copy()

    def load_player(self) -> dict:
        return self.pl.copy()

    def load_randomizer(self) -> dict:
        return self.rnd.copy()

    def load_overlay(self) -> dict:
        return self.ov.copy()

    # --- Private Update-Methoden: synchronisieren Backend-Objekte mit den aktuellen Config-Werten ---
    # Werden nur aufgerufen, wenn das jeweilige Backend noch nicht verbunden ist.

    def _update_bizhawk(self):
        try:
            self.logger.debug(f"_update_bizhawk: server={self.bizhawk.server is not None}, port={self.bh.get('port')}")
            if not self.bizhawk.server:
                self.bizhawk.port = self.bh['port']
        except Exception as err:
            self.logger.error(f"Fehler beim Aktualisieren von BizHawk: {type(err)}, {err}")
            self.logger.error(traceback.format_exc())

    def _update_obs_websocket(self):
        try:
            self.logger.debug(f"_update_obs: ws={self.obs_websocket.ws is not None}, host={self.obs.get('host')}, port={self.obs.get('port')}")
            if not self.obs_websocket.ws:
                self.obs_websocket.password = self.obs['password']
                self.obs_websocket.host = self.obs['host']
                self.obs_websocket.port = self.obs['port']
        except Exception as err:
            self.logger.error(f"Fehler beim Aktualisieren des OBS-Websockets: {type(err)}, {err}")
            self.logger.error(traceback.format_exc())

    def _update_arceus(self):
        try:
            if not self.arceus.server:
                self.arceus.port = self.rem['client_port']
        except Exception as err:
            self.logger.error(f"Fehler beim Aktualisieren von Arceus: {type(err)}, {err}")
            self.logger.error(traceback.format_exc())

    def _update_overlay(self):
        try:
            if self.overlay_server and not self.overlay_server.is_connected:
                self.overlay_server.port = int(self.ov.get('port', 43888))
        except Exception as err:
            self.logger.error(f"Fehler beim Aktualisieren des Overlay-Servers: {type(err)}, {err}")
            self.logger.error(traceback.format_exc())

    def _update_munchlax(self):
        try:
            self.logger.debug(f"_update_munchlax: connected={self.munchlax.is_connected}, start_server={self.rem.get('start_server')}")
            if not self.munchlax.is_connected:
                self.munchlax.host = '127.0.0.1' if self.rem['start_server'] else self.rem['server_ip_adresse']
                self.munchlax.port = self.rem['client_port'] if self.rem['start_server'] else self.rem['server_port']
        except Exception as err:
            self.logger.error(f"Fehler beim Aktualisieren von Munchlax: {type(err)}, {err}")
            self.logger.error(traceback.format_exc())

    # --- Save-Methoden: übernehmen Widget-Werte aus der View, schreiben in Config-Dicts und persistieren als YAML ---
    # Die View sammelt alle Werte und übergibt sie als plain dict. Der Controller entscheidet nicht,
    # welche Keys vorhanden sein müssen — er schreibt nur, was übergeben wird.

    def save_sprites(self, values: dict) -> None:
        """Aktualisiert sp-Dict und speichert sprites.yml. Löst anschließend OBS-Neuzeichnung aus."""
        try:
            self.sp.update(values)
            with open(f"{self.configsave}sprites.yml", 'w') as file:
                yaml.dump(self.sp, file)
            asyncio.create_task(self.obs_websocket.redraw_obs())
            self.logger.info("sprites.yml gespeichert.")
        except Exception as err:
            self.logger.error(f"Fehler beim Speichern der Sprite-Einstellungen: {type(err)}, {err}")
            self.logger.error(traceback.format_exc())

    def save_bizhawk(self, values: dict) -> None:
        """Aktualisiert bh-Dict, speichert bh_config.yml und synchronisiert das BizHawk-Objekt."""
        try:
            self.bh.update(values)
            self._update_bizhawk()
            with open(f"{self.configsave}bh_config.yml", 'w') as file:
                yaml.dump(self.bh, file)
            self.logger.info("bh_config.yml gespeichert.")
        except Exception as err:
            self.logger.error(f"Fehler beim Speichern der BizHawk-Einstellungen: {type(err)}, {err}")
            self.logger.error(traceback.format_exc())

    def save_obs(self, values: dict) -> None:
        """Aktualisiert obs-Dict, speichert obs_config.yml und synchronisiert den OBS-Websocket."""
        try:
            self.obs.update(values)
            self._update_obs_websocket()
            with open(f"{self.configsave}obs_config.yml", 'w') as file:
                yaml.dump(self.obs, file)
            self.logger.info("obs_config.yml gespeichert.")
        except Exception as err:
            self.logger.error(f"Fehler beim Speichern der OBS-Einstellungen: {type(err)}, {err}")
            self.logger.error(traceback.format_exc())

    def save_remote(self, values: dict) -> None:
        """Aktualisiert rem-Dict, speichert remote.yml und synchronisiert Arceus und Munchlax."""
        try:
            self.rem.update(values)
            self._update_arceus()
            self._update_munchlax()
            with open(f"{self.configsave}remote.yml", 'w') as file:
                yaml.dump(self.rem, file)
            self.logger.info("remote.yml gespeichert.")
        except Exception as err:
            self.logger.error(f"Fehler beim Speichern der Remote-Einstellungen: {type(err)}, {err}")
            self.logger.error(traceback.format_exc())

    def save_player(self, values: dict) -> None:
        """Aktualisiert pl-Dict und speichert player.yml."""
        try:
            self.pl.update(values)
            with open(f"{self.configsave}player.yml", 'w') as file:
                yaml.dump(self.pl, file)
            self.logger.info("player.yml gespeichert.")
        except Exception as err:
            self.logger.error(f"Fehler beim Speichern der Spieler-Einstellungen: {type(err)}, {err}")
            self.logger.error(traceback.format_exc())

    def save_randomizer(self, values: dict) -> None:
        """Aktualisiert rnd-Dict und speichert randomizer.yml."""
        try:
            self.rnd.update(values)
            with open(f"{self.configsave}randomizer.yml", 'w') as file:
                yaml.dump(self.rnd, file)
            self.logger.info("randomizer.yml gespeichert.")
        except Exception as err:
            self.logger.error(f"Fehler beim Speichern der Randomizer-Einstellungen: {type(err)}, {err}")
            self.logger.error(traceback.format_exc())

    def save_overlay(self, values: dict) -> None:
        """Aktualisiert ov-Dict, speichert overlay.yml und synchronisiert den Overlay-Server."""
        try:
            self.ov.update(values)
            self._update_overlay()
            with open(f"{self.configsave}overlay.yml", 'w') as file:
                yaml.dump(self.ov, file)
            self.logger.info("overlay.yml gespeichert.")
        except Exception as err:
            self.logger.error(f"Fehler beim Speichern der Overlay-Einstellungen: {type(err)}, {err}")
            self.logger.error(traceback.format_exc())

    def save_main_menu_settings(self, values: dict) -> None:
        """Speichert Anzeigeoptionen und Server-Modus aus dem Hauptmenü.

        Erwartet folgende Keys in values:
          sprites:  order, animated, show_nicknames, show_items, show_badges, show_hp_bars, show_status_effects
          bizhawk:  save_automatically
          remote:   start_server
        """
        try:
            sprite_keys = {'order', 'animated', 'show_nicknames', 'show_items', 'show_badges', 'show_hp_bars', 'show_status_effects'}
            sprite_values = {k: v for k, v in values.items() if k in sprite_keys}
            if sprite_values:
                self.sp.update(sprite_values)
                asyncio.create_task(self.obs_websocket.redraw_obs())
                with open(f"{self.configsave}sprites.yml", 'w') as file:
                    yaml.dump(self.sp, file)
                self.logger.info("sprites.yml (Hauptmenü) gespeichert.")

            if 'save_automatically' in values:
                self.bh['save_automatically'] = values['save_automatically']
                with open(f"{self.configsave}bh_config.yml", 'w') as file:
                    yaml.dump(self.bh, file)
                self.logger.info("bh_config.yml (Hauptmenü) gespeichert.")

            if 'start_server' in values:
                self.rem['start_server'] = values['start_server']
                self._update_munchlax()
                with open(f"{self.configsave}remote.yml", 'w') as file:
                    yaml.dump(self.rem, file)
                self.logger.info("remote.yml (Hauptmenü) gespeichert.")

        except Exception as err:
            self.logger.error(f"Fehler beim Speichern der Hauptmenü-Einstellungen: {type(err)}, {err}")
            self.logger.error(traceback.format_exc())
