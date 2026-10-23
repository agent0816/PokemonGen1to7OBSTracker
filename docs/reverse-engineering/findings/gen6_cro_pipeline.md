# Fundstelle: Gen 6 (X/Y) — CRO-Module in Ghidra laden + DllField Field-API

- **Datum:** 2026-10-23
- **System / Edition:** 3DS / Pokemon X (USA, CTR-P-EKJA)
- **Emulator / Tool:** Ghidra 11.3.2 via pyghidra 2.2.1 (re-lab-venv, headless)

## Ziel

Runtime-`zone_id → met_location`-Mapping für Gen-6-Encounter-Tracking statisch belegen.
Vorlauf: im `code.bin` (ARM11-Haupt-Binary) ist das Mapping NICHT statisch (keine Tabelle,
Scans negativ) — die Overworld-/Zonen-Logik liegt in **CRO-Overlay-Modulen** der RomFS.

## Pipeline (wiederverwendbar für alle 133 CROs / alle Gen-6/7-Spiele)

Tool: `docs/reverse-engineering/cro_tools.py`

1. **CRO aus RomFS dumpen** — via RomFS-Parser aus `tools/extract_zonedata.py`
   (`find_romfs` + `parse_romfs`), Datei nach Namen suchen (z.B. `DllField.cro`).
2. **Interne Relocations anwenden** — `cro_tools.apply_internal_relocations()`.
   Roher CRO-Load in Ghidra hat unrelozierte intra-modul-Pointer → **0 Cross-References**.
   Die InternalPatchTable (Header 0x128/0x12C) listet Typ-2-ABS32-Relocs; angewendet im
   Datei-Offset-Schema (Segment-Basis = Segment-Datei-Offset) ergibt sich `<modul>_reloc.bin`.
3. **In Ghidra laden** — `pyghidra.open_program(reloc_bin, language="ARM:LE:32:v6",
   loader="ghidra.app.util.opinion.BinaryLoader", analyze=True)`. Projekt wird beim
   Context-Exit auto-gespeichert (`project.save`, KEIN manuelles `prog.save` → "active
   transaction"). Reopen schnell via `GhidraProject.openProject(abspath, name, True)`.
4. **Name→Funktion-Registry** — `cro_tools.extract_name_registry()`. In `.rodata` liegt ein
   Array von 8-Byte-Records `{func_ptr, name_ptr}`; name_ptr → Identifier-String,
   func_ptr (bei name_ptr−4) → Wrapper in `.text`.

Ergebnis DllField.cro: 5493 Funktionen, **717 benannte Field-API-Funktionen**
(→ `gen6_dllfield_registry.md`). Das ist die Field-Script-Command-Tabelle der Engine.

## Statisch (WAS) — relevante Funktionen für Zone/Encounter

| Name | Adresse | Zweck |
|------|---------|-------|
| `PlayerGetZoneID` | `0x58D50` | aktuelle zone_id lesen (Wrapper → Core-Import) |
| `WordSetPlaceName` | `0x94F48` | zone_id → Orts-Namens-Label (= met_location) |
| `PlaceNameDisplay` | `0x96C04` | Ortsname einblenden |
| `CallWildBattle` | `0x927B0` | Wild-Encounter auslösen (baut Battle-Setup-Struct) |
| `_GetWildTreeParam` | `0x5E420` | Encounter-Param (Rubbelbaum/Honig) |
| `GetRockCrashItemTableNo` | `0x979DC` | Felswurf-Encounter-Tabellen-Index |
| `PokeAddTableGetMonsNo` | `0x5A674` | Species aus einer Encounter-/Add-Tabelle |

## Offen / unsicher

- **Kernlogik ist importiert:** Die Field-API-Funktionen sind dünne Wrapper, die nach
  Arg-Marshalling via PLT (`FUN_16c0`-Region, `ldr pc,[import_slot]`) in Core-Module
  (code.bin / andere CROs) springen. Die eigentliche `zone_id → met_location`-Auflösung
  (`WordSetPlaceName`→Import) liegt dort. Nächster Schritt: Import-Tabelle (ImportModule
  @0xF0, 30 Module; ExtPatch @0xF8, 3951) auflösen bzw. die importierte Funktion im
  Zielmodul analysieren. External-Relocs sind statisch NICHT auflösbar ohne Load-Adressen
  aller Module → entweder Multi-Modul-Link simulieren oder Live-Deref in Azahar.
- **Bewertung:** Für den Tracker deckt der ROM-Proxy aus `extract_zonedata.py` (+0x0C) die
  Zonen bereits zu ~92% ab (2 Konflikte: `0x0604`, `0x06DC`) — siehe Memory
  `project-gen6-encounter-research`. Die CRO-Pipeline ist primär Infrastruktur für
  künftige Features (Battle-Structs, Events, Script-Commands) über alle Gen-6/7-Module.

## Live-Bridge: CRO-Import zur Laufzeit auflösen (Azahar RPC, 2026-10-23)

Statische CRO-Analyse zeigt Import-Calls nur als Platzhalter (`bl 0x16c0`), weil die
ARM_CALL-Relocs (ExtPatch) nicht gepatcht sind. Zur Laufzeit hat Azahars Loader sie
aufgelöst — per RPC-Read (`backend/classes/citra.py` `Citra.read_memory`, v1, Azahar
2125.1.2) komplett nachvollziehbar, read-only, kein Debugging:

1. **DllField-Runtime-Base finden:** CRO-Module sind verkettet. CRS `|static|` per
   FCRAM-Scan nach `CRO0`-Magic finden (nur 1 Treffer, @0x08C55000 diese Session), dann
   Linked-List laufen: `NextCRO = u32(base+0x88)`, `PrevCRO = base+0x8C`, Name via
   `u32(base+0x84)`→String. **DllField base = 0x006A5000** (SESSION-SPEZIFISCH, ändert
   sich pro Boot → jedes Mal neu ermitteln). Runtime-Addr = base + file_offset.
2. **Import auflösen:** Wrapper-Funktion runtime lesen, `bl`→PLT-Slot (module+0x16c0-Region),
   PLT = `ldr pc,[pc,#-4]` (E51FF004) + Literal dahinter = aufgelöste Import-Zieladresse.
3. **WordSetPlaceName-Import → code.bin 0x003DCFC8** (STABIL, code.bin fix bei 0x00100000).
   `FUN_003dcfc8` = Getter: `return (arg, *0x005AE748)`; `*0x005AE748` = Pointer auf
   Field-Situation-Manager (live 0x08D1C574, session-spezifisch) — Pointer-Tabelle, met_loc
   liegt tiefer verschachtelt.

**Live-Ground-Truth Route 10 (Menhir-Weg):** `zone_id @0x08CEC568 = 0x06E8`.
Widerlegt den ROM-Proxy (der sagte 0x06E8→Route 20/met96). **3. bestätigter Proxy-Konflikt**
(nach 0x0604, 0x06DC). Route 10 = met 50.

**Offen:** exakte met_location-Quelle braucht weitere Struct-Indirektion ab 0x08D1C574 ODER
Analyse der code.bin-Funktion, die bei Zonenwechsel die place-id schreibt. Für Tracker:
Live-RPC-Read von zone_id funktioniert bereits — ein "current met_location"-Global wäre der
nächste Zielfund (dann kein yml-Lookup mehr nötig).

## Feeds into

- [ ] `backend/data/zone_id_mapping_xy.yml` — nur bei bestätigter Import-Auflösung
- [x] `docs/reverse-engineering/cro_tools.py` — wiederverwendbares CRO-Toolkit
- [x] `docs/reverse-engineering/findings/gen6_dllfield_registry.md` — 717 Field-Funktionen
