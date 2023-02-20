import asyncio
import sys
import pickle
import backend.pokedecoder as pokedecoder

if len(sys.argv) > 1:
    port = sys.argv[1]
else:
    port = 43885

teams = {}


async def main(port):
    print(f'listening on port {port}')
    server = await asyncio.start_server(new_connection, '', port)
    async with server:
        await server.serve_forever()


async def new_connection(reader, writer):
    header = await reader.read(2)
    print(f'new connection, {header=}')
    if header[0] == 0:  # not BizHawk
        await handle_munchlax(writer)

    else:  # BizHawk
        await handle_bizhawk(reader, header[0], header[1])


async def handle_bizhawk(reader, e, p):
    print('new emulator connected')

    def get_length():
        if edition < 20:
            return 331
        elif edition < 30:
            return 362
        elif edition < 40:
            return 601
        elif edition < 50:
            return 1418
        elif edition < 60:
            return 1321

    def update_teams(team):
        team = pokedecoder.team(team, edition)
        print(team)
        if player in teams:
            if teams[player] == team:
                return
        teams[player] = team

    edition = e
    player = p

    length = get_length()
    msg = await reader.read(length)
    update_teams(msg)
    while True:
        try:
            header = await reader.read(2)
            edition = header[0]
            player = header[1]
            length = get_length()
            msg = await reader.read(length)
            update_teams(msg)
        except Exception:
            break


async def handle_munchlax(writer):
    print('new client connected')
    old_teams = teams.copy()
    msg = pickle.dumps(teams)
    writer.write(int.to_bytes(len(msg), 3, 'big'))
    writer.write(msg)
    await writer.drain()
    while True:
        # print(old_teams==teams)
        try:
            if old_teams != teams:
                print('teams changed')
                old_teams = teams.copy()
                msg = pickle.dumps(teams)
                writer.write(int.to_bytes(len(msg), 3, 'big'))
                writer.write(msg)
                await writer.drain()
            await asyncio.sleep(1)
        except Exception as exc:
            print(f"handle_munchlax abgebrochen:{exc}")
            break


if __name__ == '__main__':
    asyncio.run(main(port))
