import asyncio
import traceback

import aiohttp

from backend.classes.overlay_server import BADGE_REGION
from backend.logging_setup import get_logger


class TwitchExtensionClient:
    """Push-Client für den Extension Backend Service (EBS) unter
    ``twitch_extension/ebs``. Wird beim Verbinden vom Munchlax-Kontext
    aufgesetzt und aus denselben Stellen wie ``OverlayServer.notify_update``
    getriggert — parallel dazu, nicht ersetzend. Fehlgeschlagene Pushes werden
    geloggt und geschluckt, damit ein EBS-Ausfall den Tracker nicht stört.
    """

    HTTP_TIMEOUT = aiohttp.ClientTimeout(total=3.0, connect=1.0)

    def __init__(self, munchlax, twitch_ext_config: dict):
        self.munchlax = munchlax
        self.cfg = twitch_ext_config
        self.enabled: bool = bool(self.cfg.get('enabled', False))
        self.ebs_url: str = (self.cfg.get('ebs_url', '') or '').rstrip('/')
        self.secret: str = self.cfg.get('ingest_secret', '') or ''
        self.channel_id: str = str(self.cfg.get('channel_id', '') or '')
        self.is_connected: bool | str = False
        self._session: aiohttp.ClientSession | None = None
        self._health_task: asyncio.Task | None = None

        self.logger = get_logger(__name__, './logs/twitch_ext.log')

    # --- Lifecycle ---

    async def start(self):
        if not self.enabled:
            self.logger.info("Twitch-Extension-Push deaktiviert (enabled=False)")
            return
        if not self.ebs_url or not self.secret or not self.channel_id:
            self.logger.warning(
                "Twitch-Extension-Push: unvollständige Config "
                f"(ebs_url={'set' if self.ebs_url else 'MISSING'}, "
                f"secret={'set' if self.secret else 'MISSING'}, "
                f"channel_id={'set' if self.channel_id else 'MISSING'})"
            )
            return
        try:
            self._session = aiohttp.ClientSession(timeout=self.HTTP_TIMEOUT)
            self._health_task = asyncio.create_task(self._health_loop())
            self.logger.info(f"Twitch-Extension-Push gestartet, EBS={self.ebs_url}, channel={self.channel_id}")
        except Exception as err:
            self.logger.error(f"Twitch-Extension-Push Start fehlgeschlagen: {type(err)}, {err}")
            self.logger.error(traceback.format_exc())
            self.is_connected = False

    async def stop(self):
        self.is_connected = False
        if self._health_task:
            self._health_task.cancel()
            try:
                await self._health_task
            except (asyncio.CancelledError, Exception):
                pass
            self._health_task = None
        if self._session:
            try:
                await self._session.close()
            except Exception as err:
                self.logger.warning(f"aiohttp-Session-Close: {err}")
            self._session = None
        self.logger.info("Twitch-Extension-Push gestoppt.")

    async def _health_loop(self):
        """Pollt regelmäßig /health, damit der Status im UI ehrlich ist."""
        assert self._session is not None
        while True:
            try:
                async with self._session.get(f"{self.ebs_url}/health") as resp:
                    self.is_connected = 'connected' if resp.status == 200 else False
            except asyncio.CancelledError:
                raise
            except Exception:
                self.is_connected = False
            await asyncio.sleep(30)

    # --- Public Push-API — von Munchlax als asyncio.create_task(...) aufgerufen ---

    async def push_team(self, player_id: int):
        payload = self._build_team_payload(player_id)
        if payload is None:
            return
        await self._post(f"/ingest/session/{self.channel_id}/team", payload)

    async def push_bag(self, player_id: int, pockets: dict):
        payload = {"player_id": int(player_id), "pockets": pockets}
        await self._post(f"/ingest/session/{self.channel_id}/bag", payload)

    async def push_pokedex(self, player_id: int, generations: dict):
        payload = {"player_id": int(player_id), "generations": generations}
        await self._post(f"/ingest/session/{self.channel_id}/pokedex", payload)

    async def push_player_switch(self, active_player_id: int):
        payload = {"active_player_id": int(active_player_id)}
        await self._post(f"/ingest/session/{self.channel_id}/player-switch", payload)

    async def push_session_end(self, reason: str = ""):
        payload = {"reason": reason}
        await self._post(f"/ingest/session/{self.channel_id}/session-end", payload)

    # --- Payload-Aufbau ---

    def _build_team_payload(self, player_id: int) -> dict | None:
        m = self.munchlax
        team = m.sorted_teams.get(player_id)
        if team is None:
            return None
        edition = m.editions.get(player_id, 0)
        badges_val = m.badges.get(player_id, 0)
        player_name = m.player_names.get(player_id, f"Spieler {player_id}")
        region = BADGE_REGION.get(edition, '')
        badge_count = 16 if region == 'johto' else 8

        slots = []
        for slot, pkmn in enumerate(team[:6]):
            slots.append(self._slot_dict(slot, pkmn))

        badges = [
            {"index": i, "earned": bool(badges_val & (1 << i))}
            for i in range(badge_count)
        ]

        return {
            "player_id": int(player_id),
            "player_name": player_name,
            "edition": int(edition),
            "badge_region": region,
            "badges_raw": int(badges_val),
            "badges": badges,
            "team": slots,
        }

    @staticmethod
    def _slot_dict(slot: int, pkmn) -> dict:
        # Absichtlich schmaler als OverlayServer._build_full_payload: keine
        # sprite_url/item_url — die Extension löst Sprites clientseitig aus
        # dem Bundle auf, um das 5-KB-PubSub-Limit realistisch zu halten.
        if getattr(pkmn, "dexnr", 0) == 0:
            return {"slot": slot, "dexnr": 0, "identity_key": f"empty_{slot}"}
        entry = {
            "slot": slot,
            "dexnr": int(pkmn.dexnr),
            "identity_key": getattr(pkmn, "identity_key", None) or f"empty_{slot}",
        }
        if getattr(pkmn, "nickname", None):
            entry["nickname"] = pkmn.nickname
        if getattr(pkmn, "lvl", None) is not None:
            entry["lvl"] = int(pkmn.lvl)
        if getattr(pkmn, "shiny", False):
            entry["shiny"] = True
        if getattr(pkmn, "female", False):
            entry["female"] = True
        if getattr(pkmn, "form", ""):
            entry["form"] = pkmn.form
        item = getattr(pkmn, "item", "")
        if item:
            entry["item"] = str(item)
        cur_hp = getattr(pkmn, "cur_hp", None)
        max_hp = getattr(pkmn, "max_hp", None)
        if cur_hp is not None:
            entry["cur_hp"] = int(cur_hp)
        if max_hp is not None:
            entry["max_hp"] = int(max_hp)
        status = getattr(pkmn, "status", None)
        if status:
            # Nur die tatsächlich True-Felder mitgeben, spart Bytes im Limit.
            slim = {k: True for k, v in status.items() if v}
            if slim:
                entry["status"] = slim
        personality = getattr(pkmn, "personality", None)
        if personality is not None:
            entry["personality"] = int(personality)
        return entry

    # --- HTTP ---

    async def _post(self, path: str, payload: dict):
        if not self.enabled or self._session is None:
            return
        url = f"{self.ebs_url}{path}"
        headers = {"Authorization": f"Bearer {self.secret}"}
        try:
            async with self._session.post(url, json=payload, headers=headers) as resp:
                if resp.status >= 400:
                    text = await resp.text()
                    self.logger.warning(f"EBS-Push {path} → HTTP {resp.status}: {text[:200]}")
                else:
                    self.logger.debug(f"EBS-Push {path} → HTTP {resp.status}")
        except asyncio.CancelledError:
            raise
        except Exception as err:
            # Absichtlich schlucken — der Tracker darf durch einen EBS-Ausfall
            # niemals hängen. Nur loggen und weiter.
            self.logger.warning(f"EBS-Push {path} fehlgeschlagen: {type(err).__name__}: {err}")
