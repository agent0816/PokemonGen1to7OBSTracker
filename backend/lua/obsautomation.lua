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

    local FrLg = 0x2024284 -- Feuerrot/Blattgrün
    local RS = 0x3004370 -- Rubin/Saphir
    local E = 0x20244EC -- Smaragd

    local DP = 0x26D7EC -- Diamant/Perl
    local DPBattlePlayer = 0x2B9D18
    local DPBattleOpponent = 0x2BA2C8
    local DPBattleOpponentID = 0x2AFFCE -- (u_le_16 0x0 = wild | > 0x0 ist Trainer ID)
    local DPinBattle = DPBattlePlayer - 0x8
    -- local DPinBattle = 0x23BBDF -- (u_le_8 0x21 = true, 0x20 = false) 0xA7 Unterschied zu Englisch
    -- 0x27E358
    local Pl = 0x27E40C -- Platin
    local PlBattlePlayer = 0x2C9C04
    local PlBattleOpponent = 0x2CA1B4
    local PlBattleOpponentID = 0x2BFBF6 -- (u_le_16 0x0 = wild | > 0x0 ist Trainer ID)
    local PlinBattle = PlBattlePlayer - 0x8
    -- local PlinBattle = 0x24A71A -- (u_le_8 0x10 = true, 0x00 = false)
    local HgSs = 0x27C310 -- HeartGold/SoulSilver
    local HgSsBattlePlayer = 0x0
    local HgSsBattleOpponent = 0x0
    local HgSsBattleOpponentID = 0x0
    local HgSsinBattle = HgSsBattlePlayer - 0x8

    -- 0x240 Unterschied Deutsch Englisch

    local S = 0x022348F4 -- Schwarz Offset 0xC0 zu englisch
    local SBattlePlayer = 0x0226A6D4
    local ScurHPinBattle = 0x0226D5F4 -- u_le_16 548 Byte für jedes Pokemon 0x224 | Level: addr + 0x8 u_16_le % 256
    local SBattleOpponent = 0x0226B194 
    local SBattleOpponentID = 0x022696FE -- (u_le_16 0x0 = wild | > 0x0 ist Trainer ID)
    local SinBattle = SBattlePlayer - 0x8 -- (u_le_32 0x6 | addr + 0x4 ; 0x1 <= u_le_32 <= 0x6)
    -- local SinBattle = 0x21D077E
    
    local W = 0x022349D4 -- Weiß
    local WBattlePlayer = 0x0226A7B4
    local WcurHPinBattle = 0x0226D6D4
    local WBattleOpponent = 0x0226B274
    local WBattleOpponentID = 0x022697DE
    local WinBattle = WBattlePlayer - 0x8
    
    local S2 = 0x0221E32C -- Schwarz 2
    local S2BattlePlayer = 0x02258214
    local S2curHPinBattle = 0x0225B134
    local S2BattleOpponent = 0x02258774
    local S2BattleOpponentID = 0x02257232
    local S2inBattle = S2BattlePlayer - 0x8
    
    local W2 = 0x0221E34C -- Weiß 2
    local W2BattlePlayer = 0x0221E2CC
    local W2curHPinBattle = 0x0225B154
    local W2BattleOpponent = 0x02258714
    local W2BattleOpponentID = 0x022571D2
    local W2inBattle = W2BattlePlayer - 0x8

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
            battlepointer = DPBattlePlayer
            gameversion = 41
            badgepointer = 0xB70
            badgeoffset = 0x292
        elseif gameversion == 20480 then
            pointer = DP
            battlepointer = DPBattlePlayer
            gameversion = 42
            badgepointer = 0xB70
            badgeoffset = 0x292
        elseif gameversion == 20556 then
            pointer = Pl
            battlepointer = PlBattlePlayer
            gameversion = 43
            badgepointer = 0xBA8
            badgeoffset = 0x96
        elseif gameversion == 18503 then
            pointer = HgSs
            battlepointer = HgSsBattlePlayer
            gameversion = 44
            badgepointer = 0xBA8
            badgeoffset = 0x8E
        elseif gameversion == 21331 then
            pointer = HgSs
            battlepointer = HgSsBattlePlayer
            gameversion = 45
            badgepointer = 0xBA8
            badgeoffset = 0x8E
        elseif gameversion == 16896 then
            pointer = S
            battlepointer = SBattlePlayer
            curHPinBattlepointer = ScurHPinBattle
            gameversion = 51
            badgepointer = 0x23CCF0
        elseif gameversion == 22272 then
            pointer = W
            battlepointer = WBattlePlayer
            curHPinBattlepointer = WcurHPinBattle
            gameversion = 52
            badgepointer = 0x23CDD0 -- offset 0xC0 zu englisch
        elseif gameversion == 16946 then
            pointer = S2
            battlepointer = S2BattlePlayer
            curHPinBattlepointer = S2curHPinBattle
            gameversion = 53
            badgepointer = 0x226628
        elseif gameversion == 22322 then
            pointer = W2
            battlepointer = W2BattlePlayer
            curHPinBattlepointer = W2curHPinBattle
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

    while true do
        if gameversion > 40 then
            max_team_player = memory.read_u32_le(battlepointer - 0x8, domain)
            cur_team_player = memory.read_u32_le(battlepointer - 0x4, domain)
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
        
        team = memory.read_bytes_as_array(pointer, length, domain)
        if areTablesEqual(team, lastTeam) then
            if fluctcount % 30 == 0 then
            end
            fluctcount = fluctcount + 1
        else
            fluctcount = 0
            lastTeam = team
        end

        msg = {table.unpack(team)}
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