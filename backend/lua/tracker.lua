logging = require "logging"

logging.set_level("info")
logging.set_timestamp_format("%Y-%m-%d %H:%M:%S")

-- PLAYER wird per Umgebungsvariable vom Python-Launcher gesetzt (connection_controller.start_bizhawk)
-- Fallback auf 1, wenn das Script manuell aus dem BizHawk-Lua-Console geladen wird.
-- Wichtig: PLAYER MUSS vor resolve_log_path bekannt sein, sonst kann der Player-Suffix
-- im Log-Pfad nicht gesetzt werden und mehrere BizHawks wuerden dieselbe lua.log
-- teilen + parallel rotieren (Race).
local env_player = os.getenv("TRACKER_PLAYER")
local player_fallback_warning = nil
if env_player then
    -- tonumber liefert nil bei nicht-numerischem Input, oder einen Float bei
    -- "1.5" o.ae. Beides wuerde string.format("lua_player%03d.log", ...) unten
    -- crashen — und zwar VOR logging.initialize(), also ohne dass der Fehler
    -- geloggt wuerde. Deshalb nur akzeptieren, wenn es eine Ganzzahl ist.
    -- Zusaetzliche Guards: math.huge (tonumber("1e400") ueberlaeuft zu inf und
    -- besteht sonst den floor-Check) und < 1 (negative/null Werte sind
    -- semantisch unsinnig fuer eine 1-basierte Spielernummer, wuerden aber
    -- string.format("%03d", -1) still zu "player-01" formatieren).
    local parsed = tonumber(env_player)
    if parsed == nil or parsed == math.huge or parsed == -math.huge
            or parsed ~= math.floor(parsed) or parsed < 1 then
        PLAYER = 1
        player_fallback_warning = "TRACKER_PLAYER='" .. tostring(env_player) .. "' nicht als positive Ganzzahl parsebar — Fallback auf Player 1"
    else
        PLAYER = parsed
    end
else
    PLAYER = 1
    player_fallback_warning = "TRACKER_PLAYER nicht gesetzt — Fallback auf Player 1"
end

-- Absoluten Log-Pfad bestimmen: Env-Var vom Python-Launcher hat Vorrang,
-- Fallback ist die Aufloesung ueber den Script-Pfad via debug.getinfo,
-- damit auch ein manueller BizHawk-Start (ohne connection_controller)
-- eine sinnvolle Log-Datei bekommt. Player-Suffix verhindert Rotation-Races
-- zwischen mehreren BizHawk-Instanzen, die sich sonst dieselbe Datei teilen.
local function resolve_log_path()
    local filename = string.format("lua_player%03d.log", PLAYER)
    local env_dir = os.getenv("TRACKER_LOG_DIR")
    if env_dir and env_dir ~= "" then
        env_dir = env_dir:gsub("\\", "/")
        return env_dir .. "/" .. filename
    end
    local src = debug.getinfo(1, "S").source
    if src:sub(1, 1) == "@" then
        src = src:sub(2)
    end
    src = src:gsub("\\", "/")
    local script_dir = src:match("(.*/)") or "./"
    return script_dir .. "../../logs/" .. filename
end

local log_path = resolve_log_path()
logging.set_log_file_path(log_path)
logging.initialize()
logging.info("tracker.lua start — log_path=" .. tostring(log_path))
if player_fallback_warning then
    logging.warning(player_fallback_warning)
end

-- Startup-Diagnostik: eine Zeile mit allen Kontextdaten, damit spaetere
-- Fehler ueber diesen Fingerabdruck einer BizHawk-Session zugeordnet werden.
local ok_rom, romname = pcall(gameinfo.getromname)
local ok_sys, systemid = pcall(emu.getsystemid)
local ok_ver, bhver = pcall(client.getversion)
logging.info(string.format(
    "startup: player=%s, system=%s, rom=%s, bizhawk=%s, script=%s",
    tostring(PLAYER),
    tostring(ok_sys and systemid or "?"),
    tostring(ok_rom and romname or "?"),
    tostring(ok_ver and bhver or "?"),
    tostring(debug.getinfo(1, "S").source)
))

gui.drawText(10, 10, "Player " .. PLAYER)
package.path = "./obsautomation.lua;"
connect = loadfile('obsautomation.lua')
connect()
