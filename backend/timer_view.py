"""Anzeige-Logik Race-Timer (Kivy-frei, gemeinsam für Timer-Tab + Status-Leiste).

Server-Semantik (arceus): Host pausiert/setzt direkt fort, Mitspieler brauchen
den Konsens aller Spieler (pause_consent/resume_consent gegen consent_required).
"""
from __future__ import annotations


def _hms(seconds: float) -> str:
    # Kivy-freie Kopie von md_common.format_hms (md_common importiert kivymd)
    s = int(max(0, seconds or 0))
    return f"{s // 3600:02d}:{(s // 60) % 60:02d}:{s % 60:02d}"


def race_status_suffix(timer_state: dict | None) -> str:
    """Zustands-Zusatz hinter der Zeit.

    - nie gestartet / resettet → "" (früher fälschlich "(Pause)")
    - pausiert → " (Pause)", mit offener Fortsetzen-Anfrage " (Fortsetzen angefragt n/m)"
    - läuft mit offener Pause-Anfrage → " (Pause angefragt n/m)"
    """
    ts = timer_state or {}
    if ts.get("start_ts") is None:
        return ""
    required = int(ts.get("consent_required") or 0)

    def _count(key: str) -> str:
        n = len(ts.get(key) or [])
        return f"{n}/{required}" if required else str(n)

    if ts.get("running"):
        return f" (Pause angefragt {_count('pause_consent')})" if ts.get("pause_consent") else ""
    if ts.get("resume_consent"):
        return f" (Pause, Fortsetzen angefragt {_count('resume_consent')})"
    return " (Pause)"


def split_lines(timer_state: dict | None) -> list[str]:
    """Splits als Textzeilen, älteste zuerst: "1. 00:12:34  Label"."""
    lines = []
    for i, sp in enumerate((timer_state or {}).get("splits") or [], start=1):
        if not isinstance(sp, dict):
            continue
        label = str(sp.get("label") or "")
        if label in ("manual", "split"):
            label = ""
        lines.append(f"{i}. {_hms(sp.get('elapsed', 0))}  {label}".rstrip())
    return lines
