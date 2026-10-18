"""Globale Status-Leiste der KivyMD-UI (Verbindungsstatus + Timer-Slot)."""
import traceback
from typing import Callable

from kivy.clock import Clock
from kivy.metrics import dp
from kivy.uix.widget import Widget
from kivymd.app import MDApp
from kivymd.uix.boxlayout import MDBoxLayout
from kivymd.uix.label import MDLabel, MDIcon

from backend.logging_setup import get_logger

logger = get_logger(__name__, 'logs/frontend.log')

# Farben konsistent mit md_common._SNACKBAR_COLORS
_STATE_COLORS = {
    "connected": (0.18, 0.49, 0.20, 1),
    "warning": (0.90, 0.70, 0.10, 1),
    "disconnected": (0.72, 0.11, 0.11, 1),
}


def _normalize_state(value: object) -> str:
    """Normalisiert is_connected-Werte auf connected/warning/disconnected."""
    if value == "warning":
        return "warning"
    if value and value != "disconnected":
        return "connected"
    return "disconnected"


def _best_state(*states: str) -> str:
    """Bester Status aus mehreren (connected > warning > disconnected)."""
    if "connected" in states:
        return "connected"
    if "warning" in states:
        return "warning"
    return "disconnected"


class StatusDot(MDIcon):
    """Farbiger Punkt für einen Verbindungsstatus."""

    def __init__(self, **kwargs) -> None:
        super().__init__(
            icon="circle",
            size_hint=(None, None),
            font_size=dp(14),
            size=(dp(16), dp(16)),
            pos_hint={"center_y": 0.5},
            **kwargs,
        )

    def set_state(self, state: str) -> None:
        # icon_color (nicht text_color): die <MDIcon>-KV-Regel bindet color an
        # icon_color/Theme und würde text_color beim Theme-Wechsel überschreiben.
        self.icon_color = _STATE_COLORS.get(state, _STATE_COLORS["disconnected"])


class PollingStatusDot(StatusDot):
    """StatusDot, der 1s-getickt einen Provider pollt und die Farbe nachzieht.

    MD-Ersatz für ObjectConnectionStatusCircle / ValueConnectionStatusCircle der
    Klassik-UI: ``provider`` ist eine Callable, die den Rohwert liefert — z.B.
    ``lambda: obj.is_connected`` (Objekt-Status) oder ``lambda: d.get(client_id)``
    (Dict-Lookup). ``_normalize_state`` bildet den Wert auf
    connected/warning/disconnected ab (warning = der "Stale"-Zustand).
    """

    def __init__(self, provider, interval: float = 1.0, **kwargs) -> None:
        super().__init__(**kwargs)
        self._provider = provider
        self._error_logged = False
        self.set_state("disconnected")
        self._poll_event = Clock.schedule_interval(self._poll, interval)

    def _poll(self, _dt) -> None:
        try:
            value = self._provider()
        except Exception:
            # Rate-limit: nur den ersten Fehler mit Traceback loggen, sonst
            # flutet ein dauerhaft kaputter Provider das Log (A5-Muster).
            if not self._error_logged:
                self._error_logged = True
                logger.error(
                    f"PollingStatusDot: provider fehlgeschlagen: {traceback.format_exc()}"
                )
            self.set_state("disconnected")
            return
        self._error_logged = False
        self.set_state(_normalize_state(value))

    def stop(self) -> None:
        """Poll-Tick beenden (z.B. wenn der Dot aus dem Baum fliegt)."""
        if self._poll_event is not None:
            self._poll_event.cancel()
            self._poll_event = None


class StatusItem(MDBoxLayout):
    """Punkt + Label, Status kommt live vom Provider."""

    def __init__(self, label: str, provider: Callable[[], str], **kwargs) -> None:
        super().__init__(
            orientation="horizontal",
            adaptive_width=True,
            spacing=dp(4),
            **kwargs,
        )
        self._provider = provider
        self._label = label
        self._error_logged = False  # verhindert Log-Spam im 1s-Poll
        self.dot = StatusDot()
        self.add_widget(self.dot)
        self.name_label = MDLabel(text=label, adaptive_width=True, pos_hint={"center_y": 0.5})
        self.add_widget(self.name_label)

    def refresh(self) -> None:
        try:
            state = self._provider()
            self._error_logged = False
        except Exception as err:
            # Nur beim ersten Fehler loggen (mit Traceback), nicht jede Sekunde.
            if not self._error_logged:
                logger.warning(f"StatusItem-Provider '{self._label}' fehlgeschlagen: "
                               f"{err}\n{traceback.format_exc()}")
                self._error_logged = True
            state = "disconnected"
        self.dot.set_state(state)


class StatusBarMD(MDBoxLayout):
    """Fußzeile mit Verbindungsstatus; timer_slot ist Platzhalter für Phase E."""

    def __init__(self, **kwargs) -> None:
        super().__init__(
            orientation="horizontal",
            size_hint_y=None,
            height=dp(32),
            padding=(dp(12), 0),
            spacing=dp(16),
            **kwargs,
        )
        try:
            self.md_bg_color = self.theme_cls.surfaceContainerHighColor
        except AttributeError:
            self.md_bg_color = self.theme_cls.surfaceColor

        app = MDApp.get_running_app()
        if app is None:
            logger.warning("StatusBarMD ohne laufende App")
            providers: list[tuple[str, Callable[[], str]]] = [
                (name, lambda: "disconnected")
                for name in ("Server", "Client", "OBS", "Overlay", "Emulator")
            ]
        else:
            providers = [
                ("Server", lambda: _normalize_state(getattr(app.arceus, "is_connected", False))),
                ("Client", lambda: _normalize_state(getattr(app.munchlax, "is_connected", False))),
                ("OBS", lambda: _normalize_state(getattr(app.obs_websocket, "is_connected", False))),
                ("Overlay", lambda: _normalize_state(getattr(app.overlay_server, "is_connected", False))),
                ("Emulator", lambda: _best_state(
                    _normalize_state(getattr(app.bizhawk, "is_connected", False)),
                    _normalize_state(getattr(app.citra, "is_connected", False)),
                )),
            ]

        self._items: list[StatusItem] = []
        for label, provider in providers:
            item = StatusItem(label, provider)
            self.add_widget(item)
            self._items.append(item)

        # Verbundene Clients — global im Footer (auf jedem Screen sichtbar), nicht
        # nur im Hauptmenü. Dynamisch je Poll aus arceus/munchlax-Snapshots.
        self._app = app
        self._client_items: dict = {}
        self.add_widget(MDLabel(text="|", adaptive_width=True, pos_hint={"center_y": 0.5},
                                theme_text_color="Hint"))
        self.client_area = MDBoxLayout(adaptive_width=True, spacing=dp(12))
        self.add_widget(self.client_area)

        # Spacer schiebt den Timer-Slot nach rechts
        self.add_widget(Widget())
        # Platzhalter, Phase E füllt ihn mit dem Race-Timer
        self.timer_slot = MDBoxLayout(adaptive_width=True, spacing=dp(4))
        self.add_widget(self.timer_slot)

        self._refresh_all(0)
        self._poll_event = Clock.schedule_interval(self._refresh_all, 1)

    def _refresh_all(self, _dt: float) -> None:
        for item in self._items:
            item.refresh()
        self._refresh_clients()

    def _refresh_clients(self) -> None:
        """Verbundene Clients im Footer nachziehen (Dot + Name pro Client)."""
        app = self._app
        if app is None:
            return
        try:
            rem = getattr(app, "rem", {}) or {}
            seen: dict = {}
            # Provider liefern bereits normalisierte Zustände (wie die 5 Haupt-
            # Items), damit StatusItem.set_state die richtige Farbe trifft — der
            # Rohwert (z.B. True) würde sonst auf disconnected zurückfallen.
            if rem.get("start_server"):
                for cid, (name, _status) in app.arceus.snapshot_clients().items():
                    seen[cid] = (name,
                                 lambda c=cid: _normalize_state(app.arceus.munchlax_status.get(c)))
            else:
                status = app.munchlax.snapshot_remote_connection_status()
                names = app.munchlax.snapshot_remote_connection_names()
                for cid in status:
                    seen[cid] = (names.get(cid, cid[:8]),
                                 lambda c=cid: _normalize_state(
                                     app.munchlax.remote_connection_status.get(c)))
            # Neue/aktualisierte Clients
            for cid, (name, provider) in seen.items():
                item = self._client_items.get(cid)
                if item is None:
                    item = StatusItem(name, provider)
                    self.client_area.add_widget(item)
                    self._client_items[cid] = item
                elif item.name_label.text != name:
                    item.name_label.text = name
                item.refresh()
            # Verwaiste entfernen
            for cid in list(self._client_items.keys()):
                if cid not in seen:
                    self.client_area.remove_widget(self._client_items.pop(cid))
            self._client_err_logged = False
        except Exception:
            if not getattr(self, "_client_err_logged", False):
                self._client_err_logged = True
                logger.warning(f"StatusBar-Client-Refresh fehlgeschlagen: {traceback.format_exc()}")
