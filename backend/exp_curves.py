"""EXP-Wachstumskurven (ohne Kivy).

Growth-Kurve pro Spezies aus backend/data/growth_rates.yml (Vanilla, Quelle
veekun/pokedex). CAVEAT: Der Universal Randomizer kann per "Standardize EXP
Curves" die ROM-Kurve ändern (ordinary→medium_fast, legendär→slow); das steht
NICHT im Rando-Log (verifiziert gegen UPR-ZX-Source), daher nur Vanilla
verfügbar. Für Party-Pokemon lässt sich die tatsächliche Kurve oft aus dem
(Level, EXP)-Paar ableiten (`infer_curve`).

Genutzt vom Decoder (Box-Level aus EXP), der Detailansicht (Fortschrittsbalken)
und dem Pokemon-Editor (EXP für neues Level).
"""
from __future__ import annotations

import yaml

from backend.logging_setup import get_logger

logger = get_logger(__name__, './logs/exp_curves.log')

CURVES = ("medium_fast", "fast", "slow", "medium_slow", "erratic", "fluctuating")

_growth_rates: dict[int, str] | None = None


def _load_growth_rates() -> dict[int, str]:
    global _growth_rates
    if _growth_rates is None:
        try:
            with open("backend/data/growth_rates.yml", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
        except Exception as err:
            logger.error(f"growth_rates.yml laden fehlgeschlagen: {err}")
            data = {}
        _growth_rates = {int(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}
    return _growth_rates


def growth_curve(dexnr) -> str:
    """Vanilla-Growth-Kurve einer Spezies; 'medium_fast' als Default."""
    try:
        return _load_growth_rates().get(int(dexnr), "medium_fast")
    except (TypeError, ValueError):
        return "medium_fast"


def exp_at_level(curve: str, n: int) -> int:
    """Gesamt-EP, um Level ``n`` zu erreichen (n≥1; Level 1 = 0).

    Formeln nach Bulbapedia/veekun für die sechs Kurven.
    """
    if n <= 1:
        return 0
    if curve == "medium_fast":
        val = n ** 3
    elif curve == "fast":
        val = 4 * n ** 3 / 5
    elif curve == "slow":
        val = 5 * n ** 3 / 4
    elif curve == "medium_slow":
        val = 6 / 5 * n ** 3 - 15 * n ** 2 + 100 * n - 140
    elif curve == "erratic":
        if n <= 50:
            val = n ** 3 * (100 - n) / 50
        elif n <= 68:
            val = n ** 3 * (150 - n) / 100
        elif n <= 98:
            val = n ** 3 * ((1911 - 10 * n) // 3) / 500
        else:
            val = n ** 3 * (160 - n) / 100
    elif curve == "fluctuating":
        if n <= 15:
            val = n ** 3 * ((n + 1) // 3 + 24) / 50
        elif n <= 36:
            val = n ** 3 * (n + 14) / 50
        else:
            val = n ** 3 * (n // 2 + 32) / 50
    else:
        val = n ** 3
    return max(0, int(val))


def level_from_exp(curve: str, exp: int) -> int:
    """Höchstes Level (1–100), dessen EP-Schwelle ``exp`` erreicht."""
    try:
        exp = int(exp)
    except (TypeError, ValueError):
        return 1
    lvl = 1
    for n in range(2, 101):
        if exp_at_level(curve, n) > exp:
            break
        lvl = n
    return lvl


def infer_curve(dexnr, level: int, exp: int) -> str:
    """Kurve, die zum (Level, EXP)-Paar eines Party-Pokemon passt.

    Bevorzugt die Vanilla-Kurve, wenn sie passt (Normalfall). Sonst die einzige
    passende andere Kurve (z.B. nach UPR "Standardize EXP Curves"). Bei keiner
    oder mehreren passenden Kandidaten: Vanilla-Kurve als Fallback.
    """
    vanilla = growth_curve(dexnr)
    try:
        level = int(level)
        exp = int(exp)
    except (TypeError, ValueError):
        return vanilla
    if not 1 <= level <= 100:
        return vanilla

    def _fits(curve: str) -> bool:
        if exp < exp_at_level(curve, level):
            return False
        return level == 100 or exp < exp_at_level(curve, level + 1)

    if _fits(vanilla):
        return vanilla
    candidates = [c for c in CURVES if c != vanilla and _fits(c)]
    if len(candidates) == 1:
        logger.info(f"infer_curve: #{dexnr} L{level} EXP {exp} passt nicht zu {vanilla}, nutze {candidates[0]}")
        return candidates[0]
    logger.debug(f"infer_curve: #{dexnr} L{level} EXP {exp} mehrdeutig/keine Kurve ({candidates}), Fallback {vanilla}")
    return vanilla
