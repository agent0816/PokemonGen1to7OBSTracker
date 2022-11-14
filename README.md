# PokemonGen1to5OBSTracker Checkliste

Backend:
---
*     [x] OBS-Verbindung einbauen
     
*     [x] Wechseln der Quellen einbauen
     *    [x] Bilder
     *    [x] Spitznamen

Frontend:
---
*    [x] mit Kivy auseinandersetzen

*    [x] Screen Manager kivy

*    [x] Design der Fenster
*         [x] Startfenseter
*         [x] Settingsfenster
          *   [x] Sprite-Einstellungen Pfade
          *   [x] Bizhhawk-Einstellungen
          *   [ ] OBS-Settings
               *    [ ] Standard-OBS Einstellungen für localhost auf separatem Screen
               *    [ ] Websocketeinstellungen für jeden Spieler anpassen
          *   [x] Remote-Settings
          *   [x] Spielereinstellungen (wer ist lokal und wer remote)
               *    [x] remote Knopf enabled den OBS Knopf
               *    [x] lokale Spieler bekommen den localhost Websocket zugeteilt
          *   [ ] aktueller Spieler (mit ROM?)

*    [ ] UI einbetten
     *    [ ] Einstellungen für Itemsprites in der UI
allgemein:
---
*    [x] in main.py das Frontend aufrufen

*    [ ] alles mit asyncio asynchron gestalten