"""Nuzlocke Encounter-Tracker: prüft Regeln und persistiert Encounter-Daten."""

import traceback
from collections import defaultdict
from dataclasses import dataclass

import yaml

from backend.logging_setup import get_logger


@dataclass
class EncounterResult:
    dexnr: int
    lvl: int
    shiny: bool
    route: int
    method: str
    outcome: str
    is_first: bool
    is_shiny_override: bool
    is_dupes_skip: bool
    has_balls: bool
    already_logged: bool


class EncounterTracker:
    _GIFT_FILES = {
        3: "backend/data/gift_encounters_gen3.yml",
        4: "backend/data/gift_encounters_gen4.yml",
        5: "backend/data/gift_encounters_gen5.yml",
        6: "backend/data/gift_encounters_gen6.yml",
    }

    def __init__(self, pokedex_db, nuz: dict | None = None):
        self.pokedex_db = pokedex_db
        self.nuz = nuz or {}
        self.logger = get_logger(__name__, './logs/encounter_tracker.log')

        with open("backend/data/evolution_families.yml") as f:
            self.evolution_families: dict[int, int] = yaml.safe_load(f) or {}

        self.family_members: dict[int, list[int]] = defaultdict(list)
        for dexnr, basis in self.evolution_families.items():
            self.family_members[basis].append(dexnr)

        self._gift_cache: dict[int, dict[int, list]] = {}
        self.gift_definitions: dict[int, list] = {}

        self.logger.info(
            f"EncounterTracker initialisiert: {len(self.evolution_families)} Species, "
            f"{len(self.family_members)} Familien"
        )

    @staticmethod
    def _edition_to_gen(edition: int) -> int:
        if 31 <= edition <= 35:
            return 3
        if 41 <= edition <= 45:
            return 4
        if 51 <= edition <= 54:
            return 5
        if 61 <= edition <= 64:
            return 6
        return 0

    def _load_gifts(self, edition: int) -> dict[int, list]:
        gen = self._edition_to_gen(edition)
        if gen in self._gift_cache:
            return self._gift_cache[gen]
        path = self._GIFT_FILES.get(gen)
        if path is None:
            self._gift_cache[gen] = {}
            return {}
        try:
            with open(path) as f:
                defs = yaml.safe_load(f) or {}
            self._gift_cache[gen] = defs
            self.logger.info(f"Gift-Definitionen geladen: {path} ({len(defs)} Locations)")
            return defs
        except FileNotFoundError:
            self.logger.warning(f"Gift-Datei nicht gefunden: {path}")
            self._gift_cache[gen] = {}
            return {}

    def get_family_members(self, dexnr: int) -> list[int]:
        basis = self.evolution_families.get(dexnr, dexnr)
        return self.family_members.get(basis, [dexnr])

    def _check_nuzlocke_flags(self, owner: str, edition: int,
                               route: int, dexnr: int,
                               shiny: bool, method: str) -> tuple[bool, bool, bool, bool]:
        """Prüft Nuzlocke-Regeln. Gibt (has_balls, is_first, is_shiny_override, is_dupes_skip) zurück."""
        has_balls = self.pokedex_db.has_catching_balls(owner, edition)

        gifts_additional = self.nuz.get("gifts_are_additional", True)
        if gifts_additional and method in ("gift", "fossil", "egg", "static"):
            return has_balls, True, False, False

        route_has_encounter = self.pokedex_db.has_encounter_on_route(
            owner, edition, route
        )
        is_first = not route_has_encounter
        is_shiny_override = False
        is_dupes_skip = False

        if not is_first and shiny and self.nuz.get("shiny_clause", True):
            is_shiny_override = True

        if (is_first or is_shiny_override) and self.nuz.get("dupes_clause", True):
            family = self.get_family_members(dexnr)
            is_dupes_skip = self.pokedex_db.is_species_family_caught(
                owner, family
            )

        return has_balls, is_first, is_shiny_override, is_dupes_skip

    def process_wild_encounter(self, owner: str, edition: int,
                                opponent: dict, route: int) -> EncounterResult:
        """Prüft Nuzlocke-Regeln und persistiert Wild-Encounter."""
        dexnr = opponent["dexnr"]
        lvl = opponent["lvl"]
        shiny = opponent["shiny"]
        personality = opponent["personality"]
        method = "wild"
        outcome = "unknown"
        already_logged = False
        is_first = False
        is_shiny_override = False
        is_dupes_skip = False
        has_balls = False

        try:
            has_balls, is_first, is_shiny_override, is_dupes_skip = \
                self._check_nuzlocke_flags(owner, edition, route, dexnr, shiny, method)

            inserted = self.pokedex_db.insert_encounter(
                personality=personality,
                owner=owner,
                edition=edition,
                route=route,
                dexnr=dexnr,
                lvl=lvl,
                shiny=shiny,
                is_first=is_first or is_shiny_override,
                is_shiny_override=is_shiny_override,
                is_dupes_skip=is_dupes_skip,
                has_balls=has_balls,
                method=method,
                outcome=outcome,
            )
            already_logged = not inserted

        except Exception as err:
            self.logger.error(f"process_wild_encounter fehlgeschlagen: {type(err)},{err}")
            self.logger.error(f"{traceback.format_exc()}")

        return EncounterResult(
            dexnr=dexnr, lvl=lvl, shiny=shiny, route=route,
            method=method, outcome=outcome,
            is_first=is_first or is_shiny_override,
            is_shiny_override=is_shiny_override,
            is_dupes_skip=is_dupes_skip,
            has_balls=has_balls, already_logged=already_logged,
        )

    def process_gift_encounter(self, owner: str, edition: int,
                                personality: int, dexnr: int, lvl: int,
                                shiny: bool, route: int,
                                map_header_id: int) -> EncounterResult | None:
        """Prüft ob ein neues Pokemon ein bekanntes Geschenk ist und loggt es."""
        gift_defs = self._load_gifts(edition)
        gifts = gift_defs.get(map_header_id, [])
        if not gifts:
            return None

        matched_gift = None
        for gift in gifts:
            gift_editions = gift.get("edition")
            if gift_editions and edition not in gift_editions:
                continue
            if not gift.get("repeatable", False):
                matched_gift = gift
                break
            matched_gift = gift

        if matched_gift is None:
            return None

        method = matched_gift.get("type", "gift")
        outcome = "obtained"
        already_logged = False
        is_first = True
        is_shiny_override = False
        is_dupes_skip = False
        has_balls = False

        try:
            has_balls, is_first, is_shiny_override, is_dupes_skip = \
                self._check_nuzlocke_flags(
                    owner, edition, route, dexnr, shiny, method
                )

            inserted = self.pokedex_db.insert_encounter(
                personality=personality,
                owner=owner,
                edition=edition,
                route=route,
                dexnr=dexnr,
                lvl=lvl,
                shiny=shiny,
                is_first=is_first,
                is_shiny_override=is_shiny_override,
                is_dupes_skip=is_dupes_skip,
                has_balls=has_balls,
                method=method,
                outcome=outcome,
            )
            already_logged = not inserted

        except Exception as err:
            self.logger.error(f"process_gift_encounter fehlgeschlagen: {type(err)},{err}")
            self.logger.error(f"{traceback.format_exc()}")

        if not already_logged:
            self.logger.info(
                f"Gift-Encounter: map_header={map_header_id} "
                f"gift='{matched_gift.get('name', '?')}' method={method} "
                f"dex={dexnr} lv={lvl}"
            )

        return EncounterResult(
            dexnr=dexnr, lvl=lvl, shiny=shiny, route=route,
            method=method, outcome=outcome,
            is_first=is_first,
            is_shiny_override=is_shiny_override,
            is_dupes_skip=is_dupes_skip,
            has_balls=has_balls, already_logged=already_logged,
        )

    def update_outcome(self, personality: int, owner: str, outcome: str) -> bool:
        try:
            return self.pokedex_db.update_encounter_outcome(
                personality, owner, outcome
            )
        except Exception as err:
            self.logger.error(f"update_outcome fehlgeschlagen: {type(err)},{err}")
            self.logger.error(f"{traceback.format_exc()}")
            return False
