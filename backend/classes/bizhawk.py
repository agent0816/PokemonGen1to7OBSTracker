import asyncio
import traceback
from backend.classes.munchlax import Munchlax
from backend.classes.Pokemon import Pokemon
import backend.pokedecoder as pokedecoder
import backend.bh_pointers as bh_pointers
import backend.bag_decoder as bag_decoder
from backend.encounter_tracker import EncounterTracker
from backend.logging_setup import get_logger

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
        # Jeder Eintrag ist (cmd, offset, size, future, payload_hex) —
        # cmd = "box" (CartRAM, Gen 1/2 SRAM) oder "boxw" (System-Domain, Gen
        # 3/4/5 WRAM) oder "bag" (Bag-Pockets, ebenfalls System-Domain) oder
        # "bagw" (Bag schreiben — payload_hex enthaelt die Bytes hex-codiert,
        # size ist immer 1 fuer das ACK-Byte). Bei Read-Befehlen ist
        # payload_hex == "". Der Haupt-Loop arbeitet die Queue im else-Zweig ab,
        # ein Request pro Frame.
        self.box_request_queues: dict[str, list] = {}
        # Verhindert ueberlappende Bag-Refreshs pro Client.
        self._bag_refresh_inflight: dict[str, bool] = {}
        # Verhindert ueberlappende Encounter-Reads pro Client.
        self._encounter_inflight: dict[str, bool] = {}
        self._last_battle_pv: dict[str, int | None] = {}
        self._last_map_header: dict[str, int] = {}
        # Vor Kampfstart gecachte Party-Groesse; nach Kampfende gegen den
        # aktuellen Wert diffed (Fix 2: Party-Count-Diff → deckt Fang mit
        # freiem Party-Slot auch dann ab, wenn PID-in-Team-Check aus Race-
        # Gruenden noch nicht griff).
        self._last_party_count: dict[str, int] = {}
        # Wird von update_teams gesetzt und von _check_encounter_outcome
        # gewartet — verhindert die Race zwischen "in_battle=false" (Kampfende)
        # und dem nächsten Party-Read (60-Tick-Slot). Ohne dieses Signal würde
        # der Outcome-Check gegen die Party-Momentaufnahme vor dem Fang laufen.
        self._team_updated_events: dict[str, asyncio.Event] = {}
        self.encounter_tracker = None
        # Serialisiert Bag-IO pro Client — Reads und Schreibvorgaenge duerfen
        # sich nicht ueberschneiden, sonst lesen wir ein halb-geschriebenes
        # Pocket oder allokieren denselben leeren Slot doppelt.
        self._bag_io_locks: dict[str, asyncio.Lock] = {}

        self.logger = get_logger(__name__, './logs/bizhawk.log')
    
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
                elif edition < 44:
                    return 1417
                elif edition < 50:
                    return 1418
                else:
                    return 1321

            def update_teams(msg):
                team: list[Pokemon] = pokedecoder.team(msg, edition)
                teams = self.munchlax.bizhawk_teams
                # Signal an _check_encounter_outcome, dass ein frischer
                # Party-Read vorliegt — auch wenn sich nichts geändert hat.
                # Ohne dieses Set würde ein hängender Outcome-Task ins Timeout
                # laufen, sobald der Spieler nach dem Kampf nichts weiter tut.
                ev = self._team_updated_events.get(client_id)
                if ev is not None:
                    ev.set()
                # Vor dem Update den alten Dex-Stand merken — wenn sich die
                # Tupel-Identität ändert (Pokemon ins/aus PC verschoben,
                # Tausch, Fang, Evolution), triggern wir später einen
                # gedrosselten Auto-Box-Refresh.
                old_team = teams.get(player)
                # team-Liste enthält am Ende badges/edition-Ints — nur Slot 0..5 sind Pokemon.
                old_dexnrs = tuple(p.dexnr for p in old_team[:6]) if old_team else None
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
                new_dexnrs = tuple(p.dexnr for p in team[:6])
                # Erstladung (old_dexnrs is None) triggert ebenfalls — so füllt
                # sich der Box-Cache automatisch nach dem Verbinden, ohne dass
                # der User "Aktualisieren" klicken muss.
                if old_dexnrs != new_dexnrs and self.munchlax.should_auto_refresh_boxes(player):
                    self.munchlax.mark_box_refresh(player)
                    asyncio.create_task(self._auto_refresh_boxes(client_id, player))

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
            self.logger.debug(f"Handshake: client={client_id}, edition={edition}, language={language}, player={player}")

            name = self.munchlax.pl.get('your_name', '')
            if name:
                self.munchlax.player_names[player] = name

            self.munchlax._on_new_pokemon_detected = lambda p, ed, pkmns, pvs: (
                self._handle_new_pokemon(client_id, p, ed, pkmns, pvs)
            )

            # Edition/Language merken, damit read_all_boxes() das Box-Layout
            # aus der YAML ermitteln kann, ohne es selbst zu cachen.
            self.edition_per_client[client_id] = edition
            self.language_per_client[client_id] = language
            # Frisch initialisiertes Event pro Client — braucht laufenden Loop.
            self._team_updated_events[client_id] = asyncio.Event()

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
                    self.logger.debug(f"Tick {counter}: client={client_id}, in_battle={in_battle}, queue_len={len(self.box_request_queues.get(client_id, []))}")
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
                        if in_battle and not self._encounter_inflight.get(client_id):
                            self._encounter_inflight[client_id] = True
                            asyncio.create_task(
                                self._read_encounter_data(client_id, player, edition)
                            )
                        elif not in_battle and self._last_battle_pv.get(client_id) is not None:
                            asyncio.create_task(
                                self._check_encounter_outcome(client_id, player, edition)
                            )
                    elif counter % 60 == 4 and not in_battle:
                        asyncio.create_task(
                            self._refresh_map_header(client_id, edition)
                        )
                    elif counter % 60 == 3 and in_battle and edition > 50:
                        await self.send_messages(writer, "stat_aktualisieren")
                        data = (await self.receive_messages(reader)).decode()
                        in_battle = False
                        update_stats(data)
                    elif counter % 300 == 30:
                        # Periodischer Bag-Refresh: alle 5 s einen async Task
                        # starten. Der Task fuellt die queue mit "bag"-Reads,
                        # die der Main-Loop in Folge-Frames abarbeitet — daher
                        # blockiert nichts. _bag_refresh_inflight verhindert
                        # Ueberlappung, falls die Reads laenger als 5 s brauchen.
                        asyncio.create_task(self._auto_refresh_bag(client_id, player))
                        await self.send_messages(writer, data)
                    else:
                        queue = self.box_request_queues.get(client_id)
                        if queue:
                            cmd, offset, size, fut, payload_hex = queue.pop(0)
                            try:
                                if cmd == "bagw":
                                    await self.send_messages(
                                        writer, f"bagw {offset:X} {payload_hex}"
                                    )
                                    ack = await reader.readexactly(1)
                                    if not fut.done():
                                        fut.set_result(ack[0] == 0x01)
                                else:
                                    await self.send_messages(
                                        writer, f"{cmd} {offset:X} {size:X}"
                                    )
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
          - Smaragd-Sonderfall: statt box_pointer setzt die YAML
            box_pointer_indirect (feste IWRAM-Adresse des 4-Byte-LE-Pointers
            auf gPokemonStorage) und box_pointer_offset (Header-Skip). Wir
            lesen erst diese 4 Byte, dereferenzieren und nutzen das Ergebnis
            als Box-Basis. Eine zusätzliche Lua-Roundtrip ~16 ms vor den
            Box-Reads.

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
        indirect_addr = pointers.get("box_pointer_indirect")
        indirect_offset = pointers.get("box_pointer_offset", 0)

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
                    queue.append(("box", offset, stride, fut, ""))
                    futures.append(fut)
            # Gen 1 hat zusätzlich eine aktive Box im WRAM, die in SRAM erst
            # nach In-Game-Save gespiegelt wird. Anhängen und in der Antwort
            # an die richtige Stelle einsortieren.
            active_index_fut = None
            active_box_fut = None
            if active_box_pointer and active_index_pointer:
                active_index_fut = loop.create_future()
                queue.append(("boxw", active_index_pointer, 1, active_index_fut, ""))
                active_box_fut = loop.create_future()
                queue.append(("boxw", active_box_pointer, stride, active_box_fut, ""))

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
        elif box_count and stride and (box_pointer or indirect_addr):
            # Gen 3/4/5: kontinuierlicher WRAM-Block, je Box stride Bytes.
            # Smaragd nutzt Indirektion (box_pointer_indirect) — wir lesen erst
            # 4 Byte an der festen IWRAM-Adresse, addieren box_pointer_offset
            # und nehmen das Ergebnis als Box-Basis.
            if indirect_addr:
                ptr_fut = loop.create_future()
                queue.append(("boxw", indirect_addr, 4, ptr_fut, ""))
                ptr_bytes = await ptr_fut
                deref = int.from_bytes(ptr_bytes, "little")
                effective_base = deref + indirect_offset
                self.logger.debug(
                    f"Indirekter Box-Pointer edition={edition}: "
                    f"*0x{indirect_addr:08X}=0x{deref:08X} +0x{indirect_offset:X} "
                    f"-> Box-Basis 0x{effective_base:08X}"
                )
            else:
                effective_base = box_pointer
            slot_size = pokedecoder.box_slot_size(edition)
            slots_per_box = pokedecoder.slots_per_box(edition)
            box_size = slot_size * slots_per_box
            for i in range(box_count):
                offset = effective_base + i * stride
                fut = loop.create_future()
                queue.append(("boxw", offset, box_size, fut, ""))
                futures.append(fut)
            return await asyncio.gather(*futures)
        else:
            raise RuntimeError(
                f"Kein gültiges Box-Layout für edition={edition} — "
                f"YAML braucht entweder box_sram_layout+box_stride (Gen 1/2) "
                f"oder (box_pointer | box_pointer_indirect)+box_count+box_stride (Gen 3/4/5)."
            )

    async def read_bag_pockets(self, client_id: str) -> dict[str, bytes]:
        """Liest alle definierten Bag-Pockets und liefert {pocket_key: raw_bytes}.

        Auflist-Logik:
          - Gen 1/2: Pointer-YAML enthaelt 'bag_<key>' Adressen, die auf den
            ersten Slot zeigen. Das count-Byte liegt 1 B davor, deshalb lesen
            wir ab addr - 1 mit der Window-Groesse aus
            bag_decoder.pocket_window_size().
          - Gen 3 R/S: statische 'bag_<key>' Adressen, kein count-Prefix.
          - Gen 3 E/FR/BG: 'bag_qty_xor_encrypted'==True. Wir lesen erst die
            4 Pointer-Bytes an box_pointer_indirect, addieren KEIN
            box_pointer_offset (das gilt nur fuer Boxen, nicht Bags), und
            berechnen pro Pocket bag_basis + 'bag_<key>_offset'.
          - Gen 4/5: statische 'bag_<key>' Adressen, kein Prefix.

        Liefert die rohen Bytes — Dekodierung uebernimmt der Caller via
        bag_decoder.decode_pocket(). Dadurch kann der Caller den security_key
        aus dem TMVM-Window selbst ableiten.
        """
        edition = self.edition_per_client.get(client_id)
        if edition is None:
            raise RuntimeError(f"Client {client_id} ist nicht (mehr) registriert.")
        language = self.language_per_client.get(client_id)
        pointers = bh_pointers.get_pointers(edition, language if language else None)

        gen = bag_decoder.edition_to_gen(edition)
        loop = asyncio.get_event_loop()
        queue = self.box_request_queues.setdefault(client_id, [])

        # Pocket-Adressen pro Edition ermitteln. Fehlende Pockets ueberspringen.
        addrs: dict[str, int] = {}
        gen3_indirect = (gen == 3 and bool(pointers.get("bag_qty_xor_encrypted")))
        if gen3_indirect:
            indirect_addr = pointers.get("box_pointer_indirect")
            if indirect_addr is None:
                self.logger.error(
                    f"Gen 3 indirect Bag fuer edition={edition}, aber kein "
                    f"box_pointer_indirect im Pointer-YAML."
                )
                return {}
            ptr_fut = loop.create_future()
            queue.append(("bag", indirect_addr, 4, ptr_fut, ""))
            ptr_bytes = await ptr_fut
            bag_basis = int.from_bytes(ptr_bytes, "little")
            # Beim Titelbildschirm / Continue-Auswahl ist gPokemonStoragePtr noch
            # uninitialisiert (0 oder Garbage). Mit den negativen bag_*_offset
            # waeren Pocket-Adressen negativ — Lua's Hex-Regex matcht nicht und
            # der Loop deadlocked, weil Lua keine Bytes zurueckschickt. SaveBlock1
            # liegt im EWRAM (0x02000000-0x0203FFFF), also gegen den Bereich pruefen.
            if not (0x02000000 <= bag_basis < 0x02040000):
                self.logger.debug(
                    f"Gen 3 bag_basis 0x{bag_basis:08X} ausserhalb EWRAM "
                    f"(edition={edition}) — Save vermutlich noch nicht geladen, "
                    f"ueberspringe Bag-Refresh."
                )
                return {}
            for key in bag_decoder.KNOWN_POCKET_KEYS:
                offset = pointers.get(f"bag_{key}_offset")
                if offset is not None:
                    addrs[key] = bag_basis + offset
        else:
            for key in bag_decoder.KNOWN_POCKET_KEYS:
                addr = pointers.get(f"bag_{key}")
                if isinstance(addr, int):
                    addrs[key] = addr

        if not addrs:
            return {}

        # bag_decoder.pocket_window kuemmert sich um die generationenabhaengige
        # Start-Offset-Logik (Gen 1/2 lesen ab addr-1, damit das count-Byte
        # enthalten ist; Gen 2 TMVM und Gen 3+ ab addr).
        futures: dict[str, asyncio.Future] = {}
        for key, addr in addrs.items():
            start, size = bag_decoder.pocket_window(addr, edition, key)
            fut = loop.create_future()
            queue.append(("bag", start, size, fut, ""))
            futures[key] = fut

        results: dict[str, bytes] = {}
        for key, fut in futures.items():
            try:
                results[key] = await fut
            except Exception as err:
                self.logger.warning(
                    f"Bag-Read fehlgeschlagen edition={edition} pocket={key}: "
                    f"{type(err)},{err}"
                )
        return results

    async def add_rare_candies(self, client_id: str, count: int) -> tuple[bool, str]:
        """Schreibt 'count' Sonderbonbons in die korrekte Pocket des Clients.

        Read-Modify-Write:
          1. Aktuelle Pocket frisch lesen (mit derselben Logik wie der Reader,
             damit XOR-Encryption + Gen 3 Indirection korrekt aufgeloest werden).
          2. plan_bag_write() entscheidet, ob ein bestehender Slot upgedatet
             oder ein neuer angelegt wird.
          3. Geplante Bytes ueber 'bagw'-Lua-Kommandos rausschicken.

        Liefert (success, status_message). Die Message ist UI-tauglich (z.B.
        "Tasche voll" oder "+5 Sonderbonbons (jetzt 12)").
        """
        edition = self.edition_per_client.get(client_id)
        if edition is None:
            return False, "Spieler nicht verbunden."
        if count <= 0:
            return False, "Anzahl muss > 0 sein."

        gen = bag_decoder.edition_to_gen(edition)
        pocket_key = bag_decoder.RARE_CANDY_POCKET.get(gen)
        if pocket_key is None:
            return False, f"Sonderbonbon-Pocket fuer Gen {gen} nicht definiert."
        item_id = bag_decoder.find_rare_candy_id(edition)
        if item_id is None:
            return False, f"Keine Sonderbonbon-ID fuer Edition {edition}."

        lock = self._bag_io_locks.setdefault(client_id, asyncio.Lock())
        async with lock:
            try:
                raw_pockets = await self.read_bag_pockets(client_id)
            except Exception as err:
                self.logger.error(
                    f"add_rare_candies: read_bag_pockets fehlgeschlagen "
                    f"client={client_id}: {type(err)},{err}"
                )
                return False, "Lesen der Tasche fehlgeschlagen."
            if not raw_pockets:
                return False, "Save noch nicht geladen — Tasche nicht erreichbar."
            pocket_bytes = raw_pockets.get(pocket_key)
            if pocket_bytes is None:
                return False, f"Pocket '{pocket_key}' fuer Edition {edition} nicht gelesen."

            # Gen 3 E/FR/BG braucht den security_key; gleiche Ableitung wie
            # in read_and_decode_bag.
            security_key: int | None = None
            if gen == 3 and edition >= 33:
                tmvm_raw = raw_pockets.get("tmvm")
                if tmvm_raw:
                    security_key = bag_decoder.derive_security_key_from_tmvm(tmvm_raw)
                if security_key is None:
                    return False, "security_key konnte nicht abgeleitet werden."

            try:
                plan = bag_decoder.plan_bag_write(
                    edition, pocket_key, pocket_bytes, item_id, count,
                    security_key=security_key,
                )
            except Exception as err:
                self.logger.error(
                    f"plan_bag_write fehlgeschlagen edition={edition} "
                    f"pocket={pocket_key}: {type(err)},{err}"
                )
                return False, "Schreibplan konnte nicht erstellt werden."
            if plan.status == "full":
                return False, f"Pocket '{pocket_key}' ist voll."
            if plan.status == "noop" or not plan.writes:
                return False, "Nichts zu schreiben."

            pocket_addr = await self._resolve_pocket_addr_async(
                client_id, edition, pocket_key,
            )
            if pocket_addr is None:
                return False, "Pocket-Adresse konnte nicht berechnet werden."
            # Offsets im Plan sind relativ zum pocket_window — also inklusive
            # des -1 Shift fuer Gen 1/2 (count-Byte). pocket_addr ist daher die
            # Window-Start-Adresse, nicht die YAML-Adresse.
            ok = await self._send_bag_writes(client_id, pocket_addr, plan.writes)
            if not ok:
                return False, "Lua hat das Schreiben mit ERR quittiert."
            return True, f"+{count} Sonderbonbon{'s' if count > 1 else ''} (jetzt {plan.new_qty})"

    async def _resolve_pocket_addr_async(self, client_id: str, edition: int,
                                          pocket_key: str) -> int | None:
        """Async-Variante von _resolve_pocket_write_base, die fuer Gen 3 indirect
        die SaveBlock-Pointer-Auflesung im Lua-Roundtrip mitmacht. Liefert die
        Window-Start-Adresse (also pocket_window().start).
        """
        language = self.language_per_client.get(client_id)
        pointers = bh_pointers.get_pointers(edition, language if language else None)
        gen = bag_decoder.edition_to_gen(edition)

        if gen == 3 and bool(pointers.get("bag_qty_xor_encrypted")):
            indirect_addr = pointers.get("box_pointer_indirect")
            if indirect_addr is None:
                return None
            loop = asyncio.get_event_loop()
            queue = self.box_request_queues.setdefault(client_id, [])
            ptr_fut = loop.create_future()
            queue.append(("bag", indirect_addr, 4, ptr_fut, ""))
            try:
                ptr_bytes = await ptr_fut
            except Exception:
                return None
            bag_basis = int.from_bytes(ptr_bytes, "little")
            if not (0x02000000 <= bag_basis < 0x02040000):
                return None
            offset = pointers.get(f"bag_{pocket_key}_offset")
            if offset is None:
                return None
            yaml_addr = bag_basis + offset
        else:
            yaml_addr = pointers.get(f"bag_{pocket_key}")
            if not isinstance(yaml_addr, int):
                return None
        start, _ = bag_decoder.pocket_window(yaml_addr, edition, pocket_key)
        return start

    async def _send_bag_writes(self, client_id: str, base_addr: int,
                                writes: tuple) -> bool:
        """Setzt die geplanten Schreibvorgaenge in einzelne 'bagw'-Kommandos um.

        writes sind Tupel (offset_relativ, bytes) — wir addieren base_addr drauf
        und schicken pro Eintrag ein bagw-Kommando in die Lua-Queue.
        """
        loop = asyncio.get_event_loop()
        queue = self.box_request_queues.setdefault(client_id, [])
        futures: list[asyncio.Future] = []
        for offset, data in writes:
            payload_hex = data.hex().upper()
            fut = loop.create_future()
            queue.append(("bagw", base_addr + offset, 1, fut, payload_hex))
            futures.append(fut)
        results = await asyncio.gather(*futures, return_exceptions=True)
        return all(r is True for r in results)

    async def read_and_decode_bag(self, client_id: str) -> dict[str, list]:
        """Liest und dekodiert alle Bag-Pockets. Liefert {pocket_key: list[BagItem]}.

        Bei Gen 3 E/FR/BG wird der security_key automatisch aus dem TMVM-Window
        abgeleitet (erstes leeres Slot enthaelt qty_raw == key).
        """
        edition = self.edition_per_client.get(client_id)
        if edition is None:
            raise RuntimeError(f"Client {client_id} ist nicht (mehr) registriert.")
        raw_pockets = await self.read_bag_pockets(client_id)
        if not raw_pockets:
            return {}

        security_key: int | None = None
        gen = bag_decoder.edition_to_gen(edition)
        if gen == 3 and edition >= 33:
            tmvm_raw = raw_pockets.get("tmvm")
            if tmvm_raw:
                security_key = bag_decoder.derive_security_key_from_tmvm(tmvm_raw)
            if security_key is None:
                self.logger.warning(
                    f"Gen 3 edition={edition}: konnte security_key nicht aus "
                    f"TMVM-Window ableiten — Pocket war moeglicherweise voll."
                )
                return {}

        decoded: dict[str, list] = {}
        for pocket_key, raw_bytes in raw_pockets.items():
            try:
                decoded[pocket_key] = bag_decoder.decode_pocket(
                    edition, pocket_key, raw_bytes,
                    item_lut=None, security_key=security_key,
                )
            except Exception as err:
                self.logger.warning(
                    f"decode_pocket fehlgeschlagen edition={edition} "
                    f"pocket={pocket_key}: {type(err)},{err}"
                )
        return decoded

    async def read_and_decode_boxes(self, client_id: str) -> list[list]:
        """Liest alle Boxen vom Emulator und dekodiert sie.

        Rückgabe: Liste (Box-Index → Liste von Pokemon|None je Slot).
        """
        edition = self.edition_per_client.get(client_id)
        if edition is None:
            raise RuntimeError(f"Client {client_id} ist nicht (mehr) registriert.")
        raw_boxes = await self.read_all_boxes(client_id)
        return [pokedecoder.decode_box(box_bytes, edition) for box_bytes in raw_boxes]

    async def _auto_refresh_boxes(self, client_id: str, player: int):
        """Box-Read als Reaktion auf Team-Änderung. Cache + Push via Munchlax."""
        try:
            boxes = await self.read_and_decode_boxes(client_id)
            await self.munchlax.update_boxes(player, boxes)
            self.logger.info(f"Auto-Box-Refresh player={player}: {len(boxes)} Boxen aktualisiert.")
        except Exception as err:
            self.logger.warning(f"Auto-Box-Refresh player={player} fehlgeschlagen: {type(err)},{err}")
            self.logger.warning(f"{traceback.format_exc()}")

    async def _auto_refresh_bag(self, client_id: str, player: int):
        """Bag-Read als periodischer Tick. Persistiert Pockets in PokedexDB.

        Mehrfache Aufrufe ueberschneiden sich nicht (Lock pro Client) — bei 5
        Pockets a 1 Frame Roundtrip dauert ein Refresh ca. 100 ms; das
        Polling-Intervall liegt bei 5 s, also ist die Ueberlappung
        unwahrscheinlich, der Lock ist trotzdem ein Sicherheitsnetz.
        """
        if self._bag_refresh_inflight.get(client_id):
            return
        self._bag_refresh_inflight[client_id] = True
        try:
            edition = self.edition_per_client.get(client_id)
            if edition is None:
                return
            pockets = await self.read_and_decode_bag(client_id)
            if not pockets:
                return
            await self.munchlax.update_bag(player, edition, pockets)
            self.logger.debug(
                f"Auto-Bag-Refresh player={player}: "
                f"{', '.join(f'{k}={len(v)}' for k, v in pockets.items())}"
            )
        except Exception as err:
            self.logger.warning(f"Auto-Bag-Refresh player={player} fehlgeschlagen: {type(err)},{err}")
            self.logger.warning(f"{traceback.format_exc()}")
        finally:
            self._bag_refresh_inflight[client_id] = False

    async def _resolve_gen4_pointer_chain(self, queue, pointers, offset: int) -> int:
        """Löst die Gen-4-Pointer-Chain auf (identisch zur Badge-Lesung in Lua).

        Schritte: badgepointer → read u32, mask 24bit → +0x20 → read u32, mask 24bit → +offset → read u16
        """
        loop = asyncio.get_event_loop()
        badge_ptr = pointers.get("badgepointer", 0)

        fut1 = loop.create_future()
        queue.append(("boxw", badge_ptr, 4, fut1, ""))
        base1 = int.from_bytes(await fut1, "little") & 0xFFFFFF

        fut2 = loop.create_future()
        queue.append(("boxw", base1 + 0x20, 4, fut2, ""))
        base2 = int.from_bytes(await fut2, "little") & 0xFFFFFF

        fut3 = loop.create_future()
        queue.append(("boxw", base2 + offset, 2, fut3, ""))
        return int.from_bytes(await fut3, "little")

    async def _refresh_map_header(self, client_id: str, edition: int):
        """Liest periodisch die aktuelle Map-Header-ID für Gift-Erkennung."""
        try:
            language = self.language_per_client.get(client_id)
            pointers = bh_pointers.get_pointers(edition, language)
            queue = self.box_request_queues.get(client_id)
            if queue is None:
                return
            loop = asyncio.get_event_loop()

            map_header_ptr = pointers.get("mapheaderpointer", 0)
            if map_header_ptr and edition < 40:
                # Gen 3: mapLayoutId (u16) bei gMapHeader + 0x12
                fut = loop.create_future()
                queue.append(("boxw", map_header_ptr + 0x12, 2, fut, ""))
                layout_id = int.from_bytes(await fut, "little")
                if edition in (31, 32) and layout_id > 107:
                    layout_id -= 1
                self._last_map_header[client_id] = layout_id
                return

            mapid_offset = pointers.get("mapidoffset", 0)
            if mapid_offset:
                self._last_map_header[client_id] = await self._resolve_gen4_pointer_chain(
                    queue, pointers, mapid_offset
                )
                return

            map_ptr = pointers.get("mapidpointer", 0)
            if not map_ptr:
                return
            fut = loop.create_future()
            queue.append(("boxw", map_ptr, 2, fut, ""))
            map_bytes = await fut
            self._last_map_header[client_id] = int.from_bytes(map_bytes, "little")
        except Exception:
            pass

    async def _read_encounter_data(self, client_id: str, player: int,
                                    edition: int):
        """Liest Gegner-Daten bei Kampfbeginn und prüft Nuzlocke-Regeln.

        Nutzt die bestehende boxw-Queue: battleopponentidpointer (u16) für
        Wild/Trainer-Unterscheidung, dann battleopponentpointer für den
        gegnerischen Party-Slot.
        """
        try:
            language = self.language_per_client.get(client_id)
            pointers = bh_pointers.get_pointers(edition, language)
            opp_id_ptr = pointers.get("battleopponentidpointer", 0)
            opp_ptr = pointers.get("battleopponentpointer", 0)
            if not opp_id_ptr or not opp_ptr:
                return

            queue = self.box_request_queues.get(client_id)
            if queue is None:
                return
            loop = asyncio.get_event_loop()

            oid_fut = loop.create_future()
            queue.append(("boxw", opp_id_ptr, 2, oid_fut, ""))
            oid_bytes = await oid_fut
            opponent_id = int.from_bytes(oid_bytes, "little")

            if opponent_id != 0:
                self.logger.debug(f"Trainer-Kampf erkannt (opponent_id=0x{opponent_id:04X}), kein Encounter")
                return

            if edition < 40:
                slot_size = 100
            elif edition > 50:
                slot_size = 220
            else:
                slot_size = 236
            opp_fut = loop.create_future()
            queue.append(("boxw", opp_ptr, slot_size, opp_fut, ""))
            opp_bytes = await opp_fut

            if edition < 40:
                opp = pokedecoder.decode_opponent_gen3(opp_bytes)
            else:
                opp = pokedecoder.decode_opponent_gen45(opp_bytes)
            if opp is None:
                self.logger.debug("Gegner-Slot leer oder ungültig")
                return

            route = opp["met_location"]

            map_header = None
            map_header_ptr = pointers.get("mapheaderpointer", 0)
            if map_header_ptr and edition < 40:
                fut = loop.create_future()
                queue.append(("boxw", map_header_ptr + 0x12, 2, fut, ""))
                layout_id = int.from_bytes(await fut, "little")
                if edition in (31, 32) and layout_id > 107:
                    layout_id -= 1
                map_header = layout_id
                if route == 0:
                    mapsec_fut = loop.create_future()
                    queue.append(("boxw", map_header_ptr + 0x14, 1, mapsec_fut, ""))
                    route = (await mapsec_fut)[0]
            else:
                mapid_offset = pointers.get("mapidoffset", 0)
                if mapid_offset:
                    map_header = await self._resolve_gen4_pointer_chain(
                        queue, pointers, mapid_offset
                    )
                else:
                    map_ptr = pointers.get("mapidpointer", 0)
                    if map_ptr:
                        map_fut = loop.create_future()
                        queue.append(("boxw", map_ptr, 2, map_fut, ""))
                        map_bytes = await map_fut
                        map_header = int.from_bytes(map_bytes, "little")

                if route == 0 and map_header is not None and map_header != 0:
                    route = map_header

            self._last_battle_pv[client_id] = opp["personality"]
            if map_header is not None:
                self._last_map_header[client_id] = map_header

            # Party-Groesse vor dem Kampf einfrieren — der Outcome-Check
            # vergleicht spaeter gegen den Wert nach Kampfende (Fix 2).
            if edition < 40:
                pc_before = await self._read_party_count(client_id, edition)
                if pc_before is not None:
                    self._last_party_count[client_id] = pc_before

            if self.encounter_tracker is None:
                self.munchlax._ensure_pokedex_db()
                if self.munchlax.pokedex_db is not None:
                    self.encounter_tracker = EncounterTracker(
                        self.munchlax.pokedex_db,
                        self.munchlax.nuz,
                        munchlax=self.munchlax,
                    )

            if self.encounter_tracker is None:
                self.logger.warning("PokedexDB nicht verfügbar, Encounter wird übersprungen")
                return

            from backend.classes.pokedex_db import PokedexDB
            owner = PokedexDB.build_owner(
                self.munchlax.pl.get('your_name', ''), str(player)
            )
            result = await loop.run_in_executor(
                None, self.encounter_tracker.process_wild_encounter,
                owner, edition, opp, route
            )
            if result.already_logged:
                self.logger.debug(
                    f"Encounter bereits geloggt (PV={opp['personality']:#x})"
                )
            else:
                map_info = f" map_header={map_header}" if map_header is not None else ""
                self.logger.info(
                    f"Encounter: route={route}{map_info} dex={opp['dexnr']} "
                    f"lv={opp['lvl']} shiny={opp['shiny']} first={result.is_first} "
                    f"shiny_override={result.is_shiny_override} "
                    f"dupes={result.is_dupes_skip} balls={result.has_balls}"
                )
                asyncio.create_task(self.munchlax.send_encounter_sync({
                    "personality": int(opp["personality"]),
                    "owner": owner,
                    "edition": int(edition),
                    "route": int(route),
                    "dexnr": int(opp["dexnr"]),
                    "lvl": int(opp["lvl"]),
                    "shiny": int(opp["shiny"]),
                    "is_first": int(result.is_first),
                    "is_shiny_override": int(result.is_shiny_override),
                    "is_dupes_skip": int(result.is_dupes_skip),
                    "has_balls": int(result.has_balls),
                    "method": result.method,
                    "outcome": result.outcome,
                }))
        except Exception as err:
            self.logger.error(f"Encounter-Read fehlgeschlagen: {type(err)},{err}")
            self.logger.error(f"{traceback.format_exc()}")
        finally:
            self._encounter_inflight[client_id] = False

    def _handle_new_pokemon(self, client_id: str, player: int, edition,
                            pokemons, new_pvs: list[int]):
        """Prüft ob neue Pokemon Gift-Encounters sind (nicht aus Kampf).

        Wird als Callback aus dem sync-Kontext (_persist_teams → Munchlax-Callback)
        aufgerufen. Die DB-Arbeit (process_gift_encounter läuft unter
        PokedexDB.access_lock) darf den Event-Loop-Thread nicht blocken —
        daher als Task in den Loop schieben.
        """
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError as err:
            # Kein Loop laufend — Fallback synchron (Testkontext)
            self.logger.debug(f"_handle_new_pokemon: kein Loop, sync fallback: {err}")
            self._process_new_pokemon_sync(client_id, player, edition, pokemons, new_pvs)
            return
        loop.create_task(self._handle_new_pokemon_async(
            client_id, player, edition, pokemons, new_pvs))

    async def _handle_new_pokemon_async(self, client_id: str, player: int, edition,
                                          pokemons, new_pvs: list[int]):
        try:
            await self._handle_new_pokemon_async_impl(
                client_id, player, edition, pokemons, new_pvs)
        except Exception as err:
            # Fire-and-forget-Task — Exceptions dürfen nicht stumm sterben
            # (CLAUDE.md: asyncio-Tasks brauchen try/except).
            self.logger.error(
                f"_handle_new_pokemon_async failed: {type(err).__name__},{err}")
            self.logger.error(traceback.format_exc())

    async def _handle_new_pokemon_async_impl(self, client_id: str, player: int, edition,
                                               pokemons, new_pvs: list[int]):
        battle_pv = self._last_battle_pv.get(client_id)
        non_battle_pvs = [pv for pv in new_pvs if pv != battle_pv]
        if not non_battle_pvs or self.encounter_tracker is None:
            return

        map_header = self._last_map_header.get(client_id, 0)

        from backend.classes.pokedex_db import PokedexDB
        owner = PokedexDB.build_owner(
            self.munchlax.pl.get('your_name', ''), str(player)
        )

        loop = asyncio.get_event_loop()
        for pv in non_battle_pvs:
            pokemon = None
            for p in pokemons[:6]:
                if getattr(p, "personality", None) is not None and int(p.personality) == pv:
                    pokemon = p
                    break
            if pokemon is None:
                continue

            dexnr = pokemon.dexnr
            if dexnr == 0 or dexnr == "egg":
                continue

            try:
                result = await loop.run_in_executor(
                    None,
                    lambda pv=pv, pokemon=pokemon, dexnr=dexnr: self.encounter_tracker.process_gift_encounter(
                        owner=owner,
                        edition=int(edition),
                        personality=pv,
                        dexnr=int(dexnr) if isinstance(dexnr, (int, str)) and str(dexnr).isdigit() else 0,
                        lvl=pokemon.lvl or 1,
                        shiny=bool(pokemon.shiny),
                        route=getattr(pokemon, "route", 0) or 0,
                        map_header_id=map_header or 0,
                    ),
                )
            except Exception as err:
                self.logger.error(f"process_gift_encounter async failed: {err}")
                self.logger.error(traceback.format_exc())
                continue
            if result and not result.already_logged:
                self.logger.info(
                    f"Gift erkannt: dex={dexnr} lv={pokemon.lvl} "
                    f"method={result.method} map_header={map_header}"
                )
                asyncio.create_task(self.munchlax.send_encounter_sync({
                    "personality": int(pv),
                    "owner": owner,
                    "edition": int(edition),
                    "route": int(result.route),
                    "dexnr": int(result.dexnr),
                    "lvl": int(result.lvl),
                    "shiny": int(result.shiny),
                    "is_first": int(result.is_first),
                    "is_shiny_override": int(result.is_shiny_override),
                    "is_dupes_skip": int(result.is_dupes_skip),
                    "has_balls": int(result.has_balls),
                    "method": result.method,
                    "outcome": result.outcome,
                }))
            if result and result.token_earned:
                # Trigger auf dem Event-Loop-Thread — process_gift_encounter im
                # Executor-Worker konnte kein asyncio.create_task rufen.
                self.logger.info(f"Token-Earn ausgelöst für {owner}")
                asyncio.create_task(self.munchlax.send_soullink_token_earned(owner))

    def _process_new_pokemon_sync(self, client_id: str, player: int, edition,
                                    pokemons, new_pvs: list[int]):
        """Sync-Fallback für Testkontexte ohne laufenden asyncio-Loop."""
        battle_pv = self._last_battle_pv.get(client_id)
        non_battle_pvs = [pv for pv in new_pvs if pv != battle_pv]
        if not non_battle_pvs or self.encounter_tracker is None:
            return
        map_header = self._last_map_header.get(client_id, 0)
        from backend.classes.pokedex_db import PokedexDB
        owner = PokedexDB.build_owner(
            self.munchlax.pl.get('your_name', ''), str(player)
        )
        for pv in non_battle_pvs:
            pokemon = None
            for p in pokemons[:6]:
                if getattr(p, "personality", None) is not None and int(p.personality) == pv:
                    pokemon = p
                    break
            if pokemon is None:
                continue
            dexnr = pokemon.dexnr
            if dexnr == 0 or dexnr == "egg":
                continue
            self.encounter_tracker.process_gift_encounter(
                owner=owner, edition=int(edition), personality=pv,
                dexnr=int(dexnr) if isinstance(dexnr, (int, str)) and str(dexnr).isdigit() else 0,
                lvl=pokemon.lvl or 1, shiny=bool(pokemon.shiny),
                route=getattr(pokemon, "route", 0) or 0,
                map_header_id=map_header or 0,
            )

    async def _read_party_count(self, client_id: str, edition: int) -> int | None:
        """Liest gPlayerPartyCount (u8) — Adresse aus partycountpointer."""
        try:
            language = self.language_per_client.get(client_id)
            pointers = bh_pointers.get_pointers(edition, language)
            pc_ptr = pointers.get("partycountpointer", 0)
            if not pc_ptr:
                return None
            queue = self.box_request_queues.get(client_id)
            if queue is None:
                return None
            loop = asyncio.get_event_loop()
            fut = loop.create_future()
            queue.append(("boxw", pc_ptr, 1, fut, ""))
            data = await fut
            return data[0]
        except Exception as err:
            self.logger.error(f"Party-Count-Read fehlgeschlagen: {type(err)},{err}")
            self.logger.error(f"{traceback.format_exc()}")
            return None

    async def _scan_active_box_gen3(self, client_id: str, edition: int,
                                      battle_pv: int) -> str | None:
        """Scannt aktive PC-Box in Gen 3 nach battle_pv (PID-Match).

        Return "caught", wenn PID im ersten Slot-Chunk der aktiven Box liegt.
        None bei leerer Box, PID nicht gefunden, oder Read-Fehler.

        Leere Slots werden per PID==0-Heuristik erkannt (Gen 3 loescht
        entlassene Slots komplett). Suche stoppt am ersten leeren Slot.
        """
        try:
            language = self.language_per_client.get(client_id)
            pointers = bh_pointers.get_pointers(edition, language)
            queue = self.box_request_queues.get(client_id)
            if queue is None:
                return None
            loop = asyncio.get_event_loop()

            # Box-Basis aufloesen: R/S static, E/FR/BG per indirect-Pointer.
            # Indirect-Pointer bewegt sich innerhalb einer Session, deshalb
            # bei jedem Scan neu dereferenzieren.
            box_indirect = pointers.get("box_pointer_indirect", 0)
            static_box_ptr = pointers.get("box_pointer", 0)
            static_cb_ptr = pointers.get("currentbox_pointer", 0)
            box_pointer_offset = pointers.get("box_pointer_offset", 0)
            cb_indirect_offset = pointers.get("currentbox_indirect_offset", 0)

            if box_indirect:
                ptr_fut = loop.create_future()
                queue.append(("boxw", box_indirect, 4, ptr_fut, ""))
                deref = int.from_bytes(await ptr_fut, "little")
                currentbox_addr = deref + cb_indirect_offset
                box_base = deref + box_pointer_offset
            elif static_box_ptr and static_cb_ptr:
                currentbox_addr = static_cb_ptr
                box_base = static_box_ptr
            else:
                self.logger.debug("Box-Scan: keine Box-Pointer verfuegbar")
                return None

            cb_fut = loop.create_future()
            queue.append(("boxw", currentbox_addr, 1, cb_fut, ""))
            current_box = (await cb_fut)[0]

            box_count = pointers.get("box_count", 14)
            if current_box >= box_count:
                self.logger.warning(
                    f"Box-Scan: currentBox={current_box} ausserhalb 0..{box_count-1}"
                )
                return None

            box_stride = pointers.get("box_stride", 0x960)
            slot_size = pokedecoder.box_slot_size(edition)
            slots = pokedecoder.slots_per_box(edition)
            active_box_addr = box_base + current_box * box_stride

            box_fut = loop.create_future()
            queue.append(("boxw", active_box_addr, slot_size * slots, box_fut, ""))
            box_bytes = await box_fut

            for i in range(slots):
                slot_start = i * slot_size
                pid = int.from_bytes(box_bytes[slot_start:slot_start + 4], "little")
                if pid == 0:
                    # Erster leerer Slot markiert das Ende der belegten Slots.
                    break
                if pid == int(battle_pv):
                    self.logger.info(
                        f"Box-Scan: PV={battle_pv:#x} in Box {current_box} "
                        f"Slot {i} gefunden -> caught"
                    )
                    return "caught"
            return None
        except Exception as err:
            self.logger.error(f"Box-Scan fehlgeschlagen: {type(err)},{err}")
            self.logger.error(f"{traceback.format_exc()}")
            return None

    async def _probe_opponent_hp_gen3(self, client_id: str, edition: int,
                                        battle_pv: int) -> str | None:
        """Liest PID + current HP am Opponent-Slot neu.

        Return-Werte:
          - "not_caught": PID matched noch und HP ist 0 → Gegner ist ausgeknockt,
            konnte also nicht gefangen worden sein
          - None: Slot bereits überschrieben (PID mismatch) oder HP > 0 →
            Signal nicht eindeutig, Caller nutzt Team/Box-Fallback
        """
        try:
            language = self.language_per_client.get(client_id)
            pointers = bh_pointers.get_pointers(edition, language)
            opp_ptr = pointers.get("battleopponentpointer", 0)
            if not opp_ptr:
                return None
            queue = self.box_request_queues.get(client_id)
            if queue is None:
                return None
            loop = asyncio.get_event_loop()

            pid_fut = loop.create_future()
            queue.append(("boxw", opp_ptr, 4, pid_fut, ""))
            hp_fut = loop.create_future()
            queue.append(("boxw", opp_ptr + 0x56, 2, hp_fut, ""))

            pid_bytes = await pid_fut
            hp_bytes = await hp_fut
            pid_now = int.from_bytes(pid_bytes, "little")
            hp_now = int.from_bytes(hp_bytes, "little")

            if pid_now != int(battle_pv):
                self.logger.debug(
                    f"HP-Probe: PID gewechselt ({pid_now:#x} != {battle_pv:#x}), "
                    f"kein sicheres Signal"
                )
                return None
            if hp_now == 0:
                self.logger.info(
                    f"HP-Probe: PV={battle_pv:#x} HP=0 → not_caught"
                )
                return "not_caught"
            return None
        except Exception as err:
            self.logger.error(f"HP-Probe fehlgeschlagen: {type(err)},{err}")
            self.logger.error(f"{traceback.format_exc()}")
            return None

    async def _check_encounter_outcome(self, client_id: str, player: int,
                                        edition: int):
        """Prüft nach Kampfende ob das Gegner-Pokemon gefangen wurde.

        Kaskade (Gen 3):
          1. HP-Probe: Gegner-HP=0 mit gleicher PID → not_caught (deterministisch)
          2. Team-Refresh abwarten (Event)
          3. Party-PV-Check gegen bizhawk_teams
          4. Party-Count-Diff gegen Wert vor Kampfstart (Fix 2)
          5. Aktive PC-Box scannen (Fix 3) — deckt volle Party ab
          Fallback: not_caught
        """
        try:
            battle_pv = self._last_battle_pv.pop(client_id, None)
            party_count_before = self._last_party_count.pop(client_id, None)
            if battle_pv is None or self.encounter_tracker is None:
                return

            outcome: str | None = None

            # Fix 1: HP-Probe (Gen 3). Erkennt KO-Fall (Gegner gefaintet), bei
            # dem der Fang unmöglich war.
            if edition < 40:
                outcome = await self._probe_opponent_hp_gen3(
                    client_id, edition, int(battle_pv)
                )

            # Fix 4: auf nächsten Team-Refresh warten, sonst läuft der PV-Check
            # gegen den Party-Snapshot von VOR dem Fang (Race zwischen
            # in_battle-Flanke und dem 60-Tick-Team-Poll).
            if outcome is None:
                event = self._team_updated_events.get(client_id)
                if event is not None:
                    event.clear()
                    try:
                        await asyncio.wait_for(event.wait(), timeout=2.0)
                    except asyncio.TimeoutError:
                        self.logger.warning(
                            f"Outcome-Check {client_id}: kein Team-Refresh "
                            f"binnen 2s, nutze aktuellen bizhawk_teams-Stand"
                        )

                team = self.munchlax.bizhawk_teams.get(player, [])
                team_pvs = set()
                for p in team[:6]:
                    pv = getattr(p, "personality", None)
                    if pv is not None:
                        team_pvs.add(int(pv))

                if int(battle_pv) in team_pvs:
                    outcome = "caught"
                else:
                    outcome = "not_caught"

                # Fix 2: Party-Count-Diff. Wenn Team-Check "not_caught" sagt,
                # aber die Party zwischen Kampfstart und -ende gewachsen ist,
                # ist das Pokemon trotzdem gefangen (Race-Sicherung).
                if outcome == "not_caught" and edition < 40 and party_count_before is not None:
                    count_after = await self._read_party_count(client_id, edition)
                    if count_after is not None and count_after > party_count_before:
                        self.logger.info(
                            f"Party-Count-Diff: {party_count_before}->{count_after} "
                            f"-> caught"
                        )
                        outcome = "caught"

                # Fix 3: aktive PC-Box scannen. Bei voller Party landet ein
                # frisch gefangenes Pokemon direkt in der Box, ohne Party-
                # Count zu erhoehen. Nur Gen 3.
                if outcome == "not_caught" and edition < 40:
                    box_result = await self._scan_active_box_gen3(
                        client_id, edition, int(battle_pv)
                    )
                    if box_result == "caught":
                        outcome = "caught"

            from backend.classes.pokedex_db import PokedexDB
            owner = PokedexDB.build_owner(
                self.munchlax.pl.get('your_name', ''), str(player)
            )

            loop = asyncio.get_event_loop()
            updated = await loop.run_in_executor(
                None, self.encounter_tracker.update_outcome,
                int(battle_pv), owner, outcome
            )
            if updated:
                self.logger.info(
                    f"Encounter-Outcome: PV={battle_pv:#x} → {outcome}"
                )
                asyncio.create_task(
                    self.munchlax.send_encounter_outcome(int(battle_pv), owner, outcome)
                )
        except Exception as err:
            self.logger.error(f"Outcome-Check fehlgeschlagen: {type(err)},{err}")
            self.logger.error(f"{traceback.format_exc()}")

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
        self._team_updated_events.pop(client_id, None)
        self._last_party_count.pop(client_id, None)

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
        