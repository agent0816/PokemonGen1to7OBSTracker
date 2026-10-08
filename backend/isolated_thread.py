import asyncio
import concurrent.futures
import threading
import traceback

from backend.logging_setup import get_logger


class IsolatedAsyncioThread:
    """Haelt einen dedizierten OS-Thread mit eigenem asyncio-Event-Loop.

    Generische Basis fuer Komponenten-Isolation: die Kivy-GUI blockiert ihren
    eigenen asyncio-Loop regelmaessig (Fenster-Move, Resize, Modal, langsame
    Click-Handler). Zeitkritische Netz- und Emulator-Pfade werden durch einen
    eigenen Loop in einem separaten OS-Thread von diesen Blockaden entkoppelt.

    Subklassen setzen Name, Logfile und Monitor-Tag ueber den Konstruktor:

        class BizhawkThread(IsolatedAsyncioThread):
            def __init__(self):
                super().__init__(name="BizhawkLoop",
                                 log_path='logs/bizhawk_thread.log',
                                 monitor_tag="bh")

    Nutzung:
        t = BizhawkThread()
        t.start()
        fut = t.submit_coro(some_coro())
        result = await asyncio.wrap_future(fut)   # vom Kivy-Loop
        # oder fire-and-forget mit Fehler-Log:
        t.submit_coro_logged(some_coro(), name='some_coro')
        # Shutdown:
        await t.shutdown()
    """

    def __init__(self, name: str, log_path: str, monitor_tag: str,
                 logger_name: str | None = None):
        self._name = name
        self._monitor_tag = monitor_tag
        self.logger = get_logger(logger_name or __name__, log_path)
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        # Shutdown-Window-Guard (R2 WARN): zwischen loop.stop() und
        # loop.close() laeuft in _run() noch `run_until_complete(gather(
        # pending))`. Submits waehrend dieser Phase wuerden Coros starten,
        # die bei close() als "Task was destroyed"-RuntimeWarning enden —
        # Caller der submit_coro() haengen auf einem Future, das nie
        # auflost. _closing wird in shutdown() VOR loop.stop() gesetzt,
        # submit_coro schmeisst dann RuntimeError und schliesst die Coro.
        self._closing = False
        # Submit-Lock (R3 WARN): _closing-Check und
        # run_coroutine_threadsafe muessen atomar sein, sonst kann
        # shutdown() dazwischenfunken und ein neuer Task landet nach
        # dem `pending`-Snapshot auf dem schon stoppenden Loop.
        self._submit_lock = threading.Lock()

    @property
    def loop(self) -> asyncio.AbstractEventLoop | None:
        return self._loop

    @property
    def is_closing(self) -> bool:
        """True wenn `shutdown()` eingeleitet wurde. Dispatch-Pfade (z.B.
        bizhawk._dispatch_on_munchlax) sollen auf dieser Flag pruefen,
        bevor sie `run_coroutine_threadsafe` direkt nutzen — der
        is_closed()-Check reicht nicht, da zwischen loop.stop() und
        loop.close() noch Pending-Tasks im Thread-finally durchlaufen
        und neue Submits in dieser Phase gelost werden koennten, aber
        beim close() als 'Task was destroyed' zerstoert werden.
        """
        return self._closing

    def start(self, timeout: float = 10.0) -> None:
        if self._thread is not None:
            self.logger.warning(
                f"{type(self).__name__}.start() mehrfach aufgerufen — ignoriert."
            )
            return
        self._thread = threading.Thread(
            target=self._run, name=self._name, daemon=True
        )
        self._thread.start()
        if not self._ready.wait(timeout=timeout):
            raise RuntimeError(
                f"{type(self).__name__} konnte in {timeout}s nicht starten."
            )
        self.logger.info(f"{type(self).__name__} gestartet.")

    def _run(self) -> None:
        try:
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
            # Eigener Event-Loop-Monitor pro Tag. Schreibt nach
            # logs/event_loop_monitor_<tag>.log und ermoeglicht den Isolation-
            # Beweis: Kivy-Loop darf Drifts bei User-GUI-Operationen zeigen,
            # dieser Loop muss drift-frei bleiben. start_event_loop_monitor
            # legt den Task via create_task auf dem aktuellen Loop an.
            try:
                from backend.event_loop_monitor import start_event_loop_monitor
                self._loop.call_soon(start_event_loop_monitor, self._monitor_tag)
            except Exception as err:
                self.logger.warning(
                    f"[{self._monitor_tag}] Event-Loop-Monitor konnte nicht "
                    f"gestartet werden: {err}"
                )
            self._ready.set()
            self._loop.run_forever()
        except Exception as err:
            self.logger.error(
                f"{type(self).__name__} crash: {err}\n{traceback.format_exc()}"
            )
        finally:
            try:
                if self._loop is not None:
                    pending = asyncio.all_tasks(self._loop)
                    for t in pending:
                        t.cancel()
                    if pending:
                        self._loop.run_until_complete(
                            asyncio.gather(*pending, return_exceptions=True)
                        )
                    self._loop.close()
            except Exception as err:
                self.logger.error(f"{type(self).__name__} cleanup error: {err}")

    def submit_coro(self, coro) -> concurrent.futures.Future:
        """Posted eine Coroutine auf den isolierten Loop. Thread-safe.

        Schliesst die uebergebene Coro bei Fehlpfaden (Loop nie gestartet,
        schon geschlossen, Shutdown bereits eingeleitet oder RuntimeError
        aus `call_soon_threadsafe`), damit kein "coroutine was never
        awaited"-RuntimeWarning entsteht. Caller erhaelt die Exception
        trotzdem — close() ist Cleanup, kein swallow.

        Shutdown-Fenster: Zwischen `shutdown()`-Entry und `loop.close()`
        liegt die Zeit fuer `run_until_complete(gather(pending))` im
        Thread-finally — bei Netz-Clean-up (`writer.wait_closed` bis 2s)
        laeuft das messbar lang. Submits in diesem Fenster wuerden Coros
        starten, deren Tasks aber beim nachfolgenden close() destroyed
        werden. `shutdown()` setzt `_closing` VOR `loop.stop()`, dieser
        Pfad raised sofort RuntimeError.
        """
        if self._loop is None:
            try:
                coro.close()
            except Exception:
                pass
            raise RuntimeError(f"{type(self).__name__} wurde nicht gestartet.")
        # Check + Submit unter _submit_lock atomar — ohne das wuerde
        # shutdown() zwischen Check und run_coroutine_threadsafe
        # dazwischenfunken, Coro landet nach `pending`-Snapshot und wird
        # beim close() destroyed → "Task was destroyed", Future haengt.
        with self._submit_lock:
            if self._closing or self._loop.is_closed():
                try:
                    coro.close()
                except Exception:
                    pass
                raise RuntimeError(
                    f"{type(self).__name__} wird gerade heruntergefahren."
                )
            try:
                return asyncio.run_coroutine_threadsafe(coro, self._loop)
            except RuntimeError:
                try:
                    coro.close()
                except Exception:
                    pass
                raise

    async def shutdown(self, timeout: float = 5.0) -> None:
        """Stoppt den Loop und joint den Thread.

        Thread.join() blockiert den aufrufenden Loop und kann im Shutdown-
        Pfad einen Deadlock produzieren, falls Cleanup-Pfade via
        _call_on_munchlax auf den Kivy-Loop warten (wir sind der Kivy-Loop).
        Join deshalb in Executor auslagern.
        """
        if self._loop is None or self._thread is None:
            return
        # VOR loop.stop() unter _submit_lock setzen, damit konkurrierende
        # submit_coro-Calls atomar das Shutdown-Fenster erkennen (siehe
        # submit_coro). Ein in Flight befindlicher Submit-Aufruf wartet
        # auf den Lock und sieht dann _closing=True.
        with self._submit_lock:
            self._closing = True
        try:
            self._loop.call_soon_threadsafe(self._loop.stop)
        except Exception as err:
            self.logger.error(f"{type(self).__name__} shutdown-stop error: {err}")
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._thread.join, timeout)
        if self._thread.is_alive():
            self.logger.warning(
                f"{type(self).__name__} Join-Timeout nach {timeout}s."
            )
        else:
            self.logger.info(f"{type(self).__name__} sauber gestoppt.")

    def submit_coro_logged(self, coro, name: str = "<unnamed>"):
        """submit_coro + add_done_callback mit Error-Log.

        Fuer Fire-and-forget-Pfade, bei denen keiner das Future awaitet. Ohne
        Done-Callback verschwinden Exceptions geraeuschlos in
        concurrent.futures.Future.

        CancelledError wird separat behandelt: ab Python 3.8 ist er eine
        BaseException-Subklasse, `except Exception` wuerde ihn verfehlen
        und als "coro never awaited"-Warnung durchschlagen. Shutdown-Cancel
        ist erwartet, deshalb nur INFO-Log.
        """
        fut = self.submit_coro(coro)

        def _cb(f):
            if f.cancelled():
                self.logger.info(
                    f"submit_coro_logged({name}) wurde gecancelt."
                )
                return
            try:
                exc = f.exception()
            except concurrent.futures.CancelledError:
                self.logger.info(
                    f"submit_coro_logged({name}) wurde gecancelt (exception())."
                )
                return
            except Exception:
                exc = None
            if exc is not None:
                self.logger.error(
                    f"submit_coro_logged({name}) coro failed: "
                    f"{type(exc).__name__}: {exc}"
                )
                tb = ''.join(traceback.format_exception(exc))
                self.logger.error(tb)

        fut.add_done_callback(_cb)
        return fut
