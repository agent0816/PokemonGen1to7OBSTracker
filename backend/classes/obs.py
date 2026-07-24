import asyncio
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
        self._slot_positions: dict[str, dict] = {}
        self._slot_item_info: dict[str, tuple[str, int]] = {}
        self._scene_groups: list[str] = []
        self._swap_filters: set[str] = set()
        self._fade_filters_initialized: set[str] = set()
        self._move_filters_initialized: set[str] = set()

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
                self._slot_positions.clear()
                self._slot_item_info.clear()
                self._scene_groups.clear()
                self._swap_filters.clear()
                self._fade_filters_initialized.clear()
                self._move_filters_initialized.clear()
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

    async def _reset_fade_filters(self):
        if not self.ws or not self.ws.is_identified():
            return
        batch = []
        # Alle Slot-Sources prüfen (auch stale Filter aus vorherigen Sessions)
        # Spieler aus sorted_teams oder Fallback auf [1..max_players]
        players = list(self.munchlax.sorted_teams.keys()) if self.munchlax.sorted_teams else list(range(1, self.munchlax.pl.get('number_of_players', 1) + 1))
        for player in players:
            for slot in range(6):
                for source in self._all_slot_sources(player, slot):
                    try:
                        resp = await self.ws.call(simpleobsws.Request(
                            "GetSourceFilterList", {"sourceName": source}
                        ))
                        if not resp or not resp.ok() or not resp.responseData:
                            continue
                        existing = {f["filterName"] for f in resp.responseData.get("filters", [])}
                        if self.FADE_FILTER_NAME in existing:
                            batch.append(simpleobsws.Request(
                                "SetSourceFilterSettings",
                                {
                                    "sourceName": source,
                                    "filterName": self.FADE_FILTER_NAME,
                                    "filterSettings": {"opacity": 1.0},
                                }
                            ))
                            batch.append(simpleobsws.Request(
                                "SetSourceFilterEnabled",
                                {"sourceName": source, "filterName": self.FADE_FILTER_NAME, "filterEnabled": True}
                            ))
                            self._fade_filters_initialized.add(source)
                    except Exception:
                        continue
        if batch:
            self.logger.info(f"Fade-Filter Reset: {len(batch) // 2} Sources auf opacity=1.0 + enabled")
            await self.ws.call_batch(batch)

    async def redraw_obs(self):
        if self.ws and self.ws.is_identified():
            await self._find_scene()
            await self._reset_fade_filters()
            if self.conf.get('show_hp_bars'):
                for player in self.munchlax.sorted_teams:
                    await self._ensure_hp_bars(player, range(6))
            if self.conf.get('show_status_effects'):
                for player in self.munchlax.sorted_teams:
                    await self._ensure_filters(player, range(6))
            if self.conf.get('animate_obs_reorder'):
                for player in self.munchlax.sorted_teams:
                    await self._cache_slot_info(player)
                    await self._ensure_swap_filters(player)
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
                # Alle Gruppen in dieser Szene sammeln
                groups = await self._list_groups(scene)

                # name1 direkt in Szene?
                if await self._source_exists_in(scene, "name1"):
                    self._scene_name = scene
                    self._name_group = None
                    self._scene_groups = groups
                    self.logger.info(f"Szene gefunden (direkt): {scene}, Gruppen: {groups}")
                    return
                # name1 in einer Gruppe innerhalb der Szene?
                for group_name in groups:
                    if await self._source_exists_in(group_name, "name1"):
                        self._scene_name = scene
                        self._name_group = group_name
                        self._scene_groups = groups
                        await self._cache_group_transform(scene, group_name)
                        self.logger.info(f"Szene gefunden: {scene}, Namen-Gruppe: {group_name}, Gruppen: {groups}")
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

    async def _list_groups(self, scene: str) -> list[str]:
        groups = []
        try:
            resp = await self.ws.call(simpleobsws.Request(
                "GetSceneItemList", {"sceneName": scene}
            ))
            if resp and resp.ok() and resp.responseData:
                for item in resp.responseData.get("sceneItems", []):
                    if item.get("isGroup"):
                        groups.append(item["sourceName"])
        except Exception:
            pass
        return groups

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

    # ── Reorder-Animation (Move Source Swap Filter) ─────────────────

    SWAP_FILTER_NAME = "tracker_swap"
    SWAP_FILTER_KIND = "move_source_swap_filter"
    FADE_FILTER_NAME = "tracker_fade"
    FADE_FILTER_KIND = "color_filter_v2"
    MOVE_FILTER_NAME = "tracker_move"
    MOVE_FILTER_KIND = "move_source_filter"

    def _all_slot_sources(self, player: int, slot: int) -> list[str]:
        names = [self._slot_name(player, slot)]
        if self.conf.get('show_nicknames'):
            names.append(self._name_source_name(player, slot))
        if self.conf.get('show_items'):
            names.append(f"item{slot + 6 * (player - 1) + 1}")
        if self.conf.get('show_hp_bars'):
            names.append(self._hp_bar_name(player, slot))
        return names

    async def _find_source_item(self, source_name: str) -> tuple[str, int] | None:
        containers = [self._scene_name] + self._scene_groups
        for container in containers:
            if not container:
                continue
            try:
                resp = await self.ws.call(simpleobsws.Request(
                    "GetSceneItemId",
                    {"sceneName": container, "sourceName": source_name}
                ))
                if resp and resp.ok() and resp.responseData:
                    return (container, resp.responseData["sceneItemId"])
            except Exception:
                continue
        return None

    async def _cache_slot_info(self, player: int):
        if not self._scene_name or not self.ws or not self.ws.is_identified():
            return
        self.logger.info(f"_cache_slot_info: Spieler {player}, Gruppen={self._scene_groups}")
        for slot in range(6):
            for source in self._all_slot_sources(player, slot):
                if source in self._slot_item_info:
                    continue
                try:
                    info = await self._find_source_item(source)
                    if not info:
                        continue
                    container, item_id = info
                    self._slot_item_info[source] = (container, item_id)
                    tr_resp = await self.ws.call(simpleobsws.Request(
                        "GetSceneItemTransform",
                        {"sceneName": container, "sceneItemId": item_id}
                    ))
                    if tr_resp and tr_resp.ok() and tr_resp.responseData:
                        t = tr_resp.responseData.get("sceneItemTransform", {})
                        has_bounds = t.get("boundsType", "OBS_BOUNDS_NONE") != "OBS_BOUNDS_NONE"
                        self._slot_positions[source] = {
                            "positionX": t.get("positionX", 0.0),
                            "positionY": t.get("positionY", 0.0),
                            "scaleX": t.get("scaleX", 1.0),
                            "scaleY": t.get("scaleY", 1.0),
                            "rotation": t.get("rotation", 0.0),
                            "boundsWidth": t.get("boundsWidth", 0.0) if has_bounds else 0.0,
                            "boundsHeight": t.get("boundsHeight", 0.0) if has_bounds else 0.0,
                            "hasBounds": has_bounds,
                        }
                    self.logger.info(f"  {source}: group={container}, transform={self._slot_positions.get(source)}")
                except Exception as err:
                    self.logger.error(f"_cache_slot_info Fehler bei {source}: {err}")
                    self.logger.error(traceback.format_exc())

    async def _ensure_swap_filters(self, player: int):
        if not self.ws or not self.ws.is_identified():
            return
        groups_needed = set()
        for slot in range(6):
            for source in self._all_slot_sources(player, slot):
                info = self._slot_item_info.get(source)
                if info and info[0] != self._scene_name:
                    groups_needed.add(info[0])

        duration_ms = self.conf.get('obs_animation_duration_ms', 300)
        for group in groups_needed:
            if group in self._swap_filters:
                continue
            try:
                resp = await self.ws.call(simpleobsws.Request(
                    "GetSourceFilterList", {"sourceName": group}
                ))
                existing = set()
                if resp and resp.ok() and resp.responseData:
                    for f in resp.responseData.get("filters", []):
                        existing.add(f.get("filterName"))

                if self.SWAP_FILTER_NAME not in existing:
                    create_resp = await self.ws.call(simpleobsws.Request(
                        "CreateSourceFilter",
                        {
                            "sourceName": group,
                            "filterName": self.SWAP_FILTER_NAME,
                            "filterKind": self.SWAP_FILTER_KIND,
                            "filterSettings": {
                                "custom_duration": True,
                                "duration": duration_ms,
                                "easing_match": 3,
                                "easing_function_match": 2,
                                "enabled_match_moving": True,
                            },
                        }
                    ))
                    if not create_resp or not create_resp.ok():
                        self.logger.warning(f"Swap-Filter auf '{group}' konnte nicht erstellt werden")
                        continue
                    await self.ws.call(simpleobsws.Request(
                        "SetSourceFilterEnabled",
                        {"sourceName": group, "filterName": self.SWAP_FILTER_NAME, "filterEnabled": False}
                    ))

                self._swap_filters.add(group)
                self.logger.info(f"Swap-Filter auf Gruppe '{group}' bereit")
            except Exception as err:
                self.logger.error(f"_ensure_swap_filters Fehler bei {group}: {err}")
                self.logger.error(traceback.format_exc())

    @staticmethod
    def _compute_swap_sequence(slot_mapping: dict) -> list[tuple[int, int]]:
        visited = set()
        cycles = []
        for start in slot_mapping:
            if start in ('new_slots', 'removed_slots'):
                continue
            start = int(start) if isinstance(start, str) else start
            if start in visited:
                continue
            target = slot_mapping.get(start)
            if target is None or target == start:
                visited.add(start)
                continue
            cycle = []
            current = start
            while current not in visited:
                visited.add(current)
                cycle.append(current)
                nxt = slot_mapping.get(current)
                if nxt is None or nxt == start:
                    break
                current = nxt
            if len(cycle) >= 2:
                cycles.append(cycle)

        sequence = []
        for cycle in cycles:
            if len(cycle) == 2:
                sequence.append((cycle[0], cycle[1]))
            else:
                for i in range(1, len(cycle)):
                    sequence.append((cycle[0], cycle[i]))
        return sequence

    def _build_slot_content_batch(self, player: int, slots, team, edition) -> list:
        batch = []
        for slot in slots:
            sprite = self.get_sprite(team[slot], self.conf['animated'], edition, two_pc=self.conf['obs_2_pc'])
            batch.append(simpleobsws.Request(
                "SetInputSettings",
                {
                    "inputName": f"Slot{slot + 6 * (player - 1) + 1}",
                    "inputSettings": {"file": sprite},
                },
            ))
        if self.conf['show_nicknames']:
            for slot in slots:
                batch.append(simpleobsws.Request(
                    "SetInputSettings",
                    {
                        "inputName": f"name{slot + 6 * (player - 1) + 1}",
                        "inputSettings": {"text": team[slot].nickname},
                    },
                ))
        if self.conf['show_items'] and edition > 20:
            items_path = self.conf['items_path'] if not self.conf['obs_2_pc'] else self.conf['items_obs_path']
            for slot in slots:
                item_slug = str(team[slot].item)
                item_slug = resolve_tm_hm_sprite(
                    edition, item_slug,
                    self.munchlax.rando_tm_moves,
                    self.munchlax.rando_hm_moves,
                )
                batch.append(simpleobsws.Request(
                    "SetInputSettings",
                    {
                        "inputName": f"item{slot + 6 * (player - 1) + 1}",
                        "inputSettings": {
                            "file": items_path + '/' + item_slug + ".png"
                        },
                    },
                ))
        if self.conf.get('show_hp_bars'):
            batch.extend(self._build_hp_bar_updates(player, slots, team))
        if self.conf.get('show_status_effects'):
            batch.extend(self._build_filter_updates(player, slots, team))
        return batch

    async def _animate_swap(self, player: int, slot_mapping: dict, team, edition) -> set[int]:
        sequence = self._compute_swap_sequence(slot_mapping)
        if not sequence:
            return set()

        duration_ms = self.conf.get('obs_animation_duration_ms', 300)
        animated_slots = set()

        # Intermediäre Team-Reihenfolge berechnen:
        # swap_sequence transformiert old→new. Rückwärts anwenden ergibt old_order.
        current_order = list(range(6))
        for a, b in reversed(sequence):
            current_order[a], current_order[b] = current_order[b], current_order[a]
        # current_order[i] = team-Index der aktuell an Position i angezeigt wird

        for slot_a, slot_b in sequence:
            config_batch = []
            enable_batch = []
            sources_a = self._all_slot_sources(player, slot_a)
            sources_b = self._all_slot_sources(player, slot_b)
            configured_groups = set()

            for src_a, src_b in zip(sources_a, sources_b):
                info_a = self._slot_item_info.get(src_a)
                if not info_a:
                    continue
                group = info_a[0]
                if group not in self._swap_filters or group in configured_groups:
                    continue
                configured_groups.add(group)

                config_batch.append(simpleobsws.Request(
                    "SetSourceFilterSettings",
                    {
                        "sourceName": group,
                        "filterName": self.SWAP_FILTER_NAME,
                        "filterSettings": {
                            "source1": src_a,
                            "source2": src_b,
                            "duration": duration_ms,
                        },
                    }
                ))
                enable_batch.append(simpleobsws.Request(
                    "SetSourceFilterEnabled",
                    {"sourceName": group, "filterName": self.SWAP_FILTER_NAME, "filterEnabled": True}
                ))

            if config_batch:
                await self.ws.call_batch(config_batch)
            if enable_batch:
                self.logger.info(f"Swap-Animation: Slot {slot_a} <-> {slot_b} auf {len(enable_batch)} Gruppen")
                await self.ws.call_batch(enable_batch)
                await asyncio.sleep(duration_ms / 1000.0 + 0.05)

            # Intermediären Swap anwenden
            current_order[slot_a], current_order[slot_b] = current_order[slot_b], current_order[slot_a]

            # Atomar in einem Batch: Filter disable + Positionen zurücksetzen + Content-Update.
            # Swap-Filter ändert Positionen permanent — Disable revertiert NICHT.
            # Daher explizites Position-Reset auf Home-Koordinaten nötig.
            finalize_batch = [
                simpleobsws.Request(
                    "SetSourceFilterEnabled",
                    {"sourceName": g, "filterName": self.SWAP_FILTER_NAME, "filterEnabled": False}
                ) for g in configured_groups
            ]
            for slot in [slot_a, slot_b]:
                for source in self._all_slot_sources(player, slot):
                    home = self._slot_positions.get(source)
                    info = self._slot_item_info.get(source)
                    if not home or not info:
                        continue
                    container, item_id = info
                    finalize_batch.append(simpleobsws.Request(
                        "SetSceneItemTransform",
                        {
                            "sceneName": container,
                            "sceneItemId": item_id,
                            "sceneItemTransform": dict(home),
                        }
                    ))
            intermediate_team = [team[current_order[i]] for i in range(6)]
            finalize_batch.extend(self._build_slot_content_batch(player, [slot_a, slot_b], intermediate_team, edition))
            if finalize_batch:
                await self.ws.call_batch(finalize_batch)

            animated_slots.add(slot_a)
            animated_slots.add(slot_b)

        return animated_slots

    # ── Shift-Animation (Entfernen/Hinzufügen) ──────────────────────

    async def _ensure_move_filters(self, player: int):
        """Erstellt tracker_move_0..5 auf jeder Gruppe (wie Swap-Filter, aber pro Slot)."""
        if not self.ws or not self.ws.is_identified():
            return
        groups_needed = set()
        for slot in range(6):
            for source in self._all_slot_sources(player, slot):
                info = self._slot_item_info.get(source)
                if info and info[0] != self._scene_name:
                    groups_needed.add(info[0])

        duration_ms = self.conf.get('obs_animation_duration_ms', 300)
        for group in groups_needed:
            for i in range(6):
                fname = f"{self.MOVE_FILTER_NAME}_{i}"
                if f"{group}:{fname}" in self._move_filters_initialized:
                    continue
                try:
                    resp = await self.ws.call(simpleobsws.Request(
                        "GetSourceFilterList", {"sourceName": group}
                    ))
                    existing = set()
                    if resp and resp.ok() and resp.responseData:
                        existing = {f["filterName"] for f in resp.responseData.get("filters", [])}
                    if fname not in existing:
                        create_resp = await self.ws.call(simpleobsws.Request(
                            "CreateSourceFilter",
                            {
                                "sourceName": group,
                                "filterName": fname,
                                "filterKind": self.MOVE_FILTER_KIND,
                                "filterSettings": {
                                    "custom_duration": True,
                                    "duration": duration_ms,
                                    "easing_match": 3,
                                    "easing_function_match": 2,
                                    "enabled_match_moving": True,
                                    "transform": True,
                                    "start_trigger": 5,
                                },
                            }
                        ))
                        if create_resp and not create_resp.ok():
                            self.logger.error(f"Move-Filter '{fname}' auf '{group}' fehlgeschlagen: {create_resp.responseData}")
                            continue
                    await self.ws.call(simpleobsws.Request(
                        "SetSourceFilterEnabled",
                        {"sourceName": group, "filterName": fname, "filterEnabled": False}
                    ))
                    self._move_filters_initialized.add(f"{group}:{fname}")
                except Exception as err:
                    self.logger.error(f"_ensure_move_filters Fehler bei {group}:{fname}: {err}")
                    self.logger.error(traceback.format_exc())
        self.logger.debug(f"Move-Filter bereit: {len(self._move_filters_initialized)} Filter")

    async def _shift_slots(self, player: int, movements: dict[int, int]):
        """Animiert Slot-Sources via move_source_filter auf Gruppen (GPU-seitig)."""
        if not movements or not self._slot_positions:
            return

        duration_ms = self.conf.get('obs_animation_duration_ms', 300)
        await self._ensure_move_filters(player)

        # Pro Gruppe: welche Sources bewegen sich wohin?
        # group -> [(filter_index, source_name, target_pos)]
        group_moves: dict[str, list] = {}
        for old_slot, new_slot in movements.items():
            for src_old, src_new in zip(
                self._all_slot_sources(player, old_slot),
                self._all_slot_sources(player, new_slot),
            ):
                info = self._slot_item_info.get(src_old)
                target_transform = self._slot_positions.get(src_new)
                if not info or not target_transform:
                    continue
                group = info[0]
                if group == self._scene_name:
                    continue
                if group not in group_moves:
                    group_moves[group] = []
                group_moves[group].append((src_old, target_transform))

        if not group_moves:
            return

        total = sum(len(v) for v in group_moves.values())
        self.logger.info(f"Shift-Animation: {len(movements)} Slots, {total} Sources auf {len(group_moves)} Gruppen")

        # Batch 1: Konfigurieren — jede Bewegung bekommt einen eigenen Filter (tracker_move_0..N)
        config_batch = []
        enable_batch = []
        for group, moves in group_moves.items():
            for i, (source, target) in enumerate(moves):
                fname = f"{self.MOVE_FILTER_NAME}_{i}"
                if f"{group}:{fname}" not in self._move_filters_initialized:
                    continue
                tx = target["positionX"]
                ty = target["positionY"]
                bw = target.get("boundsWidth", 0.0)
                bh = target.get("boundsHeight", 0.0)
                sx = target.get("scaleX", 1.0)
                sy = target.get("scaleY", 1.0)
                has_bounds = target.get("hasBounds", False)
                s = " "
                settings = {
                    "source": source,
                    "duration": duration_ms,
                    "start_trigger": 5,
                    "pos": {"x": tx, "x_sign": s, "y": ty, "y_sign": s},
                    "rot": 0.0,
                    "rot_sign": s,
                    "bounds": {"x": bw, "x_sign": s, "y": bh, "y_sign": s},
                    "scale": {"x": sx, "x_sign": s, "y": sy, "y_sign": s},
                    "crop": {
                        "left": 0.0, "left_sign": s,
                        "right": 0.0, "right_sign": s,
                        "top": 0.0, "top_sign": s,
                        "bottom": 0.0, "bottom_sign": s,
                    },
                }
                if has_bounds:
                    settings["transform_text"] = (
                        f"pos: x {tx:.1f} y {ty:.1f} rot: 0.0 "
                        f"bounds: x {bw:.3f} y {bh:.3f} crop: l 0 t 0 r 0 b 0"
                    )
                else:
                    settings["transform_text"] = (
                        f"pos: x {tx:.1f} y {ty:.1f} rot: 0.0 "
                        f"scale: x {sx:.3f} y {sy:.3f} crop: l 0 t 0 r 0 b 0"
                    )
                config_batch.append(simpleobsws.Request(
                    "SetSourceFilterSettings",
                    {
                        "sourceName": group,
                        "filterName": fname,
                        "filterSettings": settings,
                    }
                ))
                enable_batch.append(simpleobsws.Request(
                    "SetSourceFilterEnabled",
                    {"sourceName": group, "filterName": fname, "filterEnabled": True}
                ))

        if config_batch:
            results = await self.ws.call_batch(config_batch)
            for j, r in enumerate(results):
                if not r.ok():
                    self.logger.error(f"Move config batch[{j}] Fehler: {r.responseData}")
        if enable_batch:
            await self.ws.call_batch(enable_batch)

        # Warten bis Animation fertig
        await asyncio.sleep(duration_ms / 1000.0 + 0.05)

        # Batch 3: Filter disablen
        disable_batch = []
        for group, moves in group_moves.items():
            for i in range(len(moves)):
                fname = f"{self.MOVE_FILTER_NAME}_{i}"
                if f"{group}:{fname}" in self._move_filters_initialized:
                    disable_batch.append(simpleobsws.Request(
                        "SetSourceFilterEnabled",
                        {"sourceName": group, "filterName": fname, "filterEnabled": False}
                    ))
        if disable_batch:
            await self.ws.call_batch(disable_batch)

        # Batch 4: Positionen via Move-Filter auf Home zurücksetzen (duration=0, instant)
        # SetSceneItemTransform wirkt auf anderer Ebene als Move-Filter — nutze Filter selbst zum Reset
        home_config = []
        home_enable = []
        for group, moves in group_moves.items():
            for i, (source, _) in enumerate(moves):
                fname = f"{self.MOVE_FILTER_NAME}_{i}"
                if f"{group}:{fname}" not in self._move_filters_initialized:
                    continue
                home = self._slot_positions.get(source)
                if not home:
                    continue
                hx = home["positionX"]
                hy = home["positionY"]
                bw = home.get("boundsWidth", 0.0)
                bh = home.get("boundsHeight", 0.0)
                sx = home.get("scaleX", 1.0)
                sy = home.get("scaleY", 1.0)
                has_bounds = home.get("hasBounds", False)
                s = " "
                home_settings = {
                    "source": source,
                    "duration": 0,
                    "start_trigger": 5,
                    "pos": {"x": hx, "x_sign": s, "y": hy, "y_sign": s},
                    "rot": 0.0, "rot_sign": s,
                    "bounds": {"x": bw, "x_sign": s, "y": bh, "y_sign": s},
                    "scale": {"x": sx, "x_sign": s, "y": sy, "y_sign": s},
                    "crop": {"left": 0.0, "left_sign": s, "right": 0.0, "right_sign": s,
                             "top": 0.0, "top_sign": s, "bottom": 0.0, "bottom_sign": s},
                }
                if has_bounds:
                    home_settings["transform_text"] = (
                        f"pos: x {hx:.1f} y {hy:.1f} rot: 0.0 "
                        f"bounds: x {bw:.3f} y {bh:.3f} crop: l 0 t 0 r 0 b 0"
                    )
                else:
                    home_settings["transform_text"] = (
                        f"pos: x {hx:.1f} y {hy:.1f} rot: 0.0 "
                        f"scale: x {sx:.3f} y {sy:.3f} crop: l 0 t 0 r 0 b 0"
                    )
                home_config.append(simpleobsws.Request(
                    "SetSourceFilterSettings",
                    {"sourceName": group, "filterName": fname, "filterSettings": home_settings}
                ))
                home_enable.append(simpleobsws.Request(
                    "SetSourceFilterEnabled",
                    {"sourceName": group, "filterName": fname, "filterEnabled": True}
                ))
        if home_config:
            await self.ws.call_batch(home_config)
        if home_enable:
            await self.ws.call_batch(home_enable)
            await asyncio.sleep(0.05)
            # Wieder disablen
            home_disable = []
            for group, moves in group_moves.items():
                for i in range(len(moves)):
                    fname = f"{self.MOVE_FILTER_NAME}_{i}"
                    if f"{group}:{fname}" in self._move_filters_initialized:
                        home_disable.append(simpleobsws.Request(
                            "SetSourceFilterEnabled",
                            {"sourceName": group, "filterName": fname, "filterEnabled": False}
                        ))
            if home_disable:
                await self.ws.call_batch(home_disable)

    # ── Fade-Animation (Hinzufügen/Entfernen) ───────────────────────

    async def _ensure_fade_filters(self, player: int, slots):
        if not self.ws or not self.ws.is_identified():
            return
        for slot in slots:
            for source in self._all_slot_sources(player, slot):
                if source in self._fade_filters_initialized:
                    continue
                try:
                    resp = await self.ws.call(simpleobsws.Request(
                        "GetSourceFilterList", {"sourceName": source}
                    ))
                    existing = set()
                    if resp and resp.ok() and resp.responseData:
                        existing = {f["filterName"] for f in resp.responseData.get("filters", [])}
                    if self.FADE_FILTER_NAME not in existing:
                        create_resp = await self.ws.call(simpleobsws.Request(
                            "CreateSourceFilter",
                            {
                                "sourceName": source,
                                "filterName": self.FADE_FILTER_NAME,
                                "filterKind": self.FADE_FILTER_KIND,
                                "filterSettings": {"opacity": 1.0},
                            }
                        ))
                        if create_resp and not create_resp.ok():
                            self.logger.error(f"Fade-Filter erstellen fehlgeschlagen für {source}: {create_resp.responseData}")
                    await self.ws.call(simpleobsws.Request(
                        "SetSourceFilterEnabled",
                        {"sourceName": source, "filterName": self.FADE_FILTER_NAME, "filterEnabled": True}
                    ))
                    self._fade_filters_initialized.add(source)
                    self.logger.debug(f"Fade-Filter bereit: {source}")
                except Exception as err:
                    self.logger.error(f"_ensure_fade_filters Fehler bei {source}: {err}")
                    self.logger.error(traceback.format_exc())

    async def _fade_slots(self, player: int, slots: list[int], fade_in: bool = True):
        if not slots:
            return
        self.logger.info(f"_fade_slots: slots={slots}, fade_in={fade_in}")
        duration_ms = self.conf.get('obs_animation_duration_ms', 300)
        steps = 10
        start = 0.0 if fade_in else 1.0
        end = 1.0 if fade_in else 0.0
        step_time = duration_ms / 1000.0 / steps

        for i in range(steps + 1):
            t = i / steps
            t = t * t * (3.0 - 2.0 * t)
            opacity = start + (end - start) * t

            batch = []
            for slot in slots:
                for source in self._all_slot_sources(player, slot):
                    batch.append(simpleobsws.Request(
                        "SetSourceFilterSettings",
                        {
                            "sourceName": source,
                            "filterName": self.FADE_FILTER_NAME,
                            "filterSettings": {"opacity": opacity},
                        }
                    ))
            if batch:
                results = await self.ws.call_batch(batch)
                if i == 0:
                    for j, r in enumerate(results):
                        if not r.ok():
                            self.logger.error(f"Fade batch[{j}] Fehler: {r.responseData}")
            if i < steps:
                await asyncio.sleep(step_time)

    # ── Filter-Management (Status-Effekte) ───────────────────────────

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

    async def changeSource(self, player, slots, team, edition, slot_mapping=None):
        if not self.ws or not self.ws.is_identified():
            self.is_connected = False
            return
        if not slots:
            return
        self.logger.info(f"changeSource: Spieler {player}, Slots {list(slots)}")

        animate = (self.conf.get('animate_obs_reorder')
                   and slot_mapping is not None
                   and self._scene_name is not None)
        self.logger.debug(f"animate={animate}, animate_obs_reorder={self.conf.get('animate_obs_reorder')}, "
                          f"slot_mapping={slot_mapping}, _scene_name={self._scene_name}")
        animated_slots = set()
        fade_in_slots = []
        if animate:
            if not self._slot_item_info:
                await self._cache_slot_info(player)
                await self._ensure_swap_filters(player)

            removed_slots = slot_mapping.get('removed_slots', [])
            new_slots = slot_mapping.get('new_slots', [])
            self.logger.debug(f"removed_slots={removed_slots}, new_slots={new_slots}")

            # Movements: Slots die sich verschieben (nicht None, nicht self-mapped)
            movements = {}
            for old, new in slot_mapping.items():
                if old in ('new_slots', 'removed_slots'):
                    continue
                old = int(old) if isinstance(old, str) else old
                if new is not None and new != old:
                    movements[old] = new

            has_shift = bool(removed_slots) or bool(new_slots)

            if has_shift and movements:
                # ── Shift-Animation (Entfernen/Hinzufügen) ──────────────

                # Fade-out: Slots die im neuen Team leer sind
                fade_out_slots = []
                if removed_slots:
                    fade_out_slots = list(removed_slots)

                # Fade-in: neue Pokemon
                fade_in_slots = list(new_slots)

                # Fade-Filter vorbereiten (noch nicht opacity ändern!)
                if fade_in_slots:
                    await self._ensure_fade_filters(player, fade_in_slots)
                if fade_out_slots:
                    await self._ensure_fade_filters(player, fade_out_slots)

                # Shift + Fade-out gleichzeitig (alter Content noch sichtbar!)
                coros = [self._shift_slots(player, movements)]
                if fade_out_slots:
                    coros.append(self._fade_slots(player, fade_out_slots, fade_in=False))
                await asyncio.gather(*coros)

                # NACH Shift: neue Slots unsichtbar machen, dann Content-Update
                if fade_in_slots:
                    opacity_batch = []
                    for slot in fade_in_slots:
                        for source in self._all_slot_sources(player, slot):
                            opacity_batch.append(simpleobsws.Request(
                                "SetSourceFilterSettings",
                                {
                                    "sourceName": source,
                                    "filterName": self.FADE_FILTER_NAME,
                                    "filterSettings": {"opacity": 0.0},
                                }
                            ))
                    if opacity_batch:
                        await self.ws.call_batch(opacity_batch)

                # Content-Update für alle Slots (nach Shift sind Positionen zurückgesetzt)
                batch = self._build_slot_content_batch(player, list(range(len(team))), team, edition)
                if batch:
                    await self.ws.call_batch(batch)

                # Opacity zurücksetzen: fade_out_slots (jetzt neuer Content) + Bewegungsziele
                opacity_reset_slots = set(fade_out_slots) | set(movements.values())
                if fade_in_slots:
                    opacity_reset_slots -= set(fade_in_slots)
                if opacity_reset_slots:
                    reset_batch = []
                    for slot in opacity_reset_slots:
                        for source in self._all_slot_sources(player, slot):
                            if source in self._fade_filters_initialized:
                                reset_batch.append(simpleobsws.Request(
                                    "SetSourceFilterSettings",
                                    {
                                        "sourceName": source,
                                        "filterName": self.FADE_FILTER_NAME,
                                        "filterSettings": {"opacity": 1.0},
                                    }
                                ))
                    if reset_batch:
                        await self.ws.call_batch(reset_batch)

                # Fade-in neue Pokemon
                if fade_in_slots:
                    await self._fade_slots(player, fade_in_slots, fade_in=True)

                animated_slots = set(movements.keys()) | set(movements.values()) | set(fade_out_slots)
                fade_in_slots = []  # Shift-Pfad hat Fade-In bereits erledigt

            elif has_shift:
                # ── Nur Fade (letzter Slot add/remove, kein Shift) ──────
                fade_out_slots = []
                if removed_slots:
                    fade_out_slots = list(removed_slots)
                fade_in_slots = list(new_slots)

                # Neue Slots unsichtbar machen
                if fade_in_slots:
                    await self._ensure_fade_filters(player, fade_in_slots)
                    opacity_batch = []
                    for slot in fade_in_slots:
                        for source in self._all_slot_sources(player, slot):
                            opacity_batch.append(simpleobsws.Request(
                                "SetSourceFilterSettings",
                                {
                                    "sourceName": source,
                                    "filterName": self.FADE_FILTER_NAME,
                                    "filterSettings": {"opacity": 0.0},
                                }
                            ))
                    if opacity_batch:
                        await self.ws.call_batch(opacity_batch)

                # Fade-out
                if fade_out_slots:
                    await self._ensure_fade_filters(player, fade_out_slots)
                    await self._fade_slots(player, fade_out_slots, fade_in=False)

                # Content-Update
                all_affected = set(fade_out_slots) | set(fade_in_slots)
                content_batch = self._build_slot_content_batch(player, list(all_affected), team, edition)
                if content_batch:
                    await self.ws.call_batch(content_batch)

                # Fade-in
                if fade_in_slots:
                    await self._fade_slots(player, fade_in_slots, fade_in=True)

                animated_slots = all_affected
                fade_in_slots = []  # Fade-only-Pfad hat alles erledigt

            elif movements:
                # ── Swap-Animation (Reorder ohne Add/Remove) ────────────
                if self._swap_filters:
                    animated_slots = await self._animate_swap(player, slot_mapping, team, edition)

                # Fade-in für neue Slots die NICHT durch Swap animiert wurden
                # (Normalerweise leer bei reinem Swap)
                fade_in_slots = [s for s in new_slots if s not in animated_slots]
                if fade_in_slots:
                    await self._ensure_fade_filters(player, fade_in_slots)
                    opacity_batch = []
                    for slot in fade_in_slots:
                        for source in self._all_slot_sources(player, slot):
                            opacity_batch.append(simpleobsws.Request(
                                "SetSourceFilterSettings",
                                {
                                    "sourceName": source,
                                    "filterName": self.FADE_FILTER_NAME,
                                    "filterSettings": {"opacity": 0.0},
                                }
                            ))
                    if opacity_batch:
                        await self.ws.call_batch(opacity_batch)

        # Content-Update für nicht-animierte Slots
        remaining_slots = [s for s in slots if s not in animated_slots]
        batch = self._build_slot_content_batch(player, remaining_slots, team, edition)
        if batch:
            await self.ws.call_batch(batch)

        if animate and fade_in_slots:
            await self._fade_slots(player, fade_in_slots, fade_in=True)

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

    async def change_team_badges(self, team_id: str):
        """Aktualisiert Team-Badge-Scene-Items in OBS. Aggregat = bitwise AND
        aller Owner-Bitmasks pro Region. Input-Namens-Konvention:
        `team_{team_id}_{region}_{i+1}` (1-basiert, i=0..badge_count-1).

        Streamer legt die Inputs so an, wie er will (nur eine Region → nur
        eine Row; mehrere Regionen → Zeilen pro Region). Fehlende Inputs
        werden von OBS ignoriert (SetInputSettings 600er-Response), das ist
        gewollt: kein Fehler wenn Streamer die Region nicht angelegt hat.
        """
        from backend.classes.overlay_server import BADGE_REGION
        if not self.conf.get('show_badges'):
            return
        if not self.ws or not self.ws.is_identified():
            self.is_connected = False
            return
        team_state = getattr(self.munchlax, 'soullink_team_state', {}) or {}
        bucket = team_state.get(team_id)
        if not bucket:
            return
        owners = list(bucket.get('owners', []) or [])
        editions_by_owner = bucket.get('editions_by_owner', {}) or {}
        badges_by_owner = bucket.get('badges_by_owner', {}) or {}

        region_owners: dict[str, list[str]] = {}
        for owner in owners:
            edition = editions_by_owner.get(owner)
            if edition is None:
                continue
            region = BADGE_REGION.get(edition, '')
            if not region:
                continue
            region_owners.setdefault(region, []).append(owner)
        if not region_owners:
            return

        badge_path = self.conf['badges_path'] if not self.conf['obs_2_pc'] else self.conf['badges_obs_path']
        self.logger.info(f"change_team_badges ausgeführt: team_id={team_id}, regions={list(region_owners.keys())}")

        batch = []
        for region, r_owners in region_owners.items():
            badge_count = 16 if region == 'johto' else 8
            mask_all = (1 << badge_count) - 1
            badges_and = mask_all
            for o in r_owners:
                v = badges_by_owner.get(o)
                badges_and &= v if isinstance(v, int) else 0
            for i in range(badge_count):
                input_name = f"team_{team_id}_{region}_{i + 1}"
                if badges_and & (1 << i):
                    file_name = f"{region}{i + 1}.png"
                else:
                    file_name = f"{region}{i + 1}empty.png"
                batch.append(
                    simpleobsws.Request(
                        "SetInputSettings",
                        {
                            "inputName": input_name,
                            "inputSettings": {
                                "file": badge_path + '/' + file_name,
                            },
                        },
                    )
                )
        if batch:
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
