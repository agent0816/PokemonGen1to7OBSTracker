import yaml
from backend.classes.Pokemon import Pokemon

species1_lut = yaml.safe_load(open("backend/data/species1.yml"))
species3_lut = yaml.safe_load(open("backend/data/species3.yml"))
gen1charset = yaml.safe_load(open("backend/data/gen1charset.yml"))
gen3charset = yaml.safe_load(open("backend/data/gen3charset.yml"))
gen4charset = yaml.safe_load(open("backend/data/gen4charset.yml"))
gender_lut = yaml.safe_load(open("backend/data/gender_lut.yml"))
items2 = yaml.safe_load(open("backend/data/items2.yml"))
items3 = yaml.safe_load(open("backend/data/items3.yml"))
items4 = yaml.safe_load(open("backend/data/items4.yml"))
items5 = yaml.safe_load(open("backend/data/items5.yml"))
items6plus = yaml.safe_load(open("backend/data/items.yml"))
forms = yaml.safe_load(open("backend/data/forms.yml"))

def decryptpokemon(data, gen):
    def prng(seed):
        return (0x41C64E6D * seed + 0x6073) % 0x100000000

    offset_lut = [[0, 1, 2, 3],[0, 1, 3, 2],[0, 2, 1, 3],[0, 3, 1, 2],[0, 2, 3, 1],[0, 3, 2, 1],[1, 0, 2, 3],[1, 0, 3, 2],[2, 0, 1, 3],[3, 0, 1, 2],[2, 0, 3, 1],[3, 0, 2, 1],[1, 2, 0, 3],[1, 3, 0, 2],[2, 1, 0, 3],[3, 1, 0, 2],[2, 3, 0, 1],[3, 2, 0, 1],[1, 2, 3, 0],[1, 3, 2, 0],[2, 1, 3, 0],[3, 1, 2, 0],[2, 3, 1, 0],[3, 2, 1, 0],]
    encryption_key = int.from_bytes(data[:4], "little")
    shift_value = ((encryption_key & 0x3E000) >> 0xD) % 24
    if gen == "45":
        cutoff = 136
        blocksize = 32
        key = int.from_bytes(data[6:8], "little")
    else:
        cutoff = 232
        blocksize = 56
        key = encryption_key
    encrypted_bytes = data[8:cutoff]
    decrypted_bytes = b""
    for i in range(len(encrypted_bytes) // 2):
        key = prng(key)
        decrypted_bytes += (
            int.from_bytes(encrypted_bytes[i * 2 : i * 2 + 2], "little") ^ (key >> 16)
        ).to_bytes(2, "little")
    unshuffled_bytes = b""
    for i in range(4):
        start_offset = offset_lut[shift_value][i] * blocksize
        end_offset = start_offset + blocksize
        unshuffled_bytes += decrypted_bytes[start_offset : end_offset]
    tid = int.from_bytes(unshuffled_bytes[4:6], "little")
    sid = int.from_bytes(unshuffled_bytes[6:8], "little")
    if gen == "67":
        personality_value = int.from_bytes(unshuffled_bytes[16:20], "little")
    else:
        personality_value = encryption_key
    shiny_value = tid ^ sid ^ (personality_value >> 16) ^ (personality_value % 0x10000)
    encrypted_battle_stats = data[cutoff:]
    key = encryption_key
    decrypted_battle_stats = b""
    for i in range(len(encrypted_battle_stats) // 2):
        key = prng(key)
        decrypted_battle_stats += (
            int.from_bytes(encrypted_battle_stats[i * 2 : i * 2 + 2], "little")
            ^ (key >> 16)
        ).to_bytes(2, "little")
    return (unshuffled_bytes, decrypted_battle_stats, shiny_value, encryption_key)

def decryptpokemon3(data):
    # offset_lut = [[0, 1, 2, 3],[0, 1, 3, 2],[0, 2, 1, 3],[0, 2, 3, 1],[0, 3, 1, 2],[0, 3, 2, 1],[1, 0, 2, 3],[1, 0, 3, 2],[1, 2, 0, 3],[1, 2, 3, 0],[1, 3, 0, 2],[1, 3, 2, 0],[2, 0, 1, 3],[2, 0, 3, 1],[2, 1, 0, 3],[2, 1, 3, 0],[2, 3, 0, 1],[2, 3, 1, 0],[3, 0, 1, 2],[3, 0, 2, 1],[3, 1, 0, 2],[3, 1, 2, 0],[3, 2, 0, 1],[3, 2, 1, 0],]
    offset_lut = [[0, 1, 2, 3],[0, 1, 3, 2],[0, 2, 1, 3],[0, 3, 1, 2],[0, 2, 3, 1],[0, 3, 2, 1],[1, 0, 2, 3],[1, 0, 3, 2],[2, 0, 1, 3],[3, 0, 1, 2],[2, 0, 3, 1],[3, 0, 2, 1],[1, 2, 0, 3],[1, 3, 0, 2],[2, 1, 0, 3],[3, 1, 0, 2],[2, 3, 0, 1],[3, 2, 0, 1],[1, 2, 3, 0],[1, 3, 2, 0],[2, 1, 3, 0],[3, 1, 2, 0],[2, 3, 1, 0],[3, 2, 1, 0],]

    personality = int.from_bytes(data[:4], "little")
    otid = int.from_bytes(data[4:8], "little")
    key = (personality ^ otid )

    blocksize = 12
    shift_value = personality % 24

    shiny_value = (key % 0x10000 ^ key >> 16)

    encrypted_bytes = data[0x20:0x50]
    decrypted_bytes = b""

    for i in range(0, len(encrypted_bytes), 4):  # 32-Bit-Schritte (4 Bytes)
        encrypted_word = int.from_bytes(encrypted_bytes[i:i+4], 'little')
        decrypted_word = encrypted_word ^ key  # XOR mit dem Schlüssel
        decrypted_bytes += decrypted_word.to_bytes(4, 'little')


    unshuffled_bytes = b""
    for i in range(4):
        start_offset = offset_lut[shift_value][i] * blocksize
        end_offset = start_offset + blocksize
        unshuffled_bytes += decrypted_bytes[start_offset : end_offset]

    return (unshuffled_bytes, shiny_value)

def get_form(data, dexnr, gen):
    mega_dict = {0x00: "", 0x08: "-mega"}
    primal_dict = {0x00: "", 0x08: "-primal"}
    alola_dict = {0x00: "", 0x08: "-alola"}

    forms_dict: dict = forms.get("forms_dict")
    mega: list = forms.get("mega")
    alola: list = forms.get("alola")
    primal: list = forms.get("primal")

    form = data - data % 8
    if dexnr in forms_dict.keys():
        species_form: dict = forms_dict.get(dexnr)
        if dexnr == 25:
            species_form: dict = species_form.get(gen, {0x00: ""})
        elif dexnr == 493 and gen == 4:
            species_form = forms.get("arceus_gen_4")
        form = species_form.get(form, 0)
    if dexnr in mega:
        form = mega_dict.get(form, 0)
    if dexnr in alola:
        form = alola_dict.get(form, 0)
    if dexnr in primal:
        form = primal_dict.get(form, 0)
    if isinstance(form, int):
        form = ""

    return form


def calculate_checksum(data: bytes) -> int:
    checksum = 0

    # Prüfen, ob die Daten lang genug sind
    if len(data) % 2 != 0:
        raise ValueError("Daten sind zu kurz für die angegebene Bereichsauswahl.")

    # Daten in 2-Byte-Wörter aufteilen und summieren
    for i in range(0, len(data), 2):
        word = int.from_bytes(data[i : i + 2], "little")
        checksum += word

    checksum &= 0xFFFF
    
    return checksum


def pokemon1(data):
    dexnr = data[0]
    if dexnr in species1_lut:
        dexnr = species1_lut.get(dexnr)
    lvl = data[0x21]
    cur_hp = int.from_bytes(data[0x1:0x3], "little")
    max_hp = int.from_bytes(data[0x22:0x24], "little")
    nickname = ""
    for char in data[44:]:
        if char in gen1charset:
            nickname += gen1charset[char]
    return Pokemon(
        dexnr, False, lvl=lvl, nickname=nickname, cur_hp=cur_hp, max_hp=max_hp
    )


def pokemon2(data):
    unown_letter = ["-a","-b","-c","-d","-e","-f","-g","-h","-i","-j","-k","-l","-m","-n","-o","-p","-q","-r","-s","-t","-u","-v","-w","-x","-y","-z",]
    dexnr = data[0]
    item = data[1]
    if item in items2:
        item = items2[item]
    lvl = data[0x1F]
    cur_hp = int.from_bytes(data[0x22:0x24], "little")
    max_hp = int.from_bytes(data[0x24:0x26], "little")
    nickname = ""
    form = ""
    egg = data[-1] == 0xFD
    for char in data[48:-1]:
        if char in gen1charset:
            nickname += gen1charset[char]
    ivs = [data[0x15] >> 4, data[0x15] % 16, data[0x16] >> 4, data[0x16] % 16]
    if dexnr == 201:
        letter = (
            (((ivs[0] >> 1) % 4) << 6)
            + (((ivs[1] >> 1) % 4) << 4)
            + (((ivs[2] >> 1) % 4) << 2)
            + ((ivs[3] >> 1) % 4)
        )
        letter = letter // 10
        form = unown_letter[letter]
    if egg:
        dexnr = "egg"
        form = ""

    return Pokemon(dexnr, False, lvl=lvl, form=form, nickname=nickname, item=item, cur_hp=cur_hp, max_hp=max_hp)  # type: ignore


def pokemon3(data, edition):
    growth_lut = [32,32,32,32,32,32,44,44,56,68,56,68,44,44,56,68,56,68,44,44,56,68,56,68,]
    attack_lut = [44, 44, 56, 68, 56, 68, 32, 32, 32, 32, 32, 32, 56, 68, 44, 44, 68, 56, 56, 68, 44, 44, 68, 56]
    ev_lut = [56, 68, 44, 44, 68, 56, 56, 68, 44, 44, 68, 56, 32, 32, 32, 32, 32, 32, 68, 56, 68, 56, 44, 44]
    misc_lut = [68,56,68,56,44,44,68,56,68,56,44,44,68,56,68,56,44,44,32,32,32,32,32,32,]
    egg = False
    form = ""
    personality = int.from_bytes(data[:4], "little")

    unshuffled_bytes, shiny_value = decryptpokemon3(data)

    checksum_given = int.from_bytes(data[0x1C:0x1E], 'little')
    checksum_calculated = calculate_checksum(unshuffled_bytes)

    if data[19] == 6:
        egg = True
    
    species = int.from_bytes(unshuffled_bytes[0:2], 'little')
    item = int.from_bytes(unshuffled_bytes[2:4], 'little')

    if item in items3:
        item = items3[item]
    if species in range(277, 440):
        species = species3_lut[species]
        if isinstance(species, str):
            form = species[3:]
            species = int(species[:3])
        if species == 386:
            if edition == 34:
                form = "-attack"
            if edition == 35:
                form = "-defense"
            if edition == 33:
                form = "-speed"
    female = False
    if species in gender_lut:
        female = personality % 256 < gender_lut[species]
    if egg:
        form = ""
        species = "egg"
    lvl = data[84]
    cur_hp = int.from_bytes(data[0x56:0x58], "little")
    max_hp = int.from_bytes(data[0x58:0x5A], "little")
    met_location = int.from_bytes(unshuffled_bytes[37:38], 'little')
    nickname = ""
    for char in data[8:18]:
        if char in gen3charset:
            nickname += gen3charset[char]
        if char == 0xFF:
            break
    return Pokemon(species, not shiny_value > 8, female, form=form, lvl=lvl, item=item, nickname=nickname, route=met_location, cur_hp=cur_hp, max_hp=max_hp, checksum_given=checksum_given, checksum_calculated=checksum_calculated)  # type: ignore


def pokemon45(data, gen):
    charset = gen4charset
    items = items4 if gen == 4 else items5


    unshuffled_bytes, decrypted_battle_stats, shiny_value, personality = decryptpokemon(
        data, "45"
    )
    dexnr = int.from_bytes(unshuffled_bytes[0:2], "little")
    item = int.from_bytes(unshuffled_bytes[2:4], "little")

    checksum_given = int.from_bytes(data[0x06:0x08], "little")
    checksum_calculated = calculate_checksum(unshuffled_bytes)

    if dexnr >= 650:
        return Pokemon(0, item="-", nickname="", lvl=1)

    if item in items:
        item = items[item]
    else:
        item = "-"
    met_location = int.from_bytes(unshuffled_bytes[0x3E:0x40], "little")
    if met_location == 0:  # diamant/perl
        met_location = int.from_bytes(unshuffled_bytes[0x78:0x7A], "little")
    lvl = decrypted_battle_stats[4]
    cur_hp = int.from_bytes(decrypted_battle_stats[6:8], "little")
    max_hp = int.from_bytes(decrypted_battle_stats[8:10], "little")
    if dexnr in range(650):
        female = personality % 256 < gender_lut[dexnr]
    else:
        female = False
    if gen == 4:
        nickname = ""
        for char in unshuffled_bytes[0x40:0x56]:
            if char == 0xFF:
                break
            if char in charset:
                nickname += charset[char]
    else:
        nickname = b""
        nickname_array_length = range(0x40, 0x40 + len(unshuffled_bytes[0x40:0x56]), 2)
        for index in nickname_array_length:
            if unshuffled_bytes[index] == 0xFF:
                break
            else:
                char = unshuffled_bytes[index : index + 2]
                nickname += char
        nickname = nickname.decode("iso-8859-1", errors="ignore").replace("\u0000", "")

    form = get_form(unshuffled_bytes[0x38], dexnr, gen)  # % 32
    if dexnr not in range(650):
        dexnr = 0
        nickname = ""
    if unshuffled_bytes[0x33] & 64 and dexnr != 0:
        if dexnr == 490:
            form = "-manaphy"
        else:
            form = ""
        dexnr = "egg"
    return Pokemon(dexnr, shiny_value < 9, female, form=form, lvl=lvl, item=item, nickname=nickname, route=met_location, cur_hp=cur_hp, max_hp=max_hp, checksum_given=checksum_given, checksum_calculated=checksum_calculated)  # type: ignore


def pokemon67(data, gen):
    items = items6plus
    unshuffled_bytes, decrypted_battle_stats, shiny_value, _ = decryptpokemon(
        data, "67"
    )

    checksum_given = int.from_bytes(data[0x06:0x08], "little")
    checksum_calculated = calculate_checksum(unshuffled_bytes)

    dexnr = int.from_bytes(unshuffled_bytes[:2], "little")
    item = int.from_bytes(unshuffled_bytes[2:4], "little")
    female = False
    personality = int.from_bytes(unshuffled_bytes[0x10:0x14], "little")
    if dexnr in gender_lut:
        female = personality % 256 < gender_lut[dexnr]
    lvl = int(decrypted_battle_stats[4])
    cur_hp = int.from_bytes(decrypted_battle_stats[8:10], "little")
    max_hp = int.from_bytes(decrypted_battle_stats[10:12], "little")
    if item in items:
        item = items[item]
    else:
        item = "-"
    met_location = int.from_bytes(unshuffled_bytes[0xD2:0xD4], "little")
    nickname = (unshuffled_bytes[0x38:0x4E].decode("iso-8859-1").split("\u0000\u0000")[0].replace("\u0000", ""))
    form = get_form(unshuffled_bytes[0x15], dexnr, gen)  #  % 32
    if dexnr not in range(810):
        dexnr = 0
        nickname = ""
    if unshuffled_bytes[0x6F] & 64 and dexnr != 0:
        if dexnr == 490:
            form = "-manaphy"
        else:
            form = form = get_form(unshuffled_bytes[0x15], dexnr, gen)
        dexnr = "egg"
    return Pokemon(dexnr,shiny_value < 17,female,item=item,form=form,lvl=lvl,nickname=nickname,route=met_location,cur_hp=cur_hp,max_hp=max_hp, checksum_given=checksum_given, checksum_calculated=checksum_calculated)


def team(data, edition):
    length = len(data) // 6
    liste = []
    gen = edition // 10

    if gen == 1:
        newdata = b""
        for i in range(6):
            newdata += (
                data[i * 44 : i * 44 + 44] + data[i * 11 + 264 : 11 + i * 11 + 264]
            )
        data = newdata
        for i in range(6):
            liste.append(pokemon1(data[i * length : (i + 1) * length]))
    elif gen == 2:
        newdata = b""
        for i in range(6):
            newdata += (
                data[i * 48 : i * 48 + 48]
                + data[i * 11 + 288 : 11 + i * 11 + 288]
                + data[i + 354 : i + 355]
            )
        data = newdata
        for i in range(6):
            liste.append(pokemon2(data[i * length : (i + 1) * length]))
    elif gen == 3:
        for i in range(6):
            liste.append(pokemon3(data[i * length : (i + 1) * length], edition))
    elif gen == 4:
        for i in range(6):
            liste.append(pokemon45(data[i * length : (i + 1) * length], 4))
    elif gen == 5:
        for i in range(6):
            liste.append(pokemon45(data[i * length : (i + 1) * length], 5))
    elif gen == 6:
        for i in range(6):
            liste.append(pokemon67(data[i * length : (i + 1) * length], gen))
    elif gen == 7:
        for i in range(6):
            liste.append(pokemon67(data[i * length : (i + 1) * length], gen))

    if len(data) % 6 == 1:
        liste.append(data[-1])
    else:
        liste.append(int.from_bytes(data[-2:], "little"))
    liste.append(edition)

    return liste
