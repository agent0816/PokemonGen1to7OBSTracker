-- logging.lua
local logging = {}

local levels = {
    ["debug"] = 1,
    ["info"] = 2,
    ["warning"] = 3,
    ["error"] = 4,
    ["critical"] = 5
}

function with_file(filename, mode, func)
    local file = io.open(filename, mode)
    if file then
        local result = {pcall(func, file)}
        file:close()
        if not result[1] then
            error(result[2])
        end
        return table.unpack(result, 2)
    else
        error("Konnte Datei nicht öffnen: " .. filename)
    end
end

local current_level = "info"
local timestamp_format = "%c" -- Standard-Zeitstempelformat
local log_file_path = "lua.log" -- Standard-Speicherort des Log-Files

function logging.initialize()
    -- Leeren Sie die Log-Datei
    with_file(log_file_path, "w", function(file) end)
end

function logging.set_level(level)
    if levels[level] then
        current_level = level
    else
        error("Ungültiges Logging-Level: " .. level)
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
        with_file(log_file_path, "a", function(file)
            file:write(os.date(timestamp_format) .. " [" .. level .. "]: " .. message .. "\n")
        end)
    end
end

-- Definieren Sie Funktionen für jedes Logging-Level
for level, _ in pairs(levels) do
    logging[level] = function(message)
        logging.log(level, message)
    end
end

return logging
