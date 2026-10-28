"""Globale Status-Leiste der KivyMD-UI (Verbindungsstatus + Timer-Slot)."""
import asyncio
import traceback
from typing import Callable

from kivy.clock import Clock
from kivy.metrics import dp
from kivy.uix.widget import Widget
from kivymd.app import MDApp
from kivymd.uix.boxlayout import MDBoxLayout
from kivymd.uix.button import MDIconButton
from kivymd.uix.label import MDLabel, MDIcon

from backend.logging_setup import get_logger
from frontend.widgets.md_common import format_hms as _format_hms
from frontend.widgets.md_common import show_snackbar as _snackbar

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


class TimerChip(MDBoxLayout):
    """Read-only-Anzeige des Race-/Countdown-Timers in der Status-Leiste.

    Der Timer läuft server-authoritativ (Arceus), gespiegelt in Munchlax. Dieser
    Chip zeigt den Zustand und navigiert bei Klick auf den Text ins NuzlockeMenu
    (Timer-Tab). Ist die eigene Rolle Host, blendet er zusätzlich einen Pause-/
    Start-Button ein, der den Race-Timer über denselben konsens-basierten Request
    pausiert/fortsetzt wie der Timer-Tab (send_timer_pause_request /
    send_timer_resume_request). Sichtbar nur, solange ein Timer oder aktiver
    Countdown existiert; sonst ausgeblendet (opacity 0).
    """

    def __init__(self, app, **kwargs) -> None:
        super().__init__(
            orientation="horizontal",
            adaptive_width=True,
            spacing=dp(4),
            **kwargs,
        )
        self._app = app
        self._error_logged = False
        self._send_err_logged = False
        self._visible = False
        # Letzter bekannter Timer-Zustand (vom refresh gesetzt, von den Button-
        # Handlern gelesen, damit der Klick nicht erneut munchlax abfragen muss).
        self._race = {"present": False, "running": False, "started": False}
        self._cd = {"present": False, "running": False, "finished": False}
        self._tasks: set = set()  # Strong-Refs gegen GC (feedback_async_task_strong_ref)
        self.icon = MDIcon(
            icon="timer-outline",
            size_hint=(None, None),
            font_size=dp(16),
            size=(dp(18), dp(18)),
            pos_hint={"center_y": 0.5},
        )
        self.add_widget(self.icon)
        # Layout: [icon] [race-label] [race-ctrl] [cd-label] [cd-ctrl] — jeder
        # Button steht direkt neben SEINEM Label, nicht beide am Ende.
        # Host-Steuerung nur eingeblendet, wenn Rolle==Host und der jeweilige
        # Timer existiert. Icon spiegelt den aktuellen Zustand.
        #  - ctrl   = Race-Timer: Start (reset/nie gestartet) / Pause / Fortsetzen
        #             über den konsens-basierten Request (wie Timer-Tab).
        #  - cd_ctrl = Countdown: Pause / Fortsetzen (direkt, kein Konsens).
        self.race_label = MDLabel(text="", adaptive_width=True, pos_hint={"center_y": 0.5})
        self.add_widget(self.race_label)
        self.ctrl = MDIconButton(
            icon="play",
            size_hint=(None, None),
            size=(dp(28), dp(28)),
            pos_hint={"center_y": 0.5},
        )
        self.ctrl.bind(on_release=self._toggle_race)
        self.ctrl.opacity = 0
        self.ctrl.disabled = True
        self.add_widget(self.ctrl)
        self.cd_label = MDLabel(text="", adaptive_width=True, pos_hint={"center_y": 0.5})
        self.add_widget(self.cd_label)
        self.cd_ctrl = MDIconButton(
            icon="pause",
            size_hint=(None, None),
            size=(dp(28), dp(28)),
            pos_hint={"center_y": 0.5},
        )
        self.cd_ctrl.bind(on_release=self._toggle_countdown)
        self.cd_ctrl.opacity = 0
        self.cd_ctrl.disabled = True
        self.add_widget(self.cd_ctrl)
        self.opacity = 0  # leer bis ein Timer läuft

    def refresh(self) -> None:
        try:
            race_text, cd_text, race, cd = self._compute_state()
            self._error_logged = False
        except Exception:
            if not self._error_logged:
                self._error_logged = True
                logger.warning(f"TimerChip-Refresh fehlgeschlagen: {traceback.format_exc()}")
            race_text, cd_text = "", ""
            race = {"present": False, "running": False, "started": False}
            cd = {"present": False, "running": False, "finished": False}
        self._race = race
        self._cd = cd
        # Separator vor dem Countdown nur, wenn auch Race-Text vorhanden.
        cd_display = ("  ·  " + cd_text) if (race_text and cd_text) else cd_text
        if self.race_label.text != race_text:
            self.race_label.text = race_text
        if self.cd_label.text != cd_display:
            self.cd_label.text = cd_display
        visible = bool(race_text or cd_text)
        if visible != self._visible:
            self._visible = visible
            self.opacity = 1 if visible else 0
        self._update_controls()

    def _update_controls(self) -> None:
        """Race- und Countdown-Button nur fuer den Host, je nur bei vorhandenem
        Timer. Icon = play (starten/fortsetzen) bzw. pause (laeuft)."""
        host = self._is_host()
        # Race: play, solange nicht laeuft (deckt reset/nie-gestartet UND Pause ab).
        show_race = self._race["present"] and host
        self.ctrl.disabled = not show_race
        self.ctrl.opacity = 1 if show_race else 0
        race_icon = "pause" if self._race["running"] else "play"
        if self.ctrl.icon != race_icon:
            self.ctrl.icon = race_icon
        # Countdown: steuerbar solange nicht abgelaufen.
        show_cd = self._cd["present"] and not self._cd["finished"] and host
        self.cd_ctrl.disabled = not show_cd
        self.cd_ctrl.opacity = 1 if show_cd else 0
        cd_icon = "pause" if self._cd["running"] else "play"
        if self.cd_ctrl.icon != cd_icon:
            self.cd_ctrl.icon = cd_icon

    def _is_host(self) -> bool:
        try:
            return bool((getattr(self._app, "rem", {}) or {}).get("start_server"))
        except Exception:
            return False

    def _compute_state(self) -> tuple[str, str, dict, dict]:
        """Liefert (race-Text, countdown-Text, race-Zustand, countdown-Zustand).

        Getrennte Texte, damit jeder Button direkt neben SEINEM Label sitzt.
        race/cd sind Dicts mit present/running (+ started bzw. finished), damit die
        Button-Handler Start (reset) von Fortsetzen (Pause) unterscheiden koennen.
        """
        race = {"present": False, "running": False, "started": False}
        cd = {"present": False, "running": False, "finished": False}
        app = self._app
        munchlax = getattr(app, "munchlax", None) if app is not None else None
        if munchlax is None:
            return "", "", race, cd
        race_text = ""
        cd_text = ""
        timer_state = getattr(munchlax, "timer_state", None)
        if timer_state:
            race["present"] = True
            race["running"] = bool(timer_state.get("running"))
            # start_ts None = resettet/nie gestartet → Button muss STARTEN, nicht
            # fortsetzen (Resume greift serverseitig nur bei vorhandener Pause).
            race["started"] = timer_state.get("start_ts") is not None
            elapsed = munchlax.current_timer_elapsed()
            # "(Pause)" nur wenn wirklich gestartet-und-pausiert; frischer/reset-
            # Timer zeigt schlicht 00:00:00.
            suffix = " (Pause)" if (race["started"] and not race["running"]) else ""
            race_text = _format_hms(elapsed) + suffix
        countdown = getattr(munchlax, "countdown_state", None) or {}
        # Countdown nur, wenn tatsächlich einer gesetzt ist (duration > 0).
        if countdown.get("duration_seconds", 0) > 0:
            cd["present"] = True
            cd["running"] = bool(countdown.get("running"))
            cd["finished"] = bool(countdown.get("finished"))
            remaining = munchlax.current_countdown_remaining()
            lbl = countdown.get("label") or "Countdown"
            if cd["finished"]:
                state = " ✅"
            elif not cd["running"]:
                state = " (Pause)"
            else:
                state = ""
            cd_text = f"{lbl} {_format_hms(remaining)}{state}"
        return race_text, cd_text, race, cd

    def _toggle_race(self, *_args) -> None:
        """Host: Race-Timer starten/pausieren/fortsetzen.
         - laeuft    → Pause-Request (Konsens, wie Timer-Tab)
         - gestartet+pausiert → Resume-Request (Konsens)
         - resettet/nie gestartet → Start (direkt, wie Timer-Tab "Start")."""
        munchlax = self._connected_munchlax()
        if munchlax is None:
            return
        if self._race["running"]:
            coro = munchlax.send_timer_pause_request()
        elif self._race["started"]:
            coro = munchlax.send_timer_resume_request()
        else:
            coro = munchlax.send_timer_start()
        self._send(munchlax, coro)

    def _toggle_countdown(self, *_args) -> None:
        """Host: Countdown pausieren/fortsetzen (direkt, kein Konsens)."""
        munchlax = self._connected_munchlax()
        if munchlax is None:
            return
        if self._cd["running"]:
            coro = munchlax.send_countdown_pause()
        else:
            coro = munchlax.send_countdown_resume()
        self._send(munchlax, coro)

    def _connected_munchlax(self):
        """munchlax zurueckgeben, wenn verbunden; sonst Snackbar + None
        (kein silent Dropout, feedback_no_silent_async_dropouts)."""
        munchlax = getattr(self._app, "munchlax", None)
        if munchlax is None or not getattr(munchlax, "is_connected", False):
            _snackbar("Server nicht verbunden — Timer-Aktion verworfen.")
            return None
        return munchlax

    def _send(self, munchlax, coro) -> None:
        """Timer-Coro auf den Munchlax-Loop legen (eigener Thread seit Phase 3).
        Start vom Kivy-Loop via create_task; Fehler landen rate-limitiert im Log."""
        async def _runner():
            try:
                await munchlax.submit_cross_thread(coro)
                self._send_err_logged = False  # nach Erfolg wieder loggen
            except Exception:
                if not self._send_err_logged:
                    self._send_err_logged = True
                    logger.error(f"TimerChip-Send fehlgeschlagen: {traceback.format_exc()}")
        try:
            task = asyncio.create_task(_runner())
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
        except Exception:
            coro.close()
            logger.error(f"TimerChip-Task-Erzeugung fehlgeschlagen: {traceback.format_exc()}")

    def on_touch_down(self, touch):
        if self.opacity and self.collide_point(*touch.pos):
            # Treffer auf einen der Host-Buttons: der Button verarbeitet den Touch
            # selbst (Race bzw. Countdown), nur ein Treffer daneben navigiert.
            for btn in (self.ctrl, self.cd_ctrl):
                if not btn.disabled and btn.collide_point(*touch.pos):
                    return super().on_touch_down(touch)
            self._navigate()
            return True
        return super().on_touch_down(touch)

    def _navigate(self) -> None:
        """Zum NuzlockeMenu springen (has_screen-Guard als Absicherung)."""
        try:
            root = getattr(self._app, "root", None)
            manager = getattr(root, "screen_manager", None)
            if manager is not None and manager.has_screen("NuzlockeMenu"):
                manager.current = "NuzlockeMenu"
        except Exception:
            logger.warning(f"TimerChip-Navigation fehlgeschlagen: {traceback.format_exc()}")


class StatusBarMD(MDBoxLayout):
    """Fußzeile mit Verbindungsstatus + Race-/Countdown-Timer (timer_slot)."""

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
        # Race-/Countdown-Timer read-only; Klick → NuzlockeMenu.
        self.timer_slot = MDBoxLayout(adaptive_width=True, spacing=dp(4))
        self.timer_chip = TimerChip(app)
        self.timer_slot.add_widget(self.timer_chip)
        self.add_widget(self.timer_slot)

        self._refresh_all(0)
        self._poll_event = Clock.schedule_interval(self._refresh_all, 1)

    def _refresh_all(self, _dt: float) -> None:
        for item in self._items:
            item.refresh()
        self._refresh_clients()
        self.timer_chip.refresh()

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
