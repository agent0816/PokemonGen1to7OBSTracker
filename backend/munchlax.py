import asyncio
import pickle
import simpleobsws
import sys
import yaml
import subprocess
import os
from pathlib import Path
import logging

logger = logging.getLogger(__name__)
logger.addHandler(logging.StreamHandler(sys.stdout))
ws = None
unsorted_teams = {}
teams = {}
badges = {}
editions = {}
conf = {}

async def load_obsws(host, port, password):
    global ws

    if not ws or not ws.is_identified():
        ws = simpleobsws.WebSocketClient(url=f'ws://{host}:{port}', password=password, identification_parameters=simpleobsws.IdentificationParameters(ignoreNonFatalRequestChecks=False))
        try:
            await ws.connect()  # type:ignore
            await ws.wait_until_identified()  # type:ignore
            await redraw_obs()
            logger.info("obs connected.")
        except Exception as err:
            pass

async def redraw_obs():
    if ws and ws.is_identified():
        for player in teams:
            await changeSource(player, range(6), teams[player], edition=33)
            await change_badges(player)

def get_sprite(pokemon, anim, edition):
    shiny = "shiny/" if pokemon.shiny else ""
    subpath = {
        11: 'red',
        12: 'red',
        13: 'yellow',
        21: 'silver',
        22: 'gold',
        23: 'crystal',
        31: 'ruby',
        32: 'ruby',
        33: 'emerald',
        34: 'firered',
        35: 'firered',
        41: 'diamond',
        42: 'diamond',
        43: 'platinum',
        44: 'heartgold',
        45: 'heartgold',
        51: 'black',
        52: 'black',
        53: 'black',
        54: 'black',
    }
    if pokemon.female and edition > 40 and pokemon.dexnr in [3, 12, 19, 20, 25, 26, 41, 42, 44, 45, 64, 65, 84, 85, 97, 111, 112, 118, 119, 123, 129, 130, 154, 165, 166, 178, 185, 186, 190, 194, 195, 198, 202, 203, 207, 208, 212, 214, 215, 215, 217, 221, 224, 229, 232, 255, 256, 257, 267, 269, 272, 274, 275, 307, 308, 315, 316, 317, 322, 323, 332, 350, 369, 396, 397, 398, 399, 400, 401, 402, 403, 404, 405, 407, 415, 417, 418, 419, 424, 443, 444, 445, 449, 450, 453, 454, 456, 457, 459, 460, 461, 464, 465, 473, 521, 592, 593, 668, 678, 876, 902]:
        female = "female/"
    else:
        female = ""
    if anim and edition in (23, 33, 41, 42, 43, 44, 45, 51, 52, 53, 54):
        filetype = ".gif"
        animated = "animated/"
    else:
        animated = ""
        filetype = ".png"
    if not conf['single_path_check']:
        sub = conf[subpath[edition]]
    else:
        sub = ''
    path = (
        conf['common_path'] + '/'
        + sub
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
        sprite = get_sprite(team[slot], conf['animated'], edition)

        batch.append(
            simpleobsws.Request(
                "SetInputSettings",
                {
                    "inputName": f"Slot{slot + 6 * (player -1) +1}",
                    "inputSettings": {"file": sprite},
                },
            )
        )
    if conf['show_nicknames']:
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
    if conf['show_items'] and edition > 20:
        for slot in slots:
            batch.append(
                simpleobsws.Request(
                    "SetInputSettings",
                    {
                        "inputName": f"item{slot + 6 * (player - 1) + 1}",
                        "inputSettings": {
                            "file": conf['items_path'] + '/'
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
    if key == 'lvl':
        return sorted(sorted(liste), key=lambda a: - a.lvl if a.dexnr != 0 else 999999)
    if key == 'route':
        return sorted(sorted(liste), key=lambda a: a.route if a.dexnr != 0 else 999999)

async def change_badges(player):
    badge_lut = {
        11:'kanto',
        12:'kanto',
        13:'kanto',
        21:'johto',
        22:'johto',
        23:'johto',
        31:'hoenn',
        32:'hoenn',
        33:'hoenn',
        34:'kanto',
        35:'kanto',
        41:'sinnoh',
        42:'sinnoh',
        43:'sinnoh',
        44:'johto',
        45:'johto',
        51:'unova',
        52:'unova',
        53:'unova2',
        54:'unova2',

    }
    if not conf['show_badges']:
        return
    if not ws or not ws.is_identified():
        return
    batch = []
    for i in range(16):
        if (i == 0 and badges[player] & 2**i) ^ (i > 0 and badges[player] & 2**i and badges[player] & 2**(i-1)):
            batch.append(
                simpleobsws.Request(
                    "SetInputSettings",
                    {
                        "inputName": f"badge{i + 16 * (player - 1) + 1}",
                        "inputSettings": {
                            "file": conf['badges_path'] + '/' + badge_lut[editions[player]] + str(i + 1) + ".png"
                        }
                    }
                )
            )
        else:
            batch.append(
                simpleobsws.Request(
                    "SetInputSettings",
                    {
                        "inputName": f"badge{i + 16 * (player - 1) + 1}",
                        "inputSettings": {
                            "file": conf['badges_path'] + '/' + str(i + 1) + 'empty' + ".png"
                        }
                    }
                )
            )

    await ws.call_batch(batch)

async def pass_bh_to_server(server_address, port):
    async def bizreader(reader, _):
        while True:
            msg = await reader.read(1330)
            writer.write(msg)
            await writer.drain()

    _, writer = await asyncio.open_connection(*server_address)
    server = await asyncio.start_server(bizreader, '', port)
    async with server:
        await server.serve_forever()

async def connect_client(ip, port):
    global teams
    global unsorted_teams
    global badges
    global editions
    reader, writer = await asyncio.open_connection(ip, port)
    logger.info(f"client connected to {ip}:{port}")
    writer.write(b'\x00\x00')
    await writer.drain()

    while True:
        try:
            length = int.from_bytes(await reader.read(3), 'big')
            msg = await reader.read(length)
            unsorted_teams = pickle.loads(msg)
            logger.info(unsorted_teams)
            new_teams = unsorted_teams.copy()
            for player in new_teams:
                team = new_teams[player]
                new_teams[player] = sort(team[:6], conf['order'])
                if player not in badges or unsorted_teams[player][6] != badges[player]:
                    badges[player] = unsorted_teams[player][6]
                if player not in editions or unsorted_teams[player][7] != editions[player]:
                    editions[player] = unsorted_teams[player][7]
                    await change_badges(player)
            if new_teams != teams:
                for player in new_teams:
                    if player not in teams:
                        await changeSource(player, range(6), new_teams[player], editions[player])
                        continue

                    diff = []
                    team = new_teams[player]
                    old_team = teams[player]
                    for i in range(6):
                        if team[i] != old_team[i]:
                            diff.append(i)
                    await changeSource(player, diff, team, editions[player])
                    await change_badges(player)
                teams = new_teams.copy()
        except Exception as err:
            logger.error(err)
            break
        

def change_order(*args):
    for team in teams:
        teams[team] = sort(unsorted_teams[team][:6], conf['order'])

if __name__ == '__main__':
    loop = asyncio.get_event_loop()
    try:
        loop.run_forever()
    except KeyboardInterrupt:
        pass
