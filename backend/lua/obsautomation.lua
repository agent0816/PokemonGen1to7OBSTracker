if gameinfo.getromname() == 'Null' then
    while true do
        emu.frameadvance()
    end
end

gui.clearGraphics()

local save_msg = 0
local fluct_init = false

-- Spieler-Registrierung beim Munchlax-Server
comm.socketServerSend("player" .. string.format("%03d", PLAYER))
logging.info("registered " .. "player" .. string.format("%03d", PLAYER) .. " for Munchlax")

-- Pointer werden seit Phase 3 von Python aus backend/data/pointer_gen*.yml geliefert.
-- length und domain bleiben hier systemabhängig, gameversion und language werden lokal
-- aus ROM-Headern gelesen und dann an Python geschickt.
local function detect_game()
    local length = 0
    local gameversion = ''
    local language = 0
    local domain = ''

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

    return gameversion, language, length, domain
end

local function send_game_info(gameversion, language)
    comm.socketServerSend(tostring(gameversion))
    logging.info("registered game " .. tostring(gameversion) .. " for Munchlax")
    comm.socketServerSend(tostring(language))
end

-- Pointer-Satz von Python empfangen (Format: "key=0xHEX;key=0xHEX;...")
local function receive_pointer_config()
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
end

local function areTablesEqual(t1, t2)
    for i = 1, #t1 do
        if t1[i] ~= t2[i] then
            return false
        end
    end
    return true
end

local function init_state(gameversion, length, domain)
    local state = {
        gameversion = gameversion,
        length = length,
        domain = domain,
        max_team_player = 0,
        cur_team_player = 0,
        in_battle = false,
        battle_msg = 'false',
        team = {},
        lastTeam = {},
        fluctcount = 0,
        old_names = {},
        old_eggs = {},
        old_badges_johto = 0,
        old_badges_kanto = 0,
        old_pointer = pointer,
    }

    for i = 1, length, 1 do
        state.team[i] = 0
        state.lastTeam[i] = 0
    end

    for i = 1, 66 do
        state.old_names[i] = 0
    end

    for i = 1, 6 do
        state.old_eggs[i] = 0
    end

    return state
end

local function check_battle(state)
    if state.gameversion > 40 then
        state.max_team_player = memory.read_u32_le(battlepointer - 0x8, state.domain)
        state.cur_team_player = memory.read_u32_le(battlepointer - 0x4, state.domain)
    elseif state.gameversion == 23 then
        state.cur_team_player = memory.readbyte(0xFCD7)
    end

    if state.max_team_player == 6 and state.cur_team_player > 0 and state.cur_team_player <= 7 then
        pointer = battlepointer
        state.in_battle = true
        state.battle_msg = 'true'
    else
        pointer = state.old_pointer
        state.in_battle = false
        state.battle_msg = 'false'
    end
end

local function read_team_bytes(state)
    if state.gameversion == 23 then
        if state.cur_team_player > 0 and state.cur_team_player <= 7 then
            state.team = memory.read_bytes_as_array(pointer, state.length, state.domain)
        end
    else
        state.team = memory.read_bytes_as_array(pointer, state.length, state.domain)
    end
end

local function check_fluctuation(state)
    if areTablesEqual(state.team, state.lastTeam) then
        if state.fluctcount % 30 == 0 then
        end
        state.fluctcount = state.fluctcount + 1
    else
        state.fluctcount = 0
        state.lastTeam = state.team
    end
end

local function build_msg(state)
    local msg = {table.unpack(state.team)}
    if state.gameversion == 23 then
        local names = memory.read_bytes_as_array(namepointer, 66, state.domain)
        local eggs = memory.read_bytes_as_array(eggpointer, 6, state.domain)
        if state.cur_team_player > 0 and state.cur_team_player <= 7 then
            state.old_names = names
            for i = 1, 66 do
                msg[#msg + 1] = names[i]
            end
            state.old_eggs = eggs
            for i = 1, 6 do
                msg[#msg + 1] = eggs[i]
            end
        else
            for i = 1, 66 do
                msg[#msg + 1] = state.old_names[i]
            end
            for i = 1, 6 do
                msg[#msg + 1] = state.old_eggs[i]
            end
        end
    elseif state.gameversion < 30 then
        for i = 0, 65 do
            msg[#msg + 1] = memory.readbyte(namepointer + i, state.domain)
        end
        if state.gameversion > 20 then
            for i = 0, 5 do
                msg[#msg + 1] = memory.readbyte(eggpointer + i, state.domain)
            end
        end
    end
    return msg
end

local function append_badges(msg, state)
    local badges_johto = memory.readbyte(badgepointer, state.domain)
    local badges_kanto = memory.readbyte(badgepointer + 1, state.domain)
    if state.gameversion == 23 then
        if state.cur_team_player > 0 and state.cur_team_player <= 7 then
            state.old_badges_johto = badges_johto
            state.old_badges_kanto = badges_kanto
            msg[#msg + 1] = badges_johto
            msg[#msg + 1] = badges_kanto
        else
            msg[#msg + 1] = state.old_badges_johto
            msg[#msg + 1] = state.old_badges_kanto
        end
    elseif state.gameversion < 30 then
        msg[#msg + 1] = badges_johto
        if state.gameversion > 20 then
            msg[#msg + 1] = badges_kanto
        end
    end
    if state.gameversion > 30 and state.gameversion < 40 then
        local badges
        if state.gameversion < 33 then
            badges = memory.read_u16_le(badgepointer, state.domain)
            badges = badges >> 7
            msg[#msg + 1] = badges & 0xFFFFFFFF
        elseif state.gameversion == 33 then
            badges = memory.read_u32_le(badgepointer, state.domain) + 0x137C
            badges = memory.read_u16_le(badges, state.domain)
            badges = badges >> 7
            msg[#msg + 1] = badges & 0xFFFFFFFF
        elseif state.gameversion > 33 then
            badges = memory.read_u32_le(badgepointer, state.domain) + 0xFE4
            badges = memory.readbyte(badges, state.domain)
            msg[#msg + 1] = badges
        end
    end
    if state.gameversion > 40 and state.gameversion < 50 then
        local badges = (memory.read_u32_le(badgepointer, state.domain) & 0xFFFFFF) + 0x20
        badges = (memory.read_u32_le(badges, state.domain) & 0xFFFFFF) + badgeoffset
        msg[#msg + 1] = memory.readbyte(badges, state.domain)
        if state.gameversion > 43 then
            msg[#msg + 1] = memory.readbyte(badges + 0x5, state.domain)
        end
    end
    if state.gameversion > 50 then
        msg[#msg + 1] = memory.readbyte(badgepointer, 'Main RAM')
    end
end

local function compute_battle_stats(state)
    local battle_stats = ''
    for pokemon = 1, state.cur_team_player, 1 do
        local hp = memory.read_u16_le(curHPinBattlepointer + (pokemon - 1) * 0x224, state.domain)
        battle_stats = battle_stats .. hp
        if pokemon < state.cur_team_player then
            battle_stats = battle_stats .. ","
        end
    end
    return battle_stats
end

local function handle_protocol_step(msg, battle_stats, state)
    if state.fluctcount > 3 then
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
        comm.socketServerSend(state.battle_msg)
    end
    if response == "stat_aktualisieren" then
        comm.socketServerSend(battle_stats)
    end
    -- box <cart_offset_hex> <size_hex>: Python fragt eine PC-Box aus dem
    -- CartRAM (lineare Sicht auf alle SRAM-Banks) an. Offset & Größe werden
    -- Python-seitig aus dem box_sram_layout berechnet, Lua liest nur stur.
    -- Wird für Gen 1/2 verwendet (Boxen liegen im SRAM).
    if response and response:sub(1, 4) == "box " then
        local off_s, size_s = string.match(response:sub(5), "^(%x+)%s+(%x+)$")
        if off_s and size_s then
            local offset = tonumber(off_s, 16)
            local size = tonumber(size_s, 16)
            local bytes = memory.read_bytes_as_array(offset, size, "CartRAM")
            comm.socketServerSendBytes(bytes)
        else
            logging.error("Ungültiges box-Kommando: " .. tostring(response))
        end
    end
    -- boxw <wram_offset_hex> <size_hex>: wie 'box', aber liest aus der
    -- System-Domain (System Bus für GBA, Main RAM / ARM9 System Bus für NDS).
    -- Wird für Gen 3/4/5 verwendet (Boxen liegen im WRAM ab box_pointer).
    if response and response:sub(1, 5) == "boxw " then
        local off_s, size_s = string.match(response:sub(6), "^(%x+)%s+(%x+)$")
        if off_s and size_s then
            local offset = tonumber(off_s, 16)
            local size = tonumber(size_s, 16)
            local bytes = memory.read_bytes_as_array(offset, size, state.domain)
            comm.socketServerSendBytes(bytes)
        else
            logging.error("Ungültiges boxw-Kommando: " .. tostring(response))
        end
    end
    -- bag <addr_hex> <size_hex>: liest aus state.domain (System Bus / Main RAM /
    -- ARM9 System Bus je nach Gen). Generisch fuer alle Bag-Pockets der Gens 1-5.
    -- Bei Gen 3 E/FR/BG (Saveblock-Relocation) holt Python zuerst die 4 Pointer-
    -- Bytes per 'bag', rechnet die effektive Adresse aus und ruft 'bag' erneut
    -- mit der absoluten Pocket-Adresse — Lua bleibt zustandslos.
    if response and response:sub(1, 4) == "bag " then
        local off_s, size_s = string.match(response:sub(5), "^(%x+)%s+(%x+)$")
        if off_s and size_s then
            local offset = tonumber(off_s, 16)
            local size = tonumber(size_s, 16)
            local bytes = memory.read_bytes_as_array(offset, size, state.domain)
            comm.socketServerSendBytes(bytes)
        else
            logging.error("Ungültiges bag-Kommando: " .. tostring(response))
        end
    end
end

function main()
    local gameversion, language, length, domain = detect_game()
    send_game_info(gameversion, language)
    receive_pointer_config()
    local state = init_state(gameversion, length, domain)

    while true do
        check_battle(state)
        read_team_bytes(state)
        check_fluctuation(state)
        local msg = build_msg(state)
        append_badges(msg, state)
        local battle_stats = ''
        if state.in_battle and state.gameversion > 50 then
            battle_stats = compute_battle_stats(state)
        end
        handle_protocol_step(msg, battle_stats, state)
        emu.frameadvance()
    end
end

local status, error = pcall(main)

if not status then
    print(error)
    logging.error(error)
end
