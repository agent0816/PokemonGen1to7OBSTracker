import asyncio
import sys
import pickle
# import backend.pokedecoder as pokedecoder
import logging
import os
import subprocess

class Arceus:
    def __init__(self, host, port):
        self.host = host
        self.port = port
        self.munchlaxes = {}
        self.emulators = {}
        self.server = None

        self.logger = self.init_logging()

    def init_logging(self):
        logger = logging.getLogger(__name__)

        logging_formatter = logging.Formatter('[%(asctime)s] %(levelname)s: %(message)s')

        file_handler = logging.FileHandler('../logs/arceus.log', 'w')
        file_handler.setFormatter(logging_formatter)
        logger.addHandler(file_handler)

        stream_handler = logging.StreamHandler(sys.stdout)
        stream_handler.setFormatter(logging_formatter)
        logger.addHandler(stream_handler)
        
        return logger
    
    async def handle_client(self, reader, writer):
        
        client_id = await reader.read(100)
        self.munchlaxes[client_id] = writer
        print(f"Client {client_id.decode()} connected.")

        try:
            while True:
                data = await reader.read(100)
                if not data:
                    break
                
                self.logger.info(data)
                # message = data.decode()
                # if message == 'heartbeat':
                #     print(f"Heartbeat received from client {client_id.decode()}")
                # else:
                #     print(f"Received {message} from client {client_id.decode()}")
        except ConnectionResetError:
            pass
        finally:
            del self.munchlaxes[client_id]
            print(f"Client {client_id.decode()} disconnected.")
            writer.close()
            await writer.wait_closed()

    async def start(self):
        self.server = await asyncio.start_server(
            self.handle_client, self.host, self.port)

        async with self.server:
            await self.server.serve_forever()

    async def stop(self):
        if self.server:
            self.server.close()
            await self.server.wait_closed()
            print("Server has been stopped.")


if __name__ == "__main__":
    loop = asyncio.get_event_loop()

    server = Arceus('', 43885)

    loop.create_task(server.start())

    process = subprocess.Popen(['D:\\Emulatoren\\Bizhawk\\EmuHawk.exe', 
                    f'--lua={os.path.abspath(f"../backend/Player1.lua")}', 
                    f'--socket_ip=127.0.0.1', 
                    f'--socket_port=43885'])

    try:
        loop.run_forever()
    except KeyboardInterrupt:
        pass
    finally:
        process.terminate()
        loop.run_until_complete(server.stop())
