# D/P-Feldskript-Disassembler (Gen 4)

Liest die kompilierten Feldskripte aus dem pret/pokediamond-Decomp
(`files/fielddata/script/scr_seq_release/narc_*.bin`) und findet Fangquellen
außerhalb der Wild-Tabellen (Gifts, Statics, Tausch, Fossilien). Ergebnis
floss 2026-10-28 in `backend/data/maps/sinnoh_dppt.yml` (`pokemon_by_game`).
Platin/HGSS brauchen das nicht: deren Decomps haben lesbare `.s`-Skripte.

Voraussetzung: pokediamond-Klon als Geschwister-Ordner des Repos
(`../pokediamond`) oder Env `POKEDIAMOND=<pfad>`. Nur stdlib.

| Datei | Zweck |
|---|---|
| `table.txt` | Opcode-Namen in Reihenfolge aus `gScriptCmdTable` (arm9/asm/unk_02038C78.s, 721 Einträge) |
| `analyze_handlers.py` | leitet pro `ScrCmd_*`-Handler die Argumentgrößen aus C/ASM ab → `handlers_raw.json` |
| `dp_disasm.py` | Disassembler + Extraktion; `--dump <narc-nr>` gibt eine Datei aus, ohne Argument alle 1051 → `sources_raw.json` |
| `summarize.py` | fasst `sources_raw.json` pro Section zusammen und diffed gegen `sinnoh_dppt.yml` |

Sonderfälle mit variabler Argumentlänge stehen in `dp_disasm.py` unter `SPECIAL`
(mit Decomp-Belegstelle). map_id→Section über `backend/data/mapid_section_gen4.yml`.
