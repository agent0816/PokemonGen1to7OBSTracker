from backend.isolated_thread import IsolatedAsyncioThread


class ArceusThread(IsolatedAsyncioThread):
    """Dedizierter OS-Thread + asyncio-Loop fuer den Arceus-Server.

    Isoliert den TCP-Server und seine Watchdog-Loops (check_heartbeats,
    timer_tick_loop, handle_munchlax-Accept) vom Kivy-asyncio-Loop. Dadurch
    verzoegern Kivy-GUI-Blockaden (Fenster-Move, Popup, langsame Click-
    Handler) weder den Heartbeat-Check noch den Timer-Broadcast; verbundene
    Clients bleiben verbunden, auch wenn die GUI haengt.

    Generische Thread-/Loop-/Submit-Logik liegt in
    `backend/isolated_thread.py:IsolatedAsyncioThread`. Diese Subklasse
    konfiguriert lediglich Name, Logfile und Monitor-Tag.
    """

    def __init__(self):
        super().__init__(
            name="ArceusLoop",
            log_path='logs/arceus_thread.log',
            monitor_tag="arc",
            logger_name=__name__,
        )
