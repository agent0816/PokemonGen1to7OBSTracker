from backend.isolated_thread import IsolatedAsyncioThread


class BizhawkThread(IsolatedAsyncioThread):
    """Dedizierter OS-Thread + asyncio-Loop fuer den BizHawk-TCP-Server.

    Isoliert den BH-TCP-Server + Tick-Handler vom Kivy-asyncio-Loop, damit
    Kivy-GUI-Blockaden (Fenster-Move, langsame Click-Handler) Lua
    `comm.socketServerResponse()` nicht mehr blockieren.

    Generische Thread-/Loop-/Submit-Logik liegt in
    `backend/isolated_thread.py:IsolatedAsyncioThread`. Diese Subklasse
    konfiguriert lediglich Name, Logfile und Monitor-Tag.
    """

    def __init__(self):
        super().__init__(
            name="BizhawkLoop",
            log_path='logs/bizhawk_thread.log',
            monitor_tag="bh",
            logger_name=__name__,
        )
