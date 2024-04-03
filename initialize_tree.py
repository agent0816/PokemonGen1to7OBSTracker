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
    files = []
    sessions = []
    default_session = Path('backend/config/default')
    session_list = Path('backend/config/session_list.yml')
    if not session_list.exists():
        save_config(session_list, sessions)
    if not default_session.exists():
        default_session.mkdir(parents=True, exist_ok=True)
    for entry in config.iterdir():
        if entry.is_file() and entry != session_list:
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
        "order":'team',
        "platinum":'',
        "platinum_obs":'',
        "red":'',
        "red_obs":'',
        "ruby":'',
        "ruby_obs":'',
        "show_badges":False,
        "show_items":False,
        "show_nicknames":False,
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

if __name__ == '__main__':
    init_config_folder()