import logging
logging.basicConfig(level=logging.DEBUG, filename='server.log',format='[%(asctime)s] %(levelname)s: %(message)s', filemode='w', encoding='utf-8')
import asyncio
import yaml
import os
import simpleobsws
import pokedecoder

import requests

SPIELERANZAHL = 4

spriteconf = yaml.safe_load(open('config/sprites.yml'))
ws = None

def update_config():
    global spriteconf
    spriteconf = yaml.safe_load(open('config/sprites.yml'))


def load_obsws():
    obsconf = yaml.safe_load(open('config/obs_config.yml'))
    global ws
    ws = simpleobsws.WebSocketClient(url = 'ws://' + obsconf['host'] + ':' + obsconf['port'], password = obsconf['password'], identification_parameters = simpleobsws.IdentificationParameters(ignoreNonFatalRequestChecks = False))


async def connect_to_obs():
    await ws.connect()
    await ws.wait_until_identified()


async def changeSource(player, slots, team, edition):
    batch = []
    if spriteconf['edition_override'] != '':
        edition = spriteconf['edition_override']
    for slot in slots:
        sprite = get_sprite(team[slot], spriteconf['animated'], edition)

        batch.append(simpleobsws.Request('SetInputSettings', {'inputName':f'Slot{slot + 6 * (player -1) +1}', 'inputSettings':{'file': sprite}}))
    if spriteconf['show_nicknames']:
        for slot in slots:
            batch.append(simpleobsws.Request('SetInputSettings', {'inputName': f'name{slot + 6 * (player -1) +1}', 'inputSettings': {'text': team[slot].nickname}}))
        
    if batch != []:
        await ws.call_batch(batch)

path_lut = {
    11: '/generation-i/red-blue/transparent/',
    12: '/generation-i/red-blue/transparent/',
    13: '/generation-i/yellow/transparent/',
    21: '/generation-ii/gold/transparent/',
    22: '/generation-ii/silver/transparent/',
    23: '/generation-ii/crystal/transparent/',
    31: '/generation-iii/ruby-sapphire/',
    32: '/generation-iii/ruby-sapphire/',
    33: '/generation-iii/emerald/',
    34: '/generation-iii/firered-leafgreen/',
    35: '/generation-iii/firered-leafgreen/',
    41: '/generation-iv/diamond-pearl/',
    42: '/generation-iv/diamond-pearl/',
    43: '/generation-iv/platinum/',
    44: '/generation-iv/heartgold-soulsilver/',
    45: '/generation-iv/heartgold-soulsilver/',
    51: '/generation-v/black-white/',
    52: '/generation-v/black-white/',
    53: '/generation-v/black-white/',
    54: '/generation-v/black-white/'}
female_lut = {3,12,19,20,25,26,32,29,41,42,44,45,64,65,84,85,97,111,112,118,119,123,129,130,133,154,165,166,178,185,186,190,194,195,198,202,203,207,208,212,214,215,215,217,221,224,229,232,255,256,257,267,269,272,274,275,307,308,315,316,317,322,323,332,350,369,396,397,398,399,400,401,402,403,404,405,407,415,417,418,419,424,443,444,445,449,450,453,454,456,457,459,460,461,464,465,473,521,592,593,668,678,876,902}
def get_sprite(pokemon, anim, edition):
    
    shiny = 'shiny/' if pokemon.shiny else ''
    if pokemon.female and pokemon.dexnr in female_lut:
        female = 'female/' 
    else:
        female = ''
    if anim and edition in (51, 52, 53, 54):
        filetype = '.gif'
        animated = 'animated/'
    else:
        animated = ''
        filetype = '.png'
    path = path_lut[edition] + animated + shiny + female
    file = str(pokemon.dexnr) + pokemon.form + filetype
    
    url ='https://raw.githubusercontent.com/IntegerOverFlori/sprites/master/sprites/pokemon/versions'
    
    if not os.path.exists('.' + path + file):
        if (pokemon.dexnr == 'egg' or pokemon.dexnr in range(1, 650)):
            with requests.get(url + path + file) as r:
                if not os.path.exists('.' + path):
                    os.makedirs('.' + path)
                if not os.path.exists('.' + path + file):
                    logging.info(f'{url + path + file=}')
                    with open('.' + path + file, 'wb') as image:
                        image.write(r.content)
    return os.path.abspath('.' + path + file)


async def hide_nicknames():
    batch = []
    for i in range(24):
        batch.append(simpleobsws.Request('SetInputSettings', {'inputName': f'name{i + 1}', 'inputSettings': {'text': ''}}))
    await ws.call_batch(batch)

async def bizhawk_server(port):
    server = await asyncio.start_server(handle_client, '127.45.45.45', port)
    async with server:
        await server.serve_forever()

teams = [[]] * SPIELERANZAHL
async def handle_client(reader, writer):
    request = None
    while request != 'exit':
        header =  await reader.read(2)
        player = header[1]
        edition = header[0]
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
        elif edition <60:
            msg = await reader.read(1320)
            team = pokedecoder.team(msg, 5)

        logging.debug(f'{team=}')
        if teams[player] == []:
            teams[player] = team
            await changeSource(player, range(6), team, edition)

        else:
            diff =[]
            for i in range(6):
                if teams[player][i] != team[i]:
                    diff.append(i)
            teams[player] = team
            await changeSource(player, diff, team, edition)
    writer.close()

async def main():
    load_obsws()
    await connect_to_obs()
    await bizhawk_server(43885)

asyncio.run(main())
