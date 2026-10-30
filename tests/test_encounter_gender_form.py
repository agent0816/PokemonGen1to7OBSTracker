"""Geschlecht + Form für Encounter: Opponent-Decoder, Schema v10 (+ Backfill),
insert/sync_encounter und Durchreichen in encounter_view."""
from backend import pokedecoder as pd
from backend.classes.pokedex_db import PokedexDB
from backend.encounter_view import build_encounter_rows, group_soullink
from backend.pokemon_encoder import encrypt_gen3, shuffle_blocks


def _prng(seed):
    return (0x41C64E6D * seed + 0x6073) % 0x100000000


def _xor_stream(data: bytes, seed: int) -> bytes:
    key = seed
    out = bytearray(len(data))
    for i in range(len(data) // 2):
        key = _prng(key)
        word = int.from_bytes(data[i * 2:i * 2 + 2], "little")
        out[i * 2:i * 2 + 2] = (word ^ (key >> 16)).to_bytes(2, "little")
    return bytes(out)


def _gen3_party_slot(pid: int, species_internal: int) -> bytes:
    raw = bytearray(100)
    raw[0:4] = pid.to_bytes(4, "little")
    raw[4:8] = (0x12345678).to_bytes(4, "little")
    raw[84] = 10  # Level
    unshuffled = bytearray(48)
    unshuffled[0:2] = species_internal.to_bytes(2, "little")
    return encrypt_gen3(bytes(raw), bytes(unshuffled))


def _gen45_party_slot(pid: int, plaintext_128: bytes) -> bytes:
    checksum = pd.calculate_checksum(plaintext_128)
    shift = ((pid & 0x3E000) >> 0xD) % 24
    encrypted = _xor_stream(shuffle_blocks(plaintext_128, shift, 32), checksum)
    header = pid.to_bytes(4, "little") + b"\x00\x00" + checksum.to_bytes(2, "little")
    battle = _xor_stream(bytes(84), pid)  # 220-Byte-Gen-4-Party-Slot
    return header + encrypted + battle


def _gen67_slot(pid: int, plaintext_224: bytes) -> bytes:
    checksum = pd.calculate_checksum(plaintext_224)
    shift = ((pid & 0x3E000) >> 0xD) % 24
    encrypted = _xor_stream(shuffle_blocks(plaintext_224, shift, 56), pid)
    return pid.to_bytes(4, "little") + b"\x00\x00" + checksum.to_bytes(2, "little") + encrypted


# -- Decoder ------------------------------------------------------------------

def test_gen3_female_aus_pv():
    weiblich = pd.decode_opponent_gen3(_gen3_party_slot(0xAABB0010, 25))
    maennlich = pd.decode_opponent_gen3(_gen3_party_slot(0xAABB00F0, 25))
    assert weiblich["dexnr"] == 25 and weiblich["female"] is True
    assert maennlich["female"] is False
    assert weiblich["form"] == ""


def test_gen3_form_icognito_und_deoxys():
    icognito = pd.decode_opponent_gen3(_gen3_party_slot(0x01020304, 413))
    assert icognito["dexnr"] == 201 and icognito["form"] == "-b"
    deoxys_bg = pd.decode_opponent_gen3(_gen3_party_slot(0x01020304, 410), edition=34)
    deoxys_fr = pd.decode_opponent_gen3(_gen3_party_slot(0x01020304, 410), edition=33)
    assert deoxys_bg["dexnr"] == 386 and deoxys_bg["form"] == "-attack"
    assert deoxys_fr["form"] == "-speed"


def test_gen4_female_und_form():
    plaintext = bytearray(128)
    plaintext[0:2] = (422).to_bytes(2, "little")  # Schalellos
    plaintext[0x38] = 8                            # Ost-Form
    opp = pd.decode_opponent_gen45(_gen45_party_slot(0x87650010, bytes(plaintext)), 4)
    assert opp["dexnr"] == 422
    assert opp["female"] is True
    assert opp["form"] == "-east"


def _gen67_plaintext(dexnr: int, pid: int, gender_form_byte: int) -> bytes:
    plaintext = bytearray(224)
    plaintext[0:2] = dexnr.to_bytes(2, "little")
    plaintext[0x10:0x14] = pid.to_bytes(4, "little")
    plaintext[0x15] = gender_form_byte  # Struct 0x1D: Bit 0 fateful, 1–2 Geschlecht, 3–7 Form
    return bytes(plaintext)


def test_gen6_geschlecht_aus_byte_nicht_aus_pid():
    # PID-Low-Byte 0x10 ergaebe per PV-Formel "weiblich" — Byte sagt maennlich
    opp = pd.decode_opponent_gen67(
        _gen67_slot(0x13579BDF, _gen67_plaintext(422, 0xCAFE0010, (1 << 3) | (0 << 1))), 6)
    assert opp["dexnr"] == 422
    assert opp["female"] is False
    assert opp["form"] == "-east"
    # PID-Low-Byte 0xF0 ergaebe "maennlich" — Byte sagt weiblich, Fateful-Bit stoert nicht
    opp = pd.decode_opponent_gen67(
        _gen67_slot(0x13579BDF, _gen67_plaintext(422, 0xCAFE00F0, (1 << 3) | (1 << 1) | 1)), 6)
    assert opp["female"] is True
    assert opp["form"] == "-east"


def test_gen7_geschlechtslos_und_voller_decoder():
    # Magnetilo (81) geschlechtslos (2): nie weiblich, egal welche PID
    opp = pd.decode_opponent_gen67(
        _gen67_slot(0x2468ACE0, _gen67_plaintext(81, 0x00000000, 2 << 1)), 7)
    assert opp["female"] is False
    # pokemon67 nutzt dieselbe Quelle (Party-Slot 260 B: 232 + Kampfwerte)
    slot = _gen67_slot(0x2468ACE0, _gen67_plaintext(25, 0x000000F0, 1 << 1)) + bytes(28)
    mon = pd.pokemon67(slot, 7)
    assert mon.female is True


# -- DB -----------------------------------------------------------------------

def _enc(cur, pv, owner, edition, dexnr):
    cur.execute(
        "INSERT INTO encounters (personality, owner, edition, route, dexnr, timestamp) "
        "VALUES (?, ?, ?, 1, ?, 'x')",
        (pv, owner, edition, dexnr),
    )


def _mon(cur, pv, owner, dexnr, female, form):
    cur.execute(
        "INSERT INTO pokemon (personality, owner, dexnr, female, form) VALUES (?, ?, ?, ?, ?)",
        (pv, owner, str(dexnr), female, form),
    )


def test_v10_backfill(tmp_path):
    db = PokedexDB(tmp_path)
    db.connect()
    cur = db.connection.cursor()
    _mon(cur, 0x05, "1", 422, 1, "-east")       # gefangen, owner lokal 'slot'
    _enc(cur, 0x05, "Ash_1", 42, 422)
    _enc(cur, 0x110, "Ash_1", 34, 25)           # nicht gefangen, PV -> weiblich
    _enc(cur, 0x1F0, "Ash_1", 34, 25)           # nicht gefangen, PV -> maennlich
    _mon(cur, 0x210, "1", 26, 0, "-alola")      # entwickelt -> nur PV-Geschlecht
    _enc(cur, 0x210, "Ash_1", 34, 25)
    _enc(cur, 0x310, "Ash_1", 11, 25)           # Gen 1: unangetastet
    _mon(cur, 0x410, "1", 422, 1, "-east")      # Gen 6: pokemon.female per PV (alt) -> nur Form
    _enc(cur, 0x410, "Ash_1", 60, 422)
    _enc(cur, 0x510, "Ash_1", 70, 25)           # Gen 7 nicht gefangen: kein PV-Geschlecht
    # v10 erneut ausfuehren — muss idempotent sein (Spalten existieren schon)
    cur.execute("DELETE FROM schema_version WHERE version >= 10")
    db._migrate()
    rows = {r[0]: (r[1], r[2]) for r in db.connection.execute(
        "SELECT personality, female, form FROM encounters").fetchall()}
    assert rows == {
        0x05: (1, "-east"),
        0x110: (1, ""),
        0x1F0: (0, ""),
        0x210: (1, ""),
        0x310: (0, ""),
        0x410: (0, "-east"),
        0x510: (0, ""),
    }
    versions = [r[0] for r in db.connection.execute(
        "SELECT version FROM schema_version").fetchall()]
    assert versions.count(10) == 1


def test_insert_und_sync_encounter(tmp_path):
    db = PokedexDB(tmp_path)
    db.connect()
    assert db.insert_encounter(1, "Ash_1", 42, 5, 422, 10, False, True, False,
                               False, True, female=True, form="-east")
    # alter Client ohne female/form-Keys
    assert db.sync_encounter({"personality": 2, "owner": "Misty_1", "edition": 42,
                              "route": 5, "dexnr": 25, "lvl": 3})
    assert db.sync_encounter({"personality": 3, "owner": "Misty_1", "edition": 42,
                              "route": 6, "dexnr": 422, "lvl": 3,
                              "female": 1, "form": "-west"})
    encs = {e["personality"]: (e["female"], e["form"])
            for e in db.get_encounters("Ash_1") + db.get_encounters("Misty_1")}
    assert encs == {1: (1, "-east"), 2: (0, ""), 3: (1, "-west")}
    # Alt-Client-Replay ohne Keys darf befüllte Werte nicht zurücksetzen
    assert db.sync_encounter({"personality": 3, "owner": "Misty_1", "edition": 42,
                              "route": 6, "dexnr": 422, "lvl": 4,
                              "outcome": "caught"})
    enc = db.get_encounters("Misty_1")
    replayed = next(e for e in enc if e["personality"] == 3)
    assert (replayed["female"], replayed["form"], replayed["outcome"]) == (1, "-west", "caught")


def test_dupes_family_caught_mit_slot_owner(tmp_path):
    """pokemon.owner ist str(slot), Encounter-Owner 'name_<slot>' (Owner-Bug 2026-10-30)."""
    db = PokedexDB(tmp_path)
    db.connect()
    cur = db.connection.cursor()
    _mon(cur, 0x99, "4", 2, 0, "")              # Bisaknosp bei Spieler 4
    assert db.is_species_family_caught("Stephan_4", [1, 2, 3]) is True
    assert db.is_species_family_caught("Stephan_3", [1, 2, 3]) is False
    assert db.is_species_family_caught("Stephan_4", [4, 5, 6]) is False
    assert db.is_species_family_caught("4", [1, 2, 3]) is True


# -- Anzeige ------------------------------------------------------------------

def test_encounter_view_reicht_durch():
    encs = [{"personality": 1, "owner": "Ash_1", "edition": 42, "route": 5,
             "dexnr": 422, "lvl": 5, "female": 1, "form": "-east",
             "outcome": "caught", "link_group_id": 7}]
    rows, _, _ = build_encounter_rows(encs, {})
    assert rows[0]["female"] is True and rows[0]["form"] == "-east"
    groups = group_soullink(encs, {}, expected_count=1)
    member = groups[0]["members"][0]
    assert member["female"] is True and member["form"] == "-east"
