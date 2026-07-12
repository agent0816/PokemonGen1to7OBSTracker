# Extension-Frontend — Pokemon-Tracker Twitch Extension

Reines statisches HTML/CSS/JS, das Twitch nach dem Upload selbst hostet.
**Kein Build-Schritt**, keine externen CDNs (strikte CSP im Extension-Iframe).

## Struktur

```
extension/
  viewer.html      # Video-Overlay (die Ansicht über dem Stream)
  config.html      # Broadcaster-Config-Panel (Pairing-Code, Phase 4)
  css/
    viewer.css
  js/
    viewer.js
    config.js
  ebs-endpoint.js  # ⚠ EBS-URL — vor jedem Upload anpassen (siehe unten)
```

`ebs-endpoint.js` steht bewusst separat, damit man dieselbe Codebasis für
Dev (`http://localhost:8081`), Hosted-Test und Released-Deploy einsetzen kann
ohne Änderungen an `viewer.js`. Datei liegt im Extension-Bundle und wird von
`viewer.html` als erstes `<script>` geladen.

## Twitch-Verkabelung (Kurz)

1. `viewer.html` bindet `<script src="twitch-ext.js">` (von Twitch bereitgestellt
   unter der einen erlaubten Extension-Whitelist-Domain — muss ins finale HTML)
2. `viewer.js` ruft `window.Twitch.ext.onAuthorized(auth => ...)` — bekommt JWT + channel_id
3. Initialer State: `fetch(EBS_BASE_URL + '/state/' + channel_id, {headers: {Authorization: 'Bearer ' + auth.token}})`
4. Live-Updates: `window.Twitch.ext.listen('broadcast', (target, contentType, message) => ...)`

## Lokal testen (Developer Rig)

Später mit `twitch extensions rig` — erst wenn Twitch-Client-ID vorhanden.
Vorher kannst du `viewer.html` **nicht** direkt im Browser öffnen, weil
`window.Twitch.ext` nur im Twitch-Iframe existiert.

Für einen groben Sanity-Check der DOM-Struktur (ohne echte Daten) siehe
`viewer.js` — die Funktion `renderState()` ist datenzentriert und lässt sich
per Devtools-Konsole mit einem Mock-Objekt aufrufen.

## Was das Skeleton macht

- Zeigt die Rohdaten aus `/state` und aus PubSub als vorformatiertes JSON an
- Kein UI-Design — das ist Phase 3 der TODO_twitch_extension.md
- Sprite-Auflösung: **noch nicht** — Sprites sind das größte offene UI-Risiko
  (CSP), siehe TODO Phase 3 „Sprite-Frage früh klären"
