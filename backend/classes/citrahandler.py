import asyncio
import time
import traceback
import yaml
from backend.classes.citra import Citra
from backend.classes.azahar_writer import AzaharWriter
from backend.classes.munchlax import Munchlax
from backend.classes.Pokemon import Pokemon
import backend.pokedecoder as pokedecoder
import backend.bag_decoder as bag_decoder
from backend.logging_setup import get_logger

BLOCK_SIZE = 56
SLOT_OFFSET = 484
SLOT_DATA_SIZE = (8 + (4 * BLOCK_SIZE)) # 232
STAT_DATA_OFFSET = 112
STAT_DATA_SIZE = 22

items6andup = yaml.safe_load(open('backend/data/items.yml'))

class CitraHandler:
    def __init__(self):
        self.citra_instance = Citra()
        self.is_connected = False
        self.player_number = None
        self.started = False
        # Ausstehende Box-Read-Anfragen: (absolute_adresse, Future).
        # Wird vom handle_citra-Tick eine Box pro Iteration abgearbeitet,
        # damit Team-/Badge-Reads dazwischen durchkommen und nicht auf einen
        # vom Worker-Thread okkupierten UDP-Socket treffen.
        self.box_request_queue: list[tuple[int, "asyncio.Future[bytes]"]] = []
        # Analog fuer Bag-Pockets: (absolute_adresse, size, Future). Wird
        # periodisch (alle 5s) vom Tick-Loop befuellt und eine Pocket pro Tick
        # abgearbeitet — gleiche Thread-Safety-Begruendung wie box_request_queue.
        self.bag_request_queue: list[tuple[int, int, "asyncio.Future[bytes]"]] = []
        # Write-Queue fuer Sonderbonbon-Schreibvorgaenge. Eintrag: (addr, bytes,
        # future). Wird wie die Read-Queue im Tick-Loop abgearbeitet, damit der
        # UDP-Socket nie parallel zu read_team genutzt wird.
        self.bag_write_queue: list[tuple[int, bytes, "asyncio.Future[bool]"]] = []
        self._last_bag_refresh: float = 0.0
        self._bag_refresh_inflight: bool = False
        # Serialisiert add_rare_candies-Aufrufe gegen sich selbst — verhindert,
        # dass schnelle Doppelklicks den selben leeren Slot doppelt allokieren.
        self._bag_io_lock: asyncio.Lock = asyncio.Lock()
        # Workaround: Azahars RPC-Whitelist fehlt NEW_LINEAR_HEAP_VADDR
        # (0x30000000+). Fuer Gen 7 schreiben wir direkt in den Host-Prozess.
        self._azahar_writer = AzaharWriter()

        with open("backend/data/pointer_xy.yml") as file:
            pointer_xy = yaml.safe_load(file)
        with open("backend/data/pointer_oras.yml") as file:
            pointer_oras = yaml.safe_load(file)
        with open("backend/data/pointer_sm.yml") as file:
            pointer_sm = yaml.safe_load(file)
        with open("backend/data/pointer_usum.yml") as file:
            pointer_usum = yaml.safe_load(file)

        self.edition_lut = {
            "X": 61,
            "Y": 62,
            "Omega Rubin": 63,
            "Alpha Saphir": 64,
            "Sonne": 71,
            "Mond": 72,
            "Ultra Sonne": 73,
            "Ultra Mond": 74
        }
        self.pointer_lut = {
            61: pointer_xy,
            62: pointer_xy,
            63: pointer_oras,
            64: pointer_oras,
            71: pointer_sm,
            72: pointer_sm,
            73: pointer_usum,
            74: pointer_usum
        }

        self.logger = get_logger(__name__, './logs/citra.log')
    
    def check_connection(self):
        try:
            self.citra_instance.read_memory(0,1)
            self.is_connected = True
        except ConnectionResetError:
            self.is_connected = False

    def _select_game_process(self):
        """Waehlt den Spielprozess in Azahars RPC-Server aus.

        Ohne explizite Prozess-Selektion nutzt Azahar den alten Fallback-Pfad,
        der Writes im LINEAR-Heap (0x30000000+, Gen 7) nicht unterstuetzt.
        """
        try:
            procs = self.citra_instance.process_list()
        except Exception as err:
            self.logger.warning(f"process_list fehlgeschlagen: {type(err)},{err}")
            return
        if not procs:
            self.logger.warning("Keine Prozesse von Azahar erhalten.")
            return
        # Typischerweise laeuft genau ein Spiel. Falls mehrere: den ersten
        # waehlen, der nicht "menu" o.ae. heisst.
        pid = next(iter(procs))
        title_id, name = procs[pid]
        try:
            self.citra_instance.set_process(pid)
            self.logger.info(
                f"Azahar-Prozess gesetzt: PID={pid} title_id=0x{title_id:016X} "
                f"name={name}"
            )
        except Exception as err:
            self.logger.warning(f"set_process({pid}) fehlgeschlagen: {type(err)},{err}")

    async def handle_citra(self):
        while self.started and self.is_connected:
            try:
                new_data = b''
                new_data += self.read_team()
                new_data += self.read_badges()

                update_data = self.read_in_battle_stats()

                self.update_teams(new_data)
                self.update_stats(update_data)

                # Periodischer Bag-Refresh alle 5 s. Der eigentliche Read
                # laeuft als Background-Task, der Pockets in bag_request_queue
                # einreiht — die Pocket-Reads werden hier im Tick abgearbeitet.
                now = time.monotonic()
                if (now - self._last_bag_refresh >= 5.0
                        and not self._bag_refresh_inflight):
                    self._last_bag_refresh = now
                    asyncio.create_task(self._auto_refresh_bag())

                # Pro Tick je EINE Box, EINE Bag-Pocket und EINEN Bag-Write
                # aus der jeweiligen Queue verarbeiten, damit Team-Reads
                # dazwischen durchkommen.
                self._drain_box_queue_step()
                self._drain_bag_queue_step()
                self._drain_bag_write_queue_step()

                # Wenn Boxen, Bag-Pockets oder Bag-Writes ausstehen: schneller
                # pollen, sonst normaler 1s-Tick.
                have_pending = bool(self.box_request_queue
                                    or self.bag_request_queue
                                    or self.bag_write_queue)
                await asyncio.sleep(0.05 if have_pending else 1)
            except ConnectionResetError:
                self.is_connected = False
            except Exception as err:
                self.logger.error(f"handle_citra abgebrochen: {type(err)},{err}")
                self.logger.error(f"{traceback.format_exc()}")
                self.is_connected = False

        self._fail_pending_box_requests("Citra-Verbindung verloren")

        self.logger.info("Citra getrennt.")

        if self.started:
            reconnected = await self._auto_reconnect()
            if not reconnected:
                self.start_button.trigger_action(0)
                self.player_number = None
        else:
            self.player_number = None

    def _drain_box_queue_step(self):
        """Verarbeitet höchstens EINE ausstehende Box-Anfrage.

        Wir bleiben im Event-Loop-Thread, damit der UDP-Socket nie parallel
        zum synchronen read_team/read_badges/read_in_battle_stats benutzt wird.
        Bereits aufgelöste (z.B. gecancelte) Futures werden übersprungen und
        zählen nicht als abgearbeitete Box.
        """
        while self.box_request_queue:
            addr, fut = self.box_request_queue.pop(0)
            if fut.done():
                continue
            try:
                box_bytes = self.citra_instance.read_memory(addr, 30 * SLOT_DATA_SIZE)
                if box_bytes is None:
                    raise RuntimeError(f"Citra-Read für 0x{addr:08X} gab None zurück")
                fut.set_result(box_bytes)
            except Exception as err:
                if not fut.done():
                    fut.set_exception(err)
            return

    def _fail_pending_box_requests(self, reason: str):
        for _, fut in self.box_request_queue:
            if not fut.done():
                fut.set_exception(RuntimeError(reason))
        self.box_request_queue.clear()
        for _, _, fut in self.bag_request_queue:
            if not fut.done():
                fut.set_exception(RuntimeError(reason))
        self.bag_request_queue.clear()
        for _, _, fut in self.bag_write_queue:
            if not fut.done():
                fut.set_exception(RuntimeError(reason))
        self.bag_write_queue.clear()

    def _drain_bag_queue_step(self):
        """Verarbeitet hoechstens EINE ausstehende Bag-Pocket-Anfrage.

        Wie _drain_box_queue_step im Event-Loop-Thread, damit der UDP-Socket
        nie parallel zu read_team/read_badges/read_in_battle_stats benutzt wird.
        """
        while self.bag_request_queue:
            addr, size, fut = self.bag_request_queue.pop(0)
            if fut.done():
                continue
            try:
                pocket_bytes = self.citra_instance.read_memory(addr, size)
                if pocket_bytes is None:
                    raise RuntimeError(f"Citra-Read fuer Bag 0x{addr:08X} gab None zurueck")
                fut.set_result(pocket_bytes)
            except Exception as err:
                if not fut.done():
                    fut.set_exception(err)
            return

    def _drain_bag_write_queue_step(self):
        """Verarbeitet hoechstens EINEN ausstehenden Bag-Write.

        Selbe Thread-Safety-Begruendung wie die Read-Drain-Steps. write_memory
        liefert True/False; das Future bekommt diesen Bool.
        """
        while self.bag_write_queue:
            addr, data, fut = self.bag_write_queue.pop(0)
            if fut.done():
                continue
            try:
                ok = self.citra_instance.write_memory(addr, data)
                fut.set_result(bool(ok))
            except Exception as err:
                if not fut.done():
                    fut.set_exception(err)
            return

    def update_teams(self, team):
        team: list[Pokemon] = pokedecoder.team(team, self.edition)
        teams = self.munchlax.bizhawk_teams
        # Vor dem Update den alten Dex-Stand merken — bei Tupel-Änderung
        # triggern wir später einen gedrosselten Auto-Box-Refresh.
        old_team = teams.get(self.player_number)
        # team-Liste enthält am Ende badges/edition-Ints — nur Slot 0..5 sind Pokemon.
        old_dexnrs = tuple(p.dexnr for p in old_team[:6]) if old_team else None
        if self.player_number in teams:
            if teams[self.player_number] == team:
                return
        else:
            teams[self.player_number] = team
            self.munchlax.unsorted_teams[self.player_number] = team
        for index, pokemon in enumerate(team):
            if index < 6 and self.player_number in teams:
                if pokemon.checksum_given < 0 or pokemon.checksum_given != pokemon.checksum_calculated:
                    continue
                else:
                    teams[self.player_number][index] = team[index]
                    self.munchlax.unsorted_teams[self.player_number][index] = team[index]
            else:
                teams[self.player_number][index] = team[index]
                self.munchlax.unsorted_teams[self.player_number][index] = team[index]
        new_dexnrs = tuple(p.dexnr for p in team[:6])
        if old_dexnrs != new_dexnrs and self.munchlax.should_auto_refresh_boxes(self.player_number):
            self.munchlax.mark_box_refresh(self.player_number)
            asyncio.create_task(self._auto_refresh_boxes())

    def update_stats(self, stats: dict):
        if stats:
            team = self.munchlax.bizhawk_teams[self.player_number]
            for slot, statlist in stats.items():
                for key, value in statlist.items():
                    old_value_dict = team[slot].__dict__
                    old_value_dict[key] = value if old_value_dict[key] != value else old_value_dict[key]
            self.munchlax.unsorted_teams[self.player_number] = team

    def read_team(self):
        result = b''
        # Teamreihenfolge auslesen:
        team_pointer = self.citra_instance.read_memory(self.pointer["team_reihenfolge"], 25)
        self.number_of_team_pokemon = int.from_bytes(team_pointer[24:],'little')
        for i in range(6):
            read_address = int.from_bytes(team_pointer[i*4:i*4 + 4], 'little') + 0x40
            pokemon = self.citra_instance.read_memory(read_address, SLOT_DATA_SIZE)
            battle_stats = self.citra_instance.read_memory(read_address + SLOT_DATA_SIZE + STAT_DATA_OFFSET, STAT_DATA_SIZE)
            result += pokemon + battle_stats
        return result
    
    def read_in_battle_stats(self):
        result = {}
        battle_kind = int.from_bytes(self.citra_instance.read_memory(self.pointer["battle_kind"], 2), 'little')
        if battle_kind == self.pointer["trainer_value"]:
            read_address = self.pointer["kampf_trainer"]
        elif battle_kind == self.pointer["wild_value"]:
            read_address = self.pointer["kampf_wild"]
        else:
            read_address = None

        items = items6andup

        if read_address:
            offset = self.pointer["in_battle_stat_offset"]
            for index in range(self.number_of_team_pokemon):
                stats_dict = {}
                stat_bytes = self.citra_instance.read_memory(read_address + index * offset, 20)
                stats_dict['lvl'] = int.from_bytes(stat_bytes[16:17], 'little')
                stats_dict['max_hp'] = int.from_bytes(stat_bytes[6:8], 'little')
                stats_dict['cur_hp'] = int.from_bytes(stat_bytes[8:10], 'little')
                item = int.from_bytes(stat_bytes[10:12], 'little')
                if item in items:
                    stats_dict['item'] = items[item]
                else:
                    stats_dict['item'] = '-'
                result[index] = stats_dict

        return result
    
    def read_badges(self):
        result = b'\x00'
        read_address = self.pointer["badges"]
        if read_address:
            result = self.citra_instance.read_memory(read_address, 1)
        return result

    async def read_and_decode_boxes(self) -> list[list]:
        """Reiht alle Box-Adressen als Futures in die CitraHandler-Queue ein
        und wartet auf deren Auflösung durch den handle_citra-Tick.

        Warum nicht direkt lesen oder via to_thread? Der UDP-Socket in
        ``Citra`` ist nicht thread-safe — wenn read_team (Event-Loop-Thread)
        und ein Worker-Thread gleichzeitig sendto/recv machen, werden
        Responses vertauscht und das Team wird als leer dekodiert. Stattdessen
        landen hier alle Box-Adressen in der Queue, und der tick-loop
        arbeitet sie eine pro Iteration ab — dadurch läuft zwischen jeder
        Box ein vollständiger read_team/read_badges-Zyklus und das Team
        bleibt durchgängig befüllt.
        """
        if not self.started or not self.is_connected:
            raise RuntimeError("Citra ist nicht verbunden")

        box_start = self.pointer["box_beginning"]
        box_count = self.pointer.get("box_count", 31)
        box_stride = self.pointer.get("box_stride", 30 * SLOT_DATA_SIZE)

        loop = asyncio.get_running_loop()
        futures: list[asyncio.Future] = []
        for i in range(box_count):
            fut = loop.create_future()
            self.box_request_queue.append((box_start + i * box_stride, fut))
            futures.append(fut)

        raw_boxes = []
        for fut in futures:
            raw_boxes.append(await fut)
        return [pokedecoder.decode_box(b, self.edition) for b in raw_boxes]

    async def _auto_refresh_boxes(self):
        """Box-Read als Reaktion auf Team-Änderung. Cache + Push via Munchlax."""
        try:
            boxes = await self.read_and_decode_boxes()
            await self.munchlax.update_boxes(self.player_number, boxes)
            self.logger.info(f"Auto-Box-Refresh player={self.player_number}: {len(boxes)} Boxen aktualisiert.")
        except Exception as err:
            self.logger.warning(f"Auto-Box-Refresh player={self.player_number} fehlgeschlagen: {type(err)},{err}")
            self.logger.warning(f"{traceback.format_exc()}")

    async def read_bag_pockets(self) -> dict[str, bytes]:
        """Reiht alle definierten bag_*-Pockets der aktuellen Edition als
        Futures in die bag_request_queue ein und liefert {pocket_key: raw_bytes}.

        Gen 6/7 brauchen keine Indirect-Aufloesung — alle bag_*-Adressen im
        pointer_xy/oras/sm/usum.yml sind direkte RAM-Pointer.
        """
        if not self.started or not self.is_connected:
            raise RuntimeError("Citra ist nicht verbunden")

        loop = asyncio.get_running_loop()
        futures: dict[str, asyncio.Future] = {}
        for key in bag_decoder.KNOWN_POCKET_KEYS:
            addr = self.pointer.get(f"bag_{key}")
            if not isinstance(addr, int):
                continue
            start, size = bag_decoder.pocket_window(addr, self.edition, key)
            fut = loop.create_future()
            self.bag_request_queue.append((start, size, fut))
            futures[key] = fut

        results: dict[str, bytes] = {}
        for key, fut in futures.items():
            try:
                results[key] = await fut
            except Exception as err:
                self.logger.warning(
                    f"Bag-Read fehlgeschlagen edition={self.edition} pocket={key}: "
                    f"{type(err)},{err}"
                )
        return results

    async def read_and_decode_bag(self) -> dict[str, list]:
        """Liest und dekodiert alle Bag-Pockets. {pocket_key: list[BagItem]}."""
        raw_pockets = await self.read_bag_pockets()
        if not raw_pockets:
            return {}
        decoded: dict[str, list] = {}
        for pocket_key, raw_bytes in raw_pockets.items():
            try:
                decoded[pocket_key] = bag_decoder.decode_pocket(
                    self.edition, pocket_key, raw_bytes,
                )
            except Exception as err:
                self.logger.warning(
                    f"decode_pocket fehlgeschlagen edition={self.edition} "
                    f"pocket={pocket_key}: {type(err)},{err}"
                )
        return decoded

    async def add_rare_candies(self, count: int) -> tuple[bool, str]:
        """Pendant zu Bizhawk.add_rare_candies fuer Gen 6/7 ueber Citra UDP.

        Read-Modify-Write:
          1. Pocket frisch lesen (geht durch bag_request_queue, damit der
             UDP-Socket nicht mit read_team kollidiert).
          2. plan_bag_write() berechnet die Bytes — Gen 6 = u16+u16, Gen 7 =
             packed10 (qty wird in-place ersetzt, freespace+flags bleiben).
          3. Schreiben ueber bag_write_queue, damit auch der Write im
             Event-Loop-Thread sequentialisiert wird.
        """
        if not self.started or not self.is_connected:
            return False, "Citra ist nicht verbunden."
        if count <= 0:
            return False, "Anzahl muss > 0 sein."

        edition = self.edition
        gen = bag_decoder.edition_to_gen(edition)
        pocket_key = bag_decoder.RARE_CANDY_POCKET.get(gen)
        if pocket_key is None:
            return False, f"Sonderbonbon-Pocket fuer Gen {gen} nicht definiert."
        item_id = bag_decoder.find_rare_candy_id(edition)
        if item_id is None:
            return False, f"Keine Sonderbonbon-ID fuer Edition {edition}."

        async with self._bag_io_lock:
            try:
                raw_pockets = await self.read_bag_pockets()
            except Exception as err:
                self.logger.error(
                    f"add_rare_candies: read_bag_pockets fehlgeschlagen: "
                    f"{type(err)},{err}"
                )
                return False, "Lesen der Tasche fehlgeschlagen."
            if not raw_pockets:
                return False, "Tasche nicht erreichbar — Save geladen?"
            pocket_bytes = raw_pockets.get(pocket_key)
            if pocket_bytes is None:
                return False, f"Pocket '{pocket_key}' fuer Edition {edition} nicht gelesen."

            try:
                plan = bag_decoder.plan_bag_write(
                    edition, pocket_key, pocket_bytes, item_id, count,
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

            yaml_addr = self.pointer.get(f"bag_{pocket_key}")
            if not isinstance(yaml_addr, int):
                return False, f"bag_{pocket_key} fehlt in Pointer-YAML."
            base, _ = bag_decoder.pocket_window(yaml_addr, edition, pocket_key)

            if base >= 0x30000000:
                ok, msg = self._write_via_host(base, pocket_bytes, plan)
                if not ok:
                    return False, msg
                return True, f"+{count} Sonderbonbon{'s' if count > 1 else ''} (jetzt {plan.new_qty})"

            loop = asyncio.get_running_loop()
            futures: list[asyncio.Future] = []
            for offset, data in plan.writes:
                fut = loop.create_future()
                self.bag_write_queue.append((base + offset, data, fut))
                futures.append(fut)
            results = await asyncio.gather(*futures, return_exceptions=True)
            if not all(r is True for r in results):
                self.logger.warning(
                    f"add_rare_candies: write_memory teilweise fehlgeschlagen: {results}"
                )
                return False, "Schreiben in den Memory fehlgeschlagen."

            return True, f"+{count} Sonderbonbon{'s' if count > 1 else ''} (jetzt {plan.new_qty})"

    def _write_via_host(
        self,
        base: int,
        pocket_bytes: bytes,
        plan: bag_decoder.BagWritePlan,
    ) -> tuple[bool, str]:
        """Schreibt plan.writes via WriteProcessMemory in Azahars FCRAM.

        Workaround fuer fehlende NEW_LINEAR_HEAP_VADDR-Whitelist in Azahars
        RPC-Server. Kalibriert beim ersten Aufruf anhand der soeben gelesenen
        pocket_bytes.
        """
        w = self._azahar_writer
        if not w.is_calibrated:
            if not w.calibrate(base, pocket_bytes[:16]):
                return False, (
                    "Host-Write konnte nicht kalibriert werden "
                    "(Azahar-Prozess nicht gefunden oder FCRAM-Muster fehlt)."
                )
        for offset, data in plan.writes:
            if not w.write_memory(base + offset, data):
                return False, "Host-Write fehlgeschlagen."
        return True, ""

    async def _auto_refresh_bag(self):
        """Periodischer Bag-Read; persistiert Pockets in PokedexDB via Munchlax."""
        if self._bag_refresh_inflight:
            return
        self._bag_refresh_inflight = True
        try:
            if not self.started or not self.is_connected:
                return
            pockets = await self.read_and_decode_bag()
            if not pockets:
                return
            await self.munchlax.update_bag(self.player_number, self.edition, pockets)
            self.logger.debug(
                f"Auto-Bag-Refresh player={self.player_number}: "
                f"{', '.join(f'{k}={len(v)}' for k, v in pockets.items())}"
            )
        except Exception as err:
            self.logger.warning(
                f"Auto-Bag-Refresh player={self.player_number} fehlgeschlagen: "
                f"{type(err)},{err}"
            )
            self.logger.warning(f"{traceback.format_exc()}")
        finally:
            self._bag_refresh_inflight = False

    async def start(self, munchlax, button):
        self.munchlax: Munchlax = munchlax
        self.logger.info("Citra verbunden.")
        self.started = True

        self.start_button = button

        self._select_game_process()
        self.set_pointer()
        self.set_player_number()
        if not self.player_number:
            self.is_connected = False
        asyncio.create_task(self.handle_citra())

    def set_pointer(self):
        self.edition = self.edition_lut.get(self.munchlax.pl["session_game"], 0)

        self.pointer = self.pointer_lut[self.edition]

    def set_player_number(self):
        for i in range(1, self.munchlax.pl['player_count'] + 1):
            if not self.munchlax.pl[f"remote_{i}"]:
                self.player_number = i
        if self.player_number:
            self.munchlax.editions[self.player_number] = self.edition
            name = self.munchlax.pl.get('your_name', '')
            if name:
                self.munchlax.player_names[self.player_number] = name

    async def _auto_reconnect(self) -> bool:
        delays = [10, 20, 40]
        for attempt, delay in enumerate(delays, 1):
            self.logger.info(f"Citra Auto-Reconnect Versuch {attempt}/{len(delays)} in {delay}s...")
            await asyncio.sleep(delay)
            if not self.started:
                return False
            self.check_connection()
            if self.is_connected:
                self.logger.info(f"Citra Auto-Reconnect erfolgreich nach Versuch {attempt}.")
                self._select_game_process()
                asyncio.create_task(self.handle_citra())
                return True
        self.logger.error("Citra Auto-Reconnect aufgegeben nach 3 Versuchen.")
        return False

    async def stop(self):
        self.started = False
        self.is_connected = False
        self._azahar_writer.close()
        
if __name__ == "__main__":
    pass