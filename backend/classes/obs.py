import simpleobsws
import traceback
from websockets.exceptions import WebSocketException

from backend.tm_type_resolver import resolve_tm_hm_sprite
from backend.logging_setup import get_logger

HP_COLOR_GREEN = 0xFF00AF4C
HP_COLOR_YELLOW = 0xFF0098FF
HP_COLOR_RED = 0xFF3644F4
HP_BAR_DEFAULT_WIDTH = 150
HP_BAR_HEIGHT = 6

STATUS_GLOW_COLORS = {
    "freeze": 0xFFB06830,
    "burn": 0xFF3050E8,
    "para": 0xFF00D3FA,
    "poison": 0xFFA040A0,
    "toxic": 0xFFA040A0,
    "sleep": 0xFFD8D898,
}

GLOW_FILTER_KIND = "obs_glow_filter"


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

        self._scene_name: str | None = None
        self._name_group: str | None = None
        self._group_offset: tuple[float, float] = (0.0, 0.0)
        self._group_scale: tuple[float, float] = (1.0, 1.0)
        self._hp_bar_widths: dict[str, int] = {}
        self._filters_initialized: set[str] = set()
        self._hp_bars_initialized: set[str] = set()
        self._glow_available: bool = True

        self.logger = get_logger(__name__, './logs/obs.log')

    async def load_obsws(self):
        if not self.ws or not self.ws.is_identified():
            self.logger.debug(f"Verbinde zu OBS: ws://{self.host}:{self.port}")
            self.ws = simpleobsws.WebSocketClient(url=f'ws://{self.host}:{self.port}', password=self.password, identification_parameters=simpleobsws.IdentificationParameters(ignoreNonFatalRequestChecks=False))
            try:
                await self.ws.connect()  # type:ignore
                await self.ws.wait_until_identified()  # type:ignore
                self._scene_name = None
                self._name_group = None
                self._group_offset = (0.0, 0.0)
                self._group_scale = (1.0, 1.0)
                self._hp_bar_widths.clear()
                self._filters_initialized.clear()
                self._hp_bars_initialized.clear()
                self._glow_available = True
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
            if self.conf.get('show_hp_bars'):
                await self._find_scene()
                for player in self.munchlax.sorted_teams:
                    await self._ensure_hp_bars(player, range(6))
            if self.conf.get('show_status_effects'):
                for player in self.munchlax.sorted_teams:
                    await self._ensure_filters(player, range(6))
            for player in self.munchlax.sorted_teams:
                await self.changeSource(player, range(6), self.munchlax.sorted_teams[player], self.munchlax.editions[player])
                await self.change_badges(player)

    # ── Scene-Auto-Detection ─────────────────────────────────────────

    async def _find_scene(self):
        if self._scene_name:
            return
        if not self.ws or not self.ws.is_identified():
            return
        try:
            scenes_to_check = []
            resp = await self.ws.call(simpleobsws.Request("GetCurrentProgramScene"))
            if resp and resp.ok() and resp.responseData:
                scenes_to_check.append(resp.responseData.get("sceneName", ""))
            resp = await self.ws.call(simpleobsws.Request("GetSceneList"))
            if resp and resp.ok() and resp.responseData:
                for s in resp.responseData.get("scenes", []):
                    name = s.get("sceneName", "")
                    if name and name not in scenes_to_check:
                        scenes_to_check.append(name)

            for scene in scenes_to_check:
                if not scene:
                    continue
                # name1 direkt in Szene?
                if await self._source_exists_in(scene, "name1"):
                    self._scene_name = scene
                    self._name_group = None
                    self.logger.info(f"Szene gefunden (direkt): {scene}")
                    return
                # name1 in einer Gruppe innerhalb der Szene?
                group_name = await self._find_group_containing(scene, "name1")
                if group_name:
                    self._scene_name = scene
                    self._name_group = group_name
                    await self._cache_group_transform(scene, group_name)
                    self.logger.info(f"Szene gefunden: {scene}, Namen-Gruppe: {group_name}")
                    return

            self.logger.warning("Keine Szene mit 'name1' gefunden — HP-Bars können nicht erstellt werden.")
        except Exception as err:
            self.logger.error(f"_find_scene Fehler: {err}")
            self.logger.error(traceback.format_exc())

    async def _source_exists_in(self, scene_or_group: str, source_name: str) -> bool:
        try:
            resp = await self.ws.call(simpleobsws.Request(
                "GetSceneItemId",
                {"sceneName": scene_or_group, "sourceName": source_name}
            ))
            return resp is not None and resp.ok()
        except Exception:
            return False

    async def _find_group_containing(self, scene: str, source_name: str) -> str | None:
        try:
            resp = await self.ws.call(simpleobsws.Request(
                "GetSceneItemList", {"sceneName": scene}
            ))
            if not resp or not resp.ok() or not resp.responseData:
                return None
            for item in resp.responseData.get("sceneItems", []):
                if item.get("isGroup"):
                    group = item["sourceName"]
                    if await self._source_exists_in(group, source_name):
                        return group
        except Exception:
            pass
        return None

    async def _cache_group_transform(self, scene: str, group_name: str):
        try:
            resp = await self.ws.call(simpleobsws.Request(
                "GetSceneItemId", {"sceneName": scene, "sourceName": group_name}
            ))
            if not resp or not resp.ok() or not resp.responseData:
                return
            item_id = resp.responseData["sceneItemId"]
            tr_resp = await self.ws.call(simpleobsws.Request(
                "GetSceneItemTransform", {"sceneName": scene, "sceneItemId": item_id}
            ))
            if tr_resp and tr_resp.ok() and tr_resp.responseData:
                t = tr_resp.responseData["sceneItemTransform"]
                self._group_offset = (t.get("positionX", 0.0), t.get("positionY", 0.0))
                self._group_scale = (t.get("scaleX", 1.0), t.get("scaleY", 1.0))
        except Exception as err:
            self.logger.error(f"_cache_group_transform Fehler: {err}")
            self.logger.error(traceback.format_exc())

    # ── HP-Bar Management ────────────────────────────────────────────

    def _slot_name(self, player: int, slot: int) -> str:
        return f"Slot{slot + 6 * (player - 1) + 1}"

    def _hp_bar_name(self, player: int, slot: int) -> str:
        return f"hp_bar{slot + 6 * (player - 1) + 1}"

    def _name_source_name(self, player: int, slot: int) -> str:
        return f"name{slot + 6 * (player - 1) + 1}"

    async def _ensure_hp_bars(self, player: int, slots):
        if not self._scene_name or not self.ws or not self.ws.is_identified():
            return
        name_container = self._name_group or self._scene_name
        for slot in slots:
            hp_name = self._hp_bar_name(player, slot)
            if hp_name in self._hp_bars_initialized:
                continue
            try:
                if await self._source_exists_in(self._scene_name, hp_name):
                    self._hp_bars_initialized.add(hp_name)
                    if hp_name not in self._hp_bar_widths:
                        await self._read_hp_bar_width(hp_name)
                    continue

                name_src = self._name_source_name(player, slot)
                rel_x, rel_y, vis_w, vis_h = await self._get_name_visual_rect(
                    name_container, name_src
                )
                if vis_w <= 0:
                    vis_w = HP_BAR_DEFAULT_WIDTH
                self._hp_bar_widths[hp_name] = int(vis_w)

                if self._name_group:
                    abs_x = self._group_offset[0] + rel_x * self._group_scale[0]
                    abs_y = self._group_offset[1] + (rel_y + vis_h) * self._group_scale[1]
                else:
                    abs_x = rel_x
                    abs_y = rel_y + vis_h

                resp = await self.ws.call(simpleobsws.Request(
                    "CreateInput",
                    {
                        "sceneName": self._scene_name,
                        "inputName": hp_name,
                        "inputKind": "color_source_v3",
                        "inputSettings": {
                            "color": HP_COLOR_GREEN,
                            "width": int(vis_w),
                            "height": HP_BAR_HEIGHT,
                        },
                        "sceneItemEnabled": True,
                    }
                ))
                if not resp or not resp.ok():
                    self.logger.warning(f"HP-Bar '{hp_name}' konnte nicht erstellt werden: {resp}")
                    continue

                hp_item_id = resp.responseData["sceneItemId"]
                await self.ws.call(simpleobsws.Request(
                    "SetSceneItemTransform",
                    {
                        "sceneName": self._scene_name,
                        "sceneItemId": hp_item_id,
                        "sceneItemTransform": {
                            "positionX": abs_x,
                            "positionY": abs_y,
                        }
                    }
                ))

                self._hp_bars_initialized.add(hp_name)
                self.logger.info(f"HP-Bar '{hp_name}' erstellt bei ({abs_x:.0f},{abs_y:.0f}), {int(vis_w)}x{HP_BAR_HEIGHT}")
            except Exception as err:
                self.logger.error(f"_ensure_hp_bars Fehler bei {hp_name}: {err}")
                self.logger.error(traceback.format_exc())

    async def _read_hp_bar_width(self, hp_name: str):
        try:
            resp = await self.ws.call(simpleobsws.Request(
                "GetInputSettings", {"inputName": hp_name}
            ))
            if resp and resp.ok() and resp.responseData:
                settings = resp.responseData.get("inputSettings", {})
                w = settings.get("width", HP_BAR_DEFAULT_WIDTH)
                self._hp_bar_widths[hp_name] = int(w)
        except Exception:
            self._hp_bar_widths[hp_name] = HP_BAR_DEFAULT_WIDTH

    async def _get_name_visual_rect(self, container: str, source: str) -> tuple[float, float, float, float]:
        """Gibt (posX, posY, visuelle_breite, visuelle_höhe) zurück.

        Berücksichtigt boundsWidth/boundsHeight falls Bounds gesetzt sind.
        """
        try:
            id_resp = await self.ws.call(simpleobsws.Request(
                "GetSceneItemId", {"sceneName": container, "sourceName": source}
            ))
            if not id_resp or not id_resp.ok() or not id_resp.responseData:
                return 0.0, 0.0, 0.0, 0.0
            item_id = id_resp.responseData["sceneItemId"]
            tr_resp = await self.ws.call(simpleobsws.Request(
                "GetSceneItemTransform",
                {"sceneName": container, "sceneItemId": item_id}
            ))
            if tr_resp and tr_resp.ok() and tr_resp.responseData:
                t = tr_resp.responseData.get("sceneItemTransform", {})
                has_bounds = t.get("boundsType", "OBS_BOUNDS_NONE") != "OBS_BOUNDS_NONE"
                vis_w = t.get("boundsWidth", 0.0) if has_bounds else t.get("width", 0.0)
                vis_h = t.get("boundsHeight", 0.0) if has_bounds else t.get("height", 0.0)
                return (
                    t.get("positionX", 0.0),
                    t.get("positionY", 0.0),
                    vis_w,
                    vis_h,
                )
        except Exception:
            pass
        return 0.0, 0.0, 0.0, 0.0

    @staticmethod
    def _hp_color(pct: float) -> int:
        if pct > 0.5:
            return HP_COLOR_GREEN
        elif pct > 0.25:
            return HP_COLOR_YELLOW
        return HP_COLOR_RED

    def _build_hp_bar_updates(self, player: int, slots, team) -> list:
        batch = []
        for slot in slots:
            pokemon = team[slot]
            hp_name = self._hp_bar_name(player, slot)
            if pokemon.dexnr == 0 or pokemon.dexnr == 'egg':
                batch.append(simpleobsws.Request(
                    "SetInputSettings",
                    {"inputName": hp_name, "inputSettings": {"color": 0x00000000, "width": 0}}
                ))
                continue
            max_hp = max(pokemon.max_hp, 1)
            hp_pct = pokemon.cur_hp / max_hp
            max_width = self._hp_bar_widths.get(hp_name, HP_BAR_DEFAULT_WIDTH)
            batch.append(simpleobsws.Request(
                "SetInputSettings",
                {
                    "inputName": hp_name,
                    "inputSettings": {
                        "color": self._hp_color(hp_pct),
                        "width": max(int(max_width * hp_pct), 1) if hp_pct > 0 else 0,
                    }
                }
            ))
        return batch

    # ── Filter-Management ────────────────────────────────────────────

    async def _ensure_filters(self, player: int, slots):
        if not self.ws or not self.ws.is_identified():
            return
        for slot in slots:
            source = self._slot_name(player, slot)
            if source in self._filters_initialized:
                continue
            try:
                resp = await self.ws.call(simpleobsws.Request(
                    "GetSourceFilterList", {"sourceName": source}
                ))
                existing = set()
                if resp and resp.ok() and resp.responseData:
                    for f in resp.responseData.get("filters", []):
                        existing.add(f.get("filterName"))

                if "besiegt" not in existing:
                    await self.ws.call(simpleobsws.Request(
                        "CreateSourceFilter",
                        {
                            "sourceName": source,
                            "filterName": "besiegt",
                            "filterKind": "color_filter",
                            "filterSettings": {"saturation": -1.0},
                        }
                    ))
                    await self.ws.call(simpleobsws.Request(
                        "SetSourceFilterEnabled",
                        {"sourceName": source, "filterName": "besiegt", "filterEnabled": False}
                    ))

                if "status_glow" not in existing and self._glow_available:
                    glow_resp = await self.ws.call(simpleobsws.Request(
                        "CreateSourceFilter",
                        {
                            "sourceName": source,
                            "filterName": "status_glow",
                            "filterKind": GLOW_FILTER_KIND,
                            "filterSettings": {
                                "glow_size": 5.0,
                                "glow_intensity": 100.0,
                                "glow_fill_type": 1,
                                "glow_position": 1,
                                "glow_padding": 2,
                                "padding_amount": 5,
                            },
                        }
                    ))
                    if not glow_resp or not glow_resp.ok():
                        self._glow_available = False
                        self.logger.warning(
                            f"stroke-glow-shadow Plugin nicht verfügbar — "
                            f"Glow-Filter deaktiviert. (filterKind='{GLOW_FILTER_KIND}')"
                        )
                    else:
                        await self.ws.call(simpleobsws.Request(
                            "SetSourceFilterEnabled",
                            {"sourceName": source, "filterName": "status_glow", "filterEnabled": False}
                        ))

                self._filters_initialized.add(source)
            except Exception as err:
                self.logger.error(f"_ensure_filters Fehler bei {source}: {err}")
                self.logger.error(traceback.format_exc())

    @staticmethod
    def _status_glow_color(status: dict) -> int | None:
        if not status:
            return None
        if status.get("freeze"):
            return STATUS_GLOW_COLORS["freeze"]
        if status.get("burn"):
            return STATUS_GLOW_COLORS["burn"]
        if status.get("para"):
            return STATUS_GLOW_COLORS["para"]
        if status.get("toxic"):
            return STATUS_GLOW_COLORS["toxic"]
        if status.get("poison"):
            return STATUS_GLOW_COLORS["poison"]
        if status.get("sleep"):
            return STATUS_GLOW_COLORS["sleep"]
        return None

    def _build_filter_updates(self, player: int, slots, team) -> list:
        batch = []
        for slot in slots:
            pokemon = team[slot]
            source = self._slot_name(player, slot)

            fainted = pokemon.cur_hp == 0 and pokemon.dexnr not in (0, 'egg')
            batch.append(simpleobsws.Request(
                "SetSourceFilterEnabled",
                {"sourceName": source, "filterName": "besiegt", "filterEnabled": fainted}
            ))

            if self._glow_available:
                status = getattr(pokemon, 'status', {})
                glow_color = self._status_glow_color(status)
                if glow_color is not None:
                    batch.append(simpleobsws.Request(
                        "SetSourceFilterSettings",
                        {
                            "sourceName": source,
                            "filterName": "status_glow",
                            "filterSettings": {"glow_fill_color": glow_color},
                        }
                    ))
                    batch.append(simpleobsws.Request(
                        "SetSourceFilterEnabled",
                        {"sourceName": source, "filterName": "status_glow", "filterEnabled": True}
                    ))
                else:
                    batch.append(simpleobsws.Request(
                        "SetSourceFilterEnabled",
                        {"sourceName": source, "filterName": "status_glow", "filterEnabled": False}
                    ))
        return batch

    # ── Bestehende Methoden (erweitert) ──────────────────────────────

    async def changeSource(self, player, slots, team, edition):
        if not self.ws or not self.ws.is_identified():
            self.is_connected = False
            return
        if not slots:
            return
        self.logger.info(f"changeSource: Spieler {player}, Slots {list(slots)}")
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
                item_slug = str(team[slot].item)
                item_slug = resolve_tm_hm_sprite(
                    edition, item_slug,
                    self.munchlax.rando_tm_moves,
                    self.munchlax.rando_hm_moves,
                )
                batch.append(
                    simpleobsws.Request(
                        "SetInputSettings",
                        {
                            "inputName": f"item{slot + 6 * (player - 1) + 1}",
                            "inputSettings": {
                                "file": items_path + '/'
                                + item_slug
                                + ".png"
                            },
                        },
                    )
                )

        if self.conf.get('show_hp_bars'):
            batch.extend(self._build_hp_bar_updates(player, slots, team))

        if self.conf.get('show_status_effects'):
            batch.extend(self._build_filter_updates(player, slots, team))

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
        if anim and edition in (23, 33, 41, 42, 43, 44, 45, 51, 52, 53, 54, 61,62,63,64,71,72,73,74):
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
        full_path = path + file
        self.logger.debug(f"Sprite: dex={pokemon.dexnr}, shiny={pokemon.shiny}, path={full_path}")
        return full_path
