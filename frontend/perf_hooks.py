"""Kivy-Seite der Performance-Instrumentierung (siehe backend/perf_probe.py).

Alle Funktionen sind ohne ``TRACKER_PERF=1`` sofortige No-ops.
"""
from __future__ import annotations

import gc
import os
import sys
import threading
import time
import traceback
from collections import Counter, deque
from typing import Any

from kivy.clock import Clock
from kivy.core.window import Window

from backend import perf_probe
from backend.perf_probe import perf_log

SLOW_FRAME_S = 0.05
SUMMARY_INTERVAL_S = 60.0
# Nachruckler-Diagnose: GC-Läufe ab dieser Dauer loggen
SLOW_GC_S = 0.02
# Stack-Sampler: Abtastintervall + Puffer (~6 s) + Mindestdauer für Stack-Dump
SAMPLE_INTERVAL_S = 0.01
SAMPLE_BUFFER = 600
STACK_DUMP_FRAME_S = 0.2
STACK_DEPTH = 8
STACK_TOP = 6
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

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
                    if gap >= STACK_DUMP_FRAME_S:
                        _dump_stacks(t - gap, t, f"slow_frame ms={gap * 1000.0:.1f}")
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
    _install_gc_logger()
    _start_stack_sampler()


# --- Nachruckler-Diagnose ----------------------------------------------------

_gc_state: dict[str, Any] = {}


def _install_gc_logger() -> None:
    """Loggt GC-Läufe > SLOW_GC_S (Generation, Dauer, eingesammelte Objekte)."""
    if _gc_state:
        return
    _gc_state["start"] = 0.0

    def _cb(phase: str, info: dict) -> None:
        try:
            if phase == "start":
                _gc_state["start"] = time.perf_counter()
                return
            dur = time.perf_counter() - _gc_state["start"]
            if dur >= SLOW_GC_S:
                perf_log(f"gc gen={info.get('generation')} ms={dur * 1000.0:.1f} "
                         f"collected={info.get('collected')} "
                         f"uncollectable={info.get('uncollectable')}")
        except Exception:
            pass  # GC-Callback darf nie werfen

    _gc_state["cb"] = _cb
    gc.callbacks.append(_cb)


# (perf_counter, kompakter Stack des Main-Threads) — vom Sampler-Thread befüllt
_samples: deque = deque(maxlen=SAMPLE_BUFFER)
_sampler_state: dict[str, Any] = {}


def _compact_stack(frame: Any) -> tuple[str, ...]:
    """Innerste STACK_DEPTH Frames als 'datei:zeile(funktion)', innerster zuerst."""
    out: list[str] = []
    while frame is not None and len(out) < STACK_DEPTH:
        code = frame.f_code
        path = code.co_filename
        if path.startswith(_PROJECT_ROOT):
            path = os.path.relpath(path, _PROJECT_ROOT)
            if path.startswith(".venv"):
                # site-packages-Präfix kürzen: kivy/uix/boxlayout.py statt .venv/…
                idx = path.find("site-packages")
                if idx >= 0:
                    path = path[idx + len("site-packages") + 1:]
        else:
            path = os.path.basename(path)
        out.append(f"{path}:{frame.f_lineno}({code.co_name})")
        frame = frame.f_back
    return tuple(out)


def _start_stack_sampler() -> None:
    """Daemon-Thread tastet alle SAMPLE_INTERVAL_S den Stack des Kivy-Main-Threads
    ab. Bei einem Frame > STACK_DUMP_FRAME_S fasst _dump_stacks die Samples aus
    genau diesem Zeitfenster zusammen → zeigt, WAS den Frame blockiert hat."""
    if _sampler_state:
        return
    main_id = threading.main_thread().ident

    def _run() -> None:
        while True:
            try:
                frame = sys._current_frames().get(main_id)
                if frame is not None:
                    _samples.append((time.perf_counter(), _compact_stack(frame)))
                del frame
            except Exception:
                pass
            time.sleep(SAMPLE_INTERVAL_S)

    thread = threading.Thread(target=_run, name="perf-stack-sampler", daemon=True)
    _sampler_state["thread"] = thread
    thread.start()


def _dump_stacks(t_start: float, t_end: float, label: str) -> None:
    """Häufigste Main-Thread-Stacks im Fenster [t_start, t_end] loggen."""
    window = [stack for (t, stack) in list(_samples) if t_start <= t <= t_end]
    if not window:
        perf_log(f"stacks {label}: keine Samples im Fenster")
        return
    # 1) Innerste Funktion (wo die Zeit verbrannt wird)
    leaf = Counter(stack[0] for stack in window if stack)
    # 2) Erste Projekt-Funktion im Stack (wer es ausgelöst hat)
    def _first_project(stack: tuple[str, ...]) -> str:
        for entry in stack:
            if entry.startswith(("frontend", "backend")):
                return entry
        return "(kein Projekt-Frame)"
    owner = Counter(_first_project(stack) for stack in window)
    full = Counter(window)
    n = len(window)
    perf_log(f"stacks {label}: samples={n}")
    for entry, cnt in owner.most_common(STACK_TOP):
        perf_log(f"  owner {cnt * 100 // n:3d}% {entry}")
    for entry, cnt in leaf.most_common(STACK_TOP):
        perf_log(f"  leaf  {cnt * 100 // n:3d}% {entry}")
    for stack, cnt in full.most_common(3):
        perf_log(f"  stack {cnt * 100 // n:3d}% " + " <- ".join(stack))


def count_widgets(widget: Any) -> int:
    """Anzahl Widgets im Teilbaum (inkl. des Widgets selbst)."""
    return sum(1 for _ in widget.walk(restrict=True))
