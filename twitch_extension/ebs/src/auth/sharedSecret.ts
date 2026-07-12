import { timingSafeEqual } from 'node:crypto';
import type { FastifyReply, FastifyRequest } from 'fastify';
import { loadConfig } from '../config.js';

function extractBearer(header: string | undefined): string | null {
    if (!header) return null;
    const [scheme, token] = header.split(' ', 2);
    if (!scheme || scheme.toLowerCase() !== 'bearer' || !token) return null;
    return token.trim();
}

function safeCompare(a: string, b: string): boolean {
    // Beide Strings auf gleiche Länge padden, damit timingSafeEqual nicht wirft;
    // ungleiche Länge ist automatisch false.
    if (a.length !== b.length) return false;
    return timingSafeEqual(Buffer.from(a, 'utf8'), Buffer.from(b, 'utf8'));
}

export async function verifyIngestSecret(request: FastifyRequest, reply: FastifyReply): Promise<void> {
    const provided = extractBearer(request.headers.authorization);
    if (!provided) {
        return reply.code(401).send({ error: 'missing_bearer_token' });
    }
    const cfg = loadConfig();
    if (!safeCompare(provided, cfg.ingestSharedSecret)) {
        request.log.warn({ ip: request.ip }, 'Ingest-Auth mit falschem Secret abgelehnt');
        return reply.code(401).send({ error: 'invalid_secret' });
    }
}
