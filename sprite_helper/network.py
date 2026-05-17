import asyncio
import pickle
import logging

logger = logging.getLogger("sprite_helper.network")

CHUNK_SIZE = 500


async def send_message(writer: asyncio.StreamWriter, message):
    serialized = pickle.dumps(message)

    length = len(serialized).to_bytes(4, 'big')
    writer.write(length)
    await writer.drain()

    for i in range(0, len(serialized), CHUNK_SIZE):
        chunk = serialized[i:i + CHUNK_SIZE]
        chunk_length = len(chunk).to_bytes(4, 'big')
        writer.write(chunk_length)
        await writer.drain()
        writer.write(chunk)
        await writer.drain()


async def receive_message(reader: asyncio.StreamReader):
    total_length = int.from_bytes(await reader.readexactly(4), 'big')
    message = b''

    while len(message) < total_length:
        chunk_length = int.from_bytes(await reader.readexactly(4), 'big')
        chunk = await reader.readexactly(chunk_length)
        message += chunk

    return pickle.loads(message)


async def send_sprite_paths(host: str, port: int, paths: dict) -> bool:
    try:
        reader, writer = await asyncio.open_connection(host, port)
        message = {
            "type": "sprite_path_obs",
            **paths
        }
        await send_message(writer, message)

        response = await asyncio.wait_for(receive_message(reader), timeout=10)
        writer.close()
        await writer.wait_closed()

        if response == "ok":
            logger.info("Pfade erfolgreich an Tracker gesendet.")
            return True
        else:
            logger.error(f"Unerwartete Antwort vom Tracker: {response}")
            return False
    except ConnectionRefusedError:
        logger.error(f"Verbindung zu {host}:{port} verweigert. Läuft der Tracker?")
        return False
    except Exception as err:
        logger.error(f"Fehler beim Senden: {err}")
        return False
