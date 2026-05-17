import os
import logging
from dulwich import porcelain
from dulwich.errors import NotGitRepository

logger = logging.getLogger("sprite_helper.git_ops")

REPO_URL = "https://github.com/agent0816/sprites.git"


def clone(url: str, target_path: str) -> bool:
    try:
        logger.info(f"Klone {url} nach {target_path}")
        porcelain.clone(url, target_path)
        logger.info("Klonen erfolgreich.")
        return True
    except Exception as err:
        logger.error(f"Fehler beim Klonen: {err}")
        return False


def pull(repo_path: str) -> bool:
    try:
        logger.info(f"Pull in {repo_path}")
        porcelain.pull(repo_path)
        logger.info("Pull erfolgreich.")
        return True
    except NotGitRepository:
        logger.error(f"Kein Git-Repository: {repo_path}")
        return False
    except Exception as err:
        logger.error(f"Fehler beim Pull: {err}")
        return False


def is_repo(path: str) -> bool:
    if not path or not os.path.isdir(path):
        return False
    try:
        porcelain.open_repo(path)
        return True
    except NotGitRepository:
        return False
