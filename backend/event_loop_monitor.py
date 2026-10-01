"""Event-Loop-Latency-Monitor.

Misst, wie viel laenger ein ``await asyncio.sleep(interval)`` tatsaechlich
dauert als interval. Wenn der Kivy-Thread (Click-Handler, Widget-Rebuild,
Popup-Open, Toast, Clock-Scheduled-Callback) oder ein anderer Executor den
Event-Loop blockiert, schiebt sich das direkt in diesen Drift. Weil der
Lua-Loop auf blocking ``comm.socketServerResponse()`` wartet, uebertraegt
sich Event-Loop-Latency 1:1 auf BizHawk-Framerate.

Verwendung: Einmal pro Prozess ``start_event_loop_monitor()`` aufrufen.
Der Monitor loggt pro Drift-Event ueber ``SLOW_DRIFT_S`` eine WARN-Zeile
und alle 60s eine INFO-Zusammenfassung (count, max, p95-ish via max/avg).
"""
from __future__ import annotations

import asyncio
import time

from backend.logging_setup import get_logger

logger = get_logger(__name__, './logs/event_loop_monitor.log')

TICK_INTERVAL_S = 0.1
SLOW_DRIFT_S = 0.05
SUMMARY_INTERVAL_S = 60.0

_monitor_task: asyncio.Task | None = None


async def _run_monitor():
    stats = {"count": 0, "slow": 0, "sum_drift": 0.0, "max_drift": 0.0}
    next_summary = time.perf_counter() + SUMMARY_INTERVAL_S
    last_slow_warn_at = 0.0
    try:
        while True:
            t0 = time.perf_counter()
            await asyncio.sleep(TICK_INTERVAL_S)
            try:
                elapsed = time.perf_counter() - t0
                drift = elapsed - TICK_INTERVAL_S
                stats["count"] += 1
                stats["sum_drift"] += max(0.0, drift)
                if drift > stats["max_drift"]:
                    stats["max_drift"] = drift
                if drift > SLOW_DRIFT_S:
                    stats["slow"] += 1
                    # Rate-Limit (1 WARN/sek), analog zum Python-Slow-Tick-
                    # Detektor: bei dauerhaftem Lag sonst Instrument selbst
                    # Last-Treiber.
                    now_warn = time.perf_counter()
                    if now_warn - last_slow_warn_at >= 1.0:
                        last_slow_warn_at = now_warn
                        logger.warning(
                            f"Event-Loop-Drift: {drift*1000:.1f}ms "
                            f"(sleep({TICK_INTERVAL_S}s) returned nach "
                            f"{elapsed*1000:.1f}ms)"
                        )
                now = time.perf_counter()
                if now >= next_summary and stats["count"] > 0:
                    avg_drift = stats["sum_drift"] / stats["count"]
                    logger.info(
                        f"Loop-Latency-Summary: ticks={stats['count']}, "
                        f"slow(>{int(SLOW_DRIFT_S*1000)}ms)={stats['slow']}, "
                        f"avg_drift={avg_drift*1000:.1f}ms, "
                        f"max_drift={stats['max_drift']*1000:.1f}ms"
                    )
                    stats["count"] = 0
                    stats["slow"] = 0
                    stats["sum_drift"] = 0.0
                    stats["max_drift"] = 0.0
                    next_summary = now + SUMMARY_INTERVAL_S
            except Exception as err:  # pragma: no cover
                logger.error(f"Monitor-Iteration failed: {err}")
                import traceback
                logger.error(traceback.format_exc())
    except asyncio.CancelledError:
        logger.info("Event-Loop-Monitor cancelled.")
        raise


def start_event_loop_monitor() -> None:
    """Startet den Monitor einmal pro Prozess (idempotent).

    Muss aus einem Kontext mit laufendem Event-Loop aufgerufen werden.
    """
    global _monitor_task
    if _monitor_task is not None and not _monitor_task.done():
        return
    _monitor_task = asyncio.create_task(_run_monitor())
    logger.info(
        f"Event-Loop-Monitor gestartet "
        f"(tick={TICK_INTERVAL_S}s, slow_threshold={SLOW_DRIFT_S*1000:.0f}ms)"
    )


async def stop_event_loop_monitor() -> None:
    """Stoppt den Monitor sauber (cancel + await). Shutdown-Hook."""
    global _monitor_task
    if _monitor_task is None or _monitor_task.done():
        _monitor_task = None
        return
    _monitor_task.cancel()
    try:
        await _monitor_task
    except (asyncio.CancelledError, Exception):
        pass
    _monitor_task = None
