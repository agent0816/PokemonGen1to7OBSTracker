"""KivyMD-Datei-/Ordner-Auswahl mit MDFileManager.

Signatur-kompatibel zu frontend/widgets/file_picker.py (pick_file/pick_directory),
damit MD-Screens dieselben Aufrufe nutzen koennen. Die klassische Kivy-Variante
(FileChooser-Popup) bleibt unveraendert fuer die klassische UI (Feature-Freeze).
"""
from __future__ import annotations

import os
import string
import traceback
from typing import Callable, List, Optional

from kivy.utils import platform
from kivymd.uix.filemanager import MDFileManager

from backend.logging_setup import get_logger

logger = get_logger(__name__, 'logs/frontend.log')


class _SafeFileManager(MDFileManager):
    """MDFileManager mit Windows-sicherer Laufwerks-Auflistung.

    Upstream ``show_disks`` liest ``os.popen("mountvol /").read()`` und dekodiert
    die Ausgabe mit cp1252 — das crasht bei Nicht-ASCII-Bytes (beobachtet:
    ``UnicodeDecodeError: 'charmap' codec can't decode byte 0x81``, ausgelöst durch
    wiederholtes „nach oben" bis zur Laufwerks-Wurzel). Hier via Laufwerksbuchstaben-
    Prüfung ganz ohne Subprozess — gibt zugleich die Laufwerks-Auswahl zurück.
    """

    def show_disks(self) -> None:
        if platform != "win":
            super().show_disks()
            return
        self.disks = [f"{d}:\\" for d in string.ascii_uppercase
                      if os.path.exists(f"{d}:\\")]
        self.current_path = ""
        manager_list = []
        for disk in self.disks:
            access_string = self.get_access_string(disk)
            icon = "harddisk" if "r" in access_string else "harddisk-remove"
            manager_list.append({
                "viewclass": "MDFileManagerItem",
                "path": disk,
                "icon": icon,
                "dir_or_file_name": disk,
                "events_callback": self.select_dir_or_file,
                "_selected": False,
            })
        self.ids.rv.data = manager_list
        self._show()


# Nur EIN Manager gleichzeitig offen; Referenz halten, damit er nicht vom GC
# eingesammelt wird, solange das Overlay sichtbar ist.
_active_manager: Optional[MDFileManager] = None


def _safe_start_dir(start_path: Optional[str]) -> str:
    """Liefert ein existierendes Verzeichnis zum Oeffnen. Ist start_path eine
    Datei, wird deren Ordner genommen; ist nichts Brauchbares da, das Home-Dir."""
    try:
        if start_path:
            if os.path.isdir(start_path):
                return start_path
            parent = os.path.dirname(start_path)
            if parent and os.path.isdir(parent):
                return parent
    except Exception:
        pass
    return os.path.expanduser("~")


def _filters_to_ext(filters: Optional[list]) -> List[str]:
    """Mappt FileChooser-Filter ('*.jar') auf MDFileManager-ext ('.jar')."""
    ext: List[str] = []
    for f in filters or []:
        s = str(f).strip()
        if not s:
            continue
        if s.startswith("*"):
            s = s[1:]
        if s and not s.startswith("."):
            s = "." + s
        if s:
            ext.append(s)
    return ext


def _close_active() -> None:
    global _active_manager
    mgr = _active_manager
    _active_manager = None
    if mgr is not None:
        try:
            mgr.close()
        except Exception as err:
            logger.debug(f"MDFileManager close fehlgeschlagen: {err}")


def _open(
    on_select: Callable[[str], None],
    start_path: Optional[str],
    selector: str,
    ext: List[str],
    title: str,
) -> MDFileManager:
    global _active_manager
    # Ein evtl. noch offener Manager wird zuerst geschlossen.
    _close_active()

    def _on_select(path: str):
        _close_active()
        try:
            on_select(path)
        except Exception as err:
            logger.error(f"file_picker_md on_select-Callback-Fehler: {err}")
            logger.error(traceback.format_exc())

    def _on_exit(*_args):
        _close_active()

    # Ordner-Auswahl: nur Verzeichnisse listen (+ Bestätigungs-FAB), damit Datei-
    # und Ordner-Picker klar unterscheidbar sind. Datei-Auswahl: Ordner UND Dateien
    # (Ordner braucht man zum Navigieren).
    search = "dirs" if selector == "folder" else "all"
    manager = _SafeFileManager(
        exit_manager=_on_exit,
        select_path=_on_select,
        selector=selector,
        ext=ext,
        search=search,
    )
    _active_manager = manager
    manager.show(_safe_start_dir(start_path))
    return manager


def pick_file(
    on_select: Callable[[str], None],
    start_path: Optional[str] = None,
    filters: Optional[list] = None,
    title: str = "Datei auswählen",
) -> MDFileManager:
    """Dateiauswahl (MD). `title` wird aus Signatur-Kompatibilitaet akzeptiert,
    MDFileManager hat keine eigene Titelzeile."""
    return _open(on_select, start_path, selector="file",
                 ext=_filters_to_ext(filters), title=title)


def pick_directory(
    on_select: Callable[[str], None],
    start_path: Optional[str] = None,
    title: str = "Ordner auswählen",
) -> MDFileManager:
    """Ordnerauswahl (MD)."""
    return _open(on_select, start_path, selector="folder", ext=[], title=title)
