import yaml
from backend.classes.Pokemon import Pokemon

species1_lut = yaml.safe_load(open('backend/data/species1.yml'))
species3_lut = yaml.safe_load(open('backend/data/species3.yml'))
gen1charset = yaml.safe_load(open('backend/data/gen1charset.yml'))
gen3charset = yaml.safe_load(open('backend/data/gen3charset.yml'))
gen4charset = yaml.safe_load(open('backend/data/gen4charset.yml'))
gender_lut = yaml.safe_load(open('backend/data/gender_lut.yml'))
items2 = yaml.safe_load(open('backend/data/items2.yml'))
items3 = yaml.safe_load(open('backend/data/items3.yml'))
items4 = yaml.safe_load(open('backend/data/items4.yml'))
items5 = yaml.safe_load(open('backend/data/items5.yml'))

def decryptpokemon(data, gen):
    def prng(seed):
        return (0x41C64E6D * seed + 0x6073) % 0x100000000
    offset_lut = [[0, 1, 2, 3], [0, 1, 3, 2], [0, 2, 1, 3], [0, 3, 1, 2], [0, 2, 3, 1], [0, 3, 2, 1], [1, 0, 2, 3], [1, 0, 3, 2], [2, 0, 1, 3], [3, 0, 1, 2], [2, 0, 3, 1], [3, 0, 2, 1], [1, 2, 0, 3], [1, 3, 0, 2], [2, 1, 0, 3], [3, 1, 0, 2], [2, 3, 0, 1], [3, 2, 0, 1], [1, 2, 3, 0], [1, 3, 2, 0], [2, 1, 3, 0], [3, 1, 2, 0], [2, 3, 1, 0], [3, 2, 1, 0]]
    personality_value = int.from_bytes(data[:4], 'little')
    shift_value = ((personality_value & 0x3E000) >> 0xD) % 24
    if gen == '45':
        cutoff = 136
        blocksize = 32
        key = int.from_bytes(data[6:8], 'little')
    else:
        cutoff = 232
        blocksize = 56
        key = personality_value
    encrypted_bytes = data[8:cutoff]
    decrypted_bytes = b''
    for i in range(len(encrypted_bytes) // 2):
        key = prng(key)
        decrypted_bytes += (int.from_bytes(encrypted_bytes[i * 2:i * 2 + 2], 'little') ^ (key >> 16)).to_bytes(2, 'little')
    unshuffled_bytes = b''
    for i in range(4):
        unshuffled_bytes += decrypted_bytes[offset_lut[shift_value][i] * blocksize:offset_lut[shift_value][i] * blocksize + blocksize]
    tid = int.from_bytes(unshuffled_bytes[4:6], 'little')
    sid = int.from_bytes(unshuffled_bytes[6:8], 'little')
    shiny_value = tid ^ sid ^ (personality_value >> 16) ^ (personality_value % 0x10000)
    encrypted_battle_stats = data[cutoff:]
    key = personality_value
    decrypted_battle_stats = b''
    for i in range(len(encrypted_battle_stats) // 2):
        key = prng(key)
        decrypted_battle_stats += (int.from_bytes(encrypted_battle_stats[i * 2:i * 2 + 2], 'little') ^ (key >> 16)).to_bytes(2, 'little')
    return (unshuffled_bytes, decrypted_battle_stats, shiny_value, personality_value)


def pokemon1(data):
    dexnr = data[0]
    if dexnr in species1_lut:
        dexnr = species1_lut.get(dexnr)
    lvl = data[0x21]
    cur_hp = int.from_bytes(data[0x1:0x3], 'little')
    max_hp = int.from_bytes(data[0x22:0x24], 'little')
    nickname = ''
    for char in data[44:]:
        if char in gen1charset:
            nickname += gen1charset[char]
    return Pokemon(dexnr, False, lvl=lvl, nickname=nickname, cur_hp=cur_hp, max_hp=max_hp)


def pokemon2(data):
    unown_letter = ['-a', '-b', '-c', '-d', '-e', '-f', '-g', '-h', '-i', '-j', '-k', '-l', '-m', '-n', '-o', '-p', '-q', '-r', '-s', '-t', '-u', '-v', '-w', '-x', '-y', '-z']
    dexnr = data[0]
    item = data[1]
    if item in items2:
        item = items2[item]
    lvl = data[0x1F]
    cur_hp = int.from_bytes(data[0x22:0x24], 'little')
    max_hp = int.from_bytes(data[0x24:0x26], 'little')
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

    return Pokemon(dexnr, False, lvl=lvl, form=form, nickname=nickname, item=item, cur_hp=cur_hp, max_hp=max_hp) # type: ignore


def pokemon3(data, edition):
    growth_lut = [32, 32, 32, 32, 32, 32, 44, 44, 56, 68, 56, 68, 44, 44, 56, 68, 56, 68, 44, 44, 56, 68, 56, 68]
    # attack_lut = [44, 44, 56, 68, 56, 68, 32, 32, 32, 32, 32, 32, 56, 68, 44, 44, 68, 56, 56, 68, 44, 44, 68, 56]
    # ev_lut = [56, 68, 44, 44, 68, 56, 56, 68, 44, 44, 68, 56, 32, 32, 32, 32, 32, 32, 68, 56, 68, 56, 44, 44]
    misc_lut = [68, 56, 68, 56, 44, 44, 68, 56, 68, 56, 44, 44, 68, 56, 68, 56, 44, 44, 32, 32, 32, 32, 32, 32]
    egg = False
    form = ''
    personality = int.from_bytes(data[:4], 'little')
    otid = int.from_bytes(data[4:8], 'little')
    key = personality ^ otid
    offset = personality % 24
    if data[19] == 6:
        egg = True
    species = int.from_bytes(data[growth_lut[offset]:growth_lut[offset] + 2], 'little')
    species = species ^ (key % 0x10000)
    item = int.from_bytes(data[growth_lut[offset] + 2:growth_lut[offset] + 4], 'little')
    item = item ^ (key // 0x10000)
    if item in items3:
        item = items3[item]
    if species in range(252, 440):
        species = species3_lut[species]
        if isinstance(species, str):
            form = species[3:]
            species = int(species[:3])
        if species == 386:
            if edition == 34:
                form = '-attack'
            if edition == 35:
                form = '-defense'
            if edition == 33:
                form = '-speed'
    if egg:
        form = ''
        species = 'egg'
    lvl = data[84]
    cur_hp = int.from_bytes(data[0x56:0x58], 'little')
    max_hp = int.from_bytes(data[0x58:0x5a], 'little')
    # female = False
    # if not egg:
    #     female = personality % 256 < gender_lut[species]
    met_location = data[misc_lut[offset] + 1]
    met_location = met_location ^ ((key >> 8) % 0x100)
    nickname = ''
    for char in data[8:18]:
        if char in gen3charset:
            nickname += gen3charset[char]
        if char == 0xFF:
            break
    return Pokemon(species, not (key % 0x10000 ^ key >> 16) > 8, form=form, lvl=lvl, item=item, nickname=nickname, route=met_location, cur_hp=cur_hp, max_hp=max_hp) # type: ignore

def get_form(data, dexnr):   
    mega_charizard_mewtu = {0x00: '', 0x08: '-megax', 0x10: 'megay'}
    mega_dict = {0x00: '', 0x08: '-mega'}
    alola_dict = {0x00: '', 0x08: '-alola'}
    unown = {0x00: '', 0x08: '-b', 0x10: '-c', 0x18: '-d', 0x20: '-e', 0x28: '-f', 0x30: '-g', 0x38: '-h', 0x40: '-i', 0x48: '-j', 0x50: '-k', 0x58: '-l', 0x60: '-m', 0x68: '-n', 0x70: '-o', 0x78: '-p', 0x80: '-q', 0x88: '-r', 0x90: '-s', 0x98: '-t', 0xA0: '-u', 0xA8: '-v', 0xB0: '-w', 0xB8: '-x', 0xC0: '-y', 0xC8: '-z', 0xD0: '-exclamation', 0xD8: '-question'}
    burmy = {0x00: '-plant', 0x08: '-sandy', 0x10: '-trash'}
    shellos = {0x00: '-west', 0x08: '-east'}
    rotom = {0x00: '', 0x08: '-heat', 0x10: '-wash', 0x18: '-frost', 0x20: '-fan', 0x28: '-mow'}
    giratina = {0x00: '', 0x08: '-origin'}
    shaymin = {0x00: '', 0x08: '-sky'}
    deoxys = {0x00: '', 0x08: '-attack', 0x10: '-defense', 0x18: '-speed'}
    arceus = {0x00: '', 0x08: '-fighting', 0x10: '-flying', 0x18: '-poison', 0x20: '-ground', 0x28: '-rock', 0x30: '-bug', 0x38: '-ghost', 0x40: '-steel', 0x48: '-unknown', 0x50: '-fire', 0x58: '-water', 0x60: '-grass', 0x68: '-electric', 0x70: '-psychic', 0x78: '-ice', 0x80: '-dragon', 0x88: '-dark'}
    deerling = {0x00: '', 0x08: '-spring', 0x10: '-summer', 0x18: '-autumn', 0x20: '-winter'}
    basculin = {0x00: '', 0x08: '-blue-striped'}
    boreos = {0x00: '', 0x08: '-therian'}
    kyurem = {0x00: '', 0x08: '-white', 0x18: '-black'}
    keldeo = {0x00: '', 0x08: '-resolute'}
    genesect = {0x00: '', 0x08: '-douse', 0x10: '-shock', 0x18: '-burn', 0x20: '-chill'}
    vivillon = {0x00: '', 0x08: '-polar', 0x10: '-tundra', 0x18: '-continental', 0x20: '-garden', 0x28: '-elegant', 0x30: '-meadow', 0x38: '-modern', 0x40: '-marine', 0x48: '-archipelago', 0x50: '-high-plains', 0x58: '-sandstorm', 0x60: '-river', 0x68: '-monsoon', 0x70: '-savanna', 0x78: '-sun', 0x80: '-ocean', 0x88: '-jungle', 0x90: '-fancy', 0x98: '-poke-ball'} # 666
    flabebe = {0x00: '', 0x08: '-yellow', 0x10: '-orange', 0x18: '-blue', 0x20: '-white'} # 669-671
    furfrou = {0x00: '', 0x08: '-heart', 0x10: '-star', 0x18: '-diamond', 0x20: '-debutante', 0x28: '-matron', 0x30: '-dandy', 0x38: '-la-reine', 0x40: '-kabuki', 0x48: '-pharaoh', 0x50: '-k'} # 676
    aegislash = {0x00: '', 0x08: '-blade'} # 681
    pumpkaboo = {0x00: '', 0x08: '-small', 0x10: '-large', 0x18: '-super'} # 710 - 711
    xerneas = {0x00: '-neutral', 0x08: '-active'} # 716
    zygarde = {0x00: ''} # 718
    hoopa = {0x00: '', 0x08: '-unbound'} # 720
    oricorio = {} # 741
    lycanroc = {} # 745
    wishiwashi = {} # 746
    silvally = {} # 773
    minior = {} # 774
    mimikyu = {} # 778
    necrozma = {} # 800
    magearna = {} # 801

    forms_dict = {
        6: mega_charizard_mewtu,
        150: mega_charizard_mewtu,
        201: unown,
        412: burmy,
        413: burmy,
        422: shellos,
        423: shellos,
        479: rotom,
        487: giratina,
        492: shaymin,
        493: arceus,
        386: deoxys,
        585: deerling,
        586: deerling,
        550: basculin,
        641: boreos,
        642: boreos,
        645: boreos,
        647: keldeo,
        646: kyurem,
        649: genesect,
        666: vivillon,
        669: flabebe,
        670: flabebe,
        671: flabebe,
        676: furfrou,
        681: aegislash,
        710: pumpkaboo,
        711: pumpkaboo,
        716: xerneas,
        718: zygarde,
        720: hoopa,
        741: oricorio,
        745: lycanroc,
        746: wishiwashi,
        773: silvally,
        774: minior,
        778: mimikyu,
        800: necrozma,
        801: magearna
    }

    mega = [3,6,9,15,18,65,80,94,115,127,130,142,150,181,208,212,214,229,248,254,257,260,282,302,303,306,308,310,319,323,334,354,359,362,373,376,380,381,384,428,445,448,460,475,531,719]

    alola = [19,20,26,27,28,37,38,50,51,74,75,76,88,89,103,105]

    form = data - data % 8
    # print(f"{form:02x}")
    if dexnr in forms_dict.keys():
        species_form = forms_dict.get(dexnr)
        form = species_form.get(form, 0)
    if dexnr in mega:
        form = mega_dict.get(form, 0)
    if dexnr in alola:
        form = alola_dict.get(form, 0)
    if isinstance(form, int):
        form = ''

    return form


def pokemon45(data, gen):
    charset = gen4charset
    items = items4 if gen == 4 else items5

    unshuffled_bytes, decrypted_battle_stats, shiny_value, personality = decryptpokemon(data, '45')
    dexnr = int.from_bytes(unshuffled_bytes[0:2], 'little')
    item = int.from_bytes(unshuffled_bytes[2:4], 'little')

    if dexnr >= 650:
        return Pokemon(0, item='-', nickname='', lvl=1)

    if item in items:
        item = items[item]
    else:
        item = '-'
    met_location = int.from_bytes(unshuffled_bytes[0x3E:0x40], 'little')
    if met_location == 0:  # diamant/perl
        met_location = int.from_bytes(unshuffled_bytes[0x78:0x7A], 'little')
    lvl = decrypted_battle_stats[4]
    cur_hp = int.from_bytes(decrypted_battle_stats[6:8], 'little')
    max_hp = int.from_bytes(decrypted_battle_stats[8:10], 'little')
    if dexnr in range(650):
        female = personality % 256 < gender_lut[dexnr]
    else:
        female = False
    if gen == 4:
        nickname = ''
        for char in unshuffled_bytes[0x40:0x56]:
            if char == 0xff:
                break
            if char in charset:
                nickname += charset[char]
    else:
        nickname = b''
        nickname_array_length = range(0x40,0x40+len(unshuffled_bytes[0x40:0x56]),2)
        for index in nickname_array_length:
            if unshuffled_bytes[index] == 0xff:
                break
            else:
                char = unshuffled_bytes[index:index+2]
                nickname += char
        nickname = nickname.decode('iso-8859-1', errors='ignore').replace('\u0000','')

    form = get_form(unshuffled_bytes[0x38], dexnr) # % 32
    if dexnr not in range(650):
        dexnr = 0
        nickname = ''
    if unshuffled_bytes[0x33] & 64 and dexnr != 0:
        if dexnr == 490:
            form = '-manaphy'
        else:
            form = ''
        dexnr = 'egg'
    return Pokemon(dexnr, shiny_value < 9, female, form=form, lvl=lvl, item=item, nickname=nickname, route=met_location, cur_hp=cur_hp, max_hp=max_hp) # type: ignore

def pokemon67(data):
    items = items5
    unshuffled_bytes, decrypted_battle_stats, shiny_value, _ = decryptpokemon(data, '67')
    dexnr = int.from_bytes(unshuffled_bytes[:2], 'little')
    item = int.from_bytes(unshuffled_bytes[2:4], 'little')
    female = False
    personality = int.from_bytes(unshuffled_bytes[0x10:0x14], 'little')
    if dexnr in gender_lut:
        female = personality % 256 < gender_lut[dexnr]
    lvl = int(decrypted_battle_stats[4])
    cur_hp = int.from_bytes(decrypted_battle_stats[8:10], 'little')
    max_hp = int.from_bytes(decrypted_battle_stats[10:12], 'little')
    if item in items:
        item = items[item]
    else:
        item = '-'
    met_location = int.from_bytes(unshuffled_bytes[0xD2:0xD4], 'little')
    nickname = unshuffled_bytes[0x38:0x4e].decode('iso-8859-1').split('\u0000\u0000')[0].replace('\u0000', '')
    form = get_form(unshuffled_bytes[0x15], dexnr)#  % 32
    if dexnr not in range(810):
        dexnr = 0
        nickname = ''
    if unshuffled_bytes[0x6f] & 64 and dexnr != 0:
        if dexnr == 490:
            form = '-manaphy'
        else:
            form = form = get_form(unshuffled_bytes[0x15], dexnr)
        dexnr = 'egg'
    return Pokemon(dexnr, shiny_value < 9, female, item=item, form=form, lvl=lvl, nickname=nickname, route=met_location, cur_hp=cur_hp, max_hp=max_hp)


def team(data, edition):
    length = len(data) // 6
    liste = []
    gen = edition // 10

    if gen == 1:
        newdata = b''
        for i in range(6):
            newdata += data[i * 44:i * 44 + 44] + data[i * 11 + 264:11 + i * 11 + 264]
        data = newdata
        for i in range(6):
            liste.append(pokemon1(data[i * length: (i + 1) * length]))
    elif gen == 2:
        newdata = b''
        for i in range(6):
            newdata += data[i * 48:i * 48 + 48] + data[i * 11 + 288:11 + i * 11 + 288] + data[i + 354:i + 355]
        data = newdata
        for i in range(6):
            liste.append(pokemon2(data[i * length: (i + 1) * length]))
    elif gen == 3:
        for i in range(6):
            liste.append(pokemon3(data[i * length: (i + 1) * length], edition))
    elif gen == 4:
        for i in range(6):
            liste.append(pokemon45(data[i * length: (i + 1) * length], 4))
    elif gen == 5:
        for i in range(6):
            liste.append(pokemon45(data[i * length: (i + 1) * length], 5))
    elif gen == 6:
        for i in range(6):
            liste.append(pokemon67(data[i * length: (i + 1) * length]))
    elif gen == 7:
        for i in range(6):
            liste.append(pokemon67(data[i * length: (i + 1) * length]))

    if len(data) % 6 == 1:
        liste.append(data[-1])
    else:
        liste.append(int.from_bytes(data[-2:], 'little'))
    liste.append(edition)

    return liste
