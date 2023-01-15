import os
import subprocess
import yaml
import backend.server as server
import backend.pokedecoder
import threading
import tkinter as tk
from tkinter import ttk
import tkinter.filedialog as fd
connector = threading.Thread(target=server.main, args=(), daemon=True)


def launchserver():
    global connector
    try:
        connector.start()
    except RuntimeError:
        server.running = False
        connector = threading.Thread(target=server.main, args=(), daemon=True)
        connector.start()


def launchbh():
    for i in range(playercount.get()):
        if not playerconfig[f'remote_{i+1}']:
            subprocess.Popen([bizpath.get(), f'--lua={os.path.abspath(f"./backend/Player{i+1}.lua")}', '--socket_ip=127.0.0.1', f'--socket_port={bhport.get()}'])


def save_config(dic, key, val, path):
    dic[key] = val
    with open(path, 'w') as file:
        yaml.dump(dic, file)
    server.update = True
    if key == 'player_count':
        remote()


# Die beiden Funktionen sind nicht nötig, wenn man die config ändert


def save_remote(*args):
    for i, player in zip(range(1, 5), (player1, player2, player3, player4)):
        if player.get() == 'local':
            playerconfig[f'remote_{i}'] = False
        elif player.get() == 'remote':
            playerconfig[f'remote_{i}'] = True
            playerconfig[f'obs_{i}'] = True
        elif player.get() == 'no OBS':
            playerconfig[f'remote_{i}'] = True
            playerconfig[f'obs_{i}'] = False

    with open('backend/config/player.yml', 'w') as file:
        yaml.dump(playerconfig, file)


def load_remote(player):
    if playerconfig[f'remote_{player}']:
        if playerconfig[f'obs_{player}']:
            return 'remote'
        else:
            return 'no OBS'
    else:
        return'local'


root = tk.Tk()
root.geometry('400x250')
root.title("Main Menu")

# config
with open("backend/config/sprites.yml") as file:
    spriteconfig = yaml.safe_load(file)
with open("backend/config/bh_config.yml") as file:
    bhconfig = yaml.safe_load(file)
with open("backend/config/obs_config.yml") as file:
    obsconfig = yaml.safe_load(file)
with open("backend/config/player.yml") as file:
    playerconfig = yaml.safe_load(file)
with open("backend/config/remote.yml") as file:
    remoteconfig = yaml.safe_load(file)

# Variablen
spritepath = tk.StringVar(value=spriteconfig['common_path'])
spritepath.trace_add('write', lambda *x: save_config(spriteconfig, 'common_path', spritepath.get(), 'backend/config/sprites.yml'))
animated = tk.IntVar(value=spriteconfig['animated'])
animated.trace_add('write', lambda *x: save_config(spriteconfig, 'animated', animated.get(), 'backend/config/sprites.yml'))
sort = tk.StringVar(value=spriteconfig['order'])
sort.trace_add('write', lambda *x: save_config(spriteconfig, 'order', sort.get().lower().replace('.', ''), 'backend/config/sprites.yml'))
bizpath = tk.StringVar(value=bhconfig['path'])
bizpath.trace_add('write', lambda *x: save_config(bhconfig, 'path', bizpath.get(), 'backend/config/bh_config.yml'))
bhport = tk.StringVar(value=bhconfig['port'])
bhport.trace_add('write', lambda *x: save_config(bhconfig, 'port', bhport.get(), 'backend/config/bh_config.yml'))
obsport = tk.StringVar(value=obsconfig['port'])
obsport.trace_add('write', lambda *x: save_config(obsconfig, 'port', obsport.get(), 'backend/config/obs_config.yml'))
obspw = tk.StringVar(value=obsconfig['password'])
obspw.trace_add('write', lambda *x: save_config(obsconfig, 'password', obspw.get(), 'backend/config/obs_config.yml'))
playercount = tk.IntVar(value=playerconfig['player_count'])
playercount.trace_add('write', lambda *x: save_config(playerconfig, 'player_count', playercount.get(), 'backend/config/player.yml'))
player1 = tk.StringVar(value=load_remote(1))
player1.trace_add('write', save_remote)
player2 = tk.StringVar(value=load_remote(2))
player2.trace_add('write', save_remote)
player3 = tk.StringVar(value=load_remote(3))
player3.trace_add('write', save_remote)
player4 = tk.StringVar(value=load_remote(4))
player4.trace_add('write', save_remote)
IP1 = tk.StringVar(value=remoteconfig['ip_adresse_1'])
IP1.trace_add('write', lambda *x: save_config(remoteconfig, 'ip_adresse_1', IP1.get(), 'backend/config/remote.yml'))
IP2 = tk.StringVar(value=remoteconfig['ip_adresse_2'])
IP2.trace_add('write', lambda *x: save_config(remoteconfig, 'ip_adresse_2', IP2.get(), 'backend/config/remote.yml'))
IP3 = tk.StringVar(value=remoteconfig['ip_adresse_3'])
IP3.trace_add('write', lambda *x: save_config(remoteconfig, 'ip_adresse_3', IP3.get(), 'backend/config/remote.yml'))
IP4 = tk.StringVar(value=remoteconfig['ip_adresse_4'])
IP4.trace_add('write', lambda *x: save_config(remoteconfig, 'ip_adresse_4', IP4.get(), 'backend/config/remote.yml'))
Port1 = tk.IntVar(value=remoteconfig['port_1'])
Port1.trace_add('write', lambda *x: save_config(remoteconfig, 'port_1', Port1.get(), 'backend/config/remote.yml'))
Port2 = tk.IntVar(value=remoteconfig['port_2'])
Port2.trace_add('write', lambda *x: save_config(remoteconfig, 'port_2', Port2.get(), 'backend/config/remote.yml'))
Port3 = tk.IntVar(value=remoteconfig['port_3'])
Port3.trace_add('write', lambda *x: save_config(remoteconfig, 'port_3', Port3.get(), 'backend/config/remote.yml'))
Port4 = tk.IntVar(value=remoteconfig['port_4'])
Port4.trace_add('write', lambda *x: save_config(remoteconfig, 'port_4', Port4.get(), 'backend/config/remote.yml'))

content = ttk.Notebook(root)
content.place(relheight=1, relwidth=1)
screens = {
    'Hauptmenü': ttk.Frame(content),
    'Sprite Pfade': ttk.Frame(content),
    'BizHawk': ttk.Frame(content),
    'OBS': ttk.Frame(content),
    'Remote': ttk.Frame(content),
}
for screen in screens:
    content.add(screens[screen], text=screen)

# Hauptmenü
screen = screens['Hauptmenü']

ttk.Button(screen, text="Start Server", command=launchserver).place(relheight=.2, relwidth=.4, anchor='sw', rely=1)
ttk.Button(screen, text="Start Emulator", command=launchbh).place(relheight=.2, relwidth=.4, anchor='se', relx=1, rely=1)

# SpritePfade
screen = screens['Sprite Pfade']

ttk.Label(screen, text='Sprites').place(relheight=.2, width=50)
ttk.Entry(screen, textvariable=spritepath).place(relheight=.2, relwidth=.7, x=50)
ttk.Button(screen, text='Browse', command=lambda var=spritepath: var.set(fd.askdirectory())).place(relheight=.2, relwidth=.2, relx=1, anchor='ne')

ttk.Checkbutton(screen, text='animierte Sprites', variable=animated).place(relheight=.2, relwidth=.4, rely=.3, relx=.4)

ttk.Label(screen, text='Sortierung').place(relheight=.2, width=60, rely=.6)
ttk.Combobox(screen, textvariable=sort, state='readonly', values=['DexNr.', 'Team', 'Level', 'Route']).place(x=60, relheight=.2, relwidth=.4, rely=.6)

# BizHawk
screen = screens['BizHawk']

ttk.Label(screen, text='EmuHawk.exe:').place(relheight=.2, width=90)
ttk.Entry(screen, textvariable=bizpath).place(relheight=.2, relwidth=.7, x=90)
ttk.Button(screen, text='Browse', command=lambda var=bizpath: var.set(fd.askopenfilename())).place(relheight=.2, relwidth=.2, anchor='ne', relx=1)

ttk.Label(screen, text='Port:').place(relheight=.2, rely=.3, width=90)
ttk.Entry(screen, textvariable=bhport).place(relheight=.2, rely=.3, x=90, relwidth=.4)

# OBS
screen = screens['OBS']

ttk.Label(screen, text='Port:').place(relheight=.2, width=55)
ttk.Entry(screen, textvariable=obsport).place(relheight=.2, x=55, relwidth=.4)

ttk.Label(screen, text='Passwort').place(relheight=.2, rely=.3, width=55)
ttk.Entry(screen, textvariable=obspw, show='\u25CF').place(relheight=.2, rely=.3, x=55, relwidth=.4)

# Remote
screen = screens['Remote']


def remote():
    for w in (L2, L3, L4, C2, C3, C4, I2, I3, I4, EI2, EI3, EI4, P2, P3, P4, EP2, EP3, EP4):
        w.place_forget()

    L1.place(relheight=.2, width=55, rely=0.2)
    C1.place(x=55, relheight=.2, relwidth=.2, rely=.2)
    I1.place(relx=.35, width=50, rely=.2)
    EI1.place(relx=.45, relwidth=.3, rely=.2)
    P1.place(relx=.35, width=50, rely=.4, anchor='sw')
    EP1.place(relx=.45, relwidth=.3, rely=.4, anchor='sw')

    if playercount.get() == 1:
        return
    L2.place(relheight=.2, width=55, rely=0.4)
    C2.place(x=55, relheight=.2, relwidth=.2, rely=.4)
    I2.place(relx=.35, width=50, rely=.4)
    EI2.place(relx=.45, relwidth=.3, rely=.4)
    P2.place(relx=.35, width=50, rely=.6, anchor='sw')
    EP2.place(relx=.45, relwidth=.3, rely=.6, anchor='sw')

    if playercount.get() == 2:
        return
    L3.place(relheight=.2, width=55, rely=0.6)
    C3.place(x=55, relheight=.2, relwidth=.2, rely=.6)
    I3.place(relx=.35, width=50, rely=.6)
    EI3.place(relx=.45, relwidth=.3, rely=.6)
    P3.place(relx=.35, width=50, rely=.8, anchor='sw')
    EP3.place(relx=.45, relwidth=.3, rely=.8, anchor='sw')

    if playercount.get() == 3:
        return
    L4.place(relheight=.2, width=55, rely=0.8)
    C4.place(x=55, relheight=.2, relwidth=.2, rely=.8)
    I4.place(relx=.35, width=50, rely=.8)
    EI4.place(relx=.45, relwidth=.3, rely=.8)
    P4.place(relx=.35, width=50, rely=1, anchor='sw')
    EP4.place(relx=.45, relwidth=.3, rely=1, anchor='sw')


ttk.Label(screen, text='Spieleranzahl').place(relheight=.2, width=80)
ttk.Combobox(screen, textvariable=playercount, state='readonly', values=[1, 2, 3, 4]).place(x=80, relheight=.2, relwidth=.3)
L1 = ttk.Label(screen, text='Spieler1')
L2 = ttk.Label(screen, text='Spieler2')
L3 = ttk.Label(screen, text='Spieler3')
L4 = ttk.Label(screen, text='Spieler4')
C1 = ttk.Combobox(screen, textvariable=player1, state='readonly', values=['local', 'remote', 'no OBS'])
C2 = ttk.Combobox(screen, textvariable=player2, state='readonly', values=['local', 'remote', 'no OBS'])
C3 = ttk.Combobox(screen, textvariable=player3, state='readonly', values=['local', 'remote', 'no OBS'])
C4 = ttk.Combobox(screen, textvariable=player4, state='readonly', values=['local', 'remote', 'no OBS'])
I1 = ttk.Label(screen, text='IP')
I2 = ttk.Label(screen, text='IP')
I3 = ttk.Label(screen, text='IP')
I4 = ttk.Label(screen, text='IP')
EI1 = ttk.Entry(screen, textvariable=IP1)
EI2 = ttk.Entry(screen, textvariable=IP2)
EI3 = ttk.Entry(screen, textvariable=IP3)
EI4 = ttk.Entry(screen, textvariable=IP4)
P1 = ttk.Label(screen, text='Port')
P2 = ttk.Label(screen, text='Port')
P3 = ttk.Label(screen, text='Port')
P4 = ttk.Label(screen, text='Port')
EP1 = ttk.Entry(screen, textvariable=Port1)
EP2 = ttk.Entry(screen, textvariable=Port2)
EP3 = ttk.Entry(screen, textvariable=Port3)
EP4 = ttk.Entry(screen, textvariable=Port4)


remote()
root.mainloop()
