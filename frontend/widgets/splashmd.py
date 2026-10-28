"""Ladescreen der KivyMD-UI (Phase F3).

Zeigt sofort nach dem Fenster-Aufbau einen Fortschritts-Screen, während die
Boot-Sequenz die blockierenden Schritte (öffentliche IPs, Update-Check)
abarbeitet — ausgelagert in Executor/Thread, damit der Splash nicht mitfriert.
Die Boot-Sequenz lebt in ``appmd.Screens._boot_sequence`` und ruft hier nur
``set_status`` zum Textwechsel.
"""

from kivy.metrics import dp
from kivy.uix.anchorlayout import AnchorLayout
from kivymd.uix.boxlayout import MDBoxLayout
from kivymd.uix.label import MDLabel
from kivymd.uix.progressindicator import MDCircularProgressIndicator
from kivymd.uix.screen import MDScreen

from version import VERSION


class SplashScreen(MDScreen):
    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.name = "Splash"

        # Volle Breite, vertikal mittig: size_hint_x=1 verhindert, dass die Box
        # auf die minimale Label-Breite kollabiert (sonst bricht die Überschrift
        # Zeichen für Zeichen um).
        root = MDBoxLayout(
            orientation="vertical",
            adaptive_height=True,
            spacing=dp(16),
            padding=(dp(24), 0, dp(24), 0),
            size_hint=(1, None),
            pos_hint={"center_y": 0.5},
        )

        root.add_widget(MDLabel(
            text="Pokémon OBS Tracker",
            halign="center", font_style="Headline", role="small",
            size_hint_x=1, adaptive_height=True,
        ))
        root.add_widget(MDLabel(
            text=f"Version {VERSION}",
            halign="center", role="small", theme_text_color="Secondary",
            size_hint_x=1, adaptive_height=True,
        ))

        # Spinner horizontal zentriert: in einer MDBoxLayout wirkt pos_hint nur
        # auf der Gegenachse (center_x wird in einer horizontalen Box ignoriert),
        # daher AnchorLayout.
        spinner_row = AnchorLayout(anchor_x="center", anchor_y="center",
                                   size_hint_y=None, height=dp(48))
        self._spinner = MDCircularProgressIndicator(
            size_hint=(None, None), size=(dp(40), dp(40)),
        )
        spinner_row.add_widget(self._spinner)
        root.add_widget(spinner_row)

        self._status = MDLabel(
            text="App startet …",
            halign="center", role="medium", size_hint_x=1, adaptive_height=True,
        )
        root.add_widget(self._status)

        self.add_widget(root)

    def set_status(self, text: str) -> None:
        self._status.text = text

    def on_leave(self, *args) -> None:
        # Spinner-Animation stoppen, sobald der Splash verlassen ist — sonst
        # läuft sie unsichtbar als Dauer-Animation weiter (Review F3).
        self._spinner.active = False
