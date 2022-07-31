import threading
import socket
import time
from classes.Pokemon import Pokemon
import logging
from classes.Spieler import Spieler
from classes.Slot import Slot
from classes.Game import Game
import yaml
import os
logging.basicConfig(level=logging.DEBUG, filename='bizhawk-obs-bridge.log',format='[%(asctime)s] %(levelname)s: %(message)s', filemode='w', encoding='utf-8')


config = yaml.safe_load(open('config.yml'))
ORDER = config['order']

server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
server.bind((config['bizhost'], config['bizport']))
server.listen()

clients = []
connections = {}
teams = {}

species1_lut = yaml.safe_load(open('data/species1.yml'))
species3_lut = yaml.safe_load(open('data/species3.yml'))
gen1charset = yaml.safe_load(open('data/gen1charset.yml'))
gen3charset = yaml.safe_load(open('data/gen3charset.yml'))
gen4charset = yaml.safe_load(open('data/gen4charset.yml'))
gen5charset = yaml.safe_load(open('data/gen5charset.yml'))
gender_lut = yaml.safe_load(open('data/gender_lut.yml'))

def accept():
    while True:
        try:
            client, _ = server.accept()
        except:
            pass
        if client not in clients:
            clients.append(client)
            if __name__ == '__main__':
                print('accepted new client')
            logging.debug("neuer akzeptierter Client: " + str(client))
            connections[client] = threading.Thread(target=interact, args=([client]), daemon=True)
            connections[client].start()


def interact(client):
    interacting = True
    while interacting:
        try:
            recieve(client)
            logging.debug("Verbindung mit Client erfolgreich: " + str(client))
        except Exception as err:
            logging.debug("Verbindung mit Client gelöst: " + str(client) + str(err))
            connections.pop(client, None)
            teams.pop(client, None)
            interacting = False


def recieve(client):
    msg = client.recv(2)
    player = msg[1]
    edition = msg[0]
    logging.debug("Kontrolldaten von Client " + str(client) + " erhalten: " + str(msg))

    if edition < 20:
        if edition == 11:
            edition = 'red'
        elif edition == 12:
            edition = 'blue'
        elif edition == 13:
            edition = 'yellow'
        msg = client.recv(330)
        teams[client] = Spieler(player, Game(edition), team(msg, 1))

    elif edition < 30:
        if edition == 21:
            edition = 'gold'
        elif edition == 22:
            edition = 'silver'
        elif edition == 23:
            edition = 'crystal'

        msg = client.recv(360)
        teams[client] = Spieler(player, Game(edition), team(msg, 2))

    elif edition < 40:
        if edition == 31:
            edition = 'ruby'
        elif edition == 32:
            edition = 'sapphire'
        elif edition == 33:
            edition = 'emerald'
        elif edition == 34:
            edition = 'fire red'
        elif edition == 35:
            edition = 'leaf green'
        msg = client.recv(600)
        teams[client] = Spieler(player, Game(edition), team(msg, edition))

    elif edition < 50:
        if edition == 41:
            edition = 'diamond'
        elif edition == 42:
            edition = 'pearl'
        elif edition == 43:
            edition = 'platinum'
        elif edition == 44:
            edition = 'heart gold'
        elif edition == 45:
            edition = 'soul silver'
        msg = client.recv(1416)
        teams[client] = Spieler(player, Game(edition), team(msg, 4))

    elif edition < 60:
        if edition == 51:
            edition = 'black'
        if edition == 52:
            edition = 'white'
        if edition == 53:
            edition = 'black'
        if edition == 54:
            edition = 'white'
        msg = client.recv(1320)
        teams[client] = Spieler(player, Game(edition), team(msg, 5))



def pokemon1(data):
    dexnr = data[0]
    if dexnr in species1_lut:
        dexnr = species1_lut.get(dexnr)
    lvl = data[0x21]
    nickname = ''
    for char in data[44:]:
        if char in gen1charset:
            nickname += gen1charset[char]
    return Pokemon(dexnr, False, lvl=lvl, nickname=nickname)


def pokemon2(data):
    unown_letter = ['-a', '-b', '-c', '-d', '-e', '-f', '-g', '-h', '-i', '-j', '-k', '-l', '-m', '-n', '-o', '-p', '-q', '-r', '-s', '-t', '-u', '-v', '-w', '-x', '-y', '-z']
    dexnr = data[0]
    lvl = data[0x1F]
    nickname = ''
    form = ''
    egg = data[-1] == 0xFD
    for char in data[48:-1]:
        if char in gen1charset:
            nickname += gen1charset[char]
    ivs = [data[0x15] >> 4, data[0x15] % 16, data[0x16] >> 4, data[0x16] % 16]
    if dexnr == 201:
        letter = (((ivs[0] >> 1) % 4) << 6) + (((ivs[1] >> 1) % 4) << 4) + (((ivs[2] >> 1) % 4) << 2) + ((ivs[3] >> 1) % 4)
        letter = letter // 10
        form = unown_letter[letter]
    if egg:
        dexnr = 'egg'
        form = ''

    return Pokemon(dexnr, False, lvl=lvl, form=form, nickname=nickname)


def pokemon3(data, edition):
    growth_lut =   [32, 32, 32, 32, 32, 32, 44, 44, 56, 68, 56, 68, 44, 44, 56, 68, 56, 68, 44, 44, 56, 68, 56, 68]
    # attack_lut = [44, 44, 56, 68, 56, 68, 32, 32, 32, 32, 32, 32, 56, 68, 44, 44, 68, 56, 56, 68, 44, 44, 68, 56]
    # ev_lut =     [56, 68, 44, 44, 68, 56, 56, 68, 44, 44, 68, 56, 32, 32, 32, 32, 32, 32, 68, 56, 68, 56, 44, 44]
    misc_lut =     [68, 56, 68, 56, 44, 44, 68, 56, 68, 56, 44, 44, 68, 56, 68, 56, 44, 44, 32, 32, 32, 32, 32, 32]
    egg = False
    form = ''
    female = False
    personality = int.from_bytes(data[:4], 'little')
    otid = int.from_bytes(data[4:8], 'little')
    key = personality ^ otid
    offset = personality % 24
    if data[19] == 6:
        egg = True
    species = int.from_bytes(data[growth_lut[offset]:growth_lut[offset]+2], 'little')
    species = species ^ (key % 0x10000)
    if species in range(252, 440):
        species = species3_lut[species]
    if type(species) == str:
        form = species[3:]
        species = int(species[:3])
    if species == 386:
        if edition == 'fire red':
            form = '-attack'
        if edition== 'leaf green':
            form = '-defense'
        if edition == 'emerald':
            form = '-speed'
    if egg:
        form = ''
        species = 'egg'
    lvl = data[84]
    if not egg:
        female = personality % 256 < gender_lut[species]
    met_location = data[misc_lut[offset]+1]
    met_location = met_location ^ ((key >> 8) % 0x100)
    nickname = ''
    for char in data[8:18]:
        if char in gen3charset:
            nickname += gen3charset[char]
        if char == 0xFF:
            break
    return Pokemon(species, not (key % 0x10000 ^ key >> 16) > 8, female, form=form, lvl=lvl, nickname=nickname, route=met_location)


def decryptpokemon(data):
    offset_lut = [[0, 1, 2, 3], [0, 1, 3, 2], [0, 2, 1, 3], [0, 3, 1, 2], [0, 2, 3, 1], [0, 3, 2, 1], [1, 0, 2, 3], [1, 0, 3, 2], [2, 0, 1, 3], [3, 0, 1, 2], [2, 0, 3, 1], [3, 0, 2, 1], [1, 2, 0, 3], [1, 3, 0, 2], [2, 1, 0, 3], [3, 1, 0, 2], [2, 3, 0, 1], [3, 2, 0, 1], [1, 2, 3, 0], [1, 3, 2, 0], [2, 1, 3, 0], [3, 1, 2, 0], [2, 3, 1, 0], [3, 2, 1, 0]]
    prng = lambda seed : (0x41C64E6D * seed + 0x6073) % 0x100000000
    personality_value = int.from_bytes(data[:4], 'little')
    checksum = int.from_bytes(data[6:8], 'little')
    shift_value = ((personality_value & 0x3E000) >> 0xD) % 24
    encrypted_bytes = data[8:136]
    key = checksum
    decrypted_bytes = b''
    for i in range(64):
        key = prng(key)
        decrypted_bytes += (int.from_bytes(encrypted_bytes[i * 2:i * 2 + 2], 'little') ^ (key >> 16)).to_bytes(2, 'little')
    unshuffled_bytes = b''
    for i in range(4):
        unshuffled_bytes += decrypted_bytes[offset_lut[shift_value][i] * 32:offset_lut[shift_value][i] * 32 + 32]
    tid = int.from_bytes(unshuffled_bytes[4:6], 'little')
    sid = int.from_bytes(unshuffled_bytes[6:8], 'little')
    shiny_value = tid ^ sid ^ (personality_value >> 16) ^ (personality_value % 0x10000)
    encrypted_battle_stats = data[136:]
    key = personality_value
    decrypted_battle_stats = b''
    for i in range(int(len(encrypted_battle_stats)/2)):
        key = prng(key)
        decrypted_battle_stats += (int.from_bytes(encrypted_battle_stats[i * 2:i * 2 + 2], 'little') ^ (key >> 16)).to_bytes(2, 'little')
    return (unshuffled_bytes, decrypted_battle_stats, shiny_value, personality_value)


def pokemon45(data, charset):
    unown={0x00:'' , 0x08:'-b' ,0x10:'-c' ,0x18:'-d' ,0x20:'-e' ,0x28:'-f' ,0x30:'-g' ,0x38:'-h' ,0x40:'-i' ,0x48:'-j' ,0x50:'-k' ,0x58:'-l' ,0x60:'-m' ,0x68:'-n' ,0x70:'-o' ,0x78:'-p' ,0x80:'-q' ,0x88:'-r' ,0x90:'-s' ,0x98:'-t' ,0xA0:'-u' ,0xA8:'-v' ,0xB0:'-w' ,0xB8:'-x' ,0xC0:'-y' ,0xC8:'-z' ,0xD0:'-exclamation' ,0xD8:'-question'}
    burmy={0x00: '-plant', 0x08: '-sandy',0x10: '-trash'}
    shellos={0x00: '-west', 0x08: '-east'}
    rotom={0x00: '', 0x08: '-heat', 0x10: '-wash',0x18: '-frost',0x20: '-fan',0x28: '-mow'}
    giratina={0x00: '', 0x08: '-origin'}
    shaymin={0x00: '', 0x08: '-sky'}
    deoxys={0x00: '', 0x08: '-attack', 0x10: '-defense', 0x18: '-speed'}
    arceus={0x00:'' , 0x08:'-fighting' ,0x10:'-flying' ,0x18:'-poison' ,0x20:'-ground' ,0x28:'-rock' ,0x30:'-bug' ,0x38:'-ghost' ,0x40:'-steel' ,0x48:'-unknown' ,0x50:'-fire' ,0x58:'-water' ,0x60:'-grass' ,0x68:'-electric' ,0x70:'-psychic' ,0x78:'-ice' ,0x80:'-dragon' ,0x88:'-dark'}
    deerling = {0x00:'' , 0x08: '-spring', 0x10: '-summer', 0x18: '-autumn', 0x20: '-winter'}
    basculin = {0x00:'' , 0x08: '-blue-striped'}
    boreos = {0x00:'' , 0x08: '-therian'}
    kyurem = {0x00:'' , 0x08: '-white', 0x18: '-black'}
    keldeo = {0x00:'' , 0x08: '-resolute'}
    genesect = {0x00:'' , 0x08: '-douse', 0x10: '-shock', 0x18: '-burn', 0x20: '-chill'}


    unshuffled_bytes, decrypted_battle_stats, shiny_value, personality = decryptpokemon(data)
    dexnr = int.from_bytes(unshuffled_bytes[0:2], 'little')
    met_location = int.from_bytes(unshuffled_bytes[0x3E:0x40], 'little')
    if met_location == 0:  # diamant/perl
        met_location = int.from_bytes(unshuffled_bytes[0x78:0x7A], 'little')
    lvl = decrypted_battle_stats[4]
    if dexnr in range(650):
        female = personality % 256 < gender_lut[dexnr]
    else:
        female = False
    nickname = ''
    for char in unshuffled_bytes[0x40:0x56]:
        if char == 0xff:
            break
        if char in charset:
            nickname += charset[char]
    form = unshuffled_bytes[0x38] % 32
    form -= form % 8
    if dexnr == 201:
        form = unown[form]
    if dexnr in [412, 413]:
        form = burmy[form]
    if dexnr in[422, 423]:
        form = shellos[form]
    if dexnr == 479:
        form = rotom[form]
    if dexnr == 487:
        form = giratina[form]
    if dexnr == 492:
        form = shaymin[form]
    if dexnr == 493:
        form = arceus[form]
    if dexnr == 386:
        form = deoxys[form]
    if dexnr in [585, 586]:
        form = deerling[form]
    if dexnr == 550:
        form = basculin[form]
    if dexnr in [641, 642, 645]:
        form = boreos[form]
    if dexnr == 647:
        form = keldeo[form]
    if dexnr == 646:
        form = kyurem[form]
    if dexnr == 649:
        form = genesect[form]
    if type(form) == int:
        form = ''
    if dexnr not in range(650):
        dexnr = 0
        nickname = ''
    if unshuffled_bytes[0x33] & 64 and dexnr != 0:
        if dexnr == 490:
            form = '-manaphy'
        else:
            form = ''
        dexnr = 'egg'
    return Pokemon(dexnr, shiny_value < 9, female, form=form, lvl=lvl, nickname=nickname, route=met_location)


def team(data, edition):
    length = len(data) // 6
    liste = []

    if edition == 1:
        newdata = b''
        for i in range(6):
            newdata += data[i * 44:i * 44 + 44] + data[i * 11 + 264:11 + i * 11 + 264]
        data = newdata
        for i in range(6):
            liste.append(pokemon1(data[i * length: (i + 1) * length]))
    if edition == 2:
        newdata = b''
        for i in range(6):
            newdata += data[i * 48:i * 48 + 48] + data[i * 11 + 288:11 + i * 11 + 288] + data[i + 354:i + 355]
        data = newdata
        for i in range(6):
            liste.append(pokemon2(data[i * length: (i + 1) * length]))
    if edition in ('ruby', 'sapphire', 'emerald', 'fire red', 'leaf green'):
        for i in range(6):
            liste.append(pokemon3(data[i * length: (i + 1) * length], edition))
    if edition == 4:
        for i in range(6):
            liste.append(pokemon45(data[i * length: (i + 1) * length], gen4charset))
    if edition == 5:
        for i in range(6):
            liste.append(pokemon45(data[i * length: (i + 1) * length], gen5charset))
    

    liste = sort(liste, ORDER)
    for i in range(6):
        liste[i] = Slot(i+1, liste[i])
    return liste

def sort(liste, key):
    if key == 'dexnr':
        algorithm = lambda a: a
    if key == 'team':
        return liste
    if key == 'lvl':
        algorithm = lambda a: - a.lvl if a.dexnr != 0 else 999999
    if key == 'route':
        algorithm = lambda a: a.route if a.dexnr != 0 else 999999

    return sorted(sorted(liste), key=algorithm)


new_connection_listener = threading.Thread(target=accept, args=(), daemon=True)
new_connection_listener.start()
print(f'Start Bizhawk from command line with flags --socket_ip={config["bizhost"]} --socket_port={config["bizport"]} --lua={os.path.abspath("./obsautomation.lua")}')
if __name__ == '__main__':
    while True:
        time.sleep(5)
        for t in teams:
            print(teams[t])
        print('')
        

        