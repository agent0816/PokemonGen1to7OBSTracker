"""Einmal-Logik fuer den Nuzlocke-Modus-Hinweis (reine Funktion, ohne Kivy)."""
from backend.nuzlocke_view import modus_warnung_faellig


def test_warnt_einmal_pro_session_und_modus():
    show, key = modus_warnung_faellig(None, "s1", "nuzlocke", 2)
    assert show is True
    show, key = modus_warnung_faellig(key, "s1", "nuzlocke", 2)
    assert show is False


def test_kein_hinweis_bei_einem_spieler():
    show, key = modus_warnung_faellig(None, "s1", "nuzlocke", 1)
    assert show is False and key is None


def test_kein_hinweis_in_soullink_und_reset():
    _, key = modus_warnung_faellig(None, "s1", "nuzlocke", 2)
    show, key = modus_warnung_faellig(key, "s1", "coop", 2)
    assert show is False and key is None
    # zurueck auf nuzlocke -> erneut warnen
    show, _ = modus_warnung_faellig(key, "s1", "nuzlocke", 2)
    assert show is True


def test_session_wechsel_warnt_erneut_und_legacy_off():
    _, key = modus_warnung_faellig(None, "s1", "nuzlocke", 3)
    show, _ = modus_warnung_faellig(key, "s2", "nuzlocke", 3)
    assert show is True
    show, _ = modus_warnung_faellig(None, "s1", "off", 2)
    assert show is True
    show, _ = modus_warnung_faellig(None, "s1", None, 2)
    assert show is True
