# PokemonGen1to5OBSTracker Checkliste

Backend:
---
[x] OBS-Verbindung einbauen

[x] Wechseln der Quellen einbauen
*    [x] Bilder
*    [x] Spitznamen

Frontend:
---
[x] mit Kivy auseinandersetzen

[x] Screen Manager kivy

[x] Design der Fenster
*    [x] Startfenseter
*    [x] Settingsfenster
     *   [x] Sprite-Einstellungen Pfade
     *   [x] Bizhhawk-Einstellungen
     *   [ ] OBS-Settings
          *    [ ] Standard-OBS Einstellungen für localhost auf separatem Screen
          *    [ ] Websocketeinstellungen für jeden Spieler anpassen
     *   [x] Remote-Settings
     *   [ ] Spielereinstellungen (wer ist lokal und wer remote)
          *    [ ] remote Knopf enabled den OBS Knopf
          *    [ ] lokale Spieler bekommen den localhost Websocket zugeteilt
     *   [ ] aktueller Spieler (mit ROM?)

[ ] UI einbetten

allgemein:
---
[x] in main.py das Frontend aufrufen

[ ] alles mit asyncio asynchron gestalten