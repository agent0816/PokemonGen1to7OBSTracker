logging = require "logging"

logging.set_level("info")
logging.set_timestamp_format("%Y-%m-%d %H:%M:%S")
logging.set_log_file_path("../logs/lua.log")
logging.initialize()

PLAYER=2
gui.drawText(10,10, "Player 2")
package.path = "./obsautomation.lua;"
connect = loadfile('obsautomation.lua')
connect()