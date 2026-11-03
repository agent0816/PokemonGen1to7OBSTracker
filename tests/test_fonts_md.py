"""Roboto mit ♂/♀ (Testrunde 2026-11-02): Dateien vorhanden + registrierbar."""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("KIVY_NO_ARGS", "1")


def test_gender_fonts_registriert_und_rendert_glyphen():
    import kivymd  # noqa: F401  registriert Standard-Roboto
    from kivy.core.text import Label as CoreLabel, LabelBase
    from frontend.fonts_md import register_gender_fonts

    assert register_gender_fonts() is True
    assert "RobotoGender-Regular.ttf" in LabelBase._fonts["Roboto"][0]
    def width(text):
        lbl = CoreLabel(text=text, font_size=40, font_name="Roboto")
        lbl.refresh()
        return lbl.texture.width

    # Fehlende Glyphe (Private Use U+E000) rendert als .notdef-Kaestchen;
    # ♂/♀ haben eigene, breitere Advance-Werte.
    tofu = width("")
    assert width("♂") != tofu
    assert width("♀") != tofu
