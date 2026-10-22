"""MD-Variante des Crash-Report-Popups (Phase F5).

Gleiche Funktion wie `frontend.widgets.mainmenu.CrashReportPopup`, aber als
`MDDialog` statt klassischem Kivy-`Popup` — damit der Erststart-/Crash-Hinweis in
der MD-UI zum Rest passt. Die (8-Runden-reviewte) Log-Archivierung wird 1:1
wiederverwendet (`CrashReportPopup._archive_log`, jetzt `@staticmethod`); hier
sind NUR Darstellung und Bundle-Trigger neu. Controller-Objekt (kein Widget),
analog `TotalWipeBannerMD`.
"""

import asyncio
import os
import traceback
from pathlib import Path

from kivy.core.clipboard import Clipboard
from kivy.metrics import dp
from kivy.uix.widget import Widget
from kivymd.uix.button import MDButton, MDButtonText
from kivymd.uix.dialog import (MDDialog, MDDialogButtonContainer,
                               MDDialogContentContainer, MDDialogHeadlineText,
                               MDDialogSupportingText)
from kivymd.uix.label import MDLabel

from backend.logging_setup import get_logger
from frontend.widgets.md_common import show_snackbar
# CrashReportPopup liefert die geteilte Archivierung; should_show_crash_popup
# re-exportiert, damit Aufrufer alles aus einem Modul beziehen können.
from frontend.widgets.mainmenu import CrashReportPopup, should_show_crash_popup  # noqa: F401

logger = get_logger(__name__, 'logs/frontend.log')


class CrashReportPopupMD:
    """MDDialog-Controller für den Crash-Hinweis beim Start."""

    def __init__(self, crash_log_path, configsave=None, pl=None, rem=None):
        # Config-Dicts by-reference halten (CLAUDE.md), nicht durch {} ersetzen.
        self._pl = pl if pl is not None else {}
        self._rem = rem if rem is not None else {}
        self._configsave = configsave
        self._bundling = False
        self._tasks: set = set()

        # Snapshot-at-Open: exakt die gleiche Archivierung wie die Klassik, damit
        # Fenster-Close/Kill bei offenem Dialog den Re-Popup-Bug nicht triggert.
        archived = CrashReportPopup._archive_log(crash_log_path)
        display_path = archived or os.path.abspath(crash_log_path)
        session_ok = bool(configsave) and Path(str(configsave)).is_dir()

        content = MDDialogContentContainer(orientation="vertical", spacing=dp(8),
                                           size_hint_y=None)
        # MDDialogContentContainer kennt kein adaptive_height (Phase-E3-Lehre) →
        # Höhe explizit an die Kinder binden.
        content.bind(minimum_height=content.setter("height"))
        self._path_label = MDLabel(text=display_path, role="small", adaptive_height=True)
        content.add_widget(self._path_label)

        self._bundle_btn = MDButton(MDButtonText(text="Support-Bundle"), style="tonal",
                                    on_release=lambda *_a: self._on_bundle())
        self._bundle_btn.disabled = not session_ok

        self.dialog = MDDialog(auto_dismiss=False)
        self.dialog.add_widget(MDDialogHeadlineText(text="Crash erkannt"))
        self.dialog.add_widget(MDDialogSupportingText(
            text="Beim letzten Start ist ein Fehler aufgetreten. "
                 "Das Crash-Log wurde gespeichert."))
        self.dialog.add_widget(content)
        self.dialog.add_widget(MDDialogButtonContainer(
            Widget(),
            MDButton(MDButtonText(text="Pfad kopieren"), style="text",
                     on_release=lambda *_a: self._copy_path(display_path)),
            self._bundle_btn,
            MDButton(MDButtonText(text="Schließen"), style="filled",
                     on_release=lambda *_a: self.dialog.dismiss()),
            spacing="8dp",
        ))

    def open(self) -> None:
        self.dialog.open()

    def _copy_path(self, path: str) -> None:
        abs_path = os.path.abspath(path)
        try:
            Clipboard.copy(abs_path)
            show_snackbar(f"Pfad kopiert: {abs_path}", level='success')
        except Exception as err:
            logger.error(f"Pfad kopieren fehlgeschlagen: {err}\n{traceback.format_exc()}")
            show_snackbar("Pfad kopieren fehlgeschlagen.", level='error', duration=4.0)

    def _on_bundle(self) -> None:
        if self._bundling:
            return
        session_raw = str(self._configsave) if self._configsave is not None else ""
        if not session_raw.strip():
            show_snackbar("Keine Session gewählt — Bundle nicht möglich.",
                          level='error', duration=4.0)
            return
        session_path = Path(session_raw)
        if not session_path.is_dir():
            show_snackbar(f"Session-Pfad fehlt: {session_path}", level='error', duration=4.0)
            return
        player_name = str(self._pl.get("your_name", "") or "unknown")
        is_host = bool(self._rem.get("start_server", False))
        self._bundling = True
        self._bundle_btn.disabled = True
        show_snackbar("Support-Bundle wird gepackt …", level='info', duration=2.5)

        async def _runner():
            try:
                from backend.log_bundler import build_bundle
                loop = asyncio.get_running_loop()
                bundle_path = await loop.run_in_executor(
                    None, build_bundle, session_path, player_name, is_host, None)
                size_mib = bundle_path.stat().st_size / (1024 * 1024)
                # Button bleibt bei Erfolg disabled — Bundle ist gebaut.
                show_snackbar(f"Bundle gespeichert ({size_mib:.1f} MiB): {bundle_path.name}",
                              level='success', duration=3.5)
            except Exception as err:
                logger.error(f"build_bundle aus MD-Crash-Popup fehlgeschlagen: "
                             f"{err}\n{traceback.format_exc()}")
                show_snackbar(f"Bundle fehlgeschlagen: {err}", level='error', duration=5.0)
                self._bundle_btn.disabled = False  # erneuter Versuch möglich
            finally:
                self._bundling = False

        task = asyncio.create_task(_runner())
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
