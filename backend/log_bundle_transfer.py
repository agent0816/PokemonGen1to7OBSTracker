"""Chunk-Protokoll fuer Log-Bundle-Upload Client->Host.

Reine Funktionen + State-Objekte, keine Netz-Logik. Munchlax/Arceus wrappen
diese Primitive in ihre send_message/receive_message-Pipeline.

Wire-Form eines Chunks (als Message-Payload):
  {
    "type": "log_bundle_chunk",
    "upload_id": str,
    "player_name": str,
    "chunk_index": int,            # 0-basiert
    "total_chunks": int,
    "total_bytes": int,
    "data": bytes,
    "sha256": str | None,          # nur im letzten Chunk gesetzt
  }
"""
from __future__ import annotations

import hashlib
import re
import secrets
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

from backend.logging_setup import get_logger

logger = get_logger("log_bundle_transfer", log_file="logs/log_bundle_transfer.log")

BUNDLE_CHUNK_SIZE = 256 * 1024              # 256 KiB
STALE_UPLOAD_TIMEOUT_S = 600                # 10 min seit letzter Aktivitaet
MAX_BUNDLE_BYTES = 512 * 1024 * 1024        # 512 MiB hartes Cap pro Upload
MAX_BUNDLE_CHUNKS = (MAX_BUNDLE_BYTES // BUNDLE_CHUNK_SIZE) + 1
MAX_UPLOADS_PER_CLIENT = 2                  # gegen DoS: 2 parallele Uploads reichen
MAX_TOTAL_BUNDLES_BYTES = 2 * 1024 ** 3     # 2 GiB globale Belegung support_bundles/
MAX_PLAYER_NAME_LEN = 32                    # kuerzt Final-Name gegen >255-char-Pfade
INCOMING_PREFIX = ".incoming_"
_UPLOAD_ID_RE = re.compile(r"\d{10,16}_[0-9a-f]{8}")


def generate_upload_id() -> str:
    return f"{int(time.time() * 1000)}_{secrets.token_hex(4)}"


def _valid_upload_id(upload_id: object) -> bool:
    # fullmatch verhindert, dass ein trailing \n (den re.match $ akzeptiert)
    # in Dateinamen landet.
    return isinstance(upload_id, str) and bool(_UPLOAD_ID_RE.fullmatch(upload_id))


def _dir_total_bytes(target_dir: Path) -> int:
    total = 0
    try:
        for entry in target_dir.iterdir():
            try:
                if entry.is_file():
                    total += entry.stat().st_size
            except OSError:
                continue
    except OSError:
        pass
    return total


def chunk_file(path: Path, upload_id: str, player_name: str) -> Iterator[dict]:
    """Streamt die Datei als Sequenz von Chunk-Messages. Keine Netz-I/O.

    Berechnet SHA-256 inkrementell; der Hex-Digest wird im letzten Chunk mitgegeben,
    damit der Empfänger die Integrität der zusammengesetzten Datei verifizieren kann.
    """
    path = Path(path)
    total_bytes = path.stat().st_size
    if total_bytes == 0:
        logger.warning(f"chunk_file: leere Datei {path}")
    total_chunks = max(1, (total_bytes + BUNDLE_CHUNK_SIZE - 1) // BUNDLE_CHUNK_SIZE)
    hasher = hashlib.sha256()

    with open(path, "rb") as f:
        index = 0
        while True:
            data = f.read(BUNDLE_CHUNK_SIZE)
            if not data and index > 0:
                break
            hasher.update(data)
            is_last = (index == total_chunks - 1)
            yield {
                "type": "log_bundle_chunk",
                "upload_id": upload_id,
                "player_name": player_name,
                "chunk_index": index,
                "total_chunks": total_chunks,
                "total_bytes": total_bytes,
                "data": data,
                "sha256": hasher.hexdigest() if is_last else None,
            }
            index += 1
            if is_last:
                break


@dataclass
class PendingUpload:
    upload_id: str
    player_name: str
    total_chunks: int
    total_bytes: int
    part_path: Path
    client_id: str = ""
    started_at: float = field(default_factory=time.time)
    last_activity: float = field(default_factory=time.time)
    received_count: int = 0
    expected_next: int = 0
    bytes_received: int = 0
    hasher: "hashlib._Hash" = field(default_factory=hashlib.sha256)
    # Per-Upload-Lock: serialisiert File-I/O auf dieser part-Datei. Der
    # Protokoll-Caller sendet stop-and-wait, in der Praxis also einer nach
    # dem anderen. Lock schuetzt gegen Fehlverhalten + gegen den Teardown
    # waehrend eines laufenden Writes (drop_pending wartet darauf).
    fs_lock: threading.Lock = field(default_factory=threading.Lock)
    # Drop-Flag: ``_drop_pending_locked`` setzt es unter _lock, der Writer
    # prueft es vor jedem Dateizugriff im fs_lock und bricht ab, statt den
    # Final-replace fuer einen bereits verworfenen Pending zu publizieren.
    # Unlink wird vom Writer selbst gemacht (Windows unlink-on-open-Fall).
    dropped: bool = False


class BundleReassembler:
    """Server-seitiger Zusammenbau mehrerer paralleler Uploads.

    Speichert eingehende Chunks in eine ``.incoming_<id>.part`` im Zielordner,
    verifiziert am Ende die SHA-256-Summe und benennt die Datei in das endgültige
    ``bundle_<ts>_<player>.zip`` um.
    """

    def __init__(self, target_dir: Path):
        self.target_dir = Path(target_dir)
        self.target_dir.mkdir(parents=True, exist_ok=True)
        self.pending: dict[str, PendingUpload] = {}
        # Reservierte Bytes aller laufenden Pendings (total_bytes - bytes_received).
        # Verhindert, dass N parallele Erst-Chunks die 2-GiB-Quota zusammen
        # ueberschreiten; auf Disk gezaehlte Bytes alleine reichen nicht
        # (Pendings sind erst teilweise geschrieben). Update unter self._lock.
        self._reserved_bytes: int = 0
        # Dict-Lock: nur Dict-Mutation (get/insert/pop + ganzzahlige
        # Status-Felder im Pending). Keine blockierenden Disk-Scans unter
        # diesem Lock — Quota-Berechnung (``_dir_total_bytes``) wird in
        # ``accept_chunk`` VOR dem Lock gemacht und nur beim ersten Chunk
        # noch mal unter Lock gegen Race geprueft. Unlinks laufen ebenfalls
        # ausserhalb des Lock (siehe ``_drop_pending`` /
        # ``_unlink_part_best_effort``). File-Write + Hash + Rename haengen
        # am pending.fs_lock (per-Upload serialisiert).
        self._lock = threading.Lock()
        # Shutdown-Flag: ``drop_all`` setzt es; ``accept_chunk`` prueft es
        # unter _lock und lehnt neue/laufende Chunks ab, damit kein
        # verwaister Pending nach stop() neu angelegt wird.
        self._closed: bool = False

    def accept_chunk(self, msg: dict, client_id: str = "") -> dict:
        """Verarbeitet einen Chunk. Returns Status-Dict für den Ack.

        Status:
          {"status": "ok", "chunk_index": N, "upload_id": ID, "final_name": name?}
          {"status": "error", "chunk_index": N, "upload_id": ID, "reason": str}

        ``client_id`` erlaubt den Upload-Count-Guard pro Client und das
        gezielte Verwerfen aller Pendings eines Clients bei Disconnect.
        """
        upload_id = msg.get("upload_id")
        chunk_index = msg.get("chunk_index")
        if not _valid_upload_id(upload_id):
            return {"status": "error", "upload_id": upload_id,
                    "chunk_index": chunk_index, "reason": "invalid upload_id"}
        if not isinstance(chunk_index, int) or chunk_index < 0:
            return {"status": "error", "upload_id": upload_id,
                    "chunk_index": chunk_index, "reason": "invalid chunk_index"}

        data = msg.get("data")
        total_chunks = msg.get("total_chunks")
        total_bytes = msg.get("total_bytes")
        # Kuerzen gegen >255-char-Pfade (Windows MAX_PATH + Linux-NAME_MAX).
        player_name = str(msg.get("player_name") or "unknown")[:MAX_PLAYER_NAME_LEN]
        sha_hex = msg.get("sha256")

        if not isinstance(data, (bytes, bytearray)):
            return {"status": "error", "upload_id": upload_id,
                    "chunk_index": chunk_index, "reason": "data not bytes"}
        if len(data) > BUNDLE_CHUNK_SIZE:
            return {"status": "error", "upload_id": upload_id,
                    "chunk_index": chunk_index,
                    "reason": f"chunk too large ({len(data)} > {BUNDLE_CHUNK_SIZE})"}
        if not isinstance(total_chunks, int) or total_chunks <= 0:
            return {"status": "error", "upload_id": upload_id,
                    "chunk_index": chunk_index, "reason": "invalid total_chunks"}
        if total_chunks > MAX_BUNDLE_CHUNKS:
            return {"status": "error", "upload_id": upload_id,
                    "chunk_index": chunk_index,
                    "reason": f"total_chunks exceeds cap ({total_chunks} > {MAX_BUNDLE_CHUNKS})"}
        if not isinstance(total_bytes, int) or total_bytes < 0 or total_bytes > MAX_BUNDLE_BYTES:
            return {"status": "error", "upload_id": upload_id,
                    "chunk_index": chunk_index,
                    "reason": f"invalid total_bytes ({total_bytes})"}
        if chunk_index >= total_chunks:
            return {"status": "error", "upload_id": upload_id,
                    "chunk_index": chunk_index,
                    "reason": f"chunk_index out of range ({chunk_index} >= {total_chunks})"}

        # Quota-Scan laeuft nur beim ersten Chunk eines Uploads (sonst
        # Verschwendung: iterdir+stat pro Chunk). Ergebnis ist Snapshot,
        # Lock-Scan unten mit reserved_bytes-Counter ergaenzt.
        used_pre = None
        if chunk_index == 0 and int(total_bytes or 0) > 0:
            used_pre = _dir_total_bytes(self.target_dir)

        pre_cleanup: PendingUpload | None = None
        pre_cleanup_reason: str | None = None
        new_pending = False

        # 1) Dict-Lock: Pending-Lookup + Neu-Anlage + Guards (keine Disk-I/O).
        with self._lock:
            if self._closed:
                return {"status": "error", "upload_id": upload_id,
                        "chunk_index": chunk_index,
                        "reason": "reassembler closed"}
            pending = self.pending.get(upload_id)
            if pending is None:
                if chunk_index != 0:
                    return {"status": "error", "upload_id": upload_id,
                            "chunk_index": chunk_index,
                            "reason": "first chunk must be index 0"}
                # Pro-Client-Guard gegen Disk-Fill-DoS.
                if client_id:
                    active = sum(
                        1 for p in self.pending.values()
                        if p.client_id == client_id
                    )
                    if active >= MAX_UPLOADS_PER_CLIENT:
                        return {"status": "error", "upload_id": upload_id,
                                "chunk_index": chunk_index,
                                "reason": f"too many concurrent uploads ({active})"}
                # Quota unter Lock: fertige Bundles (used_pre) + reserved
                # (noch nicht geschriebene Bytes aller offenen Pendings) +
                # neues total_bytes <= MAX. Verhindert N-paralleler-Erst-
                # Chunks-ueberschreiten-Quota (R5 WARN W2).
                tb = int(total_bytes or 0)
                used_at_scan = used_pre if used_pre is not None else 0
                if (used_at_scan + self._reserved_bytes + tb
                        > MAX_TOTAL_BUNDLES_BYTES):
                    return {"status": "error", "upload_id": upload_id,
                            "chunk_index": chunk_index,
                            "reason": (f"support_bundles-Quota erreicht "
                                        f"(used={used_at_scan} + "
                                        f"reserved={self._reserved_bytes} + "
                                        f"new={tb} > {MAX_TOTAL_BUNDLES_BYTES})")}
                part_path = self.target_dir / f"{INCOMING_PREFIX}{upload_id}.part"
                pending = PendingUpload(
                    upload_id=upload_id,
                    player_name=player_name,
                    total_chunks=int(total_chunks),
                    total_bytes=tb,
                    part_path=part_path,
                    client_id=client_id,
                )
                self.pending[upload_id] = pending
                self._reserved_bytes += tb
                new_pending = True
            else:
                # Ownership-Check: Fremde client_id kann einen Upload NICHT
                # mitten im Stream kapern (R5 WARN W1). Protokoll-Erwartung:
                # ein Pending gehoert nur seinem Besitzer.
                if client_id and pending.client_id and pending.client_id != client_id:
                    return {"status": "error", "upload_id": upload_id,
                            "chunk_index": chunk_index,
                            "reason": (f"foreign upload_id "
                                        f"(owned by {pending.client_id})")}
                # Konsistenz-Check: Chunk-weise identische Header.
                if pending.total_chunks != int(total_chunks) \
                        or pending.total_bytes != int(total_bytes or 0):
                    pre_cleanup = self.pending.pop(upload_id, None)
                    if pre_cleanup is not None:
                        pre_cleanup.dropped = True
                        self._reserved_bytes -= max(
                            0, pre_cleanup.total_bytes - pre_cleanup.bytes_received
                        )
                    pre_cleanup_reason = "header mismatch across chunks"
                elif chunk_index != pending.expected_next:
                    pre_cleanup = self.pending.pop(upload_id, None)
                    if pre_cleanup is not None:
                        pre_cleanup.dropped = True
                        self._reserved_bytes -= max(
                            0, pre_cleanup.total_bytes - pre_cleanup.bytes_received
                        )
                    pre_cleanup_reason = (
                        f"out of order (expected {pending.expected_next})"
                    )
                elif pending.bytes_received + len(data) > pending.total_bytes:
                    pre_cleanup = self.pending.pop(upload_id, None)
                    if pre_cleanup is not None:
                        pre_cleanup.dropped = True
                        self._reserved_bytes -= max(
                            0, pre_cleanup.total_bytes - pre_cleanup.bytes_received
                        )
                    pre_cleanup_reason = "bytes_received exceeds total_bytes"

        # Verzoegerte Disk-Operationen aus dem Dict-Lock-Block ausfuehren.
        if pre_cleanup is not None:
            self._unlink_part_best_effort(pre_cleanup)
            return {"status": "error", "upload_id": upload_id,
                    "chunk_index": chunk_index,
                    "reason": pre_cleanup_reason or "pre-cleanup"}

        if new_pending:
            logger.info(
                f"Neuer Upload {upload_id} von {player_name} (client={client_id}): "
                f"{total_chunks} chunks / {total_bytes} bytes"
            )

        # 2) File-I/O ausserhalb Dict-Lock, unter per-upload fs_lock. Loop-
        # Thread-Caller (cleanup/drop) blockieren hier NICHT auf fremdem
        # Disk-I/O, sondern finden den Pending im Dict und droppen ihn.
        with pending.fs_lock:
            # Re-Check: zwischen Dict-Lock-Release und fs_lock-Acquire kann
            # drop_client_uploads/drop_all/cleanup_stale uns den Pending aus
            # dem Dict genommen und das dropped-Flag gesetzt haben.
            if pending.dropped:
                self._unlink_part_best_effort(pending)
                return {"status": "error", "upload_id": upload_id,
                        "chunk_index": chunk_index,
                        "reason": "pending dropped before write"}

            # Bei neu angelegtem Pending die part-Datei unter fs_lock
            # entfernen BEVOR geoeffnet wird (R5 WARN W1). Serialisiert
            # Unlink und Write; stale part-Dateien aus Vor-Crash werden
            # hier entfernt, nicht beim zweiten open-Call.
            if new_pending:
                try:
                    pending.part_path.unlink(missing_ok=True)  # type: ignore[call-arg]
                except Exception as err:
                    logger.warning(
                        f"part-unlink vor Neu-Anlage fehlgeschlagen: {err}"
                    )

            try:
                with open(pending.part_path, "ab") as f:
                    f.write(bytes(data))
            except Exception as err:
                logger.error(f"Chunk-Write fehlgeschlagen {upload_id}#{chunk_index}: {err}")
                self._drop_pending_by_obj(pending)
                return {"status": "error", "upload_id": upload_id,
                        "chunk_index": chunk_index, "reason": f"write failed: {err}"}

            # Nach Close pruefen ob der Pending mittlerweile gedroppt wurde
            # (Windows unlink-on-open-Pfad). In dem Fall selbst unlinken +
            # abort, damit kein Final-replace fuer verworfenen Pending
            # publiziert.
            if pending.dropped:
                self._unlink_part_best_effort(pending)
                return {"status": "error", "upload_id": upload_id,
                        "chunk_index": chunk_index,
                        "reason": "pending dropped mid-write"}

            pending.hasher.update(bytes(data))
            # 3) Dict-Lock fuer Statusfelder, damit Snapshots in cleanup_stale
            # konsistent sind. KEIN unlink unter diesem Lock.
            deferred_unlink: PendingUpload | None = None
            with self._lock:
                if pending.dropped:
                    deferred_unlink = pending
                else:
                    pending.received_count += 1
                    pending.expected_next += 1
                    pending.bytes_received += len(data)
                    pending.last_activity = time.time()
                    # Reserved-Counter folgt dem tatsaechlichen Fortschritt.
                    self._reserved_bytes -= len(data)
                    if self._reserved_bytes < 0:
                        self._reserved_bytes = 0
            if deferred_unlink is not None:
                self._unlink_part_best_effort(deferred_unlink)
                return {"status": "error", "upload_id": upload_id,
                        "chunk_index": chunk_index,
                        "reason": "pending dropped mid-write"}

            is_last = (chunk_index == pending.total_chunks - 1)
            if not is_last:
                return {"status": "ok", "upload_id": upload_id,
                        "chunk_index": chunk_index}

            # Final-Chunk-Pfad weiter unter fs_lock (replace darf nicht mit
            # einem konkurrenten drop_pending/unlink kollidieren).
            if pending.bytes_received != pending.total_bytes:
                self._drop_pending_by_obj(pending)
                return {"status": "error", "upload_id": upload_id,
                        "chunk_index": chunk_index,
                        "reason": (f"byte-count mismatch "
                                    f"({pending.bytes_received}/{pending.total_bytes})")}

            actual_sha = pending.hasher.hexdigest()
            if not isinstance(sha_hex, str) or sha_hex.lower() != actual_sha.lower():
                logger.error(
                    f"SHA-Mismatch {upload_id}: expected={sha_hex} actual={actual_sha}"
                )
                self._drop_pending_by_obj(pending)
                return {"status": "error", "upload_id": upload_id,
                        "chunk_index": chunk_index, "reason": "sha256 mismatch"}

            # Publish: dropped-Check + Pop atomar unter _lock, aber das
            # ``replace()`` selbst laeuft NICHT unter _lock (R6 WARN replace-
            # unter-_lock). Rename ist zwar normalerweise metadata-only,
            # kann aber unter Windows + AV-Scan den Loop blockieren. Wir
            # halten weiterhin fs_lock, damit kein konkurrenter Drop die
            # Datei zwischen Pop und replace wegunlinkt.
            final_name = self._final_filename(pending)
            final_path = self.target_dir / final_name
            publish_ok = False
            with self._lock:
                if pending.dropped or self.pending.get(upload_id) is not pending:
                    deferred_unlink = pending
                else:
                    self.pending.pop(upload_id, None)
                    publish_ok = True
            if deferred_unlink is not None:
                self._unlink_part_best_effort(deferred_unlink)
                return {"status": "error", "upload_id": upload_id,
                        "chunk_index": chunk_index,
                        "reason": "pending dropped before publish"}
            if publish_ok:
                try:
                    pending.part_path.replace(final_path)
                except Exception as err:
                    logger.error(
                        f"Rename fehlgeschlagen {pending.part_path} "
                        f"-> {final_path}: {err}"
                    )
                    # Pop bereits erfolgt; dropped-Flag setzen + part-Rest
                    # entfernen, damit die Datei nicht als Orphan die Quota
                    # belastet (R6 WARN).
                    pending.dropped = True
                    self._unlink_part_best_effort(pending)
                    return {"status": "error", "upload_id": upload_id,
                            "chunk_index": chunk_index,
                            "reason": f"rename failed: {err}"}

        logger.info(
            f"Upload {upload_id} fertig: {final_name} "
            f"({pending.total_bytes} bytes, {pending.total_chunks} chunks)"
        )
        # Nur Dateiname zurueckgeben, kein absoluter Pfad (Info-Leak-Vermeidung).
        return {"status": "ok", "upload_id": upload_id,
                "chunk_index": chunk_index, "final_name": final_name}

    def cleanup_stale(self, max_age_s: int = STALE_UPLOAD_TIMEOUT_S) -> int:
        """Entfernt unfertige Uploads, deren letzte Aktivität > max_age_s zurück liegt."""
        stale: list[PendingUpload] = []
        with self._lock:
            if not self.pending:
                return 0
            now = time.time()
            stale_ids = [uid for uid, p in self.pending.items()
                         if (now - p.last_activity) > max_age_s]
            for uid in stale_ids:
                p = self.pending.pop(uid, None)
                if p is not None:
                    p.dropped = True
                    self._reserved_bytes -= max(0, p.total_bytes - p.bytes_received)
                    stale.append(p)
            if self._reserved_bytes < 0:
                self._reserved_bytes = 0
        for p in stale:
            logger.warning(f"Stale Upload gedroppt: {p.upload_id}")
            self._unlink_part_best_effort(p)
        return len(stale)

    def drop_client_uploads(self, client_id: str) -> int:
        """Verwirft alle Pendings eines Clients (z.B. nach Disconnect)."""
        if not client_id:
            return 0
        drops: list[PendingUpload] = []
        with self._lock:
            for uid in [uid for uid, p in self.pending.items()
                        if p.client_id == client_id]:
                p = self.pending.pop(uid, None)
                if p is not None:
                    p.dropped = True
                    self._reserved_bytes -= max(0, p.total_bytes - p.bytes_received)
                    drops.append(p)
            if self._reserved_bytes < 0:
                self._reserved_bytes = 0
        for p in drops:
            self._unlink_part_best_effort(p)
        if drops:
            logger.info(
                f"drop_client_uploads client={client_id}: {len(drops)} Pending(s) verworfen"
            )
        return len(drops)

    def drop_all(self) -> int:
        """Verwirft alle Pendings und markiert den Reassembler als
        geschlossen (``accept_chunk`` lehnt danach weitere Chunks ab)."""
        drops: list[PendingUpload] = []
        with self._lock:
            self._closed = True
            for uid in list(self.pending.keys()):
                p = self.pending.pop(uid, None)
                if p is not None:
                    p.dropped = True
                    drops.append(p)
            self._reserved_bytes = 0
        for p in drops:
            self._unlink_part_best_effort(p)
        return len(drops)

    def _drop_pending(self, upload_id: str) -> None:
        """Externer Entry: markiert als dropped und versucht opportunistisch
        die part-Datei zu entfernen. Fehlgeschlagene unlinks (Windows:
        unlink-on-open) werden nicht hart gefaehrdet — der Writer selbst
        entfernt die Datei nach dem Close, wenn er das dropped-Flag sieht.
        """
        pending: PendingUpload | None
        with self._lock:
            pending = self.pending.pop(upload_id, None)
            if pending is None:
                return
            pending.dropped = True
            self._reserved_bytes -= max(
                0, pending.total_bytes - pending.bytes_received
            )
            if self._reserved_bytes < 0:
                self._reserved_bytes = 0
        self._unlink_part_best_effort(pending)

    def _drop_pending_by_obj(self, pending: PendingUpload) -> None:
        """Dropt gezielt dieses Objekt, nicht nach ID (verhindert, dass ein
        gleichnamiger Nachfolger-Pending versehentlich entfernt wird)."""
        with self._lock:
            if self.pending.get(pending.upload_id) is pending:
                self.pending.pop(pending.upload_id, None)
            pending.dropped = True
            self._reserved_bytes -= max(
                0, pending.total_bytes - pending.bytes_received
            )
            if self._reserved_bytes < 0:
                self._reserved_bytes = 0
        self._unlink_part_best_effort(pending)

    def _drop_pending_locked(self, upload_id: str) -> None:
        """Erwartet dass self._lock bereits gehalten wird.

        Setzt nur das Dropped-Flag und entfernt aus Dict. KEIN Disk-I/O
        unter dem Lock (siehe Lock-Docstring + R4-WARN). Der eigentliche
        unlink wird dem Writer ueberlassen oder passiert in einem
        best-effort-Pass nach dem Lock-Release.
        """
        pending = self.pending.pop(upload_id, None)
        if pending is None:
            return
        pending.dropped = True
        self._reserved_bytes -= max(
            0, pending.total_bytes - pending.bytes_received
        )
        if self._reserved_bytes < 0:
            self._reserved_bytes = 0

    def _unlink_part_best_effort(self, pending: PendingUpload) -> None:
        """Versucht die part-Datei zu entfernen; Fehler werden geloggt aber
        nicht reraised. Auf Windows kann unlink waehrend open("ab") des
        Writers scheitern — in dem Fall unlinkt der Writer selbst nach
        Close (per dropped-Flag-Check).
        """
        try:
            pending.part_path.unlink()
        except FileNotFoundError:
            pass
        except OSError as err:
            logger.info(
                f"unlink deferred {pending.part_path.name} "
                f"(Writer entfernt beim Close): {err}"
            )

    @staticmethod
    def _final_filename(pending: PendingUpload) -> str:
        safe = "".join(c if c.isalnum() or c in "-_" else "_"
                       for c in (pending.player_name or "unknown"))
        ts = time.strftime("%Y-%m-%d_%H-%M-%S")
        # upload_id als Suffix verhindert Overwrite bei zwei Uploads desselben
        # Clients in derselben Sekunde (z.B. Dev-Button + Broadcast parallel).
        return f"bundle_{ts}_{safe or 'unknown'}_{pending.upload_id}_received.zip"
