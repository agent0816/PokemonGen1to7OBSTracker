# PokemonGen1to7OBSTracker

Desktop-Tracker, der Pokemon-Teams live aus Emulatoren (Gen 1–7) ausliest und als Stream-Overlay ausgibt — per OBS-WebSocket oder Browser-Source. Mehrere Spieler lokal oder über einen eigenen Remote-Server.

## Features

- **Generationen 1–7** aus BizHawk (Gen 1–5) und Citra/Azahar (Gen 6–7)
- **BizHawk-Autostart**: Tracker spawnt EmuHawk-Prozesse pro lokalem Spieler, lädt `backend/lua/tracker.lua` und setzt Socket-Args automatisch — kein manuelles Lua-Load
- **Zwei Overlay-Modi**
  - OBS-WebSocket: Sprites/Badges als Scene-Items
  - Browser-Source: lokaler HTTP/WebSocket-Server, HTML-Overlay als OBS Browser-Source (Pokelink-Stil)
- **Multi-Player-Sessions** (bis zu 4 Spieler) — lokal oder remote via eigenem asyncio-TCP-Server (`Arceus`)
- **Team-, Boxen- und Bag-Ansicht** in der GUI, Detailview pro Pokemon (Stats, Moves, Ability, Nature)
- **Encounter-Tracking (Nuzlocke)** für Gen 3–6, Gen 7 in Arbeit — Wild + Gift/Fossil, Routen-Status via YAML-LUTs
- **Randomizer-Runs pro Session**: pro lokalem Spieler wird ein ROM erzeugt, ROM/Log/Encounter-DB werden pro Run archiviert. Ein Run endet bei Total-Wipe, per manuellem Button oder mit dem nächsten Randomize-Vorgang
- **Sprite-Repo-Integration** (git clone/pull) — Fork unter [agent0816/sprites](https://github.com/agent0816/sprites)
- **Auto-Updater** über tufup
- **ROM-Hack-Support**: erste Anbindung für Renegade Platinum (English-Pointer-Override)

## Unterstützte Editionen

| Gen | Editionen | Emulator |
|-----|-----------|----------|
| 1   | Rot, Blau, Gelb | BizHawk |
| 2   | Gold, Silber, Kristall | BizHawk |
| 3   | Rubin, Saphir, Smaragd, FR/BG | BizHawk |
| 4   | D/P, Platin, HG/SS, Renegade Platinum | BizHawk |
| 5   | S/W, S2/W2 | BizHawk |
| 6   | X/Y, OR/AS | Citra / Azahar |
| 7   | S/M, US/UM | Citra / Azahar |

Deutsche ROMs primär getestet. Englische Versionen und Randomizer laufen oft nur eingeschränkt.

## Installation

### Binary (Windows)

Aktuelles Release aus [GitHub Releases](https://github.com/agent0816/PokemonGen1to7OBSTracker/releases) laden, entpacken, `PokemonGen1to7OBSTracker.exe` starten. Auto-Updater hält die Version aktuell.

## Setup

### BizHawk (Gen 1–5)

1. In der Tracker-App unter **Settings → BizHawk** Pfad zur `EmuHawk.exe`, Host + Port setzen
2. Verbindung starten — Tracker spawnt pro lokalem Spieler eine EmuHawk-Instanz mit vorgeladenem Lua und Socket-Config
3. ROM in der Instanz starten — Handshake läuft automatisch

### Citra / Azahar (Gen 6–7)

1. In Citra/Azahar RPC aktivieren (Emulation → Configure → Debug → Enable Debug RPC)
3. Für Bag-Writes (z. B. Sonderbonbon-Trick): Azahars RPC-Whitelist fehlt für den LINEAR-Heap, Tracker nutzt WriteProcessMemory-Workaround

### Overlay

- **OBS-WebSocket**: `Settings → OBS` → Host/Port/Passwort. Tracker legt Scene-Items automatisch an.
- **Browser-Source**: Overlay-Server im Menü starten, URL (`http://localhost:<port>/overlay`) als Browser-Source in OBS einbinden.

### Sprite-Repo

Beim ersten Start öffnet sich der Sprite-Setup-Popup: Repo wird von [agent0816/sprites](https://github.com/agent0816/sprites) nach `backend/sprites/` geklont und per Pull aktualisiert.

## Sessions

Konfiguration liegt pro Session unter `backend/config/<session_name>/` mit acht YAMLs (`sprites`, `bh_config`, `obs_config`, `player`, `remote`, `randomizer`, `overlay`, `nuzlocke`), der SQLite-DB `pokemon.db` und dem `runs/`-Ordner (Randomizer-Run-Historie). Sessions per **Session-Menü** anlegen/wechseln/löschen.

## Architektur (Kurz)

- **Frontend**: Kivy + ScreenManager (`frontend/`)
- **Backend**: asyncio, Pickle-basiertes Chunked-Messaging zwischen `Munchlax` (Client) und `Arceus` (Server)
- **Decoder**: `backend/pokedecoder.py` (gen-spezifisch), Speicher-Offsets in `backend/data/pointer_*.yml`
- **Datenbank**: SQLite via `PokedexDB` (Pokedex, Bag, Encounter)
- **Logging**: zentrales Setup in `backend/logging_setup.py`, pro Modul RotatingFileHandler unter `logs/`

## Roadmap / TODO

### Kern-Features

- [ ] Sprite-Animation aus, sobald Pokemon besiegt
- [ ] `update_battle_stats` ab Gen 5 vollständig erfassen (Kampfdetails)
- [ ] Encounter-Datenbank anlegen (persistente Speicherung)
- [ ] Nuzlocke-Regeln implementieren (Death-Detection, Same-Route-Regel, Species-Clause)
- [ ] Soullink-Regeln implementieren
- [ ] Encounter-Tracking Gen 7 fertigstellen (Zonen mappen)

### UI

- [ ] UI-Rewrite fortsetzen (KivyMD-Migration)
- [ ] OBS-Settings: Standard-Localhost-Preset auf eigenem Screen
- [ ] OBS-Settings: WebSocket-Konfig pro Spieler
- [ ] Anzeige "aktueller Spieler" (Kontext ROM)
- [ ] Item-Sprites in die UI einbetten
- [ ] Uhr / Versus-Timer

### Overlay

- [ ] OBS-Overlay-Erzeuger (HTML-Templates + Auto-Scene-Layout, einheitlicher Einstieg für beide Modi)
- [ ] CSS-Themes für Browser-Source-Overlays

### Infrastruktur

- [ ] `Arceus` als Headless-Server (ohne GUI-Abhängigkeit)
- [ ] Twitch Extension
- [ ] Vollständige Umstellung auf asyncio (Rest-Blocking eliminieren)
- [ ] Erweiterter ROM-Hack-Support (Community-Pointer-YAMLs)

## Bekannte Einschränkungen

- Windows-only (PowerShell-Aufrufe im Frontend)
- Deutsche ROMs primär getestet
- Heartbeat 5 s / 3 Fehltakte → Disconnect (kann bei Debug-Sessions triggern)
- Azahar: LINEAR-Heap-Writes nur via WriteProcessMemory-Umgehung

## Lizenz

TODO — noch nicht festgelegt.

## Credits

- Sprites: [agent0816/sprites](https://github.com/agent0816/sprites) (Fork mit Anpassungen)
- Inspiration Browser-Overlay: [Pokelink](https://pokelink.xyz/)
