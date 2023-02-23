from pathlib import Path
Path('logs').mkdir(parents=True, exist_ok=True)
import logging
logging.basicConfig(level=logging.INFO, filename='logs/server.log',format='[%(asctime)s] %(levelname)s: %(message)s', filemode='w', encoding='utf-8')

Path('backend/config').mkdir(parents=True, exist_ok=True)
sprites = Path('backend/config/sprites.yml')
if not sprites.exists():
    sprites.write_text("animated: false\nblack: ''\ncommon_path: ''\ncrystal: ''\ndiamond: ''\nedition_override: ''\nemerald: ''\nfirered: ''\ngold: ''\nheartgold: ''\nitems_path: ''\norder: lvl\nplatinum: ''\nred: ''\nruby: ''\nshow_items: true\nshow_nicknames: true\nsilver: ''\nsingle_path_check: true\nyellow: ''")
player = Path('backend/config/player.yml')
if not player.exists():
    player.write_text('obs_1: true\nobs_2: false\nobs_3: false\nobs_4: false\nplayer_count: 1\nremote_1: true\nremote_2: false\nremote_3: false\nremote_4: false')
obs = Path('backend/config/obs_config.yml')
if not obs.exists():
    obs.write_text("host: '127.0.0.1'\npassword: ''\nport: '4444'")
biz = Path('backend/config/bh_config.yml')
if not biz.exists():
    biz.write_text("host: 127.0.0.1\npath: ''\nport: '43885'")
remote = Path('backend/config/remote.yml')
if not remote.exists():
    remote.write_text("ip_adresse_1: ''\nip_adresse_2: ''\nip_adresse_3: ''\nip_adresse_4: ''\nport_1: ''\nport_2: ''\nport_3: ''\nport_4: ''")



import frontend.app as FEApp
import asyncio

def main():
    app = FEApp.TrackerApp()
    asyncio.run(app.async_run())

if __name__ == '__main__':
    main()