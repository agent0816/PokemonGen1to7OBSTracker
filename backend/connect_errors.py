"""Verständliche Texte für fehlgeschlagene Client-Verbindungen (Kivy-frei).

Testrunde 2026-11-10: WinError 5 / 121 beim Connect über Tailscale landeten
nur im Log, die App zeigte nichts. Diese Funktion übersetzt die Exception aus
``Munchlax.connect`` in einen Satz für die Snackbar.
"""
import socket

# Windows-Fehlercodes (winerror) → Kategorie. Winsock-Codes (100xx) kommen bei
# Selector-Loops, die Win32-Codes (12xx, 121, 5) beim Proactor-Loop.
_REFUSED = {10061, 1225}
_TIMEOUT = {10060, 121, 1460}
_UNREACHABLE = {10051, 10065, 1231, 1232}
_BLOCKED = {5, 10013}

TEXT_REFUSED = "Server antwortet nicht – läuft der Host? Port richtig?"
TEXT_TIMEOUT = ("Host nicht erreichbar – IP, Port-Freigabe/Firewall am Host "
                "oder VPN (Tailscale) prüfen.")
TEXT_UNREACHABLE = "Netzwerk/Host nicht erreichbar – IP und VPN (Tailscale) prüfen."
TEXT_BLOCKED = ("Verbindung lokal blockiert – Windows-Firewall/Virenscanner "
                "für den Tracker freigeben.")
TEXT_BAD_HOST = "Hostname/IP ungültig – Server-Adresse in den Einstellungen prüfen."
TEXT_BAD_PORT = "Port ungültig – Server-Port in den Einstellungen prüfen (1–65535)."
TEXT_BAD_ADDRESS = "Adresse ungültig – Server-IP und Port in den Einstellungen prüfen."
TEXT_LOCAL_SERVER = ("Eigener Server nicht erreichbar – Port belegt oder von "
                     "Firewall blockiert? Server neu starten.")

_BAD_ADDRESS = {10049}


def describe_connect_error(exc: BaseException, local_server: bool = False) -> str:
    """Exception aus dem Client-Connect → kurzer deutscher Hinweistext.

    ``local_server``: Host-Modus, der lokale Client verbindet sich mit dem
    eigenen Arceus auf 127.0.0.1 — "läuft der Host?" wäre dort irreführend.
    """
    prefix = "Verbindung fehlgeschlagen: "
    winerror = getattr(exc, "winerror", None)
    if local_server and (winerror in _REFUSED or isinstance(exc, ConnectionRefusedError)):
        return prefix + TEXT_LOCAL_SERVER
    # gaierror ist OSError-Subklasse → vor den winerror-/Typ-Checks prüfen.
    # UnicodeError: idna-Encoding bei leerem/zu langem Hostnamen.
    if isinstance(exc, (socket.gaierror, UnicodeError)):
        return prefix + TEXT_BAD_HOST
    if winerror in _BAD_ADDRESS:
        return prefix + TEXT_BAD_ADDRESS
    if winerror in _BLOCKED or (winerror is None and isinstance(exc, PermissionError)):
        return prefix + TEXT_BLOCKED
    if winerror in _REFUSED or isinstance(exc, ConnectionRefusedError):
        return prefix + TEXT_REFUSED
    if winerror in _TIMEOUT or isinstance(exc, TimeoutError):
        return prefix + TEXT_TIMEOUT
    if winerror in _UNREACHABLE:
        return prefix + TEXT_UNREACHABLE
    if isinstance(exc, OverflowError):
        # asyncio/socket: "port must be 0-65535"
        return prefix + TEXT_BAD_PORT
    detail = str(exc).strip()
    name = type(exc).__name__
    return f"{prefix}{name}: {detail}" if detail else f"{prefix}{name}"
