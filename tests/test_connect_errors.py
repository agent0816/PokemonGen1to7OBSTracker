"""Klartext-Mapping für Client-Connect-Fehler (Testrunde 2026-11-10, Tailscale)."""
import socket

from backend import connect_errors as ce
from backend.connect_errors import describe_connect_error


def _winerr(code: int, cls=OSError) -> OSError:
    err = cls(f"[WinError {code}] Testfehler")
    err.winerror = code
    return err


def test_refused():
    assert ce.TEXT_REFUSED in describe_connect_error(ConnectionRefusedError())
    assert ce.TEXT_REFUSED in describe_connect_error(_winerr(1225, ConnectionRefusedError))
    assert ce.TEXT_REFUSED in describe_connect_error(_winerr(10061))


def test_timeout_tester_winerror_121():
    # Tester-Log: OSError [WinError 121] Semaphore-Timeout
    assert ce.TEXT_TIMEOUT in describe_connect_error(_winerr(121))
    assert ce.TEXT_TIMEOUT in describe_connect_error(_winerr(10060, TimeoutError))
    assert ce.TEXT_TIMEOUT in describe_connect_error(TimeoutError())


def test_blocked_tester_winerror_5():
    # Tester-Log: PermissionError [WinError 5] Zugriff verweigert
    assert ce.TEXT_BLOCKED in describe_connect_error(_winerr(5, PermissionError))
    assert ce.TEXT_BLOCKED in describe_connect_error(_winerr(10013, PermissionError))


def test_unreachable():
    assert ce.TEXT_UNREACHABLE in describe_connect_error(_winerr(1231))
    assert ce.TEXT_UNREACHABLE in describe_connect_error(_winerr(10065))


def test_bad_host_and_port():
    gai = socket.gaierror(11001, "getaddrinfo failed")
    assert ce.TEXT_BAD_HOST in describe_connect_error(gai)
    assert ce.TEXT_BAD_PORT in describe_connect_error(OverflowError("port must be 0-65535."))


def test_lokaler_server_und_adresse():
    assert ce.TEXT_LOCAL_SERVER in describe_connect_error(
        _winerr(1225, ConnectionRefusedError), local_server=True)
    # Timeout im Host-Modus bleibt Timeout-Text
    assert ce.TEXT_TIMEOUT in describe_connect_error(_winerr(121), local_server=True)
    assert ce.TEXT_BAD_ADDRESS in describe_connect_error(_winerr(10049))
    assert ce.TEXT_BAD_HOST in describe_connect_error(UnicodeError("label empty or too long"))


def test_fallback_nennt_typ():
    assert describe_connect_error(RuntimeError("kaputt")).endswith("RuntimeError: kaputt")
    assert describe_connect_error(OSError()).endswith("OSError")
