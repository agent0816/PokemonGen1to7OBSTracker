"""Tests fuer backend/pokemon_encoder.py (Gen-3-Pokemon-Editor)."""
from __future__ import annotations

import random
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend import pokedecoder  # noqa: E402
from backend.exp_curves import exp_at_level  # noqa: E402
from backend.pokemon_encoder import (  # noqa: E402
    EditError, PokemonEdit, encrypt_gen3, plan_edit, shuffle_blocks,
)
from backend.stat_calc import calc_stats_gen3plus  # noqa: E402

FEUERROT = 34


def _gen3_plain(species_internal: int, exp: int, moves: list[int], pp: list[int],
                ppups: int = 0, evs=(0,) * 6, iv_word: int = 0) -> bytes:
    u = bytearray(48)
    u[0:2] = species_internal.to_bytes(2, "little")
    u[4:8] = exp.to_bytes(4, "little")
    u[8] = ppups
    u[9] = 70  # Freundschaft
    for i, m in enumerate(moves):
        u[0x0C + 2 * i:0x0E + 2 * i] = m.to_bytes(2, "little")
        u[0x14 + i] = pp[i]
    u[0x18:0x1E] = bytes(evs)
    u[0x25] = 101  # Fangort
    u[0x28:0x2C] = iv_word.to_bytes(4, "little")
    return bytes(u)


def _gen3_slot(pid: int, otid: int, plain: bytes, party: dict | None = None) -> bytes:
    header = bytearray(0x20)
    header[0:4] = pid.to_bytes(4, "little")
    header[4:8] = otid.to_bytes(4, "little")
    header[8:18] = bytes([0xBB, 0xBC, 0xFF] + [0xFF] * 7)
    header[0x12] = 0x02
    header[0x13] = 0x02  # hasSpecies
    header[0x14:0x1B] = bytes([0xCC, 0xFF, 0, 0, 0, 0, 0])
    raw = bytes(header) + bytes(48)
    if party is not None:
        tail = bytearray(20)
        tail[4] = party["level"]
        tail[6:8] = party["cur_hp"].to_bytes(2, "little")
        tail[8:10] = party["max_hp"].to_bytes(2, "little")
        raw += bytes(tail)
    return encrypt_gen3(raw, plain)


def test_shuffle_ist_inverse_zum_decoder_alle_24_varianten() -> None:
    rng = random.Random(42)
    for shift in range(24):
        pid = shift + 24 * rng.randrange(1, 10_000_000)
        assert pid % 24 == shift
        otid = rng.getrandbits(32)
        plain = bytes(rng.getrandbits(8) for _ in range(48))
        slot = _gen3_slot(pid, otid, plain)
        unshuffled, _ = pokedecoder.decryptpokemon3(slot)
        assert unshuffled == plain
        assert int.from_bytes(slot[0x1C:0x1E], "little") == pokedecoder.calculate_checksum(plain)


def test_shuffle_blocks_identitaet() -> None:
    data = bytes(range(48))
    assert shuffle_blocks(data, 0, 12) == data


# Bisasam (#1, intern 1, medium_slow), IVs alle 31 -> iv_word mit 6x31
IV31 = sum(31 << (5 * i) for i in range(6))


def _bisasam_party(level: int = 10) -> bytes:
    exp = exp_at_level("medium_slow", level) + 5
    plain = _gen3_plain(1, exp, [33, 45, 0, 0], [35, 40, 0, 0], ppups=0b0100, iv_word=IV31,
                        evs=(4, 8, 0, 0, 0, 0))
    return _gen3_slot(0x12345678, 0xABCD1234, plain, party={"level": level, "cur_hp": 20, "max_hp": 30})


def test_decoder_liest_gen3_ev_iv_korrekt() -> None:
    p = pokedecoder.pokemon3(_bisasam_party(), FEUERROT)
    assert p.dexnr == 1 and p.lvl == 10
    assert p.evs["hp"] == 4 and p.evs["attack"] == 8
    assert all(v == 31 for v in p.ivs.values())
    assert p.checksum_given == p.checksum_calculated


def test_level_hoch_party_stats_und_exp() -> None:
    raw = _bisasam_party(10)
    plan = plan_edit(raw, FEUERROT, False, PokemonEdit(new_level=20, moves=[33, 45, 0, 0]))
    assert plan.changed
    p = pokedecoder.pokemon3(plan.new_bytes, FEUERROT)
    assert p.checksum_given == p.checksum_calculated
    assert p.lvl == 20
    assert p.experience_points == exp_at_level("medium_slow", 20)
    base = {"hp": 45, "attack": 49, "defense": 49, "special_attack": 65,
            "special_defense": 65, "speed": 45}
    expected = calc_stats_gen3plus(base, p.ivs, p.evs, p.nature, 20)
    assert p.max_hp == expected["hp"]
    assert p.cur_hp == expected["hp"] - 10  # 10 Schaden bleibt
    assert p.battle_stats["attack"] == expected["attack"]
    assert p.battle_stats["special_defense"] == expected["special_defense"]
    # Header unverändert
    assert plan.new_bytes[:0x1C] == raw[:0x1C]


def test_level_runter_box() -> None:
    plain = _gen3_plain(1, exp_at_level("medium_slow", 30), [33, 0, 0, 0], [35, 0, 0, 0])
    raw = _gen3_slot(0x0BADF00D, 0x11112222, plain)
    assert pokedecoder.pokemon3(raw, FEUERROT, is_boxed=True).lvl == 30
    plan = plan_edit(raw, FEUERROT, True, PokemonEdit(new_level=12, moves=[33, 0, 0, 0]))
    assert len(plan.new_bytes) == 80
    p = pokedecoder.pokemon3(plan.new_bytes, FEUERROT, is_boxed=True)
    assert p.lvl == 12 and p.checksum_given == p.checksum_calculated


def test_attacken_tausch_pp_und_ppups() -> None:
    raw = _bisasam_party(10)
    # Heuler (45) wandert auf Slot 0 und behält AP 40 + AP-Plus 1; Egelsamen (73) neu
    plan = plan_edit(raw, FEUERROT, False, PokemonEdit(new_level=10, moves=[45, 0, 73, 0]))
    p = pokedecoder.pokemon3(plan.new_bytes, FEUERROT)
    assert [m["id"] for m in p.moves] == [45, 73, 0, 0]
    assert [m["pp"] for m in p.moves] == [40, 10, 0, 0]
    u, _ = pokedecoder.decryptpokemon3(plan.new_bytes)
    assert u[8] == 0b01  # AP-Plus von Heuler nun in Slot 0
    assert p.lvl == 10 and p.experience_points == exp_at_level("medium_slow", 10) + 5
    assert plan.new_bytes[0x50:] == raw[0x50:]  # Party-Teil unverändert ohne Level-Änderung


def test_keine_aenderung() -> None:
    raw = _bisasam_party(10)
    plan = plan_edit(raw, FEUERROT, False, PokemonEdit(new_level=10, moves=[33, 45, 0, 0]))
    assert not plan.changed and plan.new_bytes == raw


@pytest.mark.parametrize("moves", [[0, 0, 0, 0], [33, 33, 0, 0]])
def test_ungueltige_attacken(moves) -> None:
    with pytest.raises(EditError):
        plan_edit(_bisasam_party(), FEUERROT, False, PokemonEdit(new_level=10, moves=moves))


def test_kaputte_checksumme_abgelehnt() -> None:
    raw = bytearray(_bisasam_party())
    raw[0x30] ^= 0xFF
    with pytest.raises(EditError):
        plan_edit(bytes(raw), FEUERROT, False, PokemonEdit(new_level=11))


def test_gen4_noch_nicht_unterstuetzt() -> None:
    with pytest.raises(EditError):
        plan_edit(bytes(236), 41, False, PokemonEdit(new_level=5))


# ----------------------------------------------------------------------------
# bizhawk.edit_pokemon mit simuliertem Lua (Queue-Abarbeitung auf Fake-Speicher)
# ----------------------------------------------------------------------------

import asyncio  # noqa: E402

from backend.classes.bizhawk import Bizhawk  # noqa: E402


class _FakeLua:
    """Arbeitet box_request_queues wie der Tick-Loop + obsautomation.lua ab."""

    def __init__(self, bh: Bizhawk, cid: str, memory: dict[int, int], mutate_once=None):
        self.bh, self.cid, self.mem = bh, cid, memory
        self.mutate_once = mutate_once
        self.writes = 0

    def _read(self, addr: int, n: int) -> bytes:
        return bytes(self.mem.get(addr + i, 0) for i in range(n))

    async def run(self):
        while True:
            queue = self.bh.box_request_queues.get(self.cid) or []
            if not queue:
                await asyncio.sleep(0)
                continue
            cmd, addr, size, fut, payload = queue.pop(0)
            if cmd == "boxw":
                fut.set_result(self._read(addr, size))
            elif cmd == "pkw":
                if self.mutate_once:
                    self.mutate_once(self.mem)
                    self.mutate_once = None
                ptr_hex, expect_hex, data_hex = payload.split(" ")
                ptr = int(ptr_hex, 16)
                if ptr:
                    addr = int.from_bytes(self._read(ptr, 4), "little") + addr
                expect = bytes.fromhex(expect_hex)
                if self._read(addr, len(expect)) != expect:
                    fut.set_result(0x02)
                    continue
                for i, b in enumerate(bytes.fromhex(data_hex)):
                    self.mem[addr + i] = b
                self.writes += 1
                fut.set_result(0x01)


def _bh_stub(edition: int) -> Bizhawk:
    bh = Bizhawk.__new__(Bizhawk)
    import logging
    bh.logger = logging.getLogger("test_bizhawk_edit")
    bh.edition_per_client = {"player001": edition}
    bh.language_per_client = {"player001": 0}
    bh.box_request_queues = {}
    bh._poke_io_locks = {}
    bh._in_battle = {}
    bh._edit_tasks = set()
    return bh


def _run_edit(bh, lua, target, edit):
    async def _go():
        task = asyncio.create_task(lua.run())
        try:
            return await bh.edit_pokemon("player001", target, edit)
        finally:
            task.cancel()
    return asyncio.run(_go())


def _load(memory: dict[int, int], addr: int, data: bytes) -> None:
    for i, b in enumerate(data):
        memory[addr + i] = b


def test_edit_pokemon_party_feuerrot() -> None:
    party_ptr = 0x02024284  # pointer_gen3.yml, Edition 34
    raw = _bisasam_party(10)
    mem: dict[int, int] = {}
    _load(mem, party_ptr + 100, raw)  # Slot 1
    bh = _bh_stub(FEUERROT)
    lua = _FakeLua(bh, "player001", mem)
    ok, msg = _run_edit(bh, lua, {"kind": "party", "slot": 1, "personality": 0x12345678},
                        PokemonEdit(new_level=25, moves=[33, 45, 0, 0]))
    assert ok, msg
    p = pokedecoder.pokemon3(lua._read(party_ptr + 100, 100), FEUERROT)
    assert p.lvl == 25 and p.checksum_given == p.checksum_calculated


def test_edit_pokemon_box_indirekt_mit_retry() -> None:
    ptr_addr, storage = 0x03004F60, 0x02029314
    plain = _gen3_plain(1, exp_at_level("medium_slow", 30), [33, 0, 0, 0], [35, 0, 0, 0])
    raw = _gen3_slot(0x0BADF00D, 0x11112222, plain)
    slot_addr = storage + 4 + 2 * 0x960 + 5 * 80  # Box 3, Slot 6
    mem: dict[int, int] = {}
    _load(mem, ptr_addr, storage.to_bytes(4, "little"))
    _load(mem, slot_addr, raw)
    bh = _bh_stub(FEUERROT)
    bh._auto_refresh_boxes = lambda cid, player: asyncio.sleep(0)

    def _spiel_aendert_slot(m):  # z.B. Spiel schreibt Markierung zwischen Read und Write
        m[slot_addr + 0x1B] = 0x01

    lua = _FakeLua(bh, "player001", mem, mutate_once=_spiel_aendert_slot)
    ok, msg = _run_edit(bh, lua, {"kind": "box", "box": 2, "slot": 5, "personality": 0x0BADF00D},
                        PokemonEdit(new_level=40, moves=[33, 0, 0, 0]))
    assert ok, msg
    assert lua.writes == 1
    p = pokedecoder.pokemon3(lua._read(slot_addr, 80), FEUERROT, is_boxed=True)
    assert p.lvl == 40 and p.checksum_given == p.checksum_calculated
    assert mem[slot_addr + 0x1B] == 0x01  # Änderung des Spiels nicht überschrieben


def test_edit_pokemon_abgelehnt_im_kampf_und_falsche_pid() -> None:
    bh = _bh_stub(FEUERROT)
    bh._in_battle["player001"] = True
    lua = _FakeLua(bh, "player001", {})
    ok, msg = _run_edit(bh, lua, {"kind": "party", "slot": 0, "personality": 1}, PokemonEdit(new_level=5))
    assert not ok and "Kampf" in msg

    bh = _bh_stub(FEUERROT)
    mem: dict[int, int] = {}
    _load(mem, 0x02024284, _bisasam_party(10))
    lua = _FakeLua(bh, "player001", mem)
    ok, msg = _run_edit(bh, lua, {"kind": "party", "slot": 0, "personality": 999}, PokemonEdit(new_level=5))
    assert not ok and lua.writes == 0


def test_box_level_enrichment_nur_bei_gleicher_exp(tmp_path) -> None:
    """DB-Team-Level darf ein Box-Level nur überschreiben, wenn die EXP seitdem
    gleich ist — sonst zeigt die Box nach einem Editor-Level-Wechsel das alte Level."""
    from backend.classes.pokedex_db import PokedexDB
    db = PokedexDB(tmp_path)
    db.connect()
    p = pokedecoder.pokemon3(_bisasam_party(6), FEUERROT)
    db.upsert_pokemon("tester_1", FEUERROT, p)
    pv = p.personality
    assert db.get_lvls_by_personalities({pv: p.experience_points}) == {pv: 6}
    assert db.get_lvls_by_personalities({pv: exp_at_level("medium_slow", 14)}) == {}
    assert db.get_lvls_by_personalities({pv: None}) == {}
