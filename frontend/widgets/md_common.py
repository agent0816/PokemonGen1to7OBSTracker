"""Wiederverwendbare KivyMD-Bausteine (Ersatz für toast.py / Popup-Factory der Klassik-UI)."""
import traceback
from typing import Any, Awaitable, Callable

from kivy.core.window import Window
from kivy.graphics import Color, Rectangle
from kivy.metrics import dp
from kivy.uix.widget import Widget

from kivymd.uix.boxlayout import MDBoxLayout
from kivymd.uix.button import MDButton
from kivymd.uix.button import MDButtonText
from kivymd.uix.dialog import MDDialog
from kivymd.uix.dialog import MDDialogIcon
from kivymd.uix.dialog import MDDialogHeadlineText
from kivymd.uix.dialog import MDDialogSupportingText
from kivymd.uix.dialog import MDDialogButtonContainer
from kivymd.uix.label import MDLabel
from kivymd.uix.loadingindicator import MDLoadingIndicator
from kivymd.uix.snackbar import MDSnackbar
from kivymd.uix.snackbar import MDSnackbarText

from backend.logging_setup import get_logger

logger = get_logger(__name__, 'logs/frontend.log')

_SNACKBAR_COLORS = {
    'info': None,
    'success': (0.18, 0.49, 0.20, 1),
    'error': (0.72, 0.11, 0.11, 1),
}


def format_hms(seconds: float) -> str:
    """Sekunden → HH:MM:SS (gemeinsame Quelle für Status-Leiste + Timer-Tab)."""
    s = int(max(0, seconds))
    return f"{s // 3600:02d}:{(s // 60) % 60:02d}:{s % 60:02d}"


def show_snackbar(text: str, level: str = 'info', duration: float = 3.0) -> None:
    """Kurze Rückmeldung unten im Fenster (Ersatz für frontend.widgets.toast)."""
    label = MDSnackbarText(text=text)
    snackbar = MDSnackbar(
        label,
        y=dp(24),
        pos_hint={"center_x": 0.5},
        size_hint_x=0.6,
        duration=duration,
    )
    color = _SNACKBAR_COLORS.get(level)
    if color is not None:
        # Eigene Hintergrundfarbe → Theme-Textfarbe passt nicht mehr, weiß erzwingen
        snackbar.md_bg_color = color
        label.theme_text_color = "Custom"
        label.text_color = (1, 1, 1, 1)
    snackbar.open()


def confirm_dialog(headline: str, supporting_text: str = '', *, on_confirm: Callable[[], None],
                   confirm_text: str = 'OK', cancel_text: str | None = 'Abbrechen',
                   icon: str | None = None) -> MDDialog:
    """Generischer Ja/Nein-Dialog. Schließt sich vor dem Aufruf von on_confirm.

    on_confirm ist synchron; für async-Arbeit im Callback `asyncio.create_task(...)`
    starten (eine Coroutine-Funktion direkt zu übergeben würde nie ausgeführt).
    ``cancel_text=None`` baut einen Ein-Button-Hinweis-Dialog (nur Bestätigen).
    """
    dialog = MDDialog(auto_dismiss=False)

    if icon:
        dialog.add_widget(MDDialogIcon(icon=icon))
    dialog.add_widget(MDDialogHeadlineText(text=headline))
    if supporting_text:
        dialog.add_widget(MDDialogSupportingText(text=supporting_text))

    def _handle_confirm(*_args: Any) -> None:
        dialog.dismiss()
        try:
            on_confirm()
        except Exception as err:
            logger.error(f"confirm_dialog: on_confirm fehlgeschlagen: {err}\n{traceback.format_exc()}")
            show_snackbar(f"Aktion fehlgeschlagen: {err}", level='error', duration=6.0)

    buttons = [Widget()]
    if cancel_text is not None:
        buttons.append(MDButton(
            MDButtonText(text=cancel_text),
            style='tonal',
            on_press=dialog.dismiss,
        ))
    buttons.append(MDButton(
        MDButtonText(text=confirm_text),
        style='filled',
        on_press=_handle_confirm,
    ))
    dialog.add_widget(MDDialogButtonContainer(*buttons, spacing="10dp"))
    dialog.open()
    return dialog


class _SpinnerOverlay(MDBoxLayout):
    """Vollfenster-Overlay mit abgedunkeltem Hintergrund, das alle Touch-Events schluckt."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        with self.canvas.before:
            Color(0, 0, 0, .55)
            self._rect = Rectangle(pos=self.pos, size=self.size)
        self.bind(pos=self._update_rect, size=self._update_rect)

    def _update_rect(self, *_args: Any) -> None:
        self._rect.pos = self.pos
        self._rect.size = self.size

    def on_touch_down(self, touch: Any) -> bool:
        return True

    def on_touch_move(self, touch: Any) -> bool:
        return True

    def on_touch_up(self, touch: Any) -> bool:
        return True


async def run_with_spinner(awaitable: Awaitable[Any], text: str = 'Bitte warten …') -> Any:
    """Zeigt einen modalen Vollfenster-Spinner, solange das Awaitable läuft.

    Exceptions aus dem Awaitable werden durchgereicht (nicht geloggt); der Aufrufer
    startet run_with_spinner selbst in einem Task mit try/except + Logger.
    """
    overlay = _SpinnerOverlay(
        orientation='vertical',
        size_hint=(None, None),
        size=Window.size,
        pos=(0, 0),
        spacing=dp(16),
        padding=dp(16),
    )
    indicator = MDLoadingIndicator(
        size_hint=(None, None),
        shape_size=dp(64),
        pos_hint={'center_x': .5},
    )
    label = MDLabel(text=text, halign='center', adaptive_height=True)
    overlay.add_widget(Widget())
    overlay.add_widget(indicator)
    overlay.add_widget(label)
    overlay.add_widget(Widget())

    def _on_window_size(_window: Any, size: Any) -> None:
        overlay.size = size

    Window.bind(size=_on_window_size)
    Window.add_widget(overlay)
    indicator.start()
    try:
        return await awaitable
    finally:
        indicator.stop()
        Window.unbind(size=_on_window_size)
        Window.remove_widget(overlay)
