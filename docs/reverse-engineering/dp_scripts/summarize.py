"""Fasst sources_raw.json pro Section zusammen und diffed gegen sinnoh_dppt.yml."""
import json, re, os
from collections import defaultdict
HERE = os.path.dirname(os.path.abspath(__file__))
YML = os.path.join(HERE, "..", "..", "..", "backend", "data", "maps", "sinnoh_dppt.yml")
FOSSILS = [142, 138, 140, 345, 347, 410, 408]  # scrcmd_fossils.c gFossilPokemonMap
r = json.load(open(os.path.join(HERE, "sources_raw.json")))
by_sec = defaultdict(list)
for x in r:
    e = {k: v for k, v in x.items() if k not in ("sections", "map_ids", "pc")}
    e["pc"] = hex(x["pc"])
    if e.get("species") == "fossil":
        e["species_candidates"] = FOSSILS
    for s in (x["sections"] or ["unmapped"]):
        if e not in by_sec[s]:
            by_sec[s].append(e)
out = {str(k): v for k, v in sorted(by_sec.items(), key=lambda kv: (isinstance(kv[0], str), kv[0]))}
json.dump(out, open(os.path.join(HERE, "dp_nonwild_sources.json"), "w"), indent=1)
# YAML (handgeschrieben, stdlib)
with open(os.path.join(HERE, "dp_nonwild_sources.yml"), "w", encoding="utf-8") as f:
    f.write("# D/P Nicht-Wild-Quellen pro Section (met_location), aus pokediamond scr_seq_release via dp_disasm.py\n")
    for k, v in out.items():
        f.write(f"{k}:\n")
        for e in v:
            f.write("  - {" + ", ".join(f"{a}: {json.dumps(b, ensure_ascii=False)}" for a, b in e.items()) + "}\n")
# Diff
KMAP = {"static": "static", "gift": "gift", "egg": "gift", "trade": "trade", "starter": "gift"}
cur = {}
sec = None
for line in open(YML, encoding="utf-8"):
    m = re.match(r"^  (\d+):\s*$", line)
    if m:
        sec = int(m.group(1)); continue
    m = re.match(r"^    pokemon: \[(.*)\]", line)
    if m and sec is not None:
        cur[sec] = {p.strip() for p in m.group(1).split(",") if p.strip()}
dp = defaultdict(set)
for s, v in by_sec.items():
    for e in v:
        if e["kind"] in KMAP:
            dp[s].add(KMAP[e["kind"]])
print("Section | yml (ohne wild) | D/P-Skripte | fehlt in yml | zusaetzlich in yml")
for s in sorted(set(cur) | {k for k in dp if isinstance(k, int)}):
    y = cur.get(s, set()) - {"wild"}
    d = dp.get(s, set())
    if y != d:
        print(s, sorted(y), sorted(d), sorted(d - y), sorted(y - d), "" if s in cur else "(Section nicht in yml)")
print("unmapped:", by_sec.get("unmapped"))
