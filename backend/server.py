import logging
logging.basicConfig(level=logging.DEBUG, filename='logs/server.log',format='[%(asctime)s] %(levelname)s: %(message)s', filemode='w', encoding='utf-8')
import asyncio
import yaml
import simpleobsws
from simpleobsws import WebSocketClient
import backend.pokedecoder as pokedecoder

#from backend.citra import Citra

global configsave
configsave = 'backend/config/'

SPIELERANZAHL = 4
with open(f'{configsave}sprites.yml') as file:
    spriteconf = yaml.safe_load(file)
with open(f'{configsave}bh_config.yml') as file:
    bizhawk_config = yaml.safe_load(file)



# ws = None

def update_config():
    global spriteconf
    global bizhawk_config
    with open(f'{configsave}sprites.yml') as file:
        spriteconf = yaml.safe_load(file)
    with open(f'{configsave}bh_config.yml') as file:
        bizhawk_config = yaml.safe_load(file)

def load_obsws():
    with open(f'{configsave}obs_config.yml') as file:
        obsconf = yaml.safe_load(file)
    global ws
    if obsconf['host'] not in [None,''] and obsconf['port'] not in [None,'']:
        ws = simpleobsws.WebSocketClient(url = 'ws://' + obsconf['host'] + ':' + obsconf['port'], password = obsconf['password'], identification_parameters = simpleobsws.IdentificationParameters(ignoreNonFatalRequestChecks = False))
    else:
        ws = None


async def connect_to_obs():
    await ws.connect() #type: ignore
    await ws.wait_until_identified() #type: ignore


async def changeSource(player, slots, team, edition):
    batch = []
    if spriteconf['edition_override'] != '':
        edition = max(edition, spriteconf['edition_override'])
    for slot in slots:
        sprite = get_sprite(team[slot], spriteconf['animated'], edition)

        batch.append(simpleobsws.Request('SetInputSettings', {'inputName':f'Slot{slot + 6 * (player -1) +1}', 'inputSettings':{'file': sprite}}))
    if spriteconf['show_nicknames']:
        for slot in slots:
            batch.append(simpleobsws.Request('SetInputSettings', {'inputName': f'name{slot + 6 * (player -1) +1}', 'inputSettings': {'text': team[slot].nickname}}))
        
    if batch != []:
        await ws.call_batch(batch) #type: ignore

luts = yaml.safe_load(open('backend/data/luts.yml'))

edition_lut = luts['edition_lut']

female_lut = luts['female_lut']

def get_sprite(pokemon, anim, edition):
    
    shiny = 'shiny/' if pokemon.shiny else ''
    if pokemon.female and pokemon.dexnr in female_lut:
        female = 'female/' 
    else:
        female = ''
    if anim and edition in (23, 33, 41, 42, 43, 44, 45, 51, 52, 53, 54):
        filetype = '.gif'
        animated = 'animated/'
    else:
        animated = ''
        filetype = '.png'
    path = spriteconf['common_path'] + spriteconf[edition_lut[edition]] + animated + shiny + female
    file = str(pokemon.dexnr) + pokemon.form + filetype
    return path + file


async def hide_nicknames():
    batch = []
    for i in range(24):
        batch.append(simpleobsws.Request('SetInputSettings', {'inputName': f'name{i + 1}', 'inputSettings': {'text': ''}}))
    await ws.call_batch(batch) #type: ignore

async def bizhawk_server():
    server = await asyncio.start_server(handle_client, bizhawk_config['host'], bizhawk_config['port'])
    async with server:
        await server.serve_forever()

teams = [[]] * SPIELERANZAHL
editions = [[]] * SPIELERANZAHL
update = False
async def handle_client(reader, writer):
    global update
    request = None
    while request != 'exit':
        if update:
            update = False
            for i in range(SPIELERANZAHL):
                if teams[i] != []:
                    await changeSource(i, range(6), teams[i], editions[i])
        header =  await reader.read(2)
        player = header[1]
        edition = header[0]
        editions[player] = edition
        logging.debug(f'{player=}{edition=}')

        if edition < 20:
            msg = await reader.read(330)
            team = pokedecoder.team(msg, 1)
        elif edition < 30:
            msg = await reader.read(360)
            team = pokedecoder.team(msg, 2)
        elif edition < 40:
            msg = await reader.read(600)
            team = pokedecoder.team(msg, 3, edition)
        elif edition < 50:
            msg = await reader.read(1416)
            team = pokedecoder.team(msg, 4)
        elif edition < 60:
            msg = await reader.read(1320)
            team = pokedecoder.team(msg, 5)

        logging.debug(f'{team=}') # type: ignore
        if teams[player] == []:
            teams[player] = team # type: ignore
            await changeSource(player, range(6), team, edition) # type: ignore

        else:
            diff =[]
            for i in range(6):
                if teams[player][i] != team[i]: # type: ignore
                    diff.append(i)
            teams[player] = team # type: ignore
            await changeSource(player, diff, team, edition) # type: ignore
    writer.close()

async def start():
    load_obsws()
    await connect_to_obs()
#    await citra()
    await bizhawk_server()

async def citra():
    pointer = 0x8CE1CE8
    c = Citra()
    team = []
    while True:
        party = b''
        for i in range(6):
            pokemon = c.read_memory(pointer + i * 484, 232)
            battle_stats = c.read_memory(pointer + i * 484 + 112, 28)
            party += pokemon + battle_stats

        if team == []:
            print('first')
            team = pokedecoder.team(party, 6)
            for p in team:
                print(p)
            await changeSource(1, range(6), team, 51)

        else:
            diff =[]
            new_team =pokedecoder.team(party, 6)
            for i in range(6):
                if team[i] != new_team[i]:
                    diff.append(i)
            team = new_team
            if diff !=[]:
                print('new')
                print(team)
                await changeSource(1, diff, team, 51)

def main():
    asyncio.run(start())