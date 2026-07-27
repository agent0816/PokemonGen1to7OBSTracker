import os
import traceback
from pathlib import Path
from git import Repo, GitCommandError
from backend.logging_setup import get_logger

logger = get_logger(__name__, 'logs/sprite_repo.log')

REPO_URL = "https://github.com/agent0816/sprites.git"

EDITION_DEFAULTS = {
    "red": "/generation-i/red-blue/transparent/",
    "yellow": "/generation-i/yellow/transparent/",
    "gold": "/generation-ii/gold/transparent/",
    "silver": "/generation-ii/silver/transparent/",
    "crystal": "/generation-ii/crystal/transparent/",
    "ruby": "/generation-iii/ruby-sapphire/",
    "emerald": "/generation-iii/emerald/",
    "firered": "/generation-iii/firered-leafgreen/",
    "diamond": "/generation-iv/diamond-pearl/",
    "platinum": "/generation-iv/platinum/",
    "heartgold": "/generation-iv/heartgold-soulsilver/",
    "black": "/generation-v/black-white/",
    "x": "/generation-vi/x-y",
    "alphasapphire": "/generation-vi/omegaruby-alphasapphire",
    "sun": "/generation-vii/ultra-sun-ultra-moon",
    "usun": "/generation-vii/ultra-sun-ultra-moon",
}


def clone_sprite_repo(target_path: str, url: str = REPO_URL) -> bool:
    try:
        logger.info(f"Klone Sprite-Repo von {url} nach {target_path}")
        Repo.clone_from(url, target_path)
        logger.info("Sprite-Repo erfolgreich geklont.")
        return True
    except GitCommandError as err:
        logger.error(f"Git-Fehler beim Klonen: {err}")
        logger.error(traceback.format_exc())
        return False
    except Exception as err:
        logger.error(f"Fehler beim Klonen: {err}")
        logger.error(traceback.format_exc())
        return False


def pull_sprite_repo(repo_path: str) -> bool:
    try:
        repo = Repo(repo_path)
        origin = repo.remotes.origin
        logger.info(f"Pull Sprite-Repo in {repo_path}")
        origin.pull()
        logger.info("Sprite-Repo erfolgreich aktualisiert.")
        return True
    except GitCommandError as err:
        logger.error(f"Git-Fehler beim Pull: {err}")
        logger.error(traceback.format_exc())
        return False
    except Exception as err:
        logger.error(f"Fehler beim Pull: {err}")
        logger.error(traceback.format_exc())
        return False


def is_sprite_repo(path: str) -> bool:
    if not path or not os.path.isdir(path):
        return False
    try:
        repo = Repo(path, search_parent_directories=True)
        for remote in repo.remotes:
            if "sprites" in remote.url:
                return True
        return False
    except Exception:
        return False


def get_repo_root_from_subpath(sprite_path: str) -> str | None:
    if not sprite_path or not os.path.isdir(sprite_path):
        return None
    try:
        repo = Repo(sprite_path, search_parent_directories=True)
        return str(repo.working_dir)
    except Exception:
        return None


def apply_sprite_paths(sp: dict, repo_root: str):
    base = Path(repo_root) / "sprites"
    common = str(base / "pokemon" / "versions").replace("\\", "/")
    items = str(base / "items").replace("\\", "/")
    badges = str(base / "badges").replace("\\", "/")

    sp["common_path"] = common
    sp["items_path"] = items
    sp["badges_path"] = badges

    for edition, subpath in EDITION_DEFAULTS.items():
        sp[edition] = subpath

    # OBS-PC-Pfade nur setzen, wenn kein separater Streaming-PC konfiguriert ist.
    # Bei 2-PC-Setup hat der OBS-PC eigene Klonpfade — die dürfen wir nicht mit
    # unseren lokalen Pfaden überschreiben.
    if not sp.get("obs_2_pc"):
        sp["common_obs_path"] = common
        sp["items_obs_path"] = items
        sp["badges_obs_path"] = badges
        for edition, subpath in EDITION_DEFAULTS.items():
            sp[f"{edition}_obs"] = subpath
        logger.info(f"Sprite-Pfade gesetzt (Single-PC): common={common}, items={items}, badges={badges}")
    else:
        logger.info(f"Sprite-Pfade gesetzt (Gaming-PC): common={common}, items={items}, badges={badges} — OBS-Pfade unangetastet (2-PC-Setup)")
