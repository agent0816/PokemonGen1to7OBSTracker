from pathlib import Path
import yaml

def init_logging_folder():
    log_path = Path('logs')
    if not log_path.exists():
        log_path.mkdir(parents=True, exist_ok=True)

def save_config(path, setting):
    with open(path, 'w') as file:
        yaml.dump(setting, file)

def load_config(path, standard_settings):
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

    save_config(path, cur_settings)

def init_config_folder():
    config = Path('backend/config')
    if not config.exists():
        config.mkdir(parents=True, exist_ok=True)
    sprites = Path('backend/config/sprites.yml')
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
        save_config(sprites, sp)
    else:
        load_config(sprites, sp)
    player = Path('backend/config/player.yml')
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
    }
    if not player.exists():
        save_config(player, pl)
    else: 
        load_config(player, pl)
    obs_path = Path('backend/config/obs_config.yml')
    obs = {
        "host":'localhost',
        "password":'',
        "port":"4455"
    }
    if not obs_path.exists():
        save_config(obs_path, obs)
    else:
        load_config(obs_path, obs)
    biz = Path('backend/config/bh_config.yml')
    bh = {
        "host":'127.0.0.1',
        "path":'',
        "port":'43885'
    }
    if not biz.exists():
        save_config(biz, bh)
    else:
        load_config(biz, bh)
    remote = Path('backend/config/remote.yml')
    rem = {
        "client_port":'43886',
        "server_ip_adresse":'',
        "server_port":'43886',
        "start_server":False
    }
    if not remote.exists():
        save_config(remote, rem)
    else:
        load_config(remote, rem)

if __name__ == '__main__':
    init_config_folder()