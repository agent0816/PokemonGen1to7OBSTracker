import jwt from 'jsonwebtoken';
import type { FastifyReply, FastifyRequest } from 'fastify';
import { loadConfig } from '../config.js';

// Struktur des Twitch-Extension-JWT (nur die Felder, die wir brauchen).
// Twitch signiert das mit dem Extension-Secret (HS256), enthält u.a.
// channel_id, user_id (optional, wenn Zuschauer identity geteilt hat) und role.
export interface TwitchExtensionJwt {
    exp: number;
    channel_id: string;
    user_id?: string;
    opaque_user_id: string;
    role: 'broadcaster' | 'moderator' | 'viewer' | 'external';
    pubsub_perms?: {
        listen?: string[];
        send?: string[];
    };
}

declare module 'fastify' {
    interface FastifyRequest {
        twitchJwt?: TwitchExtensionJwt;
    }
}

function extractBearer(header: string | undefined): string | null {
    if (!header) return null;
    const [scheme, token] = header.split(' ', 2);
    if (!scheme || scheme.toLowerCase() !== 'bearer' || !token) return null;
    return token.trim();
}

export async function verifyTwitchJwt(request: FastifyRequest, reply: FastifyReply): Promise<void> {
    const token = extractBearer(request.headers.authorization);
    if (!token) {
        return reply.code(401).send({ error: 'missing_bearer_token' });
    }
    const cfg = loadConfig();
    try {
        const decoded = jwt.verify(token, cfg.twitch.secret, {
            algorithms: ['HS256'],
        }) as TwitchExtensionJwt;
        if (!decoded.channel_id) {
            return reply.code(401).send({ error: 'jwt_missing_channel_id' });
        }
        request.twitchJwt = decoded;
    } catch (err) {
        request.log.warn({ err }, 'JWT-Verifikation fehlgeschlagen');
        return reply.code(401).send({ error: 'invalid_jwt' });
    }
}

// Signiert ein "external"-JWT, mit dem das EBS bei der Helix-PubSub-API
// als Extension auftritt. Muss der Twitch-Doku entsprechen:
// exp, user_id (=owner-id), role="external", channel_id, pubsub_perms.send=["broadcast"].
export function signExternalJwt(channelId: string, ttlSeconds = 60): string {
    const cfg = loadConfig();
    const payload = {
        exp: Math.floor(Date.now() / 1000) + ttlSeconds,
        user_id: cfg.twitch.ownerId,
        role: 'external' as const,
        channel_id: channelId,
        pubsub_perms: {
            send: ['broadcast'],
        },
    };
    return jwt.sign(payload, cfg.twitch.secret, { algorithm: 'HS256' });
}
