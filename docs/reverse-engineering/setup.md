# RE-Setup: Claude Code + Ghidra + Emulatoren

**Ziel:** Pointer- und Struct-Layouts in den Spielen verstehen — Live-Memory-Analyse
(wo liegt der Wert?) + statische Ghidra-Analyse (was bedeutet er?), gesteuert über
Claude Code als Agent.

**Tracks:** BizHawk (NES/SNES/GB/GBA/N64/NDS — Gen 1–5) und Azahar (3DS — Gen 6/7).

> Nur mit selbst besessenen, legal gedumpten ROMs arbeiten.

---

## 0. Das große Bild

| Ebene | Frage | Werkzeug |
|-------|-------|----------|
| **WO** liegt der Wert live | Adresse + ggf. Pointer-Chain | Emulator-RAM-Tools |
| **WAS/WARUM** bedeutet er | Code, Struct-Layout, Feldtypen | Ghidra (statisch) |
| **Steuerung** | beides per natürlicher Sprache | Claude Code + MCP |

**Emulator-Besonderheit:** Emulierte Konsolen haben **kein ASLR** — Konsolen-RAM liegt
an festen Adressen (GBA EWRAM `0x02000000`, NDS Main RAM `0x02000000`, SNES WRAM
`7E0000`, 3DS Code ab `0x00100000`). Im Emulator-Memory-Space arbeiten, nicht auf dem
Host-Prozess (dort schlägt ASLR wieder zu).

---

## 1. Lokaler Stand (diese Maschine, verifiziert 2026-10-23)

Bereits eingerichtet — nichts davon neu installieren:

- Ghidra **11.3.2**: `D:\Programmieren\Ghidra\ghidra_11.3.2_PUBLIC`
- JDK **25** (Ghidra-Minimum 21)
- RE-venv: `.venv/re-lab/`, Pakete per `requirements-re.txt`:
  `ghidra-bizhawk-mcp` 1.1.0, **mcp 1.30.0**, **pyghidra 2.2.1**, jpype 1.7.1

Install (reproduzierbar):
```powershell
python -m venv .venv/re-lab
.venv/re-lab/Scripts/python -m pip install -r docs/reverse-engineering/requirements-re.txt
```

> **Zwei Korrekturen gegenüber der ersten Planfassung — sonst startet der Server nicht:**
>
> 1. **`pip install "ghidra-bizhawk-mcp[ghidra]"` funktioniert gegen 11.3.2 NICHT.** Das
>    Extra `[ghidra]` zieht pyghidra≥3.0, und pyghidra≥3 lehnt Ghidra 11.3.2 ab
>    („Unsupported Version … minimum required version is 12.0"). Lösung: **pyghidra 2.2.1**
>    explizit pinnen (akzeptiert 11.3.2), NICHT das Extra nehmen.
> 2. **mcp muss `<2` sein.** ghidra-bizhawk-mcp 1.1.0 nutzt die mcp-1.x-API
>    (`server.list_tools()`); mcp 2.x entfernt sie → `AttributeError` beim Start. Das Paket
>    deklariert nur `mcp>=1.0.0`, pip zieht sonst 2.x. → **mcp 1.30.0** gepinnt.
>
> Beide Pins stehen in `requirements-re.txt`. Mit ihnen: `/mcp` → „connected" (verifiziert).

### Ghidra-Version — warum 11.3.2 und nicht 12.x

Ghidra-Extensions sind versions-gebunden (`ghidraVersion` in `extension.properties` muss
exakt passen). Die Emulator-Loader und `LaurieWired/GhidraMCP` sind gegen **11.3.2** gebaut.
Auf 12.x: version-passende Forks nehmen, Loader neu kompilieren (nur `ghidraVersion` +
JAR-Refs anpassen) oder im Dialog „Install Anyway". Parallelinstallation mehrerer Ghidra-
Versionen in getrennten Ordnern + `GHIDRA_INSTALL_DIR` umstellen geht auch.

---

## 2. MCP-Server registrieren (einmalig)

```powershell
claude mcp add ghidra-bizhawk `
  -e GHIDRA_INSTALL_DIR=D:\Programmieren\Ghidra\ghidra_11.3.2_PUBLIC `
  -- D:\Programmieren\gitprojects\PokemonGen1to5OBSTracker\.venv\re-lab\Scripts\ghidra-bizhawk-mcp.exe
```

Prüfen: `claude` starten → `/mcp` → `ghidra-bizhawk` muss „connected" zeigen.

---

## TEIL A — BizHawk-Track (Gen 1–5)

Kombinierter Server `ghidra-bizhawk-mcp`: fährt Ghidra headless UND spricht mit BizHawk.
BizHawks eigene RAM-Tools sind der Live-Layer.

### A1. System-Loader in Ghidra installieren

`File` → `Install Extensions` → `+` → Zip → Neustart. Pro System:

| System | Loader | Ghidra-Sprache |
|--------|--------|----------------|
| GBA | `pudii/gba-ghidra-loader` | `ARM:LE:32:v4t` |
| SNES | `joshleaves/ghidra-snes` (+ `achan1989/ghidra-65816`) | `65816:LE:24` |
| NES | nativ 6502 | `6502:LE:16` |
| GB/GBC | `ImNotAVirus/ghidra-gb` (WIP) | SM83/LR35806 |
| NDS | `NTRGhidra` (ARM9/ARM7) | `ARM:LE:32` |
| N64 | MIPS (nativ) | `MIPS:BE:64` |

> GBA + NDS für uns am wichtigsten (Gen 3–5). GB/GBC-Loader ist WIP — manuelle Nacharbeit.

### A2. Live-Wert in BizHawk finden (WO)

1. Spiel starten → `Tools` → **RAM Search**.
2. Memory-Domain wählen (`System Bus`, `WRAM`, `Combined WRAM`, `EWRAM`).
3. Suche starten → Wert im Spiel ändern → nach „changed/decreased" filtern. Wiederholen.
4. Treffer → Doppelklick → **RAM Watch** → feste Konsolen-Adresse.

Pointer prüfen: `Tools` → **Hex Editor** an der Fundstelle (4-Byte-Wert, der auf andere
RAM-Region zeigt = Pointer). Oder live mitloggen per `Tools` → Lua Console:

```lua
while true do
  local hp = memory.read_u16_le(0x0200A2, "EWRAM")
  gui.text(10, 10, "HP: " .. hp)
  emu.frameadvance()
end
```

### A3. Statisch verstehen via Claude Code (WAS)

ROM in Ghidra laden lassen (MCP: `triage_and_load_retro_rom`), Auto-Analyse abwarten, dann:

- „Lade ROM `<pfad>.gba` und zeig die Memory-Map."
- „Welcher Code schreibt auf EWRAM `0x0200A2`? Dekompiliere die Funktion."
- „Erklär die Struct ab dieser Adresse — Feldtypen und Offsets."

---

## TEIL B — Azahar-Track (Gen 6/7, 3DS = ARM11/ARMv6)

Kein Turnkey-MCP. Weg: Live-Suche in Azahar + statische Analyse der extrahierten
`code.bin` über `LaurieWired/GhidraMCP`.

### B1. code.bin extrahieren + dekomprimieren (verifiziert 2026-10-23)

Kein ctrtool/boot9 nötig — `extract_3ds_code.py` nutzt pyctr + Azahars eigene
`aes_keys.txt` und dekomprimiert BLZ automatisch:

```powershell
.venv/re-lab/Scripts/python docs/reverse-engineering/extract_3ds_code.py `
  "tools/roms/gen6/Pokemon X (USA) ....3ds" tools/roms/gen6/x_extract
# aes_keys.txt default: %APPDATA%\Azahar\sysdata\aes_keys.txt
```

Ergebnis in `<out>/`: `code.bin` (roh) und — falls `CompressExefsCode`-Flag gesetzt —
`code_decompressed.bin`. **Die dekomprimierte Datei in Ghidra laden.**

So funktioniert's (Details im Skript-Docstring):
- 3DS-Decrypt braucht normal `boot9.bin`. Azahar legt die abgeleiteten Keyslots in
  `aes_keys.txt` ab → nur die **KeyX**-Slots laden (`CryptoEngine(setup_b9_keys=False)` +
  `set_keyslot('x', …)`), KeyY kommt aus dem NCCH-Header.
- `CCIReader` baut intern eine eigene CryptoEngine (boot9) → **nicht** nutzbar. Stattdessen
  NCSD-Header @0x120 parsen → Application-Partition (0) per `SubsectionIO` direkt an
  `NCCHReader(crypto=ce)`.
- Gen-6-Code ist BLZ-komprimiert (SCI-Flag @exheader 0x0D bit0) → backward-LZ dekomprimieren.
- Validierung: Pokemon X (`CTR-P-EKJA`) .code 2.86 MB → 5.16 MB, Entry-Word `0xEB000007`
  (ARM `BL`) = plausibler ARM11-Code.

### B2. Live-Wert in Azahar finden (WO)

Cheat-Menü → **Search Memory** → Wert suchen → im Spiel ändern → filtern. Adresse
notieren (3DS-Raum, Code ab `0x00100000`). Schlanker als BizHawk — mehr Iterationen.

### B3. GhidraMCP installieren (zweiter, separater MCP-Server)

**Wichtig: Release 1.4** nehmen (für Ghidra 11.3.2 gebaut), NICHT 2.0 (das ist für
Ghidra 12.1.4 → Mismatch). Bereits heruntergeladen + entpackt unter
`tools/ghidra-mcp/extracted/GhidraMCP-release-1-4/` (lokal, gitignored):
- `GhidraMCP-1-4.zip` — die Ghidra-Extension (GUI-Install, siehe unten)
- `bridge_mcp_ghidra.py` — der MCP-Bridge (bereits bei Claude registriert)

Bridge ist registriert (venv-Python + `requests`):
```powershell
claude mcp add ghidra `
  -- D:\...\.venv\re-lab\Scripts\python.exe `
     D:\...\tools\ghidra-mcp\extracted\GhidraMCP-release-1-4\bridge_mcp_ghidra.py `
     --ghidra-server http://127.0.0.1:8080/
```

Plugin in Ghidra aktivieren (einmalig):
1. Ghidra starten (`ghidraRun.bat` im Install-Ordner).
2. `File` → `Install Extensions…` → grünes `+` → `GhidraMCP-1-4.zip` wählen → OK → Ghidra neu starten.
3. Ein Programm im CodeBrowser öffnen → `File` → `Configure…` → `Developer` (bzw. „Miscellaneous")
   → Haken bei **GhidraMCPPlugin** → OK. Startet HTTP-Server auf `127.0.0.1:8080`.
4. Prüfen: Ghidra + Programm offen → in Claude `/mcp` → `ghidra` „connected",
   oder `curl http://127.0.0.1:8080/methods`.

> Der Bridge verbindet NUR, solange Ghidra mit aktivem Plugin + geladenem Programm läuft.
> Ohne laufendes Ghidra zeigt `/mcp` „failed" — das ist normal.

### B4. code_decompressed.bin importieren + analysieren

`File` → `Import File` → `code_decompressed.bin` wählen. Im Import-Dialog:
- **Format:** Raw Binary
- **Language:** Picker öffnen → ARM / little endian / 32 / **v6** (ARM11 = ARMv6) → `ARM:LE:32:v6`
- **Options → Base Address:** `00100000`

Dann Auto-Analyse laufen lassen (5 MB ARM → 20–40 Min). Danach in Claude Code
dekompilieren/referenzieren/umbenennen lassen (Prompts in §4).

---

## 3. Kombinierter Workflow

1. **Live:** Wert per RAM-Suche finden → feste Konsolen-Adresse.
2. **Zugriff finden:** Welche Instruktion schreibt/liest die Adresse? (Lua-Hook / MCP-Analyse)
3. **Übersetzen:** Konsolen-Adresse ↔ ROM-Code-Stelle = Brücke Live ↔ Ghidra.
4. **Statisch:** Funktion dekompilieren → Feldnamen, Nachbar-Offsets, Struct-Layout.
5. **Festhalten:** Ergebnis in `findings/` + direkt in die Ziel-Repo-Datei
   (`pointer_*.yml` / `battle_engine_structs.md` / `pokedecoder.py`).

---

## 4. Nützliche Prompts

- „Lade ROM `<pfad>` und gib Memory-Map + Entry Point."
- „Welche Funktion greift auf `<X>` zu? Dekompiliere + kommentiere jede Zeile."
- „Rekonstruiere die Struct ab `<X>`: Größe, Feldtypen, Offsets, plausible Namen."
- „Finde alle Code-/Daten-Referenzen auf `<X>`."
- „Vergleiche zwei Speicherzustände (vorher/nachher) — welches Feld änderte sich?"

---

## 5. Troubleshooting

| Problem | Lösung |
|---------|--------|
| Extension-Mismatch-Dialog | `ghidraVersion` ≠ laufende Version. Auf 11.3.2 bleiben / neu bauen / „Install Anyway". |
| `/mcp` zeigt Server nicht | venv-Pfad absolut? `GHIDRA_INSTALL_DIR` im `-e` gesetzt? |
| `Server object has no attribute 'list_tools'` | mcp 2.x installiert → `pip install "mcp<2"`. |
| `Unsupported Version … minimum required version is 12.0` | pyghidra≥3 gegen Ghidra 11.3.2 → `pip install "pyghidra<3"` (2.2.1). |
| ROM nur „Raw Binary" | Loader + Processor-Modul installiert + Ghidra neu gestartet? |
| GhidraMCP-Bridge verbindet nicht | Ghidra offen, Plugin aktiv, HTTP `localhost:8080`? |
| Decompile = Unsinn | Falsche Basis-Adresse/Processor. 3DS: `ARM:LE:32`, Basis `0x00100000`. |
| 3DS-ROM lädt nicht | ROM entschlüsselt? ExeFS/`code.bin` korrekt extrahiert? |

---

## 6. Projekt-Kontext — wohin die Ergebnisse fließen

| Fund | Ziel-Datei im Repo |
|------|--------------------|
| Speicher-Offsets pro Edition | `backend/data/pointer_*.yml` (+ `bh_pointers.py`, Lua) |
| In-Battle-Struct-Layouts Gen 5–7 | `backend/data/battle_engine_structs.md` |
| Decoder-Logik pro Gen | `backend/pokedecoder.py` (sensibel — erst Plan, dann Umsetzung) |
| Charset-Tabellen | `backend/data/gen*charset.yml` |
| Encounter-Tabellen | Encounter-YAMLs (siehe Skill `encounter-gen`) |

> Decoder/Pointer-YAMLs sind laut CLAUDE.md sensibel — Änderungen nur mit Beleg aus der
> RE-Analyse, Plan vor Umsetzung.
