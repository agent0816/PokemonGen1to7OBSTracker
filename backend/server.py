import logging
logging.basicConfig(level=logging.DEBUG, filename='server.log',format='[%(asctime)s] %(levelname)s: %(message)s', filemode='w', encoding='utf-8')
import asyncio
import yaml
import simpleobsws
import backend.pokedecoder as pokedecoder

SPIELERANZAHL = 4
with open('backend/config/sprites.yml') as file:
    spriteconf = yaml.safe_load(file)
with open('backend/config/bh_config.yml') as file:
    bizhawk_config = yaml.safe_load(file)



ws = None

def update_config():
    global spriteconf
    global bizhawk_config
    with open('backend/config/sprites.yml') as file:
        spriteconf = yaml.safe_load(file)
    with open('backend/config/bh_config.yml') as file:
        bizhawk_config = yaml.safe_load(file)

def load_obsws():
    obsconf = yaml.safe_load(open('backend/config/obs_config.yml'))
    global ws
    if obsconf['host'] != '' and obsconf['port'] != '':
        ws = simpleobsws.WebSocketClient(url = 'ws://' + obsconf['host'] + ':' + obsconf['port'], password = obsconf['password'], identification_parameters = simpleobsws.IdentificationParameters(ignoreNonFatalRequestChecks = False))


async def connect_to_obs():
    if ws is not None:
        await ws.connect()
        await ws.wait_until_identified()


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
        await ws.call_batch(batch)

edition_lut = {
    11: 'red',
    12: 'red',
    13: 'yellow',
    21: 'gold',
    22: 'silver',
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
    54: 'black'}
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
    path = spriteconf['common_path'] + spriteconf[edition_lut[edition]] + animated + shiny + female
    file = str(pokemon.dexnr) + pokemon.form + filetype
    return path + file


async def hide_nicknames():
    batch = []
    for i in range(24):
        batch.append(simpleobsws.Request('SetInputSettings', {'inputName': f'name{i + 1}', 'inputSettings': {'text': ''}}))
    await ws.call_batch(batch)

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

async def start():
    load_obsws()
    await connect_to_obs()
    await bizhawk_server()


def main():
    asyncio.run(start())