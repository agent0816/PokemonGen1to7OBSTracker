"""Encounter-Import aus bereits gefangenen Pokemon (Team + PC-Boxen).

Kivy-frei, reine Funktionen. Erzeugt Encounter-Dicts im Format von
``PokedexDB.sync_encounter`` -- ohne manuelle Sperren (manual_fields leer),
damit die Automatik die Zeilen wie normale Encounter behandelt.
"""
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Iterable

from backend.encounter_edit import pokemon_gender_form
from backend.logging_setup import get_logger

logger = get_logger(__name__, 'logs/encounter_import.log')


@dataclass
class ImportCandidate:
    """Ein importierbares Pokemon (Vorschau-Zeile)."""
    pv: int
    dexnr: int
    lvl: int
    shiny: bool
    female: bool | None
    form: str
    route: int
    method: str  # 'wild' | 'gift'
    source: str
    foreign_ot: bool = False


@dataclass
class RouteGroup:
    """Kandidaten einer Route inkl. Vorschlag für das erste Encounter."""
    route: int
    candidates: list[ImportCandidate] = field(default_factory=list)
    default_first_pv: int | None = None
    has_existing_first: bool = False


def _is_egg(pokemon: object) -> bool:
    return getattr(pokemon, "dexnr", 0) == "egg"


def _is_real(pokemon: object) -> bool:
    """Kein leerer Slot, kein Ei."""
    if pokemon is None:
        return False
    dexnr = getattr(pokemon, "dexnr", 0)
    return isinstance(dexnr, int) and not isinstance(dexnr, bool) and dexnr > 0


def _real_pv(pokemon: object) -> int | None:
    pv = getattr(pokemon, "personality", None)
    if isinstance(pv, int) and not isinstance(pv, bool) and pv > 0:
        return pv
    return None


def _trainer(pokemon: object) -> tuple[int, int] | None:
    tid = getattr(pokemon, "ot_id", None)
    sid = getattr(pokemon, "ot_secret_id", None)
    if tid is None or sid is None:
        return None
    return int(tid), int(sid)


def own_trainer(pokemons: Iterable) -> tuple[int, int] | None:
    """Häufigstes (TID, SID)-Paar unter echten, nicht-Ei-Pokemon."""
    counter: Counter = Counter()
    for pokemon in pokemons:
        if not _is_real(pokemon):
            continue
        trainer = _trainer(pokemon)
        if trainer is not None:
            counter[trainer] += 1
    if not counter:
        return None
    return counter.most_common(1)[0][0]


def _make_candidate(pokemon: object, pv: int, source: str,
                    own: tuple[int, int] | None) -> ImportCandidate:
    trainer = _trainer(pokemon)
    foreign = own is not None and trainer is not None and trainer != own
    female, form = pokemon_gender_form(pokemon)
    return ImportCandidate(
        pv=pv, dexnr=int(pokemon.dexnr),
        lvl=int(getattr(pokemon, "lvl", 0) or 0),
        shiny=bool(getattr(pokemon, "shiny", False)),
        female=female, form=form,
        route=int(getattr(pokemon, "route", 0) or 0),
        method="gift" if foreign else "wild",
        source=source, foreign_ot=foreign,
    )


def collect_candidates(team: Iterable, boxes: Iterable[Iterable],
                       own: tuple[int, int] | None,
                       existing_pvs: set) -> tuple[list[ImportCandidate], dict[str, int]]:
    """Sammelt Kandidaten aus Team + Boxen.

    Rückgabe: (Kandidaten, Skip-Zähler ``{"egg", "known", "no_pv"}``).
    Leere Slots werden still ignoriert; doppelte PVs nur einmal übernommen.
    """
    skipped = {"egg": 0, "known": 0, "no_pv": 0}
    result: list[ImportCandidate] = []
    seen: set[int] = set()

    def _visit(pokemon: object, source: str) -> None:
        if pokemon is None:
            return
        if _is_egg(pokemon):
            skipped["egg"] += 1
            return
        if not _is_real(pokemon):
            return
        pv = _real_pv(pokemon)
        if pv is None:
            skipped["no_pv"] += 1
            return
        if pv in existing_pvs:
            skipped["known"] += 1
            return
        if pv in seen:
            return
        seen.add(pv)
        result.append(_make_candidate(pokemon, pv, source, own))

    for idx, pokemon in enumerate(team or [], start=1):
        _visit(pokemon, f"Team Slot {idx}")
    for box_idx, box in enumerate(boxes or [], start=1):
        for slot_idx, pokemon in enumerate(box or [], start=1):
            _visit(pokemon, f"Box {box_idx}, Platz {slot_idx}")
    logger.info(
        f"Import-Kandidaten: {len(result)} (übersprungen: Eier={skipped['egg']}, "
        f"bekannt={skipped['known']}, ohne PV={skipped['no_pv']})"
    )
    return result, skipped


def group_by_route(candidates: Iterable[ImportCandidate],
                   routes_with_first: set[int]) -> list[RouteGroup]:
    """Gruppiert nach Route (sortiert).

    Default für das erste Encounter = niedrigstes Level unter Wild + eigener OT;
    Geschenke nie; Route mit vorhandenem ersten Encounter: keine Vorauswahl.
    """
    groups: dict[int, RouteGroup] = {}
    for cand in candidates:
        group = groups.setdefault(cand.route, RouteGroup(route=cand.route))
        group.candidates.append(cand)
    for route, group in groups.items():
        group.has_existing_first = route in routes_with_first
        if group.has_existing_first:
            continue
        eligible = [c for c in group.candidates
                    if c.method == "wild" and not c.foreign_ot]
        if eligible:
            group.default_first_pv = min(eligible, key=lambda c: c.lvl).pv
    return [groups[r] for r in sorted(groups)]


def build_encounters(groups: Iterable[RouteGroup], selected_pvs: set,
                     first_pvs: set, owner: str, edition: int) -> list[dict]:
    """Encounter-Dicts für ``sync_encounter`` (ohne Sperren).

    is_first: Geschenke immer 1 (gifts_are_additional, wie encounter_tracker),
    Wild nur für PVs in ``first_pvs``.
    """
    now = datetime.now(timezone.utc).isoformat()
    result: list[dict] = []
    for group in groups:
        for cand in group.candidates:
            if cand.pv not in selected_pvs:
                continue
            is_first = 1 if (cand.method == "gift" or cand.pv in first_pvs) else 0
            result.append({
                "personality": cand.pv, "owner": owner, "edition": int(edition),
                "route": cand.route, "dexnr": cand.dexnr, "lvl": cand.lvl,
                "shiny": int(cand.shiny), "female": cand.female,
                "form": cand.form, "is_first": is_first,
                "is_shiny_override": 0, "is_dupes_skip": 0, "has_balls": 1,
                "method": cand.method, "outcome": "caught", "deleted": 0,
                "dead": 0, "manual_fields": "", "timestamp": now,
            })
    return result


def auto_select(groups: Iterable[RouteGroup]) -> tuple[set[int], set[int]]:
    """Standard-Auswahl ohne Dialog (Host-Sync): alle Kandidaten uebernehmen,
    erstes Encounter je Route = Vorschlag aus ``group_by_route``.

    Entspricht dem Anfangszustand der Dialog-Vorschau (ImportSelection).
    """
    selected: set[int] = set()
    first: set[int] = set()
    for group in groups:
        selected.update(c.pv for c in group.candidates)
        if group.default_first_pv is not None:
            first.add(group.default_first_pv)
    return selected, first
