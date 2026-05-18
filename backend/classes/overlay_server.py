import asyncio
import json
import mimetypes
import traceback
from pathlib import Path

from aiohttp import web

from backend.logging_setup import get_logger
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

    def _setup_routes(self):
        self.app.router.add_get('/player/{player_id}', self._handle_team_page)
        self.app.router.add_get('/player/{player_id}/badges', self._handle_badges_page)
        self.app.router.add_get('/player/{player_id}/events', self._handle_sse)
        self.app.router.add_get('/player/{player_id}/state', self._handle_state)
        self.app.router.add_get('/sprite/{player_id}/{slot}', self._handle_sprite)
        self.app.router.add_get('/item/{player_id}/{slot}', self._handle_item)
        self.app.router.add_get('/badge_img/{player_id}/{index}', self._handle_badge_img)

    # --- Notify (aufgerufen von Munchlax) ---

    async def notify_update(self, player_id: int, update_type: str):
        queues = self._sse_queues.get(player_id, [])
        if not queues:
            return
        payload = self._build_full_payload(player_id)
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

    # --- Payload-Builder ---

    def _build_full_payload(self, player_id: int) -> dict:
        team = self.munchlax.sorted_teams.get(player_id, [])
        edition = self.munchlax.editions.get(player_id, 0)
        badges_val = self.munchlax.badges.get(player_id, 0)
        player_name = self.munchlax.player_names.get(player_id, f"Spieler {player_id}")

        team_data = []
        for slot, pkmn in enumerate(team[:6]):
            if pkmn.dexnr == 0:
                team_data.append({"slot": slot, "dexnr": 0})
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
            }
            status = getattr(pkmn, 'status', {})
            if status:
                entry["status"] = status
            if pkmn.item and edition > 20:
                entry["item_url"] = f"/item/{player_id}/{slot}?v={pkmn.item}"
            team_data.append(entry)

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
        }

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
        if badge_layout not in ('horizontal', 'vertical', '2x4', '4x2'):
            badge_layout = 'horizontal'
        html = self._render_badges_html(player_id, badge_layout)
        return web.Response(text=html, content_type='text/html')

    async def _handle_state(self, request: web.Request) -> web.Response:
        player_id = int(request.match_info['player_id'])
        payload = self._build_full_payload(player_id)
        return web.json_response(payload)

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

function renderTeam(data) {{
    const container = document.getElementById('team');
    container.innerHTML = '';
    container.className = 'layout-' + (QUERY_LAYOUT || data.team_layout || 'horizontal');
    const team = data.team || [];
    for (const p of team) {{
        const div = document.createElement('div');
        div.className = 'slot' + (p.dexnr === 0 ? ' empty' : '');
        if (p.dexnr === 0) {{ container.appendChild(div); continue; }}

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

        container.appendChild(div);
    }}
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
.badge {{ width: 40px; height: 40px; object-fit: contain; image-rendering: pixelated; }}
.badge.not-earned {{ opacity: 0.4; }}
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
