# Gen 7 (SM/USUM): Zonen-Index → met_location — Live-RE-Log

> STATUS: IN ARBEIT (2026-10-23). Plan: `~/.claude/plans/gen7-zonen-index-ermitteln.md`.
> ROM-Tabelle (Phase 0) steht; Runtime-Index-Adresse (Phase 1) noch offen.

## Phase 0 — ROM-Tabelle (ERLEDIGT, gegen Pokemon Sun USA)

- ZoneData = GARC **`a/0/7/7`**, **Sub-File 0** (nicht wie Gen 6 ein File unter vielen).
  SM: 24780 B = **295 Records × 0x54 (84 B)**, Rest 0. Sub-File 1 (592 B) = worldData.
- met/Ortsname = **`ParentMap`, int32 LE @ +0x1C** (pk3DS `ZoneData7`, UPR). Alle 295 Werte
  in [0,233), max 192. KEIN 9-Bit-Encoding (das ist Gen 6).
- `tools/extract_zonedata.py sm --emit-index-met` → `backend/data/zone_index_met_sm.yml`
  (295 Einträge, 172 benannt via `encounter_locations_gen7.yml`).
- Tool bleibt lokal (gitignored); Config `GAME_CONFIGS["sm"]/["usum"]` + `_emit_gen7_table`.

### WICHTIG: ParentMap ist die ELTERN-Location, nicht die Fein-met der Route
- Route 1 (eigene met 4 laut encounter_locations_gen7.yml) hat **keinen** ZoneData-Record
  (kein Record mit ParentMap==4). met 6 kommt 29× vor → Sammel-Parent (Hau'oli-Gegend).
- pk3DS liest NUR ParentMap @0x1C als Location; kein separates Fein-Feld im 0x54-Record.
- **Folge:** `zone_index_met_sm.yml` liefert Eltern-/Insel-Namen, bei Routen evtl. zu grob.
  Ob ParentMap == PK7-met_location (0xD2) numerisch gleich ist, ist UNVERIFIZIERT →
  live prüfen: Pokémon auf Route 1 fangen, 0xD2 lesen, mit ParentMap des Live-Records vergleichen.

## Phase 1 — Runtime-Index-Adresse (OFFEN)

### Versuch 1: bare-u16 Differential-Scan — INCONCLUSIVE
- Azahar 2125.1.2, Prozess `niji_loc` (SM USA). Reads im 0x3xxxxxxx-Heap OK **nach `set_process`**.
- Fenster (Player/SaveBlock-Cluster): SM `0x34190000+0x40000`. Probe `team_reihenfolge` 0x34195D84 ok.
- Snapshots: Route 1 → Lili'i (Iki Town) → Route 1 (Revisit). Tools + Daten dauerhaft in
  `tools/dumps/gen7_snaps/` (gitignored): `snap_route1.pkl`, `snap_lilii.pkl`, `snap_route1b.pkl`,
  `snap_gen7.py` (Snapshot), `diff_gen7.py` (N-Wege-Filter), `anchor_gen7.py` (Wert→Record→Name).
- Ergebnis: 232 Kandidaten nach 1 Wechsel, Revisit killte fast nichts (232→225) → der Cluster
  ist voll **zonen-stabiler Flags** in [0,294], keine Zähler. Kein Zähler-Kill-Effekt.
- `anchor_gen7.py` löst Kandidaten zu Record-Namen auf → reines Rauschen (Flag-Wert 1 →
  table[1]=Route 3 zufällig). **Keine saubere Record-Index-Adresse isolierbar.**
- Vermutung: Gen 7 hält die aktuelle Zone evtl. als **u32-Pointer auf den Record** (oder als
  residente Record-Kopie), nicht als bare-u16 — wie Gen 6 ("finde die Daten, dann den Pointer").

### Versuch 2 (vorbereitet, noch nicht gelaufen): Byte-Match residente Records
- Alle 295 ROM-Records sind **byte-eindeutig** → exakter 84-B-Match identifiziert den Index eindeutig.
- `tools/dumps/gen7_snaps/zonedata_sm_records.pkl` (ROM-Records) +
  `find_zone_record_ptr.py`: liest ein RAM-Fenster, sucht (a) verbatim-Record-Kopie (Adresse +
  Index + ParentMap), (b) konsekutive Records = residente Tabelle. Default Cluster, `--wide` für
  größeres Fenster, `--start/--size` frei. Noch gegen Azahar laufen zu lassen (nächste Session).

### Nächste Schritte (bei nächster Azahar-Session)
1. `find_zone_record_ptr.py sm` auf Route 1 — residente Record-Kopie/Tabelle finden
   (ggf. `--wide` oder gezielte Fenster). Liefert Adresse A des aktuellen Records.
2. Falls A gefunden: über Zonen prüfen (A+0x1C = aktuelle ParentMap live). Ggf. direkt
   ParentMap@A+0x1C als met-Quelle, oder Record→Index→Tabelle.
3. `anchor_gen7.py sm 294 --expect "Hauoli City=16" <snaps>` sobald ein Hau'oli-City-Snapshot da
   ist (benannte Zone = starker Anker für bare-u16, falls Versuch 2 nichts bringt).
4. Fallback (User-Vorschlag): **Ghidra auf code.bin** wie X/AS — Global/Pointer der aktuellen
   Zone statisch finden (Skill `3ds-code-extrahieren` + `gen6-cro-reverse-engineering`-Muster).
5. ParentMap==met_location-Frage live klären (Pokémon fangen, 0xD2).

## Live-Fortschritt 2026-10-23 (Session 2) — Pivot auf "aktuelle met"-Feld

- **ZoneData-Tabelle resident** @ `0x341BB2C4` (Record i = B+i*0x54, byte-identisch ROM),
  GARC-Deskriptor @ 0x341BB108 (+0x28 ptr, +0x2C size=0x60CC, +0x30 worldData-ptr). Kein
  record-aligned Current-Pointer im 4MB-Scan → Zone wird als bare Zahl referenziert.
- **ParentMap @+0x1C ist GROB (Eltern):** Route 1 (echte met 8) hat keinen Record, Hauholi-
  Stadtrand auch nicht. ParentMap der Area-Records = Insel/Stadt-Parent. **Also NICHT als
  Routen-met brauchbar.**
- **GROUND TRUTH per Fangen (PK7 met @ unshuffled 0xD2, via Party-Slot ptr+0x40):**
  - Route 1 → **met 8** (gefangenes Pikipek dex731). yml met8 ist fälschlich "Route 3" →
    `encounter_locations_gen7.yml` teils mis-indiziert (met24=Mahalo-Bergpfad stimmt dagegen,
    Starter dort gefangen).
- **Aktuelle-met-Feld-Suche:** wide-Region `0x34000000+0x400000`, u16-Snapshots pro Zone in
  `tools/dumps/gen7_snaps/wide_{stadtrand,route1,hauoli}.pkl`. Filter route1==8 & stadtrand!=8
  → 44; + Hauoli distinct → 28 Kandidaten, alle im Block **0x341F1Bxx–0x341F3Exx** (Field/Map-
  State). = das met-Feld-Nest. Final-Filter braucht Hau'oli-Citys echten met (nächster Catch).
- **Zu merken (PV für Catch-Verifikation):** wildes Alola-Sleimok (dex88, Lvl6) @ Trainerschule/
  Hau'oli City, **PV = 664536268**. Nach Fangen: Party-met = Hau'oli-met → 28 Kandidaten auf 1
  filtern (route1=8, hauoli=<met>, stadtrand!=beide).
- encounter_pokemon SM = **0x3002F7B8** (decode_opponent_gen67 ok, dex/lvl/PV/met korrekt).
- team_reihenfolge SM 0x34195D84: 6×u32 Slot-Ptr + count@+24; Slot-PK7 @ ptr+0x40 (232 B).

### DURCHBRUCH: met == ParentMap, Index-Feld gefunden (2026-10-23)
- **met_location == ParentMap** (numerisch), bestätigt durch 3 Catches:
  Route 1 → met 8 (ZoneData rec1, met8 = NUR rec1), Hau'oli City → met 6 (viele Records),
  Mahalo-Bergpfad → met 24. Die yml-*Namen* sind teils falsch (met8 stand als "Route 3"),
  die *met-Zahlen* in `zone_index_met_sm.yml` stimmen → als met-Tabelle gültig.
- **Das Field-State-Feld hält den Record-INDEX, nicht den met-Wert.** Darum scheiterten alle
  Wert==met-Filter. Anker: met8 = eindeutig rec1 → Zonen-Index auf Route 1 == 1.
- **Heap relokiert NICHT** (10/10 Stichprobe stabil über Zonenwechsel), ZoneData-Tabelle fix
  @0x341BB2C4.
- **Zonen-Index-Feld (6× gespiegelt), live verifiziert:**
  `0x34183CC6, 0x34199C8C, 0x3419A150, 0x3419A614, 0x3419AAD8, 0x3419AF9C`
  — Route1=rec1, Hauholi-Stadtrand=rec0, Hau'oli City/Trainerschule=rec53. Alle lesen live
  identisch. rec → `zone_index_met_sm.yml` → met.
- **LOCKED über 4 Zonen (2026-10-23):** Route 1=rec1(met8), Hauholi-Stadtrand=rec0(met6),
  Trainerschule-Gras=rec53(met6), Einkaufsviertel=rec8(met20) — alle 6 Adressen tracken identisch.
  Pipeline Feld(u16 rec-idx) → zone_index_met_sm.yml → met → Name bestätigt; met == PK7 0xD2.
- **REBOOT-STABIL (2026-10-23):** Nach Azahar-Neustart lesen alle 6 Adressen wieder rec0/met6 auf
  Stadtrand, ZoneData-Tabelle weiter @0x341BB2C4. → Adressen FIX (deterministisches Heap-Mapping
  pro ROM+Version, wie Gen 6). Keine Pointer-Chain nötig. Primär für pointer_sm.yml:
  **`encounter_zone_index: 0x3419A150`** (Mirrors 0x34183CC6, 0x34199C8C, 0x3419A614, 0x3419AAD8,
  0x3419AF9C als Fallback).
- **Encounter-Einheit = met_location (nicht Name).** ACHTUNG Split: 8 benannte Orte haben MEHRERE
  met-ids: Route 1 = met {6,8,230}, Hauholi City = {18,20,22}, Mahalo-Bergpfad = {26,28},
  Tenkarat-Hügel = {34,36}, Vegetationshöhle = {46,48}, Wela-Vulkanpark = {82,84},
  Plätscherhügel = {86,88}, Malihe City = {132,192}. Anders als Gen 6 (dort teilten Sub-Zonen EINE
  met). Für strikten Nuzlocke „ein Fang pro Route" braucht es eine Name-Gruppierungsschicht
  (met-id → kanonischer Routenname → dedup). Gilt für SM UND USUM (geteilter met-Namespace).
### USUM gelöst (2026-10-23, Prozess 'momiji' = Ultramond)
- ZoneData-Tabelle resident @ **0x33FA609C** (record0; 380 Records × 0x54).
- **Zonen-Index-Feld (USUM), primär `0x33FC1FD4`** (+ Mirror `0x33FC3CE4` und 16× im Block
  0x33F834B4–0x33F873AC, sowie 0x340909CA). Verifiziert über 3 Zonen: Hauholi-Stadtrand=rec0,
  Hau'oli City=rec12(met18), Route 3=rec6(met10). Deckt sich mit den 2026-07-07-Kandidaten
  (zone_watch_gen7: 0x33FC1FD4/0x33FC3CE4).
- Wide-Snapshots `tools/dumps/gen7_snaps/uwide_{stadtrand,hauoli,route3}.pkl`.
- **USUM reboot-stabil bestätigt (2026-10-23):** nach Neustart 0x33FC1FD4 + Mirrors = rec6/met10 auf
  Route 3, Tabelle fix @0x33FA609C. Adressen fest wie SM.

### OFFEN (für Wiring, separater Strang)
1. pointer_sm.yml `encounter_zone_index: 0x3419A150`, pointer_usum.yml `encounter_zone_index: 0x33FC1FD4`.
   citrahandler._load_zone_mappings: Gen-7-Branch (71/72→sm, 73/74→usum) für zone_index_met_*.yml.
   `_read_zone_index`/`_met_for_index`/`_current_met_location` sind bereits generisch.
2. **Name-Gruppierung für Split-Routen** (Route 1=met6/8/230, 8 Orte betroffen) — Design: pro Fang
   dedup nach kanonischem Namen statt met-id? Entscheiden.
3. USUM-Reboot-Stabilität bestätigen.
4. Gen-7-Encounter-Tick (Wild/Trainer via TID, `enchanted-bubbling-pearl`) konsumiert `_current_met_location()`.

### encounter_locations_gen7.yml ist −2 fehl-indiziert (2026-10-23, gegen lokales PKHeX)
- Quelle `D:\Programmieren\gitprojects\PKHeX\PKHeX.Core\Resources\text\locations\gen7\text_sm_00000_de.txt`
  (233 Zeilen, 0-indexed = echte met_location). Deckt sich mit allen 3 Catches.
- **Unsere yml-met = PKHeX-met − 2** (4 Anker: Hauholi-Stadtrand yml5/PKHeX7, Hauholi City
  yml16/PKHeX18, Route 3 yml8/PKHeX10, Route 1 yml4/PKHeX6). Blank-Line-Off-by wie Gen 6.
- Echte met (PKHeX): met6+met8=Route 1 (zwei IDs), met10=Route 3, met12=Route 2, met16=Meer von
  Mele-Mele, met18=Hauholi City, met24=Lili'i (Iki Town, Starter-Event), met26/28=Mahalo-Bergpfad.
- **ParentMap = echte met (PKHeX-Index)** → `zone_index_met_sm.yml`-WERTE korrekt; nur
  `encounter_locations_gen7.yml`-KEYS falsch.
- Offset ist NICHT konstant −2: höhere met driften (PKHeX hat mehr Blank/Extra-Zeilen je höher
  die ID; z.B. yml "Route 10" real +4). Daher kein Shift, sondern Regeneration.
- **FIXED 2026-10-23:** `encounter_locations_gen7.yml` neu aus PKHeX generiert — 106 Einträge,
  keyed by echte met (= Werte der ZoneData-Tabellen), PKHeX-Namen, games [71,72]/[73,74]/beide.
  `zone_index_met_{sm,usum}.yml` Name-Kommentare refreshed (Werte unverändert). Konsument
  `pokedex_view.location_name` ist reiner met→name-Lookup → kompatibel.

### Konsequenz für den Tracker-Plan
- Der ursprüngliche Index→ParentMap-Weg (zone_index_met_*.yml) liefert nur Insel/Stadt-Granularität.
  Für Routen-genaues Nuzlocke-Tracking brauchen wir das **aktuelle-met-Feld** (u16, Field-State ~0x341Fxxxx).
  Adresse evtl. pro Boot/Version instabil (Heap) → Pointer-Chain statt Absolutadresse nötig (wie Gen 6 encounter_zone_pointer eine feste Adresse war; hier ggf. nicht). TODO: Stabilität über Neustart prüfen.
- `encounter_locations_gen7.yml` met-IDs gegen echte PK7-met neu verifizieren (mehrere Catches).

## Offene Punkte
- Runtime-Zone-Repräsentation (bare-u16 vs u32-Pointer vs residente Record-Kopie) ungeklärt.
- ParentMap-Granularität (Eltern vs Fein-Route) — evtl. reicht Eltern-Ebene fürs Overlay, sonst
  separate Fein-met-Quelle nötig.
- USUM: Phase 0 (gleiche GARC a/0/7/7, Text-File 72) + Phase 1 analog, noch offen.
