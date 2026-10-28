"""KivyMD-Variante des Sprite-Setup-Dialogs (MDDialog statt Popup).

API-gleich zur klassischen SpriteSetupPopup: SpriteSetupPopupMD(sp, configsave,
save_callback).open(). Klont das Sprite-Repo per Git in einen vom User gewaehlten
Ordner und setzt die Pfade. Klassische Variante bleibt unveraendert (Feature-Freeze).
"""
from __future__ import annotations

import asyncio
import os
import traceback
from typing import Any, Callable, Optional

from kivy.uix.widget import Widget

from kivymd.uix.button import MDButton, MDButtonText
from kivymd.uix.dialog import (
    MDDialog, MDDialogIcon, MDDialogHeadlineText,
    MDDialogSupportingText, MDDialogButtonContainer,
)

from backend.sprite_repo import clone_sprite_repo, apply_sprite_paths
from backend.logging_setup import get_logger
from frontend.widgets.file_picker_md import pick_directory
from frontend.widgets.md_common import show_snackbar

logger = get_logger(__name__, 'logs/frontend.log')

DEFAULT_CLONE_PATH = os.path.abspath("sprites")

_INFO_TEXT = (
    "Die Sprite-Dateien koennen automatisch von GitHub heruntergeladen werden. "
    "Waehle einen Ordner, in den das Sprite-Repository geklont wird "
    "(vorgeschlagen: sprites/ neben der Anwendung). Der Download laeuft danach im "
    "Hintergrund; der Fortschritt erscheint als Meldung."
)


class SpriteSetupPopupMD:
    """Nicht-Widget-Wrapper um einen MDDialog; `.open()` zeigt ihn an."""

    def __init__(self, sp: dict, configsave: Any, save_callback: Optional[Callable[[], None]] = None) -> None:
        self.sp = sp
        self.configsave = configsave
        self.save_callback = save_callback
        self._tasks: set[asyncio.Task] = set()

        self._dialog = MDDialog(auto_dismiss=False)
        self._dialog.add_widget(MDDialogIcon(icon="folder-download-outline"))
        self._dialog.add_widget(MDDialogHeadlineText(text="Sprite-Dateien herunterladen"))
        self._dialog.add_widget(MDDialogSupportingText(text=_INFO_TEXT))
        self._dialog.add_widget(MDDialogButtonContainer(
            Widget(),
            MDButton(MDButtonText(text="Abbrechen"), style='tonal', on_press=lambda *_a: self._dialog.dismiss()),
            MDButton(MDButtonText(text="Herunterladen"), style='filled', on_press=self._on_download),
            spacing="10dp",
        ))

    def open(self) -> None:
        self._dialog.open()

    def _on_download(self, *_args: Any) -> None:
        pick_directory(
            on_select=self._start_clone,
            start_path=os.path.dirname(DEFAULT_CLONE_PATH),
            title="Zielordner für Sprite-Repository wählen",
        )

    def _start_clone(self, target: str) -> None:
        if not target:
            return
        clone_target = os.path.join(target, "sprites")
        if os.path.exists(clone_target) and os.listdir(clone_target):
            # Ziel nicht leer — Dialog offen lassen, anderen Ordner waehlen lassen.
            show_snackbar(f"Ordner existiert bereits: {clone_target}", level='error', duration=4.0)
            return
        self._dialog.dismiss()
        task = asyncio.create_task(self._do_clone(clone_target))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _do_clone(self, clone_target: str) -> None:
        show_snackbar("Sprites werden geladen …", level='info', duration=2.5)
        try:
            loop = asyncio.get_event_loop()
            success = await loop.run_in_executor(None, clone_sprite_repo, clone_target)
            if success:
                apply_sprite_paths(self.sp, clone_target)
                if self.save_callback:
                    self.save_callback()
                show_snackbar("Sprites erfolgreich geladen", level='success', duration=4.0)
            else:
                show_snackbar("Sprite-Download fehlgeschlagen. Siehe Log.", level='error', duration=4.0)
        except Exception as err:
            logger.error(f"Fehler im Sprite-Clone-Task: {err}")
            logger.error(traceback.format_exc())
            show_snackbar("Unerwarteter Fehler beim Sprite-Download. Siehe Log.", level='error', duration=4.0)
