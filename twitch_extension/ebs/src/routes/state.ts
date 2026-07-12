import type { FastifyInstance } from 'fastify';
import { verifyTwitchJwt } from '../auth/jwt.js';
import type { StateStore } from '../state/store.js';

interface Deps {
    store: StateStore;
}

interface ChannelParams {
    channelId: string;
}

export async function registerStateRoutes(app: FastifyInstance, deps: Deps): Promise<void> {
    app.get<{ Params: ChannelParams }>('/state/:channelId', {
        preHandler: verifyTwitchJwt,
    }, async (req, reply) => {
        const jwtChannel = req.twitchJwt?.channel_id;
        if (!jwtChannel || jwtChannel !== req.params.channelId) {
            // Ein JWT für Channel A darf nicht den State von Channel B abfragen.
            return reply.code(403).send({ error: 'channel_mismatch' });
        }
        return deps.store.get(req.params.channelId);
    });
}
