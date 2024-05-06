import simpleobsws
import logging
import sys
import traceback
from websockets.exceptions import WebSocketException

class OBS():
    def __init__(self, host, port, password, munchlax, conf, obs):
        self.ws = None
        self.is_connected = False
        self.host = host
        self.port = port
        self.password = password
        self.munchlax = munchlax
        self.conf = conf
        self.obs = obs

        self.logger = self.init_logging()

    def init_logging(self):
        logger = logging.getLogger(__name__)
        logger.setLevel(logging.INFO)

        logging_formatter = logging.Formatter('[%(asctime)s] %(levelname)s: %(message)s')

        file_handler = logging.FileHandler('./logs/obs.log', 'w')
        file_handler.setFormatter(logging_formatter)
        logger.addHandler(file_handler)

        stream_handler = logging.StreamHandler(sys.stdout)
        stream_handler.setFormatter(logging_formatter)
        logger.addHandler(stream_handler)

        return logger

    async def load_obsws(self):
        if not self.ws or not self.ws.is_identified():
            self.ws = simpleobsws.WebSocketClient(url=f'ws://{self.host}:{self.port}', password=self.password, identification_parameters=simpleobsws.IdentificationParameters(ignoreNonFatalRequestChecks=False))
            try:
                await self.ws.connect()  # type:ignore
                await self.ws.wait_until_identified()  # type:ignore
                await self.redraw_obs()
                self.logger.info("obs connected.")
                self.is_connected = 'connected'
                if not self.munchlax.obs:
                    self.munchlax.obs = self
            except WebSocketException as wserr:
                self.logger.error(f"wserr: {type(wserr)}, {wserr}")
                self.logger.error(f"{traceback.format_exc()}")
                self.is_connected = False
            except Exception as err:
                self.logger.error(f"err: {type(err)},{err}")
                self.logger.error(f"{traceback.format_exc()}")
                self.is_connected = False

    async def disconnect(self):
        if self.ws:
            await self.ws.disconnect()
            self.is_connected = False
            self.password = self.obs['password']
            self.host = self.obs['host']
            self.port = self.obs['port']

    async def redraw_obs(self):
        if self.ws and self.ws.is_identified():
            for player in self.munchlax.sorted_teams:
                await self.changeSource(player, range(6), self.munchlax.sorted_teams[player], self.munchlax.editions[player])
                await self.change_badges(player)

    async def changeSource(self, player, slots, team, edition):
        if not self.ws or not self.ws.is_identified():
            self.is_connected = False
            return
        if not slots:
            return
        self.logger.info(f"changeSource ausgeführt: Spieler {player} mit Slots {slots}")
        batch = []
        for slot in slots:
            sprite = self.get_sprite(team[slot], self.conf['animated'], edition, two_pc=self.conf['obs_2_pc'])

            batch.append(
                simpleobsws.Request(
                    "SetInputSettings",
                    {
                        "inputName": f"Slot{slot + 6 * (player -1) +1}",
                        "inputSettings": {"file": sprite},
                    },
                )
            )
        if self.conf['show_nicknames']:
            for slot in slots:
                batch.append(
                    simpleobsws.Request(
                        "SetInputSettings",
                        {
                            "inputName": f"name{slot + 6 * (player -1) +1}",
                            "inputSettings": {"text": team[slot].nickname},
                        },
                    )
                )
        if self.conf['show_items'] and edition > 20:
            items_path = self.conf['items_path'] if not self.conf['obs_2_pc'] else self.conf['items_obs_path']
            for slot in slots:
                batch.append(
                    simpleobsws.Request(
                        "SetInputSettings",
                        {
                            "inputName": f"item{slot + 6 * (player - 1) + 1}",
                            "inputSettings": {
                                "file": items_path + '/'
                                + str(team[slot].item)
                                + ".png"
                            },
                        },
                    )
                )

        if batch != []:
            await self.ws.call_batch(batch)

    async def change_badges(self, player):
        if self.munchlax.editions[player] < 70:
            badge_lut = {
                11:'kanto',
                12:'kanto',
                13:'kanto',
                21:'johto',
                22:'johto',
                23:'johto',
                31:'hoenn',
                32:'hoenn',
                33:'hoenn',
                34:'kanto',
                35:'kanto',
                41:'sinnoh',
                42:'sinnoh',
                43:'sinnoh',
                44:'johto',
                45:'johto',
                51:'unova',
                52:'unova',
                53:'unova2',
                54:'unova2',
                61:'kalos',
                62:'kalos',
                63:'hoenn',
                64:'hoenn',
                71:'alola',
                72:'alola',
                73:'alola',
                74:'alola',
            }
            if not self.conf['show_badges']:
                return
            if not self.ws or not self.ws.is_identified():
                self.is_connected = False
                return
            badge_path = self.conf['badges_path'] if not self.conf['obs_2_pc'] else self.conf['badges_obs_path']
            self.logger.info(f"change_badges ausgeführt: Spieler {player}")
            region = badge_lut[self.munchlax.editions[player]]
            badge_number = 16 if region == 'johto' else 8
            batch = []
            for i in range(badge_number):
                if (self.munchlax.badges[player] & 2**i):
                    batch.append(
                        simpleobsws.Request(
                            "SetInputSettings",
                            {
                                "inputName": f"badge{i + 16 * (player - 1) + 1}",
                                "inputSettings": {
                                    "file": badge_path + '/' + region + str(i + 1) + ".png"
                                }
                            }
                        )
                    )
                else:
                    batch.append(
                        simpleobsws.Request(
                            "SetInputSettings",
                            {
                                "inputName": f"badge{i + 16 * (player - 1) + 1}",
                                "inputSettings": {
                                    "file": badge_path + '/' + badge_lut[self.munchlax.editions[player]] + str(i + 1) + 'empty' + ".png"
                                }
                            }
                        )
                    )

            await self.ws.call_batch(batch)

    def get_sprite(self, pokemon, anim, edition, two_pc=False):
        if two_pc:
            common_path = self.conf['common_obs_path']
            obs = '_obs'
        else:
            common_path = self.conf['common_path']
            obs = ''
        shiny = "shiny/" if pokemon.shiny else ""
        subpath = {
            11: 'red',
            12: 'red',
            13: 'yellow',
            21: 'silver',
            22: 'gold',
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
            54: 'black',
            61: 'x',
            62: 'x',
            63: 'alphasapphire',
            64: 'alphasapphire',
            71: 'sun',
            72: 'sun',
            73: 'usun',
            74: 'usun'
        }
        if pokemon.female and edition > 40 and pokemon.dexnr in [3, 12, 19, 20, 25, 26, 41, 42, 44, 45, 64, 65, 84, 85, 97, 111, 112, 118, 119, 123, 129, 130, 154, 165, 166, 178, 185, 186, 190, 194, 195, 198, 202, 203, 207, 208, 212, 214, 215, 215, 217, 221, 224, 229, 232, 255, 256, 257, 267, 269, 272, 274, 275, 307, 308, 315, 316, 317, 322, 323, 332, 350, 369, 396, 397, 398, 399, 400, 401, 402, 403, 404, 405, 407, 415, 417, 418, 419, 424, 443, 444, 445, 449, 450, 453, 454, 456, 457, 459, 460, 461, 464, 465, 473, 521, 592, 593, 668, 678, 876, 902]:
            female = "female/"
        else:
            female = ""
        if anim and edition in (23, 33, 41, 42, 43, 44, 45, 51, 52, 53, 54):
            filetype = ".gif"
            animated = "animated/"
        else:
            animated = ""
            filetype = ".png"
        if not self.conf['single_path_check']:
            conf_path = self.conf[f"{subpath[edition]}{obs}"]
            common_slash = "" if (common_path.endswith("/") and not conf_path.startswith("/")) or (not common_path.endswith("/") and conf_path.startswith("/")) else "/"
            slash = "" if conf_path.endswith("/") else "/"
            sub = common_slash + self.conf[f"{subpath[edition]}{obs}"] + slash
        else:
            sub = "" if common_path.endswith("/") else "/"
        path = (
            common_path
            + sub 
            + animated
            + shiny
            + female
        )
        file = str(pokemon.dexnr) + pokemon.form + filetype
        return path + file