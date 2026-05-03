"""Host-Level Memory-Writer fuer Azahar (Citra-Fork).

Azahars RPC-Whitelist fuer write_memory fehlt NEW_LINEAR_HEAP_VADDR
(0x30000000–0x40000000). Gen 7 (SM/USUM) legt alle Spieldaten dort ab.
WriteProcessMemory umgeht das Problem, indem direkt in Azahars
FCRAM-Buffer geschrieben wird.

Sobald Azahar upstream NEW_LINEAR_HEAP_VADDR whitelistet, kann dieses
Modul entfernt werden.
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import logging

kernel32 = ctypes.windll.kernel32

PROCESS_VM_READ = 0x0010
PROCESS_VM_WRITE = 0x0020
PROCESS_VM_OPERATION = 0x0008
PROCESS_QUERY_INFORMATION = 0x0400

MEM_COMMIT = 0x1000
FCRAM_SIZE = 256 * 1024 * 1024
LINEAR_BASE = 0x30000000

AZAHAR_PROCESS_NAME = "azahar.exe"


class MEMORY_BASIC_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BaseAddress", ctypes.c_void_p),
        ("AllocationBase", ctypes.c_void_p),
        ("AllocationProtect", wt.DWORD),
        ("RegionSize", ctypes.c_size_t),
        ("State", wt.DWORD),
        ("Protect", wt.DWORD),
        ("Type", wt.DWORD),
    ]


class AzaharWriter:

    def __init__(self):
        self._handle: int | None = None
        self._pid: int | None = None
        self._fcram_base: int | None = None
        self._linear_offset: int | None = None
        self._calibrated = False
        self.logger = logging.getLogger(__name__)

    def close(self):
        if self._handle:
            kernel32.CloseHandle(self._handle)
            self._handle = None
        self._calibrated = False
        self._fcram_base = None
        self._linear_offset = None
        self._pid = None

    def _find_pid(self) -> int | None:
        TH32CS_SNAPPROCESS = 0x00000002

        class PROCESSENTRY32(ctypes.Structure):
            _fields_ = [
                ("dwSize", wt.DWORD),
                ("cntUsage", wt.DWORD),
                ("th32ProcessID", wt.DWORD),
                ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
                ("th32ModuleID", wt.DWORD),
                ("cntThreads", wt.DWORD),
                ("th32ParentProcessID", wt.DWORD),
                ("pcPriClassBase", ctypes.c_long),
                ("dwFlags", wt.DWORD),
                ("szExeFile", ctypes.c_char * 260),
            ]

        snap = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
        if snap == -1:
            return None
        pe = PROCESSENTRY32()
        pe.dwSize = ctypes.sizeof(pe)
        pid = None
        if kernel32.Process32First(snap, ctypes.byref(pe)):
            while True:
                name = pe.szExeFile.decode("utf-8", errors="replace").lower()
                if name == AZAHAR_PROCESS_NAME:
                    pid = pe.th32ProcessID
                    break
                if not kernel32.Process32Next(snap, ctypes.byref(pe)):
                    break
        kernel32.CloseHandle(snap)
        return pid

    def _open_process(self) -> bool:
        pid = self._find_pid()
        if pid is None:
            self.logger.warning("Azahar-Prozess nicht gefunden.")
            return False
        access = PROCESS_VM_READ | PROCESS_VM_WRITE | PROCESS_VM_OPERATION | PROCESS_QUERY_INFORMATION
        handle = kernel32.OpenProcess(access, False, pid)
        if not handle:
            self.logger.warning(f"OpenProcess fuer PID {pid} fehlgeschlagen: {ctypes.GetLastError()}")
            return False
        self._handle = handle
        self._pid = pid
        return True

    def _find_fcram_region(self) -> bool:
        mbi = MEMORY_BASIC_INFORMATION()
        address = 0
        while address < 0x7FFFFFFFFFFF:
            result = kernel32.VirtualQueryEx(
                self._handle, ctypes.c_void_p(address),
                ctypes.byref(mbi), ctypes.sizeof(mbi),
            )
            if result == 0:
                break
            if mbi.State == MEM_COMMIT and FCRAM_SIZE <= mbi.RegionSize <= FCRAM_SIZE + 65536:
                self._fcram_base = mbi.BaseAddress
                self.logger.info(
                    f"FCRAM-Region gefunden: base=0x{mbi.BaseAddress:016X} "
                    f"size={mbi.RegionSize // (1024 * 1024)}MB"
                )
                return True
            address = (mbi.BaseAddress or 0) + mbi.RegionSize
            if mbi.RegionSize == 0:
                address += 4096
        self.logger.warning("Keine 256-MB-Region (FCRAM) in Azahar gefunden.")
        return False

    def calibrate(self, known_vaddr: int, known_pattern: bytes) -> bool:
        """Kalibriert das Adress-Mapping anhand eines bekannten Musters.

        known_vaddr: 3DS-Virtual-Adresse im LINEAR-Bereich (>= 0x30000000).
        known_pattern: dort erwartete Bytes (via Citra.read_memory() gelesen).
        """
        if self._handle is None:
            if not self._open_process():
                return False
        if self._fcram_base is None:
            if not self._find_fcram_region():
                return False

        virtual_offset = known_vaddr - LINEAR_BASE
        search_start = max(0, virtual_offset - 0x1000)
        search_end = min(FCRAM_SIZE, virtual_offset + 0x1000)
        buf_size = search_end - search_start
        buf = (ctypes.c_char * buf_size)()
        br = ctypes.c_size_t(0)
        ok = kernel32.ReadProcessMemory(
            self._handle,
            ctypes.c_void_p(self._fcram_base + search_start),
            buf, buf_size, ctypes.byref(br),
        )
        if not ok or br.value < len(known_pattern):
            self.logger.warning("ReadProcessMemory bei Kalibrierung fehlgeschlagen.")
            return False

        data = bytes(buf[: br.value])
        idx = data.find(known_pattern)
        if idx < 0:
            self.logger.warning(
                f"Kalibrierungsmuster nicht in FCRAM gefunden "
                f"(gesucht in Bereich +0x{search_start:X}..+0x{search_end:X})."
            )
            return False

        host_offset_from_region = search_start + idx
        self._linear_offset = host_offset_from_region - virtual_offset
        self._calibrated = True
        self.logger.info(
            f"Kalibrierung OK: linear_offset=0x{self._linear_offset:X} "
            f"(pattern bei FCRAM+0x{host_offset_from_region:X}, "
            f"erwartet 0x{virtual_offset:X})"
        )
        return True

    @property
    def is_calibrated(self) -> bool:
        return self._calibrated

    def write_memory(self, virtual_address: int, data: bytes) -> bool:
        if not self._calibrated or self._handle is None or self._fcram_base is None:
            return False

        host_addr = (
            self._fcram_base
            + self._linear_offset
            + (virtual_address - LINEAR_BASE)
        )
        bw = ctypes.c_size_t(0)
        ok = kernel32.WriteProcessMemory(
            self._handle, ctypes.c_void_p(host_addr),
            data, len(data), ctypes.byref(bw),
        )
        if not ok or bw.value != len(data):
            self.logger.warning(
                f"WriteProcessMemory an 0x{host_addr:016X} fehlgeschlagen: "
                f"ok={ok} written={bw.value}"
            )
            return False

        vbuf = (ctypes.c_char * len(data))()
        br = ctypes.c_size_t(0)
        kernel32.ReadProcessMemory(
            self._handle, ctypes.c_void_p(host_addr),
            vbuf, len(data), ctypes.byref(br),
        )
        if bytes(vbuf[: br.value]) != data:
            self.logger.warning(
                f"WriteProcessMemory Verify fehlgeschlagen an 0x{host_addr:016X}"
            )
            return False

        return True
