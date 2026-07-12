import 'dotenv/config';

export interface AppConfig {
    port: number;
    host: string;
    logLevel: string;
    twitch: {
        clientId: string;
        // Bereits base64-dekodierter Secret-Buffer, direkt für HS256 nutzbar.
        secret: Buffer;
        ownerId: string;
    };
    ingestSharedSecret: string;
    pubsubDryRun: boolean;
    allowedOrigins: string[];
}

function required(key: string): string {
    const v = process.env[key];
    if (!v || v.trim() === '') {
        throw new Error(`Env-Variable ${key} fehlt oder ist leer`);
    }
    return v;
}

function optional(key: string, fallback: string): string {
    const v = process.env[key];
    return v && v.trim() !== '' ? v : fallback;
}

function decodeSecret(base64: string): Buffer {
    try {
        // Twitch liefert das Extension-Secret als Base64-String — vor der
        // Verwendung als HS256-Key IMMER dekodieren (klassische Anfänger-Falle).
        return Buffer.from(base64, 'base64');
    } catch (err) {
        throw new Error(`TWITCH_EXTENSION_SECRET_BASE64 nicht dekodierbar: ${String(err)}`);
    }
}

let cached: AppConfig | null = null;

export function loadConfig(): AppConfig {
    if (cached) return cached;

    const dryRun = optional('PUBSUB_DRY_RUN', 'true').toLowerCase() === 'true';

    // Im Dry-Run darf das Extension-Secret ein Platzhalter sein — wir prüfen
    // nur, dass irgendwas da ist. Für echten Betrieb ist es Pflicht.
    const secretRaw = dryRun
        ? optional('TWITCH_EXTENSION_SECRET_BASE64', 'ZHJ5LXJ1bi1wbGFjZWhvbGRlcg==')
        : required('TWITCH_EXTENSION_SECRET_BASE64');

    cached = {
        port: Number(optional('PORT', '8081')),
        host: optional('HOST', '0.0.0.0'),
        logLevel: optional('LOG_LEVEL', 'info'),
        twitch: {
            clientId: dryRun
                ? optional('TWITCH_EXTENSION_CLIENT_ID', 'dry-run-client-id')
                : required('TWITCH_EXTENSION_CLIENT_ID'),
            secret: decodeSecret(secretRaw),
            ownerId: dryRun
                ? optional('TWITCH_EXTENSION_OWNER_ID', '0')
                : required('TWITCH_EXTENSION_OWNER_ID'),
        },
        ingestSharedSecret: required('INGEST_SHARED_SECRET'),
        pubsubDryRun: dryRun,
        allowedOrigins: optional('ALLOWED_ORIGINS', '')
            .split(',')
            .map((o) => o.trim())
            .filter((o) => o.length > 0),
    };
    return cached;
}
