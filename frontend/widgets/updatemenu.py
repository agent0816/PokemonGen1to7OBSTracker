import asyncio
import os
import shutil
import socket
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
from backend.app_state import get_config_backup_keep
from backend.config_backup import create_config_backup
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

# tufup/tuf setzen auf ihren Netz-Requests keinen Timeout. Ist der Release-
# Server tot oder blockiert eine Firewall den Connect, haengt check_for_updates
# sonst unbegrenzt im Executor-Thread. Da der Default-ThreadPoolExecutor
# non-daemon ist und beim Interpreter-Exit gejoint wird, wuerde ein solcher
# Hänger auch das Schliessen der App blockieren. socket.setdefaulttimeout im
# Worker bindet den Connect- und Read-Timeout der (blockierenden) tufup-Sockets,
# sodass der Call von selbst zurueckkehrt. UPDATE_CHECK_TOTAL_TIMEOUT ist die
# zusaetzliche Boot-UX-Grenze (wait_for), damit der Splash nie lange haengt.
UPDATE_CHECK_SOCKET_TIMEOUT = 15.0
UPDATE_CHECK_TOTAL_TIMEOUT = 20.0


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
        # tufup filtert PEP440-Pre-Releases per Default aus (pre=None -> prereleases='').
        # Auf dem Alpha-Kanal muessen wir 'a' erlauben, sonst wird kein alpha-Archiv gefunden.
        pre_level = "a" if self.channel == "alpha" else None
        return self.client.check_for_updates(pre=pre_level)

    def _resolve_update_check(self):
        """Blockierender Update-Check inkl. Cache-Reset-Retry.

        Reiner Worker ohne UI-Seiteneffekt — läuft sync (Klassik) oder im
        Executor (KivyMD-Splash, Phase F3). Rückgabe: neues Archiv-Objekt oder
        ``None`` (kein Update ODER Fehler → beide führen zu SessionMenu)."""
        # Netz-I/O von tufup zeitlich beschränken (siehe Konstanten-Kommentar).
        # Default global zwischenspeichern und im finally wiederherstellen —
        # asyncio-Sockets (Arceus/Munchlax/BizHawk) ignorieren den Default und
        # sind davon nicht betroffen.
        prev_timeout = socket.getdefaulttimeout()
        socket.setdefaulttimeout(UPDATE_CHECK_SOCKET_TIMEOUT)
        try:
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
                    return None
            except Exception as err:
                logger.error(f"Update-Check fehlgeschlagen: {err}\n{traceback.format_exc()}")
                return None
        finally:
            socket.setdefaulttimeout(prev_timeout)

        logger.info(f"new_archive={new_archive}")
        return new_archive

    def _apply_update_result(self, new_archive, only_if_current=None,
                             no_update_target="SessionMenu"):
        """UI-Reaktion auf das Check-Ergebnis (immer auf dem Main-Thread).

        `only_if_current`: navigiert nur dann weiter, wenn der ScreenManager
        noch auf diesem Screen steht (MD-Splash-Boot: "Splash"). Verhindert,
        dass ein zwischenzeitlicher User-Wechsel überschrieben wird. Das
        Update-Popup wird dagegen immer gezeigt (wichtiger als die Nav).
        `no_update_target`: Zielscreen wenn kein Update vorliegt (MD-Erststart
        = "Onboarding", sonst "SessionMenu")."""
        if new_archive:
            self.show_update_popup(new_archive)
            return
        # self.manager (vom ScreenManager gesetzt) statt self.parent: parent
        # ist nur beim gerade angezeigten Screen gesetzt. Im MD-Splash-Boot
        # ist nicht der Update-Screen current, sondern der Splash.
        if only_if_current is not None and self.manager.current != only_if_current:
            return
        self.manager.current = no_update_target

    def check_for_update(self):
        """Synchroner Update-Check (klassische UI)."""
        self._apply_update_result(self._resolve_update_check())

    async def check_for_update_async(self, no_update_target="SessionMenu"):
        """Nicht-blockierender Update-Check (KivyMD-Splash-Boot, Phase F3).

        Die tufup-Netz-I/O läuft im Executor, die UI-Reaktion danach wieder
        auf dem Main-Thread (run_in_executor kehrt dorthin zurück).
        `no_update_target`: Zielscreen wenn kein Update vorliegt."""
        loop = asyncio.get_running_loop()
        try:
            new_archive = await asyncio.wait_for(
                loop.run_in_executor(None, self._resolve_update_check),
                timeout=UPDATE_CHECK_TOTAL_TIMEOUT,
            )
        except asyncio.TimeoutError:
            # Boot nicht am Update-Check aufhängen. Der Socket-Timeout im Worker
            # bringt den Executor-Thread ohnehin bald zurück; der Splash geht
            # jetzt ohne Update weiter.
            logger.warning("Update-Check-Timeout beim Start; weiter ohne Update.")
            new_archive = None
        self._apply_update_result(new_archive, only_if_current="Splash",
                                  no_update_target=no_update_target)

    def show_update_popup(self, new_archive):
        box = BoxLayout(orientation='vertical')
        text = f'Update verfügbar: Version {new_archive.version}'
        self._status_label = Label(text=text)
        box.add_widget(self._status_label)

        self.progress_bar = ProgressBar(max=100)
        box.add_widget(self.progress_bar)

        btn_layout = BoxLayout()
        download_btn = Button(text='Download')
        download_btn.bind(on_press=lambda x: self.download_update())  # type: ignore
        self._download_btn = download_btn
        btn_layout.add_widget(download_btn)

        cancel_btn = Button(text='Abbrechen')
        cancel_btn.bind(on_press=self.cancel_update)  # type: ignore
        self._cancel_btn = cancel_btn
        btn_layout.add_widget(cancel_btn)

        box.add_widget(btn_layout)

        self.popup = Popup(title='Update verfügbar!', content=box,
                           size_hint=(None, None), size=(600, 300), auto_dismiss=False)
        self.popup.open()

    def _set_status(self, text):
        label = getattr(self, "_status_label", None)
        if label is not None:
            label.text = text

    def _set_download_enabled(self, enabled):
        # Download UND Abbrechen gemeinsam: Abbrechen während Backup/Download
        # würde nur das Popup schließen, der Task liefe unsichtbar weiter und
        # beendete am Ende die App (App.stop nach Install-Start).
        for attr in ("_download_btn", "_cancel_btn"):
            btn = getattr(self, attr, None)
            if btn is not None:
                btn.disabled = not enabled

    def _start_update_task(self, skip_backup=False):
        """Update-Task mit Strong-Ref starten (Projektmuster, kein GC-Verlust)."""
        task = asyncio.ensure_future(self.async_download_update(skip_backup=skip_backup))
        self._update_task = task

        def _done(t):
            if getattr(self, "_update_task", None) is t:
                self._update_task = None
        task.add_done_callback(_done)

    async def _run_config_backup(self) -> bool:
        """Zip von backend/config vor dem Update (Executor, blockiert die UI nicht)."""
        self._set_status("Sichere Konfiguration …")
        try:
            target = await asyncio.get_running_loop().run_in_executor(
                None,
                lambda: create_config_backup(keep=get_config_backup_keep()),
            )
        except Exception as err:
            logger.error(f"Config-Backup vor Update fehlgeschlagen: {err}\n{traceback.format_exc()}")
            return False
        logger.info(f"Config-Backup vor Update: {target}")
        self._set_status(f"Konfiguration gesichert: {target.name}\nUpdate wird geladen …")
        return True

    def _ask_update_without_backup(self):
        box = BoxLayout(orientation='vertical', spacing=10, padding=10)
        box.add_widget(Label(
            text="Config-Backup fehlgeschlagen (Details in logs/frontend.log).\n"
                 "Update trotzdem installieren?",
            halign='center'))
        btns = BoxLayout(size_hint_y=None, height=44, spacing=10)
        popup = Popup(title='Backup fehlgeschlagen', content=box,
                      size_hint=(None, None), size=(560, 220), auto_dismiss=False)

        def _go(*_args):
            popup.dismiss()
            self._start_update_task(skip_backup=True)

        def _abort(*_args):
            popup.dismiss()
            self._set_status("Update abgebrochen: Backup fehlgeschlagen.")
            self._set_download_enabled(True)

        go_btn = Button(text='Trotzdem updaten')
        go_btn.bind(on_press=_go)  # type: ignore
        abort_btn = Button(text='Abbrechen')
        abort_btn.bind(on_press=_abort)  # type: ignore
        btns.add_widget(go_btn)
        btns.add_widget(abort_btn)
        box.add_widget(btns)
        popup.open()

    async def async_download_update(self, skip_backup=False):
        # Doppelklick auf Download verhindern, solange Backup/Download laufen
        self._set_download_enabled(False)
        try:
            if not skip_backup and not await self._run_config_backup():
                self._ask_update_without_backup()
                return
        except Exception as err:
            # z.B. Popup-Aufbau — Task darf nicht still sterben (Buttons gesperrt)
            logger.error(f"Backup-Schritt vor Update fehlgeschlagen: {err}\n{traceback.format_exc()}")
            self._set_status("Update abgebrochen: Fehler beim Backup (siehe Log).")
            self._set_download_enabled(True)
            return
        install_started = False
        try:
            logger.info("tufup-Download und -Install gestartet.")
            await asyncio.get_running_loop().run_in_executor(
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

        if not install_started:
            self._set_download_enabled(True)
        if install_started:
            App.get_running_app().stop()

    def download_update(self):
        if getattr(self, "_update_task", None) is not None:
            logger.info("Update läuft bereits — zweiter Download-Klick ignoriert.")
            return
        self._start_update_task()

    def cancel_update(self, instance):
        self.popup.dismiss()
        self.manager.current = "MainMenu"

    def _progress_hook(self, bytes_downloaded, bytes_expected):
        # Wird aus dem Download-Thread aufgerufen — UI-Update auf Main-Thread schieben.
        if not bytes_expected:
            return
        percent = float(bytes_downloaded) * 100.0 / float(bytes_expected)
        Clock.schedule_once(lambda dt: self._set_progress(percent), 0)

    def _set_progress(self, percent):
        if self.progress_bar is not None:
            self.progress_bar.value = percent
