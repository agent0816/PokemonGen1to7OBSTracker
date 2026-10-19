"""Wiederverwendbare KivyMD-Bausteine fuer den Einstellungs-Screen (Phase D).

Reine Widget-Fabriken: Beschriftung + Eingabe, Pfad-Feld mit Durchsuchen-Button,
Dropdown-Feld (Spinner-Ersatz). Die Save-/Lade-Logik bleibt im Screen; diese
Helfer liefern nur die Widgets und einfache Callbacks.
"""
from __future__ import annotations

from typing import Any, Callable, Optional, Sequence

from kivy.metrics import dp

from kivymd.uix.boxlayout import MDBoxLayout
from kivymd.uix.button import MDButton, MDButtonText, MDIconButton
from kivymd.uix.label import MDLabel
from kivymd.uix.menu import MDDropdownMenu
from kivymd.uix.textfield import MDTextField, MDTextFieldHintText

# Feste Beschriftungs-Spaltenbreite, damit Felder MIT Icon (browse_row) und OHNE
# Icon (labeled_row) linksbündig dieselbe Startkante haben.
LABEL_W = dp(150)


def make_textfield(hint: str, *, text: str = "", password: bool = False,
                   readonly: bool = False, is_port: bool = False,
                   on_text: Optional[Callable[[str], None]] = None,
                   on_validate: Optional[Callable[[], None]] = None) -> MDTextField:
    """MDTextField mit Hint. `is_port` begrenzt auf Ziffern, `readonly` sperrt die
    Eingabe (z.B. Session-Spiel). `on_text` feuert bei jeder Aenderung,
    `on_validate` bei Enter/Fokus-Verlust."""
    field = MDTextField(MDTextFieldHintText(text=hint), text=text or "")
    if password:
        field.password = True
    if is_port:
        field.input_filter = "int"
    if readonly:
        field.disabled = True
    if on_text is not None:
        field.bind(text=lambda _inst, val: on_text(val))
    if on_validate is not None:
        # Enter bestaetigt; Fokus-Verlust speichert ebenfalls.
        field.bind(on_text_validate=lambda *_a: on_validate())
        field.bind(focus=lambda _inst, focused: (None if focused else on_validate()))
    return field


def labeled_row(label_text: str, widget: Any, *, label_hint_x: float | None = None,
                height: Optional[float] = None) -> MDBoxLayout:
    """Horizontale Zeile: Beschriftung links (feste Breite LABEL_W), Widget rechts.
    So fluchtet die Feld-Startkante mit den Zeilen MIT Icon (browse_row)."""
    row = MDBoxLayout(orientation="horizontal", size_hint_y=None, spacing=dp(10))
    if height is not None:
        row.height = height
    else:
        row.adaptive_height = True
    label = MDLabel(text=label_text, role="small", valign="center")
    if label_hint_x is not None:
        label.size_hint_x = label_hint_x
    else:
        label.size_hint_x = None
        label.width = LABEL_W
    row.add_widget(label)
    row.add_widget(widget)
    return row


def browse_row(label_text: str, field: MDTextField,
               on_browse: Callable[[MDTextField], None]) -> MDBoxLayout:
    """Beschriftung (feste Breite LABEL_W) + Pfad-Textfeld + Durchsuchen-Button."""
    row = MDBoxLayout(orientation="horizontal", size_hint_y=None,
                      adaptive_height=True, spacing=dp(8))
    row.add_widget(MDLabel(text=label_text, size_hint_x=None, width=LABEL_W,
                           role="small", valign="center"))
    row.add_widget(field)
    row.add_widget(MDIconButton(icon="folder-outline",
                                on_release=lambda *_a: on_browse(field)))
    return row


class DropdownField(MDButton):
    """Button, der bei Klick ein MDDropdownMenu mit den Werten oeffnet.
    Ersetzt den Kivy-Spinner. `.value` haelt die aktuelle Auswahl."""

    def __init__(self, values: Sequence[str], current: str,
                 on_select: Callable[[str], None], **kwargs: Any) -> None:
        self._label = MDButtonText(text=str(current))
        super().__init__(self._label, style="outlined", **kwargs)
        self.values = [str(v) for v in values]
        self.value = str(current)
        self._on_select = on_select
        self._menu: Optional[MDDropdownMenu] = None
        self.bind(on_release=self._open_menu)

    def _open_menu(self, *_args: Any) -> None:
        items = [{
            "text": v,
            "on_release": (lambda x=v: self._choose(x)),
        } for v in self.values]
        self._menu = MDDropdownMenu(caller=self, items=items)
        self._menu.open()

    def _choose(self, value: str) -> None:
        self.value = value
        self._label.text = value
        if self._menu is not None:
            self._menu.dismiss()
        self._on_select(value)

    def set_value(self, value: str) -> None:
        """Setzt den Wert OHNE on_select zu feuern (fuer load/init)."""
        self.value = str(value)
        self._label.text = str(value)
