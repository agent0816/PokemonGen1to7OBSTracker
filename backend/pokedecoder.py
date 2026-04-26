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
    cur_hp = int.from_bytes(data[0x1:0x3], "big")
    max_hp = int.from_bytes(data[0x22:0x24], "big")
    nickname = ""
    for char in data[44:]:
        if char in gen1charset:
            nickname += gen1charset[char]
        if char == 80:
            break
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
    cur_hp = int.from_bytes(data[0x22:0x24], "big")
    max_hp = int.from_bytes(data[0x24:0x26], "big")
    nickname = ""
    form = ""
    egg = data[-1] == 0xFD
    for char in data[48:-1]:
        if char in gen1charset:
            nickname += gen1charset[char]
        if char == 80:
            break
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

    experience_points = int.from_bytes(unshuffled_bytes[4:8], 'little')
    # ability = int.from_bytes(unshuffled_bytes[0x0D:0x0E])
    female = False
    nature = personality % 25

    ev_names = ['hp','attack','defense','speed','special_attack','special_defense']
    evs = {ev_names[index]: int(byte) for index, byte in enumerate(unshuffled_bytes[0x19:0x1E])}

    move_bytes = unshuffled_bytes[0x0D:0x15]
    pp_bytes = unshuffled_bytes[0x15:0x19]
    moves = [{f"id": int.from_bytes(move_bytes[2*i:2*i+2], 'little'), "pp": int(byte)} for i, byte in enumerate(pp_bytes)]

    iv_base = int.from_bytes(unshuffled_bytes[0x29:0x2D], 'little')
    ivs = {ev_names[index]: ((iv_base >> (index * 5)) & 0x1F) for index in range(6)}

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
    
    status_bytes = f"{int.from_bytes(data[0x50:0x51]):#010b}".replace('0b','')
    status = {}
    status["sleep"] = int(status_bytes[0:3])
    status["poison"] = int(status_bytes[3])
    status["burn"] = int(status_bytes[4])
    status["freeze"] = int(status_bytes[5])
    status["para"] = int(status_bytes[6])
    status["toxic"] = int(status_bytes[7])
    
    lvl = data[84]
    cur_hp = int.from_bytes(data[0x56:0x58], "little")
    max_hp = int.from_bytes(data[0x58:0x5A], "little")
    met_location = int.from_bytes(unshuffled_bytes[37:38], 'little')

    battle_stats = {ev_names[index]: int.from_bytes(data[index*2 + 0x5A:0x63 + index*2], "little") for index in range(1,6)}

    nickname = ""
    for char in data[8:18]:
        if char in gen3charset:
            nickname += gen3charset[char]
        if char == 0xFF:
            break
    result = Pokemon(species, not shiny_value > 8, female, form=form, lvl=lvl, item=item, nickname=nickname, route=met_location, cur_hp=cur_hp, max_hp=max_hp, checksum_given=checksum_given, checksum_calculated=checksum_calculated, experience_points=experience_points, nature=nature, evs=evs, moves=moves, ivs=ivs, battle_stats=battle_stats, status=status, personality=personality)
    return result


def pokemon45(data, gen):
    charset = gen4charset
    items = items4 if gen == 4 else items5


    unshuffled_bytes, decrypted_battle_stats, shiny_value, personality = decryptpokemon(
        data, "45"
    )
    dexnr = int.from_bytes(unshuffled_bytes[0:2], "little")
    item = int.from_bytes(unshuffled_bytes[2:4], "little")

    experience_points = int.from_bytes(unshuffled_bytes[0x08:0x0C], 'little')
    ability = int.from_bytes(unshuffled_bytes[0x0D:0x0E])
    female = False
    if gen == 5:
        nature = int.from_bytes(unshuffled_bytes[0x39:0x3A])
    else:
        nature = personality % 25

    ev_names = ['hp','attack','defense','speed','special_attack','special_defense']
    evs = {ev_names[index]: int(byte) for index, byte in enumerate(unshuffled_bytes[0x10:0x16])}

    move_bytes = unshuffled_bytes[0x20:0x28]
    pp_bytes = unshuffled_bytes[0x28:0x2C]
    moves = [{f"id": int.from_bytes(move_bytes[2*i:2*i+2], 'little'), "pp": int(byte)} for i, byte in enumerate(pp_bytes)]

    iv_base = int.from_bytes(unshuffled_bytes[0x30:0x34], 'little')
    ivs = {ev_names[index]: ((iv_base >> (index * 5)) & 0x1F) for index in range(6)}

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

    status_bytes = f"{int.from_bytes(decrypted_battle_stats[0:1]):#010b}".replace('0b','')
    status = {}
    status["sleep"] = int(status_bytes[0:3])
    status["poison"] = int(status_bytes[3])
    status["burn"] = int(status_bytes[4])
    status["freeze"] = int(status_bytes[5])
    status["para"] = int(status_bytes[6])
    status["toxic"] = int(status_bytes[7])
    lvl = decrypted_battle_stats[4]
    cur_hp = int.from_bytes(decrypted_battle_stats[6:8], "little")
    max_hp = int.from_bytes(decrypted_battle_stats[8:10], "little")
    battle_stats = {ev_names[index]: int.from_bytes(decrypted_battle_stats[index*2 + 8:10 + index*2], "little") for index in range(1,6)}
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
    return Pokemon(dexnr, shiny_value < 9, female, form=form, lvl=lvl, item=item, nickname=nickname, route=met_location, cur_hp=cur_hp, max_hp=max_hp, checksum_given=checksum_given, checksum_calculated=checksum_calculated, experience_points=experience_points, ability=ability, nature=nature, evs=evs, moves=moves, ivs=ivs, battle_stats=battle_stats, status=status, personality=personality)  # type: ignore


def pokemon67(data, gen):
    items = items6plus
    unshuffled_bytes, decrypted_battle_stats, shiny_value, _ = decryptpokemon(
        data, "67"
    )

    checksum_given = int.from_bytes(data[0x06:0x08], "little")
    checksum_calculated = calculate_checksum(unshuffled_bytes)

    dexnr = int.from_bytes(unshuffled_bytes[:2], "little")
    item = int.from_bytes(unshuffled_bytes[2:4], "little")
    experience_points = int.from_bytes(unshuffled_bytes[0x08:0x0C], 'little')
    ability = int.from_bytes(unshuffled_bytes[0x0C:0x0D])
    # ability_number = int.from_bytes(unshuffled_bytes[0x0D:0x0E])
    female = False
    personality = int.from_bytes(unshuffled_bytes[0x10:0x14], "little")
    nature = int.from_bytes(unshuffled_bytes[0x14:0x15])

    ev_names = ['hp','attack','defense','speed','special_attack','special_defense']
    evs = {ev_names[index]: int(byte) for index, byte in enumerate(unshuffled_bytes[0x16:0x1C])}

    move_bytes = unshuffled_bytes[0x52:0x5A]
    pp_bytes = unshuffled_bytes[0x5A:0x5E]
    moves = [{f"id": int.from_bytes(move_bytes[2*i:2*i+2], 'little'), "pp": int(byte)} for i, byte in enumerate(pp_bytes)]

    iv_base = int.from_bytes(unshuffled_bytes[0x6C:0x70], 'little')
    ivs = {ev_names[index]: ((iv_base >> (index * 5)) & 0x1F) for index in range(6)}

    if dexnr in gender_lut:
        female = personality % 256 < gender_lut[dexnr]
    status_bytes = f"{int.from_bytes(decrypted_battle_stats[0:1]):#010b}".replace('0b','')
    status = {}
    status["sleep"] = int(status_bytes[0:3])
    status["poison"] = int(status_bytes[3])
    status["burn"] = int(status_bytes[4])
    status["freeze"] = int(status_bytes[5])
    status["para"] = int(status_bytes[6])
    status["toxic"] = int(status_bytes[7])
    lvl = int(decrypted_battle_stats[4])
    cur_hp = int.from_bytes(decrypted_battle_stats[8:10], "little")
    max_hp = int.from_bytes(decrypted_battle_stats[10:12], "little")
    battle_stats = {ev_names[index]: int.from_bytes(decrypted_battle_stats[index*2 + 10:12 + index*2], "little") for index in range(1,6)}
    if item in items:
        item = items[item]
    else:
        item = "-"
    met_location = int.from_bytes(unshuffled_bytes[0xD2:0xD4], "little")
    nickname = (unshuffled_bytes[0x38:0x51].decode("iso-8859-1").split("\u0000\u0000")[0].replace("\u0000", ""))
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
    return Pokemon(dexnr,shiny_value < 17,female,item=item,form=form,lvl=lvl,nickname=nickname,route=met_location,cur_hp=cur_hp,max_hp=max_hp, checksum_given=checksum_given, checksum_calculated=checksum_calculated, experience_points=experience_points, ability=ability, nature=nature, evs=evs, moves=moves, ivs=ivs, battle_stats=battle_stats, status=status, personality=personality)


# ============================================================================
# Box-Dekoder (Phase 1)
# ============================================================================
# Box-Pokemon unterscheiden sich von Team-Pokemon primär dadurch, dass
# - kein Battle-Stats-Block existiert (cur_hp/max_hp/Status fehlen),
# - kein Level-Byte gespeichert ist (ab Gen 3 — Level wird aus XP abgeleitet),
# - bei Gen 1/2 Nickname und OT-Name in separaten Arrays außerhalb des
#   33-/32-Byte-Slot-Structs liegen.
# Die Decryption-Logik für Gen 3+ ist identisch zum Team (decryptpokemon*).

def xp_to_level_mediumfast(xp: int) -> int:
    """Näherung Level aus XP via Medium-Fast-Wachstumskurve (lvl = xp^(1/3)).

    Exakt für Medium-Fast-Pokemon (häufigste Wachstumsgruppe).
    Weicht bei Fast/Slow/Erratic/Fluctuating um 1–3 Level ab —
    genau genug für UI-Anzeige, bis eine Wachstumsraten-LUT pro Spezies ergänzt wird.
    """
    if xp <= 0:
        return 1
    lvl = round(xp ** (1 / 3))
    return max(1, min(100, int(lvl)))


def _decode_gen3_string(raw: bytes) -> str:
    """Dekodiert einen Gen-3-String (Nickname/OT), bricht bei 0xFF ab."""
    result = ""
    for char in raw:
        if char == 0xFF:
            break
        if char in gen3charset:
            result += gen3charset[char]
    return result


def _decode_gen4_string(raw: bytes) -> str:
    """Dekodiert einen Gen-4-String (Nickname/OT), bricht bei 0xFF ab.

    Mirror der Logik in pokemon45() — byte-weise mit gen4charset.
    """
    result = ""
    for char in raw:
        if char == 0xFF:
            break
        if char in gen4charset:
            result += gen4charset[char]
    return result


def _decode_gen5_string(raw: bytes) -> str:
    """Dekodiert einen Gen-5-String (UTF-16-LE, Terminator 0xFFFF)."""
    result = b""
    for i in range(0, len(raw) - 1, 2):
        if raw[i] == 0xFF and raw[i + 1] == 0xFF:
            break
        result += raw[i:i + 2]
    return result.decode("iso-8859-1", errors="ignore").replace("\u0000", "")


def _decode_gen67_string(raw: bytes) -> str:
    """Dekodiert einen Gen-6/7-String (UTF-16-LE, Terminator 0x0000)."""
    return raw.decode("iso-8859-1", errors="ignore").split("\u0000\u0000")[0].replace("\u0000", "")


def _is_empty_gen3plus_slot(data: bytes) -> bool:
    """Leerer Box-Slot: PID und OTID beide 0 (Gen 3+)."""
    if len(data) < 8:
        return True
    pid = int.from_bytes(data[:4], "little")
    otid = int.from_bytes(data[4:8], "little")
    return pid == 0 and otid == 0


def box_pokemon3(data: bytes, edition: int):
    """Dekodiert ein Gen-3-Box-Pokemon (80 Bytes).

    Struktur identisch zu den ersten 0x50 Bytes eines Team-Pokemon — nur der
    Battle-Stats-Block (ab 0x50) fehlt. Level wird aus XP abgeleitet.
    """
    if _is_empty_gen3plus_slot(data):
        return None

    personality = int.from_bytes(data[:4], "little")
    otid_full = int.from_bytes(data[4:8], "little")
    ot_id = otid_full & 0xFFFF
    ot_secret_id = otid_full >> 16

    unshuffled_bytes, shiny_value = decryptpokemon3(data)

    species = int.from_bytes(unshuffled_bytes[0:2], "little")
    item = int.from_bytes(unshuffled_bytes[2:4], "little")
    experience_points = int.from_bytes(unshuffled_bytes[4:8], "little")

    ev_names = ["hp", "attack", "defense", "speed", "special_attack", "special_defense"]
    evs = {ev_names[i]: int(b) for i, b in enumerate(unshuffled_bytes[0x19:0x1E])}

    move_bytes = unshuffled_bytes[0x0D:0x15]
    pp_bytes = unshuffled_bytes[0x15:0x19]
    moves = [{"id": int.from_bytes(move_bytes[2 * i:2 * i + 2], "little"), "pp": int(b)}
             for i, b in enumerate(pp_bytes)]

    iv_base = int.from_bytes(unshuffled_bytes[0x29:0x2D], "little")
    ivs = {ev_names[i]: ((iv_base >> (i * 5)) & 0x1F) for i in range(6)}

    checksum_given = int.from_bytes(data[0x1C:0x1E], "little")
    checksum_calculated = calculate_checksum(unshuffled_bytes)

    egg = data[19] == 6
    form = ""

    if species in range(277, 440):
        species = species3_lut[species]
        if isinstance(species, str):
            form = species[3:]
            species = int(species[:3])
        if species == 386:
            form = {34: "-attack", 35: "-defense", 33: "-speed"}.get(edition, "")

    if item in items3:
        item = items3[item]

    female = False
    if species in gender_lut:
        female = personality % 256 < gender_lut[species]

    if egg:
        species = "egg"
        form = ""

    nature = personality % 25
    lvl = xp_to_level_mediumfast(experience_points)
    met_location = int.from_bytes(unshuffled_bytes[37:38], "little")

    nickname = _decode_gen3_string(data[8:18])
    ot_name = _decode_gen3_string(data[0x14:0x1B])

    return Pokemon(
        species, not shiny_value > 8, female, form=form,
        lvl=lvl, item=item, nickname=nickname, route=met_location,
        cur_hp=0, max_hp=0,
        checksum_given=checksum_given, checksum_calculated=checksum_calculated,
        experience_points=experience_points, nature=nature,
        evs=evs, moves=moves, ivs=ivs,
        personality=personality, ot_id=ot_id, ot_secret_id=ot_secret_id,
        ot_name=ot_name, is_boxed=True,
    )


def box_pokemon45(data: bytes, gen: int):
    """Dekodiert ein Gen-4/5-Box-Pokemon (136 Bytes).

    decryptpokemon(data, "45") liefert unshuffled_bytes (128 B) und einen leeren
    battle-stats-Block — da data exakt bei Cutoff 136 endet.
    """
    if _is_empty_gen3plus_slot(data):
        return None

    items = items4 if gen == 4 else items5
    unshuffled_bytes, _, shiny_value, personality = decryptpokemon(data, "45")

    # Offsets sind 1:1 dieselben wie in pokemon45() — unshuffled_bytes startet
    # bei raw 0x08, daher subtrahiert sich der 8-Byte-Header bereits.
    dexnr = int.from_bytes(unshuffled_bytes[0:2], "little")
    item = int.from_bytes(unshuffled_bytes[2:4], "little")
    ot_id = int.from_bytes(unshuffled_bytes[0x04:0x06], "little")
    ot_secret_id = int.from_bytes(unshuffled_bytes[0x06:0x08], "little")
    experience_points = int.from_bytes(unshuffled_bytes[0x08:0x0C], "little")
    ability = int.from_bytes(unshuffled_bytes[0x0D:0x0E])

    if gen == 5:
        nature = int.from_bytes(unshuffled_bytes[0x39:0x3A])
    else:
        nature = personality % 25

    ev_names = ["hp", "attack", "defense", "speed", "special_attack", "special_defense"]
    evs = {ev_names[i]: int(b) for i, b in enumerate(unshuffled_bytes[0x10:0x16])}

    move_bytes = unshuffled_bytes[0x20:0x28]
    pp_bytes = unshuffled_bytes[0x28:0x2C]
    moves = [{"id": int.from_bytes(move_bytes[2 * i:2 * i + 2], "little"), "pp": int(b)}
             for i, b in enumerate(pp_bytes)]

    iv_base = int.from_bytes(unshuffled_bytes[0x30:0x34], "little")
    ivs = {ev_names[i]: ((iv_base >> (i * 5)) & 0x1F) for i in range(6)}

    checksum_given = int.from_bytes(data[0x06:0x08], "little")
    checksum_calculated = calculate_checksum(unshuffled_bytes)

    if dexnr >= 650:
        return Pokemon(0, item="-", nickname="", lvl=1, is_boxed=True)

    item_name = items.get(item, "-") if item in items else "-"

    met_location = int.from_bytes(unshuffled_bytes[0x3E:0x40], "little")
    if met_location == 0:
        met_location = int.from_bytes(unshuffled_bytes[0x78:0x7A], "little") if len(unshuffled_bytes) >= 0x7A else 0

    female = False
    if dexnr in range(650):
        female = personality % 256 < gender_lut[dexnr]

    if gen == 4:
        nickname = _decode_gen4_string(unshuffled_bytes[0x40:0x56])
        ot_name = _decode_gen4_string(unshuffled_bytes[0x60:0x70])
    else:
        nickname = _decode_gen5_string(unshuffled_bytes[0x40:0x56])
        ot_name = _decode_gen5_string(unshuffled_bytes[0x60:0x70])

    form = get_form(unshuffled_bytes[0x38], dexnr, gen)

    egg_flag = bool(unshuffled_bytes[0x33] & 0x40)
    if egg_flag and dexnr != 0:
        if dexnr == 490:
            form = "-manaphy"
        else:
            form = ""
        dexnr = "egg"

    lvl = xp_to_level_mediumfast(experience_points)

    return Pokemon(
        dexnr, shiny_value < 9, female, form=form,
        lvl=lvl, item=item_name, nickname=nickname, route=met_location,
        cur_hp=0, max_hp=0,
        checksum_given=checksum_given, checksum_calculated=checksum_calculated,
        experience_points=experience_points, ability=ability, nature=nature,
        evs=evs, moves=moves, ivs=ivs,
        personality=personality, ot_id=ot_id, ot_secret_id=ot_secret_id,
        ot_name=ot_name, is_boxed=True,
    )


def box_pokemon67(data: bytes, gen: int):
    """Dekodiert ein Gen-6/7-Box-Pokemon (232 Bytes)."""
    if _is_empty_gen3plus_slot(data):
        return None

    unshuffled_bytes, _, shiny_value, _ = decryptpokemon(data, "67")

    checksum_given = int.from_bytes(data[0x06:0x08], "little")
    checksum_calculated = calculate_checksum(unshuffled_bytes)

    # Offsets sind 1:1 dieselben wie in pokemon67() — unshuffled_bytes startet
    # bei raw 0x08, daher subtrahiert sich der 8-Byte-Header bereits.
    dexnr = int.from_bytes(unshuffled_bytes[:2], "little")
    item = int.from_bytes(unshuffled_bytes[2:4], "little")
    ot_id = int.from_bytes(unshuffled_bytes[0x04:0x06], "little")
    ot_secret_id = int.from_bytes(unshuffled_bytes[0x06:0x08], "little")
    experience_points = int.from_bytes(unshuffled_bytes[0x08:0x0C], "little")
    ability = int.from_bytes(unshuffled_bytes[0x0C:0x0D])
    personality = int.from_bytes(unshuffled_bytes[0x10:0x14], "little")
    nature = int.from_bytes(unshuffled_bytes[0x14:0x15])

    ev_names = ["hp", "attack", "defense", "speed", "special_attack", "special_defense"]
    evs = {ev_names[i]: int(b) for i, b in enumerate(unshuffled_bytes[0x16:0x1C])}

    move_bytes = unshuffled_bytes[0x52:0x5A]
    pp_bytes = unshuffled_bytes[0x5A:0x5E]
    moves = [{"id": int.from_bytes(move_bytes[2 * i:2 * i + 2], "little"), "pp": int(b)}
             for i, b in enumerate(pp_bytes)]

    iv_base = int.from_bytes(unshuffled_bytes[0x6C:0x70], "little")
    ivs = {ev_names[i]: ((iv_base >> (i * 5)) & 0x1F) for i in range(6)}

    female = False
    if dexnr in gender_lut:
        female = personality % 256 < gender_lut[dexnr]

    if item in items6plus:
        item = items6plus[item]
    else:
        item = "-"

    met_location = int.from_bytes(unshuffled_bytes[0xD2:0xD4], "little") if len(unshuffled_bytes) >= 0xD4 else 0
    nickname = _decode_gen67_string(unshuffled_bytes[0x38:0x51])
    ot_name = _decode_gen67_string(unshuffled_bytes[0xA8:0xC0]) if len(unshuffled_bytes) >= 0xC0 else ""
    form = get_form(unshuffled_bytes[0x15], dexnr, gen)

    if dexnr not in range(810):
        dexnr = 0
        nickname = ""

    egg_flag = bool(unshuffled_bytes[0x6F] & 0x40)
    if egg_flag and dexnr != 0:
        if dexnr == 490:
            form = "-manaphy"
        dexnr = "egg"

    lvl = xp_to_level_mediumfast(experience_points)

    return Pokemon(
        dexnr, shiny_value < 17, female, item=item, form=form,
        lvl=lvl, nickname=nickname, route=met_location,
        cur_hp=0, max_hp=0,
        checksum_given=checksum_given, checksum_calculated=checksum_calculated,
        experience_points=experience_points, ability=ability, nature=nature,
        evs=evs, moves=moves, ivs=ivs,
        personality=personality, ot_id=ot_id, ot_secret_id=ot_secret_id,
        ot_name=ot_name, is_boxed=True,
    )


def _decode_gen1_string(raw: bytes) -> str:
    """Dekodiert einen Gen-1-String (Nickname/OT), bricht bei 0x50 ab."""
    result = ""
    for char in raw:
        if char == 0x50:
            break
        if char in gen1charset:
            result += gen1charset[char]
    return result


def box_pokemon1(data: bytes, ot_name_bytes: bytes = b"", nickname_bytes: bytes = b""):
    """Dekodiert ein Gen-1-Box-Pokemon (33-Byte-Struct).

    Nickname und OT-Name liegen in Gen 1/2 nicht im Slot, sondern in separaten
    Arrays pro Box — deshalb als extra Parameter.
    """
    if len(data) < 33 or data[0] == 0 or data[0] == 0xFF:
        return None

    dexnr = data[0]
    if dexnr in species1_lut:
        dexnr = species1_lut.get(dexnr)

    cur_hp = int.from_bytes(data[0x01:0x03], "big")
    lvl = data[0x03]
    ot_id = int.from_bytes(data[0x0C:0x0E], "big")
    experience_points = int.from_bytes(data[0x0E:0x11], "big")

    ev_names = ["hp", "attack", "defense", "speed", "special"]
    evs = {name: int.from_bytes(data[0x11 + 2 * i:0x13 + 2 * i], "big") for i, name in enumerate(ev_names)}

    iv_byte1, iv_byte2 = data[0x1B], data[0x1C]
    ivs = {
        "attack": iv_byte1 >> 4,
        "defense": iv_byte1 & 0x0F,
        "speed": iv_byte2 >> 4,
        "special": iv_byte2 & 0x0F,
    }

    move_ids = list(data[0x08:0x0C])
    pp_values = list(data[0x1D:0x21])
    moves = [{"id": mid, "pp": pp} for mid, pp in zip(move_ids, pp_values)]

    nickname = _decode_gen1_string(nickname_bytes)
    ot_name = _decode_gen1_string(ot_name_bytes)

    return Pokemon(
        dexnr, False,
        lvl=lvl, nickname=nickname, cur_hp=cur_hp, max_hp=cur_hp,
        experience_points=experience_points, evs=evs, ivs=ivs, moves=moves,
        ot_id=ot_id, ot_name=ot_name, is_boxed=True,
    )


def box_pokemon2(data: bytes, ot_name_bytes: bytes = b"", nickname_bytes: bytes = b""):
    """Dekodiert ein Gen-2-Box-Pokemon (32-Byte-Struct)."""
    unown_letter = ["-a","-b","-c","-d","-e","-f","-g","-h","-i","-j","-k","-l","-m",
                    "-n","-o","-p","-q","-r","-s","-t","-u","-v","-w","-x","-y","-z"]
    if len(data) < 32 or data[0] == 0 or data[0] == 0xFF:
        return None

    dexnr = data[0]
    item = data[1]
    if item in items2:
        item = items2[item]
    lvl = data[0x1F]
    ot_id = int.from_bytes(data[0x06:0x08], "big")
    experience_points = int.from_bytes(data[0x08:0x0B], "big")

    ev_names = ["hp", "attack", "defense", "speed", "special"]
    evs = {name: int.from_bytes(data[0x0B + 2 * i:0x0D + 2 * i], "big") for i, name in enumerate(ev_names)}

    iv_byte1, iv_byte2 = data[0x15], data[0x16]
    ivs = {
        "attack": iv_byte1 >> 4,
        "defense": iv_byte1 & 0x0F,
        "speed": iv_byte2 >> 4,
        "special": iv_byte2 & 0x0F,
    }

    move_ids = list(data[0x02:0x06])
    pp_values = list(data[0x17:0x1B])
    moves = [{"id": mid, "pp": pp} for mid, pp in zip(move_ids, pp_values)]

    form = ""
    if dexnr == 201:
        letter = (
            (((ivs["attack"] >> 1) % 4) << 6)
            + (((ivs["defense"] >> 1) % 4) << 4)
            + (((ivs["speed"] >> 1) % 4) << 2)
            + ((ivs["special"] >> 1) % 4)
        )
        form = unown_letter[letter // 10]

    nickname = _decode_gen1_string(nickname_bytes)
    ot_name = _decode_gen1_string(ot_name_bytes)

    return Pokemon(
        dexnr, False, form=form,
        lvl=lvl, nickname=nickname, item=item,
        experience_points=experience_points, evs=evs, ivs=ivs, moves=moves,
        ot_id=ot_id, ot_name=ot_name, is_boxed=True,
    )


# ---------- Box-Level-Parser (eine ganze Box → Liste von Pokemon|None) -------

def decode_gen1_box(box_bytes: bytes) -> list:
    """Parst eine Gen-1-Box (1122 Bytes) in 20 Slots.

    Layout: count(1) + species_list(21) + 20×struct(33) + 20×OT(11) + 20×nick(11).
    """
    count = box_bytes[0]
    poke_start = 0x16
    ot_start = poke_start + 20 * 33
    nick_start = ot_start + 20 * 11

    slots = []
    for slot in range(20):
        if slot >= count:
            slots.append(None)
            continue
        poke = box_bytes[poke_start + slot * 33:poke_start + (slot + 1) * 33]
        ot = box_bytes[ot_start + slot * 11:ot_start + (slot + 1) * 11]
        nick = box_bytes[nick_start + slot * 11:nick_start + (slot + 1) * 11]
        slots.append(box_pokemon1(poke, ot, nick))
    return slots


def decode_gen2_box(box_bytes: bytes) -> list:
    """Parst eine Gen-2-Box (1104 Bytes) in 20 Slots.

    Layout: count(1) + species_list(21) + 20×struct(32) + 20×OT(11) + 20×nick(11).
    """
    count = box_bytes[0]
    poke_start = 0x16
    ot_start = poke_start + 20 * 32
    nick_start = ot_start + 20 * 11

    slots = []
    for slot in range(20):
        if slot >= count:
            slots.append(None)
            continue
        poke = box_bytes[poke_start + slot * 32:poke_start + (slot + 1) * 32]
        ot = box_bytes[ot_start + slot * 11:ot_start + (slot + 1) * 11]
        nick = box_bytes[nick_start + slot * 11:nick_start + (slot + 1) * 11]
        slots.append(box_pokemon2(poke, ot, nick))
    return slots


def decode_gen3_box(box_bytes: bytes, edition: int, slots_per_box: int = 30) -> list:
    """Parst eine Gen-3-Box (Array aus 80-Byte-Slots)."""
    return [box_pokemon3(box_bytes[i * 80:(i + 1) * 80], edition) for i in range(slots_per_box)]


def decode_gen45_box(box_bytes: bytes, gen: int, slots_per_box: int = 30) -> list:
    """Parst eine Gen-4/5-Box (Array aus 136-Byte-Slots)."""
    return [box_pokemon45(box_bytes[i * 136:(i + 1) * 136], gen) for i in range(slots_per_box)]


def decode_gen67_box(box_bytes: bytes, gen: int, slots_per_box: int = 30) -> list:
    """Parst eine Gen-6/7-Box (Array aus 232-Byte-Slots)."""
    return [box_pokemon67(box_bytes[i * 232:(i + 1) * 232], gen) for i in range(slots_per_box)]


def box_slot_size(edition: int) -> int:
    """Größe eines Box-Slots in Bytes für die jeweilige Edition.

    Gen 1: 33 B (Box-Pokemon-Struct, ohne Nickname/OT-Array).
    Gen 2: 32 B.
    Gen 3: 80 B (verschlüsselt, inkl. Header + 4 Datenblöcke).
    Gen 4/5: 136 B (verschlüsselt, ohne Battle-Stats).
    Gen 6/7: 232 B.
    """
    gen = edition // 10
    if gen == 1:
        return 33
    if gen == 2:
        return 32
    if gen == 3:
        return 80
    if gen in (4, 5):
        return 136
    if gen in (6, 7):
        return 232
    raise ValueError(f"Unbekannte Generation für edition={edition} (gen={gen})")


def slots_per_box(edition: int) -> int:
    """Anzahl Slots pro PC-Box (Gen 1/2: 20, Gen 3+: 30)."""
    gen = edition // 10
    if gen in (1, 2):
        return 20
    return 30


def decode_box(box_bytes: bytes, edition: int) -> list:
    """Dispatcher: dekodiert eine Box anhand der Edition zur passenden Gen-Funktion.

    Gibt eine Liste von Pokemon|None zurück (Länge = Slots pro Box;
    Gen 1/2 = 20, Gen 3+ = 30). Box-Bytes müssen das komplette rohe Box-Layout
    der Edition enthalten (Gen 1/2 inkl. OT- und Nickname-Arrays).
    """
    gen = edition // 10
    if gen == 1:
        return decode_gen1_box(box_bytes)
    if gen == 2:
        return decode_gen2_box(box_bytes)
    if gen == 3:
        return decode_gen3_box(box_bytes, edition)
    if gen in (4, 5):
        return decode_gen45_box(box_bytes, gen)
    if gen in (6, 7):
        return decode_gen67_box(box_bytes, gen)
    raise ValueError(f"Unbekannte Generation für edition={edition} (gen={gen})")


def decode_box_name(raw: bytes, gen: int) -> str:
    """Dekodiert einen Box-Namen gemäß der Generation."""
    if gen == 1:
        return ""
    if gen == 2:
        return _decode_gen1_string(raw)
    if gen == 3:
        return _decode_gen3_string(raw)
    if gen == 4:
        return _decode_gen4_string(raw)
    if gen == 5:
        return _decode_gen5_string(raw)
    if gen in (6, 7):
        return _decode_gen67_string(raw)
    return ""


# ============================================================================
# Ende Box-Dekoder
# ============================================================================


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
        for i in range(6):
            liste.append(pokemon1(newdata[i * length : (i + 1) * length]))
    elif gen == 2:
        newdata = b""
        for i in range(6):
            newdata += (
                data[i * 48 : i * 48 + 48]
                + data[i * 11 + 288 : 11 + i * 11 + 288]
                + data[i + 354 : i + 355]
            )
        for i in range(6):
            liste.append(pokemon2(newdata[i * length : (i + 1) * length]))
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
