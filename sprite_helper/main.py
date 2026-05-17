"""
Sprite-Helper: Eigenständiges Tool für den OBS-PC.
Klont das Sprite-Repository, richtet einen Scheduled Task ein
und meldet den Pfad an die Tracker-App zurück.
"""
import asyncio
import os
import subprocess
import sys
import logging
import threading
import tkinter as tk
from tkinter import filedialog, messagebox
import yaml

logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s: %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("sprite_helper")

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


class SpriteHelperApp:
    def __init__(self, config: dict):
        self.config = config
        self.repo_url = config.get("repo_url", "https://github.com/agent0816/sprites.git")
        self.tracker_ip = config.get("tracker_ip", "")
        self.tracker_port = config.get("tracker_port", 0)

        self.root = tk.Tk()
        self.root.title("Sprite-Helper")
        self.root.geometry("500x300")
        self.root.resizable(False, False)

        self._build_ui()

    def _build_ui(self):
        frame = tk.Frame(self.root, padx=15, pady=15)
        frame.pack(fill=tk.BOTH, expand=True)

        info = tk.Label(frame, text=(
            "Dieses Tool klont das Sprite-Repository auf diesen PC\n"
            "und richtet automatische Updates ein.\n\n"
            "Wähle einen Ordner für die Sprites:"
        ), justify=tk.LEFT)
        info.pack(anchor=tk.W)

        path_frame = tk.Frame(frame)
        path_frame.pack(fill=tk.X, pady=(10, 5))

        default_path = os.path.abspath("sprites")
        self.path_var = tk.StringVar(value=default_path)
        self.path_entry = tk.Entry(path_frame, textvariable=self.path_var, width=50)
        self.path_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)

        browse_btn = tk.Button(path_frame, text="...", width=3, command=self._browse)
        browse_btn.pack(side=tk.RIGHT, padx=(5, 0))

        self.status_var = tk.StringVar(value="")
        self.status_label = tk.Label(frame, textvariable=self.status_var, fg="gray")
        self.status_label.pack(anchor=tk.W, pady=(5, 10))

        btn_frame = tk.Frame(frame)
        btn_frame.pack(fill=tk.X)

        self.install_btn = tk.Button(btn_frame, text="Installieren", command=self._on_install, width=15)
        self.install_btn.pack(side=tk.LEFT)

        cancel_btn = tk.Button(btn_frame, text="Abbrechen", command=self.root.destroy, width=15)
        cancel_btn.pack(side=tk.RIGHT)

    def _browse(self):
        path = filedialog.askdirectory(title="Zielordner wählen")
        if path:
            self.path_var.set(os.path.join(path, "sprites"))

    def _on_install(self):
        target = self.path_var.get().strip()
        if not target:
            messagebox.showerror("Fehler", "Bitte einen Pfad angeben.")
            return

        if os.path.exists(target) and os.listdir(target):
            messagebox.showerror("Fehler", f"Ordner existiert bereits und ist nicht leer:\n{target}")
            return

        self.install_btn.config(state=tk.DISABLED)
        self.status_var.set("Klone Repository... Bitte warten.")
        self.status_label.config(fg="orange")

        threading.Thread(target=self._install_thread, args=(target,), daemon=True).start()

    def _install_thread(self, target: str):
        from sprite_helper.git_ops import clone
        success = clone(self.repo_url, target)

        if not success:
            self.root.after(0, self._on_error, "Fehler beim Klonen. Siehe Konsole.")
            return

        self._setup_scheduled_task(target)

        self._save_config(target)

        paths = self._build_paths(target)

        if self.tracker_ip and self.tracker_port:
            self.root.after(0, self._set_status, "Sende Pfade an Tracker...", "orange")
            sent = asyncio.run(self._send_paths(paths))
            if not sent:
                self.root.after(0, self._on_error,
                    "Klonen erfolgreich, aber Pfade konnten nicht\nan den Tracker gesendet werden.\nBitte OBS-Pfade manuell eintragen.")
                return

        self.root.after(0, self._on_success)

    def _build_paths(self, repo_root: str) -> dict:
        base = os.path.join(repo_root, "sprites").replace("\\", "/")
        paths = {
            "common_obs_path": f"{base}/pokemon/versions",
            "items_obs_path": f"{base}/items",
            "badges_obs_path": f"{base}/badges",
        }
        for edition, subpath in EDITION_DEFAULTS.items():
            paths[f"{edition}_obs"] = subpath
        return paths

    def _setup_scheduled_task(self, repo_path: str):
        try:
            exe_dir = os.path.dirname(os.path.abspath(sys.argv[0]))
            pull_exe = os.path.join(exe_dir, "pull_task.exe")

            if not os.path.exists(pull_exe):
                pull_exe = os.path.join(exe_dir, "pull_task.py")
                if not os.path.exists(pull_exe):
                    logger.warning("pull_task nicht gefunden, überspringe Scheduled Task.")
                    return

            task_name = "PokemonTracker_SpriteUpdate"
            cmd = (
                f'schtasks /create /tn "{task_name}" /tr "{pull_exe}" '
                f'/sc daily /st 12:00 /f'
            )
            result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
            if result.returncode == 0:
                logger.info(f"Scheduled Task '{task_name}' erstellt.")
            else:
                logger.warning(f"Scheduled Task konnte nicht erstellt werden: {result.stderr}")
        except Exception as err:
            logger.warning(f"Fehler beim Erstellen des Scheduled Tasks: {err}")

    def _save_config(self, repo_path: str):
        config_path = os.path.join(os.path.dirname(os.path.abspath(sys.argv[0])), "config.yml")
        config = self.config.copy()
        config["sprite_path"] = repo_path
        with open(config_path, 'w') as f:
            yaml.dump(config, f)

    async def _send_paths(self, paths: dict) -> bool:
        from sprite_helper.network import send_sprite_paths
        return await send_sprite_paths(self.tracker_ip, self.tracker_port, paths)

    def _set_status(self, text: str, color: str):
        self.status_var.set(text)
        self.status_label.config(fg=color)

    def _on_error(self, msg: str):
        self.status_var.set(msg)
        self.status_label.config(fg="red")
        self.install_btn.config(state=tk.NORMAL)

    def _on_success(self):
        self.status_var.set("Erfolgreich installiert!")
        self.status_label.config(fg="green")
        messagebox.showinfo("Fertig", "Sprites wurden erfolgreich installiert.\nAutomatische Updates sind eingerichtet.")
        self.root.destroy()

    def run(self):
        self.root.mainloop()


def main():
    config_path = os.path.join(os.path.dirname(os.path.abspath(sys.argv[0])), "config.yml")

    config = {}
    if os.path.exists(config_path):
        with open(config_path) as f:
            config = yaml.safe_load(f) or {}
    else:
        logger.warning("Keine config.yml gefunden. Starte ohne Tracker-Verbindung.")

    app = SpriteHelperApp(config)
    app.run()


if __name__ == "__main__":
    main()
