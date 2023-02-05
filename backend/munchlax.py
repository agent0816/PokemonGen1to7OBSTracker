import asyncio
import pickle
import simpleobsws
import yaml
import subprocess
import os
from pathlib import Path

import tkinter as tk
from tkinter import ttk
import tkinter.filedialog as fd


ws = None
teams = {}
unsorted_teams = {}
badges = {}


def load_obsws():
    global ws

    async def connect_to_obs():
        try:
            await ws.connect()  # type:ignore
            await ws.wait_until_identified()  # type:ignore
            await redraw_obs()
        except Exception as err:
            pass

    if not ws or not ws.is_identified():
        ws = simpleobsws.WebSocketClient(url=f'ws://localhost:{obsport.get()}', password=obspassword.get(), identification_parameters=simpleobsws.IdentificationParameters(ignoreNonFatalRequestChecks=False))
        asyncio.ensure_future(connect_to_obs())


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
    return
    if not show_badges.get():
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
                            "file": badges_path.get() + '/' + str(i + 1) + ".png"
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
                            "file": badges_path.get() + '/' + str(i + 1) + 'empty' + ".png"
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
            log.append(str(err) + '\n')
            break



iddmain = None


def connect_indeedee():
    global iddmain
    if iddmain:
        iddmain.cancel()

    iddmain = asyncio.ensure_future(indeedee())



def change_order(*args):
    for team in teams:
        teams[team] = sort(unsorted_teams[team][:6], order.get())




if __name__ == '__main__':



    loop = asyncio.get_event_loop()

    try:
        loop.run_forever()
    except KeyboardInterrupt:
        pass
