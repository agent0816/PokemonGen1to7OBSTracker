"""
Standalone-Script für den Windows Task Scheduler.
Führt einen git pull im konfigurierten Sprite-Repository aus.
Wird als eigener Entry-Point kompiliert oder als .pyw ausgeführt.
"""
import os
import sys
import logging
import yaml

logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s: %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("sprite_helper.pull_task")


def main():
    config_path = os.path.join(os.path.dirname(os.path.abspath(sys.argv[0])), "config.yml")

    if not os.path.exists(config_path):
        logger.error(f"config.yml nicht gefunden: {config_path}")
        sys.exit(1)

    with open(config_path) as f:
        config = yaml.safe_load(f)

    repo_path = config.get("sprite_path")
    if not repo_path or not os.path.isdir(repo_path):
        logger.error(f"Sprite-Pfad ungültig: {repo_path}")
        sys.exit(1)

    from sprite_helper.git_ops import pull
    success = pull(repo_path)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
