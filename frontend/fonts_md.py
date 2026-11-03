"""Registriert Roboto mit ♂/♀-Glyphen als Schrift "Roboto".

KivyMD registriert beim Import "Roboto" aus kivymd/fonts — dort fehlen
U+2640/U+2642 ("Nidoran♂" mit Kaestchen, Testrunde 2026-11-02). Die
ergaenzten Schnitte erzeugt tools/merge_font.py nach backend/ressources/fonts/
(im Nuitka-Build ueber --include-data-dir=backend/ressources enthalten).

Muss nach `import kivymd` und vor dem ersten gerenderten Label laufen; alle
KivyMD-Font-Styles verweisen auf den Namen "Roboto" und greifen dadurch
automatisch auf die neuen Dateien zu.
"""
from __future__ import annotations

import os
import traceback

from kivy.core.text import LabelBase

from backend.logging_setup import get_logger

logger = get_logger(__name__, 'logs/frontend.log')

_FONT_REL = "backend/ressources/fonts"
# Repo-Root (Dev) bzw. Onefile-Unpack-Dir. Bewusst nicht
# backend.pokemon_detail_view.resource_path: dessen Import kostet ~1,6 s und
# laeuft hier auf dem Startpfad (appmd-Modulebene).
_PKG_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _resource_path(rel: str) -> str:
    for base in (os.getcwd(), _PKG_ROOT):
        path = os.path.join(base, rel)
        if os.path.exists(path):
            return path
    return ""


_STYLES = {
    "fn_regular": "Regular",
    "fn_bold": "Bold",
    "fn_italic": "Italic",
    "fn_bolditalic": "BoldItalic",
}


def register_gender_fonts() -> bool:
    """True, wenn die ergaenzte Roboto registriert wurde.

    Fehlt eine Datei, bleibt die KivyMD-Roboto aktiv (nur ♂/♀ fehlen dann).
    """
    try:
        paths = {}
        for key, style in _STYLES.items():
            path = _resource_path(f"{_FONT_REL}/RobotoGender-{style}.ttf")
            if not path:
                logger.warning(
                    f"RobotoGender-{style}.ttf fehlt — Standard-Roboto bleibt (ohne ♂/♀)")
                return False
            paths[key] = path
        LabelBase.register(name="Roboto", **paths)
        logger.info("Roboto mit ♂/♀-Glyphen registriert")
        return True
    except Exception as err:
        logger.error(f"register_gender_fonts failed: {type(err).__name__},{err}")
        logger.error(traceback.format_exc())
        return False
