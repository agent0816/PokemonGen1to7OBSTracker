"""Drosselt Per-Nachricht-DEBUG-Logs für Hochfrequenz-Message-Typen.

Hintergrund (Live-Session 2026-11-11): Arceus/Munchlax loggten jede gesendete und
empfangene Nachricht einzeln — ``position_sync``/``teams``/``heartbeat``/
``connection_status``/``bag_sync`` machten zusammen mit dem Sprite-Log >90 % von
``main.log`` aus (5 MB in ~12 min, Log-Bundles deckten nur die letzte Stunde ab).

Statt Einzelzeilen werden diese Typen gezählt und höchstens alle ``interval``
Sekunden als eine Summary-Zeile geloggt. Seltene Typen (encounter_sync,
boxes_update, …) loggt der Caller weiterhin einzeln.

Kein Timer-Task: die Summary wird beim nächsten Aufruf nach Ablauf des Intervalls
geschrieben (Hochfrequenz-Typen kommen ohnehin laufend). Thread-sicher per Lock,
da Sender/Empfänger auf unterschiedlichen Threads laufen können.
"""
from __future__ import annotations

import threading
import time
from logging import Logger

HIGH_FREQ_TYPES: frozenset[str] = frozenset({
    "position_sync",
    "teams",
    "heartbeat",
    "connection_status",
    "bag_sync",
})


class MessageLogThrottle:
    def __init__(self, logger: Logger, label: str,
                 high_freq: frozenset[str] = HIGH_FREQ_TYPES,
                 interval: float = 60.0) -> None:
        self.logger = logger
        self.label = label
        self.high_freq = high_freq
        self.interval = interval
        self._lock = threading.Lock()
        # (Richtung, Typ) -> [Anzahl, Bytes]
        self._counts: dict[tuple[str, str], list[int]] = {}
        self._window_start = time.monotonic()

    def is_high_freq(self, msg_type: str) -> bool:
        return msg_type in self.high_freq

    def count(self, direction: str, msg_type: str, nbytes: int) -> None:
        """Zählt eine Hochfrequenz-Nachricht; schreibt ggf. die fällige Summary."""
        summary = None
        with self._lock:
            entry = self._counts.setdefault((direction, msg_type), [0, 0])
            entry[0] += 1
            entry[1] += int(nbytes or 0)
            now = time.monotonic()
            elapsed = now - self._window_start
            if elapsed >= self.interval:
                summary = self._format_summary(elapsed)
                self._counts = {}
                self._window_start = now
        if summary:
            self.logger.debug(summary)

    def _format_summary(self, elapsed: float) -> str:
        parts = []
        for (direction, msg_type), (n, nbytes) in sorted(self._counts.items()):
            size = f" ({nbytes // 1024} KB)" if nbytes else ""
            parts.append(f"{direction} {msg_type}={n}×{size}")
        return f"[{self.label}] Nachrichten-Summary {elapsed:.0f}s: " + ", ".join(parts)
