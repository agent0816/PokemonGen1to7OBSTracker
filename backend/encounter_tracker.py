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
    # True → Caller (bizhawk/citrahandler) muss send_soullink_token_earned auf
    # dem Event-Loop-Thread triggern. process_gift_encounter selbst läuft im
    # run_in_executor-Worker-Thread und kann kein asyncio.create_task rufen.
    token_earned: bool = False


class EncounterTracker:
    _GIFT_FILES = {
        3: "backend/data/gift_encounters_gen3.yml",
        4: "backend/data/gift_encounters_gen4.yml",
        5: "backend/data/gift_encounters_gen5.yml",
        6: "backend/data/gift_encounters_gen6.yml",
    }

    def __init__(self, pokedex_db, nuz: dict | None = None, munchlax=None):
        self.pokedex_db = pokedex_db
        self.nuz = nuz or {}
        # Optionaler Munchlax-Ref für Token-Credits (Task #12): erlaubt der Instanz,
        # server-seitigen Token-State zu lesen und Extra-Encounter-Slots freizuschalten.
        self.munchlax = munchlax
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

    def _has_active_token_credit(self, owner: str, edition, route) -> bool:
        if not self.nuz.get("rule_token_rule", False):
            return False
        if self.munchlax is None:
            return False
        tokens = getattr(self.munchlax, "soullink_tokens", None)
        if not isinstance(tokens, dict):
            return False
        st = tokens.get(owner)
        if not st:
            return False
        active_route = st.get("active_route")
        if active_route is None or int(active_route) != int(route):
            return False
        active_edition = st.get("active_edition")
        if active_edition is not None and edition is not None:
            try:
                if int(active_edition) != int(edition):
                    return False
            except (ValueError, TypeError):
                return False
        return True

    def _consume_token_credit(self, owner: str, edition, route):
        # Optimistisches lokales Update — Server erkennt den Verbrauch beim
        # nächsten encounter_sync über _token_consume_if_matches und broadcastet
        # den finalen Token-State zurück.
        if self.munchlax is None:
            return
        tokens = getattr(self.munchlax, "soullink_tokens", None)
        if isinstance(tokens, dict):
            st = tokens.get(owner)
            if st:
                st["active_route"] = None
                st["active_edition"] = None

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
        """Prüft Nuzlocke-Regeln. Gibt (has_balls, is_first, is_shiny_override, is_dupes_skip) zurück.

        Rule-Mapping (Task #10):
        - rule_shiny_clause_always_catchable → überschreibt shiny_clause (Preset-Kompat)
        - rule_same_species_retry            → überschreibt dupes_clause (Preset-Kompat)
        - rule_run_start_on_ball             → run beginnt erst mit erstem Ball
        """
        has_balls = self.pokedex_db.has_catching_balls(owner, edition)
        if self.nuz.get("rule_run_start_on_ball", True) and not has_balls:
            # Ohne Ball zählt nichts — is_first=False verhindert Link + DB-First-Marker
            return has_balls, False, False, False

        gifts_additional = self.nuz.get("gifts_are_additional", True)
        if gifts_additional and method in ("gift", "fossil", "egg", "static"):
            return has_balls, True, False, False

        route_has_encounter = self.pokedex_db.has_encounter_on_route(
            owner, edition, route
        )
        is_first = not route_has_encounter
        is_shiny_override = False
        is_dupes_skip = False

        # Token-Regel (Task #12): Bei aktivem Token-Credit für (owner, edition, route)
        # wird Route wie ohne bisherigen Encounter behandelt.
        if route_has_encounter and self._has_active_token_credit(owner, edition, route):
            is_first = True
            self._consume_token_credit(owner, edition, route)
            self.logger.info(
                f"Token-Extra-Slot verbraucht: owner={owner} route={route} edition={edition}"
            )

        shiny_active = self.nuz.get(
            "rule_shiny_clause_always_catchable",
            self.nuz.get("shiny_clause", True),
        )
        if not is_first and shiny and shiny_active:
            is_shiny_override = True

        dupes_active = self.nuz.get(
            "rule_same_species_retry",
            self.nuz.get("dupes_clause", True),
        )
        if (is_first or is_shiny_override) and dupes_active:
            family = self.get_family_members(dexnr)
            is_dupes_skip = self.pokedex_db.is_species_family_caught(
                owner, family
            )

        # Ersttyp-Clause-Retry: Wenn rule_first_type_clause_linked aktiv ist
        # UND ein verlinkter Partner bereits einen Pokemon mit gleichem Ersttyp
        # gefangen hat, gilt der Encounter als Retry (nicht als verbrauchter Slot).
        is_type_clash_retry = False
        if (is_first or is_shiny_override) and self._has_type_clash_with_linked(
            owner, dexnr, method
        ):
            is_type_clash_retry = True

        # Retry-Regeln: dupes-Skip UND Type-Clash-Retry verbrauchen die Route
        # NICHT — Encounter wird zwar geloggt (für Statistik), aber mit
        # is_first=False, damit der echte First-Encounter noch ansteht.
        # is_dupes_skip wird als gemeinsamer "Retry"-Marker in der DB verwendet.
        if is_dupes_skip or is_type_clash_retry:
            is_first = False
            is_shiny_override = False
            is_dupes_skip = True  # Marker vereinheitlichen
            if is_type_clash_retry:
                self.logger.info(
                    f"Ersttyp-Clause-Retry: owner={owner} dex={dexnr} method={method}"
                )

        return has_balls, is_first, is_shiny_override, is_dupes_skip

    def _has_type_clash_with_linked(self, owner: str, dexnr, method: str) -> bool:
        """Prüft ob ein verlinkter Partner bereits einen Pokemon mit gleichem
        Ersttyp gefangen hat. Wenn ja → Encounter darf zurückgewiesen werden
        (Retry-Regel), ohne die Route zu verbrauchen.

        - rule_first_type_clause_linked muss aktiv sein
        - method ∈ {gift, fossil, static, egg} zählt als exempt (bekommt Token
          statt Retry — der Spieler konnte den Typ nicht wählen)
        """
        if not self.nuz.get("rule_first_type_clause_linked", False):
            return False
        exempt = self.nuz.get("rule_first_type_clause_static_exception", True)
        exempt_methods = {"static", "gift", "fossil", "egg"} if exempt else set()
        if method in exempt_methods:
            return False
        if self.munchlax is None:
            return False
        from backend.type_lookup import first_type
        my_type = first_type(dexnr)
        if my_type is None:
            return False
        links = getattr(self.munchlax, "soullink_links", None)
        if not isinstance(links, dict):
            return False
        caught_like = {"caught", "obtained"}
        for link in links.values():
            expected = link.get("expected_owners", []) or []
            if owner not in expected:
                continue
            for other_owner, member in (link.get("members") or {}).items():
                if other_owner == owner:
                    continue
                if member.get("outcome") not in caught_like:
                    continue
                other_type = first_type(member.get("dexnr"))
                if other_type == my_type:
                    return True
        return False

    def process_wild_encounter(self, owner: str, edition: int,
                                opponent: dict, route: int) -> EncounterResult:
        """Prüft Nuzlocke-Regeln und persistiert Wild-Encounter.

        Läuft komplett unter ``pokedex_db.access_lock`` (RLock), damit
        ``_check_nuzlocke_flags`` (mehrere einzelne @_serialized-Reads) und
        ``insert_encounter`` (Write) als eine Einheit gegen echte
        Nebenläufigkeit anderer Executor-Tasks atomar bleiben. Sonst könnten
        z.B. zwei nahezu zeitgleiche Wild-Encounters beide den
        Species-Dupes-Check bestehen, bevor einer committet.
        """
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
            with self.pokedex_db.access_lock:
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

        # Wie process_wild_encounter: Read-Prüfungen + Insert unter einem
        # Lock-Halt, damit parallele Executor-Tasks nicht beide durch die
        # Nuzlocke-Checks laufen, bevor eine committet hat.
        try:
            with self.pokedex_db.access_lock:
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

        token_earned = False
        if not already_logged:
            self.logger.info(
                f"Gift-Encounter: map_header={map_header_id} "
                f"gift='{matched_gift.get('name', '?')}' method={method} "
                f"dex={dexnr} lv={lvl}"
            )
            # Token-Earn: bei method='token' Flag setzen — Caller triggert den
            # send_soullink_token_earned-Coroutine auf dem Event-Loop-Thread.
            # process_gift_encounter läuft aus run_in_executor im Worker-Thread,
            # asyncio.create_task würde hier deterministisch scheitern
            # ("no running event loop").
            if method == "token" and self.munchlax is not None and self.nuz.get("rule_token_rule", False):
                token_earned = True

        return EncounterResult(
            dexnr=dexnr, lvl=lvl, shiny=shiny, route=route,
            method=method, outcome=outcome,
            is_first=is_first,
            is_shiny_override=is_shiny_override,
            is_dupes_skip=is_dupes_skip,
            has_balls=has_balls, already_logged=already_logged,
            token_earned=token_earned,
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
