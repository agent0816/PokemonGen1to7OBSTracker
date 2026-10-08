import asyncio
import hashlib
import os
import pickle
import random
import threading
import time
import traceback
import uuid
from collections import deque
from pathlib import Path
from pickle import UnpicklingError
from backend.bag_decoder import BagItem
from backend.classes.obs import OBS
from backend.classes.pokedex_db import PokedexDB
from backend.controller.run_manager import RunManager
from backend.logging_setup import get_logger

# Mindestabstand zwischen automatischen Box-Refreshs pro Spieler.
# Verhindert Box-Read-Stürme z.B. bei Team-Reorder-Spam oder Evolutionen.
BOX_REFRESH_THROTTLE_SECONDS = 5.0

# Netzwerk-Exceptions, bei denen weitere Reads/Writes keinen Sinn mehr
# ergeben (Socket ist tot). Werden explizit gefangen, damit der Loop
# break'd statt in Dauerschleife denselben Error zu loggen — klassischer
# Satougame-Fall vom 2026-09-30: IncompleteReadError erbt von EOFError,
# EOFError wurde nur geloggt ohne break → ~1000 Fehler/sec bis OOM/Rotation.
TRANSIENT_NET_EXCEPTIONS = (
    asyncio.IncompleteReadError,
    ConnectionResetError,
    ConnectionAbortedError,
    BrokenPipeError,
    EOFError,
    OSError,
)

# Reconnect-Backoff: exponentiell mit Cap. Jitter 0-2 s gegen Thundering-Herd
# wenn mehrere Clients gleichzeitig vom gleichen Netz-Event erwischt werden.
# Infinite retries — User kann Session jederzeit beenden, aber ein kurzer
# Netz-Dropout darf nicht nach 3 Versuchen den ganzen Run killen.
RECONNECT_DELAYS = [2, 5, 10, 20, 30, 60]
RECONNECT_MAX_DELAY = 60
RECONNECT_JITTER_MAX = 2.0

# Rate-limited Logging: innerhalb dieses Fensters wird jeder Error-Typ
# nur einmal mit vollem Traceback geloggt. Alle weiteren Occurrences
# werden gezählt und beim nächsten erfolgreichen Reconnect (oder nach
# Fenster-Ende) als Zusammenfassung ausgegeben.
LOG_DEDUP_WINDOW_SECONDS = 10.0

# Replay-Buffer-Groesse (Client->Server). Haelt die letzten N Payloads mit
# monoton steigender sequence_id. Nach Reconnect sendet Server die hoechste
# seq, die er gesehen hat; Client resendet alle Buffer-Items mit groesserer
# seq. Dimensionierung: 1 Encounter + 1 Encounter-Outcome + 1 Soullink-
# Config pro ~10 s Spielzeit, 300 Items ~ 50 Minuten Puffer bei typischer
# Rate — deutlich mehr als jeder realistische Disconnect-Zeitraum.
OUTBOUND_BUFFER_MAXLEN = 300

# Message-Typen (Dict-type) + String-Prefixe, die NICHT gebuffert und NICHT
# sequence-nummeriert werden. Heartbeat ist zustandsfrei; declared_player_ids
# wird bei jedem Connect frisch gesendet; resume_query ist Teil des Resume-
# Handshakes selbst und wuerde sich endlos re-queuen; Handshake- und
# disconnect-Strings sind rein verbindungsbezogen; boxes_update ist der
# groesste Payload (~50-150 KB fuer Gen3-PC-Tree) und ist nicht idempotent-
# interessant — nach Reconnect pusht der BizHawk-Tick ohnehin frische Boxen,
# Replay eines alten Snapshots waere veraltet. Rausnehmen spart ~90% des
# Buffer-RAM-Verbrauchs und streckt die 300-Item-Kapazitaet von ~25 min
# (hochaktive Session mit Dauer-Boxes-Spam) auf mehrere Stunden.
UNBUFFERED_DICT_TYPES = frozenset({
    "heartbeat",
    "declared_player_ids",
    "resume_query",
    "boxes_update",
    # rando_moves_sync hat eine eigene Pending-Queue (_pending_rando_moves)
    # mit Flush beim Reconnect. Zusaetzliches Replay via Outbound-Buffer
    # fuehrt zu Doppel-Send (buffer replay + queue flush) und bei spaetem
    # Resume-Replay zu einem alten Payload nach dem frischen Direkt-Send
    # (R2 WARNUNG sync).
    "rando_moves_sync",
})

# Wipe-Detection HP-Consistency: erst nach N Zero-Reads pro Pokemon gilt
# es als tot.
#
# Threshold=1 (2026-08-02): Frueher =5, das kollidiert mit dem Team-Diff-
# Guard in bizhawk.py:update_teams (`if teams[player] == team: return`).
# Bei einem Gen-3-Blackout bleibt die Party ~10-12s auf hp=0 stehen, bevor
# das Spiel automatisch ins Pokemon-Center teleportiert und revived. Der
# Diff-Guard verwirft alle identischen Folge-Reads, so dass nur der einmalige
# Uebergang hp>0 -> hp=0 als Tick durchkommt. Bei Threshold=5 wurde die
# Zero-Read-Zaehler-Schwelle nie erreicht und Death/Wipe blieben stumm.
# Zwei-Schicht-Schutz ist weiter aktiv:
#   1. max_hp-Plausibilitaets-Filter (Battle-RAM-Ausreisser Gen 6/7)
#   2. Initial-Read-Guard (state is None -> False beim allerersten Read)
WIPE_ZERO_READ_THRESHOLD = 1

class Munchlax:
    def __init__(self, host, port, rem, sp, pl, configsave=None, nuz=None):
        self.client_id = rem.get("client_id", 0)
        if self.client_id == 0:
            self.client_id = self.generate_hashed_id()
            rem["client_id"] = self.client_id
        # Thread-Safety-Guard fuer Writes aus dem BH-Thread auf shared State.
        # BizHawk schreibt bizhawk_teams/unsorted_teams/player_names +
        # mark_box_refresh aus seinem eigenen asyncio-Loop (siehe
        # backend/bizhawk_thread.py). Reads kommen aus Kivy — insbesondere
        # Dict-Iterationen (_persist_teams, Overlay-Broadcast, TrainerBox)
        # koennen in `RuntimeError: dict changed size during iteration`
        # laufen, wenn BH gleichzeitig einen neuen Player-Key einfuegt.
        # Writer wrappen jeden strukturellen Write in `with state_lock`,
        # Reader snapshoten den Dict-Zustand unter gleichem Lock und
        # iterieren das Snapshot ausserhalb.
        self.state_lock = threading.Lock()
        self.bizhawk_teams = {}
        self.sorted_teams = {}
        self.unsorted_teams = {}
        self.badges = {}
        self.editions = {}
        self.player_names: dict[int, str] = {}
        # Client-Namen unabhaengig von player_ids — wird vom Server mit jedem
        # player_names-Broadcast mitgeliefert. Nuzlocke-Menue nutzt es fuer
        # "Aus Clients uebernehmen" (funktioniert auch ohne BizHawk-Team).
        self.client_names: dict[str, str] = {}
        self.remote_connection_status: dict[str, str] = {}
        self.remote_connection_names: dict[str, str] = {}
        # PC-Boxen pro Spieler: dict[player_id, list[list[Pokemon|None]]].
        # Auto-Refresh läuft, wenn sich das Team eines lokal bedienten
        # Spielers ändert — gedrosselt via BOX_REFRESH_THROTTLE_SECONDS,
        # sonst weiter on-demand vom BoxMenu.
        self.boxes: dict[int, list] = {}
        # Letzter erfolgreicher (oder gestarteter) Auto-Refresh-Zeitpunkt
        # pro Spieler. Wird vor dem Box-Read gesetzt — fehlgeschlagene Reads
        # sperren den Trigger ebenfalls für die Throttle-Dauer, das ist hier
        # bewusst, um Spam bei dauerhaftem Fehler zu verhindern.
        self.last_box_refresh_at: dict[int, float] = {}
        self.initialized = False
        self.rem = rem
        self.sp = sp
        self.pl = pl
        self.configsave = configsave
        self.nuz = nuz or {}
        self.pokedex_db: PokedexDB | None = None
        self.host = host
        self.port = port
        self.is_connected = False
        self.reconnecting = False  # True während _auto_reconnect läuft (UI-Grace)
        self.obs: OBS | None = None
        self.overlay_server = None
        # Rando-TM/HM-Zuordnungen pro player_id. Multi-Player-Setup:
        # jeder Client randomized seine eigene ROM mit eigenem Seed →
        # jeder Spieler-Slot hat eine andere TM→Move-Zuordnung. Flat-Dict
        # (vorher) brach den Overlay-Resolver fuer Remote-Spieler, weil
        # der Host nur seine EIGENE Zuordnung hatte und die auf alle
        # Teams anwendete.
        # Struktur: {player_id: {tm_num: move_name}}. Werte werden fuer
        # lokale Slots durch _sync_rando_to_munchlax gesetzt und ueber
        # rando_moves_sync an alle anderen Clients verteilt.
        self.rando_tm_moves: dict[int, dict[int, str]] = {}
        self.rando_hm_moves: dict[int, dict[int, str]] = {}
        # Pending-queue fuer rando_moves_sync: wenn beim Randomize keine
        # Verbindung besteht (Standalone, Reconnect-Phase), landet der
        # Payload hier und wird beim naechsten erfolgreichen Connect
        # geflusht. Ohne diese Queue bliebe der Server fuer den
        # betroffenen Slot ohne TM-Mapping (silent-dropout).
        # Key: player_id. Value: {"tm_moves": {...}, "hm_moves": {...}}.
        self._pending_rando_moves: dict[int, dict] = {}
        self._rando_flush_task: asyncio.Task | None = None
        self.rando_abilities_gen3: dict[int, list[int]] | None = None
        # Lazy init in connect(): Lock an aktuellen Loop binden. Im __init__
        # lief Munchlax historisch unter Kivy-Loop; mit Thread-Isolation
        # (Phase 3) laeuft connect/receive_messages auf dem Munchlax-Loop
        # eines dedizierten OS-Threads. asyncio.Lock() in __init__ wuerde
        # an den Kivy-Loop binden und spaeter RuntimeError werfen.
        self.writer_lock: asyncio.Lock | None = None
        self.disconnect_lock: asyncio.Lock | None = None
        # Cross-Thread-Delegation: wird in app.py:build() nach
        # Munchlax-Instanzierung via set_kivy_loop() auf den Kivy-Loop
        # gesetzt. _call_on_kivy / _dispatch_on_kivy posten OBS-/Overlay-
        # /UI-Coros dort hin, auch wenn der Caller im Munchlax-Thread
        # laeuft. None → Fallback auf direktes await (Testkontext).
        self._kivy_loop: asyncio.AbstractEventLoop | None = None
        # Connect-Generation-Counter (R4 WARN): bool-Flag war anfaellig
        # fuer disconnect→connect-Race (B resettet A's Cancel). Jetzt
        # inkrementiert disconnect() den Counter, connect() snapshotet
        # ihn am Entry und bricht ab, sobald die Snapshot-Generation
        # nicht mehr der aktuellen entspricht — jeder in-flight connect
        # kann so sauber abbrechen, auch wenn ein frischer connect schon
        # laeuft.
        self._connect_generation = 0
        # Serialisiert connect()-Aufrufe (R6 KRIT): zwei parallele
        # connects (User-Klick waehrend _auto_reconnect) wuerden sonst
        # self.reader/self.writer gegenseitig clobbern und stale-Handshake-
        # Writes in frische Sockets senden. Lock lazy im Munchlax-Loop
        # angelegt (siehe _ensure_async_locks).
        self._connect_lock: asyncio.Lock | None = None
        # OBS-Serialisierung: der Lock liegt jetzt am OBS-Objekt
        # (`obs._obs_serial`), damit auch OBS.redraw_obs + redraw-Pfade
        # aus settings_controller unter derselben Reihenfolge laufen.
        # Siehe backend/classes/obs.py.
        # Referenz auf den eigenen MunchlaxThread; via set_own_thread()
        # von app.py gesetzt. submit_cross_thread/dispatch_cross_thread
        # posten damit Coros aus Fremd-Threads (z.B. CitraHandler aus
        # Kivy) auf den Munchlax-Loop. None → create_task-Fallback.
        self._own_thread = None
        self._last_bag_hash: dict[str, str] = {}
        # Soullink-State, gespiegelt vom Server. Persistenz auf DB kommt in
        # späteren Tasks (Frontend/Persistierung); hier reine In-Memory-Ablage.
        self.soullink_config: dict = {}
        self.soullink_links: dict[int, dict] = {}
        self.soullink_deaths: dict[tuple[int, str], dict] = {}
        self.soullink_versus_state: dict[str, dict] = {}
        self.soullink_versus_battles: list[dict] = []
        # Team-State (modus-unabhängig) fürs Overlay. Owner-Rohdaten pro Team;
        # Aggregation (AND, Region-Gruppierung) macht das Overlay selbst.
        self.soullink_team_state: dict[str, dict] = {}
        self.soullink_rule_violations: dict[tuple[str, str], dict] = {}
        # Token-State pro Owner (Server-authoritativ, Cache).
        # {owner: {earned, used, active_route, active_edition}}
        self.soullink_tokens: dict[str, dict] = {}
        self._wipe_signaled: dict = {}
        self._last_wipe_player_id: int | str | None = None
        # Death-Reporting: einmal gemeldete Tode (pv, owner) + Queue für Meldungen,
        # die mangels Verbindung noch nicht raus sind (Flush beim nächsten Team-Tick).
        self._reported_deaths: set[tuple[int, str]] = set()
        self._pending_death_reports: list[dict] = []
        self.on_total_wipe_callback = None
        # Wird von app.py gesetzt: Callback, wenn ein anderer Client sein
        # Wipe-Popup per Cancel geschlossen hat und wir im selben Team sind.
        # Empfaengt subject_pid (int).
        self.on_wipe_dismissed_callback = None
        # HP-Consistency: pro (player_id, personality) → {last_max_hp, last_lvl, zero_reads}.
        # Filtert kurze RAM-Fluktuationen und Gen-6/7-Battle-RAM-Ausreißer, bevor ein
        # Pokemon als "tot" gilt.
        self._pokemon_hp_state: dict[tuple, dict] = {}
        # Race-Timer, gespiegelt vom Server. `timer_state` = kompletter Snapshot,
        # `timer_last_tick` = (elapsed, server_ts, local_ts_at_receive) für lokale
        # Drift-Korrektur zwischen Ticks (Client rechnet elapsed lokal weiter).
        self.timer_state: dict = {}
        self.timer_last_tick: dict | None = None
        # Countdown (z.B. YouTube-Aufnahme). `countdown_finished_callback` wird
        # vom Frontend gesetzt und beim Ablauf einmal aufgerufen (Ton/Popup).
        self.countdown_state: dict = {}
        self.countdown_last_tick: dict | None = None
        self.countdown_finished_callback = None

        # Diagnose-State fuer Reconnect/Log-Analyse (Phase B).
        # session_id: UUID pro erfolgreicher connect() — taucht in jedem
        #   Reconnect-Zyklus im Log auf, erlaubt saubere Trennung alter/
        #   neuer Verbindung in nachtraeglichen Log-Reviews.
        # _reconnect_log: Ringpuffer der letzten 20 Disconnect/Reconnect-
        #   Events (ts, reason, session_id, attempt). Wird beim naechsten
        #   erfolgreichen Connect ausgedumpt — zeigt die Flap-Historie.
        # _last_recv_at / _last_recv_type: fuer "letzte Message vor
        #   Disconnect"-Diagnose.
        # _msg_counters: recv/sent pro Session, bei Disconnect geloggt.
        # _err_counts: Rate-Limit-Zaehler pro (err_type_name) im
        #   LOG_DEDUP_WINDOW_SECONDS-Fenster.
        # _last_disconnect_reason: wird vom alter_teams/heartbeat/send_teams-
        #   Loop vor dem break gesetzt und von _auto_reconnect geloggt.
        self._session_id: str | None = None
        self._reconnect_log: deque = deque(maxlen=20)
        self._last_recv_at: float = 0.0
        self._last_recv_type: str = ""
        self._msg_counters: dict[str, int] = {"recv": 0, "sent": 0}
        self._err_counts: dict[str, dict] = {}
        self._last_disconnect_reason: str = "unknown"
        # Markiert die Session, fuer die ein intentional-Disconnect
        # bereits in Flight ist (R10 WARN): verhindert, dass ein paralleler
        # Loop-Error-Handler (`alter_teams`/`send_heartbeat`/`send_teams`)
        # mit demselben `_session_id` den intentional-Grund mit einem
        # transienten `recv_*`/`heartbeat_*` ueberschreibt, obwohl der
        # Disconnect vom User kommt.
        self._intentional_session: str | None = None
        # Signal fuer `_auto_reconnect`: User-Disconnect ist unterwegs
        # oder gerade abgeschlossen (R11 KRIT). Ohne dieses Flag koennte
        # ein Loop-Error-Disconnect, der VOR dem intentional-Call das
        # Lock bekommt, nach seiner Teardown-Phase einen `_auto_reconnect`
        # spawnen, dessen reference im intentional-Pfad (vor Lock) noch
        # nicht existierte. Ergebnis: User klickt Trennen, Verbindung
        # wird kurz zu, Reconnect laeuft trotzdem durch.
        self._user_disconnect_pending = False
        self._session_started_at: float = 0.0

        # Phase C — Replay-Buffer. Haelt (seq, payload)-Tupel. Monoton
        # steigender _next_seq ueberlebt Reconnects (sonst wuerde Server
        # Replays nicht dedupen koennen). _resume_pending = True solange
        # Server-resume_ack ausstaendig ist — bufferable Payloads werden in
        # dieser Phase nur in den Buffer gelegt und NICHT direkt gewired.
        # Reihenfolge-Garantie: Replay (inkl. der neuen seqs waehrend des
        # Pending-Fensters) geht als erstes raus, erst dann Live-Traffic.
        # Erster Connect: _next_seq=1, Buffer leer → resume_ack mit
        # last_seen_seq=0 → nichts zu replayen, _resume_pending kippt auf
        # False, Live-Send laeuft an.
        self._outbound_buffer: deque = deque(maxlen=OUTBOUND_BUFFER_MAXLEN)
        self._next_seq: int = 1
        self._resume_pending: bool = False
        # Boot-Epoch: eindeutig pro Prozess-Lebenszeit. Wird im Handshake an
        # Arceus geschickt, damit der Server bei einem Client-App-Neustart
        # (gleiche client_id, aber _next_seq startet wieder bei 1) seinen
        # last_seen_seq-Counter resetten kann. Ohne diesen Guard wuerde der
        # Server stillschweigend alle neuen Nachrichten als "Replay" dedupen
        # bis seq > alter_last_seen — Datenverlust ohne Warn-Log.
        self._boot_epoch: int = time.time_ns()
        # Capability-Flag: True sobald mind. ein resume_ack vom Server kam —
        # dann weiss der Client, dass der Server Phase-C-Protokoll spricht
        # und wrappen+buffern Sinn macht. None waehrend des ersten Resume-
        # Fensters, False nach Watchdog-Timeout (alter Server). Bei False
        # faellt send_message auf unwrapped-Modus zurueck und der Buffer
        # wird geleert.
        self._server_supports_resume: bool | None = None
        # Strong-Ref auf den _auto_reconnect-Task. GC-Risiko siehe memory
        # feedback-async-task-strong-ref — und wird in disconnect(
        # intentional=True) gecancelt, damit ein User-Disconnect waehrend
        # des Backoff-Sleep nicht ignoriert wird (vorher: Loop verbindet
        # trotz User-Cancel wieder).
        self._reconnect_task: asyncio.Task | None = None
        # Resend-Running-Flag: _resend_buffered darf von Cancel nicht mitten
        # im Frame unterbrochen werden (Writer-Stream wuerde desynchroni-
        # sieren). Watchdog + resume_ack-Handler konsultieren das Flag;
        # Dedupe bei doppeltem Trigger (Watchdog resendete bereits, dann
        # kommt resume_ack verzoegert nach).
        self._resend_running: bool = False
        # Timeout-Fallback: wenn Server kein resume_ack schickt (alter Server
        # ohne Phase-C-Code, oder Netz hat Push gefressen), kippt dieses
        # Timeout-Task das Pending-Flag selbst, damit bufferable Payloads
        # irgendwann rausgehen. Server ohne Phase C ignoriert den __seq__-
        # Wrapper nicht automatisch — Kompat erfordert, dass der Server
        # beide Formate kennt. Als Rueckfall-Strategie koennten wir dann
        # ohne Wrapper senden; aktuell ist nur der Phase-C-Server supported.
        self._resume_timeout_task: asyncio.Task | None = None

        self.logger = get_logger(__name__, './logs/munchlax.log')

    def clear_everything(self):
        # state_lock schuetzt den Rebind: BH-Thread und (seit Phase 3)
        # Munchlax-Thread schreiben parallel auf shared State. Zwischen dem
        # Lesen von `teams = self.munchlax.bizhawk_teams` (bizhawk.py) und
        # dem anschliessenden `with state_lock:` koennten Writes im
        # verworfenen Dict landen und fehlen bis zum naechsten Tick, oder
        # Iterationen in Kivy wuerden `dict changed size`-RuntimeErrors
        # werfen. Alle cross-thread-sichtbaren Dicts gehen deshalb unter
        # Lock.
        # clear_everything laeuft aus Kivy (`clear_clients` Button), die
        # mutierten Felder gehoeren aber zum Munchlax-Thread. Alle Rebinds
        # unter state_lock, damit konkurrierende Munchlax-Loop-Reads nicht
        # halbleere Dicts sehen oder RuntimeError (`dict changed size`).
        # Einzel-Key-Writes aus dem Munchlax-Loop auf diese Felder sind
        # GIL-atomar (`.add()`, `[k] = v`, `.append()`) — der kurze Race-
        # Spalt beim Rebind kostet im Worst-Case eine verlorene Add-Op,
        # das ist bei Reset-Semantik vertretbar.
        with self.state_lock:
            self.bizhawk_teams = {}
            self.sorted_teams = {}
            self.unsorted_teams = {}
            self.badges = {}
            self.editions = {}
            self.boxes = {}
            self.player_names.clear()
            self.client_names.clear()
            self.remote_connection_status.clear()
            self.remote_connection_names.clear()
            self.soullink_config = {}
            self.soullink_links = {}
            self.soullink_deaths = {}
            self.soullink_versus_state = {}
            self.soullink_versus_battles = []
            self.soullink_team_state = {}
            self.soullink_rule_violations = {}
            self.soullink_tokens = {}
            self.timer_state = {}
            self.countdown_state = {}
            self._reported_deaths = set()
            self._pending_death_reports = []
            self.timer_last_tick = None
            self.countdown_last_tick = None
        self.initialized = False
        # rando_tm_moves/rando_hm_moves bewusst NICHT clearen: die ROMs der
        # Clients bleiben randomisiert, bis zum naechsten Randomize kommt
        # kein Sync-Push mehr. Clearen wuerde Overlays bis zum naechsten
        # Randomize auf Vanilla fallen lassen (R1 WARNUNG sync).

    def _clear_session_runtime_state(self):
        """Setzt In-Memory-Session-Caches zurück, OHNE ``soullink_config`` zu
        verlieren. Wird sowohl vom lokalen Reset (Host-Trigger) als auch vom
        Remote-Handler (session_reset-Broadcast) gerufen — beide Pfade sollen
        auf denselben Zustand kommen.

        Behalten: ``player_names``, ``client_names``, ``remote_connection_*``
        (Verbindungs-Metadaten, kein Session-Content) und ``soullink_config``.
        """
        # Analog clear_everything: alle Rebinds unter state_lock, damit
        # weder BH- noch Munchlax-Writes im verworfenen Dict landen und
        # keine Reader auf halbleere Dicts stossen.
        with self.state_lock:
            self.bizhawk_teams = {}
            self.sorted_teams = {}
            self.unsorted_teams = {}
            self.badges = {}
            self.editions = {}
            self.boxes = {}
            self.soullink_links = {}
            self.soullink_deaths = {}
            self.soullink_versus_state = {}
            self.soullink_versus_battles = []
            self.soullink_team_state = {}
            self.soullink_rule_violations = {}
            self.soullink_tokens = {}
            self._reported_deaths = set()
            self._pending_death_reports = []
            self._wipe_signaled = {}
            self._last_wipe_player_id = None
            self._pokemon_hp_state = {}
            self._last_bag_hash = {}
            self.last_box_refresh_at = {}
        self.initialized = False
        # rando_tm_moves/rando_hm_moves bewusst NICHT clearen — siehe
        # Begruendung in clear_everything.
        # Replay-Buffer leeren — Payloads referenzieren den alten Session-State
        # (Encounter-Reports, Soullink-Deaths etc.), die auf dem Server durch
        # den Session-Reset bereits weg sind. Re-Senden wuerde Zombie-Daten
        # produzieren. seq-Counter weiter laufen lassen; der naechste
        # resume_ack vom Server traegt last_seen_seq=0 fuer diesen Client
        # (wird in Arceus beim Session-Reset ebenfalls geloescht).
        self._outbound_buffer.clear()

    # ------------------------------------------------------------------
    # Snapshot-Getter fuer cross-thread-Reads aus dem Kivy-Loop.
    #
    # Seit Phase 3 (Loop-Isolation) laufen Munchlax-Loop (receive_messages,
    # alter_teams, Timer-Ticks) und BH-Thread (update_teams) parallel zu
    # Kivy. Direkt iteration ueber die Live-Dicts aus Kivy wuerde
    # `RuntimeError: dict changed size during iteration` werfen, sobald
    # ein paralleler Writer einen Key einfuegt oder entfernt.
    #
    # Die Snapshot-Getter kopieren den Dict-Zustand unter `state_lock`
    # einmal flach aus, der Caller iteriert dann ausserhalb des Locks.
    # Shallow-Copy reicht fuer Dicts mit primitiven Werten; verschachtelte
    # Dicts (badges, sorted_teams) werden level-2 kopiert, damit auch die
    # inneren Dict-Objekte stabil iteriert werden koennen.
    # ------------------------------------------------------------------

    def snapshot_badges(self) -> dict:
        with self.state_lock:
            return {pid: dict(b) if isinstance(b, dict) else b
                    for pid, b in self.badges.items()}

    def snapshot_sorted_teams(self) -> dict:
        with self.state_lock:
            return {pid: list(t) if isinstance(t, list) else t
                    for pid, t in self.sorted_teams.items()}

    def snapshot_unsorted_teams(self) -> dict:
        with self.state_lock:
            return {pid: list(t) if isinstance(t, list) else t
                    for pid, t in self.unsorted_teams.items()}

    def snapshot_editions(self) -> dict:
        with self.state_lock:
            return dict(self.editions)

    def snapshot_player_names(self) -> dict:
        with self.state_lock:
            return dict(self.player_names)

    def snapshot_client_names(self) -> dict:
        with self.state_lock:
            return dict(self.client_names)

    def snapshot_remote_connection_status(self) -> dict:
        with self.state_lock:
            return dict(self.remote_connection_status)

    def snapshot_remote_connection_names(self) -> dict:
        with self.state_lock:
            return dict(self.remote_connection_names)

    def snapshot_timer_state(self) -> dict:
        with self.state_lock:
            return dict(self.timer_state)

    def snapshot_countdown_state(self) -> dict:
        with self.state_lock:
            return dict(self.countdown_state)

    def snapshot_soullink_links(self) -> dict:
        with self.state_lock:
            return {pid: dict(v) if isinstance(v, dict) else v
                    for pid, v in self.soullink_links.items()}

    def snapshot_soullink_team_state(self) -> dict:
        with self.state_lock:
            return {tid: dict(v) if isinstance(v, dict) else v
                    for tid, v in self.soullink_team_state.items()}

    def snapshot_soullink_tokens(self) -> dict:
        with self.state_lock:
            return {owner: dict(v) if isinstance(v, dict) else v
                    for owner, v in self.soullink_tokens.items()}

    def snapshot_rando_tm_moves(self) -> dict:
        with self.state_lock:
            return {pid: dict(v) if isinstance(v, dict) else v
                    for pid, v in self.rando_tm_moves.items()}

    def snapshot_rando_hm_moves(self) -> dict:
        with self.state_lock:
            return {pid: dict(v) if isinstance(v, dict) else v
                    for pid, v in self.rando_hm_moves.items()}

    # ------------------------------------------------------------------
    # Cross-Thread-Dispatcher auf den Kivy-Loop.
    #
    # Seit Phase 3 laeuft Munchlax (connect, receive_messages, alter_teams,
    # Timer-Ticks) auf einem eigenen asyncio-Loop im MunchlaxThread. OBS,
    # OverlayServer und UI leben weiterhin auf dem Kivy-Loop. Direkte
    # `await self.obs.xxx(...)` aus dem Munchlax-Loop wuerde `asyncio.Lock`-
    # Instanzen anfassen, die an den Kivy-Loop gebunden sind → RuntimeError
    # "attached to a different loop".
    #
    # Blaupause: backend/classes/bizhawk.py:1898-2009
    # (set_munchlax_loop/_call_on_munchlax/_dispatch_on_munchlax/_guard_coro).
    # ------------------------------------------------------------------

    def set_own_thread(self, thread) -> None:
        """Setzt die MunchlaxThread-Referenz fuer Fremd-Thread-Submits.

        Wird in frontend/app.py:build() nach Thread-Start aufgerufen.
        submit_cross_thread / dispatch_cross_thread nutzen die Ref, um
        Coros aus anderen Threads (CitraHandler auf Kivy-Loop, ggf.
        andere Backend-Komponenten) auf den eigenen Loop zu posten,
        ohne dass der Caller die App-Singleton kennen muss.
        """
        self._own_thread = thread

    def submit_cross_thread(self, coro) -> asyncio.Future:
        """Postet eine Coroutine auf den Munchlax-Loop. Thread-safe.

        Fallback (Testkontext, kein Thread gesetzt): create_task auf dem
        aktuellen Loop. Caller erhaelt entweder asyncio.Future (nach
        wrap_future) oder einen Task — beide via `await` abwartbar.

        Caller-Erwartung: `await submit_cross_thread(...)` kann RuntimeError
        werfen, wenn der Munchlax-Thread bereits im Shutdown ist (R3 WARN).
        Widget-Caller sollten im try/except arbeiten, oder stattdessen
        `dispatch_cross_thread` nutzen, das Shutdown-Fehler schluckt.
        """
        thread = self._own_thread
        if thread is None or thread.loop is None:
            return asyncio.create_task(coro)
        return asyncio.wrap_future(thread.submit_coro(coro))

    def dispatch_cross_thread(self, coro, name: str = "<unnamed>") -> None:
        """Fire-and-forget-Dispatch einer Coroutine auf den Munchlax-Loop.

        Ersatz fuer `asyncio.create_task(munchlax.xxx(...))` aus einem
        anderen Thread. Exceptions laufen durch submit_coro_logged ins
        Munchlax-Threadlog, nicht silent.
        """
        thread = self._own_thread
        if thread is None or thread.loop is None:
            task = asyncio.create_task(coro)

            def _log(t):
                # CancelledError ist BaseException-Subklasse — getrennter
                # cancelled()-Zweig plus Exception-Handler in exception().
                if t.cancelled():
                    self.logger.info(f"dispatch_cross_thread({name}) wurde gecancelt.")
                    return
                try:
                    exc = t.exception()
                except asyncio.CancelledError:
                    self.logger.info(
                        f"dispatch_cross_thread({name}) wurde gecancelt (exception())."
                    )
                    return
                except Exception:
                    exc = None
                if exc is not None:
                    self.logger.error(
                        f"dispatch_cross_thread({name}) failed: "
                        f"{type(exc).__name__}: {exc}"
                    )
            task.add_done_callback(_log)
            return
        try:
            thread.submit_coro_logged(coro, name=name)
        except RuntimeError as err:
            # MunchlaxThread bereits im Shutdown (`_closing=True`): coro
            # wurde durch submit_coro bereits geschlossen. Fire-and-forget-
            # Semantik → kein Weiterwerfen in den Caller (Kivy-Button).
            self.logger.warning(
                f"dispatch_cross_thread({name}): MunchlaxThread shutdown — "
                f"Dispatch verworfen ({err})"
            )

    def set_kivy_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Setzt die Kivy-Loop-Referenz fuer cross-thread-Delegation.

        Wird in frontend/app.py:build() nach Munchlax-Instanzierung
        aufgerufen. _call_on_kivy / _dispatch_on_kivy nutzen diese Ref,
        um OBS-/Overlay-/UI-Coros thread-safe auf den Kivy-Loop zu posten,
        auch wenn der Caller im Munchlax-Thread laeuft.
        """
        self._kivy_loop = loop

    async def _call_on_kivy(self, coro):
        """Delegiert eine Coroutine an den Kivy-Loop und wartet auf Ergebnis.

        Fallback: wenn _kivy_loop (noch) nicht gesetzt ist (Testkontext),
        oder falls der Call bereits auf dem Kivy-Loop lebt, direkt `await`
        der Coro — identisches Ergebnis ohne cross-thread-Overhead.
        """
        current = None
        try:
            current = asyncio.get_running_loop()
        except RuntimeError:
            current = None
        kivy_loop = getattr(self, '_kivy_loop', None)
        if kivy_loop is None or current is kivy_loop:
            return await coro
        if kivy_loop.is_closed():
            try:
                coro.close()
            except Exception:
                pass
            raise RuntimeError("_call_on_kivy: Kivy-Loop ist geschlossen")
        # TOCTOU: zwischen is_closed()-Check und run_coroutine_threadsafe
        # kann der Kivy-Loop sterben (Shutdown-Race). Submit wuerde dann
        # RuntimeError werfen und die Coro ungeschlossen zuruecklassen.
        try:
            fut = asyncio.run_coroutine_threadsafe(coro, kivy_loop)
        except RuntimeError as err:
            try:
                coro.close()
            except Exception:
                pass
            raise RuntimeError(
                f"_call_on_kivy: Submit abgelehnt (Loop-Shutdown-Race): {err}"
            ) from err
        return await asyncio.wrap_future(fut)

    def _dispatch_on_kivy(self, coro) -> None:
        """Fire-and-forget-Dispatch einer Coroutine auf den Kivy-Loop.

        Ersatz fuer `asyncio.create_task(obs.xxx(...))` an Call-Sites ohne
        Rueckkanal. Fehler aus der Coroutine werden ueber `_guard_coro`
        geloggt (nicht silent gedropt).
        """
        try:
            current = asyncio.get_running_loop()
        except RuntimeError:
            current = None
        kivy_loop = getattr(self, '_kivy_loop', None)
        if kivy_loop is None or current is kivy_loop:
            if current is None:
                self.logger.error(
                    "_dispatch_on_kivy ohne Loop — Coro verworfen."
                )
                try:
                    coro.close()
                except Exception:
                    pass
                return
            asyncio.create_task(self._guard_coro(coro))
            return
        if kivy_loop.is_closed():
            self.logger.warning(
                "_dispatch_on_kivy: Kivy-Loop geschlossen, Coro verworfen."
            )
            try:
                coro.close()
            except Exception:
                pass
            return
        # Wrapper-Coro materialisieren, damit sie bei Submit-Fehler
        # geschlossen werden kann (sonst "coroutine was never awaited").
        guard = self._guard_coro(coro)
        try:
            asyncio.run_coroutine_threadsafe(guard, kivy_loop)
        except RuntimeError as err:
            self.logger.warning(
                f"_dispatch_on_kivy submit fehlgeschlagen: {err}"
            )
            try:
                guard.close()
            except Exception:
                pass
            try:
                coro.close()
            except Exception:
                pass

    async def _guard_coro(self, coro):
        try:
            await coro
        except Exception as err:
            self.logger.error(f"_dispatch_on_kivy coro failed: {type(err)},{err}")
            self.logger.error(traceback.format_exc())

    async def reset_session_data(self) -> tuple[bool, str]:
        """Host-Trigger: löscht pokemon.db-Inhalte + In-Memory-Session-State,
        informiert Server via ``session_reset``. Server broadcastet an Peers,
        die dann ebenfalls ``_handle_remote_session_reset`` laufen lassen.

        Rückgabe ``(ok, msg)`` für UI-Feedback. Bei Fehlern bleibt der In-
        Memory-State trotzdem gecleart, damit der User nicht mit halb-altem
        Zustand weiterläuft — die DB-Datei wird beim nächsten
        ``_ensure_pokedex_db`` neu angelegt.
        """
        loop = asyncio.get_event_loop()
        db_ok = True
        try:
            self._ensure_pokedex_db()
            if self.pokedex_db is not None and self.pokedex_db.connection is not None:
                db_ok = await loop.run_in_executor(None, self.pokedex_db.reset_all_data)
        except Exception as err:
            self.logger.error(f"reset_session_data DB failed: {type(err)},{err}")
            self.logger.error(f"{traceback.format_exc()}")
            db_ok = False

        self._clear_session_runtime_state()

        if self.is_connected:
            try:
                async with self.writer_lock:
                    await self.send_message({"type": "session_reset"})
            except Exception as err:
                self.logger.warning(f"session_reset senden failed: {err}")

        await self._refresh_overlay_after_reset()

        if db_ok:
            self.logger.info("reset_session_data: DB geleert, State geleert, Server informiert")
            return True, "Session-Daten zurückgesetzt"
        return False, "DB-Reset fehlgeschlagen — In-Memory-State trotzdem geleert"

    async def _handle_remote_session_reset(self):
        """Wird vom Arceus-Broadcast ``session_reset`` getriggert. Lokale DB
        + In-Memory-State analog zum Host-Reset entleeren; kein Rück-Broadcast.
        """
        self.logger.info("session_reset vom Server empfangen — DB + State leeren")
        loop = asyncio.get_event_loop()
        try:
            self._ensure_pokedex_db()
            if self.pokedex_db is not None and self.pokedex_db.connection is not None:
                await loop.run_in_executor(None, self.pokedex_db.reset_all_data)
        except Exception as err:
            self.logger.error(f"remote session_reset DB failed: {type(err)},{err}")
            self.logger.error(f"{traceback.format_exc()}")
        self._clear_session_runtime_state()
        await self._refresh_overlay_after_reset()

    async def _refresh_overlay_after_reset(self):
        """Overlay-Refresh nach Reset: alle bekannten player_ids + team_ids
        triggern, damit Browser-Overlay sofort leere Slots zeigt statt bis
        zum nächsten notify_update alte Cache-Daten zu halten."""
        srv = self.overlay_server
        if srv is None or not getattr(srv, "is_connected", False):
            return
        notifier = getattr(srv, "notify_config_change", None)
        if callable(notifier):
            # Fire-and-forget auf Kivy-Loop (OverlayServer dort).
            self._dispatch_on_kivy(notifier())

    async def force_overlay_broadcast(self, reason: str = ""):
        """Erzwingt einen kompletten Team- + Badge-Refresh an alle SSE-Clients.

        Öffentlicher Wrapper um `_refresh_overlay_after_reset` für Aufrufer
        außerhalb des Reset-Flows: Snapshot-Restore und Randomize+Restore
        wechseln den DB-Inhalt (und damit oft indirekt die sorted_teams,
        wenn der neue Save geladen wird), aber der reguläre Diff-Guard in
        `alter_teams` (Zeile ~500) kann in Edge-Cases (identischer Content,
        Objekt-Equality-Fallen) den notify_update-Trigger verschlucken.
        Nach einem Restore explizit forcen — dann bekommt das Overlay
        garantiert sofort den frischen Zustand statt bis zum nächsten
        echten Team-Diff zu warten.
        """
        srv = self.overlay_server
        if srv is None or not getattr(srv, "is_connected", False):
            self.logger.debug(
                f"force_overlay_broadcast skip ({reason}): overlay_server "
                f"nicht verfügbar/verbunden"
            )
            return
        self.logger.info(f"force_overlay_broadcast: {reason}")
        await self._refresh_overlay_after_reset()

    def should_auto_refresh_boxes(self, player_id: int) -> bool:
        """True wenn die Throttle-Drosselung einen automatischen Refresh erlaubt."""
        last = self.last_box_refresh_at.get(player_id, 0.0)
        return (time.time() - last) >= BOX_REFRESH_THROTTLE_SECONDS

    def mark_box_refresh(self, player_id: int):
        """Throttle-Zeitstempel setzen (vor dem eigentlichen Box-Read aufrufen)."""
        self.last_box_refresh_at[player_id] = time.time()

    async def update_boxes(self, player_id: int, boxes: list):
        """Lokal cachen + (falls verbunden) an Arceus pushen.

        Arceus verteilt das Update an alle anderen Munchlaxes, sodass
        BoxMenüs auf Remote-Clients automatisch frische Daten bekommen.
        """
        await self._enrich_box_levels(boxes)
        with self.state_lock:
            self.boxes[player_id] = boxes
        if not self.is_connected:
            return
        try:
            async with self.writer_lock:
                await self.send_message({
                    "type": "boxes_update",
                    "player_id": player_id,
                    "boxes": boxes,
                })
        except Exception as err:
            self.logger.warning(f"boxes_update an Arceus senden failed: {type(err)},{err}")
            self.logger.warning(f"{traceback.format_exc()}")

    async def _enrich_box_levels(self, boxes: list):
        """Ersetzt berechnete Box-Pokemon-Level durch DB-Werte (per PV).

        Box-Pokemon haben ab Gen 3 kein gespeichertes Level — der pokedecoder
        nutzt deshalb eine Medium-Fast-Approximation, die bei Fast/Slow/
        Erratic/Fluctuating-Spezies bis zu mehrere Level daneben liegen kann.
        Wenn dasselbe Pokemon (per PV) schon mal im Team war, kennen wir das
        echte Level aus pokemon.db und nehmen es bevorzugt.

        Gen 1/2 wird automatisch übersprungen: dort kommen Box-Pokemon ohne
        Personality-Attribut, das Memory-Level steht direkt im Slot.
        """
        self._ensure_pokedex_db()
        if self.pokedex_db is None or self.pokedex_db.connection is None:
            return
        slots_by_pv: dict[int, list] = {}
        for box in boxes:
            for slot in box:
                if slot is None:
                    continue
                pv = getattr(slot, "personality", None)
                if pv is None:
                    continue
                slots_by_pv.setdefault(int(pv), []).append(slot)
        if not slots_by_pv:
            return
        try:
            loop = asyncio.get_event_loop()
            levels = await loop.run_in_executor(
                None,
                self.pokedex_db.get_lvls_by_personalities,
                list(slots_by_pv.keys()),
            )
            for pv, lvl in levels.items():
                for slot in slots_by_pv.get(pv, []):
                    slot.lvl = lvl
        except Exception as err:
            self.logger.warning(f"Box-Level-Enrichment failed: {type(err)},{err}")
            self.logger.warning(f"{traceback.format_exc()}")

    def _log_dedup(self, err_type: str, msg: str, *, exc: BaseException | None = None,
                   level: str = "warning") -> None:
        """Rate-limited Logging: innerhalb LOG_DEDUP_WINDOW_SECONDS wird pro
        err_type nur die erste Occurrence mit Traceback geloggt, Folge-
        Occurrences nur gezaehlt. _flush_dedup() dumpt die Summary.

        Verhindert, dass ein toter Socket mit 1000+ IncompleteReadError/sec
        das Logfile zerreisst (Satougame-Pattern 2026-09-30).
        """
        now = time.time()
        slot = self._err_counts.get(err_type)
        if slot is None or (now - slot["first_ts"]) > LOG_DEDUP_WINDOW_SECONDS:
            # Window-Rollover: ggf. alten Slot als Summary ausgeben
            if slot is not None and slot["count"] > 1:
                self.logger.warning(
                    f"[{err_type}] {slot['count']-1}x weitere Occurrences "
                    f"im Fenster {slot['first_ts']:.0f}-{now:.0f} unterdrueckt"
                )
            self._err_counts[err_type] = {"first_ts": now, "count": 1}
            emit = getattr(self.logger, level, self.logger.warning)
            emit(f"[session={self._session_id}] {msg}")
            if exc is not None:
                self.logger.warning(f"{traceback.format_exc()}")
        else:
            slot["count"] += 1

    def _flush_dedup(self) -> None:
        """Beim Reconnect-Erfolg: alle offenen Dedup-Fenster als Summary
        ausgeben und Zaehler zuruecksetzen."""
        now = time.time()
        for err_type, slot in list(self._err_counts.items()):
            if slot["count"] > 1:
                self.logger.info(
                    f"[{err_type}] waehrend Disconnect {slot['count']}x "
                    f"geloggt (Fenster {slot['first_ts']:.0f}-{now:.0f})"
                )
        self._err_counts.clear()

    def _record_disconnect(self, reason: str,
                            expected_session: str | None = None) -> None:
        """Loop-Hooks setzen den Grund bevor sie disconnect(intentional=False)
        aufrufen. Wird im Reconnect-Log festgehalten + im naechsten connect
        geloggt.

        `expected_session` (R9 WARN): Session-Identity-Check. Stale Loops
        einer alten Session wuerden sonst `_last_disconnect_reason` der
        frischen Session ueberschreiben und einen irrefuehrenden Log-
        Eintrag anhaengen. Caller aus Loop-Error-Handlern uebergeben
        `my_session`. Mismatch → noop.
        """
        if (expected_session is not None
                and expected_session != self._session_id):
            self.logger.info(
                f"_record_disconnect(reason={reason}, session="
                f"{expected_session}) uebersprungen — Session gehoert "
                f"inzwischen einem frischeren Connect ({self._session_id})."
            )
            return
        # Zusaetzlicher Guard (R10 WARN): intentional-Disconnect laeuft
        # und hat `_last_disconnect_reason='intentional'` gesetzt. Ein
        # Loop-Error-Handler mit gleicher Session wuerde den Grund jetzt
        # mit `recv_*`/`heartbeat_*` ueberschreiben, obwohl der User-
        # Disconnect der eigentliche Trigger ist. Skip + Log.
        if (expected_session is not None
                and expected_session == self._intentional_session):
            self.logger.info(
                f"_record_disconnect(reason={reason}, session="
                f"{expected_session}) uebersprungen — intentional-"
                f"Disconnect ist bereits in Flight."
            )
            return
        self._last_disconnect_reason = reason
        self._reconnect_log.append({
            "ts": time.time(),
            "event": "disconnect",
            "reason": reason,
            "session_id": self._session_id,
            "msg_recv": self._msg_counters.get("recv", 0),
            "msg_sent": self._msg_counters.get("sent", 0),
            "session_duration_s": time.time() - self._session_started_at
                                   if self._session_started_at else 0,
        })

    async def alter_teams(self):
        # Snapshot der aktuellen Session, damit ein verspaeteter Error-
        # Disconnect dieses Tasks keinen frisch aufgebauten Socket einer
        # spaeteren Session kappen kann (R6 WARN).
        my_session = self._session_id
        while True:
            try:
                data = await self.receive_message()
                self._msg_counters["recv"] += 1
                self._last_recv_at = time.time()
                self._last_recv_type = (
                    data.get("type", "teams") if isinstance(data, dict)
                    else type(data).__name__
                )
                # Getypte Server-Push-Nachrichten: separater Pfad, damit die
                # Teams-Logik darunter nicht versehentlich ein {"type": ...}-
                # Dict als Player-Map behandelt.
                if isinstance(data, dict) and "type" in data:
                    msg_type = data.get("type")
                    if msg_type == "resume_ack":
                        # Phase C: Server teilt mit, welche sequence_id er
                        # zuletzt gesehen hat. Alles mit groesserer seq im
                        # lokalen Buffer re-senden. Watchdog canceln,
                        # Capability bestaetigen, dann Resend.
                        #
                        # WICHTIG (sync-reviewer R2 Runde 2+3):
                        # _resume_pending wird NICHT hier gekippt — sondern
                        # erst unter writer_lock in _resend_buffered. Grund:
                        # Race zwischen Flag-Flip und Shield-Task-Start,
                        # Live-send mit hoeherer seq koennte sonst vor dem
                        # Replay auf die Leitung gehen, Server dedupt die
                        # alten Payloads weg.
                        #
                        # Dedup gegen Watchdog (sync-reviewer R3 W1+W2):
                        # Wenn der Watchdog bereits im Resend ist (Lock-Wait
                        # oder mid-frame), DARF der Handler weder cancel()
                        # auf ihn werfen (bricht Frame ab, desync Stream),
                        # noch _server_supports_resume=True setzen (der
                        # Watchdog sendet gerade unwrapped — mittendrin das
                        # Flag umzudrehen wuerde Reihenfolge invertieren).
                        # Watchdog laeuft dann einfach durch.
                        last_seen = int(data.get("last_seen_seq", 0))
                        buffered_count = len(self._outbound_buffer)
                        self.logger.info(
                            f"resume_ack empfangen: last_seen_seq={last_seen}, "
                            f"local_next_seq={self._next_seq}, "
                            f"buffer_size={buffered_count}"
                        )
                        if self._resend_running:
                            self.logger.info(
                                "resume_ack: Watchdog-Resend bereits aktiv — "
                                "ueberspringe Cancel + Flag + Resend, "
                                "Watchdog uebernimmt"
                            )
                        else:
                            if self._resume_timeout_task is not None and not self._resume_timeout_task.done():
                                self._resume_timeout_task.cancel()
                            self._server_supports_resume = True
                            # asyncio.shield verhindert Cancel von aussen:
                            # Resend darf nie mitten im Frame unterbrochen
                            # werden, sonst Writer-Stream permanent desync.
                            # Done-Callback loggt Exceptions, die sonst nur
                            # als "Task exception never retrieved" im stderr
                            # auftauchen (reviewer R3 H6).
                            inner = asyncio.ensure_future(
                                self._resend_buffered(last_seen)
                            )
                            inner.add_done_callback(
                                self._on_resend_done
                            )
                            try:
                                resent = await asyncio.shield(inner)
                                if resent > 0:
                                    self.logger.info(
                                        f"Resume: {resent} Items erneut gesendet"
                                    )
                                else:
                                    self.logger.info(
                                        "Resume: kein Resend noetig (Buffer "
                                        "bereits beim Server oder leer)"
                                    )
                            except asyncio.CancelledError:
                                # Outer wurde gecancelt. inner laeuft via
                                # Shield weiter und loggt sein Ergebnis
                                # ueber _on_resend_done. Nicht blockieren.
                                self.logger.info(
                                    "Resume-Resend outer cancelled — inner "
                                    "Task laeuft via shield weiter"
                                )
                                raise
                            except Exception as err:
                                self.logger.error(
                                    f"Resume-Resend failed: {type(err).__name__}: {err}"
                                )
                                self.logger.error(f"{traceback.format_exc()}")
                        # Server-Capability jetzt bestaetigt (True) — gepufferte
                        # rando_moves_sync-Payloads jetzt sicher flushen. Vor
                        # resume_ack war die Capability None und ein Legacy-
                        # Server haette die Nachricht als Teams-Dict fehlgedeutet.
                        # Laufenden Flush NICHT cancelen (koennte Stream mitten
                        # im Frame zerreissen); er iteriert ohnehin per Snapshot
                        # und Identity-Check, Zusatz-Flush ist dann No-op.
                        if self._pending_rando_moves and (
                            self._rando_flush_task is None
                            or self._rando_flush_task.done()
                        ):
                            self._rando_flush_task = asyncio.create_task(
                                self._flush_pending_rando_moves()
                            )
                    elif msg_type == "boxes_update":
                        player_id = data.get("player_id")
                        boxes = data.get("boxes")
                        if player_id is not None and boxes is not None:
                            with self.state_lock:
                                self.boxes[player_id] = boxes
                            self.logger.info(
                                f"boxes_update empfangen: player={player_id}, "
                                f"box_count={len(boxes)}"
                            )
                    elif msg_type == "player_names":
                        # clear() + update() statt Rebind: BH-Thread schreibt
                        # player_names[player] = name unter state_lock
                        # (bizhawk.py). Ein Rebind hier wuerde BH-Writes im
                        # verworfenen Dict verlieren.
                        incoming_names = data.get("names", {})
                        incoming_client_names = data.get("client_names", {}) or {}
                        # Writes unter state_lock: Kivy iteriert player_names
                        # /client_names fuer Popup-Rendering, zwischen clear
                        # und update sieht Kivy sonst kurz einen leeren Zustand.
                        with self.state_lock:
                            self.player_names.clear()
                            self.player_names.update(incoming_names)
                            self.client_names.clear()
                            self.client_names.update(incoming_client_names)
                        self.logger.info(
                            f"player_names empfangen: {self.player_names}, "
                            f"client_names: {self.client_names}"
                        )
                    elif msg_type == "connection_status":
                        # Writes unter state_lock, damit Kivy
                        # (mainmenu.change_munchlax_status) nicht zwischen
                        # clear und update einen leeren Zustand sieht
                        # → "status flackert kurz leer".
                        new_status = data.get("status", {})
                        new_names = data.get("names", {})
                        with self.state_lock:
                            self.remote_connection_status.clear()
                            self.remote_connection_status.update(new_status)
                            self.remote_connection_names.clear()
                            self.remote_connection_names.update(new_names)
                    elif msg_type == "slot_collision":
                        # Server hat declared_player_ids abgelehnt weil ein anderer
                        # Client bereits einen dieser Slots belegt. Callback ins
                        # Frontend (Popup) und danach sauber disconnecten — der
                        # User muss in seiner player.yml einen anderen Slot waehlen.
                        requested = data.get("requested_slots", [])
                        conflicts = data.get("conflicts", {})
                        self.logger.error(
                            f"slot_collision: angefordert={requested}, "
                            f"kollidiert_mit={conflicts}"
                        )
                        cb = getattr(self, "on_slot_collision_callback", None)
                        if callable(cb):
                            try:
                                cb(requested, conflicts)
                            except Exception as err:
                                self.logger.warning(f"on_slot_collision_callback: {err}")
                        asyncio.create_task(
                            self.disconnect(intentional=True, reason="slot_collision")
                        )
                    elif msg_type == "session_reset":
                        await self._handle_remote_session_reset()
                    elif msg_type == "encounter_sync":
                        await self._handle_remote_encounters(data.get("encounters", []))
                    elif msg_type == "encounter_outcome":
                        await self._handle_remote_outcome(data)
                    elif msg_type == "bag_sync":
                        await self._handle_remote_bag(data)
                    elif msg_type == "rando_moves_sync":
                        pid = data.get("player_id")
                        if isinstance(pid, int):
                            # Owned-Slot-Guard: fuer eigene lokale Slots ist
                            # dieser Client authoritativ — der Server-Replay
                            # koennte sonst ein frisch randomisiertes
                            # Mapping mit dem alten Server-Cache ueberschreiben
                            # (R1 KRITISCH sync 2026-09-30).
                            owned = set(self._owned_player_ids())
                            if pid in owned:
                                self.logger.info(
                                    f"rando_moves_sync ignoriert fuer eigenen "
                                    f"Slot pid={pid} (owned={sorted(owned)})"
                                )
                            else:
                                tm_moves = data.get("tm_moves") or {}
                                hm_moves = data.get("hm_moves") or {}
                                # Writes auf cross-thread-sichtbare Dicts
                                # unter state_lock, damit Kivy-Snapshots
                                # (snapshot_rando_tm_moves etc.) atomar
                                # bleiben.
                                with self.state_lock:
                                    if isinstance(tm_moves, dict):
                                        self.rando_tm_moves[pid] = {
                                            int(k): v for k, v in tm_moves.items()
                                        }
                                    if isinstance(hm_moves, dict):
                                        self.rando_hm_moves[pid] = {
                                            int(k): v for k, v in hm_moves.items()
                                        }
                                self.logger.info(
                                    f"rando_moves_sync empfangen: player_id={pid}, "
                                    f"tms={len(tm_moves)}, hms={len(hm_moves)}"
                                )
                        else:
                            self.logger.warning(
                                f"rando_moves_sync ohne gueltige player_id: {pid!r}"
                            )
                    elif msg_type == "soullink_config":
                        self.soullink_config = data.get("config", {}) or {}
                        # Rules + Mode in local nuz-Dict spiegeln. Konsumenten wie
                        # first_type_dupe_slots (trainerbox/overlay_server/obs) lesen
                        # ausschliesslich aus self.nuz — ohne Spiegel bleibt auf
                        # non-host-Clients z.B. rule_single_type_per_team=True
                        # haengen, obwohl der Host die Regel bereits deaktiviert
                        # oder den Modus auf versus_ffa gewechselt hat. Resultat
                        # waere ein permanent "— gesperrt —"-Slot. Nur In-Memory,
                        # nuz.yml bleibt unangetastet (Host-authoritativ auf Disk).
                        #
                        # Host-Guard: der eigene Broadcast kommt per
                        # arceus.broadcast_soullink_config auch beim Host-Munchlax
                        # an. Dessen nuz ist aber bereits authoritativ via
                        # nuzlockemenu._on_rule_checkbox_change gesetzt — ein
                        # weiterer Mirror hier wuerde bei schnellen Toggles den
                        # stale Echo-State ueber den aktuellen User-Stand schreiben.
                        is_host = False
                        try:
                            is_host = bool(self.rem.get("start_server", False))
                        except AttributeError:
                            is_host = False
                        mirrored_count = 0
                        if not is_host and isinstance(self.nuz, dict):
                            # Nur spiegeln wenn Host das rules-Feld explizit gesetzt
                            # hat. Fehlender Key (alter Server ohne rules-Field)
                            # darf NICHT alle lokalen rule_*-Keys loeschen —
                            # Clear-All ist nur bei explizit leerem {} korrekt.
                            raw_rules = self.soullink_config.get("rules")
                            if isinstance(raw_rules, dict):
                                # Alte rule_*-Keys, die der Host nicht mehr sendet
                                # (Preset-Wechsel mit kleinerem Rule-Set), explizit
                                # entfernen — sonst behaelt der Client seinen
                                # Pre-Broadcast-Default und first_type_dupe_slots
                                # blockt obwohl Host die Regel entfernt hat.
                                for key in [
                                    k for k in list(self.nuz.keys())
                                    if isinstance(k, str) and k.startswith("rule_")
                                ]:
                                    if key not in raw_rules:
                                        self.nuz.pop(key, None)
                                for rule_key, val in raw_rules.items():
                                    if isinstance(rule_key, str) and rule_key.startswith("rule_"):
                                        self.nuz[rule_key] = val
                                        mirrored_count += 1
                            mode_val = self.soullink_config.get("mode")
                            if isinstance(mode_val, str) and mode_val:
                                self.nuz["soullink_mode"] = mode_val
                        log_raw = self.soullink_config.get('rules')
                        log_rules = (
                            list(log_raw.keys()) if isinstance(log_raw, dict) else "absent"
                        )
                        self.logger.info(
                            f"soullink_config empfangen: mode={self.soullink_config.get('mode')}, "
                            f"players={self.soullink_config.get('expected_owners')}, "
                            f"rules={log_rules}, "
                            f"host={is_host}, nuz_mirrored={mirrored_count}"
                        )
                        await self._notify_overlay_session("soullink_config", self.soullink_config)
                    elif msg_type == "soullink_link_state":
                        link = data.get("link") or {}
                        if link.get("link_id") is not None:
                            with self.state_lock:
                                self.soullink_links[link["link_id"]] = link
                            self.logger.info(
                                f"soullink_link_state empfangen: link={link.get('link_id')} "
                                f"state={link.get('state')} members={list(link.get('members', {}).keys())}"
                            )
                            await self._notify_overlay_session("soullink_link_state", link)
                    elif msg_type == "soullink_death":
                        death = data.get("death") or {}
                        self._handle_remote_death(death)
                        await self._notify_overlay_session("soullink_death", death)
                    elif msg_type == "soullink_versus_state":
                        with self.state_lock:
                            self.soullink_versus_state = data.get("state", {}) or {}
                            self.soullink_versus_battles = data.get("battles", []) or []
                        self.logger.info(
                            f"soullink_versus_state empfangen: teams={list(self.soullink_versus_state.keys())}"
                        )
                        await self._notify_overlay_session("soullink_versus_state", {
                            "state": self.soullink_versus_state,
                            "battles": self.soullink_versus_battles,
                        })
                    elif msg_type == "soullink_team_state":
                        with self.state_lock:
                            self.soullink_team_state = data.get("state", {}) or {}
                        self.logger.info(
                            f"soullink_team_state empfangen: teams={list(self.soullink_team_state.keys())}"
                        )
                        await self._notify_overlay_session("soullink_team_state", {
                            "state": self.soullink_team_state,
                        })
                        team_ids = list(self.soullink_team_state.keys())
                        if self.overlay_server is not None:
                            notifier = getattr(self.overlay_server, "notify_team_update", None)
                            if callable(notifier):
                                # OverlayServer lebt auf Kivy-Loop (SSE-Queues
                                # dort gebunden). Fire-and-forget via
                                # _dispatch_on_kivy — Receive-Loop stall soll
                                # nicht durch Kivy-Lag entstehen.
                                for team_id in team_ids:
                                    self._dispatch_on_kivy(notifier(team_id, "badges"))
                        if self.obs is not None:
                            team_updater = getattr(self.obs, "change_team_badges", None)
                            if callable(team_updater) and team_ids:
                                # OBS-WebSocket auf Kivy-Loop. fire-and-forget +
                                # _obs_serial: pro Team ein Call, globaler Lock
                                # verhindert Ueberschneidung mit anderen OBS-
                                # Dispatches (change_badges/changeSource).
                                for team_id in team_ids:
                                    self._dispatch_on_kivy(self.obs._obs_serial(team_updater(team_id)))
                    elif msg_type == "soullink_versus_battle":
                        battle = data.get("battle") or {}
                        with self.state_lock:
                            self.soullink_versus_battles.append(battle)
                        self.logger.info(
                            f"soullink_versus_battle empfangen: {battle.get('winner')} vs {battle.get('loser')}"
                        )
                        await self._notify_overlay_session("soullink_versus_battle", battle)
                    elif msg_type == "soullink_rule_violation":
                        violation = data.get("violation") or {}
                        key = (violation.get("type", "unknown"), str(violation.get("subject", "")))
                        with self.state_lock:
                            self.soullink_rule_violations[key] = violation
                        self.logger.warning(
                            f"soullink_rule_violation empfangen: type={violation.get('type')} "
                            f"subject={violation.get('subject')} msg={violation.get('message')}"
                        )
                        await self._notify_overlay_session("soullink_rule_violation", violation)
                        # Frontend-Trigger für total_wipe
                        if violation.get("type") == "total_wipe":
                            cb = getattr(self, "on_total_wipe_callback", None)
                            if callable(cb):
                                self.logger.info(
                                    f"on_total_wipe_callback wird ausgeloest: "
                                    f"subject={violation.get('subject')}"
                                )
                                try:
                                    cb(violation)
                                except Exception as err:
                                    self.logger.warning(f"on_total_wipe_callback: {err}")
                            else:
                                self.logger.warning(
                                    "on_total_wipe_callback nicht registriert — "
                                    "TotalWipe-Popup wird NICHT geoeffnet."
                                )
                    elif msg_type == "wipe_dismissed":
                        subj = data.get("subject_pid")
                        if subj is not None:
                            try:
                                self._handle_remote_wipe_dismissed(int(subj))
                            except Exception as err:
                                self.logger.warning(f"_handle_remote_wipe_dismissed: {err}")
                    elif msg_type == "soullink_tokens":
                        self.soullink_tokens = data.get("tokens", {}) or {}
                        self.logger.info(
                            f"soullink_tokens empfangen: {[(o, s.get('earned'), s.get('used'), s.get('active_route')) for o, s in self.soullink_tokens.items()]}"
                        )
                        await self._notify_overlay_session("soullink_tokens", self.soullink_tokens)
                    elif msg_type == "timer_state":
                        self._handle_timer_state(data.get("timer") or {})
                        await self._notify_overlay_session("timer_state", self.timer_state)
                        await self._push_timer_text_to_obs()
                    elif msg_type == "timer_tick":
                        self._handle_timer_tick(data)
                        await self._notify_overlay_session("timer_tick", {
                            "elapsed": data.get("elapsed", 0.0),
                            "running": data.get("running", False),
                            "server_ts": data.get("server_ts"),
                        })
                        await self._push_timer_text_to_obs()
                    elif msg_type == "countdown_state":
                        self._handle_countdown_state(data.get("countdown") or {})
                        await self._notify_overlay_session("countdown_state", self.countdown_state)
                    elif msg_type == "countdown_tick":
                        self._handle_countdown_tick(data)
                        await self._notify_overlay_session("countdown_tick", {
                            "remaining_seconds": data.get("remaining_seconds", 0.0),
                            "running": data.get("running", False),
                            "server_ts": data.get("server_ts"),
                        })
                    elif msg_type == "countdown_finished":
                        self._handle_countdown_finished(data)
                        await self._notify_overlay_session("countdown_finished", {
                            "label": data.get("label", ""),
                            "duration_seconds": data.get("duration_seconds", 0.0),
                        })
                    else:
                        self.logger.warning(f"Unbekannter Message-Typ vom Server: {msg_type}")
                    continue
                # state_lock schuetzt gegen Dict-Size-Change-Race:
                # BH-Thread kann waehrend des Rebinds `unsorted_teams[p] = t`
                # auf dem alten Dict schreiben; `copy()` dort wuerde dann
                # RuntimeError werfen. Komplett-Replace + Snapshots unter Lock.
                # `unsorted_snapshot` wird an _persist_teams uebergeben, damit
                # BH-Mutationen waehrend des await-Fensters (upsert_team im
                # Executor) nicht in `dict changed size during iteration`
                # laufen koennen.
                with self.state_lock:
                    self.unsorted_teams = data
                    new_teams = self.unsorted_teams.copy()
                    unsorted_snapshot = self.unsorted_teams.copy()
                self.logging_teams(unsorted_snapshot, "unsorted teams received")
                for player in new_teams:
                    team = new_teams[player]
                    new_teams[player] = self.sort(team[:6], self.sp['order'])
                    # Pre-fetch edition/badges aus dem authoritativen
                    # unsorted_snapshot (lokale Variablen), damit nachgelagerte
                    # Dispatches gegen `clear_everything` aus Kivy immun sind
                    # (sonst KeyError auf self.editions[player] → Exception →
                    # Receive-Loop break → stiller Disconnect-Cascade).
                    new_edition = unsorted_snapshot[player][7]
                    new_badges_val = unsorted_snapshot[player][6]
                    # Writes auf editions/badges unter state_lock, damit
                    # Kivy-Snapshots (snapshot_badges/snapshot_editions) kein
                    # half-updates sehen und keine dict-size-Races auftreten.
                    with self.state_lock:
                        current_edition = self.editions.get(player)
                        if current_edition is None or new_edition != current_edition:
                            self.editions[player] = new_edition
                        current_badges = self.badges.get(player)
                        badges_changed = (
                            current_badges is None
                            or new_badges_val != current_badges
                        )
                        if badges_changed:
                            self.badges[player] = new_badges_val
                    if badges_changed:
                        self.logger.info(f"badges[{player}]={new_badges_val}")
                        if self.obs and self.obs.is_connected:
                            # Fire-and-forget + _obs_serial: Kivy-Lag darf
                            # Receive-Loop nicht blockieren, aber OBS-Calls
                            # desselben Spielers in Reihenfolge laufen.
                            self._dispatch_on_kivy(self.obs._obs_serial(self.obs.change_badges(player)))
                        if self.overlay_server and self.overlay_server.is_connected:
                            self._dispatch_on_kivy(self.overlay_server.notify_update(player, "badges"))
                await self._persist_teams(unsorted_snapshot)
                if new_teams != self.sorted_teams or not self.initialized:
                    for player in new_teams:
                        # Edition pro Player wieder lokal, falls clear_everything
                        # inzwischen gefeuert hat.
                        edition_val = unsorted_snapshot[player][7]
                        # old_team unter Lock holen; snapshot-Semantik gegen
                        # parallelen Rebind durch clear_everything.
                        with self.state_lock:
                            old_team = self.sorted_teams.get(player)
                        if old_team is None or not self.initialized:
                            if self.obs and self.obs.is_connected:
                                # Initial-Draw fire-and-forget via _obs_serial.
                                # Reihenfolge gegen nachfolgende diff-Updates
                                # bleibt gewahrt, OBS-Lag entkoppelt Receive.
                                self._dispatch_on_kivy(self.obs._obs_serial(self.obs.changeSource(player, range(6), new_teams[player], edition_val)))
                            with self.state_lock:
                                self.sorted_teams[player] = new_teams[player]
                            # initialized erst nach erfolgter sorted_teams-
                            # Zuordnung — sonst wuerde bei einem Fehler im
                            # Dispatch der Flag gesetzt bleiben und der naechste
                            # Tick den Initial-Draw ueberspringen.
                            self.initialized = True
                            if self.overlay_server and self.overlay_server.is_connected:
                                self._dispatch_on_kivy(self.overlay_server.notify_update(player, "team"))
                            continue

                        diff = []
                        team = new_teams[player]
                        for i in range(6):
                            if not team[i].obs_property_changed(old_team[i], self.sp):
                                self.logger.debug(f"{i=},{team[i]=}")
                                diff.append(i)
                        slot_mapping = self.compute_slot_mapping(old_team, team)
                        if self.obs and self.obs.is_connected:
                            self._dispatch_on_kivy(self.obs._obs_serial(self.obs.changeSource(player, diff, team, edition_val, slot_mapping=slot_mapping)))
                        with self.state_lock:
                            self.sorted_teams[player] = team
                        if self.overlay_server and self.overlay_server.is_connected:
                            self._dispatch_on_kivy(self.overlay_server.notify_update(player, "team", slot_mapping=slot_mapping))
            except (UnicodeEncodeError, UnicodeDecodeError) as err:
                self.logger.warning(f"Unicode error:{type(err)},{err}")
                self.logger.warning(f"{traceback.format_exc()}")
            except (UnpicklingError, AttributeError) as err:
                self.logger.warning(f"Pickle Data error:{type(err)},{err}")
                self.logger.warning(f"{traceback.format_exc()}")
            except TRANSIENT_NET_EXCEPTIONS as err:
                # Reader ist tot — weitere receive_message-Aufrufe wuerden nur
                # denselben Fehler in Busy-Loop werfen (vor-2026-10-01-Bug:
                # EOFError-Zweig loggte nur, kein break → 1000+ Fehler/sec).
                # Rate-Limited Log + Diagnose-Kontext + break fuer Reconnect-Pfad.
                last_msg_age = (
                    time.time() - self._last_recv_at if self._last_recv_at else -1.0
                )
                self._log_dedup(
                    type(err).__name__,
                    f"alter_teams Netz-Fehler: {type(err).__name__}: {err} "
                    f"(letzte_msg_type={self._last_recv_type!r}, "
                    f"letzte_msg_vor={last_msg_age:.1f}s, "
                    f"recv_total={self._msg_counters['recv']})",
                    exc=err,
                    level="warning",
                )
                self._record_disconnect(f"recv_{type(err).__name__}", expected_session=my_session)
                break
            except Exception as err:
                self.logger.error(f"alter_teams abgebrochen: {type(err)},{err}")
                self.logger.error(f"{traceback.format_exc()}")
                self._record_disconnect(f"recv_unexpected_{type(err).__name__}", expected_session=my_session)
                break

        await self.disconnect(intentional=False, expected_session=my_session)

    def _ensure_pokedex_db(self):
        if self.configsave is None:
            return
        try:
            current_path = Path(str(self.configsave)).resolve()
            if self.pokedex_db is None or self.pokedex_db.session_path.resolve() != current_path:
                if self.pokedex_db is not None:
                    self.pokedex_db.close()
                self.pokedex_db = PokedexDB(current_path)
                self.pokedex_db.connect()
        except Exception as err:
            self.logger.error(f"_ensure_pokedex_db failed: {type(err)},{err}")
            self.logger.error(f"{traceback.format_exc()}")

    def close_pokedex_db(self) -> bool:
        """Schliesst die aktive PokedexDB-Verbindung (falls offen) und nullt
        die Referenz. Muss vor Datei-Operationen auf ``pokemon.db`` (Snapshot
        Restore, Backup-Import) aufgerufen werden, sonst blockiert der
        Windows-File-Lock das Zurueckschreiben. Rueckgabe ``True`` wenn
        vorher eine Verbindung existierte.

        Exceptions aus ``PokedexDB.close`` werden bewusst geschluckt und nur
        gewarnt, damit Aufrufer wie ``disconnect`` (Host/Port-Reset,
        Reconnect-Zweig) auch bei kaputter DB-Verbindung noch fertig laufen.
        Die Referenz wird in jedem Fall auf ``None`` gesetzt, damit der
        naechste ``_ensure_pokedex_db``-Call eine frische Verbindung baut.
        """
        if self.pokedex_db is None:
            return False
        try:
            self.pokedex_db.close()
        except Exception as err:
            self.logger.warning(f"close_pokedex_db: close failed: {err}")
        self.pokedex_db = None
        return True

    async def update_bag(self, player, edition, pockets):
        """Persistiert die ausgelesenen Bag-Pockets pro Player in die PokedexDB.

        ``pockets`` ist ein Dict {pocket_key: list[BagItem]}. Pro Pocket
        ueberschreibt die DB den bisherigen Bestand fuer (owner, edition, pocket)
        komplett — verlorene Items verschwinden also auch wieder. Parallel
        pflegt upsert_bag_pocket die bag_first_seen-Tabelle (Nuzlocke-Marker),
        die NIE ueberschrieben wird.

        Anders als _persist_teams wird hier nicht aus self.unsorted_teams gelesen,
        sondern direkt vom Bizhawk-Reader uebergeben — der Bag laeuft auf einem
        eigenen Polling-Intervall und ist von der Team-Tick-Logik entkoppelt.

        owner-Format: build_owner(your_name, str(player)) — konsistent zu
        encounter_tracker und _run_active_for_local_player. Frueher wurde nur
        str(player) verwendet, das kollidierte mit has_catching_balls (das
        immer im build_owner-Format sucht) — Folge: Nuzlocke-Run galt trotz
        erhaltener Baelle als "nicht gestartet".
        """
        try:
            self._ensure_pokedex_db()
            if self.pokedex_db is None or self.pokedex_db.connection is None:
                return
            owner = PokedexDB.build_owner(
                self.pl.get('your_name', ''), str(player)
            )
            loop = asyncio.get_event_loop()
            for pocket_key, items in pockets.items():
                await loop.run_in_executor(
                    None,
                    self.pokedex_db.upsert_bag_pocket,
                    owner,
                    edition,
                    pocket_key,
                    items,
                )
            bag_key = f"{owner}_{edition}"
            current_hash = self._bag_hash(pockets)
            if current_hash != self._last_bag_hash.get(bag_key):
                self._last_bag_hash[bag_key] = current_hash
                await self.send_bag_sync(owner, edition, pockets)
        except Exception as err:
            self.logger.error(f"update_bag failed: {type(err)},{err}")
            self.logger.error(f"{traceback.format_exc()}")

    async def send_encounter_sync(self, encounter_dict: dict):
        """Sendet einen Encounter-Record an Arceus zur Weiterverteilung."""
        if not self.is_connected:
            return
        try:
            async with self.writer_lock:
                await self.send_message({
                    "type": "encounter_sync",
                    "encounters": [encounter_dict],
                })
        except Exception as err:
            self.logger.warning(f"send_encounter_sync failed: {err}")

    async def send_encounter_outcome(self, personality: int, owner: str, outcome: str):
        """Sendet ein Outcome-Update an Arceus zur Weiterverteilung."""
        if not self.is_connected:
            return
        try:
            async with self.writer_lock:
                await self.send_message({
                    "type": "encounter_outcome",
                    "personality": personality,
                    "owner": owner,
                    "outcome": outcome,
                })
        except Exception as err:
            self.logger.warning(f"send_encounter_outcome failed: {err}")

    async def send_soullink_death(self, personality: int, owner: str,
                                    edition=None, cause: str = "faint"):
        """Meldet dem Server, dass ein Pokemon gestorben ist."""
        if not self.is_connected:
            return
        try:
            async with self.writer_lock:
                await self.send_message({
                    "type": "soullink_death",
                    "personality": personality,
                    "owner": owner,
                    "edition": edition,
                    "cause": cause,
                })
        except Exception as err:
            self.logger.warning(f"send_soullink_death failed: {err}")

    async def send_soullink_config(self, config: dict):
        """Setzt/aktualisiert die Soullink-Config auf dem Server."""
        if not self.is_connected:
            return
        try:
            async with self.writer_lock:
                await self.send_message({
                    "type": "soullink_config",
                    "config": config,
                })
        except Exception as err:
            self.logger.warning(f"send_soullink_config failed: {err}")

    async def send_soullink_versus_battle(self, winner: str, loser: str, label: str = ""):
        """Meldet manuelles PvP-Battle-Result zwischen zwei Teams."""
        if not self.is_connected:
            return
        try:
            async with self.writer_lock:
                await self.send_message({
                    "type": "soullink_versus_battle",
                    "winner": winner,
                    "loser": loser,
                    "label": label,
                })
        except Exception as err:
            self.logger.warning(f"send_soullink_versus_battle failed: {err}")

    async def send_soullink_token_earned(self, owner: str):
        await self._send_timer_message({
            "type": "soullink_token_earned",
            "owner": owner,
        })

    async def send_soullink_token_redeem(self, owner: str, edition, route: int):
        await self._send_timer_message({
            "type": "soullink_token_redeem",
            "owner": owner,
            "edition": edition,
            "route": int(route),
        })

    async def _send_timer_message(self, msg: dict):
        if not self.is_connected:
            self.logger.info(f"timer msg '{msg.get('type')}' verworfen — nicht verbunden")
            return
        try:
            async with self.writer_lock:
                await self.send_message(msg)
        except Exception as err:
            self.logger.warning(f"send timer msg failed: {err}")

    async def send_timer_start(self):
        await self._send_timer_message({"type": "timer_start"})

    async def send_timer_reset(self):
        await self._send_timer_message({"type": "timer_reset"})

    async def send_timer_split(self, label: str, source: str = "manual"):
        await self._send_timer_message({"type": "timer_split", "label": label, "source": source})

    async def send_timer_pause_request(self):
        await self._send_timer_message({"type": "timer_pause_request"})

    async def send_timer_resume_request(self):
        await self._send_timer_message({"type": "timer_resume_request"})

    def _handle_timer_state(self, timer: dict):
        self.timer_state = timer
        self.timer_last_tick = {
            "elapsed": timer.get("elapsed", 0.0),
            "server_ts": timer.get("server_ts", time.time()),
            "local_ts": time.time(),
            "running": timer.get("running", False),
        }
        self.logger.info(
            f"timer_state empfangen: running={timer.get('running')} "
            f"elapsed={timer.get('elapsed'):.1f}s splits={len(timer.get('splits', []))} "
            f"pause_consent={len(timer.get('pause_consent', []))}/{timer.get('consent_required')}"
        )

    def _handle_timer_tick(self, data: dict):
        self.timer_last_tick = {
            "elapsed": data.get("elapsed", 0.0),
            "server_ts": data.get("server_ts", time.time()),
            "local_ts": time.time(),
            "running": data.get("running", False),
        }

    def current_timer_elapsed(self) -> float:
        """Lokal gerechneter Timer-Wert. Verwendet letzten Tick als Anker."""
        if not self.timer_last_tick:
            return 0.0
        base = self.timer_last_tick.get("elapsed", 0.0)
        if not self.timer_last_tick.get("running"):
            return base
        return base + (time.time() - self.timer_last_tick.get("local_ts", time.time()))

    async def send_countdown_start(self, duration_seconds: float, label: str = ""):
        await self._send_timer_message({
            "type": "countdown_start",
            "duration_seconds": float(duration_seconds),
            "label": label,
        })

    async def send_countdown_pause(self):
        await self._send_timer_message({"type": "countdown_pause"})

    async def send_countdown_resume(self):
        await self._send_timer_message({"type": "countdown_resume"})

    async def send_countdown_cancel(self):
        await self._send_timer_message({"type": "countdown_cancel"})

    def _handle_countdown_state(self, cd: dict):
        self.countdown_state = cd
        self.countdown_last_tick = {
            "remaining": cd.get("remaining_seconds", 0.0),
            "server_ts": cd.get("server_ts", time.time()),
            "local_ts": time.time(),
            "running": cd.get("running", False),
        }
        self.logger.info(
            f"countdown_state empfangen: running={cd.get('running')} "
            f"remaining={cd.get('remaining_seconds'):.1f}s label='{cd.get('label')}'"
        )

    def _handle_countdown_tick(self, data: dict):
        self.countdown_last_tick = {
            "remaining": data.get("remaining_seconds", 0.0),
            "server_ts": data.get("server_ts", time.time()),
            "local_ts": time.time(),
            "running": data.get("running", False),
        }

    def _handle_countdown_finished(self, data: dict):
        label = data.get("label", "")
        duration = data.get("duration_seconds", 0.0)
        self.logger.info(f"countdown_finished empfangen: label='{label}' dauer={duration}s")
        if self.countdown_finished_callback is not None:
            try:
                self.countdown_finished_callback(label, duration)
            except Exception as err:
                self.logger.error(f"countdown_finished_callback failed: {type(err)},{err}")

    async def _notify_overlay_session(self, event_type: str, payload):
        srv = self.overlay_server
        if srv is None or not srv.is_connected:
            return
        # Fire-and-forget auf Kivy-Loop (OverlayServer-SSE-Queues dort
        # gebunden). Receive-Loop koppelt nicht an GUI-Lag.
        self._dispatch_on_kivy(srv.notify_session_event(event_type, payload))

    async def _run_active_for_local_player(self, player_id, edition) -> bool:
        """True wenn der Nuzlocke-Run für diesen lokalen Player scharf ist.

        Gate für Death-/Wipe-Detection: solange `rule_run_start_on_ball` an
        ist und der Spieler noch nie einen Ball hatte, laufen Kämpfe (Rivalen-
        Fight vor Route 1, HP=0 durch Kampf-Ende) ins Leere, ohne einen
        `soullink_death` oder `total_wipe` zu triggern.

        `has_catching_balls` ist monoton (bag_first_seen) — einmal True,
        bleibt True. Rückgabe False nur wenn Regel aktiv UND noch nie Ball.

        async: PokedexDB serialisiert ueber ein RLock (`access_lock`) das
        auch von Executor-Threads gehalten wird (z.B. parallel laufende
        upsert_team-Calls in _persist_teams). Direkter sync-Call wuerde den
        Event-Loop-Thread blocken, wenn der Lock hoch steht — deshalb via
        run_in_executor, konsistent mit den Nachbaraufrufen im Umfeld.
        """
        if not self.nuz.get("rule_run_start_on_ball", True):
            return True
        if self.pokedex_db is None or self.pokedex_db.connection is None:
            return False
        owner = PokedexDB.build_owner(self.pl.get('your_name', ''), str(player_id))
        loop = asyncio.get_event_loop()
        try:
            return await loop.run_in_executor(
                None, self.pokedex_db.has_catching_balls, owner, edition
            )
        except Exception as err:
            self.logger.warning(
                f"_run_active_for_local_player has_catching_balls failed: {err}"
            )
            return False

    def _evaluate_team_deaths(self, player_id, team) -> list[tuple]:
        """Ein `_is_pokemon_dead`-Pass über das Team. Gibt [(pv, dexnr, is_dead)] zurück.

        WICHTIG: `_is_pokemon_dead` mutiert den Zero-Read-Zähler — pro Team-Tick
        nur EINMAL aufrufen und das Ergebnis an Wipe-Check UND Death-Report geben.
        """
        result = []
        for slot in team[:6] if team else []:
            if slot is None:
                continue
            if isinstance(slot, dict):
                dex = slot.get("dexnr")
                pv = slot.get("personality")
            else:
                dex = getattr(slot, "dexnr", None)
                pv = getattr(slot, "personality", None)
            if not dex:
                continue
            result.append((pv, dex, self._is_pokemon_dead(player_id, slot)))
        return result

    async def _report_deaths(self, player_id, edition, dead_infos: list[tuple]):
        """Meldet erkannte Einzel-Tode einmalig an den Server (soullink_death).

        Debounce über `_reported_deaths` — lebt ein Pokemon wieder (Heilung/Revive
        oder Fehl-Read), wird der Marker gelöscht und ein erneuter Tod wieder
        gemeldet. Ohne Verbindung landet die Meldung in `_pending_death_reports`
        und wird beim nächsten Team-Tick mit Verbindung nachgereicht.
        """
        owner = PokedexDB.build_owner(self.pl.get('your_name', ''), str(player_id))
        for pv, dexnr, is_dead in dead_infos:
            if pv is None:
                continue
            key = (pv, owner)
            if is_dead:
                if key in self._reported_deaths:
                    continue
                self._reported_deaths.add(key)
                self.logger.info(f"Death erkannt: owner={owner} pv={pv} dexnr={dexnr}")
                self._pending_death_reports.append(
                    {"personality": pv, "owner": owner, "edition": edition}
                )
            else:
                self._reported_deaths.discard(key)
        if self.is_connected and self._pending_death_reports:
            pending, self._pending_death_reports = self._pending_death_reports, []
            for report in pending:
                await self.send_soullink_death(
                    report["personality"], report["owner"], edition=report["edition"]
                )

    async def check_total_wipe(self, player_id, team, dead_infos: list[tuple] | None = None) -> bool:
        """Prüft rule_restart_on_total_wipe: alle Team-Slots tot → Broadcast Warning.

        Debounce: nach einem erkannten Wipe wird `_wipe_signaled[player_id]=True`
        gesetzt und erst wieder gelöscht sobald ein Pokemon wieder lebt. So
        entsteht keine Broadcast-Schleife bei jedem Team-Tick.

        HP-Consistency (`_is_pokemon_dead`): filtert kurze RAM-Fluktuationen
        (Threshold: WIPE_ZERO_READ_THRESHOLD consecutive Zero-Reads) und
        Gen-6/7-Battle-RAM-Ausreißer (max_hp-Wechsel ohne plausibles Level-Up).

        dead_infos: vorberechnetes Ergebnis aus `_evaluate_team_deaths` (ein Pass
        pro Tick). Ohne Angabe wird selbst ausgewertet (Standalone-Aufruf).
        """
        if not self._nuzlocke_rules_active():
            return False
        if not self.nuz.get("rule_restart_on_total_wipe", False):
            return False
        if dead_infos is None:
            dead_infos = self._evaluate_team_deaths(player_id, team)
        any_pokemon = bool(dead_infos)
        alive = sum(1 for (_pv, _dex, is_dead) in dead_infos if not is_dead)
        if not any_pokemon:
            # Team leer (z.B. vor ROM-Load) — kein Wipe, aber auch nicht als Wipe zaehlen.
            return False
        if alive > 0:
            # Debounce zuruecksetzen sobald Team wieder lebt.
            if getattr(self, "_wipe_signaled", None) is None:
                self._wipe_signaled = {}
            self._wipe_signaled.pop(player_id, None)
            return False
        # alive_count = 0 UND any_pokemon vorhanden → Wipe
        if getattr(self, "_wipe_signaled", None) is None:
            self._wipe_signaled = {}
        if self._wipe_signaled.get(player_id):
            return True  # bereits gemeldet
        self._wipe_signaled[player_id] = True
        self._last_wipe_player_id = player_id
        self.logger.warning(f"Total Wipe erkannt für player_id={player_id}")
        if self.is_connected:
            try:
                async with self.writer_lock:
                    await self.send_message({
                        "type": "soullink_rule_violation",
                        "violation": {
                            "type": "total_wipe",
                            "subject": str(player_id),
                            "player_id": player_id,
                            "message": "Alle Pokemon gefallen — Run-Neustart erforderlich",
                            "timestamp": time.time(),
                        },
                    })
            except Exception as err:
                self.logger.warning(f"total_wipe send failed: {err}")
        return True

    async def dismiss_wipe(self, subject_pid: int | None):
        """User hat das TotalWipe-Popup per Cancel geschlossen.

        1. Wipe-Marker `_wipe_signaled[subject_pid]` bleibt bewusst gesetzt.
           Der natuerliche Debounce in `check_total_wipe` unterdrueckt weitere
           Broadcasts, solange kein Team-Slot wieder lebt — genau das Verhalten,
           das der User beim "Popup schliessen" erwartet: Ruhe. Ein frueherer
           Fix-Ansatz (Marker poppen) hat bei stuck-Wipe (echtem Team=0/0)
           dazu gefuehrt, dass das Popup nach dem Server-Dedupe-Fenster (5s)
           wieder aufpoppte, weil check_total_wipe erneut den Marker frisch
           setzte und broadcastete.
        2. Bei Team-Modi (coop, versus) einen `wipe_dismissed`-Broadcast an
           den Server senden, der ihn an alle Clients weiterleitet. Team-
           Mitglieder des subject_pid schliessen dann automatisch ihr Popup.
           Bei nuzlocke/disabled/versus_ffa jeder Client selbst entscheidet
           → kein Broadcast, weil kein sinnvolles Shared-Team-Konzept.
        """
        # Marker BEHALTEN — nicht poppen, siehe Docstring Punkt 1.

        mode = str(self.nuz.get("soullink_mode", "")).strip().lower()
        if mode not in ("coop", "versus"):
            self.logger.debug(
                f"dismiss_wipe: kein Broadcast (mode={mode!r}, nur coop/versus)"
            )
            return
        if not self.is_connected or subject_pid is None:
            return
        try:
            async with self.writer_lock:
                await self.send_message({
                    "type": "wipe_dismissed",
                    "subject_pid": int(subject_pid),
                    "timestamp": time.time(),
                })
            self.logger.info(
                f"wipe_dismissed gesendet: subject_pid={subject_pid} mode={mode}"
            )
        except Exception as err:
            self.logger.warning(f"wipe_dismissed send failed: {err}")

    def _handle_remote_wipe_dismissed(self, subject_pid: int):
        """Anderer Client hat Wipe-Popup gecancelled.

        Wenn wir im selben Team wie subject_pid sind, unser Popup ebenfalls
        schliessen und lokalen Wipe-Marker fuer alle unsere Player clearen.

        Team-Check:
          - coop:   alle im selben Team, immer dismissen
          - versus: soullink_team_membership map "owner_name" -> team_letter
                    subject und mind. einer unserer lokalen Player muessen
                    im gleichen team_letter sein
          - andere: ignoriert (sollte nicht broadcastet worden sein)
        """
        mode = str(self.nuz.get("soullink_mode", "")).strip().lower()
        share_team = False
        if mode == "coop":
            share_team = True
        elif mode == "versus":
            team_map = self.nuz.get("soullink_team_membership") or {}
            subject_owner = self.player_names.get(int(subject_pid), "")
            subject_team = team_map.get(subject_owner)
            if subject_team:
                with self.state_lock:
                    pid_snapshot = list(self.bizhawk_teams.keys())
                my_owners = [self.player_names.get(pid, "") for pid in pid_snapshot]
                my_teams = {team_map.get(o) for o in my_owners if o}
                share_team = subject_team in my_teams
        if not share_team:
            self.logger.debug(
                f"wipe_dismissed empfangen: subject_pid={subject_pid} "
                f"mode={mode} — kein Share-Team, ignoriere"
            )
            return
        self.logger.info(
            f"wipe_dismissed empfangen (Team-Match): subject_pid={subject_pid} "
            f"mode={mode} — Popup schliessen (Marker bleibt fuer Debounce)"
        )
        # Marker BEHALTEN, analog dismiss_wipe: sonst wuerde bei stuck-Wipe
        # der naechste _persist_teams-Tick check_total_wipe erneut triggern
        # und die Popups poppen 5s spaeter (nach Server-Dedupe) alle wieder auf.
        cb = getattr(self, "on_wipe_dismissed_callback", None)
        if callable(cb):
            try:
                cb(int(subject_pid))
            except Exception as err:
                self.logger.warning(f"on_wipe_dismissed_callback failed: {err}")

    def get_run_manager(self) -> RunManager | None:
        """Baut den RunManager pro Aufruf frisch gegen den aktuellen
        Session-Pfad. configsave ist eine langlebige MutableString-Referenz,
        deren .text bei Session-Wechsel in-place mutiert — ein gecachter
        RunManager würde auf den alten Session-Ordner zeigen.
        """
        if self.configsave is None:
            self.logger.warning("get_run_manager: configsave nicht gesetzt")
            return None
        try:
            return RunManager(str(self.configsave))
        except Exception as err:
            self.logger.error(f"RunManager-Init fehlgeschlagen: {type(err).__name__},{err}")
            self.logger.error(traceback.format_exc())
            return None

    def archive_active_run(self) -> str | None:
        """Schließt den aktuell aktiven Run wegen Total-Wipe ab.

        Ruft RunManager.finalize_active_run mit reason='total_wipe' und
        wipe_player_id (aus check_total_wipe gemerkt). Legt einen DB-Snapshot
        als Datei im Run-Ordner ab (via VACUUM INTO), leert danach die
        encounters-Tabelle und setzt die Wipe-Debounce zurück.

        Rückgabe: run_id oder None wenn kein aktiver Run existiert / Fehler.
        """
        return self._finalize_run(reason="total_wipe",
                                   wipe_player_id=self._last_wipe_player_id)

    def end_run_manual(self, reason: str = "manual") -> str | None:
        """Beendet den aktuell aktiven Run auf Benutzeraktion (Button im UI).

        Kein wipe_player_id, aber ansonsten identisches Verhalten zu
        archive_active_run (DB-Snapshot + encounters leeren).
        """
        return self._finalize_run(reason=reason, wipe_player_id=None)

    def _serialize_final_teams(self) -> dict:
        """Serialisiert den letzten bekannten Team-Zustand pro Spieler für die
        run_meta.yml. Format:

        ``{"players": {"<pid>": {"name": ..., "edition": ..., "slots": [
            {"slot": 1, "dexnr": 25, "nickname": "Pikachu", "lvl": 30}, ...
        ]}}}``

        Leere Slots werden mit ``None``-Feldern eingefügt, damit die Länge (6)
        erhalten bleibt.
        """
        # Snapshot unter state_lock: unsorted_teams wird vom BH-Thread
        # befuellt (bizhawk.py), direkt `for ... in ...` wuerde sonst in
        # `dict changed size during iteration` laufen, wenn BH mitten im
        # Finalize einen neuen Player-Key schreibt. sorted_teams ist zwar
        # Kivy-only, aber Fallback-or-Chain evaluiert zuerst sorted; falls
        # leer, kommt unsorted dran — Snapshot der Fallback-Quelle reicht.
        if self.sorted_teams:
            teams_src = dict(self.sorted_teams)
        else:
            with self.state_lock:
                teams_src = dict(self.unsorted_teams)
        out_players: dict[str, dict] = {}
        for pid, team in teams_src.items():
            slots: list[dict] = []
            for idx, slot in enumerate(list(team or [])[:6], start=1):
                if slot is None:
                    slots.append({"slot": idx, "dexnr": None,
                                    "nickname": None, "lvl": None})
                    continue
                if isinstance(slot, dict):
                    dex = slot.get("dexnr")
                    nick = slot.get("nickname")
                    lvl = slot.get("lvl")
                else:
                    dex = getattr(slot, "dexnr", None)
                    nick = getattr(slot, "nickname", None)
                    lvl = getattr(slot, "lvl", None)
                slots.append({
                    "slot": idx,
                    "dexnr": dex if dex not in (0, "egg", None) else dex,
                    "nickname": nick or None,
                    "lvl": lvl,
                })
            out_players[str(pid)] = {
                "name": self.player_names.get(pid, ""),
                "edition": self.editions.get(pid, ""),
                "slots": slots,
            }
        return {"players": out_players}

    def _finalize_run(self, reason: str,
                     wipe_player_id: int | str | None) -> str | None:
        rm = self.get_run_manager()
        if rm is None:
            self.logger.error("_finalize_run: RunManager nicht verfügbar")
            return None
        active = rm.get_active_run()
        if active is None:
            self.logger.info("_finalize_run: kein aktiver Run vorhanden")
            return None
        self.logger.info(
            f"_finalize_run enter: reason={reason} wipe_player_id={wipe_player_id} "
            f"active_run={active.get('run_id') if isinstance(active, dict) else active}"
        )

        self._ensure_pokedex_db()
        db_path = None
        # _finalize_run läuft aus einem run_in_executor-Worker-Thread. Direkte
        # Zugriffe auf self.pokedex_db.connection sowie das VACUUM INTO durch
        # RunManager greifen auf dieselbe SQLite-Datei zu wie parallele
        # alter_teams-Executor-Calls. access_lock (RLock) serialisiert.
        if self.pokedex_db is not None:
            try:
                with self.pokedex_db.access_lock:
                    if self.pokedex_db.connection is not None:
                        self.pokedex_db.connection.commit()
                    db_path = self.pokedex_db.db_path
            except Exception as err:
                self.logger.warning(f"_finalize_run: pokedex_db commit failed: {err}")

        final_teams = self._serialize_final_teams()
        # Snapshot + DELETE FROM encounters unter EINEM Lock-Halt, damit
        # zwischen Snapshot und Leerung kein zusätzlicher Encounter über einen
        # anderen Executor-Call reinschneit und dann verloren geht.
        # Ok für Event-Loop-Kontention, weil alle DB-Caller (alter_teams,
        # _handle_remote_*, _handle_new_pokemon) über run_in_executor laufen.
        run_id = None
        deleted_rows: int | str = "n/a"
        if self.pokedex_db is not None:
            with self.pokedex_db.access_lock:
                run_id = rm.finalize_active_run(reason=reason,
                                                 wipe_player_id=wipe_player_id,
                                                 pokedex_db_path=db_path,
                                                 final_teams=final_teams)
                try:
                    if (run_id is not None
                            and self.pokedex_db.connection is not None):
                        cur = self.pokedex_db.connection.execute("DELETE FROM encounters")
                        deleted_rows = cur.rowcount if cur.rowcount is not None else "?"
                        self.pokedex_db.connection.commit()
                except Exception as err:
                    self.logger.warning(
                        f"_finalize_run: encounters leeren failed: {err}")
        else:
            run_id = rm.finalize_active_run(reason=reason,
                                             wipe_player_id=wipe_player_id,
                                             pokedex_db_path=db_path,
                                             final_teams=final_teams)

        if run_id is None:
            self.logger.error("_finalize_run: finalize_active_run gab None zurück")
            return None

        if getattr(self, "_wipe_signaled", None) is not None:
            self._wipe_signaled.clear()
        self._last_wipe_player_id = None
        self.logger.info(
            f"_finalize_run exit: run_id={run_id} reason={reason} "
            f"deleted_encounter_rows={deleted_rows}"
        )
        return run_id

    def _is_pokemon_dead(self, player_id, slot) -> bool:
        """Consistency-Check für HP=0.

        Ein Pokemon gilt erst als tot, wenn:
        1. cur_hp konsistent 0 ist über WIPE_ZERO_READ_THRESHOLD aufeinanderfolgende Reads
        2. Der Read plausibel ist: max_hp identisch zum letzten Read ODER Level-Diff in {0,1}
           (letzteres deckt in-battle Level-Up ab).

        max_hp-Wechsel mit unplausiblem Level-Diff = wahrscheinlich Battle-RAM-Read
        an falscher Adresse (Gen 6/7): Read verwerfen, Debounce-Zähler nicht erhöhen.
        """
        if isinstance(slot, dict):
            pv = slot.get("personality")
            cur_hp = slot.get("cur_hp")
            max_hp = slot.get("max_hp")
            lvl = slot.get("lvl")
        else:
            pv = getattr(slot, "personality", None)
            cur_hp = getattr(slot, "cur_hp", None)
            max_hp = getattr(slot, "max_hp", None)
            lvl = getattr(slot, "lvl", None)

        if pv is None:
            # Ohne PID kein Consistency-Tracking möglich — direkter Check.
            return cur_hp is not None and cur_hp == 0

        key = (player_id, pv)
        state = self._pokemon_hp_state.get(key)
        if state is None:
            state = {"last_max_hp": max_hp, "last_lvl": lvl, "zero_reads": 0}
            self._pokemon_hp_state[key] = state
            # Beim allerersten Read kein Zero-Zaehler-Increment (keine Baseline für
            # Consistency vorhanden). Erst ab dem zweiten Read greift der Filter.
            return False

        # Consistency: max_hp-Wechsel ohne plausibles Level-Up → Read verwerfen.
        if (state["last_max_hp"] is not None and max_hp is not None
                and max_hp != state["last_max_hp"]):
            last_lvl = state["last_lvl"]
            plausible = False
            if last_lvl is not None and lvl is not None:
                lvl_diff = lvl - last_lvl
                if lvl_diff in (0, 1):
                    plausible = True
            if not plausible:
                self.logger.debug(
                    f"HP-Read verworfen (Battle-RAM?): player={player_id} pv={pv} "
                    f"max_hp {state['last_max_hp']}->{max_hp} lvl {last_lvl}->{lvl}"
                )
                # Zero-Reads NICHT hochzählen, state NICHT aktualisieren
                return state["zero_reads"] >= WIPE_ZERO_READ_THRESHOLD

        # Read akzeptieren + State updaten
        state["last_max_hp"] = max_hp
        state["last_lvl"] = lvl

        if cur_hp is None:
            state["zero_reads"] = 0
            return False
        if cur_hp == 0:
            state["zero_reads"] += 1
            if state["zero_reads"] >= WIPE_ZERO_READ_THRESHOLD:
                return True
            return False
        state["zero_reads"] = 0
        return False

    def _nuzlocke_rules_active(self) -> bool:
        """False bei soullink_mode 'disabled' — dann greifen keine Nuzlocke-Regeln.
        Legacy-Wert 'off' und fehlender Key bedeuten 'nuzlocke' (Regeln aktiv)."""
        return (self.nuz.get("soullink_mode") or "nuzlocke") != "disabled"

    def check_nickname_required(self, pokemon, edition, default_species_name) -> bool:
        """rule_nickname_required: prüft ob Nickname vom Default-Species-Namen abweicht."""
        if not self._nuzlocke_rules_active():
            return True
        if not self.nuz.get("rule_nickname_required", True):
            return True
        nickname = getattr(pokemon, "nickname", "") or ""
        default = default_species_name or ""
        # Wenn Nickname leer oder gleich Species-Default → Verstoss (return False)
        return bool(nickname and nickname.strip() and nickname.strip().upper() != default.strip().upper())

    async def _push_timer_text_to_obs(self):
        """Setzt eine OBS-Text-Source 'RaceTimer' auf den aktuellen Timer-Wert.

        Optional: Nur aktiv wenn OBS-WebSocket verbunden UND eine Input mit
        exakt diesem Namen existiert. Bei Fehler: still ignorieren, damit
        andere Overlay-Wege (Browser-Source) unabhängig weiterlaufen.
        """
        if self.obs is None or not self.obs.is_connected:
            return
        elapsed = self.current_timer_elapsed()
        s = int(max(0, elapsed))
        text = f"{s // 3600:02d}:{(s // 60) % 60:02d}:{s % 60:02d}"
        try:
            import simpleobsws
            # Fire-and-forget auf Kivy-Loop + _obs_serial: OBS-Call-Reihenfolge
            # bleibt konsistent mit alter_teams-Dispatches, kein Ueberlappen.
            self._dispatch_on_kivy(self.obs._obs_serial(self.obs.ws.call(simpleobsws.Request(
                "SetInputSettings",
                {
                    "inputName": "RaceTimer",
                    "inputSettings": {"text": text},
                    "overlay": True,
                },
            ))))
        except Exception as err:
            # OBS-Text-Source ist optional — kein Grund für Log-Spam.
            self.logger.debug(f"OBS RaceTimer-Text set failed: {err}")

    def current_countdown_remaining(self) -> float:
        """Lokal gerechneter Rest-Countdown. Anker = letzter Server-Tick."""
        if not self.countdown_last_tick:
            return 0.0
        base = float(self.countdown_last_tick.get("remaining", 0.0))
        if not self.countdown_last_tick.get("running"):
            return base
        return max(0.0, base - (time.time() - self.countdown_last_tick.get("local_ts", time.time())))

    def _handle_remote_death(self, death: dict):
        pv = death.get("personality")
        owner = death.get("owner")
        if pv is None or not owner:
            return
        with self.state_lock:
            self.soullink_deaths[(pv, owner)] = death
        partners = death.get("partners", [])
        # Ist dieser Client Owner eines Partners? Dann lokale UI/DB-Reaktion nötig.
        my_partner = None
        for partner in partners:
            for pid, edition in self.editions.items():
                partner_owner = str(pid)
                if partner_owner == partner.get("owner"):
                    my_partner = partner
                    break
        self.logger.info(
            f"soullink_death empfangen: owner={owner} pv={pv} "
            f"link={death.get('link_id')} own_partner={my_partner}"
        )
        # Falls Server auch Link-Group als failed markiert hat, kommt separater
        # soullink_link_state-Broadcast; hier keine doppelte Verarbeitung.

    async def send_bag_sync(self, owner: str, edition, pockets: dict):
        """Sendet Bag-Inhalt an Arceus. BagItem → (id, qty) Tupel."""
        if not self.is_connected:
            return
        try:
            wire_pockets = {}
            for pocket_key, items in pockets.items():
                wire_pockets[pocket_key] = [(it.id, it.qty) for it in items if it.id != 0 and it.qty != 0]
            async with self.writer_lock:
                await self.send_message({
                    "type": "bag_sync",
                    "owner": owner,
                    "edition": str(edition) if edition is not None else "",
                    "pockets": wire_pockets,
                })
        except Exception as err:
            self.logger.warning(f"send_bag_sync failed: {err}")

    async def send_rando_moves_sync(self, player_id: int,
                                     tm_moves: dict[int, str] | None,
                                     hm_moves: dict[int, str] | None):
        """Verteilt die randomisierten TM/HM→Move-Zuordnungen an alle Clients.

        Jeder Client randomized seine eigene ROM mit eigenem Seed, deshalb braucht
        das Overlay/OBS auf jedem Host die Zuordnung des **jeweiligen** Spielers,
        nicht nur der lokalen. Ohne diese Message faellt der tm_type_resolver auf
        den Vanilla-LUT zurueck und Browser-Source zeigt generische TM-Icons
        statt der randomisierten Typ-Icons.

        Zuverlaessigkeit (R3 KRITISCH/WARNUNG sync):

        - Payload wird IMMER zuerst in ``_pending_rando_moves[pid]`` abgelegt
          (identity-aktueller Snapshot).
        - Der eigentliche Send delegiert an ``_try_send_pending_payload``,
          das Identity-Check beim Pop macht: nur wenn der aktuell gequeuete
          Payload identisch zum gesendeten ist, wird gepopt — sonst hat
          parallel ein frischerer Direct-Send einen neuen Payload gestellt,
          und der alte darf nicht gewinnen.
        - ``CancelledError`` wird NICHT in Exception geschluckt; der Payload
          bleibt dann unangetastet in der Queue und wird beim naechsten
          Flush retried.
        - Legacy-Server (``_server_supports_resume is False``) kennt den
          Message-Typ nicht. Senden wuerde dort als Teams-Dict interpretiert
          werden — ueberspringen und nur queuen.
        """
        pid = int(player_id)
        payload = {
            "tm_moves": dict(tm_moves or {}),
            "hm_moves": dict(hm_moves or {}),
        }
        self._pending_rando_moves[pid] = payload
        if not self.is_connected:
            self.logger.info(
                f"send_rando_moves_sync: not connected — queued player_id={pid}, "
                f"tms={len(payload['tm_moves'])}, hms={len(payload['hm_moves'])}"
            )
            return
        # Capability-Guard: nur bei bestaetigtem resume (True) senden.
        # None = unbekannt (vor resume_ack), False = Legacy-Server.
        # Beide Faelle: queuen, resume_ack-Flush oder naechster Reconnect
        # retried spaeter. Vorher ging None-Pfad durch, dort wuerde ein
        # Legacy-Server die Nachricht als Teams-Dict fehlinterpretieren
        # (R4 WARNUNG sync).
        if self._server_supports_resume is not True:
            self.logger.info(
                f"send_rando_moves_sync: resume capability unbestaetigt "
                f"({self._server_supports_resume!r}) — queued player_id={pid}"
            )
            return
        await self._try_send_pending_payload(pid, payload)

    async def _try_send_pending_payload(self, pid: int, payload: dict):
        """Sendet einen Rando-Moves-Payload und poppt ihn aus der Queue nur,
        wenn er beim Pop-Zeitpunkt noch der aktuelle Eintrag ist.
        """
        try:
            async with self.writer_lock:
                await self.send_message({
                    "type": "rando_moves_sync",
                    "player_id": pid,
                    "tm_moves": payload["tm_moves"],
                    "hm_moves": payload["hm_moves"],
                })
            # Identity-Pop: ein paralleler Direct-Send koennte zwischen
            # send-Beginn und hier einen frischen Payload gestellt haben.
            # Nur poppen, wenn unsere Referenz noch die aktuelle ist, sonst
            # bleibt der frische Payload fuer den naechsten Flush.
            if self._pending_rando_moves.get(pid) is payload:
                del self._pending_rando_moves[pid]
            self.logger.info(
                f"send_rando_moves_sync: player_id={pid}, "
                f"tms={len(payload['tm_moves'])}, hms={len(payload['hm_moves'])}"
            )
        except asyncio.CancelledError:
            # Cancel (Disconnect) — Payload bleibt in Queue, Flush beim
            # naechsten Connect retried. Nicht in Exception schlucken.
            raise
        except Exception as err:
            self.logger.warning(
                f"send_rando_moves_sync failed: {err} — bleibt gequeued"
            )

    async def _flush_pending_rando_moves(self):
        """Nach erfolgreichem Connect alle gequeuten rando_moves-Payloads an
        den Server pushen.

        Identity-geschuetztes Snapshot-Iterate: Flush liest payload-Referenz
        aus Queue. Falls zwischenzeitlich ein Direct-Send einen frischen
        Payload fuer denselben pid gestellt hat, zeigt die Queue nicht mehr
        auf unseren Snapshot und wir skippen — der frische Payload wird
        durch Direct-Send oder eine spaetere Flush-Runde verschickt.
        """
        try:
            if not self._pending_rando_moves:
                return
            # Entry-Guard: disconnect zwischen Scheduling und Flush-Run →
            # Writer tot, send_message wuerde nur warn-spammen. Payload
            # bleibt in Queue (kein Verlust) — naechster Reconnect-Flush
            # pusht ihn.
            if not self.is_connected:
                self.logger.info(
                    "_flush_pending_rando_moves: nicht verbunden, "
                    "Payloads bleiben gequeued"
                )
                return
            # Symmetrisch zum Direct-Send-Guard: nur bei bestaetigtem True
            # flushen. None (vor resume_ack) oder False (Legacy) → skip.
            # Verhindert, dass ein Refactoring, das den Flush frueher aufruft,
            # versehentlich an einen Legacy-Server pusht (R6 cavecrew).
            if self._server_supports_resume is not True:
                self.logger.info(
                    f"_flush_pending_rando_moves: resume capability "
                    f"{self._server_supports_resume!r} — Flush uebersprungen"
                )
                return
            items = list(self._pending_rando_moves.items())
            self.logger.info(
                f"_flush_pending_rando_moves: {len(items)} queued payload(s)"
            )
            for pid, payload in items:
                # Pro Iteration erneut pruefen: Disconnect kann waehrend des
                # Flush-Loops passieren (sleep-Zyklen in send_message).
                if not self.is_connected:
                    self.logger.info(
                        "_flush_pending_rando_moves: Verbindung mittendrin "
                        "verloren, Rest bleibt gequeued"
                    )
                    return
                if self._pending_rando_moves.get(pid) is not payload:
                    self.logger.info(
                        f"_flush_pending_rando_moves: pid={pid} von Direct-Send "
                        f"ueberholt, skip"
                    )
                    continue
                await self._try_send_pending_payload(pid, payload)
        except asyncio.CancelledError:
            raise
        except Exception as err:
            self.logger.warning(
                f"_flush_pending_rando_moves failed: {err}"
            )
            self.logger.warning(traceback.format_exc())

    async def _handle_remote_encounters(self, encounters: list[dict]):
        # DB-Calls über run_in_executor, weil sync_encounter unter dem
        # PokedexDB.access_lock läuft und der Event-Loop-Thread sonst bei
        # Kontention mit _finalize_run einfriert.
        self._ensure_pokedex_db()
        if self.pokedex_db is None or self.pokedex_db.connection is None:
            return
        loop = asyncio.get_event_loop()
        count = 0
        for enc in encounters:
            try:
                inserted = await loop.run_in_executor(
                    None, self.pokedex_db.sync_encounter, enc)
            except Exception as err:
                self.logger.warning(f"sync_encounter failed: {err}")
                continue
            if inserted:
                count += 1
        if count:
            self.logger.info(
                f"Remote-Encounters empfangen: {count} von {len(encounters)} "
                f"eingefügt/aktualisiert")

    async def _handle_remote_outcome(self, data: dict):
        self._ensure_pokedex_db()
        if self.pokedex_db is None or self.pokedex_db.connection is None:
            return
        loop = asyncio.get_event_loop()
        try:
            await loop.run_in_executor(
                None, self.pokedex_db.update_encounter_outcome,
                data["personality"], data["owner"], data["outcome"])
        except Exception as err:
            self.logger.warning(f"update_encounter_outcome failed: {err}")

    async def _handle_remote_bag(self, data: dict):
        self._ensure_pokedex_db()
        if self.pokedex_db is None or self.pokedex_db.connection is None:
            return
        owner = data.get("owner", "")
        edition = data.get("edition", "")
        pockets = data.get("pockets", {})
        loop = asyncio.get_event_loop()
        for pocket_key, items_raw in pockets.items():
            bag_items = [BagItem(id=item_id, qty=qty) for item_id, qty in items_raw]
            try:
                await loop.run_in_executor(
                    None, self.pokedex_db.upsert_bag_pocket,
                    owner, edition, pocket_key, bag_items)
            except Exception as err:
                self.logger.warning(f"upsert_bag_pocket failed: {err}")
        self.logger.info(
            f"Remote-Bag empfangen: owner={owner} edition={edition} "
            f"pockets={list(pockets.keys())}"
        )

    @staticmethod
    def _bag_hash(pockets: dict) -> str:
        parts = []
        for key in sorted(pockets.keys()):
            items = pockets[key]
            parts.append(f"{key}:" + ",".join(f"{it.id}:{it.qty}" for it in items))
        return hashlib.md5("|".join(parts).encode()).hexdigest()

    async def _persist_teams(self, teams):
        """Persistiert ein Teams-Snapshot in die PokedexDB.

        `teams` MUSS ein Snapshot sein (`dict(unsorted_teams)` o.ae.), kein
        Live-Dict. BH-Thread schreibt sonst waehrend des await auf
        upsert_team parallel in das iterierte Dict → RuntimeError.
        """
        try:
            self._ensure_pokedex_db()
            if self.pokedex_db is None or self.pokedex_db.connection is None:
                return
            loop = asyncio.get_event_loop()
            # Snapshot der BH-owned Slots fuer Encounter/Nuzlocke-Filter,
            # damit BH-Mutationen waehrend der await-Fenster nicht in
            # inkonsistente Filter-Ergebnisse laufen.
            with self.state_lock:
                local_slots = set(self.bizhawk_teams.keys())
            for player, team_data in teams.items():
                pokemons = team_data[:6]
                edition = team_data[7] if len(team_data) > 7 else self.editions.get(player)
                owner = str(player)
                written, new_pvs = await loop.run_in_executor(
                    None,
                    self.pokedex_db.upsert_team,
                    owner,
                    edition,
                    pokemons,
                )
                if new_pvs:
                    # Nur für lokal betreute Player triggern. unsorted_teams enthält
                    # nach Arceus-Broadcast auch remote Teams; ohne diesen Filter
                    # würde jeder Tracker die Encounter aller anderen Tracker mit
                    # dem eigenen your_name-Prefix persistieren und via
                    # send_encounter_sync zurück broadcasten → n Tracker × n Player
                    # = n² Duplikat-Einträge in der encounters-Tabelle.
                    if player in local_slots:
                        self._on_new_pokemon_detected(player, edition, pokemons, new_pvs)
                    else:
                        self.logger.debug(
                            f"_persist_teams: skip encounter-detect für player={player} "
                            f"(remote, lokale Slots: {sorted(local_slots)})"
                        )
                if self._nuzlocke_rules_active():
                    # Wipe/Death nur für lokal betreute Player evaluieren
                    # (analog Encounter-Filter oben). Ohne diesen Guard würde
                    # jeder Client alle remote-Teams bewerten → n-fache
                    # soullink_rule_violation-Cascade an den Server.
                    if player not in local_slots:
                        continue
                    # Ball-Gate: rule_run_start_on_ball verhindert, dass ein
                    # HP=0 vor Ballerhalt (z.B. Starter-K.O. im Rival-Kampf)
                    # als Death oder Total-Wipe zählt. Async wegen DB-Call
                    # via run_in_executor (siehe Docstring).
                    if not await self._run_active_for_local_player(player, edition):
                        continue
                    # Ein _is_pokemon_dead-Pass pro Tick — Ergebnis geht an
                    # Wipe-Check UND Death-Report (Zähler darf nur 1x hochzählen).
                    dead_infos = self._evaluate_team_deaths(player, pokemons)
                    await self.check_total_wipe(player, pokemons, dead_infos)
                    await self._report_deaths(player, edition, dead_infos)
        except Exception as err:
            self.logger.error(f"_persist_teams failed: {type(err)},{err}")
            self.logger.error(f"{traceback.format_exc()}")

    def _on_new_pokemon_detected(self, player: int, edition, pokemons, new_pvs: list[int]):
        """Callback wenn neue Pokemon in der DB auftauchen. Wird von bizhawk
        überschrieben um Gift-Encounter-Erkennung zu triggern."""
        pass

    def logging_teams(self, teams: dict, dictname: str):
        self.logger.debug(dictname)
        for player, team in teams.items():
            self.logger.debug(f"logging_teams: player {player}: {[str(p) for p in team[:6]]}")
    
    def compute_slot_mapping(self, old_team: list, new_team: list) -> dict:
        old_keys = {}
        new_keys = {}
        for slot in range(min(6, len(old_team))):
            key = old_team[slot].identity_key
            if key is not None:
                old_keys[slot] = key
        for slot in range(min(6, len(new_team))):
            key = new_team[slot].identity_key
            if key is not None:
                new_keys[slot] = key

        new_key_to_slot = {key: slot for slot, key in new_keys.items()}
        old_key_set = set(old_keys.values())

        mapping = {}
        for old_slot, key in old_keys.items():
            mapping[old_slot] = new_key_to_slot.get(key)

        new_slots = [slot for slot, key in new_keys.items() if key not in old_key_set]
        removed_slots = [old_slot for old_slot, key in old_keys.items() if key not in new_keys.values()]

        mapping['new_slots'] = new_slots
        mapping['removed_slots'] = removed_slots
        return mapping

    def sort(self, liste, key):
        # Original-Slot als Tiebreaker mitfuehren, sonst kippt die Reihenfolge
        # bei gleichen Sort-Keys (z.B. sort=route mit flackernder met_location)
        # zwischen Ticks — im Overlay sichtbar als vertauschte Slots.
        key = key.lower().replace('.', '')
        if key == 'team':
            return list(liste)
        indexed = list(enumerate(liste))
        if key == 'dexnr':
            indexed.sort(key=lambda ip: (
                999999 if ip[1].dexnr == 0 else (0 if ip[1].dexnr == 'egg' else 1),
                0 if ip[1].dexnr in (0, 'egg') else ip[1].dexnr,
                ip[0],
            ))
        elif key == 'lvl':
            indexed.sort(key=lambda ip: (
                999999 if ip[1].dexnr == 0 else -ip[1].lvl,
                ip[0],
            ))
        elif key == 'route':
            indexed.sort(key=lambda ip: (
                999999 if ip[1].dexnr == 0 else ip[1].route,
                ip[0],
            ))
        else:
            return list(liste)
        return [p for _, p in indexed]

    def change_order(self, *args):
        # unsorted_teams wird vom BH-Thread befuellt. Snapshot unter Lock,
        # dann iterieren — direkter Zugriff koennte sonst in KeyError oder
        # dict-size-change-RuntimeError laufen, wenn BH einen neuen Slot
        # waehrend des Loops einfuegt. sorted_teams-Writes ebenfalls unter
        # Lock, damit Kivy-Snapshots (snapshot_sorted_teams) nicht gegen
        # eine Munchlax-Loop-parallele Mutation iterieren.
        with self.state_lock:
            unsorted_snapshot = dict(self.unsorted_teams)
            team_keys = list(self.sorted_teams.keys())
        sorted_updates = {}
        for team in team_keys:
            team_data = unsorted_snapshot.get(team)
            if team_data is None:
                continue
            sorted_updates[team] = self.sort(team_data[:6], self.sp['order'])
        if sorted_updates:
            with self.state_lock:
                for team, sorted_team in sorted_updates.items():
                    self.sorted_teams[team] = sorted_team

    async def send_heartbeat(self):
        # Session-Snapshot (R6 WARN): verspaeteter Error-Disconnect dieses
        # Tasks darf keinen Socket einer spaeteren Session schliessen.
        my_session = self._session_id
        while True:
            try:
                self.logger.debug("Heartbeat gesendet")
                async with self.writer_lock:
                    await self.send_message('heartbeat')
                await asyncio.sleep(5)
            except TRANSIENT_NET_EXCEPTIONS as err:
                self._log_dedup(
                    f"heartbeat_{type(err).__name__}",
                    f"send_heartbeat Netz-Fehler: {type(err).__name__}: {err}",
                    exc=err,
                )
                self._record_disconnect(f"heartbeat_{type(err).__name__}", expected_session=my_session)
                break
            except Exception as err:
                self.logger.warning(f"Heartbeat failed: {type(err)},{err}")
                self.logger.error(f"{traceback.format_exc()}")
                self._record_disconnect(f"heartbeat_unexpected_{type(err).__name__}", expected_session=my_session)
                break

        await self.disconnect(intentional=False, expected_session=my_session)

    async def send_teams(self):
        # Session-Snapshot (R6 WARN): verspaeteter Error-Disconnect dieses
        # Tasks darf keinen Socket einer spaeteren Session schliessen.
        my_session = self._session_id
        while True:
            # bizhawk_teams wird vom BH-Thread mutiert; Snapshot unter Lock,
            # dann sowohl Empty-Check als auch Pickle-Dump auf dem Snapshot
            # fahren. Ohne Snapshot wuerde `send_message` (pickle.dumps)
            # das Live-Dict iterieren und bei paralleler BH-Mutation in
            # RuntimeError laufen; vorheriger Empty-Check waere ebenfalls
            # TOCTOU-anfaellig.
            with self.state_lock:
                bizhawk_snapshot = dict(self.bizhawk_teams)
                # sorted_teams-Rebind MUSS unter dem gleichen Lock stehen
                # (Rebind-unter-Lock-Regel). Ohne Lock koennte clear_everything
                # (Kivy) oder alter_teams (Munchlax) den Zustand parallel
                # neu setzen → check-then-rebind-Race ueberschreibt den Clear.
                needs_initial = not self.sorted_teams
                if needs_initial:
                    self.sorted_teams = dict(bizhawk_snapshot)
            if needs_initial:
                # change_order nimmt den Lock selbst — ausserhalb des with-
                # Blocks aufrufen, damit threading.Lock (non-reentrant)
                # nicht in Rekursion laeuft.
                self.change_order()
            if bizhawk_snapshot:
                try:
                    async with self.writer_lock:
                        await self.send_message(bizhawk_snapshot)
                except TRANSIENT_NET_EXCEPTIONS as err:
                    self._log_dedup(
                        f"send_teams_{type(err).__name__}",
                        f"send_teams Netz-Fehler: {type(err).__name__}: {err}",
                        exc=err,
                    )
                    self._record_disconnect(f"send_teams_{type(err).__name__}", expected_session=my_session)
                    break
                except Exception as err:
                    self.logger.warning(f"Teams senden failed: {type(err)},{err}")
                    self.logger.error(f"{traceback.format_exc()}")
                    self._record_disconnect(f"send_teams_unexpected_{type(err).__name__}", expected_session=my_session)
                    break
            await asyncio.sleep(1)

        await self.disconnect(intentional=False, expected_session=my_session)
    
    async def connect(self):
        # Locks lazy anlegen (Phase-3-Isolation: Munchlax-Loop !=
        # Kivy-Loop). Zentrale Hilfe, auch von disconnect() genutzt.
        self._ensure_async_locks()
        # Generation VOR Lock-Erwerb snapshotten (R12 WARN): User-
        # Disconnect, der waehrend des Lock-Wartens eintrifft,
        # inkrementiert _connect_generation. Ohne diesen Pre-Snapshot
        # wuerde connect() nach Lock-Erwerb das Pending-Flag resetten
        # und mit der neuen (bereits erhoehten) Generation
        # weiterverbinden — trotz Trennen-Klick.
        initial_gen = self._connect_generation
        # _connect_lock serialisiert ganze connect()-Aufrufe (R6 KRIT).
        # Zweiter connect (User-Klick oder _auto_reconnect) wartet, bis
        # der erste komplett durch ist. Verhindert Writer-Clobber,
        # stale-Handshake auf frischem Writer und doppelte Task-Starts.
        async with self._connect_lock:
            # Deduplizierung (R7 KRIT): zweiter connect-Call sieht nach
            # Lock-Erwerb, dass der erste bereits erfolgreich verbunden
            # hat. Ohne Guard wuerden self.reader/writer ueberschrieben,
            # alte Tasks laufen als Zombie weiter, Server sieht doppelte
            # client_id. Check unter Lock + atomar bis _connect_impl →
            # kein TOCTOU.
            if self.is_connected:
                self.logger.info(
                    "connect() uebersprungen — bereits verbunden "
                    f"(session={self._session_id})"
                )
                return
            # Pre-Snapshot-Check (R12 WARN): User hat waehrend
            # Lock-Wartens disconnect ausgeloest → Generation gebumpt.
            # Nur gen vergleichen (R13 KRIT): `pending` auch zu lesen
            # wuerde einen User-Connect NACH Trennen dauerhaft blocken,
            # weil disconnect(intentional=True) `_user_disconnect_pending=
            # True` setzt und erst der RESET innerhalb connect() es
            # loescht — der steht aber hinter diesem Check. Jeder
            # intentional-Disconnect bumpt synchron auch die Generation
            # (Z. 2993 im disconnect-Pfad), der Gen-Vergleich deckt den
            # Lock-Warte-Fall also allein ab.
            if self._connect_generation != initial_gen:
                self.logger.info(
                    "connect() abgebrochen — User-Disconnect waehrend "
                    f"Lock-Wartens (gen {initial_gen} -> "
                    f"{self._connect_generation})."
                )
                return
            # Pending-Flags zuruecksetzen (R11): ein frischer connect-
            # Call reset't den User-Disconnect-Marker, damit anschliessende
            # _auto_reconnects nicht noch noop'en. _intentional_session
            # analog, damit Loop-Error-Handler der neuen Session ihre
            # Gruende wieder schreiben koennen.
            self._user_disconnect_pending = False
            self._intentional_session = None
            await self._connect_impl()

    async def _connect_impl(self):
        # Host/Port frisch aus rem-Dict lesen. Nach Session-Wechsel wird rem
        # in-place aktualisiert, aber self.host/self.port tragen noch die beim
        # __init__ kopierten Startwerte — Verbindung liefe sonst auf die
        # alten Werte der vorherigen Session. Analog Bizhawk/Arceus.start.
        if self.rem.get('start_server'):
            self.host = '127.0.0.1'
            self.port = self.rem.get('client_port', self.port)
        else:
            self.host = self.rem.get('server_ip_adresse', self.host)
            self.port = self.rem.get('server_port', self.port)
        # Neue Session-ID — lokal halten (R7 WARN): Wenn der Connect
        # scheitert (ConnectionRefused, Gen-Cancel), bleibt die bisherige
        # Session-Id auf self (noch laufende Tasks der alten Session
        # koennen weiter sauber per expected_session identifiziert
        # werden). Erst nach Handshake-Erfolg wird die neue ID
        # committed (siehe unten).
        new_session_id = uuid.uuid4().hex[:8]
        self._session_started_at = time.time()
        self._msg_counters = {"recv": 0, "sent": 0}
        self._last_recv_at = 0.0
        self._last_recv_type = ""
        self.logger.info(
            f"Verbinde Munchlax zu ({self.host}, {self.port}) "
            f"session={new_session_id} "
            f"vorheriger_disconnect_grund={self._last_disconnect_reason!r}"
        )
        # Generation-Snapshot (R4 WARN): disconnect() inkrementiert
        # `_connect_generation`. Dieser connect-Durchlauf prueft den
        # Snapshot gegen den aktuellen Wert — jeder User-Disconnect
        # bricht genau diese connect-Instanz ab, auch bei schnellem
        # disconnect→connect→(alte Retry wacht auf).
        gen = self._connect_generation
        # Startup-Race Mitigation (Phase 3): wenn der Server lokal in
        # diesem Prozess hochgefahren wird (`rem['start_server']=True`),
        # ArceusLoop (eigener Thread) und MunchlaxLoop starten parallel.
        # `connect` kann den Port treffen bevor `arceus.start()` an
        # start_server zurueck ist → ConnectionRefusedError. Kurzer Retry
        # mit 100ms-Backoff, max ~1s. Fuer nicht-lokale Server (externer
        # Arceus) direkt ohne Retry — dort ist ConnectionRefused ein
        # echter User-Fehler.
        retries = 10 if self.rem.get('start_server') else 1
        last_err = None
        for attempt in range(retries):
            if self._connect_generation != gen:
                # User hat disconnect() waehrend Retry-Backoff gedrueckt
                # (oder einen frischen connect gestartet). Mit
                # CancelledError raus — alter connect-Task endet sauber,
                # kein doppelter Socket.
                raise asyncio.CancelledError(
                    "connect() durch neuere Generation abgebrochen"
                )
            try:
                # Lokale Variablen, damit ein verzoegerter alter Connect
                # einen frischen `self.writer` eines parallelen Connects
                # nicht ueberschreibt (R5 KRIT: Writer-Clobber).
                local_reader, local_writer = await asyncio.open_connection(
                    self.host, self.port
                )
                break
            except ConnectionRefusedError as err:
                last_err = err
                if attempt + 1 >= retries:
                    raise
                await asyncio.sleep(0.1)
        else:
            if last_err is not None:
                raise last_err
        # Nach erfolgreichem open_connection erneut die Generation
        # pruefen: zwischen Backoff-Attempt und open_connection-Erfolg
        # kann der User ebenfalls disconnect/reconnect gedrueckt haben.
        # Lokalen writer schliessen (nicht self.writer, der koennte vom
        # frischen Connect schon gesetzt sein), dann CancelledError.
        if self._connect_generation != gen:
            try:
                local_writer.close()
            except Exception:
                pass
            raise asyncio.CancelledError(
                "connect() nach open_connection durch neuere Generation abgebrochen"
            )
        self.reader, self.writer = local_reader, local_writer
        self.logger.info(
            f"Munchlax {self.client_id} bei Arceus({self.host},{self.port}) "
            f"registriert (session={new_session_id})"
        )
        self.logger.debug(f"Client-ID: {self.client_id}, Start-Server: {self.rem.get('start_server')}")
        # Dedup-Fenster schliessen (Summary der Disconnect-Fehler) + Reconnect-
        # Event im Ringpuffer vermerken.
        self._flush_dedup()
        self._reconnect_log.append({
            "ts": time.time(),
            "event": "connect",
            "session_id": new_session_id,
        })

        # Phase C: Handshake-Phase. Capability-Flag + Resume-Pending werden
        # ERST nach erfolgreichem Handshake gesetzt — wirft der Handshake,
        # muessen wir den Socket schliessen und nicht in der Resume-Phase
        # haengen bleiben (sync-reviewer R2 WARNUNG: Socket-Leak).
        #
        # try/finally mit Success-Flag faengt auch CancelledError (BaseException)
        # ab, damit ein Cancel mitten im Handshake den Writer sauber schliesst.
        # Nur Exception zu fangen wuerde den Writer bei User-Disconnect leaken.
        name = self.pl.get('your_name', '')
        handshake_ok = False
        try:
            async with self.writer_lock:
                # Format name_cid_bootepoch. Alte Clients ohne Phase C
                # senden nur name_cid, Server fallt per rsplit-count darauf
                # zurueck (kein Resume dann). Boot-Epoch erlaubt dem Server,
                # bei Client-App-Neustart (gleiche client_id, _next_seq=1)
                # den last_seen_seq zu resetten.
                await self.send_message(f"{name}_{self.client_id}_{self._boot_epoch}")
                # Netz-Slots aus pl deklarieren — funktioniert ohne BizHawk/
                # Citra/Azahar. Server nutzt sie fuer client_player_ids/
                # player_names und damit fuer die Sortierung im NuzlockeMenu.
                # Bei Kollision antwortet der Server mit "slot_collision"
                # (alter_teams-Handler), was zu einem Popup + Disconnect fuehrt.
                owned = self._owned_player_ids()
                await self.send_message({"type": "declared_player_ids", "player_ids": owned})
                self.logger.info(f"declared_player_ids gesendet: {owned}")
            handshake_ok = True
        finally:
            if not handshake_ok:
                self.logger.warning(
                    f"Handshake abgebrochen oder fehlgeschlagen — "
                    f"schliesse Socket"
                )
                # local_writer-Close (R6 KRIT): kein self.writer-Close,
                # sonst wuerde ein paralleler frischer connect dessen
                # Writer verlieren. _connect_lock verhindert das zwar
                # bereits, aber defensive Konsistenz mit F1-Pattern.
                try:
                    local_writer.close()
                except Exception:
                    pass

        # Nach Handshake erneut die Generation pruefen (R5 WARN):
        # User-Disconnect waehrend des Handshake-Fensters sieht
        # `is_connected=False` → `disconnect()` skippt Close-Pfad; ohne
        # diese Pruefung wuerde connect() danach `is_connected='connected'`
        # setzen und Tasks starten, obwohl der User getrennt hat.
        if self._connect_generation != gen:
            self.logger.info(
                "connect(): Generation-Mismatch nach Handshake — Socket "
                "schliessen und abbrechen (User-Disconnect waehrend "
                "Handshake)."
            )
            try:
                local_writer.close()
            except Exception:
                pass
            raise asyncio.CancelledError(
                "connect() nach Handshake durch neuere Generation abgebrochen"
            )

        # Handshake durch — Resume-Phase ab jetzt. Capability-Flag auf None
        # (unknown), wird durch resume_ack auf True oder durch Watchdog-Timeout
        # auf False gesetzt.
        self._server_supports_resume = None
        self._resume_pending = True
        self._resend_running = False
        if self._resume_timeout_task is not None and not self._resume_timeout_task.done():
            self._resume_timeout_task.cancel()
        self._resume_timeout_task = asyncio.create_task(self._resume_timeout_watchdog())

        # Session-ID jetzt committen (R7 WARN): neue Loops lesen
        # self._session_id gleich im `my_session`-Snapshot. Alte Tasks
        # haben bereits ihre alte Session-ID gespeichert.
        self._session_id = new_session_id
        self.is_connected = 'connected'

        self.heartbeat_task = asyncio.create_task(self.send_heartbeat())
        self.send_teams_task = asyncio.create_task(self.send_teams())
        self.alter_teams_task = asyncio.create_task(self.alter_teams())
        # Rando-Moves-Flush: wird NICHT hier getriggert, sondern erst nach
        # resume_ack (siehe _server_supports_resume=True branch). Grund:
        # waehrend _server_supports_resume=None ist, kennen wir die
        # Capability des Servers noch nicht; ein voreiliges Senden an
        # einen Legacy-Server wuerde dort als Teams-Dict fehlinterpretiert
        # (R3 WARNUNG sync).

    def _owned_player_ids(self) -> list[int]:
        """Lokale Netz-Slots dieses Clients aus pl: 1..player_count, exkl. remote_i=True.

        Gleiche Regel wie in connection_controller._local_player_slots und
        citrahandler.set_player_number, damit die Deklaration an den Server
        mit den lokal gestarteten Emulator-Instanzen konsistent ist.
        """
        try:
            count = int(self.pl.get("player_count", 1) or 1)
        except (TypeError, ValueError):
            count = 1
        slots: list[int] = []
        for slot in range(1, count + 1):
            if self.pl.get(f"remote_{slot}", False):
                continue
            slots.append(slot)
        return slots

    def _ensure_async_locks(self):
        """Legt writer_lock + disconnect_lock + connect_lock an, falls
        connect() noch nie lief. In Py3.11.5 bindet `asyncio.Lock` den
        Loop beim ersten `__aenter__`, nicht beim `__init__` — Lazy-Create
        reicht; der Bind erfolgt in connect/disconnect auf dem Munchlax-
        Loop. Alle Lock-Pfade (disconnect, send_message-Wrapper, etc.)
        rufen _ensure_async_locks()-defensive, damit kein Pfad vor
        connect() auf `async with None` crasht.
        """
        if self.writer_lock is None:
            self.writer_lock = asyncio.Lock()
        if self.disconnect_lock is None:
            self.disconnect_lock = asyncio.Lock()
        if self._connect_lock is None:
            self._connect_lock = asyncio.Lock()

    async def disconnect(self, intentional=True, reason: str | None = None,
                         expected_session: str | None = None):
        """Trennt Munchlax-Verbindung.

        `expected_session` (R6 WARN): Fuer Non-intentional-Disconnects
        aus Loop-Error-Pfaden. Caller snapshotet `self._session_id` beim
        Task-Start, passed ihn hier rein. disconnect noop'ed, falls die
        aktuelle Session nicht mehr der erwarteten entspricht — alter
        Error-Handler-Call wuerde sonst einen frischen Socket schliessen.
        """
        self._ensure_async_locks()
        # Intentional-Disconnect als allererstes markieren (R10 WARN),
        # VOR Lock + send_message. Parallele Loop-Error-Handler mit
        # derselben Session werden damit in `_record_disconnect`
        # verworfen und ueberschreiben `_last_disconnect_reason` nicht.
        if intentional and self._session_id:
            self._intentional_session = self._session_id
        # User-Disconnect-Pending Flag (R11 KRIT): auch dann setzen, wenn
        # unser Lock-Erwerb hinter einem Loop-Disconnect wartet. Dieser
        # koennte in seinem Teardown nachgelagert einen _auto_reconnect
        # spawnen (siehe should_reconnect-Pfad weiter unten) — das Flag
        # laesst den Reconnect bei seinem Entry noop'en, so dass die
        # Verbindung nach User-Klick auch wirklich getrennt bleibt.
        if intentional:
            self._user_disconnect_pending = True
        # Session-Identity-Check: alte Non-intentional-Disconnect-Calls
        # (z.B. verspaetet aus disconnect_lock-Queue) sollen keinen
        # frischen, neu aufgebauten Socket zerreissen.
        if (expected_session is not None
                and expected_session != self._session_id):
            self.logger.info(
                f"disconnect(expected_session={expected_session}) "
                f"uebersprungen — aktuelle Session {self._session_id} "
                f"gehoert einem frischeren Connect."
            )
            return
        # Signal an einen laufenden connect()-Retry-Loop (R4 WARN):
        # Generation-Counter inkrementieren → in-flight connect sieht
        # mismatch im naechsten Retry-Check und bricht mit CancelledError
        # ab. NUR bei intentional=True bumpen (R5 HINWEIS): ein spaet
        # eintreffender auto-Disconnect (Receive-Loop-Fehler eines alten
        # Sockets) soll einen frisch laufenden _auto_reconnect nicht
        # abschiessen.
        if intentional:
            self._connect_generation += 1
        should_reconnect = False
        # Fix sync-reviewer R2 Runde 2 WARNUNG: Reconnect-Cancel MUSS ausserhalb
        # des `if self.is_connected`-Blocks laufen. Waehrend `_auto_reconnect`
        # Backoff-Sleept, ist is_connected=False — ein User-Disconnect in
        # dieser Phase wuerde den Cancel sonst ueberspringen und der Reconnect-
        # Loop verbindet nach dem Sleep trotzdem wieder. Jetzt vor dem Lock,
        # damit auch ein zweiter paralleler disconnect den Reconnect kappt.
        if intentional and self._reconnect_task is not None and not self._reconnect_task.done():
            self.logger.info(
                "disconnect(intentional=True) canceled laufenden "
                "_auto_reconnect"
            )
            self._reconnect_task.cancel()
        async with self.disconnect_lock:
            # Session-Identity-Recheck (R7 KRIT): zwischen dem Fast-Path-
            # Check oben und dem Lock-Erwerb kann ein paralleler
            # intentional-Disconnect + Reconnect eine neue Session
            # aufgebaut haben. Ohne diese zweite Pruefung wuerde ein
            # verspaeteter Error-Handler den frischen Socket abreissen.
            if (expected_session is not None
                    and expected_session != self._session_id):
                self.logger.info(
                    f"disconnect(expected_session={expected_session}) "
                    f"unter Lock verworfen — aktuelle Session "
                    f"{self._session_id} gehoert einem frischeren Connect."
                )
                return
            # Side-Effects NACH dem Recheck (R8 WARN): initialized/reason
            # sind globale Flags, die ein verworfener stale-Caller sonst
            # auf der frischen Session veraendern wuerde.
            self.initialized = False
            # Reason-Parameter ueberschreibt den vom Loop-Caller via
            # _record_disconnect gesetzten Wert. Default: Bei intentional=True
            # setzen wir "intentional", bei intentional=False bleibt der vom
            # Loop-Caller gesetzte (recv_IncompleteReadError o.ae.) stehen.
            if reason is not None:
                self._last_disconnect_reason = reason
            elif intentional:
                self._last_disconnect_reason = "intentional"
            if self.is_connected:
                if intentional:
                    try:
                        async with self.writer_lock:
                            await self.send_message(f"disconnect {self.client_id}")
                    except Exception as err:
                        self.logger.warning(f"Disconnect Nachricht versenden failed: {err}")
                else:
                    should_reconnect = True

                self.is_connected = False
                with self.state_lock:
                    self.remote_connection_status.clear()
                    self.remote_connection_names.clear()

                # Fix sync-reviewer-Hinweis: Self-Cancel darf nicht den
                # aufrufenden Task selbst canceln, sonst wird der Rest
                # dieses disconnect-Blocks (Writer-Close, Reconnect-Trigger)
                # per CancelledError uebersprungen.
                current = asyncio.current_task()
                for t in (
                    self.alter_teams_task,
                    self.heartbeat_task,
                    self.send_teams_task,
                    self._rando_flush_task,
                ):
                    if t is not None and t is not current and not t.done():
                        t.cancel()
                # Phase C: Resume-Watchdog ebenfalls canceln. _resume_pending
                # bleibt sinnvollerweise aktuell wie es ist — wird im naechsten
                # connect() auf True gesetzt.
                if self._resume_timeout_task is not None and not self._resume_timeout_task.done():
                    self._resume_timeout_task.cancel()

                # Writer lokal binden — connect() erwirbt disconnect_lock
                # NICHT und ueberschreibt self.writer beim naechsten
                # await asyncio.open_connection(). Wuerden wir self.writer
                # unten benutzen, koennte ein paralleler Reconnect (kein
                # is_connected-Guard: wir haben's oben gerade False gesetzt)
                # zwischen close() und wait_closed() das Attribut umbiegen
                # und wir wuerden den falschen (frischen) Socket abwarten
                # statt den alten (sync-reviewer R4).
                old_writer = self.writer
                try:
                    old_writer.close()
                except Exception as err:
                    self.logger.warning(
                        f"writer.close() für {self.client_id} failed: "
                        f"{type(err).__name__},{err}"
                    )
                # Beidseitigkeits-Fix zum arceus.disconnect_client-Timeout:
                # wait_closed haengt unter Windows bei Netzwerkabbruch (WinError
                # 121) minutenlang, blockiert den disconnect_lock und
                # verzoegert damit _auto_reconnect. 2 s Timeout genuegt fuer
                # sauberes FIN-Handshake.
                try:
                    await asyncio.wait_for(old_writer.wait_closed(), timeout=2.0)
                except asyncio.TimeoutError:
                    self.logger.warning(
                        f"wait_closed Timeout für {self.client_id} — "
                        f"Socket wird aufgegeben, Reconnect faehrt trotzdem an"
                    )
                except Exception as err:
                    self.logger.warning(
                        f"wait_closed für {self.client_id} failed: "
                        f"{type(err).__name__},{err}"
                    )
                now = time.time()
                dur = now - self._session_started_at if self._session_started_at else 0.0
                if self._last_recv_at:
                    last_msg_info = (
                        f"letzte_msg={self._last_recv_type!r} "
                        f"vor_{now - self._last_recv_at:.1f}s"
                    )
                else:
                    last_msg_info = "keine_msg_empfangen"
                self.logger.info(
                    f"Client {self.client_id} hat sich disconnectet "
                    f"(session={self._session_id}, "
                    f"grund={self._last_disconnect_reason!r}, "
                    f"intentional={intentional}, dauer={dur:.1f}s, "
                    f"recv={self._msg_counters.get('recv',0)}, "
                    f"sent={self._msg_counters.get('sent',0)}, "
                    f"{last_msg_info})"
                )

                # DB-Close nur, wenn die Session seit Lock-Entry nicht
                # ersetzt wurde (R8 WARN): wait_closed() haelt
                # disconnect_lock bis zu 2s. connect() nimmt den Lock
                # nicht und kann in dem Fenster eine frische Session mit
                # neuer DB-Verbindung aufbauen. Close wuerde dann die
                # frische DB zerreissen.
                if (expected_session is None
                        or expected_session == self._session_id):
                    self.close_pokedex_db()
                else:
                    self.logger.info(
                        f"close_pokedex_db uebersprungen — Session "
                        f"{self._session_id} unterscheidet sich von "
                        f"erwarteter {expected_session}."
                    )

                self.host = '127.0.0.1' if self.rem["start_server"] else self.rem["server_ip_adresse"]
                self.port = self.rem["client_port"] if self.rem["start_server"] else self.rem["server_port"]

        if should_reconnect:
            # Strong-Ref gegen GC — siehe memory feedback-async-task-strong-ref.
            # Auto-Cleanup-Callback nullt die Ref — mit Identity-Check, damit
            # bei schnellem Folge-Trigger (ein zweiter _auto_reconnect-Task,
            # der durch Dedup frueh zurueckkehrt) nicht die frische Ref auf
            # None zurueckgesetzt wird.
            new_task = asyncio.create_task(self._auto_reconnect())
            self._reconnect_task = new_task
            new_task.add_done_callback(
                lambda t: setattr(self, "_reconnect_task", None)
                if self._reconnect_task is t else None
            )

    async def _auto_reconnect(self):
        # User-Disconnect-Guard (R11 KRIT): wenn der User "Trennen"
        # geklickt hat waehrend ein Loop-Disconnect den disconnect_lock
        # hielt, kann dieser Loop-Disconnect den Reconnect bereits
        # gespawnt haben, bevor der User-Call zum Lock kam. Das Flag
        # sorgt dafuer, dass dieser Reconnect sofort noop'ed.
        if self._user_disconnect_pending:
            self.logger.info(
                "_auto_reconnect: User-Disconnect pending — Trigger "
                f"verworfen (letzter_grund={self._last_disconnect_reason!r})."
            )
            return
        # Dedup gegen parallelen Reconnect: wenn disconnect(intentional=False)
        # mehrfach triggert (z.B. alter_teams + heartbeat + send_teams brechen
        # fast gleichzeitig weg), wuerden sonst mehrere Reconnect-Tasks laufen
        # und sich beim connect() den Writer unter den Fuessen wegziehen.
        if self.reconnecting:
            self.logger.debug(
                "_auto_reconnect: schon aktiv — zweiter Trigger ignoriert "
                f"(letzter_grund={self._last_disconnect_reason!r})"
            )
            return
        self.reconnecting = True
        last_reasons = [
            e for e in list(self._reconnect_log)[-5:]
            if e.get("event") == "disconnect"
        ]
        self.logger.info(
            f"Auto-Reconnect gestartet (letzter_grund="
            f"{self._last_disconnect_reason!r}, "
            f"letzte_5_disconnects={[r.get('reason') for r in last_reasons]})"
        )
        attempt = 0
        try:
            while True:
                attempt += 1
                # Exponential-Table bis Index-Cap, dann konstant RECONNECT_MAX_DELAY.
                if attempt - 1 < len(RECONNECT_DELAYS):
                    base = RECONNECT_DELAYS[attempt - 1]
                else:
                    base = RECONNECT_MAX_DELAY
                delay = base + random.uniform(0, RECONNECT_JITTER_MAX)
                self.logger.info(
                    f"Auto-Reconnect Versuch {attempt} in {delay:.1f}s..."
                )
                # Hinweis-Log bei langen Hangs — hilft dem User zu erkennen,
                # dass der Server dauerhaft weg ist.
                if attempt == 10:
                    self.logger.warning(
                        "Auto-Reconnect: Server seit 10 Versuchen nicht "
                        "erreichbar — pruefe Verbindung/Konfiguration."
                    )
                await asyncio.sleep(delay)
                if self.is_connected:
                    self.logger.info(
                        f"Auto-Reconnect Versuch {attempt} abgebrochen — "
                        f"schon verbunden (Fremd-Reconnect?)"
                    )
                    return
                try:
                    await self.connect()
                    self.logger.info(
                        f"Auto-Reconnect erfolgreich nach Versuch {attempt} "
                        f"(session={self._session_id})"
                    )
                    return
                except asyncio.CancelledError:
                    # Explizit propagieren — darf nicht als "normaler" Fehler
                    # geloggt werden. User-Disconnect oder Shutdown.
                    raise
                except Exception as err:
                    self.logger.warning(
                        f"Auto-Reconnect Versuch {attempt} fehlgeschlagen: "
                        f"{type(err).__name__}: {err}"
                    )
        except asyncio.CancelledError:
            self.logger.info(
                f"Auto-Reconnect abgebrochen nach {attempt} Versuchen "
                f"(User-Disconnect oder Shutdown)"
            )
            raise
        finally:
            self.reconnecting = False

    def _is_bufferable(self, message) -> bool:
        """Entscheidet, ob eine Message in den Replay-Buffer gehoert.

        Nur Dict-Messages mit "type" sind kandidaten. Alles was verbindungs-
        oder zustandsfrei ist (Heartbeat, Handshake-Strings, Resume-Query)
        wird nicht gebuffert — Resend macht da keinen Sinn.
        """
        if not isinstance(message, dict):
            return False
        msg_type = message.get("type")
        if not msg_type:
            # Dicts ohne "type" sind die Legacy-Teams-Dicts (player_id -> list).
            # Die werden ohnehin jede Sekunde neu gesendet (send_teams-Loop)
            # und ihre Payloads sind fluechtig — Replay bringt nichts, der
            # naechste Tick ueberschreibt sowieso.
            return False
        return msg_type not in UNBUFFERED_DICT_TYPES

    async def send_message(self, message):
        # Phase C: ggf. mit seq wrappen und in Buffer legen. seq steigt
        # monoton ueber Reconnect-Grenzen hinweg, damit der Server beim
        # Resume die Luecke dedupen kann.
        #
        # Capability-Fallback: wenn _server_supports_resume=False (Watchdog
        # hat alte-Server-Diagnose gestellt), senden wir unwrapped und
        # umgehen die Buffer-Logik. Buffer wurde beim Watchdog bereits
        # geleert — kein weiteres Buffering bringt noch Nutzen.
        if self._is_bufferable(message) and self._server_supports_resume is not False:
            seq = self._next_seq
            self._next_seq += 1
            self._outbound_buffer.append((seq, message))
            # Resume-Pending: Payload nur buffern, Wire-Send schluckt
            # _resend_buffered beim resume_ack-Empfang. Verhindert Race-Bug
            # "Live-send mit seq=N+1 vor Replay mit seq=X — Server dedupt X".
            if self._resume_pending:
                self.logger.debug(
                    f"send_message gebuffert (resume_pending): "
                    f"seq={seq}, type={message.get('type', '')}"
                )
                return
            wrapped = {"__seq__": seq, "__payload__": message}
            serialized_message = pickle.dumps(wrapped)
            msg_type_log = message.get("type", "")
            msg_type_wire = f"seq={seq},type={msg_type_log}"
        else:
            serialized_message = pickle.dumps(message)
            msg_type_wire = (
                message.get("type", "teams") if isinstance(message, dict)
                else (message if isinstance(message, str) else type(message).__name__)
            )
        CHUNK_SIZE = 500  # Die Größe jedes Chunks in Bytes

        self.logger.debug(f"Sende: type={msg_type_wire}, {len(serialized_message)} Bytes")

        # Gesamtlänge der Nachricht senden
        length = len(serialized_message).to_bytes(4, 'big')
        self.writer.write(length)
        await self.writer.drain()

        # Nachricht in Chunks senden
        for i in range(0, len(serialized_message), CHUNK_SIZE):
            chunk = serialized_message[i:i+CHUNK_SIZE]
            # Größe des aktuellen Chunks senden
            chunk_length = len(chunk).to_bytes(4, 'big')
            self.writer.write(chunk_length)
            await self.writer.drain()
            # Chunk senden
            self.writer.write(chunk)
            await self.writer.drain()
        self._msg_counters["sent"] = self._msg_counters.get("sent", 0) + 1

    def _on_resend_done(self, task: asyncio.Task) -> None:
        """Done-Callback fuer Shield-wrapped _resend_buffered-Tasks. Wird
        der Outer-Caller gecancelt, laeuft der Inner-Task weiter — wirft
        er, taucht das sonst nur als "Task exception was never retrieved"
        in stderr auf, ohne unser Logger-Format und ohne Session-Kontext
        (reviewer R3 H6). Hier abfangen und sauber loggen."""
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            self.logger.error(
                f"Shield-Resend-Task kippte: {type(exc).__name__}: {exc}"
            )

    async def _resume_timeout_watchdog(self):
        """Fallback fuer den Fall, dass Server-resume_ack niemals ankommt
        (alter Server ohne Phase C, oder Server-Push verloren).

        Buffer wird NICHT verworfen (sync-reviewer Runde 2 W4): das waere
        stiller Datenverlust von bis zu 5 s Encounter-/Death-/Config-
        Payloads bei jedem Reconnect gegen einen alten Server. Stattdessen
        alle gepufferten Items unwrapped senden — kompatibel mit beiden
        Server-Varianten, Phase-C-Server erkennt sie als regulaere
        Nachrichten ohne seq-Semantik.

        Flag-Flip + Snapshot + Buffer-Clear laufen alle UNTER writer_lock
        (sync-reviewer Runde 3 W2 + H4): kein Fenster zwischen
        Capability-False und Live-Send auf Resend-Reihenfolge. Buffer wird
        erst NACH erfolgreichem Send geleert, damit ein Resend-Fehler nicht
        die Items verliert.
        """
        try:
            await asyncio.sleep(5.0)
        except asyncio.CancelledError:
            return
        if not self._resume_pending:
            return
        self._resend_running = True
        writer = self.writer
        try:
            async with self.writer_lock:
                # Atomar unter Lock: Flag-Flip, Snapshot und Clear-Marker.
                # Wird _resend_running vorher gesetzt, kann der resume_ack-
                # Handler nicht dazwischenrutschen und Capability wieder
                # auf True kippen.
                buffer_snapshot = list(self._outbound_buffer)
                self._server_supports_resume = False
                self._resume_pending = False
                self.logger.warning(
                    f"resume_ack-Timeout (5s) — Server antwortet nicht, "
                    f"nehme an Phase C wird nicht unterstuetzt. Sende "
                    f"{len(buffer_snapshot)} gepufferte Items unwrapped. "
                    f"Weitere Payloads werden ebenfalls unwrapped gesendet."
                )
                if not buffer_snapshot:
                    return
                CHUNK_SIZE = 500
                sent = 0
                try:
                    for seq, payload in buffer_snapshot:
                        if self.writer is not writer:
                            self.logger.warning(
                                f"Watchdog-Resend: writer identity changed bei "
                                f"{sent}/{len(buffer_snapshot)} — Abbruch"
                            )
                            break
                        serialized = pickle.dumps(payload)
                        length = len(serialized).to_bytes(4, 'big')
                        writer.write(length)
                        await writer.drain()
                        for i in range(0, len(serialized), CHUNK_SIZE):
                            chunk = serialized[i:i+CHUNK_SIZE]
                            chunk_length = len(chunk).to_bytes(4, 'big')
                            writer.write(chunk_length)
                            await writer.drain()
                            writer.write(chunk)
                            await writer.drain()
                        self._msg_counters["sent"] = self._msg_counters.get("sent", 0) + 1
                        sent += 1
                    self.logger.info(
                        f"Watchdog-Resend: {sent}/{len(buffer_snapshot)} Items "
                        f"unwrapped gesendet"
                    )
                finally:
                    # Partial-Clear fuer tatsaechlich gesendete Items — auch
                    # bei Abort oder Exception mitten im Send. Verhindert
                    # Duplikate beim naechsten Reconnect gegen einen alten
                    # Server (reviewer R4 H1). Phase-C-Server dedupt via
                    # seq ohnehin. Rest-Items bleiben im Buffer, naechster
                    # resume_ack/Watchdog nimmt sie wieder auf.
                    if sent > 0:
                        sent_seqs = {s for s, _ in buffer_snapshot[:sent]}
                        self._outbound_buffer = deque(
                            ((s, p) for s, p in self._outbound_buffer
                             if s not in sent_seqs),
                            maxlen=OUTBOUND_BUFFER_MAXLEN,
                        )
        except Exception as err:
            self.logger.error(
                f"Watchdog-Resend failed: {type(err).__name__}: {err}"
            )
            self.logger.error(f"{traceback.format_exc()}")
        finally:
            # Writer-Identity-Guard analog zu _resend_buffered (reviewer R4
            # H2): verzoegertes finally eines alten Watchdog-Tasks darf
            # _resume_pending/_server_supports_resume der neuen Verbindung
            # nicht ueberschreiben. _resend_running kann unbedingt False,
            # weil es generell nur fuer die aktuell laufende Verbindung
            # gilt und ein alter Task sowieso tot ist.
            if self.writer is writer:
                self._resume_pending = False
            self._resend_running = False

    async def _resend_buffered(self, last_seen_seq: int) -> int:
        """Nach Resume-Ack: alle Buffer-Items mit seq > last_seen_seq erneut
        senden. Gleicher seq, damit Server dedupen kann. Returnt Anzahl der
        erneut gesendeten Items.

        KRITISCH: writer_lock wird einmal am Anfang geholt und ueber den
        gesamten Resend gehalten. Grund: wuerde er pro Item neu geholt,
        koennte ein paralleler send_teams/send_heartbeat/Encounter-send mit
        hoeherer seq dazwischenrutschen (z.B. Wire-Order 10, 20, 11, 12).
        Der Server dedupt dann 11+12 als "<= last_seen=20" und verwirft sie
        — Datenverlust trotz Replay. Lock-Dauer: Resend von 300 Items ~3s,
        waehrend derer heartbeat/send_teams blockieren, aber nicht ausreichen
        fuer Heartbeat-Timeout (HEARTBEAT_STALE_SECONDS=15s).

        Writer-Identity-Guard (sync-reviewer WARNUNG): writer lokal binden
        und vor jedem Item pruefen, dass er noch mit self.writer identisch
        ist. Ein Reconnect mitten im Resend wuerde sonst halbe Frames auf
        die neue Verbindung schreiben.

        Darf NICHT aus einem bereits locked Context aufgerufen werden —
        wird vom alter_teams-Handler gerufen, der keinen writer_lock haelt.
        Caller (resume_ack-Handler, Watchdog) sollten mit asyncio.shield
        wrappen damit mid-frame Cancel ausgeschlossen ist.
        """
        self._resend_running = True
        sent_count = 0
        try:
            # Writer lokal binden fuer Identity-Guard. Wenn ein Reconnect
            # self.writer umbiegt, brechen wir ab statt auf den neuen Writer
            # halbe Frames zu schreiben.
            writer = self.writer
            async with self.writer_lock:
                # UNTER LOCK Snapshot und _resume_pending kippen (sync-reviewer
                # Runde 2 WARNUNG W3): Flag + Snapshot atomar mit dem Lock-
                # Besitz. Zwischen Flag=False und erstem Resend-Frame kann so
                # kein paralleler send_message dazwischenrutschen. Ohne das:
                # Live-send mit seq=12 vor Resend-seq=10,11 → Server dedupt
                # 10,11 als "<= 12" und verwirft sie, stiller Datenverlust.
                to_resend = [(s, p) for s, p in self._outbound_buffer if s > last_seen_seq]
                self._resume_pending = False
                if not to_resend:
                    return 0
                # Eviction-Warn: ist first_seq > last_seen+1, haben wir Items
                # verloren (Buffer voll). Server dedupt den Rest, aber die
                # Luecke ist stiller Datenverlust ohne diesen Log.
                first_seq = to_resend[0][0]
                if first_seq > last_seen_seq + 1:
                    missing = first_seq - (last_seen_seq + 1)
                    self.logger.warning(
                        f"Resume: {missing} Items verloren (Buffer voll — seq "
                        f"{last_seen_seq + 1}..{first_seq - 1} aus deque evicted)"
                    )
                self.logger.info(
                    f"Resume: resende {len(to_resend)} Items "
                    f"(seq {first_seq}..{to_resend[-1][0]}, "
                    f"last_seen={last_seen_seq})"
                )
                CHUNK_SIZE = 500
                for seq, payload in to_resend:
                    if self.writer is not writer:
                        self.logger.warning(
                            f"Resume: writer identity changed waehrend Resend "
                            f"(seq={seq}) — Abbruch bei {sent_count}/"
                            f"{len(to_resend)} Items, Reconnect sendet den "
                            f"Rest nach dem naechsten resume_ack erneut"
                        )
                        break
                    wrapped = {"__seq__": seq, "__payload__": payload}
                    serialized = pickle.dumps(wrapped)
                    length = len(serialized).to_bytes(4, 'big')
                    writer.write(length)
                    await writer.drain()
                    for i in range(0, len(serialized), CHUNK_SIZE):
                        chunk = serialized[i:i+CHUNK_SIZE]
                        chunk_length = len(chunk).to_bytes(4, 'big')
                        writer.write(chunk_length)
                        await writer.drain()
                        writer.write(chunk)
                        await writer.drain()
                    self._msg_counters["sent"] = self._msg_counters.get("sent", 0) + 1
                    sent_count += 1
            return sent_count
        finally:
            # Safety-net: falls Exception vor dem Lock oder beim Snapshot
            # kippt, muss _resume_pending trotzdem auf False — sonst blockiert
            # jede weitere send_message endlos. Setzen hier ist idempotent.
            # Writer-Identity-Guard (reviewer R4 H2): wenn self.writer schon
            # auf eine NEUE Verbindung zeigt, hat ein spaetes finally eines
            # alten Zombie-Tasks hier nichts zu suchen — der neue connect()
            # hat _resume_pending=True gesetzt, wir wuerden ihn stoeren.
            if self.writer is writer:
                self._resume_pending = False
            self._resend_running = False

    async def receive_message(self):
        reader = self.reader
        # readexactly statt read — siehe Begruendung in arceus.receive_message:
        # read(N) ist nicht-deterministisch bei fragmentierten TCP-Paketen,
        # was bei den ~210 KB Gen 6/7 Boxes-Updates zu UnpicklingError und
        # Stream-Desynchronisation fuehrt.
        total_length = int.from_bytes(await reader.readexactly(4), 'big')
        message = b''

        while len(message) < total_length:
            chunk_length = int.from_bytes(await reader.readexactly(4), 'big')
            chunk = await reader.readexactly(chunk_length)
            message += chunk

        result = pickle.loads(message)
        msg_desc = result.get("type", f"{len(result)} Spieler") if isinstance(result, dict) else type(result).__name__
        self.logger.debug(f"Empfangen: {msg_desc}, {total_length} Bytes")
        return result
    
    def generate_hashed_id(self):
        random_id = os.urandom(16)
        
        hashed = hashlib.sha256(random_id).hexdigest()

        client_id = hashed
        return client_id