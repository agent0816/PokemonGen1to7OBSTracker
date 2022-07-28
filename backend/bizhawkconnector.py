import threading
import socket
import time
import json
import logging
from classes.Pokemon import Pokemon
from classes.Spieler import Spieler
from classes.Slot import Slot
from classes.Game import Game

DEBUG = False

# sortiere nach 'dexnr', 'team', 'lvl' oder 'route'
ORDER = 'team'

server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
server.bind(('127.45.45.45', 43885))
server.listen()

clients = []
connections = {}
teams = {}

with open("species1.json") as file:
    species1_lut = json.load(file)

with open("species3.json") as file:
    species3_lut = json.load(file)

with open("gen1charset.json") as file:
    gen1charset = json.load(file)

with open("gen3charset.json")as file:
    gen3charset = json.load(file)

with open("gen4charset.json") as file:
    gen4charset = json.load(file)

with open("gen5charset.json") as file:
    gen5charset = json.load(file)


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
            if DEBUG:
                ServerLog.WriteDebugMessages("neuer akzeptierter Client: " + str(client))
            connections[client] = threading.Thread(target=interact, args=([client]), daemon=True)
            connections[client].start()


def interact(client):
    interacting = True
    while interacting:
        try:
            lasttime = time.time()
            recieve(client)
            if DEBUG:
                ServerLog.WriteBizHawkData(f'Antwort nach {time.time() - lasttime}')
                ServerLog.WriteDebugMessages("Verbindung mit Client erfolgreich: " + str(client))
        except KeyError as keyErr:
            ServerLog.WriteErrorMessages(str(type(keyErr)) + " aufgetreten für Client: " + str(client) + " Code: " + str(keyErr))
        except Exception as err:
            ServerLog.WriteErrorMessages("Verbindung mit Client gelöst: " + str(client)+ "Fehler: " + str(type(err)) + " " + str(err))
            connections.pop(client, None)
            teams.pop(client, None)
            interacting = False


def recieve(client):
    msg = client.recv(2)
    player = msg[1]
    edition = msg[0]

    if DEBUG:
        ServerLog.WriteDebugMessages("Kontrolldaten von Client " + str(client) + " erhalten: " + str(msg))

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

        msg = client.recv(354)
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
        teams[client] = Spieler(player, Game(edition), team(msg, 3))

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

    #if DEBUG:
    #    ServerLog.WriteDebugMessages("Daten von client " + str(client) + "erhalten: " + str(msg))


def pokemon1(data):
    dexnr = data[0]
    if str(dexnr) in species1_lut:
        dexnr = species1_lut.get(str(dexnr))
    lvl = data[0x21]
    nickname = ''
    for char in data[44:]:
        if str(char) in gen1charset:
            nickname += gen1charset[str(char)]
    return Pokemon(dexnr, False, lvl=lvl, nickname=nickname)


def pokemon2(data):
    dexnr = data[0]
    lvl = data[0x1F]
    nickname = ''
    for char in data[48:]:
        if str(char) in gen1charset:
            nickname += gen1charset[str(char)]
    return Pokemon(dexnr, False, lvl=lvl, nickname=nickname)


def pokemon3(data):
    growth_lut =   [32, 32, 32, 32, 32, 32, 44, 44, 56, 68, 56, 68, 44, 44, 56, 68, 56, 68, 44, 44, 56, 68, 56, 68]
    # attack_lut = [44, 44, 56, 68, 56, 68, 32, 32, 32, 32, 32, 32, 56, 68, 44, 44, 68, 56, 56, 68, 44, 44, 68, 56]
    # ev_lut =     [56, 68, 44, 44, 68, 56, 56, 68, 44, 44, 68, 56, 32, 32, 32, 32, 32, 32, 68, 56, 68, 56, 44, 44]
    misc_lut =     [68, 56, 68, 56, 44, 44, 68, 56, 68, 56, 44, 44, 68, 56, 68, 56, 44, 44, 32, 32, 32, 32, 32, 32]
    personality = int.from_bytes(data[:4], 'little')
    otid = int.from_bytes(data[4:8], 'little')
    key = personality ^ otid
    offset = personality % 24
    if data[19] == 6:
        return -1
    species = int.from_bytes(data[growth_lut[offset]:growth_lut[offset]+2], 'little')
    species = species ^ (key % 0x10000)
    if species in range(252,440):
        species = species3_lut[str(species)]
    lvl = data[84]
    met_location = data[misc_lut[offset]+1]
    met_location = met_location ^ ((key >> 8) % 0x100)
    nickname = ''
    for char in data[8:18]:
        if str(char) in gen3charset:
            nickname += gen3charset[str(char)]
        if char == 0xFF:
            break
    return Pokemon(species, not (key % 0x10000 ^ key >> 16) > 8, lvl=lvl, nickname=nickname, route=met_location)


def decryptpokemon(data):
    offset_lut = [[0, 1, 2, 3], [0, 1, 3, 2], [0, 2, 1, 3], [0, 3, 1, 2], [0, 2, 3, 1], [0, 3, 2, 1], [1, 0, 2, 3], [1, 0, 3, 2], [2, 0, 1, 3], [3, 0, 1, 2], [2, 0, 3, 1], [3, 0, 2, 1], [1, 2, 0, 3], [1, 3, 0, 2], [2, 1, 0, 3], [3, 1, 0, 2], [2, 3, 0, 1], [3, 2, 0, 1], [1, 2, 3, 0], [1, 3, 2, 0], [2, 1, 3, 0], [3, 1, 2, 0], [2, 3, 1, 0], [3, 2, 1, 0]]
    prng = lambda seed : (0x41C64E6D * seed + 0x6073) % 0x100000000
    personality_value = int.from_bytes(data[:4], 'little')
    checksum = int.from_bytes(data[6:8], 'little')
    shift_value = ((personality_value & 0x3E000) >> 0xD) % 24
    tid = int.from_bytes(data[4:6], 'little')
    sid = int.from_bytes(data[6:8], 'little')
    shiny_value = tid ^ sid ^ (personality_value >> 16) ^ (personality_value % 0x10000)
    encrypted_bytes = data[8:136]
    key = checksum
    decrypted_bytes = b''
    for i in range(64):
        key = prng(key)
        decrypted_bytes += (int.from_bytes(encrypted_bytes[i * 2:i * 2 + 2], 'little') ^ (key >> 16)).to_bytes(2, 'little')
    unshuffled_bytes = b''
    for i in range(4):
        unshuffled_bytes += decrypted_bytes[offset_lut[shift_value][i] * 32:offset_lut[shift_value][i] * 32 + 32]
    encrypted_battle_stats = data[136:]
    key = personality_value
    decrypted_battle_stats = b''
    for i in range(int(len(encrypted_battle_stats)/2)):
        key = prng(key)
        decrypted_battle_stats += (int.from_bytes(encrypted_battle_stats[i * 2:i * 2 + 2], 'little') ^ (key >> 16)).to_bytes(2, 'little')
    return (unshuffled_bytes, decrypted_battle_stats, shiny_value)


def pokemon4(data):
    unshuffled_bytes, decrypted_battle_stats, shiny_value = decryptpokemon(data)
    dexnr = int.from_bytes(unshuffled_bytes[0:2], 'little')
    met_location = int.from_bytes(unshuffled_bytes[0x3E:0x40], 'little')
    if met_location == 0:  # diamant/perl
        met_location = int.from_bytes(unshuffled_bytes[0x78:0x7A], 'little')
    lvl = decrypted_battle_stats[4]
    nickname = ''
    for char in unshuffled_bytes[0x40:0x56]:
        if char == 0xff:
            break
        if str(char) in gen4charset:
            nickname += gen4charset[str(char)]
    return Pokemon(dexnr, shiny_value < 9, lvl=lvl, nickname=nickname, route=met_location)


def pokemon5(data):
    unshuffled_bytes, decrypted_battle_stats, shiny_value = decryptpokemon(data)
    dexnr = int.from_bytes(unshuffled_bytes[0:2], 'little')
    met_location = int.from_bytes(unshuffled_bytes[0x3E:0x40], 'little')
    if met_location == 0:  # diamant/perl
        met_location = int.from_bytes(unshuffled_bytes[0x78:0x7A], 'little')
    lvl = decrypted_battle_stats[4]
    nickname = ''
    for char in unshuffled_bytes[0x40:0x56]:
        if char == 0xff:
            break
        if str(char) in gen5charset:
            nickname += gen5charset[str(char)]
    return Pokemon(dexnr, shiny_value < 9, lvl=lvl, nickname=nickname, route=met_location)


def team(data, edition):
    length = len(data) // 6
    liste = []

    if edition == 1:
        decoder = pokemon1
        newdata = b''
        for i in range(6):
            newdata += data[i * 44:i * 44 + 44] + data[i * 11 + 264:11 + i * 11 + 264]
        data = newdata
    if edition == 2:
        newdata = b''
        for i in range(6):
            newdata += data[i * 48:i * 48 + 48] + data[i * 11 + 288:11 + i * 11 + 288]
        decoder = pokemon2
        data = newdata
    if edition == 3:
        decoder = pokemon3
    if edition == 4:
        decoder = pokemon4
    if edition == 5:
        decoder = pokemon5
    for i in range(6):
        liste.append(decoder(data[i * length: (i + 1) * length]))

    liste = sort(liste, ORDER)
    for i in range(6):
        liste[i] = Slot(i+1, liste[i])
    return liste

def sort(liste, key):
    if key == 'team':
        return liste
    dexAlgorithm = lambda a: a.dexnr if a.dexnr != 0 else 999999 + a.dexnr
    if key == 'dexnr':
        algorithm = dexAlgorithm
    if key == 'lvl':
        algorithm = lambda a: - a.lvl if a.dexnr != 0 else 999999
    if key == 'route':
        algorithm = lambda a: a.route if a.dexnr != 0 else 999999

    return sorted(sorted(liste, key=dexAlgorithm), key=algorithm)


new_connection_listener = threading.Thread(target=accept, args=(), daemon=True)
new_connection_listener.start()

if __name__ == '__main__':
    while True:
        time.sleep(5)
        for t in teams:
            print(teams[t])
        print('')
        

        