# Build-Spike KivyMD 2.x + Nuitka (2026-10-15)

Prüft, ob KivyMD 2.x samt neuer Abhängigkeiten (PyCairo, MaterialShapes,
MaterialYouColor, asynckivy) mit unserem Setup läuft — aus Source und als
Nuitka-Onefile-EXE. Start wie `main.py` über `app.async_run(async_lib='asyncio')`.

## Ergebnis

| Variante | Source | Nuitka-Onefile |
|---|---|---|
| `kivymd==2.0.0` (PyPI) | ❌ `MDSnackbar` crasht: `FBO Initialization failed: Incomplete attachment` | nicht gebaut |
| `kivymd @ git+…@ce16903a` (master, 2.0.1.dev0) | ✅ alle Checks | ✅ alle Checks, EXE 26,9 MB |

Ursache Snackbar-Crash: `M3CommonRipple.init_fbos` legt FBO mit Widget-Größe 0 an.
Gefixt upstream in Commit `0b8de393` („Fix ripple fbo dynamic size“, 2026-08-19), noch in keinem Release.
→ Bis 2.0.1 erscheint: auf Git-Commit pinnen, nicht lokal patchen.

Geprüft: Fonts (Roboto + Material Icons), Images, `MDLoadingIndicator` (PyCairo + MaterialShapes),
`MDDialog`, `MDSnackbar`, `MDDropdownMenu`, `MDNavigationBar`, `MDTextField`, `MDCard`, Umlaute, `Window.screenshot`.

## Setup

- Python 3.11.5, Kivy 2.3.0, Nuitka 2.7.14 (wie Projekt-venv `py3115`)
- venv: `.venv/spike_kivymd` (getrennt vom Projekt-venv)
- Nuitka-Kivy-Plugin greift automatisch; KivyMD braucht keine Sonderbehandlung außer Package-Data

```bash
pip install "Kivy==2.3.0" "kivymd @ git+https://github.com/kivymd/KivyMD@ce16903a" "Nuitka==2.7.14" ordered-set zstandard
cd spikes/kivymd_build/_out
python -m nuitka --onefile --assume-yes-for-downloads \
    --include-package-data=kivymd --include-package-data=materialshapes --include-package=kivymd \
    --output-dir=. --output-filename=kivymd_spike.exe ../spike_app.py
```

Laufzeit: ~8 min (ccache kalt). Ergebnis in `spike_result.json` + `spike_screenshot0001.png` im cwd.

## Offen

- Pillow: KivyMD zieht 12.x, Projekt-venv hat 11.3 → beim echten Umbau Projekt-Pins abgleichen
- `--include-package=kivymd` kompiliert alle KivyMD-Module (inkl. großer `icon_definitions`) — ggf. gezielter einbinden, wenn Größe stört
- Größen-Delta zum echten Tracker nicht exakt gemessen (Spike enthält kein Backend); Tracker 0.8.0 EXE aktuell 41,3 MB
