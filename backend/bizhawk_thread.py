import asyncio
import concurrent.futures
import threading
import traceback

from backend.logging_setup import get_logger


class BizhawkThread:
    """Hält einen dedizierten OS-Thread mit eigenem asyncio-Event-Loop.

    Dient dazu, den BizHawk-TCP-Server + Tick-Handler vom Kivy-asyncio-Loop
    zu isolieren. Kivy-GUI-Blockaden (Fenster-Move, langsame Click-Handler)
    sollen Lua `comm.socketServerResponse()` nicht mehr blockieren.

    Nutzung:
        bh_thread = BizhawkThread()
        bh_thread.start()
        fut = bh_thread.submit_coro(some_coro())
        result = await asyncio.wrap_future(fut)   # vom Kivy-Loop
        # oder fire-and-forget:
        bh_thread.submit_coro(some_coro())
        # Shutdown:
        await bh_thread.shutdown()
    """

    def __init__(self):
        self.logger = get_logger(__name__, 'logs/bizhawk_thread.log')
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()

    @property
    def loop(self) -> asyncio.AbstractEventLoop | None:
        return self._loop

    def start(self, timeout: float = 10.0) -> None:
        if self._thread is not None:
            self.logger.warning("BizhawkThread.start() mehrfach aufgerufen — ignoriert.")
            return
        self._thread = threading.Thread(
            target=self._run, name="BizhawkLoop", daemon=True
        )
        self._thread.start()
        if not self._ready.wait(timeout=timeout):
            raise RuntimeError(f"BizhawkThread konnte in {timeout}s nicht starten.")
        self.logger.info("BizhawkThread gestartet.")

    def _run(self) -> None:
        try:
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
            # Zweiter Event-Loop-Monitor fuer den BH-Loop. Schreibt nach
            # logs/event_loop_monitor_bh.log und ermoeglicht den Isolation-
            # Beweis: Kivy-Loop darf drifts bei User-GUI-Operationen zeigen,
            # BH-Loop muss drift-frei bleiben. start_event_loop_monitor
            # legt den Task via create_task auf dem aktuellen Loop an.
            try:
                from backend.event_loop_monitor import start_event_loop_monitor
                self._loop.call_soon(start_event_loop_monitor, "bh")
            except Exception as err:
                self.logger.warning(
                    f"BH-Event-Loop-Monitor konnte nicht gestartet werden: {err}"
                )
            self._ready.set()
            self._loop.run_forever()
        except Exception as err:
            self.logger.error(f"BizhawkThread crash: {err}\n{traceback.format_exc()}")
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
                self.logger.error(f"BizhawkThread cleanup error: {err}")

    def submit_coro(self, coro) -> concurrent.futures.Future:
        """Posted eine Coroutine auf den BH-Loop. Thread-safe.

        Schliesst die uebergebene Coro bei Fehlpfaden (Loop nie gestartet
        oder Shutdown-Race → RuntimeError aus `call_soon_threadsafe`),
        damit kein "coroutine was never awaited"-RuntimeWarning entsteht.
        Caller erhaelt die Exception trotzdem — close() ist Cleanup, kein
        swallow.
        """
        if self._loop is None:
            try:
                coro.close()
            except Exception:
                pass
            raise RuntimeError("BizhawkThread wurde nicht gestartet.")
        try:
            return asyncio.run_coroutine_threadsafe(coro, self._loop)
        except RuntimeError:
            try:
                coro.close()
            except Exception:
                pass
            raise

    async def shutdown(self, timeout: float = 5.0) -> None:
        """Stoppt den BH-Loop und joint den Thread.

        Thread.join() blockiert den aufrufenden Loop und kann im Shutdown-
        Pfad einen Deadlock produzieren, falls BH-Cleanup-Pfade via
        _call_on_kivy auf den Kivy-Loop warten (wir sind der Kivy-Loop).
        Join deshalb in Executor auslagern.
        """
        if self._loop is None or self._thread is None:
            return
        try:
            self._loop.call_soon_threadsafe(self._loop.stop)
        except Exception as err:
            self.logger.error(f"BizhawkThread shutdown-stop error: {err}")
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._thread.join, timeout)
        if self._thread.is_alive():
            self.logger.warning(f"BizhawkThread Join-Timeout nach {timeout}s.")
        else:
            self.logger.info("BizhawkThread sauber gestoppt.")

    def submit_coro_logged(self, coro, name: str = "<unnamed>"):
        """submit_coro + add_done_callback mit Error-Log.

        Fuer Fire-and-forget-Pfade (set_port, stop(), stop_and_terminate(),
        start()), bei denen keiner das Future awaitet. Ohne Done-Callback
        verschwinden Exceptions geraeuschlos in concurrent.futures.Future.

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
