import logging
logging.basicConfig(level=logging.INFO, filename='bizhawk-obs-bridge.log',format='[%(asctime)s] %(levelname)s: %(message)s', filemode='w', encoding='utf-8')
import os
import requests
import asyncio
import simpleobsws
import yaml
import bizhawkconnector

config = yaml.safe_load(open('config.yml'))

parameters = simpleobsws.IdentificationParameters(ignoreNonFatalRequestChecks = False)
ws = simpleobsws.WebSocketClient(url = 'ws://' + config['obshost'] + ':' + str(config['obsport']), password = config['obspassword'], identification_parameters = parameters)

async def connect_to_obs():
    await ws.connect()
    await ws.wait_until_identified()


async def changeSource(player, slots):
    batch = []
    for slot in slots:
        shiny = ''
        female = ''
        animated = ''
        filetype = '.png'
        if player.slots[slot].pokemon.shiny:
            shiny = 'shiny/'
        if player.slots[slot].pokemon.female:
            female = 'female/'
        if config['animated'] and player.game.edition in ('black', 'white'):
            filetype = '.gif'
            animated = 'animated/'
        path = player.game.path + animated + shiny + female
        file = str(player.slots[slot].pokemon.dexnr) + player.slots[slot].pokemon.form + filetype
        url ='https://raw.githubusercontent.com/IntegerOverFlori/sprites/master/sprites'
        if not os.path.exists('.' + path + file):
            if (player.slots[slot].pokemon.dexnr == 'egg' or player.slots[slot].pokemon.dexnr in range(1, 650)):    
                with requests.get(url + path + file) as r:
                    if r.status_code != 200:
                        path = player.game.path + animated + shiny
                        r = requests.get(url + path + file)
                    if not os.path.exists('.' + path):
                        os.makedirs('.' + path)
                    if not os.path.exists('.' + path + file):
                        logging.info(f'{url + path + file=}')
                        with open('.' + path + file, 'wb') as image:
                            image.write(r.content)

        batch.append(simpleobsws.Request('SetInputSettings', {'inputName':f'Slot{slot + 6 * (player.id -1) +1}', 'inputSettings':{'file': os.path.abspath('.' + path + file)}}))
        if config['show_nicknames']:
            batch.append(simpleobsws.Request('SetInputSettings', {'inputName': f'name{slot + 6 * (player.id -1) +1}', 'inputSettings': {'text': player.slots[slot].pokemon.nickname}}))
    if batch != []:
        await ws.call_batch(batch)


mitspieler = []
async def handle_bizhawk():
    logging.info("Handle Bizhawk eingeleitet")
    while True:
        try:
            for player in bizhawkconnector.teams.values():
                if player not in mitspieler:
                    mitspieler.append(player)
                    logging.info("neuen Spieler erkannt - " + str(player))

                    await changeSource(player, range(6))

                else:
                    index = mitspieler.index(player)
                    slots=[]
                    for i in range(6):
                        if player.slots[i] != mitspieler[index].slots[i]:
                            logging.info("Teamänderung verzeichnet von Spieler " + str(player.id))
                            logging.info("neues Pokemon: " + str(player.slots[i]))
                            logging.info("altes Pokemon: " + str(mitspieler[index].slots[i]))
                            mitspieler[index].setSlot(player.slots[i])
                            slots.append(i)
                    await changeSource(player, slots)

                    mitspieler[index] = player

        except Exception as err:
            logging.error("handle_bizhawk(). Fehler: " + str(err))


def main():
    loop = asyncio.get_event_loop()
    loop.run_until_complete(connect_to_obs())
    loop.create_task(handle_bizhawk())
    try:
        loop.run_forever()
    except KeyboardInterrupt:
        pass
    except:
        logging.exception('Exception:\n')
    finally:
        loop.run_until_complete(ws.disconnect())



main()