import asyncio
import tkinter as tk
import pickle
import simpleobsws
from tkinter import ttk
import tkinter.filedialog as fd
import yaml
import subprocess
import os
from pathlib import Path
log = []
ws = None
teams = {}
unsorted_teams = {}
badges = {}


def load_obsws():
    global ws
    if not ws or not ws.is_identified():
        ws = simpleobsws.WebSocketClient(url=f'ws://localhost:{obsport.get()}', password=obspassword.get(), identification_parameters=simpleobsws.IdentificationParameters(ignoreNonFatalRequestChecks=False))
        asyncio.ensure_future(connect_to_obs())


async def connect_to_obs():
    try:
        await ws.connect()
        await ws.wait_until_identified()
        await redraw_obs()
        log.append('connected to OBS\n')
    except Exception as err:
        log.append(err + '\n')


async def redraw_obs():
    if ws and ws.is_identified():
        for player in teams:
            await changeSource(player, range(6), teams[player], edition=33)
            await change_badges(player)


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
    if not ws or not ws.is_identified():
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
                        "inputName": f"item{slot + 6 * (player - 1) + 1}",
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


async def change_badges(player):
    if not handle_badges.get():
        return
    if not ws or not ws.is_identified():
        log.append('not changing badges' + '\n')
        return
    log.append(f'changing badges {bin(badges[player])}' + '\n')
    batch = []
    for i in range(16):
        if badges[player] & 2**i:
            batch.append(
                simpleobsws.Request(
                    "SetInputSettings",
                    {
                        "inputName": f"badge{i + 6 * (player - 1) + 1}",
                        "inputSettings": {
                            "file": "C:/Users/Flori/OneDrive/Dokumente/GitHub/sprites/sprites/badges" + '/' + str(i + 1) + ".png"
                        }
                    }
                )
            )
        else:
            batch.append(
                simpleobsws.Request(
                    "SetInputSettings",
                    {
                        "inputName": f"badge{i + 6 * (player - 1) + 1}",
                        "inputSettings": {
                            "file": "C:/Users/Flori/OneDrive/Dokumente/GitHub/sprites/sprites/badges" + '/' + str(i + 1) + 'empty' + ".png"
                        }
                    }
                )
            )

    await ws.call_batch(batch)


async def indeedee():
    global teams
    global unsorted_teams
    global badges
    reader, writer = await asyncio.open_connection(ip, port)
    writer.write(b'\x00\x00')
    await writer.drain()

    while True:
        try:
            length = int.from_bytes(await reader.read(3), 'big')
            unsorted_teams = pickle.loads(await reader.read(length))
            new_teams = unsorted_teams.copy()
            for player in new_teams:
                team = new_teams[player]
                new_teams[player] = sort(team[:6], order.get())
                if player not in badges or unsorted_teams[player][6] != badges[player]:
                    badges[player] = unsorted_teams[player][6]
                    await change_badges(player)
            if new_teams != teams:
                for player in new_teams:
                    if player not in teams:
                        await changeSource(player, range(6), new_teams[player], edition=33)
                        continue

                    diff = []
                    team = new_teams[player]
                    old_team = teams[player]
                    for i in range(6):
                        if team[i] != old_team[i]:
                            diff.append(i)
                    await changeSource(player, diff, team, edition=33)
                teams = new_teams.copy()
        except Exception as err:
            log.append(err + '\n')
            break


async def tk_main(root):
    old_order = order.get()
    while True:
        root.update()
        t = ''
        for line in log:
            t += line
        info.configure(text=str(teams) + '\n' + str(badges) + '\n' + t)
        await asyncio.sleep(0.05)

        if old_order != order.get():
            old_order = order.get()
            await redraw_obs()


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
    config['last_game'] = game.get()
    config['show_badges'] = handle_badges.get()

    with open('config.yml', 'w') as file:
        yaml.dump(config, file)


iddmain = None


def connect_indeedee():
    global iddmain
    if iddmain:
        iddmain.cancel()

    iddmain = asyncio.ensure_future(indeedee())


def setaddr(*args):
    global ip, port
    string = address.get()
    try:
        if string[0] != '[':
            ip, port = string.split(':')
            proxy.set(0)
        else:
            ip, port = string[1:].split(']:')
            proxy.set(1)
    except Exception as err:
        log.append(err + '\n')


def openbiz():
    bip = '127.0.0.1' if proxy.get() else ip
    bport = int(port) + proxy.get()
    player = selectedplayer.get()
    if player in range(1, 256) and not Path(f'Player{player}.lua').exists():
        Path(f'Player{player}.lua').write_text(f'PLAYER={player}\ngui.drawText(10,10, "Player {player}")\npackage.path = "./obsautomation.lua;"\nconnect = loadfile(\'obsautomation.lua\')\nconnect()')
    subprocess.Popen([emu_path.get(), f'--lua={os.path.abspath(f"Player{player}.lua")}', f'--socket_ip={bip}', f'--socket_port={bport}', game.get()])


def change_order(*args):
    for team in teams:
        teams[team] = sort(unsorted_teams[team][:6], order.get())


proxyserver = None


def toggle_proxy(*args):
    global proxyserver
    if proxy.get():
        proxyserver = asyncio.ensure_future(run_proxy())
    else:
        try:
            if proxyserver:
                proxyserver.cancel()
        except AttributeError as err:
            print(err)
            pass


async def run_proxy():
    async def bizreader(reader, _):
        msg = await reader.read(1322)
        writer.write(msg)
        await writer.drain()
    while True:
        _, writer = await asyncio.open_connection(ip, port)
        server = await asyncio.start_server(bizreader, '127.0.0.1', int(port) + 1)
        async with server:
            await server.serve_forever()


if __name__ == '__main__':
    root = tk.Tk()
    root.title('Munchlax')
    root.minsize(400, 340)
    root.protocol("WM_DELETE_WINDOW", on_closing)
    if Path('config.yml').exists():
        with open('config.yml') as file:
            config = yaml.safe_load(file)
    else:
        config = {
            'animated': 0,
            'indeedeeaddress': '',
            'items': 1,
            'itemspath': '',
            'nicknames': 1,
            'obspassword': '',
            'obsport': '',
            'order': 'DexNr.',
            'spritespath': '',
            'emupath': '',
            'last_game': '',
            'show_badges': 0,

        }
    proxy = tk.IntVar()
    proxy.trace_add('write', toggle_proxy)
    address = tk.StringVar()
    address.trace_add('write', setaddr)
    address.set(config['indeedeeaddress'])
    obsport = tk.IntVar(value=config['obsport'])
    obspassword = tk.StringVar(value=config['obspassword'])
    spritespath = tk.StringVar(value=config['spritespath'])
    order = tk.StringVar(value=config['order'])
    order.trace_add('write', change_order)
    animated = tk.IntVar(value=config['animated'])
    show_nicknames = tk.IntVar(value=config['nicknames'])
    show_items = tk.IntVar(value=config['items'])
    items_path = tk.StringVar(value=config['itemspath'])
    emu_path = tk.StringVar(value=config['emupath'])
    game = tk.StringVar(value=config['last_game'])
    handle_badges = tk.IntVar(value=config['show_badges'])
    selectedplayer = tk.IntVar(value=1)
    selectedplayer.trace_add('write', lambda *x: bizbutton.configure(text=f'Launch Emulator for Player {selectedplayer.get()}'))

    tkmain = asyncio.ensure_future(tk_main(root))

    m = tk.Menu(root, tearoff=0)
    m.add_command(label='Cut')
    m.add_command(label='Copy')
    m.add_command(label='Paste')

    def show_menu(event):
        m.entryconfigure('Cut', command=lambda: event.widget.event_generate('<<Cut>>'))
        m.entryconfigure('Copy', command=lambda: event.widget.event_generate('<<Copy>>'))
        m.entryconfigure('Paste', command=lambda: event.widget.event_generate('<<Paste>>'))
        m.tk.call('tk_popup', m, event.x_root, event.y_root)

    ttk.Entry().bind_class('TEntry', '<Button-3>', show_menu)

    iddframe = tk.LabelFrame(root, text='Indeedee')
    iddframe.pack(fill='both', expand=True)
    ttk.Label(iddframe, text='Address: ').pack(side='left')
    ttk.Entry(iddframe, textvariable=address).pack(fill='x', side='left', expand=True)
    ttk.Button(iddframe, text='Connect', command=connect_indeedee).pack(side='left')

    obsframe = tk.LabelFrame(root, text='OBS')
    obsframe.pack(fill='both', expand=True)
    ttk.Label(obsframe, text='Port:').pack(side='left')
    ttk.Entry(obsframe, textvariable=obsport).pack(side='left', fill='x', expand=True)

    ttk.Label(obsframe, text='Password:').pack(side='left')
    ttk.Entry(obsframe, textvariable=obspassword, show='\u25CF').pack(side='left', fill='x', expand=True)

    ttk.Button(obsframe, text='Connect', command=load_obsws).pack(side='left')

    bizframe = tk.LabelFrame(root, text='BizHawk')
    bizframe.pack(fill='both', expand=True)
    frame = ttk.Frame(bizframe)
    frame.pack(expand=True, fill='both')
    ttk.Label(frame, text='EmuHawk.exe: ').pack(side='left')
    ttk.Entry(frame, textvariable=emu_path).pack(side='left', fill='x', expand=True)

    ttk.Button(frame, text='Browse', command=lambda var=emu_path: var.set(fd.askopenfilename())).pack(side='left')
    frame = ttk.Frame(bizframe)
    frame.pack(expand=True, fill='both')
    ttk.Label(frame, text='ROM Path:        ').pack(side='left')
    ttk.Entry(frame, textvariable=game).pack(side='left', fill='x', expand=True)

    ttk.Button(frame, text='Browse', command=lambda var=game: var.set(fd.askopenfilename())).pack(side='left')
    frame = ttk.Frame(bizframe)
    frame.pack(expand=True, fill='both')
    # ttk.Checkbutton(frame, text='Connect BizHawk\ndirectly to Indeedee', variable=proxy).pack(side='left')
    ttk.Label(frame, text='Player: ').pack(side='left')
    ttk.Combobox(frame, textvariable=selectedplayer, values=[1, 2, 3, 4]).pack(side='left', fill='x', expand=True)
    bizbutton = ttk.Button(frame, text='Launch Emulator for Player 1', command=lambda: openbiz())
    bizbutton.pack(side='left')

    spriteframe = tk.LabelFrame(root, text='Sprites')
    spriteframe.pack(fill='both', expand=True)
    frame = ttk.Frame(spriteframe)
    frame.pack(expand=True, fill='both')
    ttk.Label(frame, text='Pokemon Sprites: ').pack(side='left')
    ttk.Entry(frame, textvariable=spritespath).pack(side='left', fill='x', expand=True)

    ttk.Button(frame, text='Browse', command=lambda var=spritespath: var.set(fd.askdirectory())).pack(side='left')
    frame = ttk.Frame(spriteframe)
    frame.pack(expand=True, fill='both')
    ttk.Label(frame, text='Item Sprites:          ').pack(side='left')
    ttk.Entry(frame, textvariable=items_path).pack(side='left', fill='x', expand=True)

    ttk.Button(frame, text='Browse', command=lambda var=items_path: var.set(fd.askdirectory())).pack(side='left')

    ttk.Label(spriteframe, text='Order: ').pack(side='left')
    ttk.Combobox(spriteframe, textvariable=order, state='readonly', values=['DexNr.', 'Team', 'Level', 'Route']).pack(side='left')
    ttk.Checkbutton(spriteframe, text='Animated Sprites', variable=animated).pack()
    ttk.Checkbutton(spriteframe, text='Show Names       ', variable=show_nicknames).pack()
    ttk.Checkbutton(spriteframe, text='Show Items        \u2009\u2009', variable=show_items).pack()
    ttk.Checkbutton(spriteframe, text='Show Badges      \u2009', variable=handle_badges).pack()
    ttk.Button(root, text='Save Settings', command=save_config).pack(side='bottom')
    info = ttk.Label(text='INFO')
    # info.pack()

    loop = asyncio.get_event_loop()

    try:
        loop.run_forever()
    except KeyboardInterrupt:
        pass

    tkmain.cancel()
