"""
CRO-Toolkit für 3DS-Reverse-Engineering (Gen 6/7, Azahar-Track).

Ein 3DS-Spiel besteht aus code.bin (Haupt-ARM11-Binary) + vielen CRO-Modulen
(relozierbare Bibliotheken in der RomFS, z.B. DllField.cro = Overworld-Engine).
Ghidra hat KEINEN CRO-Loader: lädt man eine .cro als Raw-Binary, bleiben alle
intra-modul-Pointer unrelociert -> 0 Cross-References, Strings nicht verlinkt.

Dieses Toolkit macht CROs Ghidra-tauglich:
  1. parse_header()              - CRO0-Header (Segmente, Reloc-Tabellen)
  2. apply_internal_relocations()- interne Typ-2-Relocs (ABS32) anwenden
  3. extract_name_registry()     - Name->Funktions-Adresse aus der Symbol-Tabelle

Adress-Schema: Datei roh als EIN Ghidra-Block bei Basis 0 laden
(`ARM:LE:32:v6`, Base 0x0) => Ghidra-Adresse == Datei-Offset. Die Relocs werden
in diesem Schema gepatcht (Segment-Basis = Segment-Datei-Offset; .bss ohne
Datei-Praesenz bekommt eine synthetische Basis hinter dem Dateiende).

Nutzung:
    python cro_tools.py <modul.cro>              # schreibt <modul>_reloc.bin + Stats
    python cro_tools.py <modul.cro> --registry   # zusaetzlich Name->Func-Dump

Danach in Ghidra (pyghidra, re-lab-venv) <modul>_reloc.bin als Raw ARM:LE:32:v6
Basis 0 importieren + analysieren. Siehe setup.md.

Quellen für das CRO0-Format: 3dbrew.org/wiki/CRO0, Citra cro_helper.cpp.
Verifiziert an Pokemon X DllField.cro (2026-10-23).
"""
from __future__ import annotations
import struct
import sys

# --- CRO0-Header-Feld-Offsets (ab Datei-Start; Magic bei 0x80) ---------------
H_MAGIC        = 0x80
H_NAME_OFF     = 0x84
H_FILE_SIZE    = 0x90
H_BSS_SIZE     = 0x94
H_SEG_TBL_OFF  = 0xC8
H_SEG_NUM      = 0xCC
H_EXP_NAMED    = 0xD0   # (off, num) Export Named Symbols
H_EXP_STR_OFF  = 0xE0
H_IMP_MOD_OFF  = 0xF0   # (off, num) Import-Modul-Tabelle
H_EXT_PATCH    = 0xF8   # (off, num) External/Import Relocations
H_IMP_NAMED    = 0x100
H_INT_PATCH    = 0x128  # (off, num) Internal Relocations  <-- die wichtigen
H_STATIC_PATCH = 0x130

SEG_TYPE = {0: ".text", 1: ".rodata", 2: ".data", 3: ".bss"}
RELOC_ABS32 = 2   # R_ARM_ABS32  -> write(base + addend)
RELOC_REL32 = 3   # R_ARM_REL32  -> write(base + addend - target)

# .bss hat keine Datei-Praesenz -> synthetische Basis hinter dem Dateiende
BSS_SYNTH_BASE = 0x200000


def _u32(d: bytes, o: int) -> int:
    return struct.unpack_from("<I", d, o)[0]


def is_cro(d: bytes) -> bool:
    return len(d) > 0x84 and d[H_MAGIC:H_MAGIC + 4] == b"CRO0"


def parse_header(d: bytes) -> dict:
    """Liest die relevanten CRO0-Header-Felder + Segment-Tabelle."""
    if not is_cro(d):
        raise ValueError("keine CRO0-Datei (Magic @0x80 fehlt)")
    st, sn = _u32(d, H_SEG_TBL_OFF), _u32(d, H_SEG_NUM)
    segs = []
    for i in range(sn):
        off, size, typ = struct.unpack_from("<III", d, st + i * 12)
        segs.append({"idx": i, "file_off": off, "size": size,
                     "type": typ, "name": SEG_TYPE.get(typ, str(typ))})
    name_off = _u32(d, H_NAME_OFF)
    end = d.find(b"\0", name_off)
    return {
        "module_name": d[name_off:end].decode("latin1") if 0 <= name_off < len(d) else "?",
        "file_size": _u32(d, H_FILE_SIZE),
        "bss_size": _u32(d, H_BSS_SIZE),
        "segments": segs,
        "internal_patch": (_u32(d, H_INT_PATCH), _u32(d, H_INT_PATCH + 4)),
        "ext_patch": (_u32(d, H_EXT_PATCH), _u32(d, H_EXT_PATCH + 4)),
        "imp_modules": (_u32(d, H_IMP_MOD_OFF), _u32(d, H_IMP_MOD_OFF + 4)),
    }


def _seg_base(seg: dict) -> int:
    """Modul-Adresse eines Segments im Datei-Offset-Schema."""
    return BSS_SYNTH_BASE if seg["type"] == 3 else seg["file_off"]


def apply_internal_relocations(d: bytearray) -> dict:
    """Wendet die internen Relocations (Typ 2/3) in-place an.

    Entry (12 Byte): target_tag(u32), type(u8), ref_seg(u8), pad, pad, addend(u32).
    target_tag: Bits0-3 = Segment-Index, Bits4-31 = Offset im Segment.
    Typ 2 (ABS32): write32(target, seg[ref_seg].base + addend)
    Typ 3 (REL32): write32(target, seg[ref_seg].base + addend - target)
    Returns Statistik-Dict.
    """
    hdr = parse_header(d)
    segs = hdr["segments"]
    ipo, ipn = hdr["internal_patch"]
    stats = {"total": ipn, "patched": 0, "skip_bss_target": 0,
             "skip_oob": 0, "by_type": {}, "by_refseg": {}}
    for i in range(ipn):
        b = ipo + i * 12
        tag = _u32(d, b)
        typ = d[b + 4]
        ref_seg = d[b + 5]
        addend = _u32(d, b + 8)
        stats["by_type"][typ] = stats["by_type"].get(typ, 0) + 1
        stats["by_refseg"][ref_seg] = stats["by_refseg"].get(ref_seg, 0) + 1
        tidx, toff = tag & 0xF, tag >> 4
        if tidx >= len(segs) or ref_seg >= len(segs):
            stats["skip_oob"] += 1
            continue
        target = _seg_base(segs[tidx]) + toff
        if segs[tidx]["type"] == 3:        # Ziel in .bss -> nicht in Datei
            stats["skip_bss_target"] += 1
            continue
        if target + 4 > len(d):
            stats["skip_oob"] += 1
            continue
        base = _seg_base(segs[ref_seg]) + addend
        val = base if typ == RELOC_ABS32 else (base - target) & 0xFFFFFFFF
        struct.pack_into("<I", d, target, val & 0xFFFFFFFF)
        stats["patched"] += 1
    return stats


def extract_name_registry(d: bytes) -> dict:
    """Extrahiert Name->Funktions-Adresse aus der Field-API-Symbol-Tabelle.

    Muster (nach apply_internal_relocations): in .rodata liegt ein Array von
    8-Byte-Records {func_ptr, name_ptr}. name_ptr zeigt auf einen Identifier-
    String, func_ptr (bei name_ptr - 4) auf den Funktions-Wrapper in .text.
    Nur Namen, deren func-Nachbar in den .text-Bereich zeigt, werden akzeptiert.
    """
    import re
    hdr = parse_header(d)
    text = next((s for s in hdr["segments"] if s["type"] == 0), None)
    rodata = next((s for s in hdr["segments"] if s["type"] == 1), None)
    if not text or not rodata:
        return {}
    t0, t1 = text["file_off"], text["file_off"] + text["size"]
    r0, r1 = rodata["file_off"], rodata["file_off"] + rodata["size"]
    ident = re.compile(rb"[A-Za-z_][A-Za-z0-9_]{2,40}\Z")
    reg = {}
    o = r0
    while o < r1 - 4:
        v = _u32(d, o)
        if r0 <= v < r1:                     # Pointer in .rodata?
            end = d.find(b"\0", v)
            if 0 < end - v <= 40 and ident.match(d[v:end]):
                func = _u32(d, o - 4) if o - 4 >= 0 else 0
                if t0 <= (func & ~1) < t1:    # Nachbar bei -4 = Code-Ptr?
                    reg.setdefault(d[v:end].decode(), func)
        o += 4
    return reg


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 1
    path = argv[1]
    dump_reg = "--registry" in argv
    with open(path, "rb") as f:
        data = bytearray(f.read())
    hdr = parse_header(data)
    print(f"Modul: {hdr['module_name']}  ({len(data):,} Bytes)")
    for s in hdr["segments"]:
        if s["size"]:
            print(f"  {s['name']:7s} file_off=0x{s['file_off']:06X} size=0x{s['size']:X}")
    stats = apply_internal_relocations(data)
    print(f"Interne Relocs: {stats['patched']}/{stats['total']} gepatcht "
          f"(bss-target={stats['skip_bss_target']} oob={stats['skip_oob']}) "
          f"types={stats['by_type']}")
    out = path.rsplit(".", 1)[0] + "_reloc.bin"
    with open(out, "wb") as f:
        f.write(data)
    print(f"-> {out}")
    reg = extract_name_registry(data)
    print(f"Name->Func-Registry: {len(reg)} Eintraege")
    if dump_reg:
        for n in sorted(reg):
            print(f"  {n:44s} 0x{reg[n]:06X}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
