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
        language = memory.read_u8(0x23FFE0F, 'ARM9 System Bus')
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

-- BizHawk-Version an Python melden, damit der Tracker zu alte Emulatoren ablehnen kann.
-- pcall, weil sehr alte BizHawks client.getversion() eventuell nicht anbieten.
local function send_bh_version()
    local ok, v = pcall(client.getversion)
    if not ok or not v then
        v = "unknown"
    end
    comm.socketServerSend(tostring(v))
    logging.info("sent BizHawk version: " .. tostring(v))
end

-- Zeigt den Ablehnungsgrund dauerhaft im Emulator an und hält das Skript an,
-- damit der User weiß, dass er ein Update braucht.
local function halt_with_message(msg)
    logging.error(msg)
    while true do
        gui.drawText(10, 10, msg, "red", "black", 12)
        emu.frameadvance()
    end
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
    battlerscountpointer = nil
    battleoutcomepointer = nil
    battlemonspointer = nil
    local pointer_config = comm.socketServerResponse()
    logging.info("received pointer config: " .. tostring(pointer_config))
    if pointer_config and pointer_config:sub(1, 12) == "UNSUPPORTED:" then
        halt_with_message("BizHawk-Version nicht unterstuetzt (" .. pointer_config .. ") - bitte Emulator aktualisieren.")
    end
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
        if state.max_team_player == 6 and state.cur_team_player > 0 and state.cur_team_player <= 7 then
            pointer = battlepointer
            state.in_battle = true
            state.battle_msg = 'true'
        else
            pointer = state.old_pointer
            state.in_battle = false
            state.battle_msg = 'false'
        end
    elseif state.gameversion > 30 and state.gameversion < 40 then
        -- Gen 3: gBattleOutcome-basierter Compound-Check.
        --
        -- gBattlersCount alleine reicht NICHT: die Zelle wird beim Kampfende
        -- nicht auf 0 zurueckgesetzt, sie behaelt den letzten Wert. Ergebnis
        -- war stuck in_battle=true nach dem ersten Kampf → keine weiteren
        -- False->True-Edges → _read_encounter_data feuert nie wieder →
        -- wilde Pokemon fallen durch als "Gift" ein.
        --
        -- Ironmon-Tracker (Battle.lua updateBattleStatus) nutzt stattdessen
        -- die Kombination:
        --   gBattleOutcome == 0        → Kampf aktiv
        --   gBattleOutcome ~= 0        → Kampf beendet (1=won, 2=lost, 4=fled, 7=caught)
        --   gBattlersCount in [1..4]   → Sanity fuer Battler-Anzahl
        --   gBattleMons[0].species valid → Fake-Battle-Filter
        if battlerscountpointer and battleoutcomepointer and battlemonspointer then
            local cnt = memory.readbyte(battlerscountpointer, state.domain)
            local outcome = memory.readbyte(battleoutcomepointer, state.domain)
            local firstMonSpecies = memory.read_u16_le(battlemonspointer, state.domain)
            -- Species-Grenze 1024: Vanilla Gen 3 hat 411 Species, aber
            -- Randomizer koennen IDs bis 649 (Gen 2-5-Range, siehe
            -- backend/data/species_personal_gen2to5.yml) injizieren. 1024
            -- gibt Puffer fuer ROM-Hacks/Community-Randomizer, ohne den
            -- Fake-Battle-Filter zu weit zu oeffnen (u16-Max ist 65535,
            -- Garbage-Reads liegen meist deutlich darueber).
            local isFakeBattle = (cnt == 0) or (cnt > 4)
                or (firstMonSpecies == 0) or (firstMonSpecies > 1024)
            if outcome == 0 and not isFakeBattle then
                state.in_battle = true
                state.battle_msg = 'true'
            else
                state.in_battle = false
                state.battle_msg = 'false'
            end
        elseif battlerscountpointer then
            -- Legacy-Fallback fuer den Fall, dass ein User das Tracker-Update
            -- installiert hat, aber sein pointer_gen3.yml noch die alte
            -- Version ohne battleoutcomepointer/battlemonspointer benutzt
            -- (z.B. wenn er eigene Custom-Pointer-YAML gepatched hat und
            -- die neuen Keys nicht mit-uebernahm). Behaelt das bekannte
            -- stuck-Verhalten (Wilds fallen als Gift-Encounter durch), aber
            -- die App stuerzt nicht ab. Kann entfernt werden sobald der
            -- Repo-Zwangsupgrade sicher ist (alle Deploys > vX.Y.Z).
            local cnt = memory.readbyte(battlerscountpointer, state.domain)
            if cnt > 0 and cnt <= 4 then
                state.in_battle = true
                state.battle_msg = 'true'
            else
                state.in_battle = false
                state.battle_msg = 'false'
            end
        end
    elseif state.gameversion == 23 then
        state.cur_team_player = memory.readbyte(0xFCD7)
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
            local base = memory.read_u32_le(badgepointer, state.domain)
            -- Guard: am Titelbildschirm ist der SaveBlock2-Pointer noch 0 → Deref
            -- landet im BIOS-Bereich und crasht den GBA-Core.
            if base >= 0x02000000 and base < 0x03000000 then
                badges = memory.read_u16_le(base + 0x137C, state.domain)
                badges = badges >> 7
                msg[#msg + 1] = badges & 0xFFFFFFFF
            else
                msg[#msg + 1] = 0
            end
        elseif state.gameversion > 33 then
            local base = memory.read_u32_le(badgepointer, state.domain)
            if base >= 0x02000000 and base < 0x03000000 then
                msg[#msg + 1] = memory.readbyte(base + 0xFE4, state.domain)
            else
                msg[#msg + 1] = 0
            end
        end
    end
    if state.gameversion > 40 and state.gameversion < 50 then
        local base1 = memory.read_u32_le(badgepointer, state.domain) & 0xFFFFFF
        -- Guard: uninitialisierter Storage-Pointer würde den zweiten Deref auf
        -- eine BIOS-nahe Adresse zeigen lassen; DS-Core reagiert darauf teils
        -- mit Freeze.
        if base1 >= 0x02000000 then
            local base2 = (memory.read_u32_le(base1 + 0x20, state.domain) & 0xFFFFFF) + badgeoffset
            if base2 >= 0x02000000 then
                msg[#msg + 1] = memory.readbyte(base2, state.domain)
                if state.gameversion > 43 then
                    msg[#msg + 1] = memory.readbyte(base2 + 0x5, state.domain)
                end
            else
                msg[#msg + 1] = 0
                if state.gameversion > 43 then
                    msg[#msg + 1] = 0
                end
            end
        else
            msg[#msg + 1] = 0
            if state.gameversion > 43 then
                msg[#msg + 1] = 0
            end
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
    -- bagw <addr_hex> <hex_bytes_payload>: schreibt die Bytes (hex-codiert,
    -- gerade Laenge) ab addr in state.domain. Antwortet IMMER mit einem Byte
    -- (0x01 = OK, 0x00 = ERR), damit der Python-Reader nicht in readexactly()
    -- haengt — derselbe Stolperstein wie bei "bag" mit ungueltiger Adresse.
    if response and response:sub(1, 5) == "bagw " then
        local off_s, hex_s = string.match(response:sub(6), "^(%x+)%s+(%x+)$")
        local ok = false
        if off_s and hex_s and (#hex_s % 2 == 0) then
            local addr = tonumber(off_s, 16)
            ok = true
            local k = 0
            for i = 1, #hex_s, 2 do
                local byte = tonumber(hex_s:sub(i, i + 1), 16)
                if byte then
                    memory.writebyte(addr + k, byte, state.domain)
                    k = k + 1
                else
                    ok = false
                    break
                end
            end
        else
            logging.error("Ungültiges bagw-Kommando: " .. tostring(response))
        end
        if ok then
            comm.socketServerSendBytes({0x01})
        else
            comm.socketServerSendBytes({0x00})
        end
    end
end

function main()
    local gameversion, language, length, domain = detect_game()
    send_game_info(gameversion, language)
    send_bh_version()
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
