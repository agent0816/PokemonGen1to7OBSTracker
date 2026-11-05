"""Party-Team-Regeln (Frontend + Backend Consumer gemeinsam).

Reine Funktionen ueber Pokemon-Objekte + nuz-Config-Dict. Keine Klassen,
keine Kivy-/OBS-Abhaengigkeit. Wird von frontend/widgets/trainerbox.py
sowie backend/classes/overlay_server.py und backend/classes/obs.py
importiert, damit der Filter-Kern nur einmal existiert.
"""
from backend.type_lookup import first_type

# Link-Zustaende, deren Mitglieder nicht (mehr) gueltig im Team stehen duerfen
_INVALID_LINK_STATES = {"failed", "dead"}


def _link_info_by_pv(links: dict) -> tuple[dict, dict]:
    """Gruppen-Info der Mitglieder: ({(PV, Owner-Root): info}, {PV: info}),
    info = (link_id, state, member_outcome). Der PV-only-Index ist Fallback,
    wenn der Owner-Root unbekannt ist (PV-Kollisionen: erster Treffer)."""
    by_owner: dict = {}
    by_pv: dict = {}
    for key, link in list((links or {}).items()):
        if not isinstance(link, dict):
            continue
        try:
            link_id = int(link.get("link_id", key))
        except (TypeError, ValueError):
            link_id = float("inf")
        for root, member in list((link.get("members") or {}).items()):
            if not isinstance(member, dict):
                continue
            pv = member.get("personality")
            if pv is None:
                continue
            entry = (link_id, link.get("state"), member.get("outcome"))
            by_owner.setdefault((pv, str(root)), entry)
            by_pv.setdefault(pv, entry)
    return by_owner, by_pv


def first_type_dupe_slots(team, nuz: dict | None, links: dict | None = None,
                          dead_pvs=None, owner_root: str | None = None) -> set[int]:
    """Gesperrte Slots fuer Regel ``rule_single_type_per_team``.

    Nur Slots, deren Typ1 im Party-Team mehrfach vorkommt, koennen gesperrt
    werden. Pro Typ:
    - ungueltige Pokemon werden gesperrt und zaehlen nicht als Konkurrent:
      tot markiert (``dead_pvs``) und — nur mit ``links`` (Soullink) — in
      einer failed/dead-Gruppe oder in gar keiner Gruppe (Retry-/Extra-Fang);
    - unter den gueltigen bleibt das frueheste frei (kleinste Link-ID =
      frueher gefangen; ohne Gruppen bzw. ohne PV: frueherer Party-Slot),
      die uebrigen werden gesperrt.
    Eier und leere Slots (dexnr in {0, 'egg'}) sind ausgenommen. Regel aus
    bzw. ``nuz`` leer/None → leeres Set. ``links`` = None heisst kein
    Soullink (keine Gruppen-Pruefung). ``owner_root`` (Spielername des
    Teams) trennt gleiche PVs verschiedener Spieler; ohne ihn PV-only.
    Pokemon ohne PV (Gen 1/2) sind nicht zuordenbar: gelten als gueltig,
    verlieren aber gegen Pokemon mit Gruppe, tot-Markierung greift nicht.
    """
    if not nuz or not nuz.get("rule_single_type_per_team", False):
        return set()
    by_owner, by_pv = _link_info_by_pv(links) if links is not None else ({}, {})
    by_type: dict[int, list[tuple[tuple, int, bool]]] = {}
    for slot, pokemon in enumerate(team):
        dex = getattr(pokemon, "dexnr", 0)
        if not dex or dex == "egg":
            continue
        t = first_type(dex)
        if t is None:
            continue
        pv = getattr(pokemon, "personality", None)
        valid = True
        rank: tuple = (float("inf"), slot)
        if pv is not None and dead_pvs and pv in dead_pvs:
            valid = False
        elif links is not None and pv is not None:
            info = by_owner.get((pv, owner_root)) if owner_root else None
            if info is None:
                info = by_pv.get(pv)
            if info is None:
                valid = False  # in keiner Gruppe (Retry-/Extra-Fang)
            else:
                link_id, state, outcome = info
                if state in _INVALID_LINK_STATES or outcome == "dead":
                    valid = False
                else:
                    rank = (link_id, slot)
        elif links is None:
            rank = (0, slot)
        by_type.setdefault(t, []).append((rank, slot, valid))
    blocked: set[int] = set()
    for entries in by_type.values():
        if len(entries) < 2:
            continue
        blocked.update(slot for _rank, slot, valid in entries if not valid)
        winners = sorted((e for e in entries if e[2]), key=lambda e: e[0])
        blocked.update(slot for _rank, slot, _valid in winners[1:])
    return blocked


def party_blocked_slots(munchlax, player_id: int, team) -> set[int]:
    """Gemeinsamer Einstieg der Consumer (TrainerBox, Overlay, OBS): holt
    nuz, Soullink-Gruppen, Tot-Markierungen und Spielernamen thread-sicher
    aus Munchlax."""
    nuz = getattr(munchlax, "nuz", None)
    if not nuz or not nuz.get("rule_single_type_per_team", False):
        return set()
    from backend.encounter_view import is_soullink
    links = munchlax.snapshot_soullink_links() if is_soullink(nuz) else None
    dead_pvs = munchlax.snapshot_dead_pvs().get(player_id)
    owner_root = None
    if links is not None:
        # Wie overlay_server._owner_root_for_player: player_names (vom Server
        # broadcastet), Fallback eigener Name
        try:
            owner_root = munchlax.snapshot_player_names().get(player_id)
        except Exception:
            owner_root = None
        if not owner_root:
            owner_root = (getattr(munchlax, "pl", None) or {}).get("your_name") or None
    return first_type_dupe_slots(team, nuz, links=links, dead_pvs=dead_pvs,
                                 owner_root=owner_root)


def is_marked_dead(pokemon, dead_pvs) -> bool:
    """True, wenn der Encounter dieses Pokemon als tot markiert ist
    (Nuzlocke: auch bei vollen KP ausgrauen). Match ueber die PV; Pokemon
    ohne PV (Gen 1/2), Eier und leere Slots nie."""
    if not dead_pvs or pokemon is None:
        return False
    if isinstance(pokemon, dict):
        pv = pokemon.get("personality")
        dex = pokemon.get("dexnr", 0)
    else:
        pv = getattr(pokemon, "personality", None)
        dex = getattr(pokemon, "dexnr", 0)
    if pv is None or not dex or dex == "egg":
        return False
    return pv in dead_pvs
