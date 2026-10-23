#!/usr/bin/env python3
"""code.bin aus einer 3DS-ROM (.3ds/.cci) extrahieren + bei Bedarf BLZ-dekomprimieren.

Nutzt die Keys aus Azahars `aes_keys.txt` (KEIN boot9.bin noetig) und liest die
Application-NCCH-Partition direkt aus dem NCSD-Container. Ausgabe = ARM11-Image,
fertig fuer Ghidra (Processor ARM:LE:32, Basis-Adresse 0x00100000).

Laeuft in der RE-venv (pyctr):
    .venv/re-lab/Scripts/python docs/reverse-engineering/extract_3ds_code.py \
        "<rom.3ds>" <out_dir> [aes_keys.txt]

Default fuer aes_keys.txt: %APPDATA%\\Azahar\\sysdata\\aes_keys.txt

Ergebnis:
    <out_dir>/code.bin               (roh aus ExeFS; evtl. komprimiert)
    <out_dir>/code_decompressed.bin  (nur wenn CompressExefsCode-Flag gesetzt war)
Die dekomprimierte Datei (bzw. code.bin, falls unkomprimiert) in Ghidra laden.

Verifiziert 2026-10-23 an Pokemon X (CTR-P-EKJA): .code 2.86 MB -> dekomprimiert 5.16 MB.
"""
import os
import re
import struct
import sys


def load_keyx(keyfile):
    """Nur KeyX-Slots aus aes_keys.txt in eine CryptoEngine laden (KeyY kommt aus NCCH-Header)."""
    from pyctr.crypto.engine import CryptoEngine
    ce = CryptoEngine(setup_b9_keys=False)
    pat = re.compile(r'^slot0x([0-9A-Fa-f]{2})KeyX\s*=\s*([0-9A-Fa-f]{32})\s*$')
    n = 0
    with open(keyfile, 'r', encoding='utf-8', errors='ignore') as f:
        for line in f:
            m = pat.match(line.strip())
            if m:
                ce.set_keyslot('x', int(m.group(1), 16), bytes.fromhex(m.group(2)))
                n += 1
    if n == 0:
        raise SystemExit(f"Keine KeyX-Slots in {keyfile} gefunden.")
    print(f"KeyX-Slots geladen: {n}")
    return ce


def extract_code(rom, ce):
    """Application-NCCH aus dem NCSD lesen, .code + Compress-Flag zurueckgeben."""
    from pyctr.type.ncch import NCCHReader, NCCHSection
    from pyctr.fileio import SubsectionIO
    with open(rom, 'rb') as f:
        f.seek(0x100)
        if f.read(4) != b'NCSD':
            raise SystemExit("Kein NCSD-Magic @0x100 - ist das eine .3ds/.cci?")
        f.seek(0x120)
        off_mu, size_mu = struct.unpack('<II', f.read(8))  # Partition 0 = Application
    offset, size = off_mu * 0x200, size_mu * 0x200
    print(f"Application-Partition: offset=0x{offset:X} size=0x{size:X}")
    ncch = NCCHReader(SubsectionIO(open(rom, 'rb'), offset, size), crypto=ce)
    print(f"Product code: {ncch.product_code}")
    data = ncch.exefs.open('.code').read()
    eh = ncch.open_raw_section(NCCHSection.ExtendedHeader).read()
    compressed = bool(eh[0x0D] & 1)  # SCI-Flags, bit0 = CompressExefsCode
    print(f".code: {len(data)} bytes, komprimiert={compressed}")
    return data, compressed


def blz_decompress(data):
    """3DS backward-LZ (BLZ) dekomprimieren. Algorithmus nach CUE's blz.c."""
    n = len(data)
    inc = int.from_bytes(data[n - 4:n], 'little')
    if inc == 0:
        return bytes(data)  # Flag gesetzt, aber nichts komprimiert
    hdr = data[n - 5]
    enc = int.from_bytes(data[n - 8:n - 5], 'little')
    dec_len = n + inc
    prefix = n - enc  # unkomprimierter Kopf, 1:1 uebernommen
    out = bytearray(dec_len)
    out[:prefix] = data[:prefix]
    ins, outp, mask, flags = n - hdr, dec_len, 0, 0
    while outp > prefix:
        mask >>= 1
        if mask == 0:
            ins -= 1
            flags = data[ins]
            mask = 0x80
        if (flags & mask) == 0:
            ins -= 1
            outp -= 1
            out[outp] = data[ins]
        else:
            ins -= 1
            b1 = data[ins]
            ins -= 1
            b2 = data[ins]
            length = (b1 >> 4) + 3
            disp = (((b1 & 0x0F) << 8) | b2) + 3
            for _ in range(length):
                outp -= 1
                out[outp] = out[outp + disp]
    return bytes(out)


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        raise SystemExit(1)
    rom, out = sys.argv[1], sys.argv[2]
    keyfile = sys.argv[3] if len(sys.argv) > 3 else os.path.join(
        os.environ.get('APPDATA', ''), 'Azahar', 'sysdata', 'aes_keys.txt')
    os.makedirs(out, exist_ok=True)
    print(f"ROM: {rom} ({os.path.getsize(rom) / 1e9:.2f} GB)")
    print(f"Keys: {keyfile}")

    ce = load_keyx(keyfile)
    data, compressed = extract_code(rom, ce)
    raw = os.path.join(out, 'code.bin')
    with open(raw, 'wb') as f:
        f.write(data)
    print(f"GESCHRIEBEN: {raw}")

    if compressed:
        dec = blz_decompress(data)
        w0 = struct.unpack_from('<I', dec, 0)[0]
        dst = os.path.join(out, 'code_decompressed.bin')
        with open(dst, 'wb') as f:
            f.write(dec)
        print(f"GESCHRIEBEN: {dst} ({len(dec)} bytes, entry-word=0x{w0:08X})")
        print("-> code_decompressed.bin in Ghidra laden (ARM:LE:32, Basis 0x00100000).")
    else:
        print("-> code.bin in Ghidra laden (ARM:LE:32, Basis 0x00100000).")


if __name__ == '__main__':
    main()
