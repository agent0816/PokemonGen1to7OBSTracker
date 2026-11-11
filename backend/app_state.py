"""App-weiter, NICHT session-gebundener Zustand (UI-Modus, Onboarding-Marker).

Bewusst ohne Kivy-Import und ohne weitere Projekt-Abhaengigkeiten, damit das
Modul sehr frueh in ``main.py`` — noch vor dem Kivy-/Window-Import — geladen
werden kann (die UI-Weiche ``TRACKER_UI``/``app_state`` muss vor dem Import der
App-Klasse feststehen).

Persistiert in ``backend/config/app_state.yml``, parallel zu
``log_settings.yml`` (siehe ``backend/logging_setup.py``) und ebenso von der
Aufraeum-Logik in ``initialize_tree.init_config_folder`` ausgenommen.
"""

import os
import yaml

_APP_STATE_PATH = 'backend/config/app_state.yml'

# Gueltige UI-Modi + Default. MD ist seit der KivyMD-Umstellung (Phase F)
# der Standard; die klassische UI bleibt als Notausgang per Toggle erreichbar.
UI_MD = 'md'
UI_CLASSIC = 'classic'
_VALID_UI = (UI_MD, UI_CLASSIC)

# KivyMD-Theme (MD-UI): Hell/Dunkel + Akzentfarbe. primary_palette nimmt in
# KivyMD 2 jeden kivy.utils.hex_colormap-Namen; angeboten wird eine kuratierte
# Auswahl (Wert -> Anzeigename).
THEME_DARK = 'Dark'
THEME_LIGHT = 'Light'
_VALID_THEME_STYLES = (THEME_DARK, THEME_LIGHT)
ACCENT_PALETTES = {
    'Orange': 'Orange',
    'Red': 'Rot',
    'Crimson': 'Karmesin',
    'Pink': 'Pink',
    'Purple': 'Lila',
    'Indigo': 'Indigo',
    'Blue': 'Blau',
    'Teal': 'Petrol',
    'Green': 'Grün',
    'Olive': 'Oliv',
    'Gold': 'Gold',
    'Brown': 'Braun',
}

DEFAULT_STATE = {
    'ui': UI_MD,
    'onboarding_done': False,
    'theme_style': THEME_DARK,
    'primary_palette': 'Orange',
    # Config-Backup vor Update: so viele config_*.zip bleiben liegen.
    'config_backup_keep': 5,
}


def load_app_state() -> dict:
    """Liest app_state.yml und ergaenzt fehlende Keys mit den Defaults.

    Toleriert fehlende/kaputte Datei (liefert dann die Defaults) — darf beim
    allerersten Start, bevor ``initialize_tree`` die Datei angelegt hat, nicht
    crashen.
    """
    state = dict(DEFAULT_STATE)
    try:
        if os.path.exists(_APP_STATE_PATH):
            with open(_APP_STATE_PATH, encoding='utf-8') as f:
                loaded = yaml.safe_load(f) or {}
            if isinstance(loaded, dict):
                state.update(loaded)
    except Exception:
        # Defekte Datei soll den Start nicht verhindern — Defaults nutzen.
        pass
    return state


def save_app_state(state: dict) -> None:
    os.makedirs(os.path.dirname(_APP_STATE_PATH), exist_ok=True)
    with open(_APP_STATE_PATH, 'w', encoding='utf-8') as f:
        yaml.dump(state, f, sort_keys=True)


def get_ui_preference() -> str:
    """Gespeicherter UI-Modus ('md' oder 'classic'); Fallback 'md'."""
    ui = load_app_state().get('ui', UI_MD)
    return ui if ui in _VALID_UI else UI_MD


def set_ui_preference(ui: str) -> None:
    if ui not in _VALID_UI:
        raise ValueError(f"Ungueltiger UI-Modus: {ui!r} (erlaubt: {_VALID_UI})")
    state = load_app_state()
    state['ui'] = ui
    save_app_state(state)


def is_onboarding_done() -> bool:
    return bool(load_app_state().get('onboarding_done', False))


def set_onboarding_done(value: bool = True) -> None:
    state = load_app_state()
    state['onboarding_done'] = bool(value)
    save_app_state(state)


def get_theme() -> tuple[str, str]:
    """(theme_style, primary_palette); ungueltige Werte fallen auf Dark/Orange."""
    state = load_app_state()
    style = state.get('theme_style', THEME_DARK)
    palette = state.get('primary_palette', 'Orange')
    if style not in _VALID_THEME_STYLES:
        style = THEME_DARK
    if palette not in ACCENT_PALETTES:
        palette = 'Orange'
    return style, palette


def get_config_backup_keep() -> int:
    """Anzahl aufzubewahrender Config-Backups; ungueltig → 5, minimal 1."""
    try:
        return max(1, int(load_app_state().get('config_backup_keep', 5)))
    except (TypeError, ValueError):
        return 5


def set_theme(style: str | None = None, palette: str | None = None) -> None:
    """Speichert Hell/Dunkel und/oder Akzentfarbe (None = unveraendert)."""
    if style is not None and style not in _VALID_THEME_STYLES:
        raise ValueError(f"Ungueltiger Theme-Stil: {style!r}")
    if palette is not None and palette not in ACCENT_PALETTES:
        raise ValueError(f"Ungueltige Akzentfarbe: {palette!r}")
    state = load_app_state()
    if style is not None:
        state['theme_style'] = style
    if palette is not None:
        state['primary_palette'] = palette
    save_app_state(state)
