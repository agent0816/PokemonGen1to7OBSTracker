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
from backend.logging_setup import get_logger

logger = get_logger(__name__, 'logs/frontend.log')

# Variante A: alle tufup-Assets (Metadata + Targets) liegen flach im
# GitHub-Release "latest". GitHub flacht Pfade ab, daher ist die Base-URL
# für Metadata und Targets identisch.
TUFUP_METADATA_URL = os.environ.get(
    "TUFUP_METADATA_URL",
    "https://github.com/agent0816/PokemonGen1to7OBSTracker/releases/download/latest/",
)
TUFUP_TARGETS_URL = os.environ.get(
    "TUFUP_TARGETS_URL",
    "https://github.com/agent0816/PokemonGen1to7OBSTracker/releases/download/latest/",
)


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

    def _bootstrap_trust_anchor(self):
        """Kopiert root.json beim ersten Start aus dem Bundle ins metadata_dir."""
        target = self.metadata_dir / "root.json"
        if target.exists():
            return
        bundled = self.app_install_dir / "tufup_metadata" / "root.json"
        if not bundled.exists():
            logger.error(f"Trust-Anchor nicht gefunden: {bundled}")
            return
        self.metadata_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(bundled, target)
        logger.info(f"Trust-Anchor initialisiert: {target}")

    def check_for_update(self):
        try:
            self._bootstrap_trust_anchor()
            self.target_dir.mkdir(parents=True, exist_ok=True)
            self.client = Client(
                app_name=self.app_name,
                app_install_dir=self.app_install_dir,
                current_version=self.app_version,
                metadata_dir=self.metadata_dir,
                metadata_base_url=TUFUP_METADATA_URL,
                target_dir=self.target_dir,
                target_base_url=TUFUP_TARGETS_URL,
                refresh_required=False,
            )
            new_archive = self.client.check_for_updates()
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
