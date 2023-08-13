import asyncio

class Munchlax:
    def __init__(self, client_id, host, port):
        self.client_id = client_id
        self.host = host
        self.port = port

    async def send_heartbeat(self, writer):
        while True:
            await asyncio.sleep(5)
            writer.write(b'heartbeat')
            await writer.drain()

    async def start(self):
        self.reader, self.writer = await asyncio.open_connection(self.host, self.port)
        self.writer.write(self.client_id.encode())
        await self.writer.drain()

        asyncio.create_task(self.send_heartbeat(self.writer))

        try:
            while True:
                message = input("Enter message to send or 'exit' to disconnect: ")
                if message == 'exit':
                    await self.disconnect()
                    break
                self.writer.write(message.encode())
                await self.writer.drain()
        except KeyboardInterrupt:
            print("Client disconnecting...")

    async def disconnect(self):
        self.writer.close()
        await self.writer.wait_closed()
        print(f"Client {self.client_id} has been disconnected.")

class Bizhawk_Client():
    pass
