"""Pokemon-Editor: Level und Attacken eines Pokemon-Slots ändern (ohne Kivy).

Reine Funktionen nach dem Muster von ``bag_decoder.plan_bag_write``: aus den
rohen Slot-Bytes (frisch aus dem Emulator gelesen) wird der neue Slot-Inhalt
berechnet, den der Emulator-Adapter (bizhawk.py / citrahandler.py) zurück-
schreibt. Die Entschlüsselung im Decoder (``decryptpokemon3`` u.a.) bleibt
unangetastet; hier liegt nur die Gegenrichtung.

Was beim Zurückschreiben erfüllt sein muss (Gen 3):
- Substrukturen G/A/E/M ändern, Checksumme (u16-Summe der 48 Klartext-Bytes)
  nach 0x1C schreiben, Blöcke gemäß ``PID % 24`` zurückmischen, mit
  ``PID ^ OTID`` (u32) verschlüsseln.
- Party-Slots (100 B) tragen unverschlüsselt Level (0x54), aktuelle/max. KP
  (0x56/0x58) und Statuswerte (0x5A–0x63). Das Spiel rechnet sie nur beim
  Level-Up bzw. beim Ablegen/Holen neu — deshalb hier neu berechnen.
- Box-Slots (80 B) haben kein Level; es ergibt sich aus EXP + Wachstumskurve.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from backend import learnsets, pokedecoder
from backend.exp_curves import exp_at_level, growth_curve, infer_curve
from backend.logging_setup import get_logger
from backend.pokemon_detail_view import get_species_personal
from backend.stat_calc import calc_stats_gen3plus, scale_cur_hp

logger = get_logger(__name__, './logs/pokemon_editor.log')

# Muss exakt der Tabelle in pokedecoder.decryptpokemon/decryptpokemon3
# entsprechen: OFFSET_LUT[shift][i] = Index des verschlüsselten Blocks, der an
# Klartext-Position i landet.
OFFSET_LUT = [
    [0, 1, 2, 3], [0, 1, 3, 2], [0, 2, 1, 3], [0, 3, 1, 2], [0, 2, 3, 1], [0, 3, 2, 1],
    [1, 0, 2, 3], [1, 0, 3, 2], [2, 0, 1, 3], [3, 0, 1, 2], [2, 0, 3, 1], [3, 0, 2, 1],
    [1, 2, 0, 3], [1, 3, 0, 2], [2, 1, 0, 3], [3, 1, 0, 2], [2, 3, 0, 1], [3, 2, 0, 1],
    [1, 2, 3, 0], [1, 3, 2, 0], [2, 1, 3, 0], [3, 1, 2, 0], [2, 3, 1, 0], [3, 2, 1, 0],
]

GEN3_PARTY_SIZE = 100
GEN3_BOX_SIZE = 80


class EditError(Exception):
    """Edit nicht durchführbar; Message ist UI-tauglich (deutsch)."""


@dataclass
class PokemonEdit:
    """Gewünschte Änderung. ``moves``: genau 4 Attacken-IDs (0 = leer)."""
    new_level: int
    moves: list[int] = field(default_factory=list)


@dataclass
class EditPlan:
    new_bytes: bytes
    summary: str
    changed: bool


def shuffle_blocks(unshuffled: bytes, shift: int, blocksize: int) -> bytes:
    """Inverse zur Entmischung im Decoder: Klartext-Reihenfolge → Speicher-Reihenfolge."""
    blocks: list[bytes] = [b""] * 4
    for i in range(4):
        blocks[OFFSET_LUT[shift][i]] = unshuffled[i * blocksize:(i + 1) * blocksize]
    return b"".join(blocks)


def encrypt_gen3(raw: bytes, unshuffled: bytes) -> bytes:
    """Baut einen Gen-3-Slot (80/100 B) aus Klartext-Substrukturen (48 B) neu.

    Header (PID, OTID, Name, Sprache, OT-Name, Markierung) und ggf. der
    Party-Teil ab 0x50 werden aus ``raw`` übernommen.
    """
    if len(unshuffled) != 48:
        raise ValueError(f"Gen-3-Substrukturen müssen 48 Bytes haben, nicht {len(unshuffled)}")
    personality = int.from_bytes(raw[0:4], "little")
    otid = int.from_bytes(raw[4:8], "little")
    key = personality ^ otid
    checksum = pokedecoder.calculate_checksum(unshuffled)
    shuffled = shuffle_blocks(unshuffled, personality % 24, 12)
    encrypted = b"".join(
        (int.from_bytes(shuffled[i:i + 4], "little") ^ key).to_bytes(4, "little")
        for i in range(0, 48, 4)
    )
    return raw[:0x1C] + checksum.to_bytes(2, "little") + raw[0x1E:0x20] + encrypted + raw[0x50:]


def _base_stats(edition: int, dexnr: int, rando_data) -> dict:
    rando_pokemon = rando_data.pokemon.get(dexnr) if rando_data is not None else None
    if rando_pokemon is not None and rando_pokemon.stats:
        return rando_pokemon.stats
    personal = get_species_personal(edition).get(dexnr) or {}
    stats = personal.get("stats")
    if not stats:
        raise EditError(f"Keine Basiswerte für #{dexnr} gefunden.")
    return stats


def _normalize_moves(new_moves: list[int]) -> list[int]:
    """Lückenlos nach vorn schieben, auf 4 auffüllen, Duplikate/≥1 prüfen."""
    filled = [int(m) for m in new_moves if m]
    if not filled:
        raise EditError("Mindestens eine Attacke muss gesetzt sein.")
    if len(filled) > 4:
        raise EditError("Höchstens vier Attacken.")
    if len(set(filled)) != len(filled):
        raise EditError("Eine Attacke darf nicht doppelt vorkommen.")
    return filled + [0] * (4 - len(filled))


def _plan_moves(old_moves: list[int], old_pp: list[int], old_ppups: list[int],
                new_moves: list[int], edition: int, rando_data) -> tuple[list[int], list[int]]:
    """AP und AP-Plus pro Slot: unveränderte Attacken behalten ihre Werte
    (auch bei Slot-Wechsel), neue bekommen Basis-AP und AP-Plus 0."""
    pp: list[int] = []
    ppups: list[int] = []
    for move_id in new_moves:
        if move_id == 0:
            pp.append(0)
            ppups.append(0)
        elif move_id in old_moves:
            j = old_moves.index(move_id)
            pp.append(old_pp[j])
            ppups.append(old_ppups[j])
        else:
            pp.append(learnsets.base_pp(edition, move_id, rando_data))
            ppups.append(0)
    return pp, ppups


def plan_edit_gen3(raw: bytes, edition: int, is_boxed: bool, edit: PokemonEdit,
                   rando_data=None) -> EditPlan:
    expected_size = GEN3_BOX_SIZE if is_boxed else GEN3_PARTY_SIZE
    if len(raw) < expected_size:
        raise EditError(f"Slot zu kurz ({len(raw)} statt {expected_size} Bytes).")
    raw = bytes(raw[:expected_size])

    pokemon = pokedecoder.pokemon3(raw, edition, is_boxed)
    if pokemon is None:
        raise EditError("Slot ist leer.")
    if pokemon.checksum_given != pokemon.checksum_calculated:
        raise EditError("Checksumme des Slots stimmt nicht — Daten evtl. halb geschrieben, bitte erneut versuchen.")
    if pokemon.dexnr == "egg" or not isinstance(pokemon.dexnr, int):
        raise EditError("Eier können nicht bearbeitet werden.")
    if not 1 <= edit.new_level <= 100:
        raise EditError("Level muss zwischen 1 und 100 liegen.")

    dexnr = pokemon.dexnr
    unshuffled, _ = pokedecoder.decryptpokemon3(raw)
    u = bytearray(unshuffled)

    old_moves = [int.from_bytes(u[0x0C + 2 * i:0x0E + 2 * i], "little") for i in range(4)]
    old_pp = [u[0x14 + i] for i in range(4)]
    old_ppups = [(u[0x08] >> (2 * i)) & 0x03 for i in range(4)]
    new_moves = _normalize_moves(edit.moves) if edit.moves else old_moves

    old_exp = int.from_bytes(u[0x04:0x08], "little")
    old_level = pokemon.lvl
    level_changed = edit.new_level != old_level
    moves_changed = new_moves != old_moves
    if not level_changed and not moves_changed:
        return EditPlan(raw, "Keine Änderung.", False)

    notes: list[str] = []
    if level_changed:
        curve = growth_curve(dexnr) if is_boxed else infer_curve(dexnr, old_level, old_exp)
        new_exp = exp_at_level(curve, edit.new_level)
        u[0x04:0x08] = new_exp.to_bytes(4, "little")
        # Kein "→" im UI-Text: Roboto hat kein Pfeil-Glyph (Tofu in der Snackbar)
        notes.append(f"Level {old_level} auf {edit.new_level}")
        logger.info(f"Gen-3-Edit #{dexnr}: Level {old_level}→{edit.new_level}, EXP {old_exp}→{new_exp} ({curve})")

    if moves_changed:
        pp, ppups = _plan_moves(old_moves, old_pp, old_ppups, new_moves, edition, rando_data)
        ppup_byte = 0
        for i in range(4):
            u[0x0C + 2 * i:0x0E + 2 * i] = new_moves[i].to_bytes(2, "little")
            u[0x14 + i] = pp[i] & 0xFF
            ppup_byte |= (ppups[i] & 0x03) << (2 * i)
        u[0x08] = ppup_byte
        notes.append("Attacken geändert")
        logger.info(f"Gen-3-Edit #{dexnr}: Attacken {old_moves}→{new_moves}, AP {old_pp}→{pp}")

    new_raw = bytearray(encrypt_gen3(raw, bytes(u)))

    if not is_boxed and level_changed:
        base = _base_stats(edition, dexnr, rando_data)
        stats = calc_stats_gen3plus(base, pokemon.ivs, pokemon.evs, pokemon.nature, edit.new_level, dexnr)
        new_raw[0x54] = edit.new_level
        new_cur = scale_cur_hp(pokemon.cur_hp, pokemon.max_hp, stats["hp"])
        new_raw[0x56:0x58] = new_cur.to_bytes(2, "little")
        new_raw[0x58:0x5A] = stats["hp"].to_bytes(2, "little")
        for offset, key in ((0x5A, "attack"), (0x5C, "defense"), (0x5E, "speed"),
                            (0x60, "special_attack"), (0x62, "special_defense")):
            new_raw[offset:offset + 2] = stats[key].to_bytes(2, "little")
        logger.info(f"Gen-3-Edit #{dexnr}: Party-Werte neu {stats}, KP {pokemon.cur_hp}→{new_cur}")

    return EditPlan(bytes(new_raw), ", ".join(notes), True)


def slot_size(edition: int, is_boxed: bool) -> int:
    gen = learnsets.generation(edition)
    if gen == 3:
        return GEN3_BOX_SIZE if is_boxed else GEN3_PARTY_SIZE
    raise EditError(f"Pokemon-Editor unterstützt Gen {gen} noch nicht.")


def plan_edit(raw: bytes, edition: int, is_boxed: bool, edit: PokemonEdit, rando_data=None) -> EditPlan:
    """Einstieg pro Generation. Wirft ``EditError`` mit UI-tauglicher Meldung."""
    gen = learnsets.generation(edition)
    if gen == 3:
        return plan_edit_gen3(raw, edition, is_boxed, edit, rando_data)
    raise EditError(f"Pokemon-Editor unterstützt Gen {gen} noch nicht.")


def is_supported(edition: int, is_boxed: bool) -> bool:
    """Ob der Editor für diese Edition/Quelle freigeschaltet ist (UI-Button)."""
    try:
        gen = learnsets.generation(int(edition))
    except (TypeError, ValueError):
        return False
    return gen == 3
