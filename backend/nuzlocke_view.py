"""Reine Nuzlocke/Soullink-UI-Logik ohne Kivy — geteilt von klassischer UI
(frontend/widgets/nuzlockemenu.py) und KivyMD (frontend/widgets/nuzlockemenu_md.py).

Enthaelt nur Daten-Transformationen (Modus-Labels, Regel-Metadaten, Owner-Merge,
Config-Sammlung, Client-Abgleich). Kein State, kein Kivy, kein Netz — pytest-
abgedeckt in tests/test_nuzlocke_view.py. Preset-Handling bleibt in
backend/soullink_presets.py (load_presets/apply_preset/preset_choices) und wird
hier NICHT dupliziert.
"""

from __future__ import annotations


TEAM_LETTERS = ["A", "B", "C", "D"]

# Backend erwartet die Roh-Keys ("nuzlocke"/"coop"/"versus"/"versus_ffa"/"disabled").
# Im UI zeigen wir Klartext-Labels und uebersetzen an den Grenzen.
# "nuzlocke" = Regeln aktiv, kein Soullink (Default); "versus_ffa" = jeder gegen
# jeden (kein Soullink, Scoreboard pro Spieler); "disabled" = Tracker ohne
# Nuzlocke-Regeln. Legacy-Wert "off" aus alten Configs/Peers entspricht
# "nuzlocke" (Migration in initialize_tree).
MODE_LABELS = {
    "nuzlocke": "Nuzlocke",
    "coop": "Coop",
    "versus": "Versus (Teams)",
    "versus_ffa": "Versus (jeder gegen jeden)",
    "disabled": "Aus",
}
MODE_LABEL_TO_VALUE = {v: k for k, v in MODE_LABELS.items()}

# Preset-"Custom" ist eine virtuelle Option (kein Eintrag in soullink_presets.yml),
# gesetzt sobald der User eine Regel-Checkbox von Hand aendert.
CUSTOM_PRESET_LABEL = "(Custom)"
CUSTOM_PRESET_ID = "__custom__"

# Sichtbare Regel-Checkboxen als (key, label) — Reihenfolge = UI-Reihenfolge der
# 2-Spalten-Grid. Die Defaults liegen NICHT hier, sondern in RULE_DEFAULTS (Quelle:
# initialize_tree.py), damit es keinen zweiten, abweichenden Default-Satz gibt.
RULE_CHECKBOXES: list[tuple[str, str]] = [
    ("rule_run_start_on_ball",                  "Run startet erst mit Ball"),
    ("rule_dead_pokemon_unusable",              "Tote Pokemon nicht mehr nutzbar"),
    ("rule_one_encounter_per_area",             "Ein Encounter pro Route"),
    ("rule_must_catch_first_encounter",         "Erster Encounter muss gefangen werden"),
    ("rule_nickname_required",                  "Nickname Pflicht"),
    ("rule_species_clause_cross_players",       "Species-Clause spielerübergreifend"),
    ("rule_same_species_retry",                 "Dupes-Clause (gleiche Species = Retry)"),
    ("rule_token_rule",                         "Token-Regel"),
    ("rule_shiny_clause_always_catchable",      "Shiny-Clause (immer fangbar)"),
    ("rule_restart_on_total_wipe",              "Neustart bei Total-Wipe"),
    ("rule_first_type_clause_linked",           "Ersttyp-Clause (verlinkt)"),
    ("rule_first_type_clause_static_exception", "Ersttyp: Static-Ausnahme"),
    ("rule_single_type_per_team",               "Ersttyp-Dupes im Party-Team ausblenden"),
]

# Kanonische Regel-Defaults — 1:1 Spiegel von initialize_tree.py:288-305. Dient als
# Fallback, falls ein nuz-Dict einen Key noch nicht enthaelt (sehr alte Session vor
# Preset-Migration). Enthaelt AUCH die nicht als Checkbox sichtbaren Regeln, damit
# collect_config beim Mitschicken aller rule_*-Keys keine Luecke hat.
RULE_DEFAULTS: dict[str, object] = {
    "rule_run_start_on_ball": True,
    "rule_dead_pokemon_unusable": True,
    "rule_restart_on_total_wipe": False,
    "rule_one_encounter_per_area": True,
    "rule_must_catch_first_encounter": True,
    "rule_nickname_required": True,
    "rule_first_type_clause_linked": False,
    "rule_first_type_clause_static_exception": True,
    "rule_single_type_per_team": False,
    "rule_species_clause_cross_players": False,
    "rule_shiny_clause_always_catchable": True,
    "rule_doubles_only": False,
    "rule_doubles_only_after_first_rival": False,
    "rule_randomized_movesets": False,
    "rule_dragon_rage_clause": False,
    "rule_dragon_rage_clause_badge_limit": 2,
    "rule_token_rule": False,
    "rule_same_species_retry": False,
}


def normalize_mode(value) -> str:
    """Uebersetzt Legacy-/Leer-Werte auf den heutigen Modus-Schluessel."""
    if not value or value == "off":
        return "nuzlocke"
    return value if value in MODE_LABELS else "nuzlocke"


def rule_value(nuz: dict, key: str) -> bool:
    """Regel-Wert aus dem nuz-Dict, mit kanonischem Fallback aus RULE_DEFAULTS."""
    return bool(nuz.get(key, RULE_DEFAULTS.get(key, False)))


def clamp_player_count(value, lo: int = 1, hi: int = 4, default: int = 2) -> int:
    """Spieleranzahl robust auf [lo, hi] klemmen (Spinner-Text kann Muell sein)."""
    try:
        count = int(value)
    except (ValueError, TypeError):
        count = default
    return max(lo, min(hi, count))


def collect_config(mode_value: str, owner_rows, nuz: dict) -> dict:
    """Baut das Config-Dict fuer send_soullink_config aus den Owner-Zeilen + nuz.

    owner_rows: iterable von (owner_name, team_letter)-Tupeln (Team nur im
    Versus-Modus relevant). dict.fromkeys erhaelt Reihenfolge und dedupliziert —
    verhindert, dass zwei Zeilen denselben Owner tragen (Bug-Quelle: expected_owners
    ['Stephan','Stephan'] wanderte so in die Config, log-detektiv 2026-09-16).
    """
    rows = [(str(o).strip(), t) for o, t in owner_rows]
    owners = list(dict.fromkeys(o for o, _ in rows if o))
    team_map: dict[str, str] = {}
    if mode_value == "versus":
        for owner, team in rows:
            if owner and owner not in team_map:
                team_map[owner] = team
    # Alle rule_*-Keys aus nuz mitschicken, damit der Server sie fuers Enforcement
    # (Ersttyp-Clash etc.) auslesen kann — nicht nur die sichtbaren.
    rules = {k: v for k, v in nuz.items() if k.startswith("rule_")}
    return {
        "mode": mode_value,
        "player_count": len(owners),
        "link_strategy": "full_chain",
        "team_membership": team_map,
        "expected_owners": owners,
        "rules": rules,
    }


def connected_owner_names(player_names: dict | None,
                          client_names: dict | None,
                          your_name: str | None) -> list[str]:
    """Distinct Client-Namen sortiert nach player_id (Netz-Slot).

    Primaer: player_names (dict[player_id -> name]), sortiert nach player_id
    (deterministisch, Slot 1 zuerst). Fallback: client_names (Verbindungs-
    reihenfolge) fuer Clients ohne declared_player_ids. Eigener Name (your_name)
    landet am Ende, falls nicht schon aufgetaucht.
    """
    ordered: list[str] = []
    seen: set[str] = set()

    def _add(raw):
        if not isinstance(raw, str):
            return
        name = raw.strip()
        if name and name not in seen:
            seen.add(name)
            ordered.append(name)

    def _pid_key(pid):
        try:
            return int(pid)
        except (TypeError, ValueError):
            return 10 ** 9

    for pid in sorted((player_names or {}).keys(), key=_pid_key):
        _add((player_names or {}).get(pid))
    for name in (client_names or {}).values():
        _add(name)
    _add(your_name or "")
    return ordered


def merge_owner_rows(count: int,
                     existing_owners: list[str],
                     existing_teams: list[str],
                     expected_owners,
                     team_map: dict | None,
                     mode_value: str) -> list[tuple[str, str]]:
    """Berechnet die Owner-Zeilen (owner, team) nach Spieleranzahl-Aenderung.

    Merge-Strategie (1:1 aus nuzlockemenu._rebuild_owner_rows):
      - i < len(existing): bestehenden Row-Wert behalten (auch leer — bewusstes
        Loeschen darf nicht rehydrieren).
      - i >= len(existing): aus dedup'd expected auffuellen, sonst leer.
    Danach Post-hoc-Dedup (spaetere Duplikate leeren). Team-Letter aus
    existing_teams bzw. team_map (Fallback A/B im Wechsel).
    Sichtbarkeit der Team-Spalte (mode == versus) regelt der Aufrufer.
    """
    count = max(1, min(4, int(count)))
    team_map = team_map or {}
    raw_expected = expected_owners or []
    expected = list(dict.fromkeys(
        str(o) for o in raw_expected if str(o).strip()))

    merged: list[str] = []
    for i in range(count):
        if i < len(existing_owners):
            merged.append(existing_owners[i])
        elif i < len(expected):
            merged.append(expected[i])
        else:
            merged.append("")

    seen: set[str] = set()
    for i, name in enumerate(merged):
        if not name:
            continue
        if name in seen:
            merged[i] = ""
        else:
            seen.add(name)

    rows: list[tuple[str, str]] = []
    for i in range(count):
        owner = merged[i]
        if i < len(existing_teams):
            team = existing_teams[i]
        else:
            team = team_map.get(owner, TEAM_LETTERS[i % 2])
        rows.append((owner, team))
    return rows


def client_verify_diff(expected_pairs, connected):
    """Diff zwischen konfigurierten Owner-Slots und verbundenen Clients.

    expected_pairs: list[(original_row_index, owner_name)] — nur non-empty Rows.
    Original-Index ist kritisch, weil _replace_owner_in_row direkt in die
    (ungefilterte) Row-Liste indiziert (gui-reviewer Runde 2 KRITISCH, Klassik).

    Rueckgabe: (matched, missing_pairs, extra)
      matched       = list[str] expected + verbunden
      missing_pairs = list[(idx, name)] expected, aber nicht verbunden
      extra         = list[str] verbunden, aber nicht in Config
    """
    expected = [name for _idx, name in expected_pairs]
    expected_set = set(expected)
    connected_set = set(connected)
    matched = [name for _idx, name in expected_pairs if name in connected_set]
    missing_pairs = [(idx, name) for idx, name in expected_pairs
                     if name not in connected_set]
    extra = [c for c in connected if c not in expected_set]
    return matched, missing_pairs, extra
