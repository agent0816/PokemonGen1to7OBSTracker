if gameinfo.getromname() == 'Null' then
    while true do
        emu.frameadvance()
    end
end

gui.clearGraphics()

comm.socketServerSend("player" .. string.format("%03d", PLAYER))
logging.info("registered " .. "player" .. string.format("%03d", PLAYER) .. " for Munchlax")

local save_msg = 0
local fluct_init = false

function main()
    -- wie viele Sekunden zwischen den Updates
    local INTERVAL = 1

    -- Pointer werden seit Phase 3 von Python aus backend/data/pointer_gen*.yml geliefert.
    -- length und domain bleiben hier systemabhängig, gameversion und language werden lokal
    -- aus ROM-Headern gelesen und dann an Python geschickt.

    local length = 0
    local gameversion = ''
    language = 0

    if emu.getsystemid() == 'GBC' or emu.getsystemid() == 'GB' then
        gameversion = memory.read_u24_be(0x13c, 'ROM')
        if gameversion == 5391684 then
            gameversion = 11
        elseif gameversion == 4344917 then
            gameversion = 12
        elseif gameversion == 5850444 then
            gameversion = 13
        elseif gameversion == 4672580 then
            gameversion = 21
        elseif gameversion == 5459030 then
            gameversion = 22
        elseif gameversion == 4279296 then
            gameversion = 23
        end
        if gameversion < 20 then
            length = 264
        else
            length = 288
        end
        domain = 'System Bus'

    elseif emu.getsystemid() == 'GBA' then
        length = 600
        gameversion = memory.read_u24_be(0xa8, 'ROM')
        language = memory.read_u32_be(0xac, 'ROM')
        if gameversion == 5395778 then
            gameversion = 31
        elseif gameversion == 5456208 then
            gameversion = 32
        elseif gameversion == 4541765 then
            gameversion = 33
        elseif gameversion == 4606290 then
            gameversion = 34
        elseif gameversion == 4998465 then
            gameversion = 35
        end
        domain = 'System Bus'

    elseif emu.getsystemid() == 'NDS' then
        gameversion = memory.read_u16_be(0x23FFE08, 'ARM9 System Bus')
        if gameversion == 17408 then
            gameversion = 41
        elseif gameversion == 20480 then
            gameversion = 42
        elseif gameversion == 20556 then
            gameversion = 43
        elseif gameversion == 18503 then
            gameversion = 44
        elseif gameversion == 21331 then
            gameversion = 45
        elseif gameversion == 16896 then
            gameversion = 51
        elseif gameversion == 22272 then
            gameversion = 52
        elseif gameversion == 16946 then
            gameversion = 53
        elseif gameversion == 22322 then
            gameversion = 54
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
    comm.socketServerSend(tostring(language))

    -- Pointer-Satz von Python empfangen (Format: "key=0xHEX;key=0xHEX;...")
    pointer = nil
    namepointer = nil
    eggpointer = nil
    badgepointer = nil
    badgeoffset = nil
    battlepointer = nil
    curHPinBattlepointer = nil
    battleopponentpointer = nil
    battleopponentidpointer = nil
    local pointer_config = comm.socketServerResponse()
    logging.info("received pointer config: " .. tostring(pointer_config))
    if pointer_config and pointer_config ~= "" then
        for pair in string.gmatch(pointer_config, "[^;]+") do
            local k, v = string.match(pair, "([^=]+)=(.+)")
            if k and v then
                _G[k] = tonumber(v)
            end
        end
    else
        logging.error("Leere Pointer-Konfig von Python — ROM nicht erkannt?")
    end

    local msg = ''
    -- local lastTime = os.time()
    local currTime = 0
    local lastTeam = {}
    local fluctcount = 0
    old_pointer = pointer

    local function areTablesEqual(t1, t2)
        for i = 1, #t1 do
            if t1[i] ~= t2[i] then
                return false
            end
        end

        return true
    end

    local max_team_player = 0
    local cur_team_player = 0
    local in_battle = false
    local battle_msg = ''
    team = {}
    
    for i = 1, length, 1 do
        team[i] = 0
        lastTeam[i] = 0
    end
    
    local old_names = {}

    for i = 1, 66 do
        old_names[i] = 0
    end

    local old_eggs = {}

    for i = 1, 6 do
        old_eggs[i] = 0
    end

    local old_badges_johto = 0
    local old_badges_kanto = 0

    while true do
        if gameversion > 40 then
            max_team_player = memory.read_u32_le(battlepointer - 0x8, domain)
            cur_team_player = memory.read_u32_le(battlepointer - 0x4, domain)
        elseif gameversion == 23 then
            cur_team_player = memory.readbyte(0xFCD7)
        end
        
        if max_team_player == 6 and cur_team_player > 0 and cur_team_player <= 7 then
            pointer = battlepointer
            in_battle = true
            battle_msg = 'true'
        else
            pointer = old_pointer
            in_battle = false
            battle_msg = 'false'
        end
        
        if gameversion == 23 then
            if cur_team_player > 0 and cur_team_player <= 7 then
                team = memory.read_bytes_as_array(pointer, length, domain)
            end
        else
            team = memory.read_bytes_as_array(pointer, length, domain)
        end
        if areTablesEqual(team, lastTeam) then
            if fluctcount % 30 == 0 then
            end
            fluctcount = fluctcount + 1
        else
            fluctcount = 0
            lastTeam = team
        end

        msg = {table.unpack(team)}
        if gameversion == 23 then
            local names = memory.read_bytes_as_array(namepointer, 66, domain)
            local eggs = memory.read_bytes_as_array(eggpointer, 6, domain)
            if cur_team_player > 0 and cur_team_player <= 7 then
                old_names = names
                for i = 1, 66 do
                    msg[#msg + 1] = names[i]
                end
                old_eggs = eggs
                for i = 1, 6 do
                    msg[#msg + 1] = eggs[i]
                end
            else
                for i = 1, 66 do
                    msg[#msg + 1] = old_names[i]
                end
                for i = 1, 6 do
                    msg[#msg + 1] = old_eggs[i]
                end
            end
        elseif gameversion < 30 then
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
        local badges_johto = memory.readbyte(badgepointer, domain)
        local badges_kanto = memory.readbyte(badgepointer + 1, domain)
        if gameversion == 23 then
            if cur_team_player > 0 and cur_team_player <= 7 then
                old_badges_johto = badges_johto
                old_badges_kanto = badges_kanto
                msg[#msg + 1] = badges_johto
                msg[#msg + 1] = badges_kanto
            else
                msg[#msg + 1] = old_badges_johto
                msg[#msg + 1] = old_badges_kanto
            end 
        elseif gameversion < 30 then
            msg[#msg + 1] = badges_johto
            if gameversion > 20 then
                msg[#msg + 1] = badges_kanto
            end
        end
        if gameversion > 30 and gameversion < 40 then
            if gameversion < 33 then
                badges = memory.read_u16_le(badgepointer, domain)
                -- badges = bit.rshift(badges, 7)
                badges = badges >> 7
                msg[#msg + 1] = badges & 0xFFFFFFFF
            elseif gameversion == 33 then
                badges = memory.read_u32_le(badgepointer, domain) + 0x137C
                badges = memory.read_u16_le(badges, domain)
                -- badges = bit.rshift(badges, 7)
                badges = badges >> 7
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

        local battle_stats = ''

        if in_battle and gameversion > 50 then
            for pokemon = 1 , cur_team_player, 1 do
                local hp = memory.read_u16_le(curHPinBattlepointer + (pokemon - 1) * 0x224, domain)
                battle_stats = battle_stats .. hp
                if pokemon < cur_team_player then
                    battle_stats = battle_stats .. ","
                end
            end
        end

        if fluctcount > 3 then
            save_msg = msg
            fluct_init = true
        end

        local check_msg = "Aufgabe"
        comm.socketServerSend(check_msg)
        local response = comm.socketServerResponse()
        if response == "team" then
            if fluct_init then
                comm.socketServerSendBytes(save_msg)
            else
                comm.socketServerSendBytes(msg)
            end
        end
        if response == "saveRAM" then
            client.saveram()
            check_msg = "saveRAM erfolgreich"
            comm.socketServerSend(check_msg)
        end
        if response == "in_battle" then
            comm.socketServerSend(battle_msg)
        end
        if response == "stat_aktualisieren" then
            comm.socketServerSend(battle_stats)
        end
        emu.frameadvance()
    end
end

local status, error = pcall(main)

if not status then
    print(error)
    logging.error(error)
end