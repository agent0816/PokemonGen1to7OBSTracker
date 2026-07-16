import sqlite3
from pathlib import Path
import yaml

def init_logging_folder():
    log_path = Path('logs')
    if not log_path.exists():
        log_path.mkdir(parents=True, exist_ok=True)

def save_config(path, setting):
    with open(path, 'w') as file:
        yaml.dump(setting, file)

def load_config(path, standard_settings, new_path=None):
    cur_settings = {}
    with(open(path)) as file:
        cur_settings = yaml.safe_load(file)
    for key in standard_settings:
        cur_keys = cur_settings.keys()
        if key not in cur_keys:
            cur_settings[key] = standard_settings[key]
        if (key.endswith('obs') or key.endswith('obs_path')) and cur_settings[key] == '':
            new_key = key.replace('_obs', '')
            cur_settings[key] = cur_settings[new_key]

    if not new_path:
        save_config(path, cur_settings)
    else:
        save_config(new_path, cur_settings)

def init_config_folder():
    config = Path('backend/config')
    if not config.exists():
        config.mkdir(parents=True, exist_ok=True)
    log_settings = Path('backend/config/log_settings.yml')
    if not log_settings.exists():
        save_config(log_settings, {'console_level': 'INFO'})
    files = []
    sessions = []
    default_session = Path('backend/config/default')
    session_list = Path('backend/config/session_list.yml')
    if not session_list.exists():
        save_config(session_list, sessions)
    if not default_session.exists():
        default_session.mkdir(parents=True, exist_ok=True)
    for entry in config.iterdir():
        if entry.is_file() and entry not in (session_list, log_settings):
            files.append(entry)
        elif not entry.is_file():
            sessions.append(entry)
    if files:
        update_session(config, default=True)
        for file in files:
            if file != session_list:
                file.unlink()
    if sessions:
        for session in sessions:
            update_session(session)

def update_session(sessionpath, default=False):
    sprites = Path(f'{sessionpath}/sprites.yml')
    sp = {
        "alphasapphire":'',
        "alphasapphire_obs":'',
        "animate_obs_reorder": False,
        "animated":False,
        "badges_path":'',
        "badges_obs_path":'',
        "black":'',
        "black_obs":'',
        "common_path":'',
        "common_obs_path":'',
        "crystal":'',
        "crystal_obs":'',
        "diamond":'',
        "diamond_obs":'',
        "edition_override":'',
        "emerald":'',
        "emerald_obs":'',
        "firered":'',
        "firered_obs":'',
        "gold":'',
        "gold_obs":'',
        "heartgold":'',
        "heartgold_obs":'',
        "items_path":'',
        "items_obs_path":'',
        "obs_2_pc":False,
        "obs_animation_duration_ms": 300,
        "order":'team',
        "platinum":'',
        "platinum_obs":'',
        "red":'',
        "red_obs":'',
        "ruby":'',
        "ruby_obs":'',
        "show_badges":False,
        "show_hp_bars":False,
        "show_items":False,
        "show_nicknames":False,
        "show_status_effects":False,
        "silver":'',
        "silver_obs":'',
        "single_path_check":False,
        "sun":'',
        "sun_obs":'',
        "usun":'',
        "usun_obs":'',
        "x":'',
        "x_obs":'',
        "yellow":'',
        "yellow_obs":''
    }
    if not sprites.exists():
        if not default:
            save_config(sprites, sp)
        else:
            new_sprites=Path(f"{sessionpath}/default/sprites.yml")
            save_config(new_sprites, sp)
    else:
        if not default:
            load_config(sprites, sp)
        else:
            new_sprites=Path(f"{sessionpath}/default/sprites.yml")
            load_config(sprites, sp, new_path=new_sprites)
    player = Path(f'{sessionpath}/player.yml')
    pl = {
        "obs_1":True,
        "obs_2":False,
        "obs_3":False,
        "obs_4":False,
        "player_count":1,
        "remote_1":False,
        "remote_2":False,
        "remote_3":False,
        "remote_4":False,
        "session_game": '',
        "your_name": "",
    }
    if not player.exists():
        if not default:
            save_config(player, pl)
        else:
            new_player=Path(f"{sessionpath}/default/player.yml")
            save_config(new_player, pl)
    else: 
        if not default:
            load_config(player, pl)
        else:
            new_player=Path(f"{sessionpath}/default/player.yml")
            load_config(player, pl, new_path=new_player)
    obs_path = Path(f'{sessionpath}/obs_config.yml')
    obs = {
        "host":'localhost',
        "password":'',
        "port":"4455"
    }
    if not obs_path.exists():
        if not default:
            save_config(obs_path, obs)
        else:
            new_obs=Path(f"{sessionpath}/default/obs_config.yml")
            save_config(new_obs, obs)
    else:
        if not default:
            load_config(obs_path, obs)
        else:
            new_obs=Path(f"{sessionpath}/default/obs_config.yml")
            load_config(obs_path, obs, new_path=new_obs)
    biz = Path(f'{sessionpath}/bh_config.yml')
    bh = {
        "host":'127.0.0.1',
        "path":'',
        "port":'43885',
        "save_automatically" : False
    }
    if not biz.exists():
        if not default:
            save_config(biz, bh)
        else:
            new_bh=Path(f"{sessionpath}/default/bh_config.yml")
            save_config(new_bh, bh)
    else:
        if not default:
            load_config(biz, bh)
        else:
            new_bh=Path(f"{sessionpath}/default/bh_config.yml")
            load_config(biz, bh, new_path=new_bh)
    remote = Path(f'{sessionpath}/remote.yml')
    rem = {
        "client_id" : 0,
        "client_port":'43886',
        "helper_port":'43887',
        "server_ip_adresse":'',
        "server_port":'43886',
        "start_server":False
    }
    if not remote.exists():
        if not default:
            save_config(remote, rem)
        else:
            new_rem=Path(f"{sessionpath}/default/remote.yml")
            save_config(new_rem, rem)
    else:
        if not default:
            load_config(remote, rem)
        else:
            new_rem=Path(f"{sessionpath}/default/remote.yml")
            load_config(remote, rem, new_path=new_rem)
    randomizer = Path(f'{sessionpath}/randomizer.yml')
    rnd = {
        "jar_path": '',
        "java_path": '',
        "settings_path": '',
        "rom_path": '',
        "output_path": '',
    }
    if not randomizer.exists():
        if not default:
            save_config(randomizer, rnd)
        else:
            new_rnd=Path(f"{sessionpath}/default/randomizer.yml")
            save_config(new_rnd, rnd)
    else:
        if not default:
            load_config(randomizer, rnd)
        else:
            new_rnd=Path(f"{sessionpath}/default/randomizer.yml")
            load_config(randomizer, rnd, new_path=new_rnd)
    overlay = Path(f'{sessionpath}/overlay.yml')
    ov = {
        "animate_reorder": True,
        "animation_duration_ms": 300,
        "badge_layout": "horizontal",
        "enabled": False,
        "layout": "horizontal",
        "port": "43888",
        # Soullink-Anzeige: leere Slots ans Ende, sortiert nach Link-Group
        "hide_incomplete_links": True,
        "sort_mode": "default",
    }
    if not overlay.exists():
        if not default:
            save_config(overlay, ov)
        else:
            new_ov=Path(f"{sessionpath}/default/overlay.yml")
            save_config(new_ov, ov)
    else:
        if not default:
            load_config(overlay, ov)
        else:
            new_ov=Path(f"{sessionpath}/default/overlay.yml")
            load_config(overlay, ov, new_path=new_ov)
    nuzlocke = Path(f'{sessionpath}/nuzlocke.yml')
    nuz = {
        "enabled": False,
        "gifts_are_additional": True,
        "fossils_repeatable": True,
        "static_encounters_separate": True,
        "shiny_clause": True,
        "dupes_clause": True,
        "encounter_methods_separate": False,
        # Soullink-Modus + Struktur. Werte: "disabled" (Tracker ohne Nuzlocke-Regeln),
        # "nuzlocke" (Regeln aktiv, kein Soullink, Default), "coop", "versus" (Teams),
        # "versus_ffa" (jeder gegen jeden, Scoreboard pro Spieler, kein Soullink).
        "soullink_mode": "nuzlocke",
        "soullink_player_count": 2,
        "soullink_link_strategy": "full_chain",
        "soullink_team_membership": {},
        "soullink_expected_owners": [],
        "soullink_death_sync": True,
        "soullink_box_sync": True,
        # Regeln (aus echten Regelwerken, alle als Checkbox konfigurierbar)
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
        "snapshot_backup_path": None,
    }
    if not nuzlocke.exists():
        if not default:
            save_config(nuzlocke, nuz)
        else:
            new_nuz=Path(f"{sessionpath}/default/nuzlocke.yml")
            save_config(new_nuz, nuz)
    else:
        # Migration: alter Wert "off" hieß "Nuzlocke-Regeln aktiv, kein Soullink" —
        # seit Einführung des echten Aus-Modus ("disabled") heißt dieser Zustand "nuzlocke".
        # "off" wird von der UI nicht mehr geschrieben, die Umbenennung ist daher stabil.
        with open(nuzlocke) as file:
            cur_nuz = yaml.safe_load(file) or {}
        if cur_nuz.get("soullink_mode") == "off":
            cur_nuz["soullink_mode"] = "nuzlocke"
            save_config(nuzlocke, cur_nuz)
        if not default:
            load_config(nuzlocke, nuz)
        else:
            new_nuz=Path(f"{sessionpath}/default/nuzlocke.yml")
            load_config(nuzlocke, nuz, new_path=new_nuz)

    session_root = Path(f"{sessionpath}/default") if default else Path(sessionpath)
    ensure_runs_dir(session_root)
    detect_legacy_encounter_archives(session_root)

def ensure_runs_dir(session_root: Path):
    runs = session_root / "runs"
    if not runs.exists():
        runs.mkdir(parents=True, exist_ok=True)

def detect_legacy_encounter_archives(session_root: Path):
    """Meldet alte encounters_archived_* Tabellen. Nur INFO-Log, kein Umbau."""
    db_path = session_root / "pokemon.db"
    if not db_path.exists():
        return
    try:
        conn = sqlite3.connect(str(db_path))
        try:
            rows = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name LIKE 'encounters_archived_%'"
            ).fetchall()
        finally:
            conn.close()
    except Exception:
        return
    if rows:
        names = [r[0] for r in rows]
        print(
            f"[initialize_tree] {session_root.name}: "
            f"{len(names)} legacy encounters_archived_* Tabellen gefunden "
            f"({', '.join(names[:3])}{'...' if len(names) > 3 else ''}). "
            "Bleiben unangetastet."
        )

if __name__ == '__main__':
    init_config_folder()