logging = require "logging"

logging.set_level("info")
logging.set_timestamp_format("%Y-%m-%d %H:%M:%S")
logging.set_log_file_path("../../logs/lua.log")
logging.initialize()

-- PLAYER wird per Umgebungsvariable vom Python-Launcher gesetzt (connection_controller.start_bizhawk)
-- Fallback auf 1, wenn das Script manuell aus dem BizHawk-Lua-Console geladen wird
local env_player = os.getenv("TRACKER_PLAYER")
if env_player then
    PLAYER = tonumber(env_player)
else
    PLAYER = 1
    logging.warning("TRACKER_PLAYER nicht gesetzt — Fallback auf Player 1")
end

gui.drawText(10, 10, "Player " .. PLAYER)
package.path = "./obsautomation.lua;"
connect = loadfile('obsautomation.lua')
connect()
