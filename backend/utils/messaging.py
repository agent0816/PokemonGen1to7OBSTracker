import pickle
from asyncio import StreamReader, StreamWriter

CHUNK_SIZE = 500  # Bytes pro Chunk, muss konsistent mit Arceus und Munchlax bleiben


async def send_message(writer: StreamWriter, message) -> None:
    """Serialisiert eine Nachricht via Pickle und sendet sie in 500-Byte-Chunks mit Längen-Prefix."""
    serialized_message = pickle.dumps(message)

    # Gesamtlänge der Nachricht senden
    length = len(serialized_message).to_bytes(4, 'big')
    writer.write(length)
    await writer.drain()

    # Nachricht in Chunks senden
    for i in range(0, len(serialized_message), CHUNK_SIZE):
        chunk = serialized_message[i:i + CHUNK_SIZE]
        chunk_length = len(chunk).to_bytes(4, 'big')
        writer.write(chunk_length)
        await writer.drain()
        writer.write(chunk)
        await writer.drain()


async def receive_message(reader: StreamReader):
    """Empfängt eine chunked Pickle-Nachricht und gibt das deserialisierte Objekt zurück."""
    total_length = int.from_bytes(await reader.read(4), 'big')
    message = b''

    while len(message) < total_length:
        chunk_length = int.from_bytes(await reader.read(4), 'big')
        chunk = await reader.read(chunk_length)
        message += chunk

    return pickle.loads(message)
