# Copyright Citra Emulator Project / Azahar Emulator Project
# Licensed under GPLv2 or any later version
# Refer to the license.txt file included.

import struct
import random
import enum
import socket

CURRENT_REQUEST_VERSION = 1
MAX_REQUEST_DATA_SIZE = 1024
MAX_PACKET_SIZE = 1024 + 0x10

class RequestType(enum.IntEnum):
    ReadMemory = 1,
    WriteMemory = 2,
    ProcessList = 3,
    SetGetProcess = 4,

CITRA_PORT = 45987

class Citra:
    def __init__(self, address="127.0.0.1", port=CITRA_PORT):
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.address = address
        # Ausgehandelte RPC-Protokoll-Version. Azahar 2126.2 hat die Wire-
        # Version von 1 auf 2 angehoben und prueft im Server strikt auf
        # Gleichheit; Server <2126.2 akzeptieren nur Version 1. Der Default
        # bleibt 1; negotiate_version() ermittelt die passende Version.
        self.request_version = CURRENT_REQUEST_VERSION

    def is_connected(self):
        return self.socket is not None

    def _generate_header(self, request_type, data_size):
        request_id = random.getrandbits(32)
        return (struct.pack("IIII", self.request_version, request_id, request_type, data_size), request_id)

    def _read_and_validate_header(self, raw_reply, expected_id, expected_type):
        reply_version, reply_id, reply_type, reply_data_size = struct.unpack("IIII", raw_reply[:4*4])
        if (self.request_version == reply_version and
            expected_id == reply_id and
            expected_type == reply_type and
            reply_data_size == len(raw_reply[4*4:])):
            return raw_reply[4*4:]
        return None

    def negotiate_version(self) -> bool:
        """Probt die RPC-Protokoll-Version des Azahar/Citra-Servers.

        Azahar 2126.2 hat die Wire-Version von 1 auf 2 angehoben; der Server
        verwirft Requests mit abweichender Version stillschweigend. Server
        <2126.2 akzeptieren umgekehrt nur Version 1. Wir senden je Kandidat-
        Version einen billigen 1-Byte-Read und uebernehmen die erste Version,
        die gueltig antwortet.

        Waehrend der Probe setzen wir ein kurzes Socket-Timeout, damit ein
        stiller (falsche Version) oder gefilterter Server nicht blockiert;
        danach wird das vorherige Timeout wiederhergestellt.

        Rueckgabe: True, wenn eine Version antwortet (self.request_version ist
        dann gesetzt), sonst False (RPC-Server aus oder nicht erreichbar).
        """
        prev_timeout = self.socket.gettimeout()
        prev_version = self.request_version
        try:
            for version in (1, 2):
                # Verspätete Antworten der vorigen Probe verwerfen, sonst liest
                # jeder Folge-Request die Antwort seines Vorgängers.
                self._drain()
                self.socket.settimeout(1.0)
                self.request_version = version
                try:
                    if self.read_memory(0, 1) is not None:
                        return True
                except (socket.timeout, ConnectionResetError, OSError, struct.error):
                    continue
            self.request_version = prev_version
            return False
        finally:
            self._drain()
            self.socket.settimeout(prev_timeout)

    def _drain(self):
        """Liest alle bereits eingetroffenen Antworten nicht-blockierend weg."""
        self.socket.settimeout(0)
        try:
            while True:
                self.socket.recv(MAX_PACKET_SIZE)
        except OSError:  # BlockingIOError/ConnectionResetError = Puffer leer
            pass

    def process_list(self):
        processes = {}
        read_processes = 0
        while True:
            request_data = struct.pack("II", read_processes, 0x7FFFFFFF)
            request, request_id = self._generate_header(RequestType.ProcessList, len(request_data))
            request += request_data
            self.socket.sendto(request, (self.address, CITRA_PORT))

            raw_reply = self.socket.recv(MAX_PACKET_SIZE)
            reply_data = self._read_and_validate_header(raw_reply, request_id, RequestType.ProcessList)

            if reply_data:
                read_count = struct.unpack("I", reply_data[0:4])[0]
                reply_data = reply_data[4:]
                if read_count == 0:
                    break
                read_processes += read_count
                for i in range(read_count):
                    proc_data = reply_data[i * 0x14 : (i + 1) * 0x14]
                    proc_id, title_id, proc_name = struct.unpack("<IQ8s", proc_data)
                    proc_name = proc_name.rstrip(b"\x00").decode("ascii")
                    processes[proc_id] = (title_id, proc_name)
            else:
                break
        return processes

    def get_process(self):
        request_data = struct.pack("II", 0, 0)
        request, request_id = self._generate_header(RequestType.SetGetProcess, len(request_data))
        request += request_data
        self.socket.sendto(request, (self.address, CITRA_PORT))

        raw_reply = self.socket.recv(MAX_PACKET_SIZE)
        reply_data = self._read_and_validate_header(raw_reply, request_id, RequestType.SetGetProcess)

        if reply_data:
            return struct.unpack("I", reply_data)[0]
        else:
            return None

    def set_process(self, process_id):
        request_data = struct.pack("II", 1, process_id)
        request, request_id = self._generate_header(RequestType.SetGetProcess, len(request_data))
        request += request_data
        self.socket.sendto(request, (self.address, CITRA_PORT))

        self.socket.recv(MAX_PACKET_SIZE)

    def read_memory(self, read_address, read_size):
        """
        >>> c.read_memory(0x100000, 4)
        b'\\x07\\x00\\x00\\xeb'
        """
        result = bytes()
        while read_size > 0:
            temp_read_size = min(read_size, MAX_REQUEST_DATA_SIZE)
            request_data = struct.pack("II", read_address, temp_read_size)
            request, request_id = self._generate_header(RequestType.ReadMemory, len(request_data))
            request += request_data
            self.socket.sendto(request, (self.address, CITRA_PORT))

            raw_reply = self.socket.recv(MAX_PACKET_SIZE)
            reply_data = self._read_and_validate_header(raw_reply, request_id, RequestType.ReadMemory)

            if reply_data:
                result += reply_data
                read_size -= len(reply_data)
                read_address += len(reply_data)
            else:
                return None

        return result

    def write_memory(self, write_address, write_contents):
        """
        >>> c.write_memory(0x100000, b"\\xff\\xff\\xff\\xff")
        True
        >>> c.read_memory(0x100000, 4)
        b'\\xff\\xff\\xff\\xff'
        >>> c.write_memory(0x100000, b"\\x07\\x00\\x00\\xeb")
        True
        >>> c.read_memory(0x100000, 4)
        b'\\x07\\x00\\x00\\xeb'
        """
        write_size = len(write_contents)
        while write_size > 0:
            temp_write_size = min(write_size, MAX_REQUEST_DATA_SIZE - 8)
            request_data = struct.pack("II", write_address, temp_write_size)
            request_data += write_contents[:temp_write_size]
            request, request_id = self._generate_header(RequestType.WriteMemory, len(request_data))
            request += request_data
            self.socket.sendto(request, (self.address, CITRA_PORT))

            raw_reply = self.socket.recv(MAX_PACKET_SIZE)
            reply_data = self._read_and_validate_header(raw_reply, request_id, RequestType.WriteMemory)

            if None != reply_data:
                write_address += temp_write_size
                write_size -= temp_write_size
                write_contents = write_contents[temp_write_size:]
            else:
                return False
        return True

if "__main__" == __name__:
    import doctest
    doctest.testmod(extraglobs={'c': Citra()})
