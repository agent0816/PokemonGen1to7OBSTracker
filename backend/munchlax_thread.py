from backend.isolated_thread import IsolatedAsyncioThread


class MunchlaxThread(IsolatedAsyncioThread):
    """Dedizierter OS-Thread + asyncio-Loop fuer den Munchlax-Client.

    Isoliert den Client-Receive-Loop, send_heartbeat, send_teams,
    alter_teams und _auto_reconnect vom Kivy-asyncio-Loop. Dadurch
    verzoegern Kivy-GUI-Blockaden (Fenster-Move, Popup, langsame Click-
    Handler) weder Heartbeat-Sends noch Team-Broadcasts; die Verbindung
    zum Arceus-Server bleibt stabil, auch wenn die GUI haengt.

    Generische Thread-/Loop-/Submit-Logik liegt in
    `backend/isolated_thread.py:IsolatedAsyncioThread`. Diese Subklasse
    konfiguriert lediglich Name, Logfile und Monitor-Tag.
    """

    def __init__(self):
        super().__init__(
            name="MunchlaxLoop",
            log_path='logs/munchlax_thread.log',
            monitor_tag="mun",
            logger_name=__name__,
        )
