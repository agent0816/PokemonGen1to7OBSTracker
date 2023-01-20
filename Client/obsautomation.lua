if gameinfo.getromname() == 'Null' then
    while true do
        emu.frameadvance()
    end
end

gui.clearGraphics()

-- wie viele Sekunden zwischen den Updates
local INTERVAL = 1

--Pointer getestet auf deutschen ROMs, bei englischen ROMs, Romhacks oder Randomizern sind vielleicht andere Offsets nötig    
local RB = 0xD170         --Rot/Blau
local G = 0xD16F          --Gelb
local NRBG = 0xF2B9       --Nicknames Rot/Blau/Gelb

local GS = 0xFA2A         --Gold/Silber
local Kr = 0xFCDF         --Kristall
local NGS = 0xFB8C        --Nicknames Gold/Silber
local EGS = 0xFA23        --Eier Gold/Silber
local NKr = 0xDE41        --Nicknames Kristall
local EKr = 0xDCD8        --Eier Kristall

local FrLg = 0x02024284   --Feuerrot/Blattgrün
local RS = 0x03004370     --Rubin/Saphir
local E = 0x020244EC      --Smaragd

local DP = 0x26D7EC       --Diamant/Perl
local Pl = 0x27E40C       --Platin
local HgSs = 0x27C310     --HeartGold/SoulSilver

local S = 0x022348F4      --Schwarz
local W = 0x02234914      --Weiß
local S2 = 0x0221E32C     --Schwarz 2
local W2 = 0x0221E34C     --Weiß 2

local length = 0
local gameversion = ''

if emu.getsystemid() =='GBC' or emu.getsystemid() == 'GB' then
    gameversion = memory.read_u24_be(0x13c, 'ROM')
    if gameversion == 5391684 then 
        pointer = RB 
        gameversion = 11
    elseif gameversion == 4344917 then 
        pointer = RB 
        gameversion = 12
    elseif gameversion == 5850444 then 
        pointer = G 
        gameversion = 13
    elseif gameversion == 4672580 then 
        pointer = GS 
        gameversion = 21
        namepointer = NGS
        eggpointer = EGS
    elseif gameversion == 5459030 then 
        pointer = GS 
        gameversion = 22
        namepointer = NGS
        eggpointer = EGS
    elseif gameversion == 4279296 then 
        pointer = Kr
        gameversion = 23
        namepointer = NKr
        eggpointer = EKr
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
    if gameversion == 5395778 then pointer = RS 
        gameversion = 31
    elseif gameversion == 5456208 then pointer = RS
    gameversion = 32 
    elseif gameversion == 4541765 then 
        pointer = E
        gameversion = 33 
    elseif gameversion == 4606290 then 
        pointer = FrLg
        gameversion = 34 
    elseif gameversion == 4998465 then 
        pointer = FrLg
        gameversion = 35 end
    domain = 'System Bus'

elseif emu.getsystemid() =='NDS' then
    gameversion = memory.read_u16_be(0x23FFE08,'ARM9 System Bus')
    if gameversion == 17408 then 
        pointer = DP 
        gameversion = 41
    elseif gameversion == 20480 then 
        pointer = DP 
        gameversion = 42
    elseif gameversion == 20556 then 
        pointer = Pl 
        gameversion = 43
    elseif gameversion == 18503 then 
        pointer = HgSs 
        gameversion = 44
    elseif gameversion == 21331 then 
        pointer = HgSs 
        gameversion = 45
    elseif gameversion == 16896 then 
        pointer = S
        gameversion = 51
    elseif gameversion == 22272 then
        pointer = W
        gameversion = 52
    elseif gameversion == 16946 then
        pointer = S2
        gameversion = 53
    elseif gameversion == 22322 then
        pointer = W2
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


local msg = ''
local lastTime = os.time()
local currTime = 0

while true do
    currTime = os.time()
    if lastTime + INTERVAL <= currTime then
        lastTime = currTime
        if gameversion < 30 then
            msg = {gameversion, PLAYER, unpack(memory.read_bytes_as_array(pointer, length, domain))}
            for i=0,65 do
                msg[#msg + 1] = memory.readbyte(namepointer + i, domain)
            end
            if gameversion > 20 then
                for i=0,5 do
                    msg[#msg + 1] = memory.readbyte(eggpointer + i, domain)
                end
            end
        else
            msg = {gameversion, PLAYER, unpack(memory.read_bytes_as_array(pointer, length, domain))}
        end
        comm.socketServerSendBytes(msg)
        
    end
    emu.frameadvance()
end
