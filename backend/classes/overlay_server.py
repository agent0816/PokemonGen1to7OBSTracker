import asyncio
import json
import mimetypes
import traceback
from pathlib import Path

from aiohttp import web

from backend.classes.pokedex_db import PokedexDB
from backend.logging_setup import get_logger
from backend.team_id import slug_team_id
from backend.tm_type_resolver import resolve_tm_hm_sprite

EDITION_SUBPATH = {
    11: 'red', 12: 'red', 13: 'yellow',
    21: 'silver', 22: 'gold', 23: 'crystal',
    31: 'ruby', 32: 'ruby', 33: 'emerald', 34: 'firered', 35: 'firered',
    41: 'diamond', 42: 'diamond', 43: 'platinum', 44: 'heartgold', 45: 'heartgold',
    51: 'black', 52: 'black', 53: 'black', 54: 'black',
    61: 'x', 62: 'x', 63: 'alphasapphire', 64: 'alphasapphire',
    71: 'sun', 72: 'sun', 73: 'usun', 74: 'usun',
}

BADGE_REGION = {
    11: 'kanto', 12: 'kanto', 13: 'kanto',
    21: 'johto', 22: 'johto', 23: 'johto',
    31: 'hoenn', 32: 'hoenn', 33: 'hoenn',
    34: 'kanto', 35: 'kanto',
    41: 'sinnoh', 42: 'sinnoh', 43: 'sinnoh',
    44: 'johto', 45: 'johto',
    51: 'unova', 52: 'unova', 53: 'unova2', 54: 'unova2',
    61: 'kalos', 62: 'kalos', 63: 'hoenn', 64: 'hoenn',
    71: 'alola', 72: 'alola', 73: 'alola', 74: 'alola',
}

ANIMATED_EDITIONS = (23, 33, 41, 42, 43, 44, 45, 51, 52, 53, 54, 61, 62, 63, 64, 71, 72, 73, 74)

FEMALE_DEXNRS = {
    3, 12, 19, 20, 25, 26, 41, 42, 44, 45, 64, 65, 84, 85, 97, 111, 112,
    118, 119, 123, 129, 130, 154, 165, 166, 178, 185, 186, 190, 194, 195,
    198, 202, 203, 207, 208, 212, 214, 215, 217, 221, 224, 229, 232, 255,
    256, 257, 267, 269, 272, 274, 275, 307, 308, 315, 316, 317, 322, 323,
    332, 350, 369, 396, 397, 398, 399, 400, 401, 402, 403, 404, 405, 407,
    415, 417, 418, 419, 424, 443, 444, 445, 449, 450, 453, 454, 456, 457,
    459, 460, 461, 464, 465, 473, 521, 592, 593, 668, 678, 876, 902,
}


class OverlayServer:
    def __init__(self, munchlax, sp: dict, obs_conf: dict, ov: dict):
        self.munchlax = munchlax
        self.sp = sp
        self.obs_conf = obs_conf
        self.ov = ov
        self.port = int(ov.get('port', 43888))
        self.is_connected = False
        self.app: web.Application | None = None
        self.runner: web.AppRunner | None = None
        self.site: web.TCPSite | None = None
        self._sse_queues: dict[int, list[asyncio.Queue]] = {}
        # Session-weite SSE-Queues (nicht an player_id gebunden) für Soullink
        # und Race-/Countdown-Timer. Overlay-Seite /timer verbindet sich hier.
        self._session_sse_queues: list[asyncio.Queue] = []
        # Team-SSE-Queues für Team-Badge-Overlay pro team_id.
        self._team_sse_queues: dict[str, list[asyncio.Queue]] = {}

        self.logger = get_logger(__name__, './logs/overlay_server.log')

    async def start(self):
        try:
            self.port = int(self.ov.get('port', 43888))
            self.app = web.Application()
            self._setup_routes()
            self.runner = web.AppRunner(self.app)
            await self.runner.setup()
            self.site = web.TCPSite(self.runner, '0.0.0.0', self.port)
            await self.site.start()
            self.is_connected = 'connected'
            self.logger.info(f"Overlay-Server gestartet auf Port {self.port}")
        except OSError as err:
            self.logger.error(f"Port {self.port} belegt: {err}")
            self.is_connected = False
        except Exception as err:
            self.logger.error(f"Overlay-Server Start fehlgeschlagen: {type(err)}, {err}")
            self.logger.error(traceback.format_exc())
            self.is_connected = False

    async def stop(self):
        self.is_connected = False
        try:
            if self.runner:
                await self.runner.cleanup()
            self.logger.info("Overlay-Server gestoppt.")
        except Exception as err:
            self.logger.error(f"Overlay-Server Stop fehlgeschlagen: {type(err)}, {err}")
            self.logger.error(traceback.format_exc())
        finally:
            self.runner = None
            self.site = None
            self.app = None
            self._sse_queues.clear()
            self._session_sse_queues.clear()
            self._team_sse_queues.clear()

    def _setup_routes(self):
        self.app.router.add_get('/player/{player_id}', self._handle_team_page)
        self.app.router.add_get('/player/{player_id}/badges', self._handle_badges_page)
        self.app.router.add_get('/player/{player_id}/events', self._handle_sse)
        self.app.router.add_get('/player/{player_id}/state', self._handle_state)
        self.app.router.add_get('/sprite/{player_id}/{slot}', self._handle_sprite)
        self.app.router.add_get('/item/{player_id}/{slot}', self._handle_item)
        self.app.router.add_get('/badge_img/{player_id}/{index}', self._handle_badge_img)
        # Team-Badge-Overlay (AND ueber alle Owner eines Teams, Region-Gruppierung).
        self.app.router.add_get('/team/{team_id}/badges', self._handle_team_badges_page)
        self.app.router.add_get('/team/{team_id}/badges/data', self._handle_team_badges_data)
        self.app.router.add_get('/team/{team_id}/badges/events', self._handle_team_sse)
        self.app.router.add_get('/team_badge_img/{team_id}/{region}/{index}', self._handle_team_badge_img)
        # Session-Events (Timer, Countdown, Soullink) + Overlay-Seite /timer
        self.app.router.add_get('/session/events', self._handle_session_sse)
        self.app.router.add_get('/session/state', self._handle_session_state)
        self.app.router.add_get('/timer', self._handle_timer_page)

    # --- Notify (aufgerufen von Munchlax) ---

    async def notify_update(self, player_id: int, update_type: str, slot_mapping: dict | None = None):
        queues = self._sse_queues.get(player_id, [])
        if not queues:
            return
        payload = self._build_full_payload(player_id)
        if slot_mapping is not None:
            payload["slot_mapping"] = {
                str(k): v for k, v in slot_mapping.items()
                if k not in ('new_slots', 'removed_slots')
            }
            payload["new_slots"] = slot_mapping.get('new_slots', [])
            payload["removed_slots"] = slot_mapping.get('removed_slots', [])
        for p in payload.get('team', []):
            if p.get('dexnr', 0) != 0:
                self.logger.debug(f"SSE slot={p['slot']}: dexnr={p['dexnr']}, item={p.get('item','')}, nickname={p.get('nickname','')}")
        data = json.dumps(payload, ensure_ascii=False)
        event = f"event: {update_type}\ndata: {data}\n\n"
        dead = []
        for q in queues:
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                dead.append(q)
        for q in dead:
            queues.remove(q)

    async def notify_config_change(self):
        for player_id in list(self._sse_queues.keys()):
            await self.notify_update(player_id, "team")
            await self.notify_update(player_id, "badges")
        for team_id in list(self._team_sse_queues.keys()):
            await self.notify_team_update(team_id, "badges")

    async def notify_team_update(self, team_id: str, update_type: str):
        """Pusht ein SSE-Event an alle /team/{team_id}/badges/events-Clients."""
        queues = self._team_sse_queues.get(team_id, [])
        if not queues:
            return
        payload = self._build_team_badges_payload(team_id)
        try:
            data = json.dumps(payload, ensure_ascii=False, default=str)
        except Exception as err:
            self.logger.warning(f"notify_team_update serialize failed: {err}")
            return
        event = f"event: {update_type}\ndata: {data}\n\n"
        dead = []
        for q in queues:
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                dead.append(q)
        for q in dead:
            if q in queues:
                queues.remove(q)

    async def notify_session_event(self, event_type: str, payload: dict):
        """Verteilt Session-Events (Timer/Countdown/Soullink) an alle /session/events-Clients."""
        if not self._session_sse_queues:
            return
        try:
            data = json.dumps(payload, ensure_ascii=False, default=str)
        except Exception as err:
            self.logger.warning(f"notify_session_event serialize failed: {err}")
            return
        event = f"event: {event_type}\ndata: {data}\n\n"
        dead = []
        for q in list(self._session_sse_queues):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                dead.append(q)
        for q in dead:
            if q in self._session_sse_queues:
                self._session_sse_queues.remove(q)

    def _build_session_snapshot(self) -> dict:
        m = self.munchlax
        return {
            "soullink_config": getattr(m, "soullink_config", {}) or {},
            "soullink_links": list((getattr(m, "soullink_links", {}) or {}).values()),
            "soullink_deaths": list((getattr(m, "soullink_deaths", {}) or {}).values()),
            "soullink_versus_state": getattr(m, "soullink_versus_state", {}) or {},
            "soullink_versus_battles": list(getattr(m, "soullink_versus_battles", []) or []),
            "soullink_rule_violations": list((getattr(m, "soullink_rule_violations", {}) or {}).values()),
            "timer_state": getattr(m, "timer_state", {}) or {},
            "countdown_state": getattr(m, "countdown_state", {}) or {},
        }

    # --- Payload-Builder ---

    def _build_full_payload(self, player_id: int) -> dict:
        team = self.munchlax.sorted_teams.get(player_id, [])
        edition = self.munchlax.editions.get(player_id, 0)
        badges_val = self.munchlax.badges.get(player_id, 0)
        player_name = self.munchlax.player_names.get(player_id, f"Spieler {player_id}")

        owner = self._owner_for_local_player(player_id)
        hide_incomplete = self._soullink_filter_active()
        link_id_by_pv = self._link_id_by_personality(owner)

        team_data = []
        for slot, pkmn in enumerate(team[:6]):
            if pkmn.dexnr == 0:
                team_data.append({"slot": slot, "dexnr": 0, "identity_key": f"empty_{slot}"})
                continue
            pv = getattr(pkmn, "personality", None)
            link_state = self._pokemon_link_state(pkmn, owner)
            if hide_incomplete and link_state != "complete":
                team_data.append({
                    "slot": slot,
                    "dexnr": 0,
                    "identity_key": f"empty_{slot}",
                    "link_hidden": True,
                    "link_state": link_state,
                    "personality": pv,
                })
                continue
            shiny_flag = 1 if pkmn.shiny else 0
            female_flag = 1 if pkmn.female else 0
            sprite_cache_key = f"{pkmn.dexnr}_{shiny_flag}_{female_flag}_{pkmn.form}"
            entry = {
                "slot": slot,
                "dexnr": pkmn.dexnr,
                "nickname": pkmn.nickname,
                "lvl": pkmn.lvl,
                "shiny": pkmn.shiny,
                "female": pkmn.female,
                "form": pkmn.form,
                "item": str(pkmn.item) if pkmn.item else "",
                "cur_hp": pkmn.cur_hp,
                "max_hp": pkmn.max_hp,
                "sprite_url": f"/sprite/{player_id}/{slot}?v={sprite_cache_key}",
                "personality": pv,
            }
            entry["identity_key"] = pkmn.identity_key or f"empty_{slot}"
            if link_state is not None:
                entry["link_state"] = link_state
            if pv in link_id_by_pv:
                entry["link_id"] = link_id_by_pv[pv]
            status = getattr(pkmn, 'status', {})
            if status:
                entry["status"] = status
            if pkmn.item and edition > 20:
                entry["item_url"] = f"/item/{player_id}/{slot}?v={pkmn.item}"
            team_data.append(entry)

        # Duplikate in identity_key uniquifizieren (Cheat-Teams mit identischer
        # PV, Eier vor PV-Roll etc.). Sonst kollidiert die Node-Map im Frontend
        # und nur ein Slot bleibt sichtbar.
        seen_keys: dict[str, int] = {}
        for entry in team_data:
            k = entry.get("identity_key")
            if k is None:
                continue
            if k in seen_keys:
                entry["identity_key"] = f"{k}_slot{entry['slot']}"
            else:
                seen_keys[k] = 1

        # Sortierung anwenden (Task #14): leere Slots ans Ende, sonst je nach sort_mode.
        team_data = self._apply_slot_sort(team_data, link_id_by_pv)

        region = BADGE_REGION.get(edition, '')
        badge_count = 16 if region == 'johto' else 8
        badge_list = []
        for i in range(badge_count):
            earned = bool(badges_val & (1 << i))
            badge_list.append({
                "index": i,
                "earned": earned,
                "url": f"/badge_img/{player_id}/{i}?v={badges_val}",
            })

        return {
            "player_id": player_id,
            "player_name": player_name,
            "edition": edition,
            "badges_raw": badges_val,
            "badge_region": region,
            "badge_count": badge_count,
            "badges": badge_list,
            "team": team_data,
            "show_nicknames": self.sp.get('show_nicknames', False),
            "show_items": self.sp.get('show_items', False),
            "show_badges": self.sp.get('show_badges', False),
            "show_hp_bars": self.sp.get('show_hp_bars', False),
            "show_status_effects": self.sp.get('show_status_effects', False),
            "team_layout": self.ov.get('layout', 'horizontal'),
            "badge_layout": self.ov.get('badge_layout', 'horizontal'),
            "animate_reorder": self.ov.get('animate_reorder', False),
            "animation_duration_ms": self.ov.get('animation_duration_ms', 300),
        }

    # --- Team-Badge-Aggregation ---

    def _local_team_state(self) -> dict[str, dict]:
        """Fallback wenn kein soullink_team_state vom Server vorliegt: baut
        Team-Buckets aus lokalen badges/editions + nuzlocke.yml-Preset. Ohne
        Preset-Awareness liefe /team/coop/badges ins Leere (team_id = slug(owner)
        matcht nicht die URL, die das Overlay-Menü aus dem Preset erzeugt).
        Spiegelt arceus._effective_team_membership fuer den serverlosen Fall.
        """
        m = self.munchlax
        try:
            player_ids = sorted((getattr(m, "badges", {}) or {}).keys())
        except Exception:
            player_ids = []
        if not player_ids:
            return {}
        # local_owner-Matching gegen nuz.soullink_expected_owners. Diese sind
        # reine Namen ("Stephan"), _owner_for_local_player liefert dagegen
        # build_owner-Format ("Stephan_<client_id>") — direkter Vergleich matcht
        # nie. Deshalb hier pl.your_name als Match-Owner nutzen.
        try:
            local_owner = (getattr(m, "pl", {}) or {}).get("your_name") or ""
        except AttributeError:
            local_owner = ""
        if not local_owner:
            local_owner = self._owner_for_local_player(player_ids[0]) or "local"
        badges_val = None
        edition_val = None
        for pid in player_ids:
            b = (getattr(m, "badges", {}) or {}).get(pid)
            if isinstance(b, int) and (badges_val is None or b > badges_val):
                badges_val = b
            e = (getattr(m, "editions", {}) or {}).get(pid)
            if e is not None:
                edition_val = e

        def _empty_bucket(tid: str) -> dict:
            return {
                "team_id": tid,
                "owners": [],
                "badges_by_owner": {},
                "editions_by_owner": {},
                "player_ids_by_owner": {},
            }

        def _add_owner(bucket: dict, owner: str):
            if not owner or owner in bucket["owners"]:
                return
            bucket["owners"].append(owner)
            if owner == local_owner:
                bucket["badges_by_owner"][owner] = badges_val
                bucket["editions_by_owner"][owner] = edition_val
                bucket["player_ids_by_owner"][owner] = player_ids
            else:
                bucket["badges_by_owner"][owner] = None
                bucket["editions_by_owner"][owner] = None
                bucket["player_ids_by_owner"][owner] = []

        nuz = getattr(m, "nuz", {}) or {}
        mode = (nuz.get("soullink_mode") or "off").lower()
        team_map = nuz.get("soullink_team_membership", {}) or {}
        expected = nuz.get("soullink_expected_owners", []) or []

        if mode == "coop":
            buckets: dict[str, dict] = {}
            if team_map:
                for owner, raw_team in team_map.items():
                    if raw_team is None:
                        continue
                    tid = slug_team_id(raw_team)
                    _add_owner(buckets.setdefault(tid, _empty_bucket(tid)), owner)
            else:
                # NuzlockeMenu befuellt team_membership nur im versus-Zweig;
                # ohne Mapping alle expected_owners in ein Bucket "coop"
                # (analog arceus._effective_team_membership).
                tid = "coop"
                bucket = buckets.setdefault(tid, _empty_bucket(tid))
                if expected:
                    for owner in expected:
                        _add_owner(bucket, owner)
                else:
                    _add_owner(bucket, local_owner)
            return buckets

        if mode == "versus":
            buckets = {}
            for owner, raw_team in team_map.items():
                if raw_team is None:
                    continue
                tid = slug_team_id(raw_team)
                _add_owner(buckets.setdefault(tid, _empty_bucket(tid)), owner)
            return buckets

        if mode == "versus_ffa":
            buckets = {}
            for owner in expected:
                if not owner:
                    continue
                tid = slug_team_id(owner)
                _add_owner(buckets.setdefault(tid, _empty_bucket(tid)), owner)
            return buckets

        # off/nuzlocke: Solo-Bucket wie zuvor
        tid = slug_team_id(local_owner)
        bucket = _empty_bucket(tid)
        _add_owner(bucket, local_owner)
        return {tid: bucket}

    def _compute_team_badge_view(self, bucket: dict) -> dict:
        """Aggregiert einen Team-Bucket zu Region-Rows. Pro Region: badges_and
        aus AND aller Owner-Bitmasks in dieser Region (offline/unbekannt=0)."""
        team_id = bucket.get("team_id") or ""
        owners = list(bucket.get("owners", []) or [])
        editions_by_owner = bucket.get("editions_by_owner", {}) or {}
        badges_by_owner = bucket.get("badges_by_owner", {}) or {}
        region_order: list[str] = []
        region_owners: dict[str, list[str]] = {}
        for owner in owners:
            edition = editions_by_owner.get(owner)
            if edition is None:
                continue
            region = BADGE_REGION.get(edition, '')
            if not region:
                continue
            if region not in region_owners:
                region_owners[region] = []
                region_order.append(region)
            region_owners[region].append(owner)
        rows = []
        for region in region_order:
            r_owners = region_owners[region]
            badge_count = 16 if region == 'johto' else 8
            mask_all = (1 << badge_count) - 1
            badges_and = mask_all
            for o in r_owners:
                v = badges_by_owner.get(o)
                badges_and &= v if isinstance(v, int) else 0
            badges = []
            for i in range(badge_count):
                earned = bool(badges_and & (1 << i))
                badges.append({
                    "index": i,
                    "earned": earned,
                    "url": f"/team_badge_img/{team_id}/{region}/{i}?v={badges_and}",
                })
            rows.append({
                "region": region,
                "badge_count": badge_count,
                "badges_and": badges_and,
                "owners": r_owners,
                "badges": badges,
            })
        return {
            "team_id": team_id,
            "owners": owners,
            "rows": rows,
        }

    def _get_team_bucket(self, team_id: str) -> dict | None:
        # Server-State hat Prio (kennt tatsaechliche Owner-Rohdaten). Wenn
        # team_id dort nicht existiert, faellt auf _local_team_state zurueck —
        # das kennt den Coop-Preset aus nuz auch dann, wenn der Server die
        # Preset-Config noch nicht gespiegelt hat.
        m = self.munchlax
        team_state = getattr(m, "soullink_team_state", {}) or {}
        bucket = team_state.get(team_id) if team_state else None
        if bucket is None:
            local_state = self._local_team_state()
            bucket = local_state.get(team_id)
        return bucket

    def _build_team_badges_payload(self, team_id: str) -> dict:
        bucket = self._get_team_bucket(team_id)
        if not bucket:
            return {
                "team_id": team_id,
                "owners": [],
                "rows": [],
                "show_badges": self.sp.get('show_badges', False),
                "badge_layout": self.ov.get('badge_layout', 'horizontal'),
            }
        view = self._compute_team_badge_view(bucket)
        view["show_badges"] = self.sp.get('show_badges', False)
        view["badge_layout"] = self.ov.get('badge_layout', 'horizontal')
        return view

    def _link_id_by_personality(self, owner: str) -> dict[int, int]:
        """Mapping PID→link_id für alle Members des gegebenen Owners."""
        result: dict[int, int] = {}
        links = getattr(self.munchlax, "soullink_links", None)
        if not isinstance(links, dict):
            return result
        for link in links.values():
            member = (link.get("members") or {}).get(owner)
            if not member:
                continue
            pv = member.get("personality")
            if pv is None:
                continue
            result[pv] = link.get("link_id", 0)
        return result

    def _apply_slot_sort(self, team_data: list, link_id_by_pv: dict) -> list:
        """Sortiert Slots nach ov.sort_mode. Leere/versteckte Slots immer ans Ende.

        Modi:
        - default: Reihenfolge unverändert (leere Slots bleiben wo sie sind)
        - by_link_group: nach link_id aufsteigend, nicht-gelinkte danach, leere zuletzt
        - by_route: (nicht in Task 14 umgesetzt — Route-Info aktuell nicht im Payload)
        - by_dexnr: nach dexnr aufsteigend, leere zuletzt
        """
        mode = (self.ov.get("sort_mode", "default") or "default").lower()
        if mode == "default":
            # Nur leere Slots ans Ende schieben (Empty-Slot-Regel Task #13).
            visible = [e for e in team_data if e.get("dexnr", 0) != 0]
            empty = [e for e in team_data if e.get("dexnr", 0) == 0]
            return self._reindex_slots(visible + empty)
        if mode == "by_link_group":
            def key(entry):
                if entry.get("dexnr", 0) == 0:
                    return (2, 0, entry.get("slot", 0))
                pv = entry.get("personality")
                lid = link_id_by_pv.get(pv) if pv is not None else None
                if lid is None:
                    return (1, 0, entry.get("slot", 0))
                return (0, lid, entry.get("slot", 0))
            return self._reindex_slots(sorted(team_data, key=key))
        if mode == "by_dexnr":
            def key(entry):
                if entry.get("dexnr", 0) == 0:
                    return (1, 0)
                return (0, entry.get("dexnr", 0))
            return self._reindex_slots(sorted(team_data, key=key))
        # Fallback = default
        visible = [e for e in team_data if e.get("dexnr", 0) != 0]
        empty = [e for e in team_data if e.get("dexnr", 0) == 0]
        return self._reindex_slots(visible + empty)

    @staticmethod
    def _reindex_slots(entries: list) -> list:
        """Setzt slot=0..N-1 nach neuer Reihenfolge. Empty-identity_keys anpassen."""
        for i, e in enumerate(entries):
            e["slot"] = i
            if e.get("dexnr", 0) == 0 and str(e.get("identity_key", "")).startswith("empty_"):
                e["identity_key"] = f"empty_{i}"
        return entries

    def _owner_for_local_player(self, player_id: int) -> str:
        """Owner-String für lokal betreute Player (build_owner-Konvention)."""
        try:
            your_name = self.munchlax.pl.get("your_name", "") if self.munchlax.pl else ""
        except AttributeError:
            your_name = ""
        try:
            client_id = str(self.munchlax.rem.get("client_id", 0)) if self.munchlax.rem else "0"
        except AttributeError:
            client_id = "0"
        return PokedexDB.build_owner(your_name, client_id)

    def _soullink_filter_active(self) -> bool:
        """True wenn nicht-vollständig gelinkte Pokemon versteckt werden sollen."""
        cfg = getattr(self.munchlax, "soullink_config", None) or {}
        if cfg.get("mode", "off") not in ("coop", "versus"):
            return False
        return bool(self.ov.get("hide_incomplete_links", True))

    def _pokemon_link_state(self, pokemon, owner: str) -> str | None:
        """Sucht (personality, owner) in soullink_links. Return: link.state oder None."""
        pv = getattr(pokemon, "personality", None)
        if pv is None:
            return None
        links = getattr(self.munchlax, "soullink_links", None)
        if not isinstance(links, dict):
            return None
        for link in links.values():
            member = (link.get("members") or {}).get(owner)
            if not member:
                continue
            if member.get("personality") == pv:
                return link.get("state")
        return None

    def _resolve_sprite_path(self, pokemon, edition: int) -> str | None:
        """Baut Dateisystempfad — spiegelt OBS.get_sprite() Logik."""
        common_path = self.sp.get('common_path', '')
        if not common_path:
            return None
        game_key = EDITION_SUBPATH.get(edition)
        if not game_key:
            return None
        shiny = "shiny/" if pokemon.shiny else ""
        female = ""
        if pokemon.female and edition > 40 and pokemon.dexnr in FEMALE_DEXNRS:
            female = "female/"
        animated = ""
        filetype = ".png"
        if self.sp.get('animated', False) and edition in ANIMATED_EDITIONS:
            animated = "animated/"
            filetype = ".gif"
        if not self.sp.get('single_path_check', False):
            conf_path = self.sp.get(game_key, '')
            common_slash = "" if (common_path.endswith("/") and not conf_path.startswith("/")) or (not common_path.endswith("/") and conf_path.startswith("/")) else "/"
            slash = "" if conf_path.endswith("/") else "/"
            sub = common_slash + conf_path + slash
        else:
            sub = "" if common_path.endswith("/") else "/"
        path = common_path + sub + animated + shiny + female
        filename = str(pokemon.dexnr) + pokemon.form + filetype
        return path + filename

    # --- HTTP-Handler ---

    async def _handle_team_page(self, request: web.Request) -> web.Response:
        player_id = int(request.match_info['player_id'])
        layout = request.query.get('layout', 'horizontal')
        if layout not in ('horizontal', 'vertical', '2x3', '3x2'):
            layout = 'horizontal'
        html = self._render_team_html(player_id, layout)
        return web.Response(text=html, content_type='text/html')

    async def _handle_badges_page(self, request: web.Request) -> web.Response:
        player_id = int(request.match_info['player_id'])
        badge_layout = request.query.get('layout', 'horizontal')
        if badge_layout not in ('horizontal', 'vertical', '2x4', '4x2', '4x4'):
            badge_layout = 'horizontal'
        html = self._render_badges_html(player_id, badge_layout)
        return web.Response(text=html, content_type='text/html')

    async def _handle_team_badges_page(self, request: web.Request) -> web.Response:
        team_id = slug_team_id(request.match_info['team_id'])
        badge_layout = request.query.get('layout', 'horizontal')
        if badge_layout not in ('horizontal', 'vertical', '2x4', '4x2', '4x4'):
            badge_layout = 'horizontal'
        html = self._render_team_badges_html(team_id, badge_layout)
        return web.Response(text=html, content_type='text/html')

    async def _handle_team_badges_data(self, request: web.Request) -> web.Response:
        team_id = slug_team_id(request.match_info['team_id'])
        payload = self._build_team_badges_payload(team_id)
        return web.json_response(payload)

    async def _handle_state(self, request: web.Request) -> web.Response:
        player_id = int(request.match_info['player_id'])
        payload = self._build_full_payload(player_id)
        return web.json_response(payload)

    async def _handle_session_state(self, request: web.Request) -> web.Response:
        return web.json_response(self._build_session_snapshot())

    async def _handle_session_sse(self, request: web.Request) -> web.StreamResponse:
        response = web.StreamResponse(
            status=200, reason='OK',
            headers={
                'Content-Type': 'text/event-stream',
                'Cache-Control': 'no-cache',
                'Connection': 'keep-alive',
                'Access-Control-Allow-Origin': '*',
            },
        )
        await response.prepare(request)
        queue: asyncio.Queue = asyncio.Queue(maxsize=100)
        self._session_sse_queues.append(queue)
        self.logger.info("Session-SSE-Client verbunden")
        # Initial-Snapshot senden
        try:
            snap = self._build_session_snapshot()
            init = f"event: snapshot\ndata: {json.dumps(snap, ensure_ascii=False, default=str)}\n\n"
            await response.write(init.encode('utf-8'))
        except Exception as err:
            self.logger.warning(f"session SSE initial snapshot failed: {err}")
        try:
            while True:
                event = await queue.get()
                await response.write(event.encode('utf-8'))
        except (asyncio.CancelledError, ConnectionResetError):
            pass
        finally:
            if queue in self._session_sse_queues:
                self._session_sse_queues.remove(queue)
            self.logger.info("Session-SSE-Client getrennt")
        return response

    async def _handle_timer_page(self, request: web.Request) -> web.Response:
        html = self._render_timer_html()
        return web.Response(text=html, content_type='text/html')

    async def _handle_sse(self, request: web.Request) -> web.StreamResponse:
        player_id = int(request.match_info['player_id'])
        response = web.StreamResponse(
            status=200,
            reason='OK',
            headers={
                'Content-Type': 'text/event-stream',
                'Cache-Control': 'no-cache',
                'Connection': 'keep-alive',
                'Access-Control-Allow-Origin': '*',
            },
        )
        await response.prepare(request)

        queue: asyncio.Queue = asyncio.Queue(maxsize=50)
        if player_id not in self._sse_queues:
            self._sse_queues[player_id] = []
        self._sse_queues[player_id].append(queue)
        self.logger.info(f"SSE-Client verbunden für Spieler {player_id}")

        try:
            while True:
                event = await queue.get()
                await response.write(event.encode('utf-8'))
        except (asyncio.CancelledError, ConnectionResetError):
            pass
        finally:
            if player_id in self._sse_queues and queue in self._sse_queues[player_id]:
                self._sse_queues[player_id].remove(queue)
            self.logger.info(f"SSE-Client getrennt für Spieler {player_id}")

        return response

    async def _handle_team_sse(self, request: web.Request) -> web.StreamResponse:
        team_id = slug_team_id(request.match_info['team_id'])
        response = web.StreamResponse(
            status=200,
            reason='OK',
            headers={
                'Content-Type': 'text/event-stream',
                'Cache-Control': 'no-cache',
                'Connection': 'keep-alive',
                'Access-Control-Allow-Origin': '*',
            },
        )
        await response.prepare(request)

        queue: asyncio.Queue = asyncio.Queue(maxsize=50)
        self._team_sse_queues.setdefault(team_id, []).append(queue)
        self.logger.info(f"Team-SSE-Client verbunden für Team {team_id}")

        # Initial-Snapshot
        try:
            snap = self._build_team_badges_payload(team_id)
            init = f"event: snapshot\ndata: {json.dumps(snap, ensure_ascii=False, default=str)}\n\n"
            await response.write(init.encode('utf-8'))
        except Exception as err:
            self.logger.warning(f"team SSE initial snapshot failed: {err}")

        try:
            while True:
                event = await queue.get()
                await response.write(event.encode('utf-8'))
        except (asyncio.CancelledError, ConnectionResetError):
            pass
        finally:
            queues = self._team_sse_queues.get(team_id)
            if queues and queue in queues:
                queues.remove(queue)
            if queues is not None and not queues:
                self._team_sse_queues.pop(team_id, None)
            self.logger.info(f"Team-SSE-Client getrennt für Team {team_id}")

        return response

    async def _handle_sprite(self, request: web.Request) -> web.Response:
        player_id = int(request.match_info['player_id'])
        slot = int(request.match_info['slot'])
        team = self.munchlax.sorted_teams.get(player_id, [])
        edition = self.munchlax.editions.get(player_id, 0)
        if slot < 0 or slot >= len(team) or team[slot].dexnr == 0:
            return web.Response(status=404, text="Slot leer")
        file_path = self._resolve_sprite_path(team[slot], edition)
        return self._serve_resolved_file(file_path)

    async def _handle_item(self, request: web.Request) -> web.Response:
        player_id = int(request.match_info['player_id'])
        slot = int(request.match_info['slot'])
        team = self.munchlax.sorted_teams.get(player_id, [])
        edition = self.munchlax.editions.get(player_id, 0)
        if slot < 0 or slot >= len(team) or not team[slot].item:
            return web.Response(status=404, text="Kein Item")
        items_path = self.sp.get('items_path', '')
        if not items_path:
            return web.Response(status=404, text="Items-Pfad nicht konfiguriert")
        raw_item = team[slot].item
        item_slug = str(raw_item)
        item_slug = resolve_tm_hm_sprite(
            edition, item_slug,
            self.munchlax.rando_tm_moves,
            self.munchlax.rando_hm_moves,
        )
        file_path = str(Path(items_path) / f"{item_slug}.png")
        self.logger.debug(f"Item-Request: player={player_id}, slot={slot}, raw_item={raw_item!r}, slug={item_slug}, path={file_path}")
        return self._serve_resolved_file(file_path)

    async def _handle_badge_img(self, request: web.Request) -> web.Response:
        player_id = int(request.match_info['player_id'])
        index = int(request.match_info['index'])
        edition = self.munchlax.editions.get(player_id, 0)
        badges_val = self.munchlax.badges.get(player_id, 0)
        badges_path = self.sp.get('badges_path', '')
        if not badges_path:
            return web.Response(status=404, text="Badges-Pfad nicht konfiguriert")
        region = BADGE_REGION.get(edition, '')
        if not region:
            return web.Response(status=404, text="Unbekannte Edition")
        earned = bool(badges_val & (1 << index))
        suffix = '' if earned else 'empty'
        file_path = str(Path(badges_path) / f"{region}{index + 1}{suffix}.png")
        return self._serve_resolved_file(file_path)

    async def _handle_team_badge_img(self, request: web.Request) -> web.Response:
        team_id = slug_team_id(request.match_info['team_id'])
        region = request.match_info['region']
        try:
            index = int(request.match_info['index'])
        except (TypeError, ValueError):
            return web.Response(status=400, text="Index ungültig")
        badges_path = self.sp.get('badges_path', '')
        if not badges_path:
            return web.Response(status=404, text="Badges-Pfad nicht konfiguriert")
        bucket = self._get_team_bucket(team_id)
        if not bucket:
            return web.Response(status=404, text="Team nicht bekannt")
        view = self._compute_team_badge_view(bucket)
        row = next((r for r in view.get("rows", []) if r.get("region") == region), None)
        if not row:
            return web.Response(status=404, text="Region nicht im Team")
        badges_and = int(row.get("badges_and", 0))
        badge_count = int(row.get("badge_count", 8))
        if index < 0 or index >= badge_count:
            return web.Response(status=404, text="Index out of range")
        earned = bool(badges_and & (1 << index))
        suffix = '' if earned else 'empty'
        file_path = str(Path(badges_path) / f"{region}{index + 1}{suffix}.png")
        return self._serve_resolved_file(file_path)

    def _serve_resolved_file(self, file_path: str | None) -> web.Response:
        if not file_path:
            return web.Response(status=404, text="Pfad nicht aufgelöst")
        resolved = Path(file_path).resolve()
        if not resolved.is_file():
            self.logger.warning(f"Datei nicht gefunden: {resolved}")
            return web.Response(status=404, text="Datei nicht gefunden")
        content_type, _ = mimetypes.guess_type(str(resolved))
        if not content_type:
            content_type = 'application/octet-stream'
        return web.FileResponse(resolved, headers={
            'Content-Type': content_type,
            'Cache-Control': 'no-cache, no-store, must-revalidate',
        })

    # --- HTML-Renderer ---

    def _render_team_html(self, player_id: int, layout: str = 'horizontal') -> str:
        return f"""<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="UTF-8">
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{ background: transparent; font-family: 'Segoe UI', Arial, sans-serif; overflow: hidden; }}
#team {{ gap: 8px; padding: 4px; }}
#team.layout-horizontal {{ display: flex; align-items: flex-start; }}
#team.layout-vertical {{ display: flex; flex-direction: column; align-items: flex-start; }}
#team.layout-2x3 {{ display: grid; grid-template-columns: repeat(2, auto); justify-content: start; }}
#team.layout-3x2 {{ display: grid; grid-template-columns: repeat(3, auto); justify-content: start; }}
.slot {{ display: flex; flex-direction: column; align-items: center; min-width: 80px; }}
.slot.empty {{ visibility: hidden; }}
.sprite {{ width: 80px; height: 80px; object-fit: contain; image-rendering: pixelated; }}
.nickname {{ color: #fff; font-size: 12px; text-align: center; text-shadow: 1px 1px 2px #000; white-space: nowrap; }}
.level {{ color: #ffd700; font-size: 11px; text-shadow: 1px 1px 2px #000; }}
.item-icon {{ width: 24px; height: 24px; object-fit: contain; image-rendering: pixelated; }}
.hp-bar {{ width: 60px; height: 6px; background: #333; border-radius: 3px; overflow: hidden; margin-top: 2px; }}
.hp-fill {{ height: 100%; transition: width 0.3s; }}
.hp-green {{ background: #4caf50; }}
.hp-yellow {{ background: #ff9800; }}
.hp-red {{ background: #f44336; }}
.sprite.fainted {{ filter: grayscale(100%); }}
.sprite.status-freeze {{ filter: drop-shadow(0 0 5px #3068B0) drop-shadow(0 0 5px #3068B0); }}
.sprite.status-burn {{ filter: drop-shadow(0 0 5px #E85030) drop-shadow(0 0 5px #E85030); }}
.sprite.status-para {{ filter: drop-shadow(0 0 5px #FAD300) drop-shadow(0 0 5px #FAD300); }}
.sprite.status-poison {{ filter: drop-shadow(0 0 5px #A040A0) drop-shadow(0 0 5px #A040A0); }}
.sprite.status-sleep {{ filter: drop-shadow(0 0 5px #98D8D8) drop-shadow(0 0 5px #98D8D8); }}
.sprite.fainted.status-freeze {{ filter: grayscale(100%) drop-shadow(0 0 5px #3068B0) drop-shadow(0 0 5px #3068B0); }}
.sprite.fainted.status-burn {{ filter: grayscale(100%) drop-shadow(0 0 5px #E85030) drop-shadow(0 0 5px #E85030); }}
.sprite.fainted.status-para {{ filter: grayscale(100%) drop-shadow(0 0 5px #FAD300) drop-shadow(0 0 5px #FAD300); }}
.sprite.fainted.status-poison {{ filter: grayscale(100%) drop-shadow(0 0 5px #A040A0) drop-shadow(0 0 5px #A040A0); }}
.sprite.fainted.status-sleep {{ filter: grayscale(100%) drop-shadow(0 0 5px #98D8D8) drop-shadow(0 0 5px #98D8D8); }}
</style>
</head>
<body>
<div id="team"></div>
<script>
const PLAYER_ID = {player_id};
const QUERY_LAYOUT = new URLSearchParams(window.location.search).get('layout');
let prevData = null;

function hpColor(cur, max) {{
    const pct = max > 0 ? cur / max : 0;
    if (pct > 0.5) return 'hp-green';
    if (pct > 0.2) return 'hp-yellow';
    return 'hp-red';
}}

function statusGlowClass(status) {{
    if (!status) return null;
    if (status.freeze) return 'status-freeze';
    if (status.burn)   return 'status-burn';
    if (status.para)   return 'status-para';
    if (status.toxic || status.poison) return 'status-poison';
    if (status.sleep)  return 'status-sleep';
    return null;
}}

function createSlotNode(p, data) {{
    const div = document.createElement('div');
    div.className = 'slot' + (p.dexnr === 0 ? ' empty' : '');
    div.dataset.identityKey = p.identity_key || ('empty_' + p.slot);
    if (p.dexnr === 0) return div;

    const img = document.createElement('img');
    img.className = 'sprite';
    if (p.cur_hp === 0 && p.max_hp > 0) img.classList.add('fainted');
    if (data.show_status_effects) {{
        const sc = statusGlowClass(p.status);
        if (sc) img.classList.add(sc);
    }}
    img.src = p.sprite_url;
    img.alt = p.nickname || '';
    div.appendChild(img);

    if (data.show_nicknames && p.nickname) {{
        const name = document.createElement('div');
        name.className = 'nickname';
        name.textContent = p.nickname;
        div.appendChild(name);
    }}

    const lvl = document.createElement('div');
    lvl.className = 'level';
    lvl.textContent = p.lvl != null ? 'Lv.' + p.lvl : '';
    div.appendChild(lvl);

    if (data.show_hp_bars && p.max_hp > 0) {{
        const bar = document.createElement('div');
        bar.className = 'hp-bar';
        const fill = document.createElement('div');
        const pct = Math.round((p.cur_hp / p.max_hp) * 100);
        fill.className = 'hp-fill ' + hpColor(p.cur_hp, p.max_hp);
        fill.style.width = pct + '%';
        bar.appendChild(fill);
        div.appendChild(bar);
    }}

    if (data.show_items && p.item_url) {{
        const item = document.createElement('img');
        item.className = 'item-icon';
        item.src = p.item_url;
        item.alt = p.item || '';
        div.appendChild(item);
    }}

    return div;
}}

function updateSlotContent(div, p, data) {{
    div.className = 'slot' + (p.dexnr === 0 ? ' empty' : '');
    div.dataset.identityKey = p.identity_key || ('empty_' + p.slot);
    if (p.dexnr === 0) {{
        div.innerHTML = '';
        return;
    }}

    let img = div.querySelector('.sprite');
    if (!img) {{
        div.innerHTML = '';
        const newNode = createSlotNode(p, data);
        div.replaceWith(newNode);
        return;
    }}

    img.className = 'sprite';
    if (p.cur_hp === 0 && p.max_hp > 0) img.classList.add('fainted');
    if (data.show_status_effects) {{
        const sc = statusGlowClass(p.status);
        if (sc) img.classList.add(sc);
    }}
    if (img.src !== new URL(p.sprite_url, location.origin).href) {{
        img.src = p.sprite_url;
    }}
    img.alt = p.nickname || '';

    let nameEl = div.querySelector('.nickname');
    if (data.show_nicknames && p.nickname) {{
        if (!nameEl) {{
            nameEl = document.createElement('div');
            nameEl.className = 'nickname';
            img.after(nameEl);
        }}
        nameEl.textContent = p.nickname;
    }} else if (nameEl) {{
        nameEl.remove();
    }}

    let lvlEl = div.querySelector('.level');
    if (lvlEl) {{
        lvlEl.textContent = p.lvl != null ? 'Lv.' + p.lvl : '';
    }}

    let hpBar = div.querySelector('.hp-bar');
    if (data.show_hp_bars && p.max_hp > 0) {{
        if (!hpBar) {{
            hpBar = document.createElement('div');
            hpBar.className = 'hp-bar';
            const fill = document.createElement('div');
            hpBar.appendChild(fill);
            div.appendChild(hpBar);
        }}
        const fill = hpBar.querySelector('.hp-fill') || hpBar.firstChild;
        const pct = Math.round((p.cur_hp / p.max_hp) * 100);
        fill.className = 'hp-fill ' + hpColor(p.cur_hp, p.max_hp);
        fill.style.width = pct + '%';
    }} else if (hpBar) {{
        hpBar.remove();
    }}

    let itemEl = div.querySelector('.item-icon');
    if (data.show_items && p.item_url) {{
        if (!itemEl) {{
            itemEl = document.createElement('img');
            itemEl.className = 'item-icon';
            div.appendChild(itemEl);
        }}
        if (itemEl.src !== new URL(p.item_url, location.origin).href) {{
            itemEl.src = p.item_url;
        }}
        itemEl.alt = p.item || '';
    }} else if (itemEl) {{
        itemEl.remove();
    }}
}}

function renderTeam(data) {{
    const container = document.getElementById('team');
    container.className = 'layout-' + (QUERY_LAYOUT || data.team_layout || 'horizontal');
    const team = data.team || [];
    const animate = data.animate_reorder && prevData !== null;
    const duration = data.animation_duration_ms || 300;

    const oldRects = new Map();
    if (animate) {{
        for (const child of container.children) {{
            if (child.dataset.identityKey) {{
                oldRects.set(child.dataset.identityKey, child.getBoundingClientRect());
            }}
        }}
    }}

    const existingNodes = new Map();
    for (const child of Array.from(container.children)) {{
        existingNodes.set(child.dataset.identityKey, child);
    }}

    const usedKeys = new Set();
    for (const p of team) {{
        const key = p.identity_key || ('empty_' + p.slot);
        usedKeys.add(key);
        let div = existingNodes.get(key);
        if (div) {{
            updateSlotContent(div, p, data);
        }} else {{
            div = createSlotNode(p, data);
        }}
        container.appendChild(div);
    }}

    for (const [key, node] of existingNodes) {{
        if (!usedKeys.has(key)) {{
            if (animate) {{
                const anim = node.animate([{{opacity: 1}}, {{opacity: 0}}], {{duration, fill: 'forwards'}});
                anim.onfinish = () => node.remove();
            }} else {{
                node.remove();
            }}
        }}
    }}

    if (animate) {{
        for (const child of container.children) {{
            const key = child.dataset.identityKey;
            const oldRect = oldRects.get(key);
            if (!oldRect) {{
                child.animate([
                    {{opacity: 0, transform: 'scale(0.8)'}},
                    {{opacity: 1, transform: 'scale(1)'}}
                ], {{duration, easing: 'ease-out'}});
                continue;
            }}
            const newRect = child.getBoundingClientRect();
            const dx = oldRect.left - newRect.left;
            const dy = oldRect.top - newRect.top;
            if (Math.abs(dx) < 1 && Math.abs(dy) < 1) continue;
            child.animate([
                {{transform: 'translate(' + dx + 'px, ' + dy + 'px)'}},
                {{transform: 'translate(0, 0)'}}
            ], {{duration, easing: 'ease-in-out'}});
        }}
    }}

    prevData = data;
}}

fetch('/player/' + PLAYER_ID + '/state')
    .then(r => r.json())
    .then(data => renderTeam(data));

const es = new EventSource('/player/' + PLAYER_ID + '/events');
es.addEventListener('team', function(e) {{
    renderTeam(JSON.parse(e.data));
}});
es.addEventListener('badges', function(e) {{
    // Team-Overlay ignoriert Badge-Events
}});
</script>
</body>
</html>"""

    def _render_timer_html(self) -> str:
        return """<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="UTF-8">
<style>
* { margin: 0; padding: 0; box-sizing: border-box; }
body { background: transparent; font-family: 'Segoe UI', Arial, sans-serif; color: #fff; overflow: hidden; }
#wrap { padding: 12px; display: flex; flex-direction: column; gap: 8px; }
.row { display: flex; align-items: baseline; gap: 10px; text-shadow: 2px 2px 4px #000, 0 0 8px #000; }
.label { font-size: 16px; opacity: 0.8; }
.value { font-family: 'Consolas', 'Courier New', monospace; font-weight: bold; }
.timer { font-size: 42px; }
.countdown { font-size: 32px; }
.countdown.finished { color: #4caf50; }
.countdown.paused { color: #ff9800; }
.timer.paused { color: #ff9800; }
.hidden { display: none; }
.splits { font-size: 14px; color: #ffd700; }
.violations { font-size: 13px; margin-top: 6px; }
.viol { padding: 3px 6px; border-radius: 3px; margin-bottom: 3px; background: rgba(180, 40, 40, 0.85); text-shadow: 1px 1px 2px #000; }
.viol.total_wipe { background: rgba(200, 30, 30, 0.95); font-weight: bold; }
.viol.first_type_clash { background: rgba(180, 100, 30, 0.85); }
.viol.trade { background: rgba(120, 60, 180, 0.85); }
</style>
</head>
<body>
<div id="wrap">
  <div class="row"><span class="label">Race</span><span id="timer" class="value timer">00:00:00</span></div>
  <div id="cd-row" class="row hidden"><span class="label" id="cd-label">Countdown</span><span id="countdown" class="value countdown">--:--:--</span></div>
  <div id="splits" class="splits"></div>
  <div id="violations" class="violations"></div>
</div>
<script>
let state = { timer_state: {}, countdown_state: {} };
let timerAnchor = null;   // {elapsed, running, local_ts}
let countdownAnchor = null;

function fmt(sec) {
    sec = Math.max(0, Math.floor(sec));
    const h = String(Math.floor(sec / 3600)).padStart(2, '0');
    const m = String(Math.floor(sec / 60) % 60).padStart(2, '0');
    const s = String(sec % 60).padStart(2, '0');
    return h + ':' + m + ':' + s;
}

function setTimerAnchor(elapsed, running) {
    timerAnchor = { elapsed: elapsed || 0, running: !!running, local_ts: Date.now() / 1000 };
}

function setCountdownAnchor(remaining, running) {
    countdownAnchor = { remaining: remaining || 0, running: !!running, local_ts: Date.now() / 1000 };
}

function tick() {
    const timerEl = document.getElementById('timer');
    if (timerAnchor) {
        let elapsed = timerAnchor.elapsed;
        if (timerAnchor.running) elapsed += (Date.now() / 1000) - timerAnchor.local_ts;
        timerEl.textContent = fmt(elapsed);
        timerEl.className = 'value timer' + (timerAnchor.running ? '' : ' paused');
    }
    const cdEl = document.getElementById('countdown');
    const cdRow = document.getElementById('cd-row');
    const cs = state.countdown_state || {};
    if (cs.duration_seconds > 0) {
        cdRow.classList.remove('hidden');
        let remaining = countdownAnchor ? countdownAnchor.remaining : (cs.remaining_seconds || 0);
        if (countdownAnchor && countdownAnchor.running) {
            remaining = Math.max(0, remaining - ((Date.now() / 1000) - countdownAnchor.local_ts));
        }
        cdEl.textContent = fmt(remaining);
        cdEl.className = 'value countdown' + (cs.finished ? ' finished' : (cs.running ? '' : ' paused'));
        const lbl = cs.label || 'Countdown';
        document.getElementById('cd-label').textContent = lbl;
    } else {
        cdRow.classList.add('hidden');
    }
    requestAnimationFrame(tick);
}

function renderSplits() {
    const el = document.getElementById('splits');
    const ts = state.timer_state || {};
    const splits = ts.splits || [];
    el.innerHTML = splits.slice(-6).map(s => fmt(s.elapsed) + '  ' + (s.label || '')).join('<br>');
}

function renderViolations() {
    const el = document.getElementById('violations');
    const violations = state.soullink_rule_violations || [];
    if (!violations.length) { el.innerHTML = ''; return; }
    el.innerHTML = violations.slice(-5).map(v => {
        const cls = 'viol ' + (v.type || 'unknown');
        return '<div class="' + cls + '">' + (v.message || v.type || '?') + '</div>';
    }).join('');
}

function applySnapshot(snap) {
    state = snap || {};
    const ts = state.timer_state || {};
    setTimerAnchor(ts.elapsed || 0, ts.running);
    const cs = state.countdown_state || {};
    setCountdownAnchor(cs.remaining_seconds || 0, cs.running);
    renderSplits();
    renderViolations();
}

fetch('/session/state').then(r => r.json()).then(applySnapshot);

const es = new EventSource('/session/events');
es.addEventListener('snapshot', function(e) { applySnapshot(JSON.parse(e.data)); });
es.addEventListener('timer_state', function(e) {
    const t = JSON.parse(e.data);
    state.timer_state = t;
    setTimerAnchor(t.elapsed || 0, t.running);
    renderSplits();
});
es.addEventListener('timer_tick', function(e) {
    const t = JSON.parse(e.data);
    setTimerAnchor(t.elapsed || 0, t.running);
});
es.addEventListener('countdown_state', function(e) {
    const c = JSON.parse(e.data);
    state.countdown_state = c;
    setCountdownAnchor(c.remaining_seconds || 0, c.running);
});
es.addEventListener('countdown_tick', function(e) {
    const c = JSON.parse(e.data);
    setCountdownAnchor(c.remaining_seconds || 0, c.running);
});
es.addEventListener('countdown_finished', function(e) {
    state.countdown_state = Object.assign({}, state.countdown_state, {finished: true, running: false});
});
es.addEventListener('soullink_rule_violation', function(e) {
    const v = JSON.parse(e.data);
    state.soullink_rule_violations = (state.soullink_rule_violations || []).concat([v]);
    renderViolations();
});
tick();
</script>
</body>
</html>"""

    def _render_badges_html(self, player_id: int, badge_layout: str = 'horizontal') -> str:
        return f"""<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="UTF-8">
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{ background: transparent; font-family: 'Segoe UI', Arial, sans-serif; overflow: hidden; }}
#badges {{ gap: 4px; padding: 4px; }}
#badges.layout-horizontal {{ display: flex; align-items: center; flex-wrap: wrap; }}
#badges.layout-vertical {{ display: flex; flex-direction: column; align-items: center; }}
#badges.layout-2x4 {{ display: grid; grid-template-columns: repeat(2, auto); justify-items: center; }}
#badges.layout-4x2 {{ display: grid; grid-template-columns: repeat(4, auto); justify-items: center; }}
/* 4x4 == visuell identisch mit 4x2 (repeat(4,auto) legt Zeilen aus Item-Anzahl fest).
   Als eigener Layout-Name gefuehrt, damit Streamer explizit den Johto-16-Orden-Case
   wählen können statt sich auf implizites Row-Overflow bei 4x2 zu verlassen. */
#badges.layout-4x4 {{ display: grid; grid-template-columns: repeat(4, auto); justify-items: center; }}
.badge {{ width: 40px; height: 40px; object-fit: contain; image-rendering: pixelated; }}
</style>
</head>
<body>
<div id="badges"></div>
<script>
const PLAYER_ID = {player_id};
const QUERY_LAYOUT = new URLSearchParams(window.location.search).get('layout');

function renderBadges(data) {{
    const container = document.getElementById('badges');
    container.innerHTML = '';
    container.className = 'layout-' + (QUERY_LAYOUT || data.badge_layout || 'horizontal');
    if (!data.show_badges) return;
    const badges = data.badges || [];
    for (const b of badges) {{
        const img = document.createElement('img');
        img.className = 'badge' + (b.earned ? '' : ' not-earned');
        img.src = b.url;
        container.appendChild(img);
    }}
}}

fetch('/player/' + PLAYER_ID + '/state')
    .then(r => r.json())
    .then(data => renderBadges(data));

const es = new EventSource('/player/' + PLAYER_ID + '/events');
es.addEventListener('badges', function(e) {{
    renderBadges(JSON.parse(e.data));
}});
es.addEventListener('team', function(e) {{
    // Badge-Overlay ignoriert Team-Events
}});
</script>
</body>
</html>"""

    def _render_team_badges_html(self, team_id: str, badge_layout: str = 'horizontal') -> str:
        # Query-Layout gewinnt zur Laufzeit, badge_layout aus URL nur als Default-Fallback.
        team_id_js = json.dumps(team_id)
        return f"""<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="UTF-8">
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{ background: transparent; font-family: 'Segoe UI', Arial, sans-serif; overflow: hidden; }}
#rows {{ display: flex; flex-direction: column; gap: 6px; padding: 4px; }}
.badges-row {{ gap: 4px; }}
.badges-row.layout-horizontal {{ display: flex; align-items: center; flex-wrap: wrap; }}
.badges-row.layout-vertical {{ display: flex; flex-direction: column; align-items: center; }}
.badges-row.layout-2x4 {{ display: grid; grid-template-columns: repeat(2, auto); justify-items: center; }}
.badges-row.layout-4x2 {{ display: grid; grid-template-columns: repeat(4, auto); justify-items: center; }}
/* 4x4 visuell identisch mit 4x2 (siehe Player-Renderer). Eigener Layout-Name als
   Johto-Alias, damit Streamer explizit den 16-Orden-Fall wählen können. */
.badges-row.layout-4x4 {{ display: grid; grid-template-columns: repeat(4, auto); justify-items: center; }}
.badge {{ width: 40px; height: 40px; object-fit: contain; image-rendering: pixelated; }}
</style>
</head>
<body>
<div id="rows"></div>
<script>
const TEAM_ID = {team_id_js};
const QUERY_LAYOUT = new URLSearchParams(window.location.search).get('layout');

function renderTeamBadges(data) {{
    const container = document.getElementById('rows');
    container.innerHTML = '';
    if (!data.show_badges) return;
    const layout = QUERY_LAYOUT || data.badge_layout || 'horizontal';
    const rows = data.rows || [];
    for (const row of rows) {{
        const rowEl = document.createElement('div');
        rowEl.className = 'badges-row layout-' + layout;
        rowEl.dataset.region = row.region;
        for (const b of (row.badges || [])) {{
            const img = document.createElement('img');
            img.className = 'badge' + (b.earned ? '' : ' not-earned');
            img.src = b.url;
            rowEl.appendChild(img);
        }}
        container.appendChild(rowEl);
    }}
}}

fetch('/team/' + encodeURIComponent(TEAM_ID) + '/badges/data')
    .then(r => r.json())
    .then(data => renderTeamBadges(data));

const es = new EventSource('/team/' + encodeURIComponent(TEAM_ID) + '/badges/events');
es.addEventListener('snapshot', function(e) {{
    renderTeamBadges(JSON.parse(e.data));
}});
es.addEventListener('badges', function(e) {{
    renderTeamBadges(JSON.parse(e.data));
}});
</script>
</body>
</html>"""
