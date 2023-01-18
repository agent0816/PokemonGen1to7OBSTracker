import asyncio
import tkinter as tk
import pickle
import simpleobsws
from tkinter import ttk
import tkinter.filedialog as fd
import yaml
import subprocess
import os

ws = None
teams = {}


def load_obsws():
    global ws
    if not ws or not ws.is_identified():
        ws = simpleobsws.WebSocketClient(url=f'ws://localhost:{obsport.get()}', password=obspassword.get(), identification_parameters=simpleobsws.IdentificationParameters(ignoreNonFatalRequestChecks=False))
        asyncio.ensure_future(connect_to_obs())


async def connect_to_obs():
    try:
        await ws.connect()
        await ws.wait_until_identified()
        for player in teams:
            await changeSource(player, range(6), teams[player], edition=33)
    except:
        pass


def get_sprite(pokemon, anim, edition):
    shiny = "shiny/" if pokemon.shiny else ""
    if pokemon.female and pokemon.dexnr in [3, 12, 19, 20, 25, 26, 41, 42, 44, 45, 64, 65, 84, 85, 97, 111, 112, 118, 119, 123, 129, 130, 154, 165, 166, 178, 185, 186, 190, 194, 195, 198, 202, 203, 207, 208, 212, 214, 215, 215, 217, 221, 224, 229, 232, 255, 256, 257, 267, 269, 272, 274, 275, 307, 308, 315, 316, 317, 322, 323, 332, 350, 369, 396, 397, 398, 399, 400, 401, 402, 403, 404, 405, 407, 415, 417, 418, 419, 424, 443, 444, 445, 449, 450, 453, 454, 456, 457, 459, 460, 461, 464, 465, 473, 521, 592, 593, 668, 678, 876, 902]:
        female = "female/"
    else:
        female = ""
    if anim and edition in (23, 33, 41, 42, 43, 44, 45, 51, 52, 53, 54):
        filetype = ".gif"
        animated = "animated/"
    else:
        animated = ""
        filetype = ".png"
    path = (
        spritespath.get() + '/'
        + animated
        + shiny
        + female
    )
    file = str(pokemon.dexnr) + pokemon.form + filetype
    return path + file


async def changeSource(player, slots, team, edition):
    if not ws:
        return
    if not ws.is_identified():
        return
    batch = []
    for slot in slots:
        sprite = get_sprite(team[slot], animated.get(), edition)

        batch.append(
            simpleobsws.Request(
                "SetInputSettings",
                {
                    "inputName": f"Slot{slot + 6 * (player -1) +1}",
                    "inputSettings": {"file": sprite},
                },
            )
        )
    if show_nicknames.get():
        for slot in slots:
            batch.append(
                simpleobsws.Request(
                    "SetInputSettings",
                    {
                        "inputName": f"name{slot + 6 * (player -1) +1}",
                        "inputSettings": {"text": team[slot].nickname},
                    },
                )
            )
    if show_items.get() and edition > 20:
        for slot in slots:
            batch.append(
                simpleobsws.Request(
                    "SetInputSettings",
                    {
                        "inputName": f"item{slot + 6 * (player -1) +1}",
                        "inputSettings": {
                            "file": items_path.get() + '/'
                            + str(team[slot].item)
                            + ".png"
                        },
                    },
                )
            )

    if batch != []:
        await ws.call_batch(batch)


def sort(liste, key):
    key = key.lower().replace('.', '')
    if key == 'dexnr':
        return sorted(sorted(liste), key=lambda a: a)
    if key == 'team':
        return liste
    if key == 'level':
        return sorted(sorted(liste), key=lambda a: - a.lvl if a.dexnr != 0 else 999999)
    if key == 'route':
        return sorted(sorted(liste), key=lambda a: a.route if a.dexnr != 0 else 999999)


async def indeedee():
    global teams
    reader, writer = await asyncio.open_connection(ip, port)
    writer.write(b'\x00\x00')
    await writer.drain()
    response = await reader.read(4096)
    teams = pickle.loads(response)
    old_teams = teams.copy()
    for player in teams:
        await changeSource(player, range(6), teams[player], edition=33)
    while True:
        try:
            response = await reader.read(4096)
            teams = pickle.loads(response)
            if old_teams != teams:
                for player in teams:
                    diff = []
                    teams[player] = sort(teams[player], order.get())
                    team = teams[player]
                    print(team)
                    old_team = old_teams[player]
                    for i in range(6):
                        if team[i] != old_team[i]:
                            diff.append(i)
                    await changeSource(player, diff, team, edition=33)
            old_teams = teams.copy()
        except Exception:
            break


async def tk_main(root):
    while True:
        root.update()
        await asyncio.sleep(0.05)


def on_closing():
    asyncio.get_running_loop().stop()


def save_config():
    config['indeedeeaddress'] = address.get()
    config['obsport'] = obsport.get()
    config['obspassword'] = obspassword.get()
    config['spritespath'] = spritespath.get()
    config['order'] = order.get()
    config['animated'] = animated.get()
    config['nicknames'] = show_nicknames.get()
    config['items'] = show_items.get()
    config['itemspath'] = items_path.get()
    config['emupath'] = emu_path.get()

    with open('config.yml', 'w') as file:
        yaml.dump(config, file)


iddmain = None


def connect_indeedee():
    global iddmain
    try:
        iddmain.cancel()
    except:
        pass

    iddmain = asyncio.ensure_future(indeedee())


def setaddr(*args):
    global ip, port
    ip, port = address.get().split(':')


def openbiz():
    print(ip, port)
    subprocess.Popen([emu_path.get(), f'--lua={os.path.abspath(f"./backend/Player{selectedplayer.get()}.lua")}', f'--socket_ip={ip}', f'--socket_port={port}', game.get()])


if __name__ == '__main__':
    root = tk.Tk()
    root.protocol("WM_DELETE_WINDOW", on_closing)

    with open('config.yml') as file:
        config = yaml.safe_load(file)
    address = tk.StringVar()
    address.trace_add('write', setaddr)
    address.set(config['indeedeeaddress'])
    obsport = tk.IntVar(value=config['obsport'])
    obspassword = tk.StringVar(value=config['obspassword'])
    spritespath = tk.StringVar(value=config['spritespath'])
    order = tk.StringVar(value=config['order'])
    animated = tk.IntVar(value=config['animated'])
    show_nicknames = tk.IntVar(value=config['nicknames'])
    show_items = tk.IntVar(value=config['items'])
    items_path = tk.StringVar(value=config['itemspath'])
    emu_path = tk.StringVar(value=config['emupath'])
    game = tk.StringVar(value=config['last_game'])
    selectedplayer = tk.IntVar(value=1)
    selectedplayer.trace_add('write', lambda *x: bizbutton.configure(text=f'Launch Emulator for Player {selectedplayer.get()}'))

    tkmain = asyncio.ensure_future(tk_main(root))

    iddframe = tk.Frame(root)
    iddframe.pack()
    ttk.Label(iddframe, text='Indeedee Address: ').pack()
    ttk.Entry(iddframe, textvariable=address).pack()
    ttk.Button(iddframe, text='Connent', command=connect_indeedee).pack()
    obsframe = tk.Frame(root)
    obsframe.pack()
    ttk.Label(obsframe, text='OBS Port:').pack()
    ttk.Entry(obsframe, textvariable=obsport).pack()
    ttk.Label(obsframe, text='OBS Password:').pack()
    ttk.Entry(obsframe, textvariable=obspassword, show='\u25CF').pack()
    ttk.Button(obsframe, text='Connent', command=load_obsws).pack()
    bizframe = tk.Frame(root)
    bizframe.pack()
    ttk.Label(bizframe, text='EmuHawk.exe: ').pack()
    ttk.Entry(bizframe, textvariable=emu_path).pack()
    ttk.Button(bizframe, text='Browse', command=lambda var=emu_path: var.set(fd.askopenfilename())).pack()
    ttk.Label(bizframe, text='Player: ').pack()
    ttk.Combobox(bizframe, textvariable=selectedplayer, values=[1, 2, 3, 4]).pack()
    ttk.Label(bizframe, text='ROM Path: ').pack()
    ttk.Entry(bizframe, textvariable=game).pack()
    ttk.Button(bizframe, text='Browse', command=lambda var=game: var.set(fd.askopenfilename())).pack()
    bizbutton = ttk.Button(bizframe, text='Launch Emulator for Player 1', command=lambda: openbiz())
    bizbutton.pack()
    spriteframe = tk.Frame(root)
    spriteframe.pack()
    ttk.Label(spriteframe, text='Pokemon Sprites: ').pack()
    ttk.Entry(spriteframe, textvariable=spritespath).pack()
    ttk.Button(spriteframe, text='Browse', command=lambda var=spritespath: var.set(fd.askdirectory())).pack()
    ttk.Label(spriteframe, text='Order: ').pack()
    ttk.Combobox(spriteframe, textvariable=order, state='readonly', values=['DexNr.', 'Team', 'Level', 'Route']).pack()
    ttk.Checkbutton(spriteframe, text='Animated Sprites', variable=animated).pack()
    ttk.Checkbutton(spriteframe, text='Show Names', variable=show_nicknames).pack()
    ttk.Checkbutton(spriteframe, text='Show Items', variable=show_items).pack()
    ttk.Label(spriteframe, text='Item Sprites: ').pack()
    ttk.Entry(spriteframe, textvariable=items_path).pack()
    ttk.Button(spriteframe, text='Browse', command=lambda var=items_path: var.set(fd.askdirectory())).pack()
    ttk.Button(root, text='Save Settings', command=save_config).pack()

    loop = asyncio.get_event_loop()

    try:
        loop.run_forever()
    except KeyboardInterrupt:
        pass

    tkmain.cancel()
