"""Gemeinsame Test-Konfiguration fuer alle Tests unter tests/.

Leitet die Modul-Logs (backend/logging_setup.py) in ein Temp-Verzeichnis um,
BEVOR ein Testmodul Backend-Code importiert (conftest wird vorher geladen).
Sonst schreiben Tests in die echten logs/*.log und rotieren sie beim ersten
Oeffnen — das verfaelscht die Logs eines parallel laufenden Trackers.
"""
from __future__ import annotations

import os
import tempfile

os.environ.setdefault("TRACKER_PYTEST_LOG_DIR", tempfile.mkdtemp(prefix="tracker-test-logs-"))
