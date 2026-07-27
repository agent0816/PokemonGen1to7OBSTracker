import os
import string
import sys
import traceback
from pathlib import Path
from typing import Callable, List, Optional

from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.filechooser import FileChooserListView
from kivy.uix.popup import Popup
from kivy.uix.textinput import TextInput

from backend.logging_setup import get_logger

logger = get_logger(__name__, 'logs/frontend.log')


def get_app_dir() -> str:
    """Verzeichnis, in dem die Tracker-Executable liegt.

    - PyInstaller-Build (sys.frozen): Ordner der EXE.
    - Sonst: aktuelles Arbeitsverzeichnis (üblicherweise Projekt-Root).
    """
    if getattr(sys, 'frozen', False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.getcwd()


def list_windows_drives() -> List[str]:
    """Vorhandene Laufwerksbuchstaben unter Windows als ['C:/', 'D:/', ...].

    Nutzt GetLogicalDrives (Bitmask) — kein Subprocess, ~0ms.
    Auf Nicht-Windows-Systemen leere Liste.
    """
    if sys.platform != 'win32':
        return []
    try:
        import ctypes
        bitmask = ctypes.windll.kernel32.GetLogicalDrives()
    except Exception as err:
        logger.warning(f"Konnte Laufwerke nicht ermitteln: {err}")
        return []
    return [
        f'{letter}:/'
        for i, letter in enumerate(string.ascii_uppercase)
        if bitmask & (1 << i)
    ]


class FilePickerPopup(Popup):
    """Kivy-nativer Datei-/Ordner-Picker als Ersatz für tkinter.filedialog.

    Callback-basiert (Kivy-Popups blockieren nicht):
        FilePickerPopup(on_select=lambda path: do_stuff(path)).open()

    Args:
        on_select: Wird mit gewähltem Pfad aufgerufen (String). Bei Abbruch
            nicht aufgerufen.
        start_path: Startverzeichnis. Default: Home-Verzeichnis.
        filters: Kivy-FileChooser-Filter (Globs wie ['*.log', '*.gba'] oder
            Callback-Funktion). Bei dirselect=True ignoriert.
        dirselect: True → Ordnerauswahl (ersetzt askdirectory).
                   False → Dateiauswahl (ersetzt askopenfilename).
        title: Popup-Titel.
    """

    def __init__(
        self,
        on_select: Callable[[str], None],
        start_path: Optional[str] = None,
        filters: Optional[list] = None,
        dirselect: bool = False,
        title: str = "Auswählen",
        **kwargs,
    ):
        super().__init__(title=title, size_hint=(0.9, 0.9), **kwargs)
        self._on_select = on_select
        self._dirselect = dirselect

        resolved_start = self._resolve_start_path(start_path)

        root = BoxLayout(orientation='vertical', spacing=5, padding=5)

        # Shortcut-Bar: Drives (nur Windows) + App-Dir + Home + Refresh
        self._shortcut_bar = BoxLayout(size_hint_y=None, height="32dp", spacing=3)
        self._build_shortcuts(self._shortcut_bar)
        root.add_widget(self._shortcut_bar)

        # Pfad-Eingabezeile (Enter → navigieren, oder "Gehe zu"-Button)
        path_row = BoxLayout(size_hint_y=None, height="28dp", spacing=3)
        self._path_input = TextInput(
            text=resolved_start,
            multiline=False,
            write_tab=False,
        )
        self._path_input.bind(on_text_validate=lambda inst: self._goto(inst.text))
        goto_btn = Button(text='Gehe zu', size_hint_x=None, width="80dp")
        goto_btn.bind(on_release=lambda *_: self._goto(self._path_input.text))
        path_row.add_widget(self._path_input)
        path_row.add_widget(goto_btn)
        root.add_widget(path_row)

        # FileChooser
        self.chooser = FileChooserListView(
            path=resolved_start,
            filters=filters or [],
            dirselect=dirselect,
        )
        self.chooser.bind(path=self._on_path_change)
        root.add_widget(self.chooser)

        # OK / Abbrechen
        button_bar = BoxLayout(size_hint_y=None, height="40dp", spacing=5)
        cancel_btn = Button(text='Abbrechen')
        cancel_btn.bind(on_release=lambda *_: self.dismiss())
        ok_btn = Button(text='OK')
        ok_btn.bind(on_release=self._confirm)
        button_bar.add_widget(cancel_btn)
        button_bar.add_widget(ok_btn)
        root.add_widget(button_bar)

        self.content = root

    @staticmethod
    def _resolve_start_path(start_path: Optional[str]) -> str:
        """Existierender Startpfad, sonst crasht der Chooser.

        Fallback-Reihenfolge: übergebener Pfad → App-Verzeichnis → Home.
        """
        if start_path:
            try:
                p = Path(start_path)
                if p.exists() and p.is_dir():
                    return str(p)
                if p.exists() and p.is_file():
                    return str(p.parent)
            except Exception:
                pass
        try:
            app_dir = get_app_dir()
            if app_dir and Path(app_dir).is_dir():
                return app_dir
        except Exception:
            pass
        return str(Path.home())

    def _build_shortcuts(self, bar: BoxLayout):
        bar.clear_widgets()
        for drive in list_windows_drives():
            btn = Button(text=drive.rstrip('/'), size_hint_x=None, width="48dp")
            btn.bind(on_release=lambda inst, p=drive: self._goto(p))
            bar.add_widget(btn)

        app_btn = Button(text='Tracker', size_hint_x=None, width="80dp")
        app_btn.bind(on_release=lambda *_: self._goto(get_app_dir()))
        bar.add_widget(app_btn)

        home_btn = Button(text='Home', size_hint_x=None, width="64dp")
        home_btn.bind(on_release=lambda *_: self._goto(str(Path.home())))
        bar.add_widget(home_btn)

        refresh_btn = Button(text='Neu laden', size_hint_x=None, width="80dp")
        refresh_btn.bind(on_release=lambda *_: self._build_shortcuts(bar))
        bar.add_widget(refresh_btn)

        # Filler damit Buttons links kleben
        bar.add_widget(BoxLayout())

    def _goto(self, path: str):
        if not path:
            return
        try:
            resolved = Path(path).expanduser()
            if not resolved.exists():
                logger.info(f"FilePicker: Pfad '{path}' existiert nicht.")
                return
            target = str(resolved if resolved.is_dir() else resolved.parent)
            self.chooser.path = target
        except Exception as err:
            # Leeres CD-Laufwerk, keine Rechte etc. — nicht crashen
            logger.warning(f"Wechsel nach '{path}' fehlgeschlagen: {err}")

    def _on_path_change(self, _instance, value):
        self._path_input.text = value

    def _confirm(self, *_):
        selection = list(self.chooser.selection)
        chosen: Optional[str] = None

        if selection:
            chosen = selection[0]
        elif self._dirselect and self.chooser.path:
            # Ordnerauswahl ohne expliziten Klick → aktuellen Pfad nehmen
            chosen = self.chooser.path

        if not chosen:
            return

        try:
            self._on_select(chosen)
        except Exception as err:
            logger.error(f"FilePicker-Callback fehlgeschlagen: {err}")
            logger.error(traceback.format_exc())
        finally:
            self.dismiss()


def pick_file(
    on_select: Callable[[str], None],
    start_path: Optional[str] = None,
    filters: Optional[list] = None,
    title: str = "Datei auswählen",
) -> FilePickerPopup:
    """Convenience-Wrapper für Dateiauswahl (öffnet Popup und gibt es zurück)."""
    popup = FilePickerPopup(
        on_select=on_select,
        start_path=start_path,
        filters=filters,
        dirselect=False,
        title=title,
    )
    popup.open()
    return popup


def pick_directory(
    on_select: Callable[[str], None],
    start_path: Optional[str] = None,
    title: str = "Ordner auswählen",
) -> FilePickerPopup:
    """Convenience-Wrapper für Ordnerauswahl (öffnet Popup und gibt es zurück)."""
    popup = FilePickerPopup(
        on_select=on_select,
        start_path=start_path,
        filters=None,
        dirselect=True,
        title=title,
    )
    popup.open()
    return popup
