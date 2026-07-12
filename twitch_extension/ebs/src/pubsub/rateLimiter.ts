import type { FastifyBaseLogger } from 'fastify';
import type { PubSubMessage, PubSubMessageType } from '../types/payloads.js';

// Twitch-Extension-PubSub-Limit: max. ~1 Nachricht pro Sekunde pro Topic.
// Strategie: pro (channelId, message-type) merken wir uns nur die neueste
// Nachricht ("last-write-wins"). Ein Timer feuert höchstens einmal pro
// MIN_INTERVAL_MS und schickt dann den aktuellen Stand raus. Neuere Payloads,
// die während der Wartezeit reinkommen, ersetzen den vorherigen — so werden
// Bursts (z.B. während eines Kampfs) sauber zusammengefasst.

const MIN_INTERVAL_MS = 1_000;

type SendFn = (channelId: string, msg: PubSubMessage) => Promise<void>;

interface Pending {
    msg: PubSubMessage;
}

interface ChannelBucket {
    // pending pro Message-Typ (team_update / bag_update / ...).
    pending: Map<PubSubMessageType, Pending>;
    // Zeitpunkt (ms), zu dem der nächste Send frühestens erlaubt ist.
    nextAllowed: number;
    timer: NodeJS.Timeout | null;
}

export class PubSubRateLimiter {
    private readonly buckets = new Map<string, ChannelBucket>();

    constructor(
        private readonly send: SendFn,
        private readonly log: FastifyBaseLogger,
    ) {}

    enqueue(channelId: string, msg: PubSubMessage): void {
        const bucket = this.getBucket(channelId);
        // Neuere Nachricht ersetzt ältere desselben Typs — Twitch bekommt
        // immer nur den letzten Stand.
        bucket.pending.set(msg.type, { msg });
        this.scheduleFlush(channelId, bucket);
    }

    private getBucket(channelId: string): ChannelBucket {
        let b = this.buckets.get(channelId);
        if (!b) {
            b = { pending: new Map(), nextAllowed: 0, timer: null };
            this.buckets.set(channelId, b);
        }
        return b;
    }

    private scheduleFlush(channelId: string, bucket: ChannelBucket): void {
        if (bucket.timer) return; // schon geplant
        const now = Date.now();
        const wait = Math.max(0, bucket.nextAllowed - now);
        bucket.timer = setTimeout(() => {
            bucket.timer = null;
            void this.flush(channelId, bucket);
        }, wait);
    }

    private async flush(channelId: string, bucket: ChannelBucket): Promise<void> {
        if (bucket.pending.size === 0) return;
        const drained = Array.from(bucket.pending.values());
        bucket.pending.clear();
        bucket.nextAllowed = Date.now() + MIN_INTERVAL_MS;

        for (const item of drained) {
            try {
                await this.send(channelId, item.msg);
            } catch (err) {
                this.log.error({ err, channelId, type: item.msg.type }, 'PubSub-Send fehlgeschlagen');
            }
        }

        // Falls während des Sends neue Payloads reingekommen sind: erneut planen.
        if (bucket.pending.size > 0) {
            this.scheduleFlush(channelId, bucket);
        }
    }
}
