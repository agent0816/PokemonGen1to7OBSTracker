"""Drachenwut-Klausel: erkennt K.O.s durch gegnerische Drachenwut im Kampf.

Reine Logik ohne I/O und ohne Kivy — gefuettert vom BizHawk-Thread mit den
Werten aus gBattleResults (Gen 3: lastUsedMovePlayer/lastUsedMoveOpponent,
je pro Kampfseite) und den HP aus dem letzten Team-Read.

Grundsatz: Fehler laufen in Richtung "Tod zaehlt". gBattleResults wird beim
Kampfstart nicht geleert, deshalb gilt ein Drachenwut-Wert erst, wenn er
WAEHREND des Kampfes auf 82 gewechselt ist. Ein Rest-82 aus dem Vorkampf
loest nichts aus.

Bekannte Grenzen:
- Gegner setzt Drachenwut ein, kann im Folgezug nicht angreifen (Paralyse,
  Schlaf) und das eigene Pokemon faellt im selben Zug an Gift/Verbrennung/
  Rueckstoss — der Tod wird dann faelschlich vergeben. Ebenso ein Gift-Tod in
  den bis zu ~3 s Nach-Kampf-Reads, oder nach einem Gegnerwechsel, solange
  das neue Gegner-Pokemon noch nicht angegriffen hat (lastUsedMoveOpponent
  bleibt dann auf 82).
- Die Baseline entsteht erst beim ersten Read nach der Kampferkennung (bis
  ~1 s Verzug). Setzt der Gegner schon davor Drachenwut ein, steckt der Wert
  in der Baseline — ein K.O. im ersten Zug wird dann nicht vergeben (sichere
  Richtung).
"""

from __future__ import annotations

from dataclasses import dataclass, field

MOVE_DRAGON_RAGE = 82
# rule_flags-Marker (PokedexDB): Spieler hat im Run selbst Drachenwut eingesetzt.
DRAGON_RAGE_FLAG = "player_used_dragon_rage"

# Gen 3 struct BattleResults (pokeemerald), Offsets wie im Ironmon-Tracker
# (Program.lua offsetBattleResultsLastAttackerMove = 0x22, +2 pro Seite).
GEN3_BATTLE_RESULTS_READ_SIZE = 0x26
GEN3_LAST_MOVE_PLAYER_OFFSET = 0x22
GEN3_LAST_MOVE_OPPONENT_OFFSET = 0x24


def parse_gen3_battle_results(data: bytes) -> tuple[int, int]:
    """(lastUsedMovePlayer, lastUsedMoveOpponent) aus einem gBattleResults-Read."""
    if len(data) < GEN3_BATTLE_RESULTS_READ_SIZE:
        raise ValueError(f"gBattleResults-Read zu kurz: {len(data)} Bytes")
    player = int.from_bytes(
        data[GEN3_LAST_MOVE_PLAYER_OFFSET:GEN3_LAST_MOVE_PLAYER_OFFSET + 2], "little")
    enemy = int.from_bytes(
        data[GEN3_LAST_MOVE_OPPONENT_OFFSET:GEN3_LAST_MOVE_OPPONENT_OFFSET + 2], "little")
    return player, enemy


@dataclass
class TickResult:
    player_used_dragon_rage: bool = False
    dragon_rage_kos: list[int] = field(default_factory=list)


class DragonRageTracker:
    """Zustand eines Spielers ueber einen Kampf hinweg."""

    def __init__(self) -> None:
        self._baseline_pending = True
        self._prev_player_move: int | None = None
        self._prev_enemy_move: int | None = None
        # True, sobald die Gegnerseite in diesem Kampf auf Drachenwut gewechselt
        # ist und seither nichts anderes eingesetzt hat.
        self._enemy_dragon_rage_active = False
        self._prev_hp: dict[int, int] = {}

    def on_battle_start(self) -> None:
        """Naechster Tick setzt nur die Baseline (Rest-Werte aus dem Vorkampf)."""
        self._baseline_pending = True
        self._enemy_dragon_rage_active = False

    def on_battle_end(self) -> None:
        """Nach dem letzten Nach-Kampf-Read: kein Drachenwut-Kontext mehr, sonst
        wuerde ein spaeterer Gift-Tod ausserhalb des Kampfes vergeben."""
        self._enemy_dragon_rage_active = False
        self._prev_hp = {}

    def on_tick(self, player_move: int, enemy_move: int,
                hp_by_pv: dict[int, int]) -> TickResult:
        result = TickResult()
        if self._baseline_pending:
            self._baseline_pending = False
            self._prev_player_move = player_move
            self._prev_enemy_move = enemy_move
            self._prev_hp = dict(hp_by_pv)
            return result

        if player_move == MOVE_DRAGON_RAGE and self._prev_player_move != MOVE_DRAGON_RAGE:
            result.player_used_dragon_rage = True

        if enemy_move != self._prev_enemy_move:
            self._enemy_dragon_rage_active = enemy_move == MOVE_DRAGON_RAGE

        if self._enemy_dragon_rage_active:
            for pv, hp in hp_by_pv.items():
                if hp == 0 and self._prev_hp.get(pv, 0) > 0:
                    result.dragon_rage_kos.append(pv)

        self._prev_player_move = player_move
        self._prev_enemy_move = enemy_move
        self._prev_hp = dict(hp_by_pv)
        return result
