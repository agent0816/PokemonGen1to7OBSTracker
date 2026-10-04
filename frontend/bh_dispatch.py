"""Helper fuer cross-thread BizHawk-Aufrufe aus Kivy-Widgets heraus.

Hintergrund: der BizHawk-TCP-Server + handle_bizhawk-Loops laufen seit der
Thread-Isolation auf einem eigenen asyncio-Loop in einem separaten OS-Thread
(siehe backend/bizhawk_thread.py). Widgets im Kivy-Loop koennen Bizhawk-Coros
nicht mehr via `asyncio.create_task(self.bizhawk.xxx(...))` triggern — die
Coro landet sonst auf dem Kivy-Loop und faellt mit den gewrappten Lua-Reads
auf den falschen Executor zurueck.

Diese Helfer greifen auf das BizhawkThread-Objekt am App-Singleton zu und
posten die Coro per `run_coroutine_threadsafe` auf den BH-Loop.

Fallback (Testkontext, oder wenn App ohne bizhawk_thread konstruiert wird):
direkter asyncio-Aufruf auf dem aktuellen Loop. Semantisch identisch wie
vor der Thread-Isolation.
"""
import asyncio
import logging

from kivy.app import App

_logger = logging.getLogger('frontend.bh_dispatch')


def bh_submit(coro) -> asyncio.Future:
    """Postet eine BizHawk-Coroutine auf den BH-Loop.

    Rueckgabe: asyncio.Future, die der Caller mit `await` abwarten kann.
    Fehler aus der Coro propagieren via `fut.result()` wie bei einem
    regulaeren Task.
    """
    thread = getattr(App.get_running_app(), 'bizhawk_thread', None)
    if thread is None:
        # Fallback-Pfad laeuft im Kivy-Loop (Testkontext oder App ohne
        # bizhawk_thread). create_task statt ensure_future — ensure_future
        # ist seit 3.11 deprecated, create_task hat dieselbe Semantik auf
        # einem running loop.
        return asyncio.create_task(coro)
    return asyncio.wrap_future(thread.submit_coro(coro))


def bh_dispatch(coro, name: str = "<unnamed>") -> None:
    """Fire-and-forget-Dispatch einer BizHawk-Coroutine auf den BH-Loop.

    Ersatz fuer `asyncio.create_task(bizhawk.xxx(...))` an Call-Sites ohne
    Rueckkanal. Exceptions werden ueber `submit_coro_logged` via
    done_callback geloggt, nicht silent gedropt. `name` erscheint im Log.
    """
    thread = getattr(App.get_running_app(), 'bizhawk_thread', None)
    if thread is None:
        task = asyncio.create_task(coro)

        def _log(t):
            # CancelledError ist ab Python 3.8 BaseException-Subklasse,
            # `except Exception` wuerde ihn verfehlen und als "coro never
            # awaited" durchschlagen. Shutdown-Cancel ist erwartet → INFO.
            if t.cancelled():
                _logger.info(f"bh_dispatch({name}) wurde gecancelt.")
                return
            try:
                exc = t.exception()
            except asyncio.CancelledError:
                _logger.info(
                    f"bh_dispatch({name}) wurde gecancelt (exception())."
                )
                return
            except Exception:
                exc = None
            if exc is not None:
                _logger.error(
                    f"bh_dispatch({name}) failed: {type(exc).__name__}: {exc}"
                )
        task.add_done_callback(_log)
        return
    thread.submit_coro_logged(coro, name=name)
