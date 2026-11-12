"""Tests für backend/msg_log_throttle.py (Drosselung Hochfrequenz-Message-Logs)."""
import logging

from backend import msg_log_throttle
from backend.msg_log_throttle import MessageLogThrottle


class _Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def _throttle(monkeypatch, caplog, interval=60.0):
    clock = _Clock()
    monkeypatch.setattr(msg_log_throttle.time, "monotonic", clock)
    logger = logging.getLogger("throttle_test")
    caplog.set_level(logging.DEBUG, logger="throttle_test")
    return MessageLogThrottle(logger, "test", interval=interval), clock


def test_high_freq_typen():
    t = MessageLogThrottle(logging.getLogger("x"), "x")
    for typ in ("position_sync", "teams", "heartbeat", "connection_status", "bag_sync"):
        assert t.is_high_freq(typ)
    assert not t.is_high_freq("encounter_sync")
    assert not t.is_high_freq("boxes_update")


def test_vor_intervall_keine_zeile(monkeypatch, caplog):
    t, clock = _throttle(monkeypatch, caplog)
    for _ in range(100):
        t.count("send", "teams", 500)
    clock.t += 59
    t.count("send", "teams", 500)
    assert caplog.records == []


def test_summary_nach_intervall_und_reset(monkeypatch, caplog):
    t, clock = _throttle(monkeypatch, caplog)
    t.count("send", "teams", 2048)
    t.count("send", "teams", 2048)
    t.count("recv", "heartbeat", 0)
    clock.t += 60
    t.count("send", "position_sync", 1024)
    assert len(caplog.records) == 1
    msg = caplog.records[0].getMessage()
    assert "[test] Nachrichten-Summary" in msg
    assert "send teams=2× (4 KB)" in msg
    assert "recv heartbeat=1×" in msg
    assert "heartbeat=1× (" not in msg  # ohne Bytes keine KB-Angabe
    assert "send position_sync=1×" in msg
    # Fenster zurückgesetzt: direkt danach keine weitere Summary
    caplog.clear()
    t.count("send", "teams", 1)
    assert caplog.records == []
