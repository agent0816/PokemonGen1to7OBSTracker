"""ROM-Boot-Garbage-Filter fuer Bag-Reads (Testrunde 2026-11-10).

Garbage-Muster aus Live-Dump DE-Saphir am Titelbild/Birch-Intro
(ghidra-bizhawk-MCP, 2026-11-11): pc/items/keyitems komplett mit
id=0, qty=0x1A92 gefuellt, in 'beeren' u.a. id=1 (Meisterball).
"""
from backend import bag_decoder as bd
from backend.bag_decoder import BagItem
from backend.bag_view import item_lut_for_edition
from backend.classes.Pokemon import Pokemon, party_has_valid_pokemon

# R/S-Adressen aus pointer_gen3.yml, Fenster ab bag_pc.
_RS_BASE = 0x02025BCC
_RS_POCKETS = {
    "pc": 0x02025BCC, "items": 0x02025C94, "keyitems": 0x02025CE4,
    "baelle": 0x02025D34, "tmvm": 0x02025D74, "beeren": 0x02025E74,
}


def _saphir_title_dump() -> bytes:
    raw = bytearray([0, 0, 0x92, 0x1A] * 90 + [0] * (864 - 360))
    raw[360 + 32] = 64            # Einzelbyte in 'baelle' hinter leerem Slot 0
    beeren = 680                  # Offset der Beeren-Tasche im Fenster
    raw[beeren:beeren + 8] = bytes([1, 0, 128, 0, 15, 0, 4, 0])
    return bytes(raw)


def _decode_rs(raw: bytes, edition: int = 32) -> dict:
    out = {}
    for key, addr in _RS_POCKETS.items():
        start, size = bd.pocket_window(addr, edition, key)
        chunk = raw[start - _RS_BASE:start - _RS_BASE + size]
        out[key] = bd.decode_pocket(edition, key, chunk)
    return out


def _check(pockets, edition):
    return bd.bag_read_plausible(pockets, edition, item_lut_for_edition(edition))


def test_saphir_titelbild_garbage_wird_verworfen():
    pockets = _decode_rs(_saphir_title_dump())
    # Decoder liefert den Muell als Items (Tester-Log: pc=50, items=20, keyitems=20)
    assert len(pockets["pc"]) == 50
    assert len(pockets["items"]) == 20
    assert bd.bag_has_catching_ball(pockets, 32)   # Meisterball in 'beeren'
    ok, reason = _check(pockets, 32)
    assert ok is False
    assert "id 0" in reason


def test_ball_in_beeren_tasche_wird_verworfen():
    pockets = {"items": [], "beeren": [BagItem(1, 128)], "baelle": [], "pc": []}
    ok, reason = _check(pockets, 31)
    assert ok is False
    assert "Ball ausserhalb" in reason


def test_kein_ball_in_ball_tasche_wird_verworfen():
    ok, _ = _check({"baelle": [BagItem(13, 1)]}, 33)   # Trank in Ball-Tasche
    assert ok is False


def test_qty_ueber_cap_wird_verworfen():
    # E/FR/BG mit falschem XOR-Key → qty unsinnig gross
    assert _check({"items": [BagItem(13, 54321)]}, 34)[0] is False
    assert _check({"tasche": [BagItem(4, 150)]}, 11)[0] is False  # Gen 1 cap 99
    assert _check({"items": [BagItem(13, 0)]}, 32)[0] is False    # Gen 3 qty 0


def test_id_ausserhalb_lut_wird_verworfen():
    assert _check({"items": [BagItem(5000, 1)]}, 41)[0] is False


def test_beeren_und_tm_tasche_nur_passende_slugs():
    assert _check({"beeren": [BagItem(13, 1)]}, 32)[0] is False
    assert _check({"tmvm": [BagItem(13, 1)]}, 32)[0] is False
    assert _check({"tmvm": [BagItem(289, 1)], "beeren": [BagItem(133, 3)]}, 32)[0] is True


def test_echte_taschen_werden_akzeptiert():
    # Gen 3 Saphir: Baelle in Ball-Tasche, Trank, TM01, Amrenabeere, PC-Trank
    gen3 = {"items": [BagItem(13, 5)], "keyitems": [BagItem(259, 1)],
            "baelle": [BagItem(4, 10), BagItem(12, 1)], "tmvm": [BagItem(289, 1)],
            "beeren": [BagItem(133, 3)], "pc": [BagItem(13, 1), BagItem(4, 2)]}
    assert _check(gen3, 32) == (True, "")
    # Gen 1: alles in einer Tasche
    assert _check({"tasche": [BagItem(4, 5), BagItem(20, 3)], "pc": []}, 11)[0] is True
    # Gen 2: Aprikoko-Baelle in Ball-Tasche, TMVM-Array
    assert _check({"baelle": [BagItem(5, 5), BagItem(157, 1)],
                   "tmvm": [BagItem(191, 1)]}, 23)[0] is True
    # Gen 4 HGSS: Park-/Sport-Ball (keine Fang-Baelle) in Ball-Tasche erlaubt
    assert _check({"baelle": [BagItem(4, 5), BagItem(500, 1), BagItem(499, 1)]}, 44)[0] is True
    # Gen 4 DPPt: Mulch in Beeren-Tasche; Gen 4-6 TM-Slot mit qty 0 nicht verwerfen
    assert _check({"beeren": [BagItem(149, 2), BagItem(95, 1)]}, 41)[0] is True
    assert _check({"tmvm": [BagItem(328, 0)]}, 41)[0] is True
    # Gen 5-7: Baelle in Items-Tasche
    assert _check({"items": [BagItem(4, 5), BagItem(576, 1)]}, 51)[0] is True
    assert _check({"items": [BagItem(4, 5), BagItem(851, 1)]}, 73)[0] is True
    # Leerer Read ist plausibel (neues Spiel)
    assert _check({"items": [], "baelle": [], "pc": []}, 32) == (True, "")


def test_ball_pockets_for_edition():
    assert bd.ball_pockets_for_edition(32) == {"baelle", "pc"}
    assert bd.ball_pockets_for_edition(11) == {"tasche", "pc"}
    assert bd.ball_pockets_for_edition(41) == {"baelle"}
    assert bd.ball_pockets_for_edition(51) == {"items"}
    assert bd.ball_pockets_for_edition(None) == frozenset()


def test_party_has_valid_pokemon():
    empty = Pokemon(0, checksum_given=0, checksum_calculated=0)
    real = Pokemon(280, checksum_given=0x1234, checksum_calculated=0x1234)
    broken = Pokemon(280, checksum_given=0x1234, checksum_calculated=0x9999)
    # Genullte Gen-3-Party (Titelbild/Intro): Checksumme 0 == 0, aber dexnr 0
    assert party_has_valid_pokemon([empty] * 6 + [0, 32], 32) is False
    assert party_has_valid_pokemon([broken, empty], 32) is False
    assert party_has_valid_pokemon([empty, real], 32) is True
    # Gen 1/2 ohne Checksumme
    assert party_has_valid_pokemon([Pokemon(25)], 11) is True
    assert party_has_valid_pokemon([Pokemon("egg", checksum_given=1,
                                            checksum_calculated=1)], 41) is True
    assert party_has_valid_pokemon(None, 32) is False
