"""TimerWidget — zeigt Race-Timer + Countdown, sendet Steuerbefehle an Munchlax.

Race-Timer läuft server-authoritativ (Arceus); dieses Widget zeigt nur den
gespiegelten Zustand und triggert Start/Pause/Reset via `send_timer_*`. Der
Countdown ist unabhängig — bei Ablauf spielt der Client einen Ton
(winsound.Beep, Windows-only wie der Rest der App).
"""

import asyncio
import traceback

from kivy.clock import Clock
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.label import Label
from kivy.uix.textinput import TextInput

from backend.logging_setup import get_logger

logger = get_logger(__name__, './logs/timer_widget.log')


def _format_hms(seconds: float) -> str:
    s = int(max(0, seconds))
    return f"{s // 3600:02d}:{(s // 60) % 60:02d}:{s % 60:02d}"


class TimerWidget(BoxLayout):
    def __init__(self, munchlax, **kwargs):
        super().__init__(orientation="vertical", size_hint_y=None,
                          height="220dp", spacing="6dp", padding="4dp", **kwargs)
        self.munchlax = munchlax

        # ---- Race-Timer ----
        self.add_widget(Label(text="Race-Timer", font_size="16sp",
                                size_hint_y=None, height="24dp"))
        self.timer_display = Label(text="00:00:00", font_size="32sp",
                                     size_hint_y=None, height="46dp")
        self.add_widget(self.timer_display)

        timer_btns = BoxLayout(orientation="horizontal", size_hint_y=None,
                                height="36dp", spacing="4dp")
        timer_btns.add_widget(Button(text="Start",
                                       on_press=lambda *_: self._send_async(self._send_timer_start())))
        timer_btns.add_widget(Button(text="Pause",
                                       on_press=lambda *_: self._send_async(self._send_timer_pause())))
        timer_btns.add_widget(Button(text="Resume",
                                       on_press=lambda *_: self._send_async(self._send_timer_resume())))
        timer_btns.add_widget(Button(text="Split",
                                       on_press=lambda *_: self._send_async(self._send_timer_split())))
        timer_btns.add_widget(Button(text="Reset",
                                       on_press=lambda *_: self._send_async(self._send_timer_reset())))
        self.add_widget(timer_btns)

        # ---- Countdown ----
        self.add_widget(Label(text="Countdown (z.B. YouTube-Take)", font_size="14sp",
                                size_hint_y=None, height="20dp"))
        cd_row = BoxLayout(orientation="horizontal", size_hint_y=None,
                            height="36dp", spacing="4dp")
        cd_row.add_widget(Label(text="Minuten:", size_hint_x=0.25))
        self.minutes_input = TextInput(text="25", multiline=False, size_hint_x=0.15)
        cd_row.add_widget(self.minutes_input)
        cd_row.add_widget(Label(text="Label:", size_hint_x=0.15))
        self.label_input = TextInput(text="Take", multiline=False, size_hint_x=0.45)
        cd_row.add_widget(self.label_input)
        self.add_widget(cd_row)

        self.countdown_display = Label(text="Countdown: --:--:--", font_size="22sp",
                                         size_hint_y=None, height="34dp")
        self.add_widget(self.countdown_display)

        cd_btns = BoxLayout(orientation="horizontal", size_hint_y=None,
                             height="36dp", spacing="4dp")
        cd_btns.add_widget(Button(text="Start",
                                    on_press=lambda *_: self._send_async(self._send_countdown_start())))
        cd_btns.add_widget(Button(text="Pause",
                                    on_press=lambda *_: self._send_async(self._send_countdown_pause())))
        cd_btns.add_widget(Button(text="Resume",
                                    on_press=lambda *_: self._send_async(self._send_countdown_resume())))
        cd_btns.add_widget(Button(text="Abbrechen",
                                    on_press=lambda *_: self._send_async(self._send_countdown_cancel())))
        self.add_widget(cd_btns)

        # Countdown-Sound-Callback registrieren
        self.munchlax.countdown_finished_callback = self._on_countdown_finished

        # UI-Update-Tick (rein lokal, nutzt Munchlax-Tick-Anker)
        Clock.schedule_interval(self._refresh_display, 0.25)

    # ----- Send-Wrapper -----

    def _send_async(self, coro):
        try:
            asyncio.create_task(coro)
        except Exception as err:
            logger.error(f"async send failed: {type(err)},{err}")
            logger.error(f"{traceback.format_exc()}")

    async def _send_timer_start(self):
        await self.munchlax.send_timer_start()

    async def _send_timer_pause(self):
        await self.munchlax.send_timer_pause_request()

    async def _send_timer_resume(self):
        await self.munchlax.send_timer_resume_request()

    async def _send_timer_split(self):
        await self.munchlax.send_timer_split("manual")

    async def _send_timer_reset(self):
        await self.munchlax.send_timer_reset()

    async def _send_countdown_start(self):
        try:
            minutes = float((self.minutes_input.text or "0").replace(",", "."))
        except ValueError:
            minutes = 0.0
        seconds = max(1.0, minutes * 60.0)
        label = (self.label_input.text or "").strip()
        await self.munchlax.send_countdown_start(seconds, label)

    async def _send_countdown_pause(self):
        await self.munchlax.send_countdown_pause()

    async def _send_countdown_resume(self):
        await self.munchlax.send_countdown_resume()

    async def _send_countdown_cancel(self):
        await self.munchlax.send_countdown_cancel()

    # ----- UI-Refresh -----

    def _refresh_display(self, _dt):
        try:
            elapsed = self.munchlax.current_timer_elapsed()
            running = bool(self.munchlax.timer_state.get("running")) if self.munchlax.timer_state else False
            suffix = "" if running else " (Pause)"
            self.timer_display.text = _format_hms(elapsed) + suffix

            cd = self.munchlax.countdown_state or {}
            if cd.get("duration_seconds", 0) <= 0:
                self.countdown_display.text = "Countdown: --:--:--"
            else:
                remaining = self.munchlax.current_countdown_remaining()
                lbl = cd.get("label", "")
                state = " (Pause)" if not cd.get("running") and not cd.get("finished") else ""
                if cd.get("finished"):
                    state = " ✅ fertig"
                self.countdown_display.text = f"{lbl}: {_format_hms(remaining)}{state}"
        except Exception as err:
            logger.debug(f"refresh_display: {err}")

    # ----- Countdown-Finished-Sound -----

    def _on_countdown_finished(self, label: str, duration_seconds: float):
        logger.info(f"Countdown fertig: '{label}' ({duration_seconds}s) - Ton wird abgespielt")
        try:
            import winsound
            # Zwei Beeps als Signal, wie Kurzzeitwecker
            winsound.Beep(1000, 400)
            winsound.Beep(1500, 400)
        except Exception as err:
            logger.warning(f"winsound.Beep failed: {err}")
            # Fallback: nur loggen — Frontend kann optional Popup zeigen
