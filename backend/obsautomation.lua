if gameinfo.getromname() == 'Null' then
    while true do
        emu.frameadvance()
    end
end

playerselected = false


function playerSelected()
    playerselected = true
    form = forms.destroyall()
end


-- Forms testen
local table = {"1", "2", "3", "4"}
local form = forms.newform(300, 200, "Spieler")
local picture = forms.pictureBox(form, 0, 0, 300, 20)
forms.drawText(picture, 5, 5, "Bitte den Spieler auswaehlen:")
local dropdown = forms.dropdown(form, table, 5, 20)
forms.button(form, "Bestaetigen", playerSelected, 5, 40)

while not playerselected do
    text = forms.gettext(dropdown)
    emu.frameadvance()
end


if text == '1' then PLAYER = 1
elseif text == '2' then PLAYER = 2
elseif text == '3' then PLAYER = 3
elseif text == '4' then PLAYER = 4 end


-- wie viele Sekunden zwischen den Updates
local INTERVAL = 1

--Pointer getestet auf deutschen ROMs, bei englischen ROMs, Romhacks oder Randomizern sind vielleicht andere Offsets nötig    
local RB = 0xD170         --Rot/Blau
local G = 0xD16F          --Gelb
local NRBG = 0xF2B9       --Nicknames Rot/Blau/Gelb

local GS = 0xFA2A         --Gold/Silber
local Kr = 0xFCDF         --Kristall
local NGS = 0xFB8C        --Nicknames Gold/Silber
local NKr = 0xDE41        --Nicknames Kristall

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

if emu.getsystemid() =='GBC' then
    gameversion = bizstring.substring(gameinfo.getromname(),10,3)
    if gameversion == 'Rot' then 
        pointer = RB 
        gameversion = 11
    elseif gameversion == 'Bla' then 
        pointer = RB 
        gameversion = 12
    elseif gameversion == 'Gel' then 
        pointer = G 
        gameversion = 13
    elseif gameversion == 'Gol' then 
        pointer = GS 
        gameversion = 21
        namepointer = NGS
    elseif gameversion == 'Sil' then 
        pointer = GS 
        gameversion = 22
        namepointer = NGS
    elseif gameversion == 'Kri' then 
        pointer = Kr
        gameversion = 23
        namepointer = NKr
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
    gameversion = bizstring.substring(gameinfo.getromname(),10,3)
    if gameversion == 'Rub' then pointer = RS 
        gameversion = 31
    elseif gameversion == 'Sap' then pointer = RS
    gameversion = 32 
    elseif gameversion == 'Sma' or gameversion == 'Eme' then 
        pointer = E
        gameversion = 33 
    elseif gameversion == 'Feu' or gameversion == 'Fir' then 
        pointer = FrLg
        gameversion = 34 
    elseif gameversion == 'Bla' or gameversion == 'Lea' then 
        pointer = FrLg
        gameversion = 35
    else
        gameversion = memory.readbyte(0xA8,'ROM')
        if gameversion == 0x46 then 
            pointer = FrLg
            gameversion = 34 
        elseif gameversion == 0x4C then 
            pointer = FrLg
            gameversion = 35 
        elseif gameversion == 0x52 then 
            pointer = RS
            gameversion = 31 
        elseif gameversion == 0x53 then 
            pointer = RS
            gameversion = 32 
        elseif gameversion == 0x45 then 
            pointer = E
            gameversion = 33 end
    end
    domain = 'System Bus'

elseif emu.getsystemid() =='NDS' then
    gameversion = bizstring.substring(gameinfo.getromname(),10,3)
    if gameversion == 'Dia' then 
        pointer = DP 
        gameversion = 41
    elseif gameversion == 'Per' then 
        pointer = DP 
        gameversion = 42
    elseif gameversion == 'Pla' then 
        pointer = Pl 
        gameversion = 43
    elseif gameversion == 'Gol' then 
        pointer = HgSs 
        gameversion = 44
    elseif gameversion == 'Sil' then 
        pointer = HgSs 
        gameversion = 45
    end
    
    if type(gameversion) ~= 'number' then
        gameversion = bizstring.substring(gameinfo.getromname(),16,19)
    end

    if gameversion == ' Schwarze Edition (' then 
        pointer = S
        gameversion = 51
    elseif gameversion == ' Weisse Edition (Ge' then
        pointer = W
        gameversion = 52
    elseif gameversion == ' Schwarze Edition 2' then
        pointer = S2
        gameversion = 53
    elseif gameversion == ' Weisse Edition 2 (' then
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
        else
            msg = {gameversion, PLAYER, unpack(memory.read_bytes_as_array(pointer, length, domain))}
        end
        comm.socketServerSendBytes(msg)
    end
    emu.frameadvance()
end
