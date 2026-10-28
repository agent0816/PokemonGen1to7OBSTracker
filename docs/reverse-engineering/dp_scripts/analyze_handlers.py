"""Analysiert die D/P-Script-Command-Handler im pokediamond-Decomp und
leitet pro Opcode die Argumentgroessen ab (Reihenfolge der Reads)."""
import re, os, sys, json, glob
OUT = os.path.dirname(os.path.abspath(__file__))
# pokediamond-Decomp als Geschwister-Ordner des Repos (oder per Env POKEDIAMOND).
ROOT = os.environ.get("POKEDIAMOND",
                      os.path.join(OUT, "..", "..", "..", "..", "pokediamond"))
names = [l.strip() for l in open(os.path.join(OUT, "table.txt"))]

csrc = {}
for f in glob.glob(os.path.join(ROOT, "arm9", "src", "*.c")):
    txt = open(f, encoding="utf-8", errors="replace").read()
    for m in re.finditer(r"^(?:THUMB_FUNC\s+)?(?:static\s+)?BOOL\s+(ScrCmd_\w+)\s*\(([^)]*)\)[^{;]*\{", txt, re.M):
        i = m.end(); depth = 1
        while depth:
            c = txt[i]
            if c == '{': depth += 1
            elif c == '}': depth -= 1
            i += 1
        lineno = txt.count("\n", 0, m.start()) + 1
        csrc[m.group(1)] = (os.path.relpath(f, ROOT), lineno, txt[m.end():i-1])
asrc = {}
for f in glob.glob(os.path.join(ROOT, "arm9", "asm", "*.s")):
    lines = open(f, encoding="utf-8", errors="replace").read().split("\n")
    cur = None
    for n, l in enumerate(lines):
        m = re.match(r"\s*thumb_func_start (ScrCmd_\w+)", l)
        if m:
            cur = m.group(1); asrc[cur] = (os.path.relpath(f, ROOT), n + 1, []); continue
        if cur:
            if "thumb_func_end" in l or ("thumb_func_start" in l):
                cur = None; continue
            asrc[cur][2].append(l)

TOK = re.compile(r"ScriptReadHalfword\(ctx\)|ScriptReadWord\(ctx\)|ScriptReadByte\(ctx\)|ScriptGetVarPointer\(ctx\)|ScriptGetVar\(ctx\)")
res = []
for op, nm in enumerate(names):
    entry = {"op": op, "name": nm}
    if nm in csrc:
        f, ln, body = csrc[nm]
        entry["src"] = f"{f}:{ln}"
        seq = []
        for t in TOK.findall(body):
            if "Word" in t: seq.append("w")
            elif "Byte" in t: seq.append("b")
            elif "GetVarPointer" in t: seq.append("vp")
            elif "GetVar" in t: seq.append("v")
            else: seq.append("h")
        entry["args"] = seq
        flags = []
        if re.search(r"\b(if|switch|for|while)\b", body) and TOK.search(body):
            flags.append("cond")
        # Helper-Aufrufe, die ctx weiterreichen
        for h in re.findall(r"(\w+)\(\s*ctx\s*[,)]", body):
            if not TOK.match(h + "(ctx)") and h not in ("ScriptReadHalfword","ScriptReadWord","ScriptReadByte","ScriptGetVar","ScriptGetVarPointer"):
                flags.append("helper:" + h)
        if "scriptPtr" in body: flags.append("scriptPtr")
        entry["flags"] = flags
    elif nm in asrc:
        f, ln, lines = asrc[nm]
        entry["src"] = f"{f}:{ln}"
        seq = []
        for l in lines:
            if "bl ScriptReadHalfword" in l: seq.append("h")
            elif "bl ScriptReadWord" in l: seq.append("w")
        entry["args"] = seq
        entry["flags"] = ["asm"] + (["branch"] if any(re.match(r"\s*b(eq|ne|lt|gt|le|ge|hi|lo|hs|cc|cs|ls|mi|pl)\b", l) for l in lines) else [])
        if any(re.search(r"#0x8\]", l) and "str" in l for l in lines): entry["flags"].append("ptrwrite?")
    else:
        entry["src"] = None; entry["args"] = None; entry["flags"] = ["NOTFOUND"]
    res.append(entry)
json.dump(res, open(os.path.join(OUT, "handlers_raw.json"), "w"), indent=1)
for e in res:
    if e["flags"]:
        print(hex(e["op"]), e["name"], e["src"], e["args"], e["flags"])
