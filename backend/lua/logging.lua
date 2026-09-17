-- logging.lua
local logging = {}

local levels = {
    ["debug"] = 1,
    ["info"] = 2,
    ["warning"] = 3,
    ["error"] = 4,
    ["critical"] = 5
}

-- Rotation: bei Ueberschreiten von MAX_LOG_BYTES wandert lua.log -> lua.log.1,
-- ..., lua.log.4 -> lua.log.5, aeltestes wird geloescht.
local MAX_LOG_BYTES = 512 * 1024
local MAX_BACKUPS = 5

local current_level = "info"
local timestamp_format = "%c"
local log_file_path = "lua.log"

local function file_exists(path)
    local f = io.open(path, "r")
    if f then
        f:close()
        return true
    end
    return false
end

local function file_size(path)
    local f = io.open(path, "r")
    if not f then return 0 end
    local ok, size = pcall(function() return f:seek("end") end)
    f:close()
    if ok and size then return size end
    return 0
end

local function rotate_logs()
    local oldest = log_file_path .. "." .. MAX_BACKUPS
    if file_exists(oldest) then
        os.remove(oldest)
    end
    for i = MAX_BACKUPS - 1, 1, -1 do
        local src = log_file_path .. "." .. i
        local dst = log_file_path .. "." .. (i + 1)
        if file_exists(src) then
            os.rename(src, dst)
        end
    end
    if file_exists(log_file_path) then
        os.rename(log_file_path, log_file_path .. ".1")
    end
end

-- Silent-Fail: Logging darf den Tracker nie mit einer Exception killen
-- (Disk voll, Permission denied, Rotation-Race zwischen Player-Prozessen).
-- Preis: einzelne Log-Zeilen koennen bei I/O-Fehlern verloren gehen.
local function safe_write(text)
    pcall(function()
        if file_size(log_file_path) >= MAX_LOG_BYTES then
            rotate_logs()
        end
        local file = io.open(log_file_path, "a")
        if file then
            file:write(text)
            file:close()
        end
    end)
end

function logging.initialize()
    -- Kein Wipe mehr: nur Session-Marker anhaengen, damit Logs Restarts ueberleben.
    safe_write("===== new session " .. os.date(timestamp_format) .. " =====\n")
end

function logging.set_level(level)
    if levels[level] then
        current_level = level
    else
        error("Ungültiges Logging-Level: " .. tostring(level))
    end
end

function logging.set_timestamp_format(format)
    timestamp_format = format
end

function logging.set_log_file_path(path)
    log_file_path = path
end

function logging.log(level, message)
    if levels[level] >= levels[current_level] then
        safe_write("[" .. os.date(timestamp_format) .. "] " .. level .. ": " .. tostring(message) .. "\n")
    end
end

-- Definieren Sie Funktionen für jedes Logging-Level
for level, _ in pairs(levels) do
    logging[level] = function(message)
        logging.log(level, message)
    end
end

return logging
