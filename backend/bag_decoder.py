"""Decoder fuer Bag-Pockets (Gen 1-7).

Reine Funktionen — keine Emulator-Anbindung. Input ist immer das Roh-Byte-
Fenster einer einzelnen Pocket; Output eine Liste von BagItem.

Slot-Formate je Generation:
  Gen 1:    [count : u8][id : u8, qty : u8] * [0xFF terminator]
  Gen 2:    wie Gen 1, aber Sonderformate fuer KeyItems (1 B id-only) und
            TMVM (57-Byte qty-only Array, Index = TM/HM-Nummer).
  Gen 3 R/S: u16 LE id + u16 LE qty, fixed-size Pocket, leere Slots = 0.
  Gen 3 E/FR/BG: wie R/S, aber qty XOR mit security_key (16 Bit) verschluesselt.
  Gen 4-6:  u16 LE id + u16 LE qty, fixed-size Pocket, leere Slots = 0.
  Gen 7:    u32 LE bit-gepackt (10b id + 10b qty + 10b freespace + 2b flags).
            Wir lesen nur id + qty; freespace/flags ignorieren wir.

Item-Namen-Lookup (item_id -> Name) ist optional und wird vom Caller
durchgereicht, damit der Decoder generationsneutral bleibt.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BagItem:
    id: int
    qty: int
    name: str | None = None


def _name(item_id: int, item_lut: dict | None) -> str | None:
    if not item_lut:
        return None
    return item_lut.get(item_id)


# ---------------------------------------------------------------------------
# Gen 3-6: u16 id + u16 qty, fixed-size Pocket, Terminator = 0x00000000
# ---------------------------------------------------------------------------

def decode_pocket_idqty(raw: bytes, item_lut: dict | None = None,
                        max_slots: int | None = None) -> list[BagItem]:
    """Dekodiert eine Pocket im klassischen u16+u16-Format (Gen 3-6).

    Stoppt beim ersten leeren Slot (id == 0 und qty == 0). max_slots begrenzt
    optional die Anzahl gelesener Slots (Schutz gegen zu lange Buffer);
    in der Praxis terminiert der Null-Check vorher.
    """
    items: list[BagItem] = []
    n = len(raw) // 4
    if max_slots is not None:
        n = min(n, max_slots)
    for i in range(n):
        slot = raw[i * 4:i * 4 + 4]
        item_id = int.from_bytes(slot[0:2], "little")
        qty = int.from_bytes(slot[2:4], "little")
        if item_id == 0 and qty == 0:
            break
        items.append(BagItem(id=item_id, qty=qty, name=_name(item_id, item_lut)))
    return items


# ---------------------------------------------------------------------------
# Gen 3 E/FR/BG: u16 id + u16 qty, qty XOR security_key
# ---------------------------------------------------------------------------

def decode_pocket_idqty_xor(raw: bytes, security_key: int,
                            item_lut: dict | None = None,
                            max_slots: int | None = None) -> list[BagItem]:
    """Wie decode_pocket_idqty, aber qty wird mit security_key (16 Bit) XOR-decoded.

    security_key kommt aus SaveBlock2 und ist pro Spielstand konstant. In
    Smaragd/Feuerrot/Blattgruen sind die qty-Bytes der Bag-Pockets (ausser
    bag_pc) verschluesselt; ID-Bytes sind klar lesbar.
    """
    key = security_key & 0xFFFF
    items: list[BagItem] = []
    n = len(raw) // 4
    if max_slots is not None:
        n = min(n, max_slots)
    for i in range(n):
        slot = raw[i * 4:i * 4 + 4]
        item_id = int.from_bytes(slot[0:2], "little")
        qty_raw = int.from_bytes(slot[2:4], "little")
        qty = qty_raw ^ key
        # Leere Slots: id=0 und qty=0 (qty_raw=key wegen XOR). Der korrekte
        # Stop-Check arbeitet auf der entschluesselten qty.
        if item_id == 0 and qty == 0:
            break
        items.append(BagItem(id=item_id, qty=qty, name=_name(item_id, item_lut)))
    return items


# ---------------------------------------------------------------------------
# Gen 1: [count][id,qty] * [0xFF]
# ---------------------------------------------------------------------------

def decode_pocket_gen1(raw: bytes, item_lut: dict | None = None) -> list[BagItem]:
    """raw beginnt am count-Byte (= Slot-Anzahl).

    raw[0]   = Anzahl Items (n)
    raw[1..] = n * (u8 id, u8 qty)
    raw[1 + 2n] = 0xFF Terminator (zur Plausibilitaetspruefung)
    """
    if not raw:
        return []
    count = raw[0]
    items: list[BagItem] = []
    max_n = (len(raw) - 1) // 2  # robustness: nicht ueber den Buffer hinaus
    for i in range(min(count, max_n)):
        item_id = raw[1 + i * 2]
        qty = raw[2 + i * 2]
        if item_id == 0xFF:
            break
        items.append(BagItem(id=item_id, qty=qty, name=_name(item_id, item_lut)))
    return items


# ---------------------------------------------------------------------------
# Gen 2: 3 Pocket-Varianten
# ---------------------------------------------------------------------------

def decode_pocket_gen2_idqty(raw: bytes, item_lut: dict | None = None) -> list[BagItem]:
    """Wie Gen 1: [count][id,qty]*[0xFF]. Genutzt fuer Items, Baelle, PC."""
    return decode_pocket_gen1(raw, item_lut)


def decode_pocket_gen2_keyitems(raw: bytes, item_lut: dict | None = None) -> list[BagItem]:
    """Gen 2 KeyItems: [count][id]*[0xFF]. 1 B/Slot, kein qty (qty implizit 1)."""
    if not raw:
        return []
    count = raw[0]
    items: list[BagItem] = []
    max_n = len(raw) - 1
    for i in range(min(count, max_n)):
        item_id = raw[1 + i]
        if item_id == 0xFF:
            break
        items.append(BagItem(id=item_id, qty=1, name=_name(item_id, item_lut)))
    return items


# Gen 2 TMVM: 57-Byte qty-Array. Index 0..49 = TM01..TM50 (item-id 191..240),
# Index 50..56 = HM01..HM07 (item-id 243..249).
# 0 = nicht besessen.
GEN2_TMVM_ID_TM_BASE = 191
GEN2_TMVM_ID_HM_BASE = 243
GEN2_TMVM_TM_COUNT = 50
GEN2_TMVM_HM_COUNT = 7
GEN2_TMVM_TOTAL = GEN2_TMVM_TM_COUNT + GEN2_TMVM_HM_COUNT  # 57


def decode_pocket_gen2_tmvm(raw: bytes, item_lut: dict | None = None) -> list[BagItem]:
    """Gen 2 TMVM: qty-Array fester Groesse. Liefert nur besetzte Slots (qty > 0)."""
    items: list[BagItem] = []
    n = min(len(raw), GEN2_TMVM_TOTAL)
    for i in range(n):
        qty = raw[i]
        if qty == 0:
            continue
        if i < GEN2_TMVM_TM_COUNT:
            item_id = GEN2_TMVM_ID_TM_BASE + i
        else:
            item_id = GEN2_TMVM_ID_HM_BASE + (i - GEN2_TMVM_TM_COUNT)
        items.append(BagItem(id=item_id, qty=qty, name=_name(item_id, item_lut)))
    return items


# ---------------------------------------------------------------------------
# Gen 7: u32 LE bit-gepackt (10b id + 10b qty + 10b freespace + 2b flags)
# ---------------------------------------------------------------------------

def decode_pocket_packed10(raw: bytes, item_lut: dict | None = None,
                           max_slots: int | None = None) -> list[BagItem]:
    """Dekodiert eine Pocket im Gen 7 packed10-Format.

    Pro Slot: u32 LE. Bits 0..9 = item id, Bits 10..19 = qty,
    Bits 20..29 = freespace-Index (vom Spieler reordbar), Bit 30 = NewFlag,
    Bit 31 = reserviert. Wir extrahieren nur id und qty.

    Stoppt beim ersten leeren Slot (id == 0).
    """
    items: list[BagItem] = []
    n = len(raw) // 4
    if max_slots is not None:
        n = min(n, max_slots)
    for i in range(n):
        word = int.from_bytes(raw[i * 4:i * 4 + 4], "little")
        item_id = word & 0x3FF
        qty = (word >> 10) & 0x3FF
        if item_id == 0:
            break
        items.append(BagItem(id=item_id, qty=qty, name=_name(item_id, item_lut)))
    return items


# ---------------------------------------------------------------------------
# Top-Level Dispatcher
# ---------------------------------------------------------------------------

# Pocket-Kapazitaeten (max Slots) je Edition + Pocket-Name. Werte sind
# entweder dokumentiert (PKHeX) oder konservativ aus dem Pocket-Abstand
# (z.B. 0x4D8 / 4 = 310 fuer Gen 5 Items).
# Ueberlesen ist unkritisch — der Decoder stoppt am ersten Null-Slot.
POCKET_MAX_SLOTS: dict[int, dict[str, int]] = {
    # Gen 1 R/B/Y — Werte aus pointer_gen1.yml-Kommentar.
    # tasche: count(1) + 20*(id+qty) + term(1) = 42 B Buffer.
    # pc:     count(1) + 50*(id+qty) + term(1) = 102 B Buffer.
    11: {"tasche": 20, "pc": 50},
    12: {"tasche": 20, "pc": 50},
    13: {"tasche": 20, "pc": 50},
    # Gen 2 G/S/C — Werte aus pointer_gen2.yml-Kommentar.
    # items/baelle/pc: 2 B/Slot mit count+term, keyitems: 1 B/Slot mit count+term,
    # tmvm: 57 B fixes Array (TM01..TM50 + HM01..HM07), kein count/terminator.
    21: {"items": 20, "keyitems": 25, "baelle": 12, "pc": 50, "tmvm": 57},
    22: {"items": 20, "keyitems": 25, "baelle": 12, "pc": 50, "tmvm": 57},
    23: {"items": 20, "keyitems": 25, "baelle": 12, "pc": 50, "tmvm": 57},
    # Gen 3 R/S/E/FR/BG — Werte aus pointer_gen3.yml-Kommentar.
    # Slot = u16 id + u16 qty. R/S statisch, E/FR/BG XOR-encrypted (qty XOR security_key).
    # PC ist in allen Editionen 50 Slots und nicht XOR-verschluesselt.
    31: {"items": 20, "keyitems": 20, "baelle": 16, "tmvm": 64, "beeren": 46, "pc": 50},
    32: {"items": 20, "keyitems": 20, "baelle": 16, "tmvm": 64, "beeren": 46, "pc": 50},
    33: {"items": 30, "keyitems": 30, "baelle": 16, "tmvm": 64, "beeren": 46, "pc": 50},
    34: {"items": 42, "keyitems": 30, "baelle": 13, "tmvm": 58, "beeren": 43, "pc": 50},
    35: {"items": 42, "keyitems": 30, "baelle": 13, "tmvm": 58, "beeren": 43, "pc": 50},
    # Gen 4 DPPt / HG-SS — Pocket-Spacings aus pointer_gen4.yml.
    41: {"items": 165, "keyitems": 50, "tmvm": 100, "mailitems": 12,
         "medizin": 40, "beeren": 64, "baelle": 15, "kampfitems": 30},
    42: {"items": 165, "keyitems": 50, "tmvm": 100, "mailitems": 12,
         "medizin": 40, "beeren": 64, "baelle": 15, "kampfitems": 30},
    43: {"items": 165, "keyitems": 50, "tmvm": 100, "mailitems": 12,
         "medizin": 40, "beeren": 64, "baelle": 15, "kampfitems": 30},
    44: {"items": 165, "keyitems": 50, "tmvm": 100, "mailitems": 12,
         "medizin": 40, "beeren": 64, "baelle": 15, "kampfitems": 30},
    45: {"items": 165, "keyitems": 50, "tmvm": 100, "mailitems": 12,
         "medizin": 40, "beeren": 64, "baelle": 15, "kampfitems": 30},
    # Gen 5 BW / B2W2 — Spacing Items->KeyItems = 0x4D8 -> 310
    51: {"items": 310, "keyitems": 83, "tmvm": 109, "medizin": 48, "beeren": 64},
    52: {"items": 310, "keyitems": 83, "tmvm": 109, "medizin": 48, "beeren": 64},
    53: {"items": 310, "keyitems": 83, "tmvm": 109, "medizin": 48, "beeren": 64},
    54: {"items": 310, "keyitems": 83, "tmvm": 109, "medizin": 48, "beeren": 64},
    # Gen 6 XY / ORAS — Spacings aus pointer_xy.yml / pointer_oras.yml.
    61: {"items": 400, "keyitems": 96, "tmvm": 106, "medizin": 64, "beeren": 64},
    62: {"items": 400, "keyitems": 96, "tmvm": 106, "medizin": 64, "beeren": 64},
    63: {"items": 400, "keyitems": 96, "tmvm": 108, "medizin": 64, "beeren": 64},
    64: {"items": 400, "keyitems": 96, "tmvm": 108, "medizin": 64, "beeren": 64},
    # Gen 7 SM / USUM — Spacings aus pointer_sm.yml / pointer_usum.yml.
    # Beeren->Z-Kristalle ist edition-abhaengig (S/M=72, US/UM=67).
    71: {"items": 430, "keyitems": 184, "tmvm": 108, "medizin": 64, "beeren": 72,
         "zkristalle": 50},
    72: {"items": 430, "keyitems": 184, "tmvm": 108, "medizin": 64, "beeren": 72,
         "zkristalle": 50},
    73: {"items": 427, "keyitems": 198, "tmvm": 108, "medizin": 60, "beeren": 67,
         "zkristalle": 35, "kampfitems": 30},
    74: {"items": 427, "keyitems": 198, "tmvm": 108, "medizin": 60, "beeren": 67,
         "zkristalle": 35, "kampfitems": 30},
}


def edition_to_gen(edition: int) -> int:
    return edition // 10


# Pocket-Keys, die im Pointer-YAML als "bag_<key>" auftauchen koennen.
# Reihenfolge ist beliebig; der Reader iteriert und ueberspringt fehlende Keys.
KNOWN_POCKET_KEYS = (
    "tasche",      # Gen 1 (eine kombinierte Tasche)
    "items",
    "keyitems",
    "tmvm",
    "medizin",
    "beeren",
    "baelle",
    "kampfitems",
    "mailitems",
    "zkristalle",
    "pc",          # Gen 1-3
)

# Gen 3 E/FR/BG (33-35): qty XOR-encrypted. PC ist nicht verschluesselt.
GEN3_XOR_POCKETS = frozenset({"items", "keyitems", "baelle", "tmvm", "beeren"})


def pocket_window_size(edition: int, pocket_key: str) -> int:
    """Wie viele Bytes muss der Reader fuer diese Pocket lesen?

    Gen 1+2 mit count-Prefix: count(1) + max_slots*slot_size + terminator(1).
    Gen 2 TMVM: 57 B Fix-Array (kein Prefix/Terminator).
    Gen 3+: max_slots * 4 B (u16+u16-Slots ohne Prefix).

    Bei Gen 1+2 muss der Reader die Pocket-Adresse aus dem Pointer-YAML um
    -1 nach unten verschieben, damit das count-Byte mit eingelesen wird —
    siehe pocket_read_offset().
    """
    gen = edition_to_gen(edition)
    caps = POCKET_MAX_SLOTS.get(edition, {})
    max_slots = caps.get(pocket_key, 64)
    if gen == 1:
        return 1 + max_slots * 2 + 1
    if gen == 2:
        if pocket_key == "tmvm":
            return 57
        slot_size = 1 if pocket_key == "keyitems" else 2
        return 1 + max_slots * slot_size + 1
    return max_slots * 4


def pocket_read_offset(edition: int, pocket_key: str) -> int:
    """Verschiebung relativ zur YAML-Adresse, wo der Read beginnen muss.

    Pointer-YAMLs zeigen bei Gen 1/2 (ausser Gen 2 TMVM) auf den ersten Slot —
    das count-Byte liegt bei -1. Gen 3+ und Gen 2 TMVM beginnen direkt bei
    der YAML-Adresse, daher Offset = 0.
    """
    gen = edition_to_gen(edition)
    if gen == 1:
        return -1
    if gen == 2 and pocket_key != "tmvm":
        return -1
    return 0


def pocket_window(yaml_addr: int, edition: int, pocket_key: str) -> tuple[int, int]:
    """(start_addr, size) — bequeme Kombination der zwei Helpers oben.

    Wird von Reader-Code (test_bag_decoder.py, bizhawk.py) genutzt, damit die
    Start-Offset-Logik nur an einer Stelle gepflegt wird.
    """
    return (yaml_addr + pocket_read_offset(edition, pocket_key),
            pocket_window_size(edition, pocket_key))


def decode_pocket(edition: int, pocket_key: str, raw: bytes,
                  item_lut: dict | None = None,
                  security_key: int | None = None) -> list[BagItem]:
    """Top-Level-Dispatcher. Waehlt Decoder + Stop-Bedingung anhand
    Generation und Pocket-Typ.

    Bei Gen 3 E/FR/BG (Editionen 33-35) brauchen alle Pockets ausser pc
    einen security_key — ohne Key wirft der Aufruf eine ValueError.
    """
    gen = edition_to_gen(edition)
    max_slots = POCKET_MAX_SLOTS.get(edition, {}).get(pocket_key)
    if gen == 1:
        return decode_pocket_gen1(raw, item_lut)
    if gen == 2:
        if pocket_key == "tmvm":
            return decode_pocket_gen2_tmvm(raw, item_lut)
        if pocket_key == "keyitems":
            return decode_pocket_gen2_keyitems(raw, item_lut)
        return decode_pocket_gen2_idqty(raw, item_lut)
    if gen == 3 and edition >= 33 and pocket_key in GEN3_XOR_POCKETS:
        if security_key is None:
            raise ValueError(
                f"security_key fehlt fuer Gen 3 edition={edition} pocket={pocket_key}"
            )
        return decode_pocket_idqty_xor(raw, security_key, item_lut, max_slots)
    if gen in (3, 4, 5, 6):
        return decode_pocket_idqty(raw, item_lut, max_slots)
    if gen == 7:
        return decode_pocket_packed10(raw, item_lut, max_slots)
    raise NotImplementedError(f"Gen {gen} (edition={edition}) nicht unterstuetzt")


# ---------------------------------------------------------------------------
# Sonderbonbon-Schreibsupport
# ---------------------------------------------------------------------------

# Slug ist in allen items*.yml-Dateien einheitlich. Die ID variiert pro Gen.
RARE_CANDY_SLUG = "rare-candy"

# Item-IDs aus den items*.yml verifiziert. Schnelle Lookup-Konstante, damit
# der Write-Pfad nicht jedes Mal die YAMLs laden muss.
RARE_CANDY_ID_PER_GEN: dict[int, int] = {
    1: 40, 2: 32, 3: 68, 4: 50, 5: 50, 6: 50, 7: 50,
}

# Pocket, in dem das Spiel das Sonderbonbon ablegt — Quelle: Pokewiki.
# Achtung: ab Gen 5 ist das die *Medizin*-Tasche, nicht mehr Items.
RARE_CANDY_POCKET: dict[int, str] = {
    1: "tasche",
    2: "items",
    3: "items",
    4: "items",
    5: "medizin",
}


def find_rare_candy_id(edition: int, item_lut: dict | None = None) -> int | None:
    """Liefert die Sonderbonbon-ID fuer die Edition.

    Bevorzugt die fixe Gen-Konstante (RARE_CANDY_ID_PER_GEN, aus den
    items*.yml verifiziert). Faellt nur dann auf einen Slug-Lookup im
    optional uebergebenen item_lut zurueck, wenn die Konstante fehlt — z.B.
    bei zukuenftigen Editionen, die noch nicht im Mapping eingetragen sind.
    """
    gen = edition_to_gen(edition)
    fixed = RARE_CANDY_ID_PER_GEN.get(gen)
    if fixed is not None:
        return fixed
    if not item_lut:
        return None
    for item_id, slug in item_lut.items():
        if slug == RARE_CANDY_SLUG:
            return int(item_id)
    return None


def rare_candy_pocket_for_edition(edition: int) -> str | None:
    return RARE_CANDY_POCKET.get(edition_to_gen(edition))


# Spiel-Caps fuer item-quantity. Gen 1/2: 1 Byte qty (max 99). Gen 3+: u16,
# Spiel-cap ist 999. Storage erlaubt formal bis 0xFFFF, aber 999 ist die
# Grenze, die das Spielmenue anzeigt — daruebermitschreiben fuehrt zu
# Anzeige-Glitches.
QTY_CAP_PER_GEN = {1: 99, 2: 99, 3: 999, 4: 999, 5: 999, 6: 999, 7: 999}


@dataclass(frozen=True)
class BagWritePlan:
    """Was und wo geschrieben werden muss, um delta Items hinzuzufuegen.

    writes: Tuple aus (offset_relativ_zum_pocket_window, bytes_to_write).
            Mehrere Writes z.B. bei Gen 1/2 fuer neuen Slot (count-Byte +
            id/qty/Terminator).
    new_qty: Anzahl im Slot NACH dem Schreiben (vor dem Schreiben + delta,
             auf cap geclampt).
    status:  'ok' | 'full' | 'noop'.
    """
    writes: tuple[tuple[int, bytes], ...]
    new_qty: int
    status: str


def _gen12_find_or_alloc(pocket_bytes: bytes, item_id: int, max_slots: int):
    """Liefert (slot_idx, current_qty, is_new_slot, count_after) oder None.

    Format: raw[0] = count, danach count*(id, qty), dann 0xFF Terminator.
    Existierender Slot -> qty wird upgedated; sonst neuer Slot am Ende, count
    erhoeht. None heisst: Tasche voll.
    """
    if not pocket_bytes:
        return None
    count = pocket_bytes[0]
    for i in range(min(count, max_slots)):
        sid = pocket_bytes[1 + i * 2]
        if sid == 0xFF:
            break
        if sid == item_id:
            return (i, pocket_bytes[2 + i * 2], False, count)
    if count >= max_slots:
        return None
    return (count, 0, True, count + 1)


def _gen3plus_find_or_alloc(pocket_bytes: bytes, item_id: int, max_slots: int,
                            security_key: int | None, encrypted: bool):
    """Liefert (slot_idx, current_qty, is_new_slot) oder None.

    Format: u16 LE id + u16 LE qty pro Slot. Bei encrypted ist qty XOR
    security_key gespeichert. Erster Slot mit (id=0, qty=0) ist die
    Allokations-Position fuer neue Items; existierende Slots werden bevorzugt
    geupdatet.
    """
    key = (security_key & 0xFFFF) if (encrypted and security_key is not None) else 0
    n = min(len(pocket_bytes) // 4, max_slots)
    first_empty = None
    for i in range(n):
        sid = int.from_bytes(pocket_bytes[i * 4:i * 4 + 2], "little")
        qty_raw = int.from_bytes(pocket_bytes[i * 4 + 2:i * 4 + 4], "little")
        qty = qty_raw ^ key if encrypted else qty_raw
        # Im Spiel sind leere Slots bei XOR-Pockets als id=0, qty_raw=key
        # gespeichert (decoded qty=0). Bei frisch null-initialisiertem Speicher
        # waeren es 0,0 — auch das als leer erkennen, sonst wuerden wir das
        # Pocket faelschlich als "voll" melden.
        if sid == 0 and (qty == 0 or (encrypted and qty_raw == 0)):
            if first_empty is None:
                first_empty = i
            continue
        if sid == item_id:
            return (i, qty, False)
    if first_empty is None:
        return None
    return (first_empty, 0, True)


def plan_bag_write(edition: int, pocket_key: str, pocket_bytes: bytes,
                   item_id: int, delta: int,
                   *, security_key: int | None = None) -> BagWritePlan:
    """Plant einen Bag-Schreibvorgang fuer (item_id, delta) in der angegebenen
    Pocket einer Edition.

    pocket_bytes muss exakt das von pocket_window() definierte Lese-Fenster
    sein (inkl. count-Byte bei Gen 1/2). Offsets in plan.writes sind relativ
    zum Anfang dieses Fensters — der Caller addiert die Pocket-Start-Adresse
    drauf.
    """
    if delta <= 0:
        return BagWritePlan(writes=(), new_qty=0, status="noop")

    gen = edition_to_gen(edition)
    cap = QTY_CAP_PER_GEN.get(gen, 999)
    max_slots = POCKET_MAX_SLOTS.get(edition, {}).get(pocket_key, 64)

    if gen in (1, 2):
        result = _gen12_find_or_alloc(pocket_bytes, item_id, max_slots)
        if result is None:
            return BagWritePlan(writes=(), new_qty=0, status="full")
        slot_idx, cur_qty, is_new, count_after = result
        new_qty = min(cur_qty + delta, cap)
        if not is_new:
            offset = 1 + slot_idx * 2 + 1  # qty-Byte hinter id
            return BagWritePlan(writes=((offset, bytes([new_qty])),),
                                new_qty=new_qty, status="ok")
        # Neuer Slot: count-Byte + (id, qty, neuer Terminator) als zwei Writes.
        slot_offset = 1 + slot_idx * 2
        slot_bytes = bytes([item_id, new_qty, 0xFF])
        return BagWritePlan(
            writes=((0, bytes([count_after])), (slot_offset, slot_bytes)),
            new_qty=new_qty, status="ok",
        )

    if gen in (3, 4, 5, 6):
        encrypted = (gen == 3 and edition >= 33 and pocket_key in GEN3_XOR_POCKETS)
        if encrypted and security_key is None:
            raise ValueError(
                f"security_key fehlt fuer Gen 3 edition={edition} pocket={pocket_key}"
            )
        result = _gen3plus_find_or_alloc(pocket_bytes, item_id, max_slots,
                                         security_key, encrypted)
        if result is None:
            return BagWritePlan(writes=(), new_qty=0, status="full")
        slot_idx, cur_qty, is_new = result
        new_qty = min(cur_qty + delta, cap)
        key = (security_key & 0xFFFF) if encrypted else 0
        qty_bytes = (new_qty ^ key).to_bytes(2, "little")
        if not is_new:
            offset = slot_idx * 4 + 2
            return BagWritePlan(writes=((offset, qty_bytes),),
                                new_qty=new_qty, status="ok")
        slot_bytes = item_id.to_bytes(2, "little") + qty_bytes
        return BagWritePlan(writes=((slot_idx * 4, slot_bytes),),
                            new_qty=new_qty, status="ok")

    raise NotImplementedError(f"Bag-Write fuer Gen {gen} nicht implementiert")


def derive_security_key_from_tmvm(raw_tmvm: bytes) -> int | None:
    """Liefert den 16-Bit Security-Key aus dem ersten leeren TMVM-Slot.

    Bei XOR-encrypted Pockets gilt: id == 0, qty_raw = 0 ^ key = key.
    TMVM ist gross genug (58-64 Slots), um zuverlaessig leere Slots zu
    enthalten. None nur, wenn die Pocket komplett gefuellt ist — dann muss
    der Aufrufer den Key anders bestimmen.
    """
    n = len(raw_tmvm) // 4
    for i in range(n):
        slot = raw_tmvm[i * 4 : i * 4 + 4]
        item_id = int.from_bytes(slot[0:2], "little")
        if item_id == 0:
            return int.from_bytes(slot[2:4], "little")
    return None
