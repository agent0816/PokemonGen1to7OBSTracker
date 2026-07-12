import Fastify, { type FastifyInstance } from 'fastify';
import sensible from '@fastify/sensible';
import { loadConfig } from './config.js';
import { StateStore } from './state/store.js';
import { PubSubPublisher } from './pubsub/publisher.js';
import { registerHealthRoutes } from './routes/health.js';
import { registerIngestRoutes } from './routes/ingest.js';
import { registerStateRoutes } from './routes/state.js';

export async function buildServer(): Promise<FastifyInstance> {
    const cfg = loadConfig();

    // Kein `loggerInstance` — Fastify soll seinen pino selbst bauen, damit der
    // Instance-Type nicht auf einen konkreten pino.Logger verengt wird (das
    // bricht sonst mit exactOptionalPropertyTypes gegen FastifyBaseLogger).
    const app = Fastify({
        logger: {
            level: cfg.logLevel,
            timestamp: () => `,"time":"${new Date().toISOString()}"`,
        },
        bodyLimit: 1_048_576, // 1 MiB — ingest ist immer klein
        trustProxy: true,     // nginx sitzt im Produktions-Setup davor
    });
    await app.register(sensible);

    // CORS: das Extension-Frontend läuft auf twitch.tv, im Rig auf
    // localhost.rig.twitch.tv. Ohne CORS-Header schlägt der State-Fetch fehl.
    app.addHook('onSend', async (req, reply, payload) => {
        const origin = req.headers.origin;
        if (!origin) return payload;
        const allowed =
            origin.endsWith('.twitch.tv') ||
            origin === 'https://www.twitch.tv' ||
            cfg.allowedOrigins.includes(origin);
        if (allowed) {
            reply.header('Access-Control-Allow-Origin', origin);
            reply.header('Vary', 'Origin');
            reply.header('Access-Control-Allow-Headers', 'Authorization, Content-Type');
            reply.header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS');
        }
        return payload;
    });
    app.options('/*', async (_req, reply) => reply.code(204).send());

    const store = new StateStore();
    const publisher = new PubSubPublisher(app.log);

    await registerHealthRoutes(app);
    await registerIngestRoutes(app, { store, publisher });
    await registerStateRoutes(app, { store });

    return app;
}
