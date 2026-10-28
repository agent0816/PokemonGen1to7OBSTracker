# Reverse Engineering — Arbeitsbereich

Werkzeug-Setup + Fundstellen für das Verständnis von Pointer- und Struct-Layouts
in den Pokemon-Spielen. Live-Memory-Analyse (Emulator) + statische Analyse (Ghidra),
gesteuert über Claude Code via MCP.

> Nur mit selbst besessenen, legal gedumpten ROMs arbeiten.

## Was hier getrackt wird vs. was lokal bleibt

**Diese Trennung ist der Kern des "ins Repo einbetten".** Die Toolchain wird NICHT
eingecheckt — nur das Wissen, das daraus entsteht.

| Getrackt (gehört ins Repo) | Lokal (via `.gitignore` ausgeschlossen) |
|---|---|
| `setup.md` — Methodik + Runbook | ROMs (`*.gba`, `*.nds`, `*.3ds`, `*.cia`, `code.bin`) |
| `findings/*.md` — konkrete Struct-/Pointer-Fundstellen | Ghidra-Projekte (`*.gpr`, `*.rep`, `ghidra_projects/`) |
| `findings/_TEMPLATE.md` — Vorlage | RE-venv (`.venv/re-lab/`, liegt unter Projekt-root) |
| diese `README.md` | Memory-Dumps, Scratch-Skripte |

**Warum:** ROMs sind rechtlich + aus Größe tabu. Ghidra-Projekte sind riesige Binaries.
Das MCP-Setup ist maschinenspezifisch. Was bleibt und Wert hat: die **Fundstellen** —
sie füttern direkt `backend/data/pointer_*.yml`, `backend/data/battle_engine_structs.md`
und `backend/pokedecoder.py`.

## Lokaler Stand auf dieser Maschine (verifiziert 2026-10-23)

- Ghidra **11.3.2** unter `D:\Programmieren\Ghidra\ghidra_11.3.2_PUBLIC` (auch 11.0.1 vorhanden)
- JDK **25** (Ghidra-Minimum 21 — passt)
- RE-venv: `.venv/re-lab/` (getrennt von der App-venv `.venv/py3115`, damit RE-Deps
  nicht in den Nuitka-Build geraten)
- MCP-Paket: `ghidra-bizhawk-mcp` 1.1.0 (PyPI, Projekt getanirao/ghidra-bizhawk-mcp)
  mit **mcp 1.30.0** + **pyghidra 2.2.1** gepinnt — Pins zwingend, siehe `requirements-re.txt`
- Status: BizHawk-Track-Server registriert + `/mcp` „connected" (verifiziert 2026-10-23)

## Schnellstart

```powershell
# 0. RE-venv aufsetzen (falls neu)
python -m venv .venv/re-lab
.venv/re-lab/Scripts/python -m pip install -r docs/reverse-engineering/requirements-re.txt

# 1. MCP-Server bei Claude Code registrieren (einmalig, Ghidra-Pfad + venv-Binary)
claude mcp add ghidra-bizhawk `
  -e GHIDRA_INSTALL_DIR=D:\Programmieren\Ghidra\ghidra_11.3.2_PUBLIC `
  -- D:\Programmieren\gitprojects\PokemonGen1to5OBSTracker\.venv\re-lab\Scripts\ghidra-bizhawk-mcp.exe

# 2. Prüfen
claude   # dann:  /mcp    → "ghidra-bizhawk" muss "connected" zeigen
```

Voller Ablauf + Azahar-Track (Gen 6/7): siehe `setup.md`.

## Neue Fundstelle dokumentieren

1. `cp findings/_TEMPLATE.md findings/<gen>-<thema>.md`
2. Ausfüllen (Konsolen-Adresse, Ghidra-Funktion, Struct-Layout).
3. Im Feld **Feeds into** eintragen, welche Repo-Datei das Ergebnis aufnimmt.
4. Committen (deutsche Commit-Message, Fundstellen-Datei + die aktualisierte Ziel-Datei).
