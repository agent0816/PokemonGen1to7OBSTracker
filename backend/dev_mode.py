"""Developer-Mode-Detection fuer conditional UI-Features.

Aktiv wenn Tracker aus Source laeuft (nicht als PyInstaller-Binary) ODER
wenn die Umgebungsvariable ``TRACKER_DEV=1`` gesetzt ist. Damit wird die
Dev-GUI (z.B. per-Modul Log-Level-Editor unter Settings) fuer normale
User in der Release-Binary unsichtbar, aber Support-Sessions koennen sie
via ENV auch dort scharfschalten ohne Rebuild.
"""

import os
import sys


def is_developer() -> bool:
    """True wenn Dev-Features im UI angezeigt werden sollen.

    Trigger:
      - ``sys.frozen`` fehlt (Source-Run via ``python main.py``)
      - oder ``TRACKER_DEV`` Env-Var auf ``"1"`` gesetzt (Override in Binary)
    """
    if not getattr(sys, 'frozen', False):
        return True
    return os.environ.get('TRACKER_DEV') == '1'
