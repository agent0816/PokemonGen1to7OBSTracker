"""Gemeinsame Spieler-Farbpalette für Team-Anzeige (Hauptmenü) und Pokedex.

Eine Quelle, damit klassische UI (TrainerBox) und KivyMD-UI (Pokedex-Karten,
Spieler-Buttons) dieselben Farben pro Spieler-Slot zeigen. Kein Kivy-Import
(backend-Schicht).

Format der Werte: 8-stelliger RGBA-Hex-String ohne '#' (``RRGGBBAA``), wie ihn
``TrainerBox`` erwartet (parst ``color[0:2]/[2:4]/[4:6]/[6:8]``). ``AA`` = 7f
entspricht ~50 % Alpha.
"""
from __future__ import annotations

# Slot (1-basiert) -> RGBA-Hex8. Werte historisch aus mainmenu.create_pokemon_frame.
PLAYER_COLORS: dict[int, str] = {
    1: "1a4d9a7f",
    2: "9a671a7f",
    3: "9a9a1a7f",
    4: "1a9a1a7f",
}


def hex8_to_rgba(hex8: str, alpha: float | None = None) -> tuple[float, float, float, float]:
    """Wandelt ``RRGGBBAA`` in ein (r, g, b, a)-Float-Tupel (0..1) für Kivy.

    ``alpha`` überschreibt den Alpha-Anteil aus dem Hex-String (0..1), nützlich
    wenn dieselbe Farbe mal getönt (halbtransparent) und mal als volle
    Akzent-Linie gebraucht wird.
    """
    h = hex8.lstrip("#")
    if len(h) == 6:
        h = h + "ff"
    if len(h) != 8:
        raise ValueError(f"RGBA-Hex8 erwartet (RRGGBBAA), bekam: {hex8!r}")
    r = int(h[0:2], 16) / 255
    g = int(h[2:4], 16) / 255
    b = int(h[4:6], 16) / 255
    a = int(h[6:8], 16) / 255 if alpha is None else alpha
    return (r, g, b, a)


def player_rgba(slot: int, alpha: float | None = None) -> tuple[float, float, float, float]:
    """RGBA-Tupel für einen Spieler-Slot; fällt bei unbekanntem Slot auf Slot 1 zurück."""
    return hex8_to_rgba(PLAYER_COLORS.get(slot, PLAYER_COLORS[1]), alpha)
