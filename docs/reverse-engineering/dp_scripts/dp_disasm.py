"""Disassembler fuer die Gen-4-Feldskripte von Pokemon Diamant/Perl.

Opcode-Tabelle: arm9/asm/unk_02038C78.s gScriptCmdTable (721 Eintraege) aus
pret/pokediamond; Argumentgroessen pro Handler aus analyze_handlers.py
(handlers_raw.json) + manuelle Sonderfaelle (siehe SPECIAL unten).

Aufruf:
  python dp_disasm.py                 -> alle 1051 Dateien, Statistik + Extraktion
  python dp_disasm.py --dump 1234     -> Disassembly einer Datei nach stdout
Nur stdlib.
"""
import json, os, re, struct, sys
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
# pokediamond-Decomp als Geschwister-Ordner des Repos (oder per Env POKEDIAMOND).
PD = os.environ.get("POKEDIAMOND", os.path.join(REPO, "..", "pokediamond"))
SCR_DIR = os.path.join(PD, "files", "fielddata", "script", "scr_seq_release")
TRADE_DIR = os.path.join(PD, "files", "fielddata", "pokemon_trade", "fld_trade")
MAPID_YML = os.path.join(REPO, "backend", "data", "mapid_section_gen4.yml")

SIZES = {"b": 1, "h": 2, "v": 2, "vp": 2, "w": 4}

raw = json.load(open(os.path.join(HERE, "handlers_raw.json")))
OPS = {e["op"]: (e["name"].replace("ScrCmd_", ""), list(e["args"])) for e in raw}
NUM_OPS = len(OPS)  # 721 = sNumScriptCmds

# Sonderfaelle: Argumentlaenge haengt vom ersten Argument ab (Belege: Quelltext).
# Wert = Funktion(read) -> Liste der Argumente, read(kind) liest das naechste Arg.
def _sw(first_kind, table):
    def f(read):
        sel = read(first_kind)
        out = [sel]
        for k in table.get(sel, []):
            out.append(read(k))
        return out
    return f

SPECIAL = {
    # scrcmd_flags.c:213/238/263 GetSetStrength/Flash/Defog: b option; vp nur bei option==2
    0x1CF: _sw("b", {2: ["vp"]}),
    0x1D0: _sw("b", {2: ["vp"]}),
    0x1D1: _sw("b", {2: ["vp"]}),
    # scrcmd_union.c:14 UnionGroup: h option, switch
    0x21D: _sw("h", {0: ["v", "vp"], 1: ["v", "vp"], 2: ["v", "v"], 3: ["v", "v"], 4: ["vp"], 5: ["v"]}),
    # scrcmd.c:4492 Unk0289: vp + 5 Bytes (Schleife) + 1 Byte
    0x289: lambda read: [read("vp")] + [read("b") for _ in range(6)],
    # asm/scrcmd_9.s:36 Unk0235: h subcmd, Jump-Table case 0..6
    0x235: _sw("h", {0: ["h"], 1: ["h", "h", "h"], 2: [], 3: ["h", "h", "h"], 4: ["h", "h"], 5: ["h", "h", "h"], 6: ["h"]}),
    # asm/scrcmd_9.s:264 Unk0237: h subcmd 0/1 -> je 3 h
    0x237: _sw("h", {0: ["h", "h", "h"], 1: ["h", "h", "h"]}),
    # asm/scrcmd_12.s:50 Unk023E: h subcmd, Jump-Table case 0..8
    0x23E: _sw("h", {1: ["h"], 2: ["h"], 3: ["h"], 5: ["h", "h"], 6: ["h", "h"]}),
    # asm/scrcmd_9.s:387 Unk027C: h subcmd 0/1/2 -> je 1 h
    0x27C: _sw("h", {0: ["h"], 1: ["h"], 2: ["h"]}),
    # asm/scrcmd_10.s:795 Unk0294: zwei inline ScriptReadByte
    0x294: lambda read: [read("b"), read("b")],
}

OP_END, OP_GOTO, OP_RETURN, OP_CALL = 0x02, 0x16, 0x1B, 0x1A
BRANCH_W_LAST = {0x16, 0x17, 0x18, 0x19, 0x1A, 0x1C, 0x1D, 0xF1}  # letztes Arg = s32 rel. Code-Offset
TERMINATORS = {OP_END, OP_GOTO, OP_RETURN}
OP_MOVEMENT = 0x5E  # ApplyMovement: v obj, w rel. Offset -> Movement-Daten (kein Code)

OP_SETVAR, OP_COPYVAR, OP_SETORCOPY = 0x28, 0x29, 0x2A
OP_GIVEMON, OP_GIVEEGG = 0x96, 0x97
OP_WILD, OP_LEGEND, OP_FIRST = 0x124, 0x2BD, 0x125
OP_TRADEINIT, OP_TRADEEXEC = 0x226, 0x229
OP_FOSSIL = 0x1F4
OP_STARTER = 0xB4
OP_DAYCARE_EGG = 0x1A9
OP_GETSTARTER = 0xDE


class Desync(Exception):
    pass


def parse_header(data):
    entries = []
    pos = 0
    while True:
        if pos + 2 > len(data):
            raise Desync("header ohne 0xFD13")
        if struct.unpack_from("<H", data, pos)[0] == 0xFD13:
            return entries, pos + 2
        # Einige Dateien (z.B. 96/158/356) haben keinen 0xFD13-Terminator:
        # Header endet dort, wo das erste Skript beginnt.
        if entries and pos >= min(entries):
            return entries, pos
        if pos + 4 > len(data):
            raise Desync("header truncated")
        rel = struct.unpack_from("<i", data, pos)[0]
        entries.append(pos + 4 + rel)
        pos += 4


def decode_at(data, pc):
    """Dekodiert einen Befehl an pc -> (op, name, args, next_pc)."""
    if pc + 2 > len(data):
        raise Desync(f"opcode ueber Dateiende @0x{pc:X}")
    op = struct.unpack_from("<H", data, pc)[0]
    if op >= NUM_OPS:
        raise Desync(f"unbekannter opcode 0x{op:X} @0x{pc:X}")
    p = [pc + 2]

    def read(kind):
        n = SIZES[kind]
        if p[0] + n > len(data):
            raise Desync(f"arg ueber Dateiende op 0x{op:X} @0x{pc:X}")
        v = int.from_bytes(data[p[0]:p[0] + n], "little", signed=(kind == "w"))
        p[0] += n
        return v

    name, kinds = OPS[op]
    if op in SPECIAL:
        args = SPECIAL[op](read)
    else:
        args = [read(k) for k in kinds]
    return op, name, args, p[0]


def disassemble(data):
    """Rekursiver Abstieg ueber alle Header-Eintraege.
    Rueckgabe: dict pc->(op,name,args,next), entries, code_start, fehlerliste, movement-Ziele"""
    entries, code_start = parse_header(data)
    insns = {}
    owner = {}  # byte -> pc des Befehls (Overlap-Erkennung)
    errors = []
    movement = set()
    work = []
    for e in entries:
        if not (code_start <= e < len(data)):
            errors.append(f"entry 0x{e:X} ausserhalb")
            continue
        work.append(e)
    while work:
        pc = work.pop()
        while True:
            if pc in insns:
                break
            if pc in owner:
                errors.append(f"overlap @0x{pc:X} (in Befehl @0x{owner[pc]:X})")
                break
            try:
                op, name, args, nxt = decode_at(data, pc)
            except Desync as d:
                errors.append(str(d))
                break
            for b in range(pc, nxt):
                if b in owner and owner[b] != pc:
                    errors.append(f"overlap @0x{b:X}")
                owner[b] = pc
            insns[pc] = (op, name, args, nxt)
            if op in BRANCH_W_LAST:
                tgt = nxt + args[-1]
                if not (code_start <= tgt < len(data)):
                    errors.append(f"sprungziel 0x{tgt:X} ausserhalb @0x{pc:X}")
                else:
                    work.append(tgt)
            if op == OP_MOVEMENT:
                movement.add(nxt + args[-1])
            if op in TERMINATORS:
                break
            pc = nxt
    return insns, entries, code_start, errors, movement


def fmt_arg(kind_list, args):
    return ", ".join(f"0x{a:X}" if isinstance(a, int) and a >= 0 else str(a) for a in args)


def dump(fileno):
    data = open(os.path.join(SCR_DIR, f"narc_{fileno:04d}.bin"), "rb").read()
    insns, entries, cs, errors, mov = disassemble(data)
    starts = {e: i + 1 for i, e in enumerate(entries)}
    for pc in sorted(insns):
        if pc in starts:
            print(f"\n_script{starts[pc]}:  ; @0x{pc:X}")
        op, name, args, nxt = insns[pc]
        extra = ""
        if op in BRANCH_W_LAST or op == OP_MOVEMENT:
            extra = f"   ; -> 0x{nxt + args[-1]:X}"
        print(f"  {pc:05X}: {op:04X} {name}({fmt_arg(None, args)}){extra}")
    if errors:
        print("\nFEHLER:", errors)
    print(f"\nmovement-Ziele: {[hex(m) for m in sorted(mov)]}")


# --------------------------------------------------------------- Extraktion
def is_var(x):
    return x >= 0x4000


LIMIT_HITS = []
OP_GETVERSION = 0x246  # GetGameVersion(vp) (Name aus gScriptCmdTable)
OP_CMPVAL = 0x11       # CompareVarToValue(vp, h)
VERSION_NAMES = {10: "D", 11: "P"}  # include/config.h VERSION_DIAMOND=10, Pearl=11
# sScriptConditionTable (unk_02038C78.s:133): Zeile = compareType, Spalte = comparisonResult
# (0 kleiner, 1 gleich, 2 groesser) -> 1:EQ, 5:NE


def resolve_species(data, insns, entries):
    """Pfad-sensitive Var-Aufloesung: DFS je Entry mit Var-Zustand + Call-Stack.
    GetGameVersion-Vergleiche werden als Versions-Tag (D/P) mitgefuehrt.
    Liefert Liste (kind, pc, details)."""
    found = {}
    LIMIT = 200000
    steps = 0
    # dateiweite Fallbacks fuer persistente Vars (z.B. 0x40B4 Fossil im Museum)
    fossil_vars = {a[0] for (op, _, a, _) in insns.values() if op == OP_FOSSIL}
    setvar_consts = defaultdict(set)
    for (op, _, a, _) in insns.values():
        if op == OP_SETVAR:
            setvar_consts[a[0]].add(a[1])
    for e in entries:
        if e not in insns:
            continue
        stack = [(e, (), (), None)]  # pc, vars, callstack, version-tag
        seen = set()
        cnt = {}
        steps = 0
        while stack:
            pc, state, cstack, vtag = stack.pop()
            st = dict(state)
            last_cmp = None
            while pc is not None and pc in insns:
                key = (pc, tuple(sorted(st.items(), key=str)), cstack, vtag)
                if key in seen:
                    break
                # Zustandsexplosion begrenzen: max. 6 Var-Zustaende pro (pc, Callstack, Tag)
                ck = (pc, cstack, vtag)
                cnt[ck] = cnt.get(ck, 0) + 1
                if cnt[ck] > 6:
                    LIMIT_HITS.append(pc)
                    break
                seen.add(key)
                steps += 1
                if steps > LIMIT:
                    LIMIT_HITS.append(pc)
                    break
                op, name, args, nxt = insns[pc]

                def val(x):
                    if not is_var(x):
                        return x
                    if x in st:
                        return st[x]
                    if x in fossil_vars:
                        return "fossil"
                    if setvar_consts.get(x):
                        return "var_0x%X(dateiweit SetVar: %s)" % (x, sorted(setvar_consts[x]))
                    return "var_0x%X" % x

                rec = None
                if op == OP_SETVAR:
                    st[args[0]] = args[1]
                elif op in (OP_COPYVAR, OP_SETORCOPY):
                    st[args[0]] = val(args[1])
                elif op == OP_FOSSIL:
                    st[args[0]] = "fossil"
                elif op == OP_GETSTARTER:
                    st[args[0]] = "player_starter"
                elif op == OP_GETVERSION:
                    st[args[0]] = "game_version"
                elif op == OP_GIVEMON:
                    rec = ("gift", {"species": val(args[0]), "level": val(args[1]), "item": val(args[2])})
                elif op == OP_GIVEEGG:
                    rec = ("egg", {"species": val(args[0]), "met_special": val(args[1])})
                elif op == OP_WILD:
                    rec = ("static", {"species": val(args[0]), "level": val(args[1]), "cmd": "WildBattle"})
                elif op == OP_LEGEND:
                    rec = ("static", {"species": val(args[0]), "level": val(args[1]), "cmd": "LegendaryBattle"})
                elif op == OP_FIRST:
                    rec = ("first_battle", {"species": val(args[0]), "level": val(args[1])})
                elif op == OP_TRADEINIT:
                    rec = ("trade", {"trade_id": args[0]})
                elif op == OP_STARTER:
                    rec = ("starter", {})
                elif op == OP_DAYCARE_EGG:
                    rec = ("egg", {"species": "daycare", "cmd": "GiveDaycareEgg"})
                if op == OP_CMPVAL:
                    last_cmp = (st.get(args[0]), args[1])
                elif op not in (0x1C, 0x1D):
                    pass
                kinds = OPS[op][1]
                if op not in (OP_SETVAR, OP_COPYVAR, OP_SETORCOPY, OP_FOSSIL, OP_GETSTARTER, OP_GETVERSION) and op not in SPECIAL:
                    for k, a in zip(kinds, args):
                        if k == "vp" and a in st:
                            del st[a]
                if rec:
                    if vtag:
                        rec[1]["version"] = vtag
                    k2 = (pc, json.dumps(rec[1], sort_keys=True, default=str))
                    found[k2] = (rec[0], pc, rec[1])
                # Versions-Tags fuer bedingte Spruenge
                taken_tag, fall_tag = vtag, vtag
                if op in (0x1C, 0x1D) and last_cmp and last_cmp[0] == "game_version" and args[0] in (1, 5):
                    eq = VERSION_NAMES.get(last_cmp[1], str(last_cmp[1]))
                    ne = "P" if eq == "D" else ("D" if eq == "P" else "!" + eq)
                    taken_tag, fall_tag = (eq, ne) if args[0] == 1 else (ne, eq)
                fs = tuple(st.items())
                if op == OP_GOTO:
                    pc = nxt + args[-1]
                    continue
                if op == OP_CALL:
                    cstack = cstack + (nxt,)
                    pc = nxt + args[-1]
                    continue
                if op == 0x1D:  # CallIf
                    stack.append((nxt + args[-1], fs, cstack + (nxt,), taken_tag))
                    vtag = fall_tag
                elif op in BRANCH_W_LAST:
                    stack.append((nxt + args[-1], fs, cstack, taken_tag))
                    if op == 0x1C:
                        vtag = fall_tag
                if op == OP_RETURN:
                    if cstack:
                        pc = cstack[-1]
                        cstack = cstack[:-1]
                        continue
                    break
                if op == OP_END:
                    break
                pc = nxt
    # Vollstaendigkeits-Pass: jeder erreichbare relevante Befehl muss einen Eintrag haben
    RELEVANT = {OP_GIVEMON, OP_GIVEEGG, OP_WILD, OP_LEGEND, OP_FIRST, OP_TRADEINIT, OP_STARTER, OP_DAYCARE_EGG}
    have = {pc for (_, pc, _) in found.values()}
    for pc, (op, name, args, nxt) in insns.items():
        if op in RELEVANT and pc not in have:
            def fv(x):
                if not is_var(x):
                    return x
                if x in fossil_vars:
                    return "fossil"
                if setvar_consts.get(x):
                    return "var_0x%X(dateiweit SetVar: %s)" % (x, sorted(setvar_consts[x]))
                return "var_0x%X" % x
            det = {"args": [fv(a) for a in args], "cmd": name, "note": "nicht pfadaufgeloest"}
            found[(pc, "fb")] = ({OP_GIVEMON: "gift", OP_GIVEEGG: "egg", OP_WILD: "static", OP_LEGEND: "static",
                                  OP_FIRST: "first_battle", OP_TRADEINIT: "trade", OP_STARTER: "starter",
                                  OP_DAYCARE_EGG: "egg"}[op], pc, det)
    return list(found.values())


def load_trades():
    t = {}
    for fn in sorted(os.listdir(TRADE_DIR)):
        m = re.match(r"narc_(\d+)\.bin", fn)
        if not m:
            continue
        d = open(os.path.join(TRADE_DIR, fn), "rb").read()
        # Offsets belegt: overlay_06.s NPCTradeApp_GetOfferedSpecies ldr [r0] (0x00),
        # NPCTradeApp_GetRequestedSpecies ldr [r0,#0x4c]
        t[int(m.group(1))] = {"offered": struct.unpack_from("<I", d, 0)[0],
                              "requested": struct.unpack_from("<I", d, 0x4C)[0]}
    return t


def load_map_headers():
    """map_id -> (scripts_bank, level_scripts_bank, MAPSEC-Name, MAP-Name) aus map_header.c"""
    txt = open(os.path.join(PD, "arm9", "src", "map_header.c"), encoding="utf-8").read()
    body = txt[txt.index("sMapHeaders[] = {"):]
    body = body[:body.index("};")]
    rows = []
    for m in re.finditer(r"\{([^{}]*)\},\s*//\s*(\w+)", body):
        f = [x.strip() for x in m.group(1).split(",")]
        def num(s):
            mm = re.search(r"narc_(\d+)_bin", s)
            return int(mm.group(1)) if mm else int(s, 0)
        rows.append((num(f[3]), num(f[4]), f[10], m.group(2)))
    return rows


def load_sections():
    sec = {}
    names = {}
    cur = None
    for line in open(MAPID_YML, encoding="utf-8"):
        m = re.match(r"^(\d+):", line)
        if m:
            cur = int(m.group(1))
            continue
        m = re.match(r"^\s+(\d+):\s*(\d+)\s*(?:#\s*(\S+))?", line)
        if m and cur == 41:
            sec[int(m.group(1))] = int(m.group(2))
    return sec


def main():
    if len(sys.argv) > 2 and sys.argv[1] == "--dump":
        dump(int(sys.argv[2]))
        return
    headers = load_map_headers()
    sections = load_sections()
    trades = load_trades()
    script_maps = defaultdict(list)
    level_files = set()
    for mid, (sb, lb, mapsec, mname) in enumerate(headers):
        script_maps[sb].append(mid)
        level_files.add(lb)
    files = sorted(int(re.match(r"narc_(\d+)", f).group(1)) for f in os.listdir(SCR_DIR) if f.endswith(".bin"))
    stats = {"files": len(files), "script_files": 0, "level_files_skipped": 0, "clean": 0, "desync_files": 0,
             "errors": 0, "insns": 0, "uncovered_files": 0}
    per_file = {}
    results = []
    unknown_files = []
    for fno in files:
        data = open(os.path.join(SCR_DIR, f"narc_{fno:04d}.bin"), "rb").read()
        is_level = fno in level_files and fno not in script_maps
        if is_level:
            stats["level_files_skipped"] += 1
            continue
        try:
            insns, entries, cs, errors, mov = disassemble(data)
        except Desync as d:
            per_file[fno] = {"error": str(d)}
            stats["desync_files"] += 1
            stats["errors"] += 1
            unknown_files.append(fno)
            continue
        stats["script_files"] += 1
        stats["insns"] += len(insns)
        # Abdeckung: Bytes nach Header, die weder Code noch (ab Movement-Ziel) Daten sind
        covered = set()
        for pc, (_, _, _, nxt) in insns.items():
            covered.update(range(pc, nxt))
        last_code = max((nxt for (_, _, _, nxt) in insns.values()), default=cs)
        first_mov = min(mov) if mov else len(data)
        gaps = [b for b in range(cs, min(first_mov, len(data))) if b not in covered]
        per_file[fno] = {"entries": len(entries), "insns": len(insns), "errors": errors,
                         "uncovered_code_bytes": len(gaps), "movement_targets": len(mov),
                         "maps": script_maps.get(fno, [])}
        if gaps:
            stats["uncovered_files"] += 1
        if errors:
            stats["desync_files"] += 1
            stats["errors"] += len(errors)
        else:
            stats["clean"] += 1
        for kind, pc, det in resolve_species(data, insns, entries):
            if kind == "trade":
                tr = trades.get(det["trade_id"])
                if tr:
                    det.update({"species": tr["offered"], "requested": tr["requested"]})
            mids = script_maps.get(fno, [])
            secs = sorted({sections.get(m) for m in mids if sections.get(m) is not None})
            results.append({"file": fno, "pc": pc, "kind": kind, **det,
                            "map_ids": mids, "map_names": [headers[m][3] for m in mids],
                            "sections": secs})
    json.dump(per_file, open(os.path.join(HERE, "per_file.json"), "w"), indent=1)
    json.dump(results, open(os.path.join(HERE, "sources_raw.json"), "w"), indent=1, default=str)
    json.dump(stats, open(os.path.join(HERE, "stats.json"), "w"), indent=1)
    print(json.dumps(stats, indent=1))
    print("Header-Fehler-Dateien:", unknown_files[:50])
    bad = {k: v for k, v in per_file.items() if v.get("errors")}
    print("Dateien mit Fehlern:", len(bad))
    for k in list(bad)[:40]:
        print(k, bad[k]["errors"][:3], bad[k].get("maps"))


if __name__ == "__main__":
    main()
