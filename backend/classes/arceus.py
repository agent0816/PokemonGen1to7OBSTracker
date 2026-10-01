import asyncio
import pickle
import time
import traceback
from backend.classes.pokedex_db import PokedexDB
from backend.logging_setup import get_logger
from backend.team_id import slug_team_id
from backend.type_lookup import first_type, type_name

# Netzwerk-Exceptions, bei denen weitere Reads/Writes keinen Sinn mehr
# ergeben (Socket ist tot). Pendant zu munchlax.TRANSIENT_NET_EXCEPTIONS.
# Server-Seite hatte vorher nur ConnectionResetError explizit, alles andere
# fiel in generische Exception → voller Traceback bei jedem Client-Dropout.
TRANSIENT_NET_EXCEPTIONS = (
    asyncio.IncompleteReadError,
    ConnectionResetError,
    ConnectionAbortedError,
    BrokenPipeError,
    EOFError,
    OSError,
)

# Rate-limited Logging-Fenster — identisch zu munchlax, damit sich Logs
# zwischen Client und Server bei nachtraeglicher Analyse parallel lesen lassen.
LOG_DEDUP_WINDOW_SECONDS = 10.0

# Phase C: hoechste gesehene sequence_id pro Client ueberlebt disconnect_client.
# Wird NUR durch session_reset (oder Server-Neustart) geleert. Beim Reconnect
# haengt der Server diesen Wert an einen "resume_ack"-Push an, damit der Client
# nur die wirklich fehlenden Payloads re-sendet.
# Container: dict[client_id, int], Default 0.

# Periodischer Status-Dump der verbundenen Clients. Jede CONNECTED_OVERVIEW_
# SECONDS gibt der Server einen INFO-Log mit allen aktiven Clients +
# Session-Statistiken aus — macht nachtraegliche Log-Reviews deutlich schneller
# ("war Client X zu Zeitpunkt Y aktiv?").
CONNECTED_OVERVIEW_SECONDS = 300.0

# Modi mit aktiver Soullink-Logik (Links, Ersttyp-Clause, Trade-Detection).
# Alles andere ("off" legacy, "nuzlocke", "disabled", "versus_ffa") bedeutet:
# keine Cross-Owner-Checks auf dem Server.
SOULLINK_MODES = ("coop", "versus")

# Replay-TTL fuer transient (ereignishafte) soullink_rule_violations beim
# Client-Reconnect. 5 min decken Soft-Reconnects ab (Heartbeat-Latenz ~35s
# + Reconnect-Backoff + Buffer). Nur Typen in TRANSIENT_VIOLATION_TYPES
# unterliegen dem TTL — alle anderen Violations (z.B. "trade") gelten als
# persistent und werden beim Reconnect immer replay'ed, weil sie den
# aktuellen Regel-Verstoss-Zustand repraesentieren.
# Transient = Snapshot-Event (total_wipe), nach Run-Ende obsolet.
VIOLATION_REPLAY_TTL_S = 300.0
TRANSIENT_VIOLATION_TYPES = frozenset({"total_wipe"})

# Heartbeat-Watchdog-Tuning. Client sendet alle 5 s einen Heartbeat.
# HEARTBEAT_STALE_SECONDS: ab wann ein einzelner ausbleibender Heartbeat
# als "verpasst" zaehlt (mehr als ein Send-Intervall Toleranz, damit
# WiFi-Jitter und kurze Executor-Blocks keine false-positives triggern).
# HEARTBEAT_MISS_LIMIT: wie viele solcher verpassten Ticks in Folge, bevor
# der Client tatsaechlich als tot gilt (Disconnect bei count > LIMIT).
# Effektive Disconnect-Latenz: STALE + (MISS_LIMIT + 1) * 5 s Tick,
# abhaengig von der Phasenlage zwischen Send- und Check-Loop. Aktuell
# also ca. 30-35 s. Verdoppelt gegenueber der urspruenglichen
# ~20-25 s (log-detektiv 2026-09-16: instabile Client-Netze wurden zu
# schnell rausgeworfen).
HEARTBEAT_STALE_SECONDS = 15.0
HEARTBEAT_MISS_LIMIT = 3
# Modi mit Versus-Scoreboard (badges/alive/deaths). "versus" aggregiert pro Team,
# "versus_ffa" (jeder gegen jeden) pro Spieler — ohne Soullink-Links.
VERSUS_MODES = ("versus", "versus_ffa")
# Modi, deren Config beim Connect an neue Clients ausgeliefert wird.
BROADCAST_MODES = ("coop", "versus", "versus_ffa")

# Grace-Period für Owner im _team_owner_cache: nach Disconnect noch so lange
# als "known" führen, damit Heartbeat-Blips das Team-Overlay nicht kippen.
# Danach: Cache-Eintrag entfernen, Owner verschwindet aus dem Team-Bucket.
TEAM_OWNER_CACHE_GRACE_SECONDS = 300

class Arceus:
    def __init__(self, host, port, rem):
        super().__init__()
        self.host = host
        self.port = port
        self.munchlaxes = {}
        self.munchlax_names = {}
        self.munchlax_status = {}
        self.munchlax_heartbeats = {}
        self.heartbeat_counts = {}
        self.client_player_ids: dict[str, set[int]] = {}
        # Vom Client via `declared_player_ids` deklarierter Slot-Besitz
        # (nicht gleich `client_player_ids`, das von Team-Nachrichten mit
        # kleineren Teilmengen ueberschrieben wird, sobald nur ein
        # Emulator laeuft). Ownership-Check fuer rando_moves_sync nutzt
        # die Vereinigung beider Mengen, damit ein Push fuer einen
        # deklarierten, aber noch nicht gestarteten Slot nicht verworfen
        # wird (R2 KRITISCH sync 2026-10-01).
        self.declared_player_ids_by_client: dict[str, set[int]] = {}
        self.teams = {}
        # Box-Cache analog zu self.teams: dict[player_id, list[list[Pokemon|None]]].
        # Wird vom besitzenden Munchlax per "boxes_update" gefüllt und bei
        # neuen Verbindungen einmalig an den Client gepusht.
        self.boxes = {}
        # Encounter-Cache: (personality, owner) → enc_dict.
        # Wird bei encounter_sync gefüllt, bei encounter_outcome aktualisiert
        # und beim Connect eines neuen Clients einmalig ausgeliefert.
        self.encounters: dict[tuple[int, str], dict] = {}
        # Bag-Cache: (owner, edition) → pockets_dict.
        self.bags: dict[tuple[str, str], dict] = {}
        # Rando-TM/HM-Zuordnungen pro player_id.
        # {pid: {"tm_moves": {num: name}, "hm_moves": {num: name}}}.
        # Vom Client via rando_moves_sync nach jedem Randomize gefuellt;
        # beim Connect eines neuen Clients komplett ausgeliefert, damit
        # overlay_server/obs auf jedem Host die randomisierten Zuordnungen
        # ALLER Spieler kennen (sonst zeigt Browser-Source Vanilla-TM-Icons
        # fuer Remote-Spieler).
        self.rando_moves: dict[int, dict] = {}
        # Soullink-State (rein server-authoritativ, keine DB-Persistenz auf Server-Seite).
        # Config wird vom Host-Client per "soullink_config" gesetzt und beim Connect
        # neuer Clients einmalig ausgeliefert.
        # config: mode, player_count, link_strategy, team_membership, expected_owners
        self.soullink_config: dict = {
            "mode": "off",
            "player_count": 2,
            "link_strategy": "full_chain",
            "team_membership": {},
            "expected_owners": [],
        }
        # link_id → {link_id, route, edition_gen, link_index, state, members: {owner: {...}}}
        self.soullink_links: dict[int, dict] = {}
        self.soullink_next_id: int = 1
        # Death-Cache: (personality, owner) → death_dict, wird bei Reconnect gepusht.
        self.soullink_deaths: dict[tuple[int, str], dict] = {}
        # Rule-Violation-Cache: (rule_type, subject) → violation_dict, wird bei
        # Reconnect neuer Clients gepusht. Beispiele: type='trade', type='first_type_clash'.
        self.soullink_rule_violations: dict[tuple[str, str], dict] = {}
        # Broadcast-Dedupe-Timestamps: (rule_type, subject) → epoch. Verhindert
        # dass die selbe Violation innerhalb von 5s mehrfach broadcastet wird
        # (siehe broadcast_soullink_rule_violation). Init hier statt lazy in der
        # Broadcast-Methode fuer Konsistenz mit den uebrigen State-Dicts der
        # Klasse.
        self._last_violation_broadcast_ts: dict[tuple, float] = {}
        # Token-State pro Owner: {owner: {earned: int, used: int, active_route: int|None,
        #                                 active_edition: int|None}}
        # `active_route` = Route für die als nächstes ein Extra-Encounter erlaubt ist.
        self.soullink_tokens: dict[str, dict] = {}
        # Versus-State: aggregierte Team-Stats (badges_min, deaths, alive_count).
        self.soullink_versus_state: dict[str, dict] = {}
        # Team-State fürs Overlay (modus-unabhängig). Basiert auf
        # _effective_team_membership() + je Owner Rohdaten (badges/edition/pids),
        # das Overlay aggregiert selbst (AND, Region-Gruppierung).
        self.soullink_team_state: dict[str, dict] = {}
        # Owner-Cache last-seen: {owner: {"badges", "edition", "last_seen"}}.
        # Puffert kurzzeitige Disconnects/Heartbeat-Blips, damit die AND-Aggregation
        # im Overlay nicht sofort auf 0 fällt und alle Regionen als leer anzeigt.
        # Wird nur mit echten Werten aktualisiert, nie mit None überschrieben.
        # Prune nach TEAM_OWNER_CACHE_GRACE_SECONDS ohne Sichtung (siehe
        # _prune_team_owner_cache), damit Session-Wechsel/dauerhafter Disconnect
        # keine Ghost-Owner im Team-State hinterlassen.
        self._team_owner_cache: dict[str, dict] = {}
        # History manueller PvP-Runden zwischen Teams.
        self.soullink_versus_battles: list[dict] = []
        # Race-Timer (server-authoritativ). Ticks werden im Background-Task
        # broadcastet, State-Änderungen (start/pause/resume/split) zusätzlich
        # bei jedem Handler.
        self.timer_state: dict = {
            "running": False,
            "start_ts": None,          # Unix-Wall-Time des Timer-Starts
            "pause_start_ts": None,    # aktueller Pause-Beginn oder None
            "pause_total_seconds": 0.0,# summierte Pausen bis jetzt
            "splits": [],              # list[{label, elapsed, ts, source}]
            "pause_consent": [],       # list[str] Client-IDs, die pause bestätigt haben
            "resume_consent": [],      # list[str] Client-IDs, die resume bestätigt haben
        }
        # Zuletzt gesehene Badge-Werte pro (client_id, player_id) für Auto-Splits.
        self._timer_last_badges: dict[tuple[str, int], int] = {}
        self.timer_task = None
        # Countdown-Timer (unabhängig vom Race-Timer). Für z.B. YouTube-Aufnahme:
        # Zeit setzen, Server broadcastet countdown_finished wenn abgelaufen,
        # Clients spielen dann einen Ton oder zeigen ein Popup.
        self.countdown_state: dict = {
            "running": False,
            "start_ts": None,             # Unix-Zeit des Countdown-Starts (aktueller Lauf)
            "duration_seconds": 0.0,
            "remaining_at_pause": 0.0,    # verbleibende Sekunden zum Pausen-Zeitpunkt
            "label": "",
            "finished": False,
        }
        # Guard, damit ein einmal ausgelöstes countdown_finished nicht in jedem
        # tick-Zyklus erneut broadcastet wird.
        self._countdown_finished_signaled = False
        # Pro Client ein Lock, damit gleichzeitige Sender (update_all_clients +
        # broadcast_boxes_update) sich nicht in den chunked-Stream funken.
        self.writer_locks = {}
        self.server = None
        self.is_connected = False
        self.disconnect_lock = asyncio.Lock()
        self.rem = rem

        # Diagnose-State pro Client (Phase B). session_started_at, recv/sent-
        # Counter, letzter Message-Typ und Timestamp — alles pro client_id.
        # Wird in handle_munchlax beim Connect angelegt und von disconnect_client
        # beim Trennen als Statistik-Zeile ausgegeben.
        # _client_sessions: {client_id: {started_at, recv, sent, last_recv_at,
        #                                last_recv_type}}
        # _err_counts: Rate-Limit-State fuer _log_dedup, analog Munchlax.
        self._client_sessions: dict[str, dict] = {}
        self._err_counts: dict[str, dict] = {}
        # Periodische Overview geloggt in check_heartbeats-Loop (eigener Trigger
        # waere Overkill), hier nur der letzte Ausgabe-Zeitpunkt.
        self._last_overview_at: float = 0.0
        # Phase C: Replay-State. Hoechste gesehene sequence_id pro Client,
        # ueberlebt Disconnect — nur session_reset leert's. Wird in
        # handle_munchlax nach Namens-Parse an den Client gepusht (resume_ack).
        self._last_seen_seq_by_client: dict[str, int] = {}
        # Phase C — Boot-Epoch pro Client. Wird aus dem Handshake-String
        # extrahiert (name_cid_bootepoch). Bei Mismatch mit gespeichertem
        # Wert gilt Client als neu gestartet — last_seen_seq resetten,
        # sonst wuerden alle Payloads mit frisch beginnender seq=1 als
        # Replay verworfen. Alter Client ohne Boot-Epoch im Handshake
        # bekommt stattdessen last_seen=0 (= volles Resend / kein Dedup).
        self._boot_epoch_by_client: dict[str, int] = {}
        # Phase C — Poison-Counter pro (client_id, seq). Muss Disconnects
        # ueberdauern, sonst wuerde der Zaehler bei jedem Reconnect wieder
        # bei 0 anfangen und der Loop (Fehler → disconnect → Reconnect →
        # Replay → Fehler) nie durchbrochen. Session-Reset leert mit.
        self._poison_counts_by_client: dict[str, dict[int, int]] = {}

        self.logger = get_logger(__name__, './logs/arceus.log')
    
    def _log_dedup(self, err_type: str, msg: str, *, exc: BaseException | None = None,
                   level: str = "warning") -> None:
        """Rate-limited Logging — Pendant zu Munchlax._log_dedup. Verhindert,
        dass ein toter Client-Socket den Server-Log mit identischen Tracebacks
        flutet, bevor der naechste check_heartbeats-Tick den Client sauber
        rauswirft.
        """
        now = time.time()
        slot = self._err_counts.get(err_type)
        if slot is None or (now - slot["first_ts"]) > LOG_DEDUP_WINDOW_SECONDS:
            if slot is not None and slot["count"] > 1:
                self.logger.warning(
                    f"[{err_type}] {slot['count']-1}x weitere Occurrences "
                    f"im Fenster {slot['first_ts']:.0f}-{now:.0f} unterdrueckt"
                )
            self._err_counts[err_type] = {"first_ts": now, "count": 1}
            emit = getattr(self.logger, level, self.logger.warning)
            emit(msg)
            if exc is not None:
                self.logger.warning(f"{traceback.format_exc()}")
        else:
            slot["count"] += 1

    def _flush_dedup_stale(self) -> None:
        """Entleert abgelaufene Dedup-Fenster — gibt die aufgelaufenen
        Summaries aus, auch wenn kein neues Event vom gleichen Typ kommt.
        Wird im check_heartbeats-Loop regelmaessig aufgerufen, damit lange
        stille Perioden nicht die Dedup-Zahlen verschlucken.
        """
        now = time.time()
        for err_type in list(self._err_counts.keys()):
            slot = self._err_counts[err_type]
            if (now - slot["first_ts"]) > LOG_DEDUP_WINDOW_SECONDS:
                if slot["count"] > 1:
                    self.logger.info(
                        f"[{err_type}] {slot['count']-1}x weitere Occurrences "
                        f"im Fenster {slot['first_ts']:.0f}-{now:.0f} unterdrueckt"
                    )
                del self._err_counts[err_type]

    async def handle_munchlax(self, reader, writer):

        raw = await self.receive_message(reader)
        # Phase C: Handshake-Format name_cid_bootepoch. Alte Clients senden
        # name_cid (nur 1 Underscore als Trenner). rsplit mit 2 → 3 Teile fuer
        # neues Format, 2 fuer altes. Namen koennen eigene Underscores
        # enthalten, deswegen rsplit (von rechts).
        parts = raw.rsplit("_", 2)
        boot_epoch: int | None = None
        if len(parts) == 3:
            try:
                client_name, client_id, boot_epoch_str = parts
                boot_epoch = int(boot_epoch_str)
            except ValueError:
                # 3. Teil war keine Zahl — kein boot_epoch → Legacy-Format,
                # das letzte "_X" gehoerte zum Namen. Als name_cid parsen.
                client_name, client_id = raw.rsplit("_", 1)
        else:
            client_name, client_id = parts
        self.munchlaxes[client_id] = writer
        self.munchlax_names[client_id] = client_name
        self.munchlax_status[client_id] = 'connected'
        self.heartbeat_counts[client_id] = 0
        self._client_sessions[client_id] = {
            "started_at": time.time(),
            "recv": 1,  # Handshake-Nachricht zaehlt mit
            "sent": 0,
            "last_recv_at": time.time(),
            "last_recv_type": "handshake",
        }
        # Heartbeat-Timestamp bereits bei Registrierung setzen, sonst wirft
        # ein Frueh-Disconnect (z.B. Slot-Collision-Reject vor dem ersten
        # 'heartbeat'-Send) in disconnect_client KeyError beim del —
        # State-Cleanup und broadcast_*-Follow-Ups laufen dann nie durch.
        # Praktischer Effekt zusaetzlich: der neue Client ist ab jetzt
        # regulaer im check_heartbeats-Watchdog, falls er nie einen
        # Heartbeat sendet (sync-reviewer R5).
        self.munchlax_heartbeats[client_id] = time.time()
        self.client_player_ids[client_id] = set()
        self.declared_player_ids_by_client[client_id] = set()
        self.writer_locks[client_id] = asyncio.Lock()
        self.logger.info(f"Client {client_id} connected and registered.")

        # Phase C: Resume-Ack an Client. Enthaelt die hoechste sequence_id,
        # die der Server fuer DIESE client_id je gesehen hat. Erster Connect
        # oder Session-Reset: 0 — Client resendet dann nichts, falls Buffer
        # ohnehin leer. Reconnect mit bekannter client_id + gleicher Boot-
        # Epoch: letzte gesehene seq, Client resendet nur die Luecke.
        #
        # Boot-Epoch-Mismatch: Client hat die App neu gestartet (gleiche
        # client_id persistiert, aber _next_seq lief auf 1 zurueck). Ohne
        # Reset wuerde Server alle neuen Payloads als Replay dedupen —
        # stiller Datenverlust. Also: last_seen auf 0 zuruecksetzen und
        # neue Epoche speichern.
        stored_epoch = self._boot_epoch_by_client.get(client_id)
        if boot_epoch is not None and stored_epoch is not None and stored_epoch != boot_epoch:
            self.logger.info(
                f"Boot-Epoch-Mismatch fuer {client_id} ({client_name}): "
                f"gespeichert={stored_epoch}, neu={boot_epoch} — "
                f"last_seen_seq reset auf 0"
            )
            self._last_seen_seq_by_client[client_id] = 0
        if boot_epoch is not None:
            self._boot_epoch_by_client[client_id] = boot_epoch

        last_seen_seq = self._last_seen_seq_by_client.get(client_id, 0)
        # SYNCHRON vor update_all_clients/broadcast_*-Tasks senden, damit der
        # Client Chance hat, seinen Replay auszugeben BEVOR der Server ihn
        # mit Live-Traffic ueberschuettet. send_to_client holt writer_lock,
        # also laufen nachfolgende send_to_client-Aufrufe hinter resume_ack
        # her — Reihenfolge-Garantie Server→Client auf einem Writer.
        # Alte Clients ohne boot_epoch: kein resume_ack senden — die haben
        # keine Phase-C-Code-Pfade die drauf warten.
        if boot_epoch is not None:
            try:
                await self.send_to_client(client_id, {
                    "type": "resume_ack",
                    "last_seen_seq": last_seen_seq,
                })
                self.logger.info(
                    f"resume_ack an {client_id} gesendet: last_seen_seq={last_seen_seq}"
                )
            except TRANSIENT_NET_EXCEPTIONS as exc:
                # Writer tot → kein weiterer Handler-Loop nötig, direkt
                # Cleanup via disconnect_client.
                self.logger.warning(
                    f"resume_ack an {client_id} failed (Netz): "
                    f"{type(exc).__name__}: {exc}"
                )
                await self.disconnect_client(
                    client_id, writer=writer, reason=f"resume_ack_{type(exc).__name__}"
                )
                return
            except Exception as exc:
                self.logger.warning(
                    f"resume_ack an {client_id} failed: {type(exc).__name__}: {exc}"
                )

        asyncio.create_task(self.update_all_clients(client_id))
        asyncio.create_task(self.broadcast_connection_status())
        asyncio.create_task(self.broadcast_player_names())

        disconnect_reason = "unknown"
        while True:
            try:
                data = await self.receive_message(reader)
                # Phase C: Replay-Wrapper abstreifen. Alte Clients (ohne
                # Phase-C-Code) senden Payloads unverpackt — gleiches Verhalten
                # wie bisher. Phase-C-Clients wrappen bufferable Messages in
                # {"__seq__": N, "__payload__": orig}. Dedupe:
                #   - seq <= last_seen_seq → Replay einer bereits verarbeiteten
                #     Nachricht, verwerfen und mit naechstem recv weitermachen.
                #   - seq > last_seen_seq → verarbeiten + last_seen_seq ERST
                #     NACH erfolgreicher Verarbeitung updaten (at-least-once:
                #     wenn Verarbeitung mit Exception kippt, soll Client das
                #     Item beim naechsten Resume erneut senden).
                current_seq: int | None = None
                if isinstance(data, dict) and "__seq__" in data and "__payload__" in data:
                    seq = int(data["__seq__"])
                    payload = data["__payload__"]
                    last_seen = self._last_seen_seq_by_client.get(client_id, 0)
                    if seq <= last_seen:
                        self.logger.debug(
                            f"Replay von {client_id} verworfen: seq={seq} "
                            f"<= last_seen={last_seen}, "
                            f"payload_type={payload.get('type') if isinstance(payload, dict) else type(payload).__name__}"
                        )
                        continue
                    current_seq = seq
                    data = payload
                msg_desc = data.get("type") if isinstance(data, dict) else (data if isinstance(data, str) else f"{len(data)} Spieler")
                self.logger.debug(f"Empfangen von {client_id}: {msg_desc}")
                sess = self._client_sessions.get(client_id)
                if sess is not None:
                    sess["recv"] += 1
                    sess["last_recv_at"] = time.time()
                    sess["last_recv_type"] = str(msg_desc)
                if type(data) == str and data.startswith("disconnect"): # or not data:
                    disconnect_reason = "explicit_disconnect"
                    break
                if data == 'heartbeat':
                    self.munchlax_heartbeats[client_id] = time.time()
                elif isinstance(data, dict) and data.get("type") == "boxes_update":
                    player_id = data.get("player_id")
                    boxes = data.get("boxes")
                    if player_id is None or boxes is None:
                        self.logger.warning(f"boxes_update ohne player_id/boxes von {client_id}: {data!r}")
                    else:
                        self.boxes[player_id] = boxes
                        asyncio.create_task(self.broadcast_boxes_update(client_id, player_id, boxes))
                elif isinstance(data, dict) and data.get("type") == "session_reset":
                    self.logger.info(
                        f"session_reset von {client_id} — Server-State wird geleert und "
                        f"an alle Peers broadcastet"
                    )
                    self._reset_session_state()
                    asyncio.create_task(self.broadcast_session_reset(sender_id=client_id))
                elif isinstance(data, dict) and data.get("type") == "encounter_sync":
                    encounters = data.get("encounters", [])
                    changed_links: list[dict] = []
                    trade_warnings: list[dict] = []
                    tokens_changed = False
                    for enc in encounters:
                        trade = self._detect_trade(enc)
                        if trade is not None:
                            trade_warnings.append(trade)
                        key = (enc["personality"], enc["owner"])
                        self.encounters[key] = enc
                        # Auto-Consume Token-Slot wenn dieser Encounter auf einer
                        # aktiven Token-Route passiert (Task #12).
                        if self._token_consume_if_matches(
                            enc.get("owner", ""), enc.get("edition"), enc.get("route")
                        ):
                            tokens_changed = True
                        link = self._assign_link_group(enc)
                        if link is not None:
                            changed_links.append(link)
                    asyncio.create_task(self.broadcast_encounter_sync(client_id, encounters))
                    for link in changed_links:
                        asyncio.create_task(self.broadcast_soullink_link_state(link))
                        clash = self._check_first_type_clash(link)
                        if link.pop("_compensation_broadcast_pending", False):
                            asyncio.create_task(self.broadcast_soullink_tokens())
                        if clash is not None:
                            asyncio.create_task(self.broadcast_soullink_rule_violation(clash))
                    for warning in trade_warnings:
                        asyncio.create_task(self.broadcast_soullink_rule_violation(warning))
                    if tokens_changed:
                        asyncio.create_task(self.broadcast_soullink_tokens())
                elif isinstance(data, dict) and data.get("type") == "encounter_outcome":
                    pv = data["personality"]
                    owner = data["owner"]
                    outcome = data["outcome"]
                    key = (pv, owner)
                    if key in self.encounters:
                        self.encounters[key]["outcome"] = outcome
                    link = self._update_link_member_outcome(pv, owner, outcome)
                    asyncio.create_task(self.broadcast_encounter_outcome(client_id, pv, owner, outcome))
                    if link is not None:
                        if link.get("state") == "complete":
                            self.logger.info(
                                f"encounter_outcome fuellt link={link.get('link_id')} "
                                f"auf state=complete (outcome={outcome} owner={owner} pv={pv})"
                            )
                        asyncio.create_task(self.broadcast_soullink_link_state(link))
                elif isinstance(data, dict) and data.get("type") == "soullink_config":
                    cfg = data.get("config", {})
                    self.soullink_config.update(cfg)
                    self.logger.info(
                        f"Soullink-Config aktualisiert: mode={self.soullink_config.get('mode')}, "
                        f"players={self.soullink_config.get('expected_owners')}"
                    )
                    asyncio.create_task(self.broadcast_soullink_config())
                    if self._recompute_team_state():
                        asyncio.create_task(self.broadcast_soullink_team_state())
                elif isinstance(data, dict) and data.get("type") == "soullink_death":
                    payload = self._process_death(data)
                    if payload is not None:
                        asyncio.create_task(self.broadcast_soullink_death(payload))
                    if self._recompute_versus_state():
                        asyncio.create_task(self.broadcast_soullink_versus_state())
                elif isinstance(data, dict) and data.get("type") == "soullink_versus_battle":
                    battle = {
                        "winner": data.get("winner"),
                        "loser": data.get("loser"),
                        "label": data.get("label", ""),
                        "timestamp": time.time(),
                    }
                    self.soullink_versus_battles.append(battle)
                    self.logger.info(
                        f"Versus-Battle-Result: {battle['winner']} vs {battle['loser']} "
                        f"({battle['label']})"
                    )
                    asyncio.create_task(self.broadcast_soullink_versus_battle(battle))
                elif isinstance(data, dict) and data.get("type") == "timer_start":
                    self._timer_start()
                    asyncio.create_task(self.broadcast_timer_state())
                elif isinstance(data, dict) and data.get("type") == "timer_reset":
                    self._timer_reset()
                    asyncio.create_task(self.broadcast_timer_state())
                elif isinstance(data, dict) and data.get("type") == "timer_split":
                    label = data.get("label", "split")
                    split = self._timer_split(label, source=data.get("source", "manual"))
                    if split is not None:
                        asyncio.create_task(self.broadcast_timer_state())
                elif isinstance(data, dict) and data.get("type") == "timer_pause_request":
                    if self._timer_add_consent("pause", client_id):
                        asyncio.create_task(self.broadcast_timer_state())
                elif isinstance(data, dict) and data.get("type") == "timer_resume_request":
                    if self._timer_add_consent("resume", client_id):
                        asyncio.create_task(self.broadcast_timer_state())
                elif isinstance(data, dict) and data.get("type") == "countdown_start":
                    duration = float(data.get("duration_seconds", 0.0) or 0.0)
                    label = data.get("label", "")
                    self._countdown_start(duration, label)
                    asyncio.create_task(self.broadcast_countdown_state())
                elif isinstance(data, dict) and data.get("type") == "countdown_pause":
                    if self._countdown_pause():
                        asyncio.create_task(self.broadcast_countdown_state())
                elif isinstance(data, dict) and data.get("type") == "countdown_resume":
                    if self._countdown_resume():
                        asyncio.create_task(self.broadcast_countdown_state())
                elif isinstance(data, dict) and data.get("type") == "countdown_cancel":
                    self._countdown_reset()
                    asyncio.create_task(self.broadcast_countdown_state())
                elif isinstance(data, dict) and data.get("type") == "soullink_rule_violation":
                    # Client-initiierter Violation-Broadcast (z.B. total_wipe).
                    payload = data.get("violation") or {}
                    if payload:
                        asyncio.create_task(self.broadcast_soullink_rule_violation(payload))
                elif isinstance(data, dict) and data.get("type") == "wipe_dismissed":
                    # Ein Client hat sein TotalWipe-Popup per Cancel geschlossen.
                    # Weitergeben an alle Clients — die Team-Mitgliedschaftspruefung
                    # (coop = alle, versus = team_letter match) macht der Empfaenger.
                    # Kein Server-seitiges Dedupe: bei zwei parallelen Dismiss-
                    # Klicks von zwei Team-Membern soll jeder Broadcast durchlaufen.
                    subj = data.get("subject_pid")
                    if subj is not None:
                        asyncio.create_task(self.broadcast_wipe_dismissed({
                            "subject_pid": subj,
                            "timestamp": data.get("timestamp"),
                        }))
                elif isinstance(data, dict) and data.get("type") == "soullink_token_earned":
                    owner = data.get("owner") or ""
                    if self._token_earn(owner):
                        asyncio.create_task(self.broadcast_soullink_tokens())
                elif isinstance(data, dict) and data.get("type") == "soullink_token_redeem":
                    owner = data.get("owner") or ""
                    route = data.get("route")
                    edition = data.get("edition")
                    if self._token_redeem(owner, edition, route):
                        asyncio.create_task(self.broadcast_soullink_tokens())
                elif isinstance(data, dict) and data.get("type") == "bag_sync":
                    bag_owner = data.get("owner", "")
                    bag_edition = data.get("edition", "")
                    self.bags[(bag_owner, bag_edition)] = data.get("pockets", {})
                    asyncio.create_task(self.broadcast_bag_sync(client_id, data))
                elif isinstance(data, dict) and data.get("type") == "rando_moves_sync":
                    pid = data.get("player_id")
                    if isinstance(pid, int):
                        # Ownership-Check: nur der Client, der diesen player_id
                        # per declared_player_ids beansprucht hat, darf das
                        # Mapping fuer ihn pushen. Verhindert Last-Writer-Wins-
                        # Race, wenn ein Fremd-Client (Fehlkonfiguration, Bug)
                        # einen fremden Slot ueberschreibt.
                        # Vereinigung aus declared (statische Deklaration)
                        # und client_player_ids (dynamische Teams) — ein
                        # Slot ohne laufenden Emulator soll trotzdem
                        # Rando-Push erlauben (R2 KRITISCH sync).
                        owned_ids = (
                            self.declared_player_ids_by_client.get(client_id, set())
                            | self.client_player_ids.get(client_id, set())
                        )
                        if pid not in owned_ids:
                            self.logger.warning(
                                f"rando_moves_sync verworfen: client={client_id} "
                                f"nicht owner von pid={pid} (owned={sorted(owned_ids)})"
                            )
                        else:
                            tm_moves = data.get("tm_moves") or {}
                            hm_moves = data.get("hm_moves") or {}
                            self.rando_moves[pid] = {
                                "tm_moves": {int(k): v for k, v in (tm_moves or {}).items()}
                                            if isinstance(tm_moves, dict) else {},
                                "hm_moves": {int(k): v for k, v in (hm_moves or {}).items()}
                                            if isinstance(hm_moves, dict) else {},
                            }
                            self.logger.info(
                                f"rando_moves_sync empfangen: client={client_id}, "
                                f"player_id={pid}, "
                                f"tms={len(tm_moves) if isinstance(tm_moves, dict) else 0}, "
                                f"hms={len(hm_moves) if isinstance(hm_moves, dict) else 0}"
                            )
                            asyncio.create_task(self.broadcast_rando_moves_sync(client_id, pid))
                    else:
                        self.logger.warning(
                            f"rando_moves_sync ohne gueltige player_id: {pid!r} "
                            f"(client={client_id})"
                        )
                elif isinstance(data, dict) and data.get("type") == "declared_player_ids":
                    # Client meldet welche Netz-Slots (player_ids) er belegt,
                    # abgeleitet aus seiner player.yml (nicht via BizHawk-Handshake).
                    # Fuellt client_player_ids sofort, damit player_names auch ohne
                    # verbundenen Emulator bekannt sind (Sortierung im NuzlockeMenu).
                    try:
                        raw_ids = data.get("player_ids", []) or []
                        declared = {int(pid) for pid in raw_ids}
                    except (TypeError, ValueError) as exc:
                        self.logger.warning(
                            f"declared_player_ids von {client_id} unlesbar: {data!r} ({exc})"
                        )
                        continue
                    conflicts: dict[str, list[int]] = {}
                    for other_cid, other_ids in self.client_player_ids.items():
                        if other_cid == client_id:
                            continue
                        overlap = declared & (other_ids or set())
                        if overlap:
                            other_name = self.munchlax_names.get(other_cid, other_cid)
                            conflicts[other_name] = sorted(overlap)
                    if conflicts:
                        self.logger.warning(
                            f"declared_player_ids von {client_id} ({self.munchlax_names.get(client_id, '')}) "
                            f"kollidiert: angefordert={sorted(declared)}, kollisionen={conflicts}"
                        )
                        try:
                            await self.send_to_client(client_id, {
                                "type": "slot_collision",
                                "requested_slots": sorted(declared),
                                "conflicts": conflicts,
                            })
                        except Exception as exc:
                            self.logger.warning(f"slot_collision-Antwort an {client_id} failed: {exc}")
                        # Handler-Loop verlassen — anschliessender disconnect_client
                        # entfernt den Client vollstaendig (Namen, Locks, State).
                        disconnect_reason = "slot_collision"
                        break
                    # declared_player_ids_by_client: authoritative Ownership-
                    # Quelle fuer rando_moves_sync. client_player_ids wird
                    # weiterhin fuer Namen/Sortierung/Live-Slots verwendet und
                    # kann durch Team-Nachrichten verkleinert werden.
                    self.declared_player_ids_by_client[client_id] = set(declared)
                    if declared != self.client_player_ids.get(client_id, set()):
                        self.client_player_ids[client_id] = declared
                        self.logger.info(
                            f"declared_player_ids von {client_id} akzeptiert: {sorted(declared)}"
                        )
                        asyncio.create_task(self.broadcast_player_names())
                else:
                    for player, team in data.items(): #type: ignore
                        if player not in self.teams or self.teams[player] != team:
                            self.teams[player] = team
                    new_player_ids = set(data.keys())
                    if new_player_ids != self.client_player_ids.get(client_id, set()):
                        self.client_player_ids[client_id] = new_player_ids
                        asyncio.create_task(self.broadcast_player_names())
                    if self._recompute_versus_state():
                        asyncio.create_task(self.broadcast_soullink_versus_state())
                    if self._recompute_team_state():
                        asyncio.create_task(self.broadcast_soullink_team_state())
                    auto_splits = self._detect_badge_splits(client_id, data)
                    if auto_splits:
                        for split in auto_splits:
                            self.logger.info(f"Auto-Split (Badge): {split['label']}")
                        asyncio.create_task(self.broadcast_timer_state())
                # Phase C: seq ERST NACH erfolgreicher Verarbeitung committen.
                # Wenn der obige if/elif-Baum eine Exception geworfen haette,
                # waere das Item noch nicht als "gesehen" markiert und der
                # Client wuerde beim naechsten Resume erneut senden (at-
                # least-once). broadcast_*-Tasks sind create_task'd und zaehlen
                # fuer die Commit-Entscheidung nicht — die laufen eh async.
                if current_seq is not None:
                    self._last_seen_seq_by_client[client_id] = current_seq
                    # Erfolgreicher Retry nach vorherigem Fehler → Poison-
                    # Counter fuer diese seq aufraeumen, sonst leakt der
                    # Dict-Eintrag unbegrenzt (reviewer R3 H5).
                    poison = self._poison_counts_by_client.get(client_id)
                    if poison is not None and current_seq in poison:
                        poison.pop(current_seq, None)
                        if not poison:
                            self._poison_counts_by_client.pop(client_id, None)
            except TRANSIENT_NET_EXCEPTIONS as exc:
                # Reader ist tot, weitere receive_message-Aufrufe wuerden nur
                # noch denselben Fehler in Busy-Loop werfen. Schleife verlassen,
                # damit der regulaere disconnect_client-Pfad greift. Rate-
                # Limited Log — pre-fix wurden hier bei 1000+ Dropouts in Folge
                # 1000+ volle Tracebacks ins arceus.log gekippt.
                sess = self._client_sessions.get(client_id, {})
                last_at = sess.get("last_recv_at", 0)
                last_age = time.time() - last_at if last_at else -1.0
                self._log_dedup(
                    f"handle_munchlax_{type(exc).__name__}",
                    f"handle_munchlax Netz-Fehler fuer {client_id} "
                    f"({self.munchlax_names.get(client_id, '')}): "
                    f"{type(exc).__name__}: {exc} "
                    f"(letzte_msg={sess.get('last_recv_type', '')!r}, "
                    f"vor_{last_age:.1f}s, recv={sess.get('recv', 0)})",
                    exc=exc,
                )
                disconnect_reason = f"recv_{type(exc).__name__}"
                break
            except pickle.UnpicklingError as exc:
                self.logger.error(f"Fehler beim Entpacken der Daten: {type(exc)},{exc}")
                self.logger.error(f"{traceback.format_exc()}")
            except Exception as exc:
                # Poison-Message-Schutz (sync-reviewer Runde 2 WARNUNG W5):
                # bei determinstischem Fehler in der Verarbeitung wuerde
                # Client das Item nach Reconnect endlos erneut senden (seq
                # wurde nicht committet) — Server trennt erneut, Loop.
                # Nach 3 Wiederholungen committen wir die seq trotzdem,
                # loggen die Poison-Message als WARN und brechen die
                # Verbindung ab. Nachlauf: User muss den Mitspieler neu
                # verbinden lassen; Payload ist verloren, aber kein Loop.
                if current_seq is not None:
                    poison = self._poison_counts_by_client.setdefault(client_id, {})
                    poison[current_seq] = poison.get(current_seq, 0) + 1
                    if poison[current_seq] >= 3:
                        self.logger.warning(
                            f"Poison-Message bei {client_id} seq={current_seq} "
                            f"nach 3 Fehlversuchen committed und verworfen: "
                            f"{type(exc).__name__}: {exc}"
                        )
                        self._last_seen_seq_by_client[client_id] = current_seq
                        poison.pop(current_seq, None)
                self.logger.error(f"handle_munchlax abgebrochen:{type(exc)},{exc}")
                self.logger.error(f"{traceback.format_exc()}")
                disconnect_reason = f"recv_unexpected_{type(exc).__name__}"
                break

        # writer explizit mitgeben — disconnect_client soll pruefen ob der
        # registrierte Writer noch derselbe ist, sonst kann ein Reconnect
        # der zwischen Loop-Ausstieg und Entry stattfindet fälschlich den
        # frischen Socket schliessen. Details in disconnect_client-Docstring.
        await self.disconnect_client(client_id, writer=writer, reason=disconnect_reason)

    async def update_all_clients(self, client_id):
        old_teams = self.teams.copy()
        # Onboarding-Sequenz komplett in try/except gehuellt: Task laeuft ohne
        # Referenz + ohne add_done_callback (siehe start-Aufruf), eine Exception
        # in einem der send_to_client-Aufrufe wuerde den Task sonst lautlos
        # sterben lassen und der Client bekaeme unvollstaendiges Onboarding
        # (z.B. nie sein team_state) ohne Spur im Log. Sync-Review Runde 6.
        try:
            self.logger.debug(f"Initiales Team-Update an Client {client_id}: {len(self.teams)} Spieler")
            await self.send_to_client(client_id, self.teams)

            # Box-Stand einmal an den frisch verbundenen Client schicken, damit
            # remote betrachtete BoxMenüs sofort den letzten bekannten Cache haben.
            for player_id, boxes in list(self.boxes.items()):
                await self.send_to_client(client_id, {
                    "type": "boxes_update",
                    "player_id": player_id,
                    "boxes": boxes,
                })

            if self.encounters:
                await self.send_to_client(client_id, {
                    "type": "encounter_sync",
                    "encounters": list(self.encounters.values()),
                })

            if self.soullink_config.get("mode", "off") in BROADCAST_MODES:
                await self.send_to_client(client_id, {
                    "type": "soullink_config",
                    "config": dict(self.soullink_config),
                })
            for link in list(self.soullink_links.values()):
                await self.send_to_client(client_id, {
                    "type": "soullink_link_state",
                    "link": link,
                })
            for death in list(self.soullink_deaths.values()):
                await self.send_to_client(client_id, {
                    "type": "soullink_death",
                    "death": death,
                })
            # TTL-Filter nur fuer transiente Violation-Typen (total_wipe):
            # ohne Filter wuerde eine total_wipe-Violation aus einem vor
            # Stunden finalisierten Run bei jedem Reconnect erneut ein
            # Wipe-Popup auf allen Clients oeffnen (bothhaft 2026-09-30
            # 21:50:05: Reconnect replay'ed den 20:03:30-Wipe trotz
            # lebendigem Team). Persistente Typen (z.B. trade) bleiben
            # unberuehrt — sie beschreiben einen weiter geltenden Zustand
            # und muessen jedem spaeten Joiner zugestellt werden.
            # TTL misst gegen `_server_ts` (gesetzt beim Cache-Insert),
            # nicht gegen Client-timestamp — sonst wuerde Clock-Skew
            # eines Clients den Filter umgehen oder faelschlich greifen
            # lassen. Stale Entries werden ATOMAR vor dem continue
            # gepoppt — kein await dazwischen, kein Race mit einem
            # parallelen `broadcast_soullink_rule_violation`, der sonst
            # den Eintrag frisch ersetzen und direkt danach vom pop
            # geloescht werden koennte.
            now_replay = time.time()
            for key, violation in list(self.soullink_rule_violations.items()):
                vtype = violation.get("type")
                if vtype in TRANSIENT_VIOLATION_TYPES:
                    try:
                        ts = float(violation.get("_server_ts") or 0)
                    except (TypeError, ValueError):
                        ts = 0.0
                    age = now_replay - ts
                    if age > VIOLATION_REPLAY_TTL_S:
                        self.logger.info(
                            f"replay skip stale rule_violation (age={age:.0f}s): "
                            f"client={client_id} type={vtype} "
                            f"subject={violation.get('subject')}"
                        )
                        # Pop ist safe: wir halten die Original-Referenz
                        # ``violation`` und loeschen den Eintrag nur, wenn
                        # das Dict noch dasselbe Objekt enthaelt (Guard
                        # gegen Replace zwischen Snapshot-Erstellung und
                        # dieser Iteration).
                        if self.soullink_rule_violations.get(key) is violation:
                            self.soullink_rule_violations.pop(key, None)
                        continue
                await self.send_to_client(client_id, {
                    "type": "soullink_rule_violation",
                    "violation": violation,
                })
            if self.soullink_tokens:
                await self.send_to_client(client_id, {
                    "type": "soullink_tokens",
                    "tokens": dict(self.soullink_tokens),
                })
            if self.soullink_versus_state:
                await self.send_to_client(client_id, {
                    "type": "soullink_versus_state",
                    "state": self.soullink_versus_state,
                    "battles": list(self.soullink_versus_battles),
                })
            # Team-State (modus-unabhängig, siehe _compute_team_state) — beim Connect
            # einmal ausliefern. Entweder-oder-Muster gegen Duplikat:
            # - Aenderung durch den Connect → Broadcast an alle (inkl. neuer Client)
            #   als create_task (fire-and-forget), damit ein langsamer/haengender
            #   Fremd-Client die restlichen Onboarding-Sends nicht blockiert.
            # - State stable → gezielter direct-send an neuen Client.
            if self._recompute_team_state():
                asyncio.create_task(self.broadcast_soullink_team_state())
            elif self.soullink_team_state:
                await self.send_to_client(client_id, {
                    "type": "soullink_team_state",
                    "state": self.soullink_team_state,
                })
            if self.timer_state.get("start_ts") is not None:
                await self.send_to_client(client_id, {
                    "type": "timer_state",
                    "timer": self._timer_snapshot(),
                })
            if self.countdown_state.get("duration_seconds", 0.0) > 0:
                await self.send_to_client(client_id, {
                    "type": "countdown_state",
                    "countdown": self._countdown_snapshot(),
                })

            for (bag_owner, bag_edition), pockets in list(self.bags.items()):
                await self.send_to_client(client_id, {
                    "type": "bag_sync",
                    "owner": bag_owner,
                    "edition": bag_edition,
                    "pockets": pockets,
                })

            # Rando-Moves aller Spieler nachliefern — ohne diese Replay-Zeile
            # haben spaete Joiner keine TM/HM-Mappings fuer die anderen Spieler
            # und zeigen Vanilla-Icons im Overlay.
            for pid, moves in list(self.rando_moves.items()):
                await self.send_to_client(client_id, {
                    "type": "rando_moves_sync",
                    "player_id": int(pid),
                    "tm_moves": moves.get("tm_moves", {}),
                    "hm_moves": moves.get("hm_moves", {}),
                })
        except Exception as exc:
            self.logger.error(f"update_all_clients Onboarding an {client_id} abgebrochen: {type(exc)},{exc}")
            self.logger.error(f"{traceback.format_exc()}")
            return

        while True:
            try:
                if old_teams != self.teams:
                    old_teams = self.teams.copy()
                    await self.send_to_client(client_id, self.teams)
                await asyncio.sleep(1)
            except Exception as exc:
                self.logger.error(f"update_all_clients abgebrochen:{type(exc)},{exc}")
                self.logger.error(f"{traceback.format_exc()}")
                break

    async def send_to_client(self, client_id, message):
        """Sendet eine Nachricht an genau einen Client; serialisiert pro Writer."""
        writer = self.munchlaxes.get(client_id)
        lock = self.writer_locks.get(client_id)
        if writer is None or lock is None:
            return
        async with lock:
            await self.send_message(writer, message)
        sess = self._client_sessions.get(client_id)
        if sess is not None:
            sess["sent"] = sess.get("sent", 0) + 1

    async def broadcast_boxes_update(self, sender_id, player_id, boxes):
        """Verteilt einen Box-Update an alle Clients außer dem Absender."""
        message = {"type": "boxes_update", "player_id": player_id, "boxes": boxes}
        for client_id in list(self.munchlaxes.keys()):
            if client_id == sender_id:
                continue
            try:
                await self.send_to_client(client_id, message)
            except Exception as exc:
                self.logger.error(f"broadcast_boxes_update an {client_id} failed: {type(exc)},{exc}")
                self.logger.error(f"{traceback.format_exc()}")
    
    async def broadcast_encounter_sync(self, sender_id, encounters):
        message = {"type": "encounter_sync", "encounters": encounters}
        for client_id in list(self.munchlaxes.keys()):
            if client_id == sender_id:
                continue
            try:
                await self.send_to_client(client_id, message)
            except Exception as exc:
                self.logger.error(f"broadcast_encounter_sync an {client_id} failed: {type(exc)},{exc}")
                self.logger.error(f"{traceback.format_exc()}")

    async def broadcast_encounter_outcome(self, sender_id, personality, owner, outcome):
        message = {"type": "encounter_outcome", "personality": personality, "owner": owner, "outcome": outcome}
        for client_id in list(self.munchlaxes.keys()):
            if client_id == sender_id:
                continue
            try:
                await self.send_to_client(client_id, message)
            except Exception as exc:
                self.logger.error(f"broadcast_encounter_outcome an {client_id} failed: {type(exc)},{exc}")
                self.logger.error(f"{traceback.format_exc()}")

    @staticmethod
    def _edition_to_gen(edition) -> int:
        try:
            e = int(edition)
        except (TypeError, ValueError):
            return 0
        if 1 <= e <= 5:
            return 1
        if 11 <= e <= 15:
            return 2
        if 31 <= e <= 35:
            return 3
        if 41 <= e <= 45:
            return 4
        if 51 <= e <= 54:
            return 5
        if 61 <= e <= 64:
            return 6
        if 71 <= e <= 74:
            return 7
        return 0

    def _linked_owners_for(self, owner: str) -> list[str]:
        """Owner-Liste, mit denen dieser Owner verlinkt ist (unter Berücksichtigung von Mode/Team).

        Owner-Format-Normalisierung: Encounter-Sync liefert owner im
        ``build_owner``-Format ``"name_<player_slot>"``, ``expected_owners``
        speichert nur reine Namen. Direkter Vergleich matcht sonst nie.
        Root-Extraktion via ``PokedexDB.owner_root``.
        """
        cfg = self.soullink_config
        mode = cfg.get("mode", "off")
        if mode not in SOULLINK_MODES:
            return []
        expected = list(cfg.get("expected_owners", []))
        owner_key = PokedexDB.owner_root(owner)
        if owner_key not in expected:
            return []
        if mode == "versus":
            team_map = cfg.get("team_membership", {}) or {}
            my_team = team_map.get(owner_key)
            if my_team is None:
                return []
            return [o for o in expected if team_map.get(o) == my_team]
        # coop: full_chain = alle expected owners werden gelinkt.
        return list(expected)

    def _compute_link_state(self, link: dict) -> str:
        """Berechnet state (pending/complete/failed) aus Member-Outcomes."""
        members = link.get("members", {})
        outcomes = [m.get("outcome", "unknown") for m in members.values()]
        # Warten bis alle erwarteten Members drin sind
        if len(members) < len(link.get("expected_owners", [])):
            return "pending"
        if any(o == "unknown" for o in outcomes):
            return "pending"
        # min. einer nicht gefangen → failed (alle boxen)
        caught_like = {"caught", "obtained"}
        if all(o in caught_like for o in outcomes):
            return "complete"
        return "failed"

    def _find_or_create_link(self, route: int, edition_gen: int,
                              link_index: int, expected: list[str]) -> dict:
        for link in self.soullink_links.values():
            if (link["route"] == route and link["edition_gen"] == edition_gen
                    and link["link_index"] == link_index
                    and link["expected_owners"] == expected):
                return link
        link_id = self.soullink_next_id
        self.soullink_next_id += 1
        link = {
            "link_id": link_id,
            "route": route,
            "edition_gen": edition_gen,
            "link_index": link_index,
            "state": "pending",
            "expected_owners": expected,
            "members": {},
        }
        self.soullink_links[link_id] = link
        return link

    def _assign_link_group(self, enc: dict) -> dict | None:
        """Weist einem neuen Encounter eine Link-Group zu (falls Soullink aktiv + is_first).

        ``link["members"]`` verwendet Root-Owner (ohne Player-Slot-Suffix), damit
        der Vergleich gegen ``expected_owners`` (reine Namen) funktioniert.
        ``enc["owner"]`` kommt im ``build_owner``-Format ``"name_<player_slot>"``.
        """
        if self.soullink_config.get("mode", "off") not in SOULLINK_MODES:
            return None
        if not enc.get("is_first"):
            return None
        owner_raw = enc.get("owner")
        if not owner_raw:
            return None
        owner = PokedexDB.owner_root(owner_raw)
        expected = self._linked_owners_for(owner_raw)
        if len(expected) < 2:
            return None
        edition_gen = self._edition_to_gen(enc.get("edition"))
        if edition_gen == 0:
            return None
        route = enc.get("route", 0)
        # link_index = wie oft dieser Owner bereits auf (route, edition_gen) gelinkt wurde + 1
        prior = 0
        for link in self.soullink_links.values():
            if (link["route"] == route and link["edition_gen"] == edition_gen
                    and owner in link["members"]):
                prior += 1
        link_index = prior + 1
        link = self._find_or_create_link(route, edition_gen, link_index, expected)
        if owner in link["members"]:
            return None
        link["members"][owner] = {
            "personality": enc.get("personality"),
            "edition": enc.get("edition"),
            "outcome": enc.get("outcome", "unknown"),
            "dexnr": enc.get("dexnr"),
            "method": enc.get("method", "wild"),
        }
        link["state"] = self._compute_link_state(link)
        self.logger.info(
            f"Soullink-Group {link['link_id']} route={route} gen={edition_gen} "
            f"idx={link_index}: +{owner} ({len(link['members'])}/{len(expected)}) state={link['state']}"
        )
        return link

    def _update_link_member_outcome(self, personality, owner, outcome) -> dict | None:
        """Sucht Link-Group, in der (personality, owner) Mitglied ist, und aktualisiert outcome.

        ``owner`` kommt aus encounter_outcome-Payload im ``build_owner``-Format;
        ``link["members"]`` verwendet Root-Owner (siehe ``_assign_link_group``).
        """
        owner_key = PokedexDB.owner_root(owner)
        for link in self.soullink_links.values():
            member = link["members"].get(owner_key)
            if member is None:
                continue
            if member.get("personality") != personality:
                continue
            if member.get("outcome") == outcome:
                return None
            member["outcome"] = outcome
            new_state = self._compute_link_state(link)
            if new_state != link["state"]:
                link["state"] = new_state
                self.logger.info(
                    f"Soullink-Group {link['link_id']} state={new_state} "
                    f"(outcome-update {owner_key}={outcome})"
                )
            return link
        return None

    def _reset_session_state(self):
        """Löscht ALLEN Session-Content-Cache im Server, hält aber
        ``soullink_config``, ``munchlaxes``, ``client_player_ids`` &
        ``munchlax_names`` (Verbindungs-Metadaten, kein Session-Content).
        Analog zu ``Munchlax._clear_session_runtime_state``.
        """
        self.teams = {}
        self.boxes = {}
        self.encounters = {}
        self.bags = {}
        # rando_moves bewusst NICHT clearen: die ROMs der Clients sind nach
        # Session-Reset immer noch randomisiert, aber die Clients pushen
        # das Mapping nicht erneut — Overlays wuerden sonst bis zum
        # naechsten Randomize auf Vanilla fallen (R1 WARNUNG sync).
        self.soullink_links = {}
        self.soullink_next_id = 1
        self.soullink_deaths = {}
        self.soullink_versus_state = {}
        self.soullink_versus_battles = []
        self.soullink_rule_violations = {}
        # Dedupe-Timestamps auch clearen: sonst wuerde eine echte neue Violation
        # direkt nach dem Reset (innerhalb 5s) stumm gedropt werden.
        self._last_violation_broadcast_ts = {}
        self.soullink_tokens = {}
        self._team_owner_cache = {}
        # Phase C: Replay-State muss ebenfalls weg, sonst wuerde ein Client,
        # der kurz nach Session-Reset reconnected, seq-Werte senden, die
        # kleiner sind als sein alter last_seen_seq und als Duplikat verworfen.
        # Boot-Epochs + Poison-Counter ebenfalls.
        self._last_seen_seq_by_client.clear()
        self._boot_epoch_by_client.clear()
        self._poison_counts_by_client.clear()
        self.logger.info("Arceus _reset_session_state: alle Session-Caches geleert")

    async def broadcast_session_reset(self, sender_id: str | None = None):
        """Session-Reset an alle verbundenen Clients. sender_id kriegt die
        Nachricht bewusst mit — der Host, der den Reset getriggert hat, hat
        zwar seinen State schon lokal gecleart, aber symmetrisches Handling
        verhindert Drift wenn wir später mal auf Absender-Filter verzichten.
        Failsafe: Fehler pro Client isolieren, sonst reisst ein toter Client
        den restlichen Broadcast mit."""
        message = {"type": "session_reset"}
        for client_id in list(self.munchlaxes.keys()):
            if client_id == sender_id:
                # Host hat lokal bereits reset — kein zweiter Durchlauf, sonst
                # bekommt der Host-Munchlax kurz nach dem eigenen Reset noch
                # einen remote-session-reset-Broadcast → doppelt clear,
                # harmlos, aber unnötig.
                continue
            try:
                await self.send_to_client(client_id, message)
            except Exception as exc:
                self.logger.error(f"broadcast_session_reset an {client_id} failed: {exc}")

    async def broadcast_soullink_config(self):
        message = {"type": "soullink_config", "config": dict(self.soullink_config)}
        for client_id in list(self.munchlaxes.keys()):
            try:
                await self.send_to_client(client_id, message)
            except Exception as exc:
                self.logger.error(f"broadcast_soullink_config an {client_id} failed: {exc}")

    async def broadcast_soullink_link_state(self, link: dict):
        message = {"type": "soullink_link_state", "link": link}
        for client_id in list(self.munchlaxes.keys()):
            try:
                await self.send_to_client(client_id, message)
            except Exception as exc:
                self.logger.error(f"broadcast_soullink_link_state an {client_id} failed: {exc}")

    def _process_death(self, data: dict) -> dict | None:
        """Verarbeitet eine Todesmeldung: markiert Link-Members als dead und sammelt Partner.

        Death-Cache und Broadcast-Payload verwenden den Root-Owner (ohne
        Player-Slot-Suffix), damit sie zum Owner-Format in ``link["members"]``
        passen. Sonst würde bei encounter-outcome=dead der Death-Eintrag unter
        einem anderen Key liegen als der Link-Member.
        """
        personality = data.get("personality")
        owner_raw = data.get("owner")
        if personality is None or not owner_raw:
            return None
        owner = PokedexDB.owner_root(owner_raw)
        if self.soullink_config.get("mode", "off") not in SOULLINK_MODES:
            # Ohne Soullink trotzdem Broadcast (Overlay/UI-Update), aber keine Partner-Logik.
            death = {
                "personality": personality,
                "owner": owner,
                "edition": data.get("edition"),
                "cause": data.get("cause", "faint"),
                "timestamp": time.time(),
                "partners": [],
                "link_id": None,
            }
            self.soullink_deaths[(personality, owner)] = death
            return death
        target_link = None
        for link in self.soullink_links.values():
            member = link["members"].get(owner)
            if member and member.get("personality") == personality:
                target_link = link
                break
        partners: list[dict] = []
        link_id = None
        if target_link is not None:
            link_id = target_link["link_id"]
            for other_owner, member in target_link["members"].items():
                if other_owner == owner:
                    member["outcome"] = "dead"
                    continue
                partners.append({
                    "owner": other_owner,
                    "personality": member.get("personality"),
                    "edition": member.get("edition"),
                    "dexnr": member.get("dexnr"),
                })
            target_link["state"] = "failed"
        death = {
            "personality": personality,
            "owner": owner,
            "edition": data.get("edition"),
            "cause": data.get("cause", "faint"),
            "timestamp": time.time(),
            "partners": partners,
            "link_id": link_id,
        }
        self.soullink_deaths[(personality, owner)] = death
        self.logger.info(
            f"Soullink-Death: owner={owner} pv={personality} "
            f"link={link_id} partners={[p['owner'] for p in partners]}"
        )
        return death

    # ----- Timer -----

    def _timer_elapsed(self) -> float:
        ts = self.timer_state
        start = ts.get("start_ts")
        if start is None:
            return 0.0
        pause_total = ts.get("pause_total_seconds", 0.0)
        if ts.get("running"):
            return max(0.0, time.time() - start - pause_total)
        pause_start = ts.get("pause_start_ts")
        if pause_start is not None:
            return max(0.0, pause_start - start - pause_total)
        return max(0.0, time.time() - start - pause_total)

    def _timer_snapshot(self) -> dict:
        ts = self.timer_state
        return {
            "running": ts.get("running", False),
            "start_ts": ts.get("start_ts"),
            "pause_start_ts": ts.get("pause_start_ts"),
            "pause_total_seconds": ts.get("pause_total_seconds", 0.0),
            "elapsed": self._timer_elapsed(),
            "server_ts": time.time(),
            "splits": list(ts.get("splits", [])),
            "pause_consent": list(ts.get("pause_consent", [])),
            "resume_consent": list(ts.get("resume_consent", [])),
            "consent_required": len(self.munchlaxes),
        }

    def _timer_start(self):
        ts = self.timer_state
        if ts.get("start_ts") is None:
            ts["start_ts"] = time.time()
            ts["pause_total_seconds"] = 0.0
            ts["pause_start_ts"] = None
            ts["splits"] = []
        elif not ts.get("running") and ts.get("pause_start_ts") is not None:
            # Resume ohne Konsens (Force-Start): pause_total um vergangene Pause erhöhen
            ts["pause_total_seconds"] = ts.get("pause_total_seconds", 0.0) + (time.time() - ts["pause_start_ts"])
            ts["pause_start_ts"] = None
        ts["running"] = True
        ts["pause_consent"] = []
        ts["resume_consent"] = []
        self.logger.info(f"Timer gestartet/resumed: start_ts={ts['start_ts']}")

    def _timer_reset(self):
        self.timer_state = {
            "running": False,
            "start_ts": None,
            "pause_start_ts": None,
            "pause_total_seconds": 0.0,
            "splits": [],
            "pause_consent": [],
            "resume_consent": [],
        }
        self._timer_last_badges = {}
        self.logger.info("Timer resettet")

    def _timer_split(self, label: str, source: str = "manual") -> dict | None:
        ts = self.timer_state
        if ts.get("start_ts") is None:
            return None
        split = {
            "label": label,
            "elapsed": self._timer_elapsed(),
            "ts": time.time(),
            "source": source,
        }
        ts.setdefault("splits", []).append(split)
        return split

    def _timer_add_consent(self, kind: str, client_id: str) -> bool:
        ts = self.timer_state
        if ts.get("start_ts") is None:
            return False
        key = "pause_consent" if kind == "pause" else "resume_consent"
        other_key = "resume_consent" if kind == "pause" else "pause_consent"
        consent = ts.setdefault(key, [])
        if client_id not in consent:
            consent.append(client_id)
        ts[other_key] = []
        required = len(self.munchlaxes)
        if required > 0 and len(consent) >= required:
            if kind == "pause" and ts.get("running"):
                ts["running"] = False
                ts["pause_start_ts"] = time.time()
                ts["pause_consent"] = []
                self.logger.info("Timer pausiert (Konsens erreicht)")
            elif kind == "resume" and not ts.get("running") and ts.get("pause_start_ts") is not None:
                ts["pause_total_seconds"] = ts.get("pause_total_seconds", 0.0) + (time.time() - ts["pause_start_ts"])
                ts["pause_start_ts"] = None
                ts["running"] = True
                ts["resume_consent"] = []
                self.logger.info("Timer resumed (Konsens erreicht)")
        return True

    def _detect_badge_splits(self, client_id: str, teams_data) -> list[dict]:
        """Prüft Team-Update auf Badge-Count-Erhöhung → automatischer Split pro betroffenem Player."""
        if self.timer_state.get("start_ts") is None:
            return []
        splits: list[dict] = []
        if not isinstance(teams_data, dict):
            return splits
        for player_id, team in teams_data.items():
            if not team or len(team) <= 6:
                continue
            badges_raw = team[6]
            new_count = self._badge_count(badges_raw)
            if new_count is None:
                continue
            key = (client_id, player_id)
            old_count = self._timer_last_badges.get(key)
            self._timer_last_badges[key] = new_count
            if old_count is None:
                continue
            if new_count > old_count:
                owner = self._owner_for_client(client_id)
                label = f"{owner} Gym {new_count}"
                split = self._timer_split(label, source="badge")
                if split is not None:
                    splits.append(split)
        return splits

    @staticmethod
    def _badge_count(badges) -> int | None:
        if badges is None:
            return None
        if isinstance(badges, int):
            return bin(badges).count("1") if badges >= 0 else None
        if isinstance(badges, (list, tuple)):
            return sum(1 for b in badges if b)
        if isinstance(badges, str):
            try:
                return sum(1 for c in badges if c in "1TtXx✓")
            except Exception:
                return None
        return None

    async def broadcast_timer_state(self):
        message = {"type": "timer_state", "timer": self._timer_snapshot()}
        for client_id in list(self.munchlaxes.keys()):
            try:
                await self.send_to_client(client_id, message)
            except Exception as exc:
                self.logger.error(f"broadcast_timer_state an {client_id} failed: {exc}")

    async def broadcast_timer_tick(self):
        message = {
            "type": "timer_tick",
            "elapsed": self._timer_elapsed(),
            "running": self.timer_state.get("running", False),
            "server_ts": time.time(),
        }
        for client_id in list(self.munchlaxes.keys()):
            try:
                await self.send_to_client(client_id, message)
            except Exception as exc:
                self.logger.debug(f"broadcast_timer_tick an {client_id} failed: {exc}")

    async def timer_tick_loop(self):
        while True:
            try:
                if self.timer_state.get("start_ts") is not None:
                    await self.broadcast_timer_tick()
                if self.countdown_state.get("running"):
                    remaining = self._countdown_remaining()
                    await self.broadcast_countdown_tick(remaining)
                    if remaining <= 0.0 and not self._countdown_finished_signaled:
                        self._countdown_finished_signaled = True
                        self.countdown_state["running"] = False
                        self.countdown_state["finished"] = True
                        self.logger.info(
                            f"Countdown abgelaufen: label='{self.countdown_state.get('label','')}'"
                        )
                        await self.broadcast_countdown_finished()
                        await self.broadcast_countdown_state()
                await asyncio.sleep(1.0)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                self.logger.error(f"timer_tick_loop error: {exc}")
                await asyncio.sleep(1.0)

    # ----- Countdown -----

    def _countdown_remaining(self) -> float:
        cs = self.countdown_state
        if cs.get("finished"):
            return 0.0
        if not cs.get("running"):
            return max(0.0, float(cs.get("remaining_at_pause", 0.0)))
        start = cs.get("start_ts")
        base = float(cs.get("remaining_at_pause", cs.get("duration_seconds", 0.0)))
        if start is None:
            return base
        return max(0.0, base - (time.time() - start))

    def _countdown_snapshot(self) -> dict:
        cs = self.countdown_state
        return {
            "running": cs.get("running", False),
            "start_ts": cs.get("start_ts"),
            "duration_seconds": cs.get("duration_seconds", 0.0),
            "remaining_seconds": self._countdown_remaining(),
            "label": cs.get("label", ""),
            "finished": cs.get("finished", False),
            "server_ts": time.time(),
        }

    def _countdown_start(self, duration_seconds: float, label: str = ""):
        if duration_seconds <= 0:
            self._countdown_reset()
            return
        self.countdown_state = {
            "running": True,
            "start_ts": time.time(),
            "duration_seconds": float(duration_seconds),
            "remaining_at_pause": float(duration_seconds),
            "label": label or "",
            "finished": False,
        }
        self._countdown_finished_signaled = False
        self.logger.info(f"Countdown gestartet: {duration_seconds}s label='{label}'")

    def _countdown_pause(self) -> bool:
        cs = self.countdown_state
        if not cs.get("running"):
            return False
        cs["remaining_at_pause"] = self._countdown_remaining()
        cs["running"] = False
        cs["start_ts"] = None
        return True

    def _countdown_resume(self) -> bool:
        cs = self.countdown_state
        if cs.get("running") or cs.get("finished"):
            return False
        remaining = float(cs.get("remaining_at_pause", 0.0))
        if remaining <= 0:
            return False
        cs["running"] = True
        cs["start_ts"] = time.time()
        return True

    def _countdown_reset(self):
        self.countdown_state = {
            "running": False,
            "start_ts": None,
            "duration_seconds": 0.0,
            "remaining_at_pause": 0.0,
            "label": "",
            "finished": False,
        }
        self._countdown_finished_signaled = False

    async def broadcast_countdown_state(self):
        message = {"type": "countdown_state", "countdown": self._countdown_snapshot()}
        for client_id in list(self.munchlaxes.keys()):
            try:
                await self.send_to_client(client_id, message)
            except Exception as exc:
                self.logger.error(f"broadcast_countdown_state an {client_id} failed: {exc}")

    async def broadcast_countdown_tick(self, remaining: float):
        message = {
            "type": "countdown_tick",
            "remaining_seconds": remaining,
            "running": self.countdown_state.get("running", False),
            "server_ts": time.time(),
        }
        for client_id in list(self.munchlaxes.keys()):
            try:
                await self.send_to_client(client_id, message)
            except Exception as exc:
                self.logger.debug(f"broadcast_countdown_tick an {client_id} failed: {exc}")

    async def broadcast_countdown_finished(self):
        message = {
            "type": "countdown_finished",
            "label": self.countdown_state.get("label", ""),
            "duration_seconds": self.countdown_state.get("duration_seconds", 0.0),
            "server_ts": time.time(),
        }
        for client_id in list(self.munchlaxes.keys()):
            try:
                await self.send_to_client(client_id, message)
            except Exception as exc:
                self.logger.error(f"broadcast_countdown_finished an {client_id} failed: {exc}")

    def _owner_for_client(self, client_id: str) -> str:
        name = self.munchlax_names.get(client_id, "")
        return f"{name}_{client_id}"

    def _find_client_for_owner(self, owner: str) -> str | None:
        # Exakter Match auf voll-qualifizierten Owner ("name_<cid>") — greift in
        # off/versus/nuzlocke, wo _effective_team_membership diesen Format nutzt.
        for cid in self.munchlaxes.keys():
            if self._owner_for_client(cid) == owner:
                return cid
        # Fallback: Match auf blossen Client-Namen. Der Coop-Fallback in
        # _effective_team_membership liefert kurze Namen aus expected_owners
        # ("Stephan" statt "Stephan_<cid>") — ohne diesen Match findet
        # _compute_team_state keinen Client und Badges/Editions bleiben None,
        # das Team-Badge-Overlay zeigt dann nichts.
        for cid in self.munchlaxes.keys():
            if self.munchlax_names.get(cid, "") == owner:
                return cid
        return None

    def _team_stats_for_owner(self, owner: str) -> dict:
        """Aggregiert badges + alive_count aus self.teams für einen Owner."""
        cid = self._find_client_for_owner(owner)
        if cid is None:
            return {"badges": None, "alive_count": None}
        player_ids = self.client_player_ids.get(cid, set())
        badges = None
        alive_count = 0
        found_team = False
        for pid in player_ids:
            team = self.teams.get(pid)
            if not team:
                continue
            found_team = True
            # Team-Array: Index 0-5 = Slots, Index 6 = badges, Index 7 = edition
            if len(team) > 6 and team[6] is not None:
                b = team[6]
                if badges is None or (isinstance(b, int) and b > badges):
                    badges = b
            for slot in team[:6]:
                if slot is None:
                    continue
                dex = getattr(slot, "dexnr", None) or (slot.get("dexnr") if isinstance(slot, dict) else None)
                if dex:
                    alive_count += 1
        if not found_team:
            return {"badges": None, "alive_count": None}
        return {"badges": badges, "alive_count": alive_count}

    def _recompute_versus_state(self) -> bool:
        """Rechnet Versus-Aggregate neu. Gibt True zurück wenn sich was geändert hat.

        "versus": Bucket pro Team (team_membership). "versus_ffa": Bucket pro
        Spieler — jeder Owner ist sein eigenes "Team"."""
        cfg = self.soullink_config
        mode = cfg.get("mode")
        if mode not in VERSUS_MODES:
            if self.soullink_versus_state:
                self.soullink_versus_state = {}
                return True
            return False
        team_map = cfg.get("team_membership", {}) or {}
        expected = cfg.get("expected_owners", []) or []
        new_state: dict[str, dict] = {}
        for owner in expected:
            team = owner if mode == "versus_ffa" else team_map.get(owner)
            if not team:
                continue
            bucket = new_state.setdefault(team, {
                "badges_min": None,
                "badges_by_owner": {},
                "alive_count": 0,
                "alive_by_owner": {},
                "deaths": 0,
                "deaths_by_owner": {},
                "owners": [],
            })
            stats = self._team_stats_for_owner(owner)
            bucket["owners"].append(owner)
            bucket["badges_by_owner"][owner] = stats["badges"]
            if stats["badges"] is not None:
                if bucket["badges_min"] is None or stats["badges"] < bucket["badges_min"]:
                    bucket["badges_min"] = stats["badges"]
            if stats["alive_count"] is not None:
                bucket["alive_by_owner"][owner] = stats["alive_count"]
                bucket["alive_count"] += stats["alive_count"]
            # Deaths pro Owner aus soullink_deaths ableiten
            own_deaths = sum(1 for (_pv, o) in self.soullink_deaths.keys() if o == owner)
            bucket["deaths_by_owner"][owner] = own_deaths
            bucket["deaths"] += own_deaths
        if new_state == self.soullink_versus_state:
            return False
        self.soullink_versus_state = new_state
        return True

    async def broadcast_soullink_versus_state(self):
        message = {
            "type": "soullink_versus_state",
            "state": self.soullink_versus_state,
            "battles": list(self.soullink_versus_battles),
        }
        for client_id in list(self.munchlaxes.keys()):
            try:
                await self.send_to_client(client_id, message)
            except Exception as exc:
                self.logger.error(f"broadcast_soullink_versus_state an {client_id} failed: {exc}")

    def _effective_team_membership(self) -> dict[str, str]:
        """owner → team_id. Bei aktivem Soullink aus config; sonst jeder Owner
        sein eigenes Team. Für Standard-Nuzlocke/Off ergibt sich {owner: owner}
        aus verbundenen Clients + expected_owners."""
        cfg = self.soullink_config
        mode = cfg.get("mode", "off")
        if mode == "coop":
            raw = cfg.get("team_membership", {}) or {}
            if raw:
                return {o: slug_team_id(t) for o, t in raw.items()}
            # Coop-Fallback: NuzlockeMenu fuellt team_membership derzeit nur im
            # versus-Zweig. Ohne diese Zuordnung waere das gesamte Team-Feature
            # fuer den Coop-Kern-Anwendungsfall wirkungslos. Alle expected_owners
            # in einen gemeinsamen Bucket 'coop' schuetten.
            return {o: "coop" for o in (cfg.get("expected_owners", []) or []) if o}
        if mode == "versus":
            raw = cfg.get("team_membership", {}) or {}
            return {o: slug_team_id(t) for o, t in raw.items()}
        if mode == "versus_ffa":
            return {o: slug_team_id(o) for o in (cfg.get("expected_owners", []) or [])}
        owners: set[str] = set()
        for cid in self.munchlaxes.keys():
            try:
                o = self._owner_for_client(cid)
                if o:
                    owners.add(o)
            except Exception:
                pass
        for o in (cfg.get("expected_owners", []) or []):
            if o:
                owners.add(o)
        # Zusätzlich alle jemals gesehenen Owner aus dem Team-Owner-Cache. Sonst
        # verschwindet bei kurzem Disconnect eines Owners im off-Modus der
        # gesamte Team-Bucket aus dem State — für ein Regie-/Spectator-Overlay
        # sieht das wie "Team weg" aus statt "Werte unverändert".
        for o in self._team_owner_cache.keys():
            if o:
                owners.add(o)
        return {o: slug_team_id(o) for o in owners}

    def _prune_team_owner_cache(self):
        """Entfernt Cache-Einträge deren Grace-Period abgelaufen ist. Owner die
        aktuell verbunden oder in expected_owners sind bekommen frisches
        last_seen; alle anderen sterben nach TEAM_OWNER_CACHE_GRACE_SECONDS.
        """
        now = time.time()
        connected_owners: set[str] = set()
        for cid in list(self.munchlaxes.keys()):
            try:
                # Beide Namensformate ins alive-Set: voll-qualifiziert ("name_<cid>",
                # off/versus/nuzlocke) und blosser Name ("name", coop-Fallback via
                # expected_owners). Sonst prunen wir gerade den Coop-Cache weg.
                full = self._owner_for_client(cid)
                if full:
                    connected_owners.add(full)
                short = self.munchlax_names.get(cid, "")
                if short:
                    connected_owners.add(short)
            except Exception:
                pass
        expected = set(self.soullink_config.get("expected_owners", []) or [])
        alive = connected_owners | expected
        for owner in list(self._team_owner_cache.keys()):
            entry = self._team_owner_cache[owner]
            if owner in alive:
                entry["last_seen"] = now
                continue
            if now - entry.get("last_seen", now) > TEAM_OWNER_CACHE_GRACE_SECONDS:
                del self._team_owner_cache[owner]

    def _compute_team_state(self) -> dict[str, dict]:
        """Baut team_state aus effektiver Membership + self.teams. Owner-Rohdaten,
        Aggregation (AND, Region) macht das Overlay.

        Fällt für disconnected/blip-artige Owner auf `self._team_owner_cache`
        zurück, damit das Team-Badge-Overlay nicht bei jedem Heartbeat-Aussetzer
        alle Regionen auf 0 kippt.
        """
        self._prune_team_owner_cache()
        membership = self._effective_team_membership()
        new_state: dict[str, dict] = {}
        now = time.time()
        for owner, team_id in membership.items():
            bucket = new_state.setdefault(team_id, {
                "team_id": team_id,
                "owners": [],
                "badges_by_owner": {},
                "editions_by_owner": {},
                "player_ids_by_owner": {},
            })
            if owner in bucket["owners"]:
                continue
            bucket["owners"].append(owner)
            cid = self._find_client_for_owner(owner)
            if cid is None:
                self.logger.debug(
                    f"_compute_team_state: kein aktiver Client für Owner '{owner}' "
                    f"(Team '{team_id}') — Werte aus _team_owner_cache"
                )
            player_ids = sorted(self.client_player_ids.get(cid, set())) if cid else []
            bucket["player_ids_by_owner"][owner] = player_ids
            badges_val = None
            edition_val = None
            for pid in player_ids:
                team = self.teams.get(pid)
                if not team:
                    continue
                if len(team) > 6 and team[6] is not None and isinstance(team[6], int):
                    if badges_val is None or team[6] > badges_val:
                        badges_val = team[6]
                if len(team) > 7 and team[7] is not None:
                    edition_val = team[7]
            cached = self._team_owner_cache.setdefault(owner, {"badges": None, "edition": None, "last_seen": now})
            if badges_val is not None:
                cached["badges"] = badges_val
            if edition_val is not None:
                cached["edition"] = edition_val
            if cid is not None:
                cached["last_seen"] = now
            bucket["badges_by_owner"][owner] = badges_val if badges_val is not None else cached["badges"]
            bucket["editions_by_owner"][owner] = edition_val if edition_val is not None else cached["edition"]
        return new_state

    def _recompute_team_state(self) -> bool:
        """True wenn sich state geändert hat."""
        new_state = self._compute_team_state()
        if new_state == self.soullink_team_state:
            return False
        self.soullink_team_state = new_state
        return True

    async def broadcast_soullink_team_state(self):
        message = {
            "type": "soullink_team_state",
            "state": self.soullink_team_state,
        }
        for client_id in list(self.munchlaxes.keys()):
            try:
                await self.send_to_client(client_id, message)
            except Exception as exc:
                self.logger.error(f"broadcast_soullink_team_state an {client_id} failed: {exc}")

    async def broadcast_soullink_versus_battle(self, battle: dict):
        message = {"type": "soullink_versus_battle", "battle": battle}
        for client_id in list(self.munchlaxes.keys()):
            try:
                await self.send_to_client(client_id, message)
            except Exception as exc:
                self.logger.error(f"broadcast_soullink_versus_battle an {client_id} failed: {exc}")

    def _check_first_type_clash(self, link: dict) -> dict | None:
        """Prüft Ersttyp-Kollision zwischen Link-Members. Nur bei state=complete.

        - Config: rule_first_type_clause_linked (default off) muss aktiv sein
        - rule_first_type_clause_static_exception: Members mit method ∈ {static, gift,
          fossil, egg} sind vom Regel-Violation-Broadcast befreit, ABER: erleiden sie
          durch die Zwangsmechanik einen Ersttyp-Match mit einem anderen Member,
          bekommen sie als Kompensation einen Token gutgeschrieben (Task #12-Regel).
        - Kollision: mindestens 2 Members mit gleichem Typ1
        - Guard: link["compensation_paid"] verhindert doppelte Token-Gutschrift
        """
        link_id = link.get("link_id")
        if not self._rule("rule_first_type_clause_linked", False):
            self.logger.info(f"first_type_clash skip: rule inaktiv link={link_id}")
            return None
        if link.get("state") != "complete":
            members_dbg = {
                o: {"dexnr": m.get("dexnr"), "outcome": m.get("outcome")}
                for o, m in link.get("members", {}).items()
            }
            self.logger.info(
                f"first_type_clash skip: link={link_id} state={link.get('state')} "
                f"members={members_dbg}"
            )
            return None
        static_exempt = self._rule("rule_first_type_clause_static_exception", True)
        exempt_methods = {"static", "gift", "fossil", "egg"} if static_exempt else set()

        # Alle Members mit Typen + Methods sammeln
        member_types: dict[str, tuple[int, str]] = {}
        for owner, member in link.get("members", {}).items():
            t = first_type(member.get("dexnr"))
            if t is None:
                self.logger.info(
                    f"first_type_clash skip: kein type fuer {owner} "
                    f"dex={member.get('dexnr')} link={link_id}"
                )
                continue
            member_types[owner] = (t, member.get("method", "wild"))

        # Typ-Buckets (alle Owner, unabhängig von Method)
        buckets: dict[int, list[str]] = {}
        for owner, (t, _m) in member_types.items():
            buckets.setdefault(t, []).append(owner)
        colliding_buckets = {t: owners for t, owners in buckets.items() if len(owners) >= 2}
        if not colliding_buckets:
            buckets_map = {type_name(t): owners for t, owners in buckets.items()}
            self.logger.info(
                f"first_type_clash ok: link={link_id} buckets={buckets_map}"
            )
            return None

        # Token-Kompensation: Für jeden Owner in einer Kollision, dessen method
        # exempt ist, gib einen Token (nur einmal pro Link).
        if not link.get("compensation_paid"):
            compensated: list[str] = []
            for t, owners in colliding_buckets.items():
                for owner in owners:
                    _t, method = member_types.get(owner, (0, "wild"))
                    if method in exempt_methods:
                        self._token_earn(owner)
                        compensated.append(owner)
            if compensated:
                link["compensation_paid"] = True
                self.logger.info(
                    f"Ersttyp-Kollision durch geschenkte Pokemon → Token-Kompensation "
                    f"für {compensated} in Link {link['link_id']}"
                )
                # Token-State broadcast asynchron aus dem Aufrufer-Kontext getriggert:
                # setze Flag, damit dieser Aufruf-Rahmen die Broadcast-Task erstellt.
                link["_compensation_broadcast_pending"] = True

        # Violation-Broadcast: Kollisionen zwischen NICHT-exempt Members zählen als Regelbruch.
        clashing_non_exempt: dict[int, list[str]] = {}
        for t, owners in colliding_buckets.items():
            non_exempt = [o for o in owners if member_types.get(o, (0, "wild"))[1] not in exempt_methods]
            if len(non_exempt) >= 2:
                clashing_non_exempt[t] = non_exempt
        if not clashing_non_exempt:
            colliding_owners = {
                type_name(t): owners for t, owners in colliding_buckets.items()
            }
            self.logger.info(
                f"first_type_clash silent: link={link_id} kollision aber alle exempt "
                f"owners={colliding_owners}"
            )
            return None

        parts = [
            f"{type_name(t)}: {', '.join(owners)}"
            for t, owners in clashing_non_exempt.items()
        ]
        subject = f"link_{link['link_id']}"
        return {
            "type": "first_type_clash",
            "subject": subject,
            "link_id": link["link_id"],
            "clashes": clashing_non_exempt,
            "timestamp": time.time(),
            "message": f"Ersttyp-Kollision im Link {link['link_id']}: " + " | ".join(parts),
        }

    def _rule(self, key: str, default):
        """Kurzform: liest rule_* aus soullink_config['rules'] oder Fallback default.

        Server-Config speichert Rules nicht separat — sie liegen im Client-Config
        (nuz-dict). Client sollte sie als Teil von soullink_config['rules'] mitsenden;
        Fallback default wenn Feld fehlt.
        """
        rules = self.soullink_config.get("rules") if isinstance(self.soullink_config, dict) else None
        if isinstance(rules, dict) and key in rules:
            return rules[key]
        return default

    def _token_state_for(self, owner: str) -> dict:
        return self.soullink_tokens.setdefault(owner, {
            "earned": 0, "used": 0, "active_route": None, "active_edition": None,
        })

    def _token_earn(self, owner: str) -> bool:
        if not owner:
            return False
        st = self._token_state_for(owner)
        st["earned"] = int(st.get("earned", 0)) + 1
        self.logger.info(f"Token-Earn: {owner} → earned={st['earned']}")
        return True

    def _token_redeem(self, owner: str, edition, route) -> bool:
        if not owner or route is None:
            return False
        st = self._token_state_for(owner)
        available = int(st.get("earned", 0)) - int(st.get("used", 0))
        if available <= 0:
            self.logger.warning(f"Token-Redeem abgelehnt: {owner} keine freien Tokens")
            return False
        st["used"] = int(st.get("used", 0)) + 1
        st["active_route"] = int(route)
        st["active_edition"] = edition
        self.logger.info(
            f"Token-Redeem: {owner} edition={edition} route={route} "
            f"(remaining={int(st['earned']) - int(st['used'])})"
        )
        return True

    def _token_consume_if_matches(self, owner: str, edition, route) -> bool:
        """Wird beim Verarbeiten eines Encounters aufgerufen: passt Route+Edition
        zum aktiven Token-Credit, verbrauche den Credit und gebe True zurück.
        """
        if not owner or route is None:
            return False
        st = self.soullink_tokens.get(owner)
        if not st:
            return False
        active_route = st.get("active_route")
        active_edition = st.get("active_edition")
        if active_route is None:
            return False
        if int(active_route) != int(route):
            return False
        if active_edition is not None and edition is not None:
            try:
                if int(active_edition) != int(edition):
                    return False
            except (ValueError, TypeError):
                pass
        st["active_route"] = None
        st["active_edition"] = None
        self.logger.info(f"Token-Credit eingelöst: {owner} route={route}")
        return True

    async def broadcast_soullink_tokens(self):
        message = {"type": "soullink_tokens", "tokens": dict(self.soullink_tokens)}
        for client_id in list(self.munchlaxes.keys()):
            try:
                await self.send_to_client(client_id, message)
            except Exception as exc:
                self.logger.error(f"broadcast_soullink_tokens an {client_id} failed: {exc}")

    def _detect_trade(self, enc: dict) -> dict | None:
        """Erkennt Owner-Wechsel eines PID → wahrscheinlicher Trade (bei Soullink verboten).

        Regel: Wenn (personality, some_owner) im Encounter-Cache existiert und der neue
        Encounter denselben personality-Wert unter einem anderen Owner meldet, ist das
        Owner-Umzug. Cross-owner PID-Kollisionen sind selten genug, dass wir das als
        Trade-Indikator behandeln.
        """
        if self.soullink_config.get("mode", "off") not in SOULLINK_MODES:
            return None
        pv = enc.get("personality")
        new_owner = enc.get("owner")
        if pv is None or not new_owner:
            return None
        for (existing_pv, existing_owner), existing in self.encounters.items():
            if existing_pv != pv or existing_owner == new_owner:
                continue
            # gleicher PID unter anderem Owner
            now_trade = time.time()
            violation = {
                "type": "trade",
                "subject": f"{pv}",
                "old_owner": existing_owner,
                "new_owner": new_owner,
                "dexnr": enc.get("dexnr"),
                "timestamp": now_trade,
                "_server_ts": now_trade,
                "message": (
                    f"PID {pv} taucht bei {new_owner} auf, war zuvor bei {existing_owner}. "
                    f"Trades bei aktiver Soullink-Session sind verboten."
                ),
            }
            key = ("trade", str(pv))
            if key in self.soullink_rule_violations:
                return None
            self.soullink_rule_violations[key] = violation
            self.logger.warning(
                f"Soullink-Trade-Warnung: PID={pv} {existing_owner} -> {new_owner}"
            )
            return violation
        return None

    async def broadcast_soullink_rule_violation(self, violation: dict):
        key = (violation.get("type", "unknown"), str(violation.get("subject", "")))
        # Kurzfenster-Dedupe: dieselbe (type, subject)-Violation innerhalb von
        # 5s nur einmal broadcasten. Ohne diesen Guard würden Client-Echoes
        # (n Munchlaxes bewerten dasselbe fremde Team) n Popups pro Maschine
        # triggern. Fix in _persist_teams stoppt die Cascade an der Quelle,
        # diese Server-seitige Dedupe ist Defense-in-Depth für Race- und
        # Legacy-Client-Fälle.
        now = time.time()
        last_ts = self._last_violation_broadcast_ts.get(key, 0.0)
        if now - last_ts < 5.0:
            self.logger.info(
                f"broadcast_soullink_rule_violation skip (dedupe <5s): key={key}"
            )
            return
        self._last_violation_broadcast_ts[key] = now
        # Server-Timestamp zusaetzlich zum Client-timestamp: der Replay-TTL-Filter
        # misst gegen _server_ts, nicht gegen den Client-Wert — sonst wuerde ein
        # Client mit Clock-Skew (>5min) entweder echte frische Violations
        # verlieren oder alte als frisch durchlassen. _server_ts bleibt im
        # ausgehenden message-Dict mitgeschickt, Clients ignorieren das Feld.
        violation["_server_ts"] = now
        self.soullink_rule_violations[key] = violation
        message = {"type": "soullink_rule_violation", "violation": violation}
        self.logger.info(
            f"rule_violation broadcast type={violation.get('type')} "
            f"subject={violation.get('subject')} clients={len(self.munchlaxes)}"
        )
        for client_id in list(self.munchlaxes.keys()):
            try:
                await self.send_to_client(client_id, message)
            except Exception as exc:
                self.logger.error(f"broadcast_soullink_rule_violation an {client_id} failed: {exc}")

    async def broadcast_wipe_dismissed(self, payload: dict):
        """Verteilt einen wipe_dismissed-Event an alle Clients.

        Kein Server-seitiges Team-Filtering: der Empfaenger vergleicht selbst
        die soullink_team_membership. Das haelt den Server generisch und
        vermeidet Race-Conditions zwischen Config-Reload und Broadcast.
        """
        message = {"type": "wipe_dismissed",
                    "subject_pid": payload.get("subject_pid"),
                    "timestamp": payload.get("timestamp")}
        for client_id in list(self.munchlaxes.keys()):
            try:
                await self.send_to_client(client_id, message)
            except Exception as exc:
                self.logger.error(f"broadcast_wipe_dismissed an {client_id} failed: {exc}")

    async def broadcast_soullink_death(self, death: dict):
        message = {"type": "soullink_death", "death": death}
        for client_id in list(self.munchlaxes.keys()):
            try:
                await self.send_to_client(client_id, message)
            except Exception as exc:
                self.logger.error(f"broadcast_soullink_death an {client_id} failed: {exc}")
        # Falls Death eine Link-Group auf failed gesetzt hat: Link-State ebenfalls broadcasten.
        link_id = death.get("link_id")
        if link_id is not None and link_id in self.soullink_links:
            await self.broadcast_soullink_link_state(self.soullink_links[link_id])

    async def broadcast_bag_sync(self, sender_id, data):
        for client_id in list(self.munchlaxes.keys()):
            if client_id == sender_id:
                continue
            try:
                await self.send_to_client(client_id, data)
            except Exception as exc:
                self.logger.error(f"broadcast_bag_sync an {client_id} failed: {type(exc)},{exc}")
                self.logger.error(f"{traceback.format_exc()}")

    async def broadcast_rando_moves_sync(self, sender_id, player_id: int):
        """Verteilt die randomisierten TM/HM-Zuordnungen eines Spielers an alle
        anderen Clients. Der Sender hat die Daten bereits lokal, deshalb skip.
        """
        moves = self.rando_moves.get(player_id)
        if not moves:
            return
        message = {
            "type": "rando_moves_sync",
            "player_id": int(player_id),
            "tm_moves": moves.get("tm_moves", {}),
            "hm_moves": moves.get("hm_moves", {}),
        }
        for client_id in list(self.munchlaxes.keys()):
            if client_id == sender_id:
                continue
            try:
                await self.send_to_client(client_id, message)
            except Exception as exc:
                self.logger.error(
                    f"broadcast_rando_moves_sync an {client_id} failed: {type(exc)},{exc}"
                )

    async def disconnect_client(self, client_id,
                                 writer: asyncio.StreamWriter | None = None,
                                 reason: str = "unknown"):
        """Trennt den Client mit der angegebenen client_id.

        ``writer``: Der Writer der terminierenden Verbindung. Wird von
        ``handle_munchlax`` mitgegeben, damit der Entry-Identity-Guard
        einen Reconnect-Race erkennt (Reviewer Runde 2 KRITISCH):
        zwischen Loop-Break und Entry hier kann ein Reconnect mit
        derselben client_id ``self.munchlaxes[client_id]`` bereits durch
        den neuen Writer ersetzt haben (der Handshake in
        ``handle_munchlax`` lauft ohne ``disconnect_lock``). Wird
        ``writer`` uebergeben und stimmt nicht mit dem aktuell
        registrierten ueberein, brechen wir sofort ab — die
        Reconnect-Verbindung bleibt bestehen.

        ``check_heartbeats`` uebergibt seit Runde 3 ebenfalls einen
        Snapshot-Writer (``self.munchlaxes.get(client_id)`` zum
        Auswertungs-Zeitpunkt), damit der Entry-Guard auch dort einen
        Reconnect zwischen Snapshot und Cleanup erkennt und den frischen
        Socket nicht faelschlich kappt. Ist der Snapshot ``None`` (Client
        schon verschwunden), degradiert der Guard graceful: ``current``
        ist dann ebenfalls None und die Funktion returned frueh.

        ``reason``: Diagnose-Enum fuer Post-Mortem-Logs. Werte:
        ``explicit_disconnect`` (Client hat 'disconnect' geschickt),
        ``heartbeat_timeout`` (check_heartbeats hat Miss-Limit ueberschritten),
        ``slot_collision``, ``recv_<ExcType>`` (Socket-Fehler im Handler-
        Loop), ``recv_unexpected_<ExcType>`` (unerwartete Exception),
        ``unknown`` (Fallback).
        """
        async with self.disconnect_lock:
            current = self.munchlaxes.get(client_id)
            if current is None:
                return
            if writer is not None and current is not writer:
                self.logger.info(
                    f"disconnect_client({client_id}, reason={reason!r}): "
                    f"aufrufende Verbindung wurde bereits durch Reconnect "
                    f"ersetzt — State-Cleanup uebersprungen"
                )
                # Stale Writer trotzdem schliessen — GC/__del__-Finalizer sind
                # keine verlaesslichen Cleanup-Kanaele fuer offene Sockets.
                # Wir tun das NUR fuer den mitgegebenen (alten) Writer, nicht
                # fuer current (= die neue Reconnect-Verbindung).
                try:
                    writer.close()
                except Exception as exc:
                    self.logger.warning(
                        f"stale writer.close() für {client_id} failed: "
                        f"{type(exc).__name__},{exc}"
                    )
                return
            # Ab hier ist der zu schliessende Writer bekannt und
            # identisch zum aktuell registrierten (oder writer=None:
            # Aufrufer will explizit die aktuell registrierte
            # Verbindung terminieren, z.B. Heartbeat-Timeout).
            writer = current
            try:
                writer.close()
            except Exception as exc:
                self.logger.warning(
                    f"writer.close() für {client_id} failed: {type(exc).__name__},{exc}"
                )
            # wait_closed haengt unter Windows bei Netzwerkabbruch
            # (WinError 121 "Semaphore-Timeout") minutenlang und blockiert
            # den disconnect_lock — Folge: Cascade-Disconnects stauen
            # sich, neue Verbindungen werden nicht angenommen. 2 s
            # Timeout ist mehr als genug fuer sauberes FIN-Handshake.
            try:
                await asyncio.wait_for(writer.wait_closed(), timeout=2.0)
            except asyncio.TimeoutError:
                self.logger.warning(
                    f"wait_closed timeout für {client_id} — Socket wird "
                    f"aufgegeben, State wird trotzdem entfernt"
                )
            except Exception as exc:
                self.logger.warning(
                    f"wait_closed für {client_id} failed: {type(exc).__name__},{exc}"
                )
            # Tail-Guard: waehrend des 2s-Fensters von wait_closed kann
            # ein Reconnect passiert sein (Handshake laeuft ohne
            # disconnect_lock). Nur cleanup wenn writer noch identisch —
            # andernfalls wuerde dieser del-Block den State der frischen
            # Reconnect-Verbindung wegloeschen.
            if self.munchlaxes.get(client_id) is writer:
                # Diagnose-Dump bevor State weg ist. Hilft bei nachtraeglicher
                # Session-Rekonstruktion (Satougame-Pattern vom 2026-09-30:
                # vorher gab's nur "disconnected." ohne Grund und ohne Dauer).
                sess = self._client_sessions.get(client_id, {})
                started = sess.get("started_at", 0)
                dur = time.time() - started if started else 0.0
                last_at = sess.get("last_recv_at", 0)
                last_age = time.time() - last_at if last_at else -1.0
                self.logger.info(
                    f"Client {client_id} ({self.munchlax_names.get(client_id, '')}) "
                    f"disconnected (grund={reason!r}, dauer={dur:.1f}s, "
                    f"recv={sess.get('recv', 0)}, sent={sess.get('sent', 0)}, "
                    f"letzte_msg={sess.get('last_recv_type', '')!r} "
                    f"vor_{last_age:.1f}s)"
                )
                # Defensiv .pop(..., None): schon einmal (Slot-Collision
                # + Frueh-Disconnect) hat ein fehlender Heartbeat-Key hier
                # KeyError geworfen und den gesamten Cleanup + Broadcasts
                # verschluckt. Alle State-Dicts konsistent robust halten.
                self.munchlaxes.pop(client_id, None)
                self.munchlax_names.pop(client_id, None)
                self.munchlax_status.pop(client_id, None)
                self.munchlax_heartbeats.pop(client_id, None)
                self.heartbeat_counts.pop(client_id, None)
                self.writer_locks.pop(client_id, None)
                self.client_player_ids.pop(client_id, None)
                self.declared_player_ids_by_client.pop(client_id, None)
                self._client_sessions.pop(client_id, None)
            else:
                self.logger.info(
                    f"Client {client_id} disconnect (reason={reason!r}): "
                    f"Writer wurde waehrend "
                    f"wait_closed durch Reconnect ersetzt — State-Cleanup "
                    f"uebersprungen"
                )
        asyncio.create_task(self.broadcast_connection_status())
        asyncio.create_task(self.broadcast_player_names())
        if self._recompute_team_state():
            asyncio.create_task(self.broadcast_soullink_team_state())
        if self._recompute_versus_state():
            asyncio.create_task(self.broadcast_soullink_versus_state())

    # async def send_message(self, writer, message):
    #     serialized_message = pickle.dumps(message)
    #     length = len(serialized_message).to_bytes(4, 'big')
    #     writer.write(length)
    #     await writer.drain()
    #     writer.write(serialized_message)
    #     await writer.drain()
    
    async def send_message(self, writer, message):
        serialized_message = pickle.dumps(message)
        CHUNK_SIZE = 500  # Die Größe jedes Chunks in Bytes

        # Gesamtlänge der Nachricht senden
        msg_type = message.get("type", "teams") if isinstance(message, dict) else type(message).__name__
        chunk_count = (len(serialized_message) + CHUNK_SIZE - 1) // CHUNK_SIZE
        self.logger.debug(f"Sende Nachricht: type={msg_type}, {len(serialized_message)} Bytes, {chunk_count} Chunks")
        length = len(serialized_message).to_bytes(4, 'big')
        writer.write(length)
        await writer.drain()

        # Nachricht in Chunks senden
        for i in range(0, len(serialized_message), CHUNK_SIZE):
            chunk = serialized_message[i:i+CHUNK_SIZE]
            # Größe des aktuellen Chunks senden
            chunk_length = len(chunk).to_bytes(4, 'big')
            writer.write(chunk_length)
            await writer.drain()
            # Chunk senden
            writer.write(chunk)
            await writer.drain()


    # async def receive_message(self, reader):
    #     message_length = int.from_bytes(await reader.read(4), 'big')
    #     message = await reader.read(message_length)

    #     return pickle.loads(message)

    async def receive_message(self, reader):
        # readexactly statt read: read(N) liefert nur "bis zu" N Bytes — bei
        # grossen Pickles (z.B. Gen 6/7 Boxes-Update ~210 KB in 500-Byte-Chunks)
        # werden TCP-Pakete fragmentiert, der Stream desynchronisiert sich und
        # das Pickle bricht mit "invalid load key". readexactly garantiert
        # genau N Bytes oder wirft IncompleteReadError (vom Caller gefangen).
        total_length = int.from_bytes(await reader.readexactly(4), 'big')
        message = b''

        while len(message) < total_length:
            chunk_length = int.from_bytes(await reader.readexactly(4), 'big')
            chunk = await reader.readexactly(chunk_length)
            message += chunk

        return pickle.loads(message)
    
    async def broadcast_connection_status(self):
        status = {cid: self.munchlax_status.get(cid, "connected") for cid in self.munchlaxes}
        names = dict(self.munchlax_names)
        message = {"type": "connection_status", "status": status, "names": names}
        for client_id in list(self.munchlaxes.keys()):
            try:
                await self.send_to_client(client_id, message)
            except Exception as exc:
                self.logger.error(f"broadcast_connection_status an {client_id} failed: {exc}")

    async def broadcast_player_names(self):
        names: dict[int, str] = {}
        for cid, player_ids in self.client_player_ids.items():
            client_name = self.munchlax_names.get(cid, "")
            for pid in player_ids:
                names[pid] = client_name
        # client_names ist unabhaengig von player_ids gefuellt — auch vor dem
        # ersten Team-Empfang (kein BizHawk verbunden). Nuzlocke-Menue nutzt es
        # fuer "Aus Clients uebernehmen", wo die Namen der verbundenen Clients
        # reichen, auch ohne dass ein Spielstand geladen wurde.
        message = {
            "type": "player_names",
            "names": names,
            "client_names": dict(self.munchlax_names),
        }
        for client_id in list(self.munchlaxes.keys()):
            try:
                await self.send_to_client(client_id, message)
            except Exception as exc:
                self.logger.error(f"broadcast_player_names an {client_id} failed: {exc}")

    async def check_heartbeats(self):
        while True:
            # Try/except um den gesamten Iterations-Body, weil disconnect_client
            # synchrone _recompute_team_state/_recompute_versus_state aufruft.
            # Eine Exception dort darf den Heartbeat-Monitor nicht dauerhaft killen
            # (sonst laufen tote Clients ewig weiter, siehe Sync-Review Runde 6).
            try:
                now = time.time()
                # (client_id, writer) im Snapshot mitfangen: zwischen Snapshot
                # und disconnect_client-Aufruf kann der Client bereits neu
                # reconnected haben. Ohne Writer-Snapshot wuerde der
                # Entry-Guard in disconnect_client leer laufen ("aktuell
                # registriert = neuer Writer") und den frisch verbundenen
                # Socket killen (sync-reviewer R3).
                to_disconnect: list[tuple[str, "asyncio.StreamWriter | None"]] = []
                for client_id, last_heartbeat in list(self.munchlax_heartbeats.items()):
                    if now - last_heartbeat > HEARTBEAT_STALE_SECONDS:
                        # Aktueller Miss-Zaehler NACH dem Inkrement unten.
                        # Disconnect erfolgt sobald counts > MISS_LIMIT,
                        # also ab Miss MISS_LIMIT+1. Log gibt beides an,
                        # damit "Miss 4/4 → Disconnect" nicht verwirrend
                        # als "4 von 3" auftaucht (sync-reviewer R7).
                        next_miss = self.heartbeat_counts[client_id] + 1
                        sess = self._client_sessions.get(client_id, {})
                        last_type = sess.get("last_recv_type", "")
                        last_at = sess.get("last_recv_at", 0)
                        last_age = now - last_at if last_at else -1.0
                        self.logger.warning(
                            f"Client {client_id} "
                            f"({self.munchlax_names.get(client_id, '')}) hat seit "
                            f"{now - last_heartbeat:.1f}s keinen Heartbeat "
                            f"gesendet (Miss {next_miss}, "
                            f"Disconnect ab {HEARTBEAT_MISS_LIMIT + 1}, "
                            f"letzte_msg={last_type!r} vor_{last_age:.1f}s)"
                        )
                        self.heartbeat_counts[client_id] += 1
                        if self.heartbeat_counts[client_id] > HEARTBEAT_MISS_LIMIT:
                            to_disconnect.append(
                                (client_id, self.munchlaxes.get(client_id))
                            )
                        else:
                            self.munchlax_status[client_id] = "warning"
                    else:
                        self.heartbeat_counts[client_id] = 0
                        self.munchlax_status[client_id] = "connected"
                for client_id, snap_writer in to_disconnect:
                    await self.disconnect_client(
                        client_id, writer=snap_writer, reason="heartbeat_timeout"
                    )
                await self.broadcast_connection_status()
                # Periodische Verbindungs-Uebersicht (log-friendly fuer
                # nachtraegliche Session-Rekonstruktion). Zeigt wer wann wie
                # lange verbunden war und welche Message-Rate pro Client lief,
                # plus Replay-Diagnose (last_seen_seq, letzte_msg_vor).
                if now - self._last_overview_at >= CONNECTED_OVERVIEW_SECONDS:
                    self._last_overview_at = now
                    if self.munchlaxes:
                        lines = []
                        for cid in list(self.munchlaxes.keys()):
                            s = self._client_sessions.get(cid, {})
                            dur = now - s.get("started_at", now)
                            last_at = s.get("last_recv_at", 0)
                            last_age = now - last_at if last_at else -1.0
                            lines.append(
                                f"{self.munchlax_names.get(cid, '')}({cid[:8]}): "
                                f"up={dur:.0f}s "
                                f"recv={s.get('recv', 0)} sent={s.get('sent', 0)} "
                                f"last_seq={self._last_seen_seq_by_client.get(cid, 0)} "
                                f"last_msg={s.get('last_recv_type', '')!r} "
                                f"vor_{last_age:.0f}s "
                                f"status={self.munchlax_status.get(cid, '?')}"
                            )
                        self.logger.info(
                            f"Verbindungs-Uebersicht "
                            f"({len(self.munchlaxes)} Clients): " + " | ".join(lines)
                        )
                    else:
                        self.logger.info("Verbindungs-Uebersicht: keine Clients verbunden")
                # Dedup-Fenster entleeren damit Summaries nicht in stillen
                # Perioden verschluckt werden.
                self._flush_dedup_stale()
            except Exception as exc:
                self.logger.error(f"check_heartbeats Iteration abgebrochen: {type(exc)},{exc}")
                self.logger.error(f"{traceback.format_exc()}")
            await asyncio.sleep(5)
    
    async def start(self):
        # Port frisch aus rem-Dict lesen. Nach Session-Wechsel wird
        # rem['client_port'] in-place aktualisiert, aber self.port trägt noch
        # den beim __init__ kopierten Startwert — ohne Sync öffnet der Socket
        # auf dem alten Port. Analog Bizhawk.start.
        self.port = self.rem.get('client_port', self.port)
        self.server = await asyncio.start_server(
            self.handle_munchlax, self.host, self.port)

        self.logger.info(f"Arceus auf Port {self.port} gestartet")
        self.heartbeattask = asyncio.create_task(self.check_heartbeats())
        self.timer_task = asyncio.create_task(self.timer_tick_loop())
        self.is_connected = 'connected'

        async with self.server:
            await self.server.serve_forever()

    async def start_helper_listener(self, sp: dict, configsave, save_callback=None):
        helper_port = int(self.rem.get('helper_port', int(self.port) + 1))
        self.helper_server = await asyncio.start_server(
            lambda r, w: self._handle_helper(r, w, sp, configsave, save_callback),
            self.host, helper_port)
        self.logger.info(f"Helper-Listener auf Port {helper_port} gestartet")

    async def _handle_helper(self, reader, writer, sp, configsave, save_callback):
        try:
            data = await self.receive_message(reader)
            if isinstance(data, dict) and data.get("type") == "sprite_path_obs":
                for key, value in data.items():
                    if key != "type":
                        sp[key] = value
                if save_callback:
                    save_callback()
                await self.send_message(writer, "ok")
                self.logger.info(f"OBS-Sprite-Pfade vom Helper empfangen und gespeichert.")
            else:
                await self.send_message(writer, "error")
                self.logger.warning(f"Unbekannte Helper-Nachricht: {data}")
        except Exception as err:
            self.logger.error(f"Fehler im Helper-Handler: {err}")
        finally:
            writer.close()
            await writer.wait_closed()

    async def stop_helper_listener(self):
        if hasattr(self, 'helper_server') and self.helper_server:
            self.helper_server.close()
            await self.helper_server.wait_closed()
            self.helper_server = None
            self.logger.info("Helper-Listener gestoppt.")

    async def stop(self):
        await self.stop_helper_listener()
        if self.server:
            self.heartbeattask.cancel()
            if self.timer_task is not None:
                self.timer_task.cancel()
                self.timer_task = None
            self.server.close()
            await self.server.wait_closed()
            self.server = None
            self.is_connected = False
            self.logger.info("Arceus has been stopped.")
            self.port = self.rem['client_port']
