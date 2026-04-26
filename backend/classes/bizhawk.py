import logging
import sys
import asyncio
import traceback
from backend.classes.munchlax import Munchlax
from backend.classes.Pokemon import Pokemon
import backend.pokedecoder as pokedecoder
import backend.bh_pointers as bh_pointers

class Bizhawk:
    def __init__(self, host, port, bh):
        super().__init__()
        self.host = host
        self.port = port
        self.bizhawks = {}
        self.bizhawks_status = {}
        self.server = None
        self.is_connected = False
        self.disconnect_lock = asyncio.Lock()
        self.bh = bh
        self.about_to_exit = False
        self.save_automatically = True

        # Pro Client gepflegt, damit read_all_boxes() nach dem Handshake Zugriff
        # auf edition/language und damit auf die Box-Layouts hat.
        self.edition_per_client: dict[str, int] = {}
        self.language_per_client: dict[str, int] = {}
        # Jeder Eintrag ist (cmd, offset, size, future) — cmd = "box" (CartRAM,
        # Gen 1/2 SRAM) oder "boxw" (System-Domain, Gen 3/4/5 WRAM). Der
        # Haupt-Loop arbeitet die Queue im else-Zweig ab, ein Request pro Frame.
        self.box_request_queues: dict[str, list] = {}

        self.logger = self.init_logging()

    def init_logging(self):
        logger = logging.getLogger(__name__)
        logger.setLevel(logging.INFO)

        logging_formatter = logging.Formatter('[%(asctime)s] %(levelname)s: %(message)s')

        file_handler = logging.FileHandler('./logs/bizhawk.log', 'w')
        file_handler.setFormatter(logging_formatter)
        logger.addHandler(file_handler)

        stream_handler = logging.StreamHandler(sys.stdout)
        stream_handler.setFormatter(logging_formatter)
        logger.addHandler(stream_handler)

        return logger
    
    async def handle_bizhawk(self, reader, writer):
        client_id = None
        try:
            # id_length = int((await reader.read(2)).decode())
            client_id = (await self.receive_messages(reader)).decode()
            self.bizhawks[client_id] = writer
            self.bizhawks_status[client_id] = 'connected'
            self.logger.info(f"Emulator {client_id} connected.")

            def get_length():
                if edition > 10 and edition < 20:
                    return 331
                elif edition < 30:
                    return 362
                elif edition < 40:
                    return 601
                else:
                    return 1418

            def update_teams(msg):
                team: list[Pokemon] = pokedecoder.team(msg, edition)
                teams = self.munchlax.bizhawk_teams
                if player in teams:
                    if teams[player] == team:
                        return
                else:
                    teams[player] = team
                    self.munchlax.unsorted_teams[player] = team
                for index, pokemon in enumerate(team):
                    if index < 6 and player in teams:
                        if (pokemon.checksum_given < 0 or pokemon.checksum_given != pokemon.checksum_calculated) and edition > 30:
                            continue
                        else:
                            teams[player][index] = team[index]
                            self.munchlax.unsorted_teams[player][index] = team[index]
                    else:
                        teams[player][index] = team[index]
                        self.munchlax.unsorted_teams[player][index] = team[index]

            def update_stats(stats):
                team = self.munchlax.bizhawk_teams[player]
                splitted_stats = stats.split(",")
                for index, stat in enumerate(splitted_stats):
                    try:
                        new_hp = int(stat)
                        team[index].cur_hp = new_hp
                    except Exception as err:
                        self.logger.error(f"Mit folgenden Werten gescheitert:{splitted_stats}")
                self.munchlax.unsorted_teams[player] = team

            # edition_length = int((await reader.read(2)).decode())
            edition = int((await self.receive_messages(reader)).decode())
            language = int((await self.receive_messages(reader)).decode())
            player = int(client_id[7:])

            # Edition/Language merken, damit read_all_boxes() das Box-Layout
            # aus der YAML ermitteln kann, ohne es selbst zu cachen.
            self.edition_per_client[client_id] = edition
            self.language_per_client[client_id] = language

            # Pointer-Satz aus YAML bestimmen und an Lua zurückschicken (Phase 3)
            pointers = bh_pointers.get_pointers(edition, language if language else None)
            if not pointers:
                self.logger.error(
                    f"Keine Pointer für edition={edition}, language={language} in YAML — Lua erhält leere Konfig"
                )
            # Nur skalare Int-Pointer an Lua senden; box_sram_layout & Co. sind Listen
            # und bleiben Python-seitig, weil Lua die Box-Offsets nicht selbst braucht.
            pointer_str = ";".join(
                f"{k}={hex(v)}" for k, v in pointers.items() if isinstance(v, int)
            )
            await self.send_messages(writer, pointer_str)
            self.logger.info(f"Pointer-Satz an {client_id} (edition={edition}, language={language}): {pointer_str}")

            msg = (await self.receive_messages(reader)).decode()
            if msg != "Aufgabe":
                raise Exception("Irgendwas stimmt mit der Initialisierung nicht")
            else:
                await self.send_messages(writer, "team")
            length = get_length()
            msg = await reader.read(length)
            update_teams(msg)
            counter = 2
            in_battle = False
            while True:
                counter = counter % (60 * 10)
                try:
                    data = (await self.receive_messages(reader)).decode()
                    if (counter == 1 and self.bh["save_automatically"]) or self.about_to_exit:
                        if self.about_to_exit:
                            self.about_to_exit = False
                        await self.send_messages(writer, "saveRAM")
                        msg = (await self.receive_messages(reader)).decode()
                        self.logger.info(f"Bizhawk {client_id}: {msg}")
                    elif counter % 60 == 0:
                        await self.send_messages(writer, "team")
                        msg = await reader.read(length)
                        update_teams(msg)
                    elif counter % 60 == 2 and not in_battle:
                        await self.send_messages(writer, "in_battle")
                        data = (await self.receive_messages(reader)).decode()
                        in_battle = data == "true"
                    elif counter % 60 == 3 and in_battle and edition > 50:
                        await self.send_messages(writer, "stat_aktualisieren")
                        data = (await self.receive_messages(reader)).decode()
                        in_battle = False
                        update_stats(data)
                    else:
                        queue = self.box_request_queues.get(client_id)
                        if queue:
                            cmd, offset, size, fut = queue.pop(0)
                            try:
                                await self.send_messages(writer, f"{cmd} {offset:X} {size:X}")
                                box_bytes = await reader.readexactly(size)
                                if not fut.done():
                                    fut.set_result(box_bytes)
                            except Exception as err:
                                if not fut.done():
                                    fut.set_exception(err)
                                raise
                        else:
                            await self.send_messages(writer, data)

                    counter += 1
                except Exception as err:
                    self.logger.error(f"handle_bizhawk abgebrochen: {type(err)},{err}")
                    self.logger.error(f"{traceback.format_exc()}")
                    break
        except Exception as err:
            self.logger.error(f"handle_bizhawk abgebrochen: {type(err)},{err}")
            self.logger.error(f"{traceback.format_exc()}")

        await self.disconnect(client_id)

    async def read_all_boxes(self, client_id: str) -> list[bytes]:
        """Liest alle PC-Boxen des Clients aus dem Emulator-Speicher.

        Zwei Layouts:
          - Gen 1/2 (SRAM via CartRAM): YAML liefert box_sram_layout (Liste aus
            Bank/Start/Count) und box_stride. Python berechnet den linearen
            CartRAM-Offset (Bank N bei N * 0x2000, Start relativ zu 0xA000).
            Lua-Kommando: 'box <offset> <size>' (CartRAM-Domain).
            Bei Gen 1 wird zusätzlich die aktive Box aus dem WRAM gelesen
            (box_active_pointer + box_active_index_pointer) und in der Liste
            am aktuellen Box-Index ersetzt — die SRAM-Kopie ist bis zum
            nächsten In-Game-Save veraltet.
          - Gen 3/4/5 (WRAM): YAML liefert box_pointer + box_count + box_stride.
            Box i liegt bei box_pointer + i * box_stride in der System-Domain
            (System Bus für GBA, Main RAM / ARM9 System Bus für NDS).
            Lua-Kommando: 'boxw <offset> <size>' (state.domain).

        Die Kommandos werden frame-weise vom Main-Loop an Lua gesendet; diese
        Methode bündelt die Futures und liefert die rohen Box-Bytes in
        Layout-Reihenfolge zurück.
        """
        edition = self.edition_per_client.get(client_id)
        if edition is None:
            raise RuntimeError(f"Client {client_id} ist nicht (mehr) registriert.")
        language = self.language_per_client.get(client_id)

        pointers = bh_pointers.get_pointers(edition, language if language else None)
        layout = pointers.get("box_sram_layout")
        stride = pointers.get("box_stride")
        box_pointer = pointers.get("box_pointer")
        box_count = pointers.get("box_count")
        active_box_pointer = pointers.get("box_active_pointer")
        active_index_pointer = pointers.get("box_active_index_pointer")

        loop = asyncio.get_event_loop()
        futures: list[asyncio.Future] = []
        queue = self.box_request_queues.setdefault(client_id, [])

        if layout and stride:
            # Gen 1/2: SRAM-Bank-Layout via CartRAM
            for bank_cfg in layout:
                bank = bank_cfg["bank"]
                start = bank_cfg["start"]
                count = bank_cfg["count"]
                base = bank * 0x2000 + (start - 0xA000)
                for i in range(count):
                    offset = base + i * stride
                    fut = loop.create_future()
                    queue.append(("box", offset, stride, fut))
                    futures.append(fut)
            # Gen 1 hat zusätzlich eine aktive Box im WRAM, die in SRAM erst
            # nach In-Game-Save gespiegelt wird. Anhängen und in der Antwort
            # an die richtige Stelle einsortieren.
            active_index_fut = None
            active_box_fut = None
            if active_box_pointer and active_index_pointer:
                active_index_fut = loop.create_future()
                queue.append(("boxw", active_index_pointer, 1, active_index_fut))
                active_box_fut = loop.create_future()
                queue.append(("boxw", active_box_pointer, stride, active_box_fut))

            sram_boxes = await asyncio.gather(*futures)
            if active_index_fut and active_box_fut:
                idx_byte, active_box = await asyncio.gather(active_index_fut, active_box_fut)
                # Bit 7 ist das Modified-Flag, low 7 Bits = Index 0..(box_count-1)
                active_idx = idx_byte[0] & 0x7F
                if 0 <= active_idx < len(sram_boxes):
                    boxes = list(sram_boxes)
                    boxes[active_idx] = active_box
                    return boxes
                else:
                    self.logger.warning(
                        f"Aktiver Box-Index {active_idx} außerhalb 0..{len(sram_boxes)-1} "
                        f"— SRAM-Boxen unverändert gelassen."
                    )
            return list(sram_boxes)
        elif box_pointer and box_count and stride:
            # Gen 3/4/5: kontinuierlicher WRAM-Block, je Box stride Bytes
            slot_size = pokedecoder.box_slot_size(edition)
            slots_per_box = pokedecoder.slots_per_box(edition)
            box_size = slot_size * slots_per_box
            for i in range(box_count):
                offset = box_pointer + i * stride
                fut = loop.create_future()
                queue.append(("boxw", offset, box_size, fut))
                futures.append(fut)
            return await asyncio.gather(*futures)
        else:
            raise RuntimeError(
                f"Kein gültiges Box-Layout für edition={edition} — "
                f"YAML braucht entweder box_sram_layout+box_stride (Gen 1/2) "
                f"oder box_pointer+box_count+box_stride (Gen 3/4/5)."
            )

    async def read_and_decode_boxes(self, client_id: str) -> list[list]:
        """Liest alle Boxen vom Emulator und dekodiert sie.

        Rückgabe: Liste (Box-Index → Liste von Pokemon|None je Slot).
        """
        edition = self.edition_per_client.get(client_id)
        if edition is None:
            raise RuntimeError(f"Client {client_id} ist nicht (mehr) registriert.")
        raw_boxes = await self.read_all_boxes(client_id)
        return [pokedecoder.decode_box(box_bytes, edition) for box_bytes in raw_boxes]

    async def disconnect(self, client_id):
        # Noch offene Box-Futures mit Fehler beenden, damit read_all_boxes()
        # nicht ewig hängt, wenn der Emulator während der Abfrage wegbricht.
        queue = self.box_request_queues.pop(client_id, None)
        if queue:
            for _, _, _, fut in queue:
                if not fut.done():
                    fut.set_exception(ConnectionError(f"Emulator {client_id} disconnected"))
        self.edition_per_client.pop(client_id, None)
        self.language_per_client.pop(client_id, None)

        if client_id in self.bizhawks:
            self.bizhawks[client_id] = None
            self.bizhawks_status[client_id] = False
    
    async def start(self, munchlax):
        self.server = await asyncio.start_server(
            self.handle_bizhawk, self.host, self.port)

        self.munchlax: Munchlax = munchlax

        self.is_connected = True
        async with self.server:
            await self.server.serve_forever()
    
    async def stop(self):
        if self.server:
            self.server.close()
            await self.server.wait_closed()
            
            self.server = None
            self.is_connected = False
            
            self.port = self.bh['port']
            self.logger.info("Bizhawk has been stopped.")

    async def receive_messages(self, reader):
        length = b''
        flag = True
        while flag:
            data = await reader.read(1)
            if not data:
                return None
            if data.decode() != " ":
                length += data
            else:
                flag = False
        length = int(length.decode())

        result = await reader.read(length)

        return result

    async def send_messages(self, writer, message_to_biz):
        message = f"{len(message_to_biz)} {message_to_biz}"
        writer.write(message.encode())
        await writer.drain()

    async def stop_and_terminate(self, bizhawk_instances):
        self.about_to_exit = True
        await asyncio.sleep(1)
        await self.stop()

        for bizhawk in bizhawk_instances:
            bizhawk.terminate()
        