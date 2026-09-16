import asyncio
import os
import shutil
import traceback
from pathlib import Path

from kivy.app import App
from kivy.clock import Clock
from kivy.uix.button import Button
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.label import Label
from kivy.uix.popup import Popup
from kivy.uix.progressbar import ProgressBar
from kivy.uix.screenmanager import Screen
from tufup.client import Client
from tuf.api.exceptions import ExpiredMetadataError
from backend.logging_setup import get_logger

logger = get_logger(__name__, 'logs/frontend.log')

# Update-Channel wird zur Build-Zeit in channel.txt neben die EXE gelegt.
# Fehlt die Datei, gilt "stable". Env-Vars überschreiben immer.
DEFAULT_CHANNEL = "stable"
CHANNEL_URLS = {
    "stable": "https://github.com/agent0816/PokemonGen1to7OBSTracker/releases/download/latest/",
    "alpha":  "https://github.com/agent0816/PokemonGen1to7OBSTracker/releases/download/alpha-latest/",
}
# Marker im metadata_dir hält den Kanal fest, mit dem der Trust-Anchor bootstrappt wurde.
# Bei Kanalwechsel (Stable -> Alpha und umgekehrt) muss root.json aus dem neuen Bundle
# ersetzt werden, sonst mismatched die Trust-Chain zum tufup-Repo.
CHANNEL_MARKER_FILENAME = ".channel"


def _read_channel(app_install_dir):
    channel_file = app_install_dir / "channel.txt"
    if not channel_file.exists():
        return DEFAULT_CHANNEL
    try:
        # utf-8-sig verwirft ein evtl. vorhandenes BOM, das PowerShell-Skripte
        # gern unbemerkt einfügen. .strip() würde ﻿ sonst stehen lassen.
        value = channel_file.read_text(encoding="utf-8-sig").strip().lower()
    except Exception as err:
        logger.warning(f"channel.txt konnte nicht gelesen werden: {err}")
        return DEFAULT_CHANNEL
    if value not in CHANNEL_URLS:
        logger.warning(f"Unbekannter Channel '{value}' in channel.txt — falle auf {DEFAULT_CHANNEL} zurück.")
        return DEFAULT_CHANNEL
    return value


class Update(Screen):
    def __init__(self, app_name, app_version, **kwargs):
        super().__init__(**kwargs)
        self.app_name = app_name
        self.app_version = app_version
        self.name = "Update"
        self.popup = None
        self.progress_bar = None
        self.client = None

        # main.py setzt cwd bereits auf das EXE-Verzeichnis (portable Layout).
        self.app_install_dir = Path(os.getcwd()).resolve()
        self.metadata_dir = self.app_install_dir / "update_cache" / "metadata"
        self.target_dir = self.app_install_dir / "update_cache" / "targets"

        self.channel = _read_channel(self.app_install_dir)
        channel_base = CHANNEL_URLS[self.channel]
        self.metadata_url = os.environ.get("TUFUP_METADATA_URL", channel_base)
        self.targets_url = os.environ.get("TUFUP_TARGETS_URL", channel_base)
        logger.info(f"Update-Channel: {self.channel} (metadata={self.metadata_url})")

    def _bootstrap_trust_anchor(self):
        """Kopiert root.json aus dem Bundle ins metadata_dir. Bei Kanalwechsel wird der
        Cache vorher geleert, damit alte Trust-Chain (z.B. Stable) nicht die neue (Alpha)
        blockiert.
        """
        target = self.metadata_dir / "root.json"
        marker = self.metadata_dir / CHANNEL_MARKER_FILENAME
        cached_channel = None
        if marker.exists():
            try:
                cached_channel = marker.read_text(encoding="utf-8").strip().lower()
            except Exception as err:
                logger.warning(f"Channel-Marker konnte nicht gelesen werden: {err}")
        # Existiert root.json ohne Marker, stammt der Cache aus einer Version vor Kanal-Support.
        # Wir wissen dann nicht, zu welchem Repo die Chain gehört, und behandeln das wie einen
        # Kanalwechsel, damit der Trust-Anchor sicher aus dem aktuellen Bundle kommt.
        legacy_cache_without_marker = target.exists() and cached_channel is None
        channel_changed = (
            (cached_channel is not None and cached_channel != self.channel)
            or legacy_cache_without_marker
        )
        if target.exists() and not channel_changed:
            return
        bundled = self.app_install_dir / "tufup_metadata" / "root.json"
        if not bundled.exists():
            logger.error(f"Trust-Anchor nicht gefunden: {bundled}")
            return
        if channel_changed:
            if legacy_cache_without_marker:
                logger.info(
                    f"Metadata-Cache ohne Kanal-Marker gefunden (Kanal={self.channel}); "
                    f"Cache wird geleert und Trust-Anchor neu bootstrapt."
                )
            else:
                logger.info(
                    f"Kanalwechsel erkannt ({cached_channel} -> {self.channel}); "
                    f"Metadata-Cache wird geleert."
                )
            self._reset_metadata_cache()
        self.metadata_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(bundled, target)
        try:
            marker.write_text(self.channel, encoding="utf-8")
        except Exception as err:
            logger.warning(f"Channel-Marker konnte nicht geschrieben werden: {err}")
        logger.info(f"Trust-Anchor initialisiert: {target} (Kanal={self.channel})")

    def _reset_metadata_cache(self):
        """Entfernt alle Dateien unter metadata_dir. Wird bei Kanalwechsel oder
        abgelaufenem Cache gerufen, danach bootstrapt _bootstrap_trust_anchor neu.
        """
        if not self.metadata_dir.exists():
            return
        for entry in self.metadata_dir.iterdir():
            try:
                if entry.is_file() or entry.is_symlink():
                    entry.unlink()
                else:
                    shutil.rmtree(entry)
            except Exception as err:
                logger.warning(f"Konnte {entry} nicht loeschen: {err}")

    def _run_update_check(self):
        self.client = Client(
            app_name=self.app_name,
            app_install_dir=self.app_install_dir,
            current_version=self.app_version,
            metadata_dir=self.metadata_dir,
            metadata_base_url=self.metadata_url,
            target_dir=self.target_dir,
            target_base_url=self.targets_url,
            refresh_required=False,
        )
        return self.client.check_for_updates()

    def check_for_update(self):
        try:
            self._bootstrap_trust_anchor()
            self.target_dir.mkdir(parents=True, exist_ok=True)
            new_archive = self._run_update_check()
        except ExpiredMetadataError as err:
            logger.warning(
                f"Metadata abgelaufen ({err}); Cache wird geleert und Update-Check erneut versucht."
            )
            try:
                self._reset_metadata_cache()
                self._bootstrap_trust_anchor()
                new_archive = self._run_update_check()
            except Exception as retry_err:
                logger.error(
                    f"Update-Check nach Cache-Reset weiterhin fehlgeschlagen: {retry_err}\n{traceback.format_exc()}"
                )
                self.parent.current = "SessionMenu"
                return
        except Exception as err:
            logger.error(f"Update-Check fehlgeschlagen: {err}\n{traceback.format_exc()}")
            self.parent.current = "SessionMenu"
            return

        logger.info(f"new_archive={new_archive}")
        if new_archive:
            self.show_update_popup(new_archive)
        else:
            self.parent.current = "SessionMenu"

    def show_update_popup(self, new_archive):
        box = BoxLayout(orientation='vertical')
        text = f'Update verfügbar: Version {new_archive.version}'
        box.add_widget(Label(text=text))

        self.progress_bar = ProgressBar(max=100)
        box.add_widget(self.progress_bar)

        btn_layout = BoxLayout()
        download_btn = Button(text='Download')
        download_btn.bind(on_press=lambda x: self.download_update())  # type: ignore
        btn_layout.add_widget(download_btn)

        cancel_btn = Button(text='Abbrechen')
        cancel_btn.bind(on_press=self.cancel_update)  # type: ignore
        btn_layout.add_widget(cancel_btn)

        box.add_widget(btn_layout)

        self.popup = Popup(title='Update verfügbar!', content=box,
                           size_hint=(None, None), size=(600, 300), auto_dismiss=False)
        self.popup.open()

    async def async_download_update(self):
        install_started = False
        try:
            logger.info("tufup-Download und -Install gestartet.")
            await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: self.client.download_and_apply_update(
                    skip_confirmation=True,
                    progress_hook=self._progress_hook,
                ),
            )
            logger.warning("download_and_apply_update kehrte ohne Install zurück.")
        except SystemExit:
            # tufup ruft am Ende sys.exit(0) — aus dem Executor-Thread beendet
            # das aber nur den Thread. Wir müssen Kivy hier sauber herunterfahren,
            # damit der externe Install-Prozess die EXE überschreiben kann.
            install_started = True
            logger.info("Install-Skript wurde gestartet, Kivy wird beendet.")
        except Exception as err:
            logger.error(f"Update-Download fehlgeschlagen: {err}\n{traceback.format_exc()}")

        if install_started:
            App.get_running_app().stop()

    def download_update(self):
        asyncio.ensure_future(self.async_download_update())

    def cancel_update(self, instance):
        self.popup.dismiss()
        self.parent.current = "MainMenu"

    def _progress_hook(self, bytes_downloaded, bytes_expected):
        # Wird aus dem Download-Thread aufgerufen — UI-Update auf Main-Thread schieben.
        if not bytes_expected:
            return
        percent = float(bytes_downloaded) * 100.0 / float(bytes_expected)
        Clock.schedule_once(lambda dt: self._set_progress(percent), 0)

    def _set_progress(self, percent):
        if self.progress_bar is not None:
            self.progress_bar.value = percent
