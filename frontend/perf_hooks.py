"""Kivy-Seite der Performance-Instrumentierung (siehe backend/perf_probe.py).

Alle Funktionen sind ohne ``TRACKER_PERF=1`` sofortige No-ops.
"""
from __future__ import annotations

import time
import traceback
from typing import Any

from kivy.clock import Clock
from kivy.core.window import Window

from backend import perf_probe
from backend.perf_probe import perf_log

SLOW_FRAME_S = 0.05
SUMMARY_INTERVAL_S = 60.0

# Strong-Refs auf Frame-Watchdog-State (Clock hält nur schwache Refs auf Methoden)
_watchdog_state: dict[str, Any] = {}


def log_on_next_frame(label: str, t0: float, extra: str = "") -> None:
    """Loggt beim nächsten Window-Flip die Dauer seit t0 (perf_counter)."""
    if not perf_probe.ENABLED:
        return

    def _on_flip(*_args: Any) -> None:
        Window.unbind(on_flip=_on_flip)
        try:
            ms = (time.perf_counter() - t0) * 1000.0
            perf_log(f"frame label={label} ms={ms:.1f} {extra}".rstrip())
        except Exception:
            perf_log(f"log_on_next_frame-Fehler: {traceback.format_exc()}")

    Window.bind(on_flip=_on_flip)


def install_frame_watchdog(screen_manager: Any) -> None:
    """Misst den Abstand zwischen Kivy-Frames und loggt Ausreißer (>50 ms)."""
    if not perf_probe.ENABLED or _watchdog_state:
        return
    now = time.perf_counter()
    _watchdog_state.update(
        last=now, last_warn=0.0, next_summary=now + SUMMARY_INTERVAL_S,
        frames=0, slow=0, max_s=0.0, suppressed=0,
    )

    def _tick(_dt: float) -> None:
        try:
            st = _watchdog_state
            t = time.perf_counter()
            gap = t - st["last"]
            st["last"] = t
            st["frames"] += 1
            if gap > SLOW_FRAME_S:
                st["slow"] += 1
                if gap > st["max_s"]:
                    st["max_s"] = gap
                if t - st["last_warn"] >= 1.0:
                    st["last_warn"] = t
                    perf_log(f"slow_frame ms={gap * 1000.0:.1f} screen={screen_manager.current}")
                else:
                    st["suppressed"] += 1
            if t >= st["next_summary"]:
                perf_log(
                    f"frame_summary frames={st['frames']} slow={st['slow']} "
                    f"max_ms={st['max_s'] * 1000.0:.1f} suppressed={st['suppressed']}"
                )
                st.update(frames=0, slow=0, max_s=0.0, suppressed=0, next_summary=t + SUMMARY_INTERVAL_S)
        except Exception:
            perf_log(f"frame_watchdog-Fehler: {traceback.format_exc()}")

    _watchdog_state["event"] = Clock.schedule_interval(_tick, 0)


def count_widgets(widget: Any) -> int:
    """Anzahl Widgets im Teilbaum (inkl. des Widgets selbst)."""
    return sum(1 for _ in widget.walk(restrict=True))
