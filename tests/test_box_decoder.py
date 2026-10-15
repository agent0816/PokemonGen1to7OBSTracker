"""Phase-1-Tests für den Box-Dekoder.

Rein stdlib-basiert (assert + __main__), läuft ohne pytest-Installation.
Kompatibel zu pytest, sobald es verfügbar ist.

Strategie:
- Gen 3+: Encoder-Helfer (inverse Shuffle + XOR), Roundtrip-Test mit
  bekannten Semantik-Werten (species, xp, moves, ivs, ...).
- Gen 1/2: Direkt-Konstruktion der 33-/32-Byte-Structs, kein Encoding nötig.
- Box/Storage: Slots-Array mit gemischt belegten/leeren Slots.
"""
import os
import sys
import traceback

# Projekt-Root in sys.path, damit 'backend.*' importierbar ist
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# Arbeitsverzeichnis auf Projekt-Root, damit backend/data/*.yml relativ gefunden werden
os.chdir(PROJECT_ROOT)

from backend import pokedecoder as pd  # noqa: E402


# ----------------------------------------------------------------------------
# Encoder-Helfer (invertieren Decoder-Logik, für synthetische Fixtures)
# ----------------------------------------------------------------------------

GEN3_OFFSET_LUT = [[0,1,2,3],[0,1,3,2],[0,2,1,3],[0,3,1,2],[0,2,3,1],[0,3,2,1],
                   [1,0,2,3],[1,0,3,2],[2,0,1,3],[3,0,1,2],[2,0,3,1],[3,0,2,1],
                   [1,2,0,3],[1,3,0,2],[2,1,0,3],[3,1,0,2],[2,3,0,1],[3,2,0,1],
                   [1,2,3,0],[1,3,2,0],[2,1,3,0],[3,1,2,0],[2,3,1,0],[3,2,1,0]]

GEN45_67_OFFSET_LUT = GEN3_OFFSET_LUT  # identische Permutations-Tabelle


def _prng(seed):
    return (0x41C64E6D * seed + 0x6073) % 0x100000000


def _shuffle_inverse(plaintext: bytes, blocksize: int, shift_value: int) -> bytes:
    """Aus 'unshuffled' → 'decrypted' (vor XOR): pre-Shuffle zur Kodierung."""
    result = bytearray(blocksize * 4)
    for i in range(4):
        dst = GEN3_OFFSET_LUT[shift_value][i] * blocksize
        src = i * blocksize
        result[dst:dst + blocksize] = plaintext[src:src + blocksize]
    return bytes(result)


def _encode_gen3_slot(pid: int, otid: int, plaintext_48: bytes,
                      nickname_raw: bytes = b"", ot_name_raw: bytes = b"",
                      misc_byte: int = 0, language: int = 2) -> bytes:
    """Erzeugt 80-Byte-Gen-3-Box-Slot aus 48-Byte-Plaintext-Blockdaten."""
    assert len(plaintext_48) == 48
    shift_value = pid % 24
    key = pid ^ otid

    shuffled = _shuffle_inverse(plaintext_48, 12, shift_value)

    encrypted = bytearray(48)
    for i in range(0, 48, 4):
        word = int.from_bytes(shuffled[i:i + 4], "little")
        encrypted[i:i + 4] = (word ^ key).to_bytes(4, "little")

    checksum = pd.calculate_checksum(plaintext_48)

    header = bytearray(32)
    header[0:4] = pid.to_bytes(4, "little")
    header[4:8] = otid.to_bytes(4, "little")
    nickname_raw = nickname_raw[:10].ljust(10, b"\xFF")
    header[8:18] = nickname_raw
    header[18] = language
    header[19] = misc_byte
    ot_name_raw = ot_name_raw[:7].ljust(7, b"\xFF")
    header[20:27] = ot_name_raw
    header[27] = 0xFF  # Terminator
    header[28:30] = checksum.to_bytes(2, "little")

    return bytes(header) + bytes(encrypted)


def _encode_gen45_slot(pid: int, plaintext_128: bytes) -> bytes:
    """Erzeugt 136-Byte-Gen-4/5-Box-Slot aus 128-Byte-Plaintext-Blockdaten.

    Gen 4/5: Checksum aus Plaintext berechnet und als PRNG-Seed für XOR genutzt.
    """
    assert len(plaintext_128) == 128
    checksum = pd.calculate_checksum(plaintext_128)
    shift_value = ((pid & 0x3E000) >> 0xD) % 24

    shuffled = _shuffle_inverse(plaintext_128, 32, shift_value)

    key = checksum
    encrypted = bytearray(128)
    for i in range(len(shuffled) // 2):
        key = _prng(key)
        word = int.from_bytes(shuffled[i * 2:i * 2 + 2], "little")
        encrypted[i * 2:i * 2 + 2] = (word ^ (key >> 16)).to_bytes(2, "little")

    header = bytearray(8)
    header[0:4] = pid.to_bytes(4, "little")
    header[6:8] = checksum.to_bytes(2, "little")
    return bytes(header) + bytes(encrypted)


def _encode_gen67_slot(pid: int, plaintext_224: bytes) -> bytes:
    """Erzeugt 232-Byte-Gen-6/7-Box-Slot.

    Gen 6/7: PID als Encryption-Key (nicht Checksum wie bei Gen 4/5).
    """
    assert len(plaintext_224) == 224
    checksum = pd.calculate_checksum(plaintext_224)
    shift_value = ((pid & 0x3E000) >> 0xD) % 24

    shuffled = _shuffle_inverse(plaintext_224, 56, shift_value)

    key = pid
    encrypted = bytearray(224)
    for i in range(len(shuffled) // 2):
        key = _prng(key)
        word = int.from_bytes(shuffled[i * 2:i * 2 + 2], "little")
        encrypted[i * 2:i * 2 + 2] = (word ^ (key >> 16)).to_bytes(2, "little")

    header = bytearray(8)
    header[0:4] = pid.to_bytes(4, "little")
    header[6:8] = checksum.to_bytes(2, "little")
    return bytes(header) + bytes(encrypted)


# ----------------------------------------------------------------------------
# Test-Hilfen
# ----------------------------------------------------------------------------

def _assert_eq(actual, expected, label):
    if actual != expected:
        raise AssertionError(f"{label}: erwartet {expected!r}, bekommen {actual!r}")


# ----------------------------------------------------------------------------
# Tests: XP-Helper & Box-Namen
# ----------------------------------------------------------------------------

def test_xp_to_level():
    _assert_eq(pd.xp_to_level_mediumfast(0), 1, "XP=0 → Level 1")
    _assert_eq(pd.xp_to_level_mediumfast(1), 1, "XP=1 → Level 1")
    _assert_eq(pd.xp_to_level_mediumfast(125000), 50, "XP=125000 → Level 50")
    _assert_eq(pd.xp_to_level_mediumfast(1000000), 100, "XP=1M → Level 100")
    _assert_eq(pd.xp_to_level_mediumfast(2000000), 100, "XP>max → Level 100 (clamp)")
    _assert_eq(pd.xp_to_level_mediumfast(8000), 20, "XP=8000 → Level 20")


def test_box_name_decoding():
    # Gen 1: keine Namen
    _assert_eq(pd.decode_box_name(b"\x80\x81\x50", 1), "", "Gen 1 liefert leeren String")

    # Gen 2: nutzt gen1charset, Terminator 0x50
    charset = pd.gen1charset
    # Zeichen 0x80 = "A" (je nach charset) — wir prüfen nur Länge & Terminator-Stopp
    raw = bytes([0x80, 0x81, 0x50, 0x82, 0x83])
    result = pd.decode_box_name(raw, 2)
    assert 0x50 not in [ord(c) if len(c) == 1 else 0 for c in result], "Terminator nicht in Output"
    # Nach 0x50 darf nichts mehr dekodiert werden
    expected_len = sum(1 for b in raw[:2] if b in charset)
    _assert_eq(len(result), expected_len, "Gen 2 stoppt bei 0x50")

    # Gen 3: Terminator 0xFF
    gen3_raw = bytes([0xBB, 0xBC, 0xFF, 0xBD])
    gen3_result = pd.decode_box_name(gen3_raw, 3)
    # Nach 0xFF darf nichts mehr dekodiert werden
    expected3_len = sum(1 for b in gen3_raw[:2] if b in pd.gen3charset)
    _assert_eq(len(gen3_result), expected3_len, "Gen 3 stoppt bei 0xFF")


# ----------------------------------------------------------------------------
# Tests: Gen 3
# ----------------------------------------------------------------------------

def test_gen3_box_pokemon_roundtrip():
    pid = 0x12345678
    otid = 0x9ABCDEF0

    # Block A: species, item, xp → decoder liest [0:2], [2:4], [4:8]
    block_a = bytearray(12)
    block_a[0:2] = (25).to_bytes(2, "little")         # Pikachu
    block_a[2:4] = (3).to_bytes(2, "little")          # Item 3 (items3-LUT)
    block_a[4:8] = (125000).to_bytes(4, "little")     # Level 50 medium-fast

    # Block B: decoder liest moves [0x0D:0x15], pp [0x15:0x19]
    # Das sind 0x0D..0x18 relativ = Block-B [0x01:0x0C]+Block-C [0x00]
    # Wir setzen Moves in Block B ab Offset 1, PP ab Offset 9 (Block B)
    block_b = bytearray(12)
    # moves bei plaintext[0x0D:0x15] → block_b[1:9]
    block_b[1:3] = (33).to_bytes(2, "little")         # Move 1
    block_b[3:5] = (85).to_bytes(2, "little")         # Move 2
    block_b[5:7] = (91).to_bytes(2, "little")         # Move 3
    block_b[7:9] = (129).to_bytes(2, "little")        # Move 4
    # pp bei plaintext[0x15:0x19] → block_b[9:12] + block_c[0:1]
    block_b[9] = 30   # PP 1
    block_b[10] = 20  # PP 2
    block_b[11] = 15  # PP 3

    # Block C: erstes Byte ist PP 4 (wg. decoder-Offset), dann EVs
    # EVs bei plaintext[0x19:0x1E] → block_c[1:6]
    block_c = bytearray(12)
    block_c[0] = 10       # PP 4
    block_c[1] = 252      # EV slot 0
    block_c[2] = 4        # EV slot 1
    block_c[3] = 252      # EV slot 2
    block_c[4] = 0        # EV slot 3
    block_c[5] = 0        # EV slot 4

    # Block D: met-location bei plaintext[37:38] → block_d[1:2]
    # iv_base bei plaintext[0x29:0x2D] → block_d[5:9]
    block_d = bytearray(12)
    block_d[1] = 42       # met-location
    # IVs packed: hp=31, atk=25, def=20, spe=15, spa=10, spd=5
    iv_base = 31 | (25 << 5) | (20 << 10) | (15 << 15) | (10 << 20) | (5 << 25)
    block_d[5:9] = iv_base.to_bytes(4, "little")

    plaintext = bytes(block_a + block_b + block_c + block_d)

    # Nickname "PIKA" + OT "ASH"
    # Zeichen codierungen aus gen3charset invertieren wäre aufwändig — wir nutzen
    # bekannte Bytes und verifizieren nur, dass Dekodierung stoppt/funktioniert.
    nick_raw = bytes([0xBB, 0xBC, 0xBD, 0xBE, 0xFF])  # 5 "Zeichen" + Terminator
    ot_raw = bytes([0xCC, 0xCD, 0xFF])

    slot = _encode_gen3_slot(pid, otid, plaintext, nick_raw, ot_raw, misc_byte=0)

    # edition=33 (Smaragd) — beeinflusst Deoxys-Form, hier irrelevant
    p = pd.box_pokemon3(slot, edition=33)

    assert p is not None, "Slot sollte nicht leer sein"
    _assert_eq(p.dexnr, 25, "species = Pikachu")
    _assert_eq(p.experience_points, 125000, "XP")
    _assert_eq(p.lvl, 50, "Level aus XP abgeleitet")
    _assert_eq(p.personality, pid, "PID")
    _assert_eq(p.ot_id, otid & 0xFFFF, "OT-ID (public)")
    _assert_eq(p.ot_secret_id, otid >> 16, "OT-Secret-ID")
    _assert_eq(p.checksum_given, p.checksum_calculated, "Checksum konsistent")
    _assert_eq(p.route, 42, "met-location")
    _assert_eq(p.is_boxed, True, "is_boxed-Flag")
    _assert_eq(p.cur_hp, 0, "cur_hp = 0 (keine Battle-Stats in Box)")
    _assert_eq(p.max_hp, 0, "max_hp = 0")
    _assert_eq(p.nature, pid % 25, "Nature aus PID")

    # Moves
    _assert_eq(p.moves[0]["id"], 33, "Move 1 ID")
    _assert_eq(p.moves[0]["pp"], 30, "Move 1 PP")
    _assert_eq(p.moves[3]["id"], 129, "Move 4 ID")
    _assert_eq(p.moves[3]["pp"], 10, "Move 4 PP")

    # IVs
    _assert_eq(p.ivs["hp"], 31, "IV HP")
    _assert_eq(p.ivs["attack"], 25, "IV Atk")
    _assert_eq(p.ivs["special_defense"], 5, "IV SpD")


def test_gen3_empty_slot():
    empty = b"\x00" * 80
    _assert_eq(pd.box_pokemon3(empty, edition=33), None, "Leerer Gen-3-Slot = None")


def test_gen3_box_layout_mixed():
    """Eine Gen-3-Box (30 Slots × 80 Bytes = 2400 Bytes), gemischt belegt/leer."""
    pid = 0x11111111
    otid = 0x22222222
    plaintext = bytes(range(48))
    filled_slot = _encode_gen3_slot(pid, otid, plaintext, b"", b"")
    empty_slot = b"\x00" * 80

    # Slots 0, 5, 15 belegt, Rest leer
    box_data = bytearray(80 * 30)
    for idx in (0, 5, 15):
        box_data[idx * 80:(idx + 1) * 80] = filled_slot

    result = pd.decode_gen3_box(bytes(box_data), edition=33, slots_per_box=30)
    _assert_eq(len(result), 30, "30 Slots")
    for idx in range(30):
        if idx in (0, 5, 15):
            assert result[idx] is not None, f"Slot {idx} sollte belegt sein"
        else:
            assert result[idx] is None, f"Slot {idx} sollte leer sein"


# ----------------------------------------------------------------------------
# Tests: Gen 4/5
# ----------------------------------------------------------------------------

def test_gen45_box_pokemon_roundtrip_gen4():
    pid = 0x87654321

    # Plaintext: decoder liest species [0:2], item [2:4], ot_id [4:6], sid [6:8], xp [8:12]
    plaintext = bytearray(128)
    plaintext[0:2] = (143).to_bytes(2, "little")       # Relaxo
    plaintext[2:4] = (0).to_bytes(2, "little")         # kein Item
    plaintext[4:6] = (12345).to_bytes(2, "little")     # OT-ID
    plaintext[6:8] = (54321).to_bytes(2, "little")     # Secret-ID
    plaintext[8:12] = (125000).to_bytes(4, "little")   # XP → lvl 50
    plaintext[0x0D] = 3                                 # Ability
    # EVs bei [0x10:0x16]
    plaintext[0x10] = 252  # HP
    plaintext[0x11] = 4    # Atk
    plaintext[0x12] = 252  # Def
    # Moves bei [0x20:0x28], PP bei [0x28:0x2C]
    plaintext[0x20:0x22] = (1).to_bytes(2, "little")
    plaintext[0x22:0x24] = (2).to_bytes(2, "little")
    plaintext[0x28] = 35
    plaintext[0x29] = 25
    # IVs bei [0x38:0x3C]
    iv_base = 31 | (25 << 5) | (20 << 10) | (15 << 15) | (10 << 20) | (5 << 25)
    plaintext[0x38:0x3C] = iv_base.to_bytes(4, "little")

    slot = _encode_gen45_slot(pid, bytes(plaintext))
    p = pd.box_pokemon45(slot, gen=4)

    assert p is not None
    _assert_eq(p.dexnr, 143, "species Relaxo")
    _assert_eq(p.experience_points, 125000, "XP")
    _assert_eq(p.lvl, 50, "Level 50")
    _assert_eq(p.ot_id, 12345, "OT-ID")
    _assert_eq(p.ot_secret_id, 54321, "Secret-ID")
    _assert_eq(p.ability, 3, "Ability")
    _assert_eq(p.evs["hp"], 252, "EV HP")
    _assert_eq(p.moves[0]["id"], 1, "Move 1 ID")
    _assert_eq(p.moves[0]["pp"], 35, "Move 1 PP")
    _assert_eq(p.ivs["hp"], 31, "IV HP")
    _assert_eq(p.ivs["attack"], 25, "IV Atk")
    _assert_eq(p.is_boxed, True, "is_boxed")
    _assert_eq(p.checksum_given, p.checksum_calculated, "Checksum konsistent")


def test_gen45_empty_slot():
    empty = b"\x00" * 136
    _assert_eq(pd.box_pokemon45(empty, gen=4), None, "Leerer Gen-4-Slot = None")
    _assert_eq(pd.box_pokemon45(empty, gen=5), None, "Leerer Gen-5-Slot = None")


def test_gen45_out_of_range_species():
    """Species ≥ 650 → Dummy-Pokemon mit dexnr=0 zurück."""
    pid = 0x11223344
    plaintext = bytearray(128)
    plaintext[0:2] = (999).to_bytes(2, "little")  # invalid
    slot = _encode_gen45_slot(pid, bytes(plaintext))
    p = pd.box_pokemon45(slot, gen=4)
    assert p is not None
    _assert_eq(p.dexnr, 0, "Invalid species → dexnr 0")


# ----------------------------------------------------------------------------
# Tests: Gen 6/7
# ----------------------------------------------------------------------------

def test_gen67_box_pokemon_roundtrip():
    pid = 0xDEADBEEF

    plaintext = bytearray(224)
    plaintext[0:2] = (448).to_bytes(2, "little")       # Lucario
    plaintext[2:4] = (0).to_bytes(2, "little")
    plaintext[0x0C:0x0E] = (11111).to_bytes(2, "little")
    plaintext[0x0E:0x10] = (22222).to_bytes(2, "little")
    plaintext[0x10:0x14] = (216000).to_bytes(4, "little")  # lvl 60 medium-fast
    plaintext[0x14] = 5
    plaintext[0x18:0x1C] = (0xCAFEBABE).to_bytes(4, "little")  # alternative PID-Quelle
    plaintext[0x1C] = 17  # Nature
    # EVs bei [0x1E:0x24]
    plaintext[0x1E] = 252
    plaintext[0x1F] = 252
    plaintext[0x20] = 4
    # Moves bei [0x5A:0x62], PP bei [0x62:0x66]
    plaintext[0x5A:0x5C] = (7).to_bytes(2, "little")
    plaintext[0x62] = 15
    # IVs bei [0x74:0x78]
    iv_base = 31 | (30 << 5) | (29 << 10) | (28 << 15) | (27 << 20) | (26 << 25)
    plaintext[0x74:0x78] = iv_base.to_bytes(4, "little")

    slot = _encode_gen67_slot(pid, bytes(plaintext))
    p = pd.box_pokemon67(slot, gen=6)

    assert p is not None
    _assert_eq(p.dexnr, 448, "species Lucario")
    _assert_eq(p.experience_points, 216000, "XP")
    _assert_eq(p.lvl, 60, "Level 60")
    _assert_eq(p.ot_id, 11111, "OT-ID")
    _assert_eq(p.ot_secret_id, 22222, "Secret-ID")
    _assert_eq(p.ability, 5, "Ability")
    _assert_eq(p.nature, 17, "Nature")
    _assert_eq(p.personality, 0xCAFEBABE, "PID aus Block A")
    _assert_eq(p.moves[0]["id"], 7, "Move 1 ID")
    _assert_eq(p.moves[0]["pp"], 15, "Move 1 PP")
    _assert_eq(p.ivs["hp"], 31, "IV HP")
    _assert_eq(p.ivs["special_defense"], 26, "IV SpD")
    _assert_eq(p.is_boxed, True, "is_boxed")
    _assert_eq(p.checksum_given, p.checksum_calculated, "Checksum konsistent")


def test_gen67_empty_slot():
    empty = b"\x00" * 232
    _assert_eq(pd.box_pokemon67(empty, gen=6), None, "Leerer Gen-6-Slot = None")
    _assert_eq(pd.box_pokemon67(empty, gen=7), None, "Leerer Gen-7-Slot = None")


# ----------------------------------------------------------------------------
# Tests: Gen 1/2
# ----------------------------------------------------------------------------

def test_gen1_box_pokemon():
    # 33-Byte-Struct konstruieren
    struct = bytearray(33)
    struct[0x00] = 25                     # Pikachu
    struct[0x01:0x03] = (80).to_bytes(2, "big")   # cur_hp
    struct[0x03] = 42                     # level
    struct[0x08] = 85                     # Move 1
    struct[0x09] = 86                     # Move 2
    struct[0x0C:0x0E] = (12345).to_bytes(2, "big")   # OT-ID
    struct[0x0E:0x11] = (100000).to_bytes(3, "big")  # XP
    struct[0x11:0x13] = (50).to_bytes(2, "big")      # HP-EV
    struct[0x1B] = (15 << 4) | 10         # atk-IV=15, def-IV=10
    struct[0x1C] = (5 << 4) | 7           # spe-IV=5, spc-IV=7
    struct[0x1D] = 25                     # PP 1

    nick = bytes([0x80, 0x81, 0x82, 0x50, 0, 0, 0, 0, 0, 0, 0])
    ot = bytes([0x90, 0x91, 0x50, 0, 0, 0, 0, 0, 0, 0, 0])

    p = pd.box_pokemon1(bytes(struct), ot, nick)
    assert p is not None
    _assert_eq(p.lvl, 42, "Level")
    _assert_eq(p.cur_hp, 80, "cur_hp")
    _assert_eq(p.max_hp, 80, "max_hp = cur_hp (kein expliziter Max-Wert)")
    _assert_eq(p.ot_id, 12345, "OT-ID")
    _assert_eq(p.experience_points, 100000, "XP")
    _assert_eq(p.ivs["attack"], 15, "IV Atk")
    _assert_eq(p.ivs["defense"], 10, "IV Def")
    _assert_eq(p.ivs["speed"], 5, "IV Spe")
    _assert_eq(p.ivs["special"], 7, "IV Spc")
    _assert_eq(p.moves[0]["id"], 85, "Move 1")
    _assert_eq(p.moves[0]["pp"], 25, "PP 1")
    _assert_eq(p.is_boxed, True, "is_boxed")


def test_gen1_empty_slot():
    _assert_eq(pd.box_pokemon1(b"\x00" * 33), None, "species=0 → None")
    _assert_eq(pd.box_pokemon1(b"\xFF" + b"\x00" * 32), None, "species=0xFF → None")


def test_gen2_box_pokemon():
    struct = bytearray(32)
    struct[0x00] = 133                    # Evoli
    struct[0x01] = 1                      # Item
    struct[0x02] = 33                     # Move 1
    struct[0x06:0x08] = (54321).to_bytes(2, "big")   # OT-ID
    struct[0x08:0x0B] = (50000).to_bytes(3, "big")   # XP
    # IVs in Gen 1/2 sind 4-Bit-Werte (0–15)
    struct[0x15] = (15 << 4) | 10         # atk-IV=15, def-IV=10
    struct[0x16] = (13 << 4) | 7          # spe-IV=13, spc-IV=7
    struct[0x17] = 30                     # PP 1
    struct[0x1F] = 33                     # Level

    nick = bytes([0x80, 0x50, 0, 0, 0, 0, 0, 0, 0, 0, 0])
    ot = bytes([0x90, 0x50, 0, 0, 0, 0, 0, 0, 0, 0, 0])

    p = pd.box_pokemon2(bytes(struct), ot, nick)
    assert p is not None
    _assert_eq(p.lvl, 33, "Level")
    _assert_eq(p.ot_id, 54321, "OT-ID")
    _assert_eq(p.experience_points, 50000, "XP")
    _assert_eq(p.ivs["attack"], 15, "IV Atk")
    _assert_eq(p.ivs["special"], 7, "IV Spc")
    _assert_eq(p.moves[0]["id"], 33, "Move 1")
    _assert_eq(p.moves[0]["pp"], 30, "PP 1")
    _assert_eq(p.is_boxed, True, "is_boxed")


def test_gen1_gen2_full_box():
    """Box mit 2 belegten + 18 leeren Slots für Gen 1."""
    box = bytearray(0x462)
    box[0] = 2  # 2 Pokemon in der Box

    # Gen 1 nutzt interne IDs → species1_lut mappt zu Pokedex-Nr
    # Interner Index 25 → Pokedex-Nr 92 (Nebulak)
    slot0 = bytearray(33)
    slot0[0x00] = 25
    slot0[0x03] = 10
    slot0[0x01:0x03] = (20).to_bytes(2, "big")
    box[0x16:0x16 + 33] = slot0

    # Interner Index 1 → Pokedex-Nr 112 (Rihorn)
    slot1 = bytearray(33)
    slot1[0x00] = 1
    slot1[0x03] = 5
    slot1[0x01:0x03] = (15).to_bytes(2, "big")
    box[0x16 + 33:0x16 + 66] = slot1

    result = pd.decode_gen1_box(bytes(box))
    _assert_eq(len(result), 20, "20 Slots")
    assert result[0] is not None, "Slot 0 belegt"
    _assert_eq(result[0].dexnr, pd.species1_lut[25], "Slot 0: interne ID 25 → Dex")
    assert result[1] is not None, "Slot 1 belegt"
    _assert_eq(result[1].dexnr, pd.species1_lut[1], "Slot 1: interne ID 1 → Dex")
    for i in range(2, 20):
        assert result[i] is None, f"Slot {i} leer (count=2)"


def test_decode_box_dispatcher():
    """decode_box dispatcht anhand Edition zur richtigen Gen-Funktion."""
    # Gen 1 (edition 11 = Rot): erwartet 20 Slots
    box1 = bytearray(0x462)
    box1[0] = 1
    slot = bytearray(33)
    slot[0x00] = 25
    slot[0x03] = 5
    box1[0x16:0x16 + 33] = slot
    result1 = pd.decode_box(bytes(box1), edition=11)
    _assert_eq(len(result1), 20, "Gen 1: 20 Slots")
    assert result1[0] is not None and result1[1] is None, "Gen 1 dispatch OK"

    # Gen 2 (edition 23 = Kristall): erwartet 20 Slots, 32-B-Struct
    box2 = bytearray(0x450)
    box2[0] = 1
    slot2 = bytearray(32)
    slot2[0x00] = 25
    slot2[0x1F] = 10
    box2[0x16:0x16 + 32] = slot2
    result2 = pd.decode_box(bytes(box2), edition=23)
    _assert_eq(len(result2), 20, "Gen 2: 20 Slots")
    assert result2[0] is not None, "Gen 2 dispatch OK"

    # Unbekannte Edition -> ValueError
    try:
        pd.decode_box(b"", edition=99)
        raise AssertionError("ValueError erwartet für edition=99")
    except ValueError:
        pass


# ----------------------------------------------------------------------------
# Runner
# ----------------------------------------------------------------------------

ALL_TESTS = [
    test_xp_to_level,
    test_box_name_decoding,
    test_gen3_box_pokemon_roundtrip,
    test_gen3_empty_slot,
    test_gen3_box_layout_mixed,
    test_gen45_box_pokemon_roundtrip_gen4,
    test_gen45_empty_slot,
    test_gen45_out_of_range_species,
    test_gen67_box_pokemon_roundtrip,
    test_gen67_empty_slot,
    test_gen1_box_pokemon,
    test_gen1_empty_slot,
    test_gen2_box_pokemon,
    test_gen1_gen2_full_box,
    test_decode_box_dispatcher,
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
