"""
Datei-Operationen für Sessions (ohne UI) — gemeinsam genutzt von der
klassischen SessionMenu (frontend/widgets/sessionsmenu.py) und der KivyMD-
Variante (frontend/widgets/sessionsmenumd.py).

Eine Session ist ein Ordner unter ``backend/config/<name>/`` mit acht YAMLs
(siehe ``SESSION_FILES``). ``pokemon.db`` und ``runs/`` entstehen lazy über
PokedexDB bzw. RunManager, neue Config-Keys migriert ``initialize_tree``
beim App-Start.

Config-Dicts werden — wie überall im Projekt — als Referenzen gehalten und
in-place aktualisiert, damit Backend-Objekte (Munchlax, OBS, …) die neuen
Werte ohne Neuverdrahtung sehen.
"""

import copy
import shutil
from dataclasses import dataclass
from pathlib import Path

import yaml

from backend.logging_setup import get_logger

logger = get_logger(__name__, 'logs/frontend.log')

# Dateiname -> Attributname in SessionConfigs. Reihenfolge = Schreibreihenfolge.
SESSION_FILES: dict[str, str] = {
    "sprites.yml": "sp",
    "bh_config.yml": "bh",
    "obs_config.yml": "obs",
    "remote.yml": "rem",
    "player.yml": "pl",
    "randomizer.yml": "rnd",
    "overlay.yml": "ov",
    "nuzlocke.yml": "nuz",
}
# Ältere Sessions haben diese Dateien evtl. noch nicht — fehlen sie, bleibt
# das Dict unverändert (Verhalten wie bisher in sessionsmenu.load_session_config).
OPTIONAL_FILES = {"randomizer.yml", "overlay.yml", "nuzlocke.yml"}


@dataclass
class SessionConfigs:
    """Bündel der acht Session-Dicts (Referenzen, keine Kopien)."""
    sp: dict
    bh: dict
    obs: dict
    rem: dict
    pl: dict
    rnd: dict
    ov: dict
    nuz: dict


def session_path(default_config: str, session_name: str) -> str:
    """Ordnerpfad einer Session, abgeleitet vom Default-Pfad (``…/default/``)."""
    return default_config.replace("default", session_name.replace(" ", "_"))


def load_session_into(config_path: str, configs: SessionConfigs) -> None:
    """Session-YAMLs aus ``config_path`` in-place in die Dicts laden."""
    for filename, attr in SESSION_FILES.items():
        file_path = Path(f"{config_path}{filename}")
        target: dict = getattr(configs, attr)
        if filename in OPTIONAL_FILES and not file_path.exists():
            continue
        with open(file_path, 'r') as file:
            new_values = yaml.safe_load(file)
        if attr == "nuz":
            # Stale-Key-Fix (auch bei leerer/korrupter YAML): rule_* und
            # soullink_*-Keys aus der VORHERIGEN Session zuerst leeren, sonst
            # leaken sie ueber den Wechsel hinweg. Beispiel-Symptom: Session A
            # hat `rule_shiny_clause_always_catchable: true` gespeichert,
            # Session B hat den Key nicht (weil aeltere Session ohne die
            # Regel-Migration oder komplett leere YAML) → nach Wechsel steht
            # in self.nuz weiter True, obwohl die Session B-Config keine
            # Aussage dazu macht. Fehlende Keys in der neuen Session fallen
            # dann sauber auf RULE_CHECKBOXES-Defaults zurueck (per .get in
            # nuzlockemenu) bzw. bei App-Start via init_config_folder-
            # Migration.
            stale_keys = [k for k in list(target.keys())
                          if k.startswith("rule_") or k.startswith("soullink_")
                          or k == "run_mode"]
            for k in stale_keys:
                del target[k]
            # Alt-Key "soullink_mode" (bis 2026-10) -> "run_mode", falls die
            # Session ohne Startup-Migration (initialize_tree) geladen wird.
            if new_values and "soullink_mode" in new_values:
                old_mode = new_values.pop("soullink_mode")
                new_values.setdefault("run_mode", old_mode)
        if new_values:
            for key, value in new_values.items():
                target[key] = value
    logger.info(f"Session-Config geladen: {config_path}")


def read_session(config_path: str) -> SessionConfigs:
    """Session-YAMLs als frische Dicts lesen (z.B. Default-Session als Vorlage)."""
    configs = SessionConfigs(**{attr: {} for attr in SESSION_FILES.values()})
    load_session_into(config_path, configs)
    return configs


def write_session_files(config_path: str, configs: SessionConfigs, create: bool = False) -> None:
    """Alle acht YAMLs nach ``config_path`` schreiben.

    ``create=True`` legt den Ordner an und bricht ab, falls er schon existiert
    (verhindert, dass eine bestehende Session überschrieben wird).
    """
    path = Path(config_path)
    if create:
        path.mkdir()
    for filename, attr in SESSION_FILES.items():
        with open(path / filename, 'w') as file:
            yaml.dump(getattr(configs, attr), file)
    logger.info(f"Session-Dateien geschrieben: {config_path} (create={create})")


def create_session(default_config: str, session_name: str, template: SessionConfigs,
                   player_name: str | None = None, session_game: str | None = None) -> str:
    """Neue Session aus ``template`` anlegen; Spielername/-spiel nur in der Kopie setzen.

    Die Template-Dicts bleiben unverändert (kein temporäres Umschreiben der
    Live-Dicts wie früher im Onboarding). Gibt den Session-Pfad zurück.
    """
    new_configs = copy.deepcopy(template)
    if player_name is not None:
        new_configs.pl["your_name"] = player_name
    if session_game is not None:
        new_configs.pl["session_game"] = session_game
    new_path = session_path(default_config, session_name)
    write_session_files(new_path, new_configs, create=True)
    return new_path


def delete_session_dir(config_path: str) -> None:
    """Session-Ordner inkl. runs/ und pokemon.db löschen (blockierend → Executor)."""
    path = Path(config_path)
    if path.exists():
        shutil.rmtree(str(path))
        logger.info(f"Session-Ordner gelöscht: {config_path}")


def save_session_list(default_config: str, session_names: list[str]) -> None:
    """``session_list.yml`` neben den Session-Ordnern schreiben."""
    list_dir = default_config.replace("default/", "")
    with open(f"{list_dir}/session_list.yml", "w") as file:
        yaml.dump(session_names, file)
