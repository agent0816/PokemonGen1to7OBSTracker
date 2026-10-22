"""Nuitka-Onefile-Splash abbauen, sobald die App ihr eigenes Fenster zeigt.

Nuitka blendet (nur im Windows-Onefile-Build, Flag
``--onefile-windows-splash-screen-image=<bild>``) sofort beim Start ein Bild ein
— noch bevor Python/Kivy überhaupt laufen. Das deckt genau die Phase ab, die
Kivy selbst nicht bemalen kann (Onefile-Entpacken, Interpreter-Start, Imports,
GL-Fenster-Init). Das Programm muss den Splash aktiv schließen, indem es die von
Nuitka angelegte Feedback-Datei löscht.

Reines Modul ohne Kivy-Import. Im Quell-/Dev-Lauf ist ``NUITKA_ONEFILE_PARENT``
nicht gesetzt → No-op.
"""

import os
import tempfile

from backend.logging_setup import get_logger

logger = get_logger(__name__, 'logs/frontend.log')

_dismissed = False


def dismiss_splash() -> None:
    """Nuitka-Splash schließen (idempotent). No-op außerhalb des Onefile-Builds."""
    global _dismissed
    if _dismissed:
        return
    parent = os.environ.get("NUITKA_ONEFILE_PARENT")
    if not parent:
        return
    try:
        splash_path = os.path.join(
            tempfile.gettempdir(),
            f"onefile_{int(parent)}_splash_feedback.tmp",
        )
        if os.path.exists(splash_path):
            os.unlink(splash_path)
        _dismissed = True
        logger.info("Nuitka-Onefile-Splash geschlossen.")
    except Exception as err:
        # Darf den Start nie verhindern.
        logger.warning(f"Nuitka-Splash konnte nicht geschlossen werden: {err}")
