"""Mess-Instrumentierung für die Performance-Analyse (Kivy-frei).

Aktivierung ausschließlich per Umgebungsvariable ``TRACKER_PERF=1``. Ohne Flag
ist alles ein No-op: der Decorator gibt die Originalfunktion zurück, der
Context-Manager tut nichts, es entsteht keine ``logs/perf.log``.

Verwendung::

    from backend.perf_probe import perf_span, perf_log

    @perf_span("settings.on_pre_enter")      # als Decorator
    def on_pre_enter(self): ...

    with perf_span("session.read"):          # als Context-Manager
        ...

    perf_log("freie Zeile")

``TRACKER_PERF_PROFILE=<span-name>`` legt um genau diesen Span zusätzlich
einen cProfile-Lauf und schreibt den Dump nach
``logs/perf/<name>_<YYYYmmdd_HHMMSS_ffffff>.prof``.
"""
from __future__ import annotations

import contextlib
import cProfile
import functools
import os
import threading
import time
import traceback
from datetime import datetime
from typing import Any, Callable, TypeVar

from backend.logging_setup import get_logger

ENABLED: bool = os.environ.get("TRACKER_PERF", "").strip() == "1"
PROFILE_SPAN: str | None = os.environ.get("TRACKER_PERF_PROFILE", "").strip() or None

_F = TypeVar("_F", bound=Callable[..., Any])

_local = threading.local()
_logger = None


def _get_logger():
    """Logger lazy anlegen, damit ohne Flag keine perf.log entsteht."""
    global _logger
    if _logger is None:
        _logger = get_logger("backend.perf_probe", "./logs/perf.log")
    return _logger


def perf_log(msg: str) -> None:
    """Freie INFO-Zeile in perf.log (no-op ohne Flag)."""
    if not ENABLED:
        return
    try:
        _get_logger().info(msg)
    except Exception:
        # Messung darf die App nie beeinträchtigen
        pass


class perf_span(contextlib.ContextDecorator):
    """Misst die Dauer eines Blocks bzw. einer Funktion (siehe Modul-Docstring)."""

    def __init__(self, name: str) -> None:
        self.name = name
        self._stack: list[tuple[float, int, Any]] = []

    def __call__(self, func: _F) -> _F:  # type: ignore[override]
        # Ohne Flag: Originalfunktion unverändert (null Overhead)
        if not ENABLED:
            return func
        name = self.name

        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            with perf_span(name):
                return func(*args, **kwargs)

        return wrapper  # type: ignore[return-value]

    def __enter__(self) -> "perf_span":
        if not ENABLED:
            return self
        depth = getattr(_local, "depth", 0)
        _local.depth = depth + 1
        prof = None
        if PROFILE_SPAN is not None and self.name == PROFILE_SPAN:
            try:
                prof = cProfile.Profile()
                prof.enable()
            except Exception:
                prof = None
        self._stack.append((time.perf_counter(), depth, prof))
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        if not ENABLED or not self._stack:
            return False
        t0, depth, prof = self._stack.pop()
        dt_ms = (time.perf_counter() - t0) * 1000.0
        _local.depth = depth
        try:
            if prof is not None:
                prof.disable()
            suffix = " error=1" if exc_type is not None else ""
            _get_logger().info(f"{'  ' * depth}span={self.name} ms={dt_ms:.1f} depth={depth}{suffix}")
            if prof is not None:
                os.makedirs("logs/perf", exist_ok=True)
                stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
                path = os.path.join("logs", "perf", f"{self.name}_{stamp}.prof")
                prof.dump_stats(path)
                _get_logger().info(f"{'  ' * depth}profile span={self.name} file={path}")
        except Exception:
            try:
                _get_logger().error(f"perf_span-Fehler: {traceback.format_exc()}")
            except Exception:
                pass
        return False
