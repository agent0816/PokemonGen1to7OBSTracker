"""Einordnung eines Gen-3-Kampfs anhand von gBattleTypeFlags (u32).

Quelle: pret-Decomps (pokefirered/pokeemerald/pokeruby), include/constants/
battle.h + src/battle_setup.c (Recherche 2026-11-12):

- normaler Wildkampf (DoStandardWildBattle): 0 (plus IS_MASTER 1<<2, laut
  Decomp-Kommentar in Nicht-Link-Kaempfen immer gesetzt)
- FRLG ``dowildbattle`` (Relaxo, Lektrobal): WILD_SCRIPTED 1<<17
- FRLG ``special StartLegendaryBattle`` (Zapdos, Mewtu, Voegel, Lugia ...):
  LEGENDARY 1<<13 | LEGENDARY_FRLG 1<<18
- Roamer (Entei/Raikou/Suicune, Latias/Latios): ROAMER 1<<10
- FRLG Turm-Geister ohne Silph Scope / Tragosso: GHOST 1<<15
  (Tragosso mit Scope zusaetzlich 1<<13 = GHOST_UNVEILED)
- FRLG Alter Mann Vertania / RSE Wally: OLD_MAN_TUTORIAL / WALLY_TUTORIAL 1<<9
- RSE ``dowildbattle`` (Kecleon, Voltorb, Mogelbaum): 0 → nicht von wild
  unterscheidbar; nur Legendaere tragen 1<<13 (+REGI/KYOGRE_GROUDON, Emerald
  zusaetzlich 1<<28..30)

Die Werte sind aus dem Decomp belegt, live noch nicht bestaetigt — der
Tracker loggt deshalb den Flagwert bei jedem Kampfstart.
"""

BATTLE_TYPE_TRAINER = 1 << 3
BATTLE_TYPE_TUTORIAL = 1 << 9          # FRLG OLD_MAN_TUTORIAL / RSE WALLY_TUTORIAL
BATTLE_TYPE_ROAMER = 1 << 10
BATTLE_TYPE_LEGENDARY = 1 << 13
BATTLE_TYPE_GHOST_FRLG = 1 << 15       # in Emerald TWO_OPPONENTS (nur Trainer)
BATTLE_TYPE_WILD_SCRIPTED_FRLG = 1 << 17  # in Emerald PALACE (Frontier)
BATTLE_TYPE_LEGENDARY_FRLG = 1 << 18      # in Emerald ARENA (Frontier)

FRLG_EDITIONS = (34, 35)
RSE_EDITIONS = (31, 32, 33)

KLASSE_TRAINER = "trainer"
KLASSE_SKIP = "skip"
KLASSE_STATIC = "static"
KLASSE_WILD = "wild"


def classify_gen3_battle(edition: int, flags: int | None) -> str:
    """Ordnet einen Kampf ein: ``trainer``, ``skip``, ``static`` oder ``wild``.

    ``skip`` = nicht fangbarer Sonderkampf (Tutorial, Geister) → kein
    Encounter. ``flags=None`` (Pointer fehlt) → ``wild`` wie bisher.
    Nur Gen 3 (31–35) wird fein eingeordnet; andere Editionen behalten das
    bisherige Verhalten (Bit 3 → ``trainer``, sonst ``wild``).
    """
    if flags is None:
        return KLASSE_WILD
    flags = int(flags)
    if edition in FRLG_EDITIONS:
        if flags & BATTLE_TYPE_TRAINER:
            return KLASSE_TRAINER
        # Geist zuerst: GHOST_UNVEILED teilt sich das Bit mit LEGENDARY
        if flags & (BATTLE_TYPE_TUTORIAL | BATTLE_TYPE_GHOST_FRLG):
            return KLASSE_SKIP
        if flags & (BATTLE_TYPE_WILD_SCRIPTED_FRLG | BATTLE_TYPE_LEGENDARY_FRLG
                    | BATTLE_TYPE_LEGENDARY | BATTLE_TYPE_ROAMER):
            return KLASSE_STATIC
        return KLASSE_WILD
    if edition in RSE_EDITIONS:
        if flags & BATTLE_TYPE_TRAINER:
            return KLASSE_TRAINER
        if flags & BATTLE_TYPE_TUTORIAL:
            return KLASSE_SKIP
        if flags & (BATTLE_TYPE_LEGENDARY | BATTLE_TYPE_ROAMER):
            return KLASSE_STATIC
        return KLASSE_WILD
    # Andere Editionen: bisheriges Verhalten (nur Trainer-Bit) beibehalten
    if flags & BATTLE_TYPE_TRAINER:
        return KLASSE_TRAINER
    return KLASSE_WILD
