# Fundstelle: <Gen X — Thema, z.B. Party-Pokemon-Struct Gen 3 Feuerrot>

- **Datum:** YYYY-MM-DD
- **System / Edition:** <z.B. GBA / Pokemon Feuerrot DE>
- **ROM-Hash (optional):** <sha1, zur Nachvollziehbarkeit — ROM selbst NICHT einchecken>
- **Emulator:** <BizHawk 2.11 / Azahar Version>

## Live (WO)

- **Konsolen-Adresse:** `0x________` (Memory-Domain: `EWRAM` / `Main RAM` / …)
- **Wie gefunden:** <RAM Search Filterkette / Cheat Search / Lua-Hook>
- **Pointer-Chain (falls vorhanden):** `base -> +0x.. -> +0x..`

## Statisch (WAS)

- **Ghidra-Sprache/Processor:** <ARM:LE:32:v4t / 65816:LE:24 / …>
- **Zugreifende Funktion:** `0x________`  (<benannter Name, falls umbenannt>)
- **Dekompilat / Kommentar:** <Kernlogik, nicht den ganzen Dump>

## Struct-Layout

| Offset | Größe | Typ | Feld | Bemerkung |
|--------|-------|-----|------|-----------|
| `0x00` | 4 | u32 | pid | Persönlichkeitswert |
| `0x..` | .. | ..  | ..   | .. |

Gesamt-Größe: `0x__` Bytes.

## Verifikation

- <Wie bestätigt: Wert im Spiel geändert + Feld bewegte sich / 2 Editionen gegengeprüft />

## Feeds into

- [ ] `backend/data/pointer_<edition>.yml` — Keys: `________`
- [ ] `backend/data/battle_engine_structs.md`
- [ ] `backend/pokedecoder.py` — Funktion `pokemon_`
- [ ] sonstige: ________

## Offen / unsicher

- <was noch nicht belegt ist — klar als unbelegt markieren>
