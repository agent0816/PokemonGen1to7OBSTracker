# EBS — Pokemon-Tracker Twitch Extension

Extension Backend Service für die Twitch-Extension des PokemonGen1to7OBSTracker.

Nimmt Team-/Bag-/Pokedex-Updates vom Tracker (bzw. Headless Server) entgegen, hält den letzten Zustand pro Channel im Speicher und pusht Änderungen als Extension-PubSub-Nachricht an die Zuschauer-Frontends.

Nur der EBS-Teil ist self-hosted — das Extension-Frontend selbst hostet Twitch.

## Voraussetzungen

- Node.js ≥ 20
- Twitch Developer Console → Extension angelegt (Client-ID + Extension-Secret vorhanden)

## Setup

```bash
cd twitch_extension/ebs
npm install
cp .env.example .env
# .env öffnen und Werte eintragen
```

Wichtig: das Extension-Secret aus dem Dashboard ist **Base64-kodiert**. Es wird beim Start automatisch dekodiert und als HS256-Key für die JWT-Verifikation genutzt.

Solange `PUBSUB_DRY_RUN=true`, geht kein Publish echt an Twitch — sinnvoll fürs lokale Testen ohne gültige Client-ID.

## Entwicklung

```bash
npm run dev        # tsx-watch, Hot-Reload
npm run typecheck  # nur Type-Check, kein Emit
npm run build      # dist/ bauen
npm run start      # dist/index.js ausführen
```

## Endpoints

### Ingest (Tracker → EBS)

Auth: `Authorization: Bearer <INGEST_SHARED_SECRET>`

- `POST /ingest/session/:channelId/team`
- `POST /ingest/session/:channelId/bag`
- `POST /ingest/session/:channelId/pokedex`
- `POST /ingest/session/:channelId/player-switch`
- `POST /ingest/session/:channelId/session-end`

Body: siehe `src/types/payloads.ts`.

### Extension-Frontend → EBS

Auth: `Authorization: Bearer <Twitch-JWT>` (aus `Twitch.ext.onAuthorized`)

- `GET /state/:channelId` — kompletter letzter Zustand für den initialen Load

### System

- `GET /health` — Liveness

## Rate-Limit

Pro Channel wird maximal 1 PubSub-Nachricht pro Sekunde tatsächlich an Twitch geschickt. Neuere Updates ersetzen ältere, die noch nicht raus sind (last-write-wins).

## Roadmap (skeleton → produktionsreif)

- JWT-Signatur für Twitch-Publish-API (aktuell Stub)
- Persistenter State (Redis o. Ä.) statt In-Memory
- CI-Pipeline (siehe TODO_twitch_extension.md, Phase 1)
- Docker-Compose + nginx + Let's-Encrypt
