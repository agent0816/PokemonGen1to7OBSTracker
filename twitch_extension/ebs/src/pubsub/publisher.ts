import type { FastifyBaseLogger } from 'fastify';
import { request } from 'undici';
import { loadConfig } from '../config.js';
import { signExternalJwt } from '../auth/jwt.js';
import type { PubSubMessage } from '../types/payloads.js';
import { PubSubRateLimiter } from './rateLimiter.js';

const HELIX_PUBSUB_URL = 'https://api.twitch.tv/helix/extensions/pubsub';
// Twitch-Extension-PubSub: harte Payload-Grenze von 5 KB pro Nachricht.
const MAX_PAYLOAD_BYTES = 5 * 1024;

export class PubSubPublisher {
    private readonly limiter: PubSubRateLimiter;

    constructor(private readonly log: FastifyBaseLogger) {
        this.limiter = new PubSubRateLimiter((channelId, msg) => this.sendNow(channelId, msg), log);
    }

    // Öffentlicher Einstieg — geht immer durch den Rate-Limiter.
    publish(channelId: string, msg: PubSubMessage): void {
        this.limiter.enqueue(channelId, msg);
    }

    private async sendNow(channelId: string, msg: PubSubMessage): Promise<void> {
        const cfg = loadConfig();
        const messageJson = JSON.stringify(msg);
        const size = Buffer.byteLength(messageJson, 'utf8');
        if (size > MAX_PAYLOAD_BYTES) {
            // Ohne Diff-/Chunk-Strategie können wir nichts sinnvoll tun außer
            // verwerfen und laut loggen. Sobald Payloads regelmäßig zu groß
            // werden, muss ein Delta-Update her.
            this.log.error({ channelId, type: msg.type, size }, 'PubSub-Payload > 5 KB, wird verworfen');
            return;
        }

        if (cfg.pubsubDryRun) {
            this.log.info({ channelId, type: msg.type, size }, '[dry-run] PubSub-Publish übersprungen');
            return;
        }

        const bearer = signExternalJwt(channelId);
        const body = {
            target: ['broadcast'],
            broadcaster_id: channelId,
            is_global_broadcast: false,
            message: messageJson,
        };
        const res = await request(HELIX_PUBSUB_URL, {
            method: 'POST',
            headers: {
                'Authorization': `Bearer ${bearer}`,
                'Client-Id': cfg.twitch.clientId,
                'Content-Type': 'application/json',
            },
            body: JSON.stringify(body),
        });
        if (res.statusCode >= 300) {
            const text = await res.body.text();
            this.log.error({ channelId, status: res.statusCode, body: text }, 'Twitch PubSub-Publish fehlgeschlagen');
            return;
        }
        this.log.debug({ channelId, type: msg.type, size }, 'PubSub-Publish OK');
    }
}
