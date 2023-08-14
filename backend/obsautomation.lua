if gameinfo.getromname() == 'Null' then
    while true do
        emu.frameadvance()
    end
end

gui.clearGraphics()

comm.socketServerSend("player" .. string.format("%03d", PLAYER))
logging.info("registered " .. "player" .. string.format("%03d", PLAYER) .. " for Munchlax")

function main()
    -- wie viele Sekunden zwischen den Updates
    local INTERVAL = 1

    -- Pointer getestet auf deutschen ROMs, bei englischen ROMs, Romhacks oder Randomizern sind vielleicht andere Offsets nötig    
    local RB = 0xD170 -- Rot/Blau
    local G = 0xD16F -- Gelb
    local NRBG = 0xF2B9 -- Nicknames Rot/Blau/Gelb

    local GS = 0xFA2A -- Gold/Silber
    local Kr = 0xFCDF -- Kristall
    local NGS = 0xFB8C -- Nicknames Gold/Silber
    local EGS = 0xFA23 -- Eier Gold/Silber
    local NKr = 0xDE41 -- Nicknames Kristall
    local EKr = 0xDCD8 -- Eier Kristall

    local FrLg = 0x02024284 -- Feuerrot/Blattgrün
    local RS = 0x03004370 -- Rubin/Saphir
    local E = 0x020244EC -- Smaragd

    local DP = 0x26D7EC -- Diamant/Perl
    local Pl = 0x27E40C -- Platin
    local HgSs = 0x27C310 -- HeartGold/SoulSilver

    local S = 0x022348F4 -- Schwarz
    local W = 0x02234914 -- Weiß
    local S2 = 0x0221E32C -- Schwarz 2
    local W2 = 0x0221E34C -- Weiß 2

    local length = 0
    local gameversion = ''

    if emu.getsystemid() == 'GBC' or emu.getsystemid() == 'GB' then
        gameversion = memory.read_u24_be(0x13c, 'ROM')
        if gameversion == 5391684 then
            pointer = RB
            gameversion = 11
            badgepointer = 0xD35B
        elseif gameversion == 4344917 then
            pointer = RB
            gameversion = 12
            badgepointer = 0xD35B
        elseif gameversion == 5850444 then
            pointer = G
            gameversion = 13
            badgepointer = 0xD35A
        elseif gameversion == 4672580 then
            pointer = GS
            gameversion = 21
            namepointer = NGS
            eggpointer = EGS
            badgepointer = 0xD57C
        elseif gameversion == 5459030 then
            pointer = GS
            gameversion = 22
            namepointer = NGS
            eggpointer = EGS
            badgepointer = 0xD57C
        elseif gameversion == 4279296 then
            pointer = Kr
            gameversion = 23
            namepointer = NKr
            eggpointer = EKr
            badgepointer = 0xD857
        end
        if gameversion < 20 then
            length = 264
            namepointer = NRBG
        else
            length = 288
        end
        domain = 'System Bus'

    elseif emu.getsystemid() == 'GBA' then
        length = 600
        gameversion = memory.read_u24_be(0xa8, 'ROM')
        if gameversion == 5395778 then
            pointer = RS
            gameversion = 31
            badgepointer = 0x2026a54
        elseif gameversion == 5456208 then
            pointer = RS
            gameversion = 32
            badgepointer = 0x2026a54
        elseif gameversion == 4541765 then
            pointer = E
            gameversion = 33
            badgepointer = 0x03005d8c
        elseif gameversion == 4606290 then
            pointer = FrLg
            gameversion = 34
            badgepointer = 0x3004F58
        elseif gameversion == 4998465 then
            pointer = FrLg
            gameversion = 35
            badgepointer = 0x3004F58
        end
        domain = 'System Bus'

    elseif emu.getsystemid() == 'NDS' then
        gameversion = memory.read_u16_be(0x23FFE08, 'ARM9 System Bus')
        if gameversion == 17408 then
            pointer = DP
            gameversion = 41
            badgepointer = 0xB70
            badgeoffset = 0x292
        elseif gameversion == 20480 then
            pointer = DP
            gameversion = 42
            badgepointer = 0xB70
            badgeoffset = 0x292
        elseif gameversion == 20556 then
            pointer = Pl
            gameversion = 43
            badgepointer = 0xBA8
            badgeoffset = 0x96
        elseif gameversion == 18503 then
            pointer = HgSs
            gameversion = 44
            badgepointer = 0xBA8
            badgeoffset = 0x8E
        elseif gameversion == 21331 then
            pointer = HgSs
            gameversion = 45
            badgepointer = 0xBA8
            badgeoffset = 0x8E
        elseif gameversion == 16896 then
            pointer = S
            gameversion = 51
            badgepointer = 0x23CCF0
        elseif gameversion == 22272 then
            pointer = W
            gameversion = 52
            badgepointer = 0x23CD10
        elseif gameversion == 16946 then
            pointer = S2
            gameversion = 53
            badgepointer = 0x226628
        elseif gameversion == 22322 then
            pointer = W2
            gameversion = 54
            badgepointer = 0x226648
        end

        if gameversion < 50 then
            length = 1416
            domain = 'Main RAM'
        else
            length = 1320
            domain = 'ARM9 System Bus'
        end
    end

    comm.socketServerSend(tostring(gameversion))
    logging.info("registered game " .. tostring(gameversion) .. " for Munchlax")

    local msg = ''
    local lastTime = os.time()
    local currTime = 0
    local lastTeam = {}
    local fluctcount = 0

    local function areTablesEqual(t1, t2)
        for i = 1, #t1 do
            if t1[i] ~= t2[i] then
                return false
            end
        end

        return true
    end

    while true do
        logging.info("started reading RAM from bizhawk")
        team = memory.read_bytes_as_array(pointer, length, domain)
        if areTablesEqual(team, lastTeam) then
            fluctcount = fluctcount + 1
        else
            fluctcount = 0
            lastTeam = team
        end

        msg = {gameversion, PLAYER, table.unpack(team)}
        if gameversion < 30 then
            for i = 0, 65 do
                msg[#msg + 1] = memory.readbyte(namepointer + i, domain)
            end
            if gameversion > 20 then
                for i = 0, 5 do
                    msg[#msg + 1] = memory.readbyte(eggpointer + i, domain)
                end
            end
        end
        -- Orden
        if gameversion < 30 then
            msg[#msg + 1] = memory.readbyte(badgepointer, domain)
            if gameversion > 20 then
                msg[#msg + 1] = memory.readbyte(badgepointer + 1, domain)
            end
        end
        if gameversion > 30 and gameversion < 40 then
            if gameversion < 33 then
                badges = memory.read_u16_le(badgepointer, domain)
                badges = bit.rshift(badges, 7)
                msg[#msg + 1] = badges & 0xFFFFFFFF
            elseif gameversion == 33 then
                badges = memory.read_u32_le(badgepointer, domain) + 0x137C
                badges = memory.read_u16_le(badges, domain)
                badges = bit.rshift(badges, 7)
                msg[#msg + 1] = badges & 0xFFFFFFFF
            elseif gameversion > 33 then
                badges = memory.read_u32_le(badgepointer, domain) + 0xFE4
                badges = memory.readbyte(badges, domain)
                msg[#msg + 1] = badges
            end
        end
        if gameversion > 40 and gameversion < 50 then
            badges = (memory.read_u32_le(badgepointer, domain) & 0xFFFFFF) + 0x20
            badges = (memory.read_u32_le(badges, domain) & 0xFFFFFF) + badgeoffset
            msg[#msg + 1] = memory.readbyte(badges, domain)
            if gameversion > 43 then
                msg[#msg + 1] = memory.readbyte(badges + 0x5, domain)
            end
        end
        if gameversion > 50 then
            msg[#msg + 1] = memory.readbyte(badgepointer, 'Main RAM')
        end
        currTime = os.time()
        if lastTime + INTERVAL <= currTime and fluctcount > 3 then
            lastTime = currTime
            comm.socketServerSendBytes(msg)

        end
        emu.frameadvance()
    end
end

local status, error = pcall(main)

if not status then
    print(error)
    logging.error(error)
end