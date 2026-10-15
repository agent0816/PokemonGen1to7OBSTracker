"""Tests für tools/find_pointers.py.

Strategie: synthetische Dumps mit bekannten Pattern-Positionen erzeugen und
prüfen, dass scan_dump / find_repeated_patterns / die CLI korrekte Treffer liefern.
"""
import io
import os
import sys
import traceback
import contextlib

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
os.chdir(PROJECT_ROOT)

from tools import find_pointers as fp  # noqa: E402


def _assert_eq(actual, expected, label):
    if actual != expected:
        raise AssertionError(f"{label}: erwartet {expected!r}, bekommen {actual!r}")


# ----------------------------------------------------------------------------
# Dump-Fabrik
# ----------------------------------------------------------------------------

def _make_dump(size: int, inserts: dict[int, bytes]) -> bytes:
    """Erzeugt Dump der gegebenen Größe mit Rausch-Füllung.

    inserts: {offset: pattern_bytes} — wird an den Offsets eingefügt.
    Rest: deterministisches Pseudo-Rauschen (LCG), damit keine False-Positive-
    Doppel-Pattern-Kollisionen auftreten.
    """
    buf = bytearray(size)
    seed = 0xC0FFEE
    for i in range(size):
        seed = (seed * 1103515245 + 12345) & 0x7FFFFFFF
        buf[i] = (seed >> 16) & 0xFF
    for offset, pattern in inserts.items():
        buf[offset:offset + len(pattern)] = pattern
    return bytes(buf)


# ----------------------------------------------------------------------------
# Tests: Core-Funktionen
# ----------------------------------------------------------------------------

def test_scan_dump_zwei_treffer():
    pattern = b"\x78\x56\x34\x12\x01\xEF\xCD\xAB"  # PID=0x12345678 + OTID=0xABCDEF01 LE
    dump = _make_dump(0x10000, {0x2A40: pattern, 0xB100: pattern})
    positions = fp.scan_dump(dump, pattern)
    _assert_eq(positions, [0x2A40, 0xB100], "zwei Treffer an korrekten Offsets")


def test_scan_dump_keine_treffer():
    dump = _make_dump(0x1000, {})
    positions = fp.scan_dump(dump, b"\xDE\xAD\xBE\xEF")
    _assert_eq(positions, [], "keine Treffer")


def test_scan_dump_ueberlappend():
    """Überlappende Matches werden einzeln gefunden."""
    pattern = b"ABABAB"
    # "ABABABAB" enthält zwei überlappende "ABABAB" (an 0 und 2)
    dump = b"ABABABAB" + b"\x00" * 100
    positions = fp.scan_dump(dump, pattern)
    _assert_eq(positions, [0, 2], "überlappende Treffer")


def test_parse_hex_varianten():
    expected = b"\x12\x34\x56\x78"
    _assert_eq(fp.parse_hex("12345678"), expected, "Kein-Prefix")
    _assert_eq(fp.parse_hex("0x12345678"), expected, "0x-Prefix")
    _assert_eq(fp.parse_hex("12 34 56 78"), expected, "mit Spaces")
    _assert_eq(fp.parse_hex("12,34,56,78"), expected, "mit Kommas")
    _assert_eq(fp.parse_hex("0x12 34,56 78"), expected, "gemischt")


def test_parse_hex_ungerade_laenge():
    try:
        fp.parse_hex("123")
    except ValueError:
        return
    raise AssertionError("ungerade Hex-Länge sollte ValueError werfen")


def test_parse_int_hex_und_dezimal():
    _assert_eq(fp.parse_int("0x1234"), 0x1234, "Hex-Int")
    _assert_eq(fp.parse_int("4660"), 4660, "Dezimal-Int")
    _assert_eq(fp.parse_int("  0xFF  "), 0xFF, "mit Whitespace")


def test_build_pid_otid_pattern():
    pat = fp.build_pid_otid_pattern(0x12345678, 0xABCDEF01)
    _assert_eq(pat, b"\x78\x56\x34\x12\x01\xEF\xCD\xAB", "PID+OTID LE")
    # Nur PID (Gen 4/5-Variante wenn OTID nicht bekannt)
    pat_only = fp.build_pid_otid_pattern(0x12345678, None)
    _assert_eq(pat_only, b"\x78\x56\x34\x12", "nur PID LE")


def test_is_interesting_filter():
    assert not fp.is_interesting(b"\x00" * 16), "Alle 0x00 uninteressant"
    assert not fp.is_interesting(b"\xFF" * 16), "Alle 0xFF uninteressant"
    assert not fp.is_interesting(b"\x00\xFF\x00\xFF" * 4), "Nur 0x00/0xFF uninteressant"
    assert fp.is_interesting(b"\x12\x34\x56\x78" * 4), "Gemischt interessant"


# ----------------------------------------------------------------------------
# Tests: Auto-Modus
# ----------------------------------------------------------------------------

def test_find_repeated_patterns_findet_pid_otid():
    pattern = b"\x78\x56\x34\x12\x01\xEF\xCD\xAB\xAA\xBB\xCC\xDD\xEE\x11\x22\x33"
    dump = _make_dump(0x10000, {0x3000: pattern, 0xC000: pattern})
    results = fp.find_repeated_patterns(dump, pattern_size=16, target_count=2)
    # Eines der Ergebnisse muss unser Pattern sein
    found = any(r[0] == pattern and r[2] == [0x3000, 0xC000] for r in results)
    assert found, f"Pattern nicht in Auto-Modus gefunden (Results: {len(results)})"


def test_find_repeated_patterns_filter_trivial():
    """Nullen-Blöcke sollen NICHT als Treffer erscheinen."""
    dump = bytearray(_make_dump(0x2000, {}))
    # Zwei Null-Blöcke absichtlich einfügen
    dump[0x100:0x120] = b"\x00" * 32
    dump[0x800:0x820] = b"\x00" * 32
    results = fp.find_repeated_patterns(bytes(dump), pattern_size=16, target_count=2)
    for pattern, count, offsets in results:
        assert fp.is_interesting(pattern), f"Trivial-Pattern sollte gefiltert sein: {pattern.hex()}"


def test_find_repeated_patterns_too_short():
    dump = _make_dump(0x1000, {})
    try:
        fp.find_repeated_patterns(dump, pattern_size=2)
    except ValueError:
        return
    raise AssertionError("pattern_size<4 sollte ValueError werfen")


# ----------------------------------------------------------------------------
# Tests: CLI (main)
# ----------------------------------------------------------------------------

def _run_cli(args: list[str]) -> tuple[int, str, str]:
    """Führt fp.main() aus, fängt stdout/stderr ab."""
    stdout = io.StringIO()
    stderr = io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        try:
            rc = fp.main(args)
        except SystemExit as e:
            rc = e.code if isinstance(e.code, int) else 1
    return rc, stdout.getvalue(), stderr.getvalue()


def test_cli_pid_otid_suche(tmp_path_str=None):
    import tempfile
    with tempfile.TemporaryDirectory() as tmpdir:
        dump_path = os.path.join(tmpdir, "test.bin")
        pattern = fp.build_pid_otid_pattern(0x12345678, 0xABCDEF01)
        dump = _make_dump(0x10000, {0x2A40: pattern, 0xB100: pattern})
        with open(dump_path, "wb") as f:
            f.write(dump)

        rc, out, err = _run_cli([
            dump_path,
            "--pid", "0x12345678",
            "--otid", "0xABCDEF01",
            "--domain-base", "0x02000000",
        ])
        _assert_eq(rc, 0, "Exit-Code 0")
        assert "Treffer:      2" in out, f"zwei Treffer erwartet — Output:\n{out}"
        assert "0x02002A40" in out, "absolute Adresse Team"
        assert "0x0200B100" in out, "absolute Adresse Box"
        assert "YAML-Vorschlag" in out, "YAML-Hinweis bei 2 Treffern"


def test_cli_pattern_direkt():
    import tempfile
    with tempfile.TemporaryDirectory() as tmpdir:
        dump_path = os.path.join(tmpdir, "test.bin")
        dump = _make_dump(0x1000, {0x100: b"\xDE\xAD\xBE\xEF\xCA\xFE"})
        with open(dump_path, "wb") as f:
            f.write(dump)

        rc, out, err = _run_cli([dump_path, "--pattern", "DE AD BE EF CA FE"])
        _assert_eq(rc, 0, "Exit-Code 0")
        assert "Treffer:      1" in out, f"ein Treffer erwartet — Output:\n{out}"
        assert "0x00000100" in out, "Offset 0x100 enthalten"


def test_cli_auto_modus():
    import tempfile
    with tempfile.TemporaryDirectory() as tmpdir:
        dump_path = os.path.join(tmpdir, "test.bin")
        pattern = b"\xAA\xBB\xCC\xDD\xEE\xFF\x11\x22\x33\x44\x55\x66\x77\x88\x99\x00"
        dump = _make_dump(0x8000, {0x1000: pattern, 0x5000: pattern})
        with open(dump_path, "wb") as f:
            f.write(dump)

        rc, out, err = _run_cli([dump_path, "--find-repeated", "--length", "16", "--count", "2"])
        _assert_eq(rc, 0, "Exit-Code 0")
        assert "0x00001000" in out and "0x00005000" in out, \
            f"Auto-Modus sollte beide Offsets zeigen — Output:\n{out}"


def test_cli_fehlende_argumente():
    import tempfile
    with tempfile.TemporaryDirectory() as tmpdir:
        dump_path = os.path.join(tmpdir, "test.bin")
        with open(dump_path, "wb") as f:
            f.write(b"\x00" * 100)
        rc, out, err = _run_cli([dump_path])
        # argparse ruft parser.error → SystemExit(2)
        _assert_eq(rc, 2, "Exit-Code 2 bei fehlenden Args")


def test_cli_dump_nicht_gefunden():
    rc, out, err = _run_cli(["/nonexistent/path/to/dump.bin", "--pattern", "1234"])
    _assert_eq(rc, 2, "Exit-Code 2 bei fehlender Datei")
    assert "nicht gefunden" in err, "Fehlermeldung erwartet"


# ----------------------------------------------------------------------------
# Tests: parse_range
# ----------------------------------------------------------------------------

def test_parse_range_normal():
    _assert_eq(fp.parse_range("0x1000:0x2000", 0x10000), (0x1000, 0x2000), "volle Range")


def test_parse_range_offen_links():
    _assert_eq(fp.parse_range(":0x2000", 0x10000), (0, 0x2000), "offen links = 0")


def test_parse_range_offen_rechts():
    _assert_eq(fp.parse_range("0x1000:", 0x10000), (0x1000, 0x10000), "offen rechts = Dump-Ende")


def test_parse_range_ungueltig():
    try:
        fp.parse_range("0x2000:0x1000", 0x10000)
    except ValueError:
        return
    raise AssertionError("Range mit start>=end sollte ValueError werfen")


def test_parse_range_ohne_doppelpunkt():
    try:
        fp.parse_range("0x1000", 0x10000)
    except ValueError:
        return
    raise AssertionError("Range ohne ':' sollte ValueError werfen")


# ----------------------------------------------------------------------------
# Tests: Auto-Slots-Modus (synthetisches Team+Box-Layout)
# ----------------------------------------------------------------------------

def _build_team_box_dump(total_size: int, slot_size: int, team_slot_size: int,
                        slots_per_box: int, p1: bytes, p2: bytes,
                        team_offset: int, box_offsets: list[int]) -> bytes:
    """Synthetischer Dump mit:
       - P1 + P2 im Team-Layout (team_slot_size Abstand)
       - P1 am Anfang jeder Box, P2 am Ende jeder Box (box_size = slots_per_box * slot_size)
    """
    dump = bytearray(_make_dump(total_size, {}))
    # Team
    dump[team_offset:team_offset + slot_size] = p1
    dump[team_offset + team_slot_size:team_offset + team_slot_size + slot_size] = p2
    # Boxen
    box_size = slots_per_box * slot_size
    for base in box_offsets:
        dump[base:base + slot_size] = p1
        dump[base + (slots_per_box - 1) * slot_size:base + (slots_per_box - 1) * slot_size + slot_size] = p2
    return bytes(dump)


def test_find_repeating_slot_patterns_basic():
    p1 = b"\xAA" + b"\x01\x02\x03\x04\x05\x06\x07" + b"\x11" * 72
    p2 = b"\xBB" + b"\x08\x09\x0A\x0B\x0C\x0D\x0E" + b"\x22" * 72
    dump = _build_team_box_dump(
        total_size=0x10000, slot_size=80, team_slot_size=100, slots_per_box=30,
        p1=p1, p2=p2,
        team_offset=0x100,
        box_offsets=[0x4000, 0x7000],
    )
    patterns = fp.find_repeating_slot_patterns(dump, slot_size=80, min_count=3)
    found_p1 = any(p == p1 and len(offs) == 3 for p, offs in patterns)
    found_p2 = any(p == p2 and len(offs) == 3 for p, offs in patterns)
    assert found_p1, f"P1 sollte 3x gefunden werden — Patterns: {[(len(o)) for _, o in patterns]}"
    assert found_p2, "P2 sollte 3x gefunden werden"


def test_find_repeating_slot_patterns_range():
    """Scan-Bereich begrenzt das Suchfenster."""
    p1 = b"\xAA\x01\x02\x03\x04\x05\x06\x07" + b"\x11" * 72
    dump = bytearray(_make_dump(0x10000, {}))
    # Drei Occurrences, aber eine ausserhalb der Range
    dump[0x1000:0x1050] = p1
    dump[0x2000:0x2050] = p1
    dump[0x8000:0x8050] = p1  # ausserhalb des Scan-Bereichs
    patterns = fp.find_repeating_slot_patterns(
        bytes(dump), slot_size=80, min_count=2,
        start=0, end=0x5000,
    )
    found = [offs for p, offs in patterns if p == p1]
    assert found and len(found[0]) == 2, f"P1 sollte nur 2x gefunden werden — {found}"


def test_find_repeating_slot_patterns_filter_trivial():
    """Null-Bloecke (trivial) werden nicht gemeldet."""
    dump = bytearray(_make_dump(0x8000, {}))
    dump[0x100:0x150] = b"\x00" * 80
    dump[0x500:0x550] = b"\x00" * 80
    dump[0x1000:0x1050] = b"\x00" * 80
    patterns = fp.find_repeating_slot_patterns(bytes(dump), slot_size=80, min_count=3)
    for p, offs in patterns:
        assert fp.is_interesting(p), f"Trivial-Pattern gemeldet: {p.hex()[:32]}"


def test_find_repeating_slot_patterns_slot_size_kleiner_prefix():
    try:
        fp.find_repeating_slot_patterns(b"\x00" * 100, slot_size=4)
    except ValueError:
        return
    raise AssertionError("slot_size < prefix_size sollte ValueError werfen")


def test_correlate_team_and_boxes_gen3_layout():
    """Gen-3-aehnliches Layout: slot=80, team_slot=100, slots_per_box=30, 2 Boxen."""
    p1 = b"\xAA\x01\x02\x03\x04\x05\x06\x07" + b"\x11" * 72
    p2 = b"\xBB\x08\x09\x0A\x0B\x0C\x0D\x0E" + b"\x22" * 72
    dump = _build_team_box_dump(
        total_size=0x10000, slot_size=80, team_slot_size=100, slots_per_box=30,
        p1=p1, p2=p2,
        team_offset=0x244C,
        box_offsets=[0x4000, 0x7000],
    )
    patterns = fp.find_repeating_slot_patterns(dump, slot_size=80, min_count=3)
    findings = fp.correlate_team_and_boxes(
        patterns, team_slot_size=100, slots_per_box=30, slot_size=80,
    )
    assert findings, "Keine Team+Box-Korrelation gefunden"
    best = findings[0]
    _assert_eq(best["team_offset"], 0x244C, "Team-Offset")
    _assert_eq(best["team_slot_size_observed"], 100, "Team-Slot-Abstand")
    _assert_eq(best["box_delta_most_common"], 29 * 80, "Box-Delta (29*slot_size)")
    box_p1_offs = sorted(o1 for o1, _ in best["box_pairs"])
    _assert_eq(box_p1_offs, [0x4000, 0x7000], "Box-Start-Offsets")


def test_correlate_team_and_boxes_gen45_layout():
    """Gen-4/5-aehnliches Layout: slot=136, team_slot=236, slots_per_box=30."""
    p1 = b"\xAA\x01\x02\x03\x04\x05\x06\x07" + b"\x11" * 128
    p2 = b"\xBB\x08\x09\x0A\x0B\x0C\x0D\x0E" + b"\x22" * 128
    dump = _build_team_box_dump(
        total_size=0x20000, slot_size=136, team_slot_size=236, slots_per_box=30,
        p1=p1, p2=p2,
        team_offset=0x1000,
        box_offsets=[0x5000, 0xA000, 0xF000],
    )
    patterns = fp.find_repeating_slot_patterns(dump, slot_size=136, min_count=4)
    findings = fp.correlate_team_and_boxes(
        patterns, team_slot_size=236, slots_per_box=30, slot_size=136,
    )
    assert findings, "Keine Team+Box-Korrelation gefunden"
    best = findings[0]
    _assert_eq(best["team_offset"], 0x1000, "Team-Offset")
    _assert_eq(best["team_slot_size_observed"], 236, "Team-Slot-Abstand")
    _assert_eq(best["box_delta_most_common"], 29 * 136, "Box-Delta")
    _assert_eq(len(best["box_pairs"]), 3, "3 Box-Paare")


def test_correlate_team_and_boxes_keine_treffer():
    """Nur wahlloses Rauschen — keine Korrelation."""
    dump = _make_dump(0x4000, {})
    patterns = fp.find_repeating_slot_patterns(dump, slot_size=80, min_count=3)
    findings = fp.correlate_team_and_boxes(patterns, team_slot_size=100,
                                            slots_per_box=30, slot_size=80)
    _assert_eq(findings, [], "Keine Findings bei reinem Rauschen")


def test_cli_auto_slots_mit_gen_flag():
    import tempfile
    p1 = b"\xAA\x11\x22\x33\x44\x55\x66\x77" + b"\x11" * 72
    p2 = b"\xBB\x88\x99\xAA\xBB\xCC\xDD\xEE" + b"\x22" * 72
    # Dump muss gross genug sein fuer Gen-3-Range-Defaults (0x02000000:0x02040000)
    dump = bytearray(_make_dump(0x02040000, {}))
    # Team + 2 Boxen innerhalb der EWRAM-Range
    dump[0x020244EC:0x020244EC + 80] = p1
    dump[0x020244EC + 100:0x020244EC + 100 + 80] = p2
    # Box 1
    dump[0x02028000:0x02028000 + 80] = p1
    dump[0x02028000 + 29 * 80:0x02028000 + 29 * 80 + 80] = p2
    # Box 2
    dump[0x02030000:0x02030000 + 80] = p1
    dump[0x02030000 + 29 * 80:0x02030000 + 29 * 80 + 80] = p2

    with tempfile.TemporaryDirectory() as tmpdir:
        dump_path = os.path.join(tmpdir, "test.bin")
        with open(dump_path, "wb") as f:
            f.write(bytes(dump))
        rc, out, err = _run_cli([dump_path, "--auto-slots", "--gen", "3"])
        _assert_eq(rc, 0, "Exit-Code 0")
        assert "Team-Offset:" in out, f"Team-Offset fehlt — Output:\n{out[:1000]}"
        assert "0x020244EC" in out, f"Team-Offset 0x020244EC fehlt — Output:\n{out[:1000]}"
        assert "0x02028000" in out, f"Box-1-Offset 0x02028000 fehlt — Output:\n{out[:1000]}"
        assert "0x02030000" in out, f"Box-2-Offset 0x02030000 fehlt — Output:\n{out[:1000]}"


def test_cli_auto_slots_ohne_slot_size():
    import tempfile
    with tempfile.TemporaryDirectory() as tmpdir:
        dump_path = os.path.join(tmpdir, "test.bin")
        with open(dump_path, "wb") as f:
            f.write(b"\x00" * 1024)
        rc, out, err = _run_cli([dump_path, "--auto-slots"])
        _assert_eq(rc, 2, "Exit-Code 2 ohne --slot-size/--gen")


# ----------------------------------------------------------------------------
# Tests: Gen 1/2 Nickname-Modus
# ----------------------------------------------------------------------------

def test_encode_gen12_string_buchstaben():
    # A=0x80, a=0xA0, 0=0xF6, '-'=0xE3; Terminator 0x50
    expected = bytes([0x80, 0xA0, 0xF6, 0xE3, 0x50])
    _assert_eq(fp.encode_gen12_string("Aa0-"), expected, "Aa0- kodiert")


def test_encode_gen12_string_slot_nicknames():
    slot1 = fp.encode_gen12_string("Slot1")
    slotlast = fp.encode_gen12_string("Slot-last")
    _assert_eq(slot1, bytes.fromhex("92 ab ae b3 f7 50"), "Slot1 kodiert")
    _assert_eq(slotlast, bytes.fromhex("92 ab ae b3 e3 ab a0 b2 b3 50"), "Slot-last kodiert")


def test_encode_gen12_string_ohne_terminator():
    result = fp.encode_gen12_string("Ab", terminator=False)
    _assert_eq(result, bytes([0x80, 0xA1]), "ohne Terminator")


def test_encode_gen12_string_ungueltiges_zeichen():
    try:
        fp.encode_gen12_string("ä")
    except ValueError:
        return
    raise AssertionError("ValueError erwartet bei unbekanntem Zeichen")


def test_find_gen12_nicknames_team_und_box():
    # Bau synthetischen Gen-1-Dump mit 3 Boxen (Stride 1122) + 1 Team
    slot1 = fp.encode_gen12_string("Slot1")
    slotlast = fp.encode_gen12_string("Slot-last")
    name_len = 11
    box_stride = 1122
    # Team in WRAM-Bereich: beide Nicknames adjacent (Delta 11)
    team_s1 = 0x1000
    # 3 Boxen: Slot1 + adjacent Slot-last (simuliert 2er-Box-Layout)
    box1_s1 = 0x4000
    box2_s1 = box1_s1 + box_stride
    box3_s1 = box2_s1 + box_stride
    inserts = {
        team_s1: slot1,
        team_s1 + name_len: slotlast,
        box1_s1: slot1,
        box1_s1 + name_len: slotlast,
        box2_s1: slot1,
        box2_s1 + name_len: slotlast,
        box3_s1: slot1,
        box3_s1 + name_len: slotlast,
    }
    dump = _make_dump(0x10000, inserts)

    result = fp.find_gen12_nicknames(dump, name_length=name_len, mons_per_box=20)
    _assert_eq(len(result["slot1_offsets"]), 4, "4 Slot1-Treffer")
    _assert_eq(len(result["slotlast_offsets"]), 4, "4 Slot-last-Treffer")
    _assert_eq(len(result["adjacent_pairs"]), 4, "4 Adjacent-Paare")
    _assert_eq(len(result["last_pairs"]), 0, "keine Last-Slot-Paare (adjacent-Layout)")


def test_cluster_box_pairs_gruppiert_bei_stride():
    pairs = [(0x4000, 0x400B), (0x4462, 0x446D), (0x48C4, 0x48CF),
             (0x1000, 0x100B)]  # Team-Paar separat
    clusters = fp.cluster_box_pairs(pairs, box_stride=1122)
    _assert_eq(len(clusters), 1, "ein Box-Cluster")
    _assert_eq(len(clusters[0]), 3, "3 Boxen im Cluster")
    # Team-Paar (0x1000) nicht dabei
    flat = [p for c in clusters for p in c]
    assert (0x1000, 0x100B) not in flat, "Team-Paar nicht geclustert"


def test_cluster_box_pairs_toleriert_alignment():
    # Gen 2: Stride 1104, mit 2 B Alignment-Jitter
    pairs = [(0x4000, 0x400B), (0x4452, 0x445D), (0x48A2, 0x48AD)]
    clusters = fp.cluster_box_pairs(pairs, box_stride=1104)
    _assert_eq(len(clusters), 1, "ein Cluster trotz Alignment-Drift")
    _assert_eq(len(clusters[0]), 3, "3 Boxen")


def test_cli_gen1_nickname_modus():
    import tempfile
    slot1 = fp.encode_gen12_string("Slot1")
    slotlast = fp.encode_gen12_string("Slot-last")
    name_len = 11
    stride = 1122
    inserts = {}
    # 12 Boxen in Bank 2+3 (cart-ram-layout): 0x4000-0x55EA und 0x6000-0x75EA
    for i in range(6):
        o = 0x4000 + i * stride
        inserts[o] = slot1
        inserts[o + name_len] = slotlast
    for i in range(6):
        o = 0x6000 + i * stride
        inserts[o] = slot1
        inserts[o + name_len] = slotlast
    dump = _make_dump(0x8000, inserts)

    with tempfile.TemporaryDirectory() as tmpdir:
        dump_path = os.path.join(tmpdir, "test.bin")
        with open(dump_path, "wb") as f:
            f.write(dump)
        rc, out, err = _run_cli([dump_path, "--auto-slots", "--gen", "1"])
        _assert_eq(rc, 0, "Exit-Code 0")
        assert "Gen 1/2 Nickname-Modus" in out, f"Header fehlt: {out[:200]}"
        assert "Cluster #1 (adjacent): 6 Boxen" in out, f"Cluster #1 fehlt:\n{out}"
        assert "Cluster #2 (adjacent): 6 Boxen" in out, f"Cluster #2 fehlt:\n{out}"
        assert "0x00004000" in out, f"erste Box-Adresse fehlt:\n{out}"


# ----------------------------------------------------------------------------
# Runner
# ----------------------------------------------------------------------------

ALL_TESTS = [
    test_scan_dump_zwei_treffer,
    test_scan_dump_keine_treffer,
    test_scan_dump_ueberlappend,
    test_parse_hex_varianten,
    test_parse_hex_ungerade_laenge,
    test_parse_int_hex_und_dezimal,
    test_build_pid_otid_pattern,
    test_is_interesting_filter,
    test_find_repeated_patterns_findet_pid_otid,
    test_find_repeated_patterns_filter_trivial,
    test_find_repeated_patterns_too_short,
    test_cli_pid_otid_suche,
    test_cli_pattern_direkt,
    test_cli_auto_modus,
    test_cli_fehlende_argumente,
    test_cli_dump_nicht_gefunden,
    test_parse_range_normal,
    test_parse_range_offen_links,
    test_parse_range_offen_rechts,
    test_parse_range_ungueltig,
    test_parse_range_ohne_doppelpunkt,
    test_find_repeating_slot_patterns_basic,
    test_find_repeating_slot_patterns_range,
    test_find_repeating_slot_patterns_filter_trivial,
    test_find_repeating_slot_patterns_slot_size_kleiner_prefix,
    test_correlate_team_and_boxes_gen3_layout,
    test_correlate_team_and_boxes_gen45_layout,
    test_correlate_team_and_boxes_keine_treffer,
    test_cli_auto_slots_mit_gen_flag,
    test_cli_auto_slots_ohne_slot_size,
    test_encode_gen12_string_buchstaben,
    test_encode_gen12_string_slot_nicknames,
    test_encode_gen12_string_ohne_terminator,
    test_encode_gen12_string_ungueltiges_zeichen,
    test_find_gen12_nicknames_team_und_box,
    test_cluster_box_pairs_gruppiert_bei_stride,
    test_cluster_box_pairs_toleriert_alignment,
    test_cli_gen1_nickname_modus,
]


def main():
    passed = 0
    failed = 0
    for test in ALL_TESTS:
        try:
            test()
            print(f"[OK]   {test.__name__}")
            passed += 1
        except Exception as err:
            print(f"[FAIL] {test.__name__}: {type(err).__name__}: {err}")
            traceback.print_exc()
            failed += 1
    print(f"\n{passed} bestanden, {failed} fehlgeschlagen (von {len(ALL_TESTS)})")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
