# PokemonGen1to5OBSTracker Checkliste

Backend:
---
*    [x] OBS-Verbindung einbauen
     
*   [x] Wechseln der Quellen einbauen
    *    [x] Bilder
    *    [x] Spitznamen

Frontend:
---
*    [x] mit Kivy auseinandersetzen

*    [x] Screen Manager kivy

*    [x] Design der Fenster
    *    [x] Startfenseter
    *    [x] Settingsfenster
        *    [x] Sprite-Einstellungen Pfade
        *    [x] Bizhhawk-Einstellungen
        *    [ ] OBS-Settings
            *    [ ] Standard-OBS Einstellungen für localhost auf separatem Screen
            *    [ ] Websocketeinstellungen für jeden Spieler anpassen
        *    [x] Remote-Settings
        *    [x] Spielereinstellungen (wer ist lokal und wer remote)
            *    [x] remote Knopf enabled den OBS Knopf
            *    [x] lokale Spieler bekommen den localhost Websocket zugeteilt
        *   [ ] aktueller Spieler (mit ROM?)

*    [ ] UI einbetten
    *    [ ] Einstellungen für Itemsprites in der UI
allgemein:
---
*    [x] in main.py das Frontend aufrufen

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

- [ ] KivyMD-Migration abschließen (Phasen A–F fertig, MD ist Standard-UI mit Toggle/Onboarding; offen: F5 Paritäts-Check + Nuitka-Build + Alpha, Phase G = Klassik entfernen)
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

Der **Quellcode** dieses Projekts steht unter der [Apache License 2.0](LICENSE). Siehe auch die [NOTICE](NOTICE)-Datei.

Die Apache-2.0-Lizenz gilt ausschließlich für den Code und die eigenen Werke des Projekts — **nicht** für Marken, Logos oder Artworks Dritter (siehe Rechtlicher Hinweis).

## Rechtlicher Hinweis / Disclaimer

PokemonGen1to7OBSTracker ist ein **inoffizielles, von Fans erstelltes, kostenloses und nicht-kommerzielles** Tool. Es steht in keiner Verbindung zu und wird weder unterstützt noch gesponsert oder autorisiert von **Nintendo, The Pokémon Company oder Game Freak**.

„Pokémon" sowie alle zugehörigen Namen, Editions-Titel, Logos, Charaktere und Artworks sind Marken und/oder urheberrechtlich geschützte Werke ihrer jeweiligen Eigentümer.

- Dieses Tool enthält und verteilt **keine ROMs**, Spiel-Software oder sonstige Spielinhalte. Nutzer müssen legal erworbene Kopien der Spiele selbst bereitstellen.
- Die im Tool verwendeten Editions-Logos und -Artworks dienen ausschließlich der **Kennzeichnung der jeweils unterstützten Spielversion** (nominative Nutzung). Alle Rechte daran verbleiben bei den jeweiligen Eigentümern.

## Credits

- Sprites: [agent0816/sprites](https://github.com/agent0816/sprites) (Fork mit Anpassungen)
- Inspiration Browser-Overlay: [Pokelink](https://pokelink.xyz/)
