"""PC-Boxen eines Spielers aus dem zuständigen Emulator lesen.

Gemeinsam für BoxMenu (MD) und Encounter-Import. Kein Kivy-Import: Fehler
kommen als Text zurück, die Anzeige macht der Aufrufer.
"""
from frontend.bh_dispatch import bh_submit

GAMES_CITRA = {"X", "Y", "Omega Rubin", "Alpha Saphir",
               "Sonne", "Mond", "Ultra Sonne", "Ultra Mond"}


async def read_boxes_for_player(pl: dict, citra, bizhawk,
                                player: int) -> tuple[list | None, str | None]:
    """Liest die Boxen über Citra (Gen 6/7) oder BizHawk.

    Rückgabe (boxes, None) bei Erfolg, sonst (None, Fehlertext).
    """
    game = pl.get("session_game", "")
    if game in GAMES_CITRA:
        # Citra-Handler bedient aktuell nur den lokalen Player
        if citra is None or citra.player_number != player:
            number = getattr(citra, "player_number", "?")
            return None, f"Citra ist Spieler {number} zugeordnet, nicht {player}."
        return await citra.read_and_decode_boxes(), None
    client_id = f"player{player:03d}"
    if bizhawk is None or client_id not in bizhawk.bizhawks:
        return None, f"Emulator {client_id} nicht verbunden."
    return await bh_submit(bizhawk.read_and_decode_boxes(client_id)), None
