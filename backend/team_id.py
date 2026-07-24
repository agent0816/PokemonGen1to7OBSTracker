"""Gemeinsame Slug-Funktion für Team-IDs.

Client (Frontend URL-Generierung) und Server (URL-Match / Team-State-Compute)
müssen bit-identische Slugs liefern, sonst brechen Team-Badge-URLs mit 404.
"""

import re


def slug_team_id(raw) -> str:
    """URL-safer Slug für team_id: [a-zA-Z0-9_-], Fallback 'team'."""
    s = re.sub(r'[^a-zA-Z0-9_-]', '_', str(raw or '')).strip('_')
    return s or 'team'
