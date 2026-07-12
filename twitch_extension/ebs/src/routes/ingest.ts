import type { FastifyInstance } from 'fastify';
import { verifyIngestSecret } from '../auth/sharedSecret.js';
import type { StateStore } from '../state/store.js';
import type { PubSubPublisher } from '../pubsub/publisher.js';
import {
    PlayerBagSchema,
    PlayerSwitchSchema,
    PlayerTeamSchema,
    PokedexProgressSchema,
    SessionEndSchema,
} from '../types/payloads.js';

interface Deps {
    store: StateStore;
    publisher: PubSubPublisher;
}

interface ChannelParams {
    channelId: string;
}

export async function registerIngestRoutes(app: FastifyInstance, deps: Deps): Promise<void> {
    app.addHook('preHandler', async (req, reply) => {
        if (!req.url.startsWith('/ingest/')) return;
        await verifyIngestSecret(req, reply);
    });

    app.post<{ Params: ChannelParams }>('/ingest/session/:channelId/team', async (req, reply) => {
        const parsed = PlayerTeamSchema.safeParse(req.body);
        if (!parsed.success) {
            return reply.code(400).send({ error: 'invalid_payload', issues: parsed.error.issues });
        }
        deps.store.updateTeam(req.params.channelId, parsed.data);
        deps.publisher.publish(req.params.channelId, { type: 'team_update', payload: parsed.data });
        return { ok: true };
    });

    app.post<{ Params: ChannelParams }>('/ingest/session/:channelId/bag', async (req, reply) => {
        const parsed = PlayerBagSchema.safeParse(req.body);
        if (!parsed.success) {
            return reply.code(400).send({ error: 'invalid_payload', issues: parsed.error.issues });
        }
        deps.store.updateBag(req.params.channelId, parsed.data);
        deps.publisher.publish(req.params.channelId, { type: 'bag_update', payload: parsed.data });
        return { ok: true };
    });

    app.post<{ Params: ChannelParams }>('/ingest/session/:channelId/pokedex', async (req, reply) => {
        const parsed = PokedexProgressSchema.safeParse(req.body);
        if (!parsed.success) {
            return reply.code(400).send({ error: 'invalid_payload', issues: parsed.error.issues });
        }
        deps.store.updatePokedex(req.params.channelId, parsed.data);
        deps.publisher.publish(req.params.channelId, { type: 'pokedex_update', payload: parsed.data });
        return { ok: true };
    });

    app.post<{ Params: ChannelParams }>('/ingest/session/:channelId/player-switch', async (req, reply) => {
        const parsed = PlayerSwitchSchema.safeParse(req.body);
        if (!parsed.success) {
            return reply.code(400).send({ error: 'invalid_payload', issues: parsed.error.issues });
        }
        deps.store.setActivePlayer(req.params.channelId, parsed.data.active_player_id);
        deps.publisher.publish(req.params.channelId, { type: 'player_switch', payload: parsed.data });
        return { ok: true };
    });

    app.post<{ Params: ChannelParams }>('/ingest/session/:channelId/session-end', async (req, reply) => {
        const parsed = SessionEndSchema.safeParse(req.body ?? {});
        if (!parsed.success) {
            return reply.code(400).send({ error: 'invalid_payload', issues: parsed.error.issues });
        }
        deps.store.endSession(req.params.channelId);
        deps.publisher.publish(req.params.channelId, { type: 'session_end', payload: parsed.data });
        return { ok: true };
    });
}
