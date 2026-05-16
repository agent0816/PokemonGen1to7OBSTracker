import re
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from backend.logging_setup import get_logger


@dataclass
class RandoPokemon:
    dexnr: int
    name: str
    types: list[str]
    stats: dict[str, int]
    abilities: list[str]
    item: str = ''
    tm_compat: list[bool] = field(default_factory=list)


@dataclass
class MoveUpdate:
    name: str
    changes: dict[str, str]


@dataclass
class TrainerPokemon:
    name: str
    level: int
    ability: str
    moves: list[str]
    held_item: str = ''


@dataclass
class Trainer:
    id: int
    class_name: str
    pokemon: list[TrainerPokemon]


@dataclass
class InGameTrade:
    old_requested: str
    old_nickname: str
    old_given: str
    new_requested: str
    new_nickname: str
    new_given: str


@dataclass
class WildEncounter:
    name: str
    level: int
    stats: dict[str, int]
    max_level: int = 0


@dataclass
class WildArea:
    set_number: int
    location: str
    method: str
    rate: int
    encounters: list[WildEncounter]


@dataclass
class RandomizerLogData:
    version: str = ''
    seed: str = ''
    settings_string: str = ''
    pokemon: dict[int, RandoPokemon] = field(default_factory=dict)
    pokemon_name_to_id: dict[str, int] = field(default_factory=dict)
    trainers: dict[int, Trainer] = field(default_factory=dict)
    tm_moves: dict[int, str] = field(default_factory=dict)
    hm_moves: dict[int, str] = field(default_factory=dict)
    wild_areas: list[WildArea] = field(default_factory=list)
    starters: list[str] = field(default_factory=list)
    evolutions: dict[int, list[str]] = field(default_factory=dict)
    static_pokemon: list[tuple[str, str]] = field(default_factory=list)
    move_updates: list[MoveUpdate] = field(default_factory=list)
    in_game_trades: list[InGameTrade] = field(default_factory=list)


KNOWN_ENCOUNTER_METHODS = [
    'Super Rod', 'Good Rod', 'Old Rod', 'Surf', 'Surfing',
    'Grass/Cave', 'Rock Smash', 'Headbutt',
    'Doubles Grass', 'Shaking Spots', 'Dark Grass',
    'Yellow Flowers', 'Red Flowers', 'Purple Flowers',
    'Tall Grass', 'Horde', 'Rough Terrain',
    'Bug Catching', 'Fishing',
]


class RandomizerLogParser:
    SECTION_PARSE_ORDER = [
        "--Move Updates--",
        "--Pokemon Base Stats & Types--",
        "--Removing Impossible Evolutions--",
        "--Removing Timed-Based Evolutions--",
        "--Randomized Evolutions--",
        "--Random Starters--",
        "--Custom Starters--",
        "--TM Moves--",
        "--TM Compatibility--",
        "--Trainers Pokemon--",
        "--Wild Pokemon--",
        "--Static Pokemon--",
        "--In-Game Trades--",
    ]

    def __init__(self):
        self.logger = get_logger(__name__, './logs/randomizer_log_parser.log')
        self._data: RandomizerLogData | None = None

    def parse(self, log_path: str) -> RandomizerLogData | None:
        path = Path(log_path)
        if not path.exists():
            self.logger.error(f"Log-Datei nicht gefunden: {log_path}")
            return None

        try:
            with open(path, 'r', encoding='utf-8-sig') as f:
                lines = [line.rstrip('\n').rstrip('\r') for line in f.readlines()]
        except Exception as err:
            self.logger.error(f"Fehler beim Lesen der Log-Datei: {err}")
            self.logger.error(traceback.format_exc())
            return None

        self._data = RandomizerLogData()
        self._parse_header(lines)

        section_starts: dict[str, int] = {}
        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith("--") and stripped.endswith("--") and stripped.strip('-').strip():
                if stripped not in section_starts:
                    section_starts[stripped] = i + 1

        section_parsers = {
            "--Move Updates--": self._parse_move_updates,
            "--Pokemon Base Stats & Types--": self._parse_pokemon,
            "--Removing Impossible Evolutions--": self._parse_evolutions,
            "--Removing Timed-Based Evolutions--": self._parse_evolutions,
            "--Randomized Evolutions--": self._parse_evolutions,
            "--Random Starters--": self._parse_starters,
            "--Custom Starters--": self._parse_starters,
            "--TM Moves--": self._parse_tm_moves,
            "--TM Compatibility--": self._parse_tm_compatibility,
            "--Trainers Pokemon--": self._parse_trainers,
            "--Wild Pokemon--": self._parse_wild_pokemon,
            "--Static Pokemon--": self._parse_static_pokemon,
            "--In-Game Trades--": self._parse_in_game_trades,
        }

        for section_name in self.SECTION_PARSE_ORDER:
            if section_name in section_starts:
                start = section_starts[section_name]
                parser = section_parsers.get(section_name)
                if parser:
                    try:
                        parser(lines, start)
                    except Exception as err:
                        self.logger.error(f"Fehler beim Parsen von '{section_name}': {err}")
                        self.logger.error(traceback.format_exc())

        self.logger.info(
            f"Log geparst: {len(self._data.pokemon)} Pokemon, "
            f"{len(self._data.trainers)} Trainer, "
            f"{len(self._data.wild_areas)} Wild-Gebiete, "
            f"{len(self._data.tm_moves)} TMs, "
            f"{len(self._data.hm_moves)} VMs, "
            f"{len(self._data.move_updates)} Move-Updates, "
            f"{len(self._data.in_game_trades)} In-Game-Trades"
        )
        return self._data

    def get_data(self) -> RandomizerLogData | None:
        return self._data

    def _parse_header(self, lines: list[str]):
        for line in lines[:5]:
            if line.startswith("Randomizer Version:"):
                self._data.version = line.split(":", 1)[1].strip()
            elif line.startswith("Random Seed:"):
                self._data.seed = line.split(":", 1)[1].strip()
            elif line.startswith("Settings String:"):
                self._data.settings_string = line.split(":", 1)[1].strip()

    def _parse_move_updates(self, lines: list[str], start: int):
        i = start
        while i < len(lines) and lines[i].strip():
            line = lines[i].strip()
            match = re.match(r'Made (.+?) (?:have |be )(.*)', line)
            if match:
                name = match.group(1).strip()
                changes_str = match.group(2).strip()
                changes = {}
                if changes_str.endswith('-type'):
                    changes['type'] = changes_str[:-5].strip()
                else:
                    for part in re.split(r',\s*| and ', changes_str):
                        part = part.strip()
                        if not part:
                            continue
                        pp_match = re.match(r'(\d+)\s+PP', part)
                        power_match = re.match(r'(\d+)\s+power', part)
                        acc_match = re.match(r'(\d+)%\s+accuracy', part)
                        if pp_match:
                            changes['pp'] = pp_match.group(1)
                        elif power_match:
                            changes['power'] = power_match.group(1)
                        elif acc_match:
                            changes['accuracy'] = acc_match.group(1)
                self._data.move_updates.append(MoveUpdate(name=name, changes=changes))
            i += 1

    def _parse_pokemon(self, lines: list[str], start: int):
        header = lines[start] if start < len(lines) else ''
        header_parts = [h.strip().upper() for h in header.split('|')]

        if 'SPEC' in header_parts:
            fmt = 'gen1'
            min_cols = 8
        elif 'ABILITY1' in header_parts:
            fmt = 'gen3plus'
            min_cols = 12
        else:
            fmt = 'gen2'
            min_cols = 9

        i = start + 1
        while i < len(lines) and lines[i].strip():
            parts = lines[i].split('|')
            if len(parts) < min_cols:
                i += 1
                continue
            try:
                dexnr = int(parts[0].strip())
                name = parts[1].strip()
                types = parts[2].strip().split('/')

                if fmt == 'gen1':
                    spec = int(parts[7].strip())
                    stats = {
                        'hp': int(parts[3].strip()),
                        'atk': int(parts[4].strip()),
                        'def': int(parts[5].strip()),
                        'satk': spec,
                        'sdef': spec,
                        'spd': int(parts[6].strip()),
                    }
                    abilities = []
                    item = ''
                elif fmt == 'gen2':
                    stats = {
                        'hp': int(parts[3].strip()),
                        'atk': int(parts[4].strip()),
                        'def': int(parts[5].strip()),
                        'satk': int(parts[6].strip()),
                        'sdef': int(parts[7].strip()),
                        'spd': int(parts[8].strip()),
                    }
                    abilities = []
                    item = parts[9].strip() if len(parts) > 9 else ''
                else:
                    stats = {
                        'hp': int(parts[3].strip()),
                        'atk': int(parts[4].strip()),
                        'def': int(parts[5].strip()),
                        'satk': int(parts[6].strip()),
                        'sdef': int(parts[7].strip()),
                        'spd': int(parts[8].strip()),
                    }
                    if len(parts) >= 13:
                        abilities = [parts[j].strip() for j in range(9, 12) if parts[j].strip()]
                        item = parts[12].strip() if len(parts) > 12 else ''
                    else:
                        abilities = [parts[j].strip() for j in range(9, 11) if parts[j].strip()]
                        item = parts[11].strip() if len(parts) > 11 else ''

                self._data.pokemon[dexnr] = RandoPokemon(
                    dexnr=dexnr, name=name, types=types,
                    stats=stats, abilities=abilities, item=item,
                )
                self._data.pokemon_name_to_id[name.lower()] = dexnr
            except (ValueError, IndexError) as err:
                self.logger.warning(f"Pokemon-Zeile uebersprungen (Zeile {i + 1}): {err}")
            i += 1

    def _parse_evolutions(self, lines: list[str], start: int):
        i = start
        while i < len(lines) and lines[i].strip():
            match = re.match(r'(.+?)\s+->\s+(.+?)(?:\s+(?:by |at |using ).*)?$', lines[i])
            if match:
                pokemon_name = match.group(1).strip()
                evo_str = match.group(2).strip()
                pokemon_id = self._data.pokemon_name_to_id.get(pokemon_name.lower())
                if pokemon_id is not None:
                    if pokemon_id not in self._data.evolutions:
                        self._data.evolutions[pokemon_id] = []
                    for evo_part in re.split(r',\s*| and ', evo_str):
                        evo_name = evo_part.strip()
                        if evo_name and evo_name not in self._data.evolutions[pokemon_id]:
                            self._data.evolutions[pokemon_id].append(evo_name)
            i += 1

    def _parse_starters(self, lines: list[str], start: int):
        i = start
        while i < len(lines) and lines[i].strip():
            match = re.match(r'Set starter \d+ to (.+)', lines[i])
            if match:
                self._data.starters.append(match.group(1).strip())
            i += 1

    def _parse_tm_moves(self, lines: list[str], start: int):
        i = start
        while i < len(lines) and lines[i].strip():
            match = re.match(r'(TM|HM)(\d+)\s+(.+)', lines[i])
            if match:
                prefix = match.group(1)
                number = int(match.group(2))
                move_name = match.group(3).strip()
                if prefix == "TM":
                    self._data.tm_moves[number] = move_name
                else:
                    self._data.hm_moves[number] = move_name
            i += 1

    def _parse_tm_compatibility(self, lines: list[str], start: int):
        i = start
        while i < len(lines) and lines[i].strip():
            parts = lines[i].split('|')
            if len(parts) < 2:
                i += 1
                continue

            first_match = re.match(r'(\d+)\s+(.+)', parts[0].strip())
            if first_match:
                pokemon_id = int(first_match.group(1))
                if pokemon_id in self._data.pokemon:
                    compat = []
                    for tm_field in parts[1:]:
                        stripped = tm_field.strip()
                        compat.append(stripped != '-' and stripped != '')
                    self._data.pokemon[pokemon_id].tm_compat = compat
            i += 1

    def _parse_trainers(self, lines: list[str], start: int):
        i = start
        while i < len(lines):
            line = lines[i].strip()
            if line.startswith('--') and line.endswith('--'):
                break

            header_match = re.match(r'#(\d+)\s+\((.+?)\)', line)
            if header_match:
                trainer_id = int(header_match.group(1))
                class_name = header_match.group(2)
                pokemon_list = []

                inline_match = re.search(r'\)\s*(?:@[0-9A-Fa-f]+)?\s*-\s*(.+)', line)
                if inline_match:
                    pokemon_list = self._parse_trainer_inline(inline_match.group(1))
                    i += 1
                else:
                    i += 1
                    while i < len(lines) and lines[i].strip():
                        poke_line = lines[i].strip()
                        if poke_line.startswith('#'):
                            break
                        poke_match = re.match(
                            r'(.+?)\s+Lv(\d+),\s+Ability:\s+(.+?)\s+-\s+(.+)', poke_line
                        )
                        if poke_match:
                            name_field = poke_match.group(1).strip()
                            held_item = ''
                            if '@' in name_field:
                                name_field, held_item = name_field.rsplit('@', 1)
                                name_field = name_field.strip()
                                held_item = held_item.strip()
                            moves = [m.strip() for m in poke_match.group(4).split(',')]
                            pokemon_list.append(TrainerPokemon(
                                name=name_field,
                                level=int(poke_match.group(2)),
                                ability=poke_match.group(3).strip(),
                                moves=moves,
                                held_item=held_item,
                            ))
                        i += 1

                self._data.trainers[trainer_id] = Trainer(
                    id=trainer_id,
                    class_name=class_name,
                    pokemon=pokemon_list,
                )
            else:
                i += 1

    def _parse_trainer_inline(self, pokemon_str: str) -> list[TrainerPokemon]:
        result = []
        for entry in pokemon_str.split(', '):
            entry = entry.strip()
            match = re.match(r'(.+?)\s+Lv(\d+)', entry)
            if match:
                name_field = match.group(1).strip()
                held_item = ''
                if '@' in name_field:
                    name_field, held_item = name_field.rsplit('@', 1)
                    name_field = name_field.strip()
                    held_item = held_item.strip()
                result.append(TrainerPokemon(
                    name=name_field,
                    level=int(match.group(2)),
                    ability='',
                    moves=[],
                    held_item=held_item,
                ))
        return result

    def _parse_wild_pokemon(self, lines: list[str], start: int):
        i = start
        while i < len(lines):
            line = lines[i].strip()
            if line.startswith('--') and line.endswith('--') and line.strip('-').strip():
                break

            set_match = re.match(r'Set #(\d+)\s+-\s+(.+)\s+\(rate=(\d+)\)', line)
            if set_match:
                set_number = int(set_match.group(1))
                location_method = set_match.group(2).strip()
                rate = int(set_match.group(3))
                location, method = _split_location_method(location_method)

                encounters = []
                i += 1
                while i < len(lines) and lines[i].strip():
                    enc_line = lines[i].strip()
                    enc_match = re.match(
                        r'(.+?)\s+Lvs?\s*(\d+)(?:-(\d+))?\s+'
                        r'HP\s+(\d+)\s+ATK\s+(\d+)\s+DEF\s+(\d+)\s+'
                        r'(?:SPATK\s+(\d+)\s+SPDEF\s+(\d+)\s+SPEED\s+(\d+)'
                        r'|SPECIAL\s+(\d+)\s+SPEED\s+(\d+))',
                        enc_line
                    )
                    if enc_match:
                        name = enc_match.group(1).strip()
                        level = int(enc_match.group(2))
                        max_level = int(enc_match.group(3)) if enc_match.group(3) else 0
                        hp = int(enc_match.group(4))
                        atk = int(enc_match.group(5))
                        defn = int(enc_match.group(6))

                        if enc_match.group(7) is not None:
                            stats = {
                                'hp': hp, 'atk': atk, 'def': defn,
                                'spatk': int(enc_match.group(7)),
                                'spdef': int(enc_match.group(8)),
                                'speed': int(enc_match.group(9)),
                            }
                        else:
                            special = int(enc_match.group(10))
                            stats = {
                                'hp': hp, 'atk': atk, 'def': defn,
                                'spatk': special, 'spdef': special,
                                'speed': int(enc_match.group(11)),
                            }

                        encounters.append(WildEncounter(
                            name=name, level=level, stats=stats,
                            max_level=max_level,
                        ))
                    i += 1

                self._data.wild_areas.append(WildArea(
                    set_number=set_number,
                    location=location,
                    method=method,
                    rate=rate,
                    encounters=encounters,
                ))
            else:
                i += 1

    def _parse_static_pokemon(self, lines: list[str], start: int):
        i = start
        while i < len(lines) and lines[i].strip():
            line = lines[i]
            if '=>' in line:
                parts = line.split('=>', 1)
                self._data.static_pokemon.append(
                    (parts[0].strip(), parts[1].strip())
                )
            i += 1

    def _parse_in_game_trades(self, lines: list[str], start: int):
        i = start
        while i < len(lines) and lines[i].strip():
            line = lines[i].strip()
            if line.startswith('--') and line.endswith('--'):
                break
            match = re.match(
                r'Trade\s+(\S+)\s+->\s+(\S+)\s+the\s+(\S+)\s+->\s+'
                r'(\S+)\s+->\s+(\S+)\s+the\s+(.+)',
                line
            )
            if match:
                self._data.in_game_trades.append(InGameTrade(
                    old_requested=match.group(1).strip(),
                    old_nickname=match.group(2).strip(),
                    old_given=match.group(3).strip(),
                    new_requested=match.group(4).strip(),
                    new_nickname=match.group(5).strip(),
                    new_given=match.group(6).strip(),
                ))
            i += 1


def _split_location_method(location_method: str) -> tuple[str, str]:
    for method in KNOWN_ENCOUNTER_METHODS:
        if location_method.endswith(method):
            location = location_method[:-len(method)].strip()
            return location, method
    for method in KNOWN_ENCOUNTER_METHODS:
        prefix = method + ' on '
        if location_method.startswith(prefix):
            location = location_method[len(prefix):].strip()
            return location, method
        prefix_lower = method.lower() + ' on '
        if location_method.lower().startswith(prefix_lower):
            location = location_method[len(prefix_lower):].strip()
            return location, method
    fishing_match = re.match(r'(.+?)\s+(Fishing|Surfing)\b', location_method)
    if fishing_match:
        return fishing_match.group(1).strip(), fishing_match.group(2)
    parts = location_method.rsplit(' ', 1)
    if len(parts) == 2:
        return parts[0], parts[1]
    return location_method, ''
