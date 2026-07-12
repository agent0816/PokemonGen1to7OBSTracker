import type { FastifyInstance } from 'fastify';

export async function registerHealthRoutes(app: FastifyInstance): Promise<void> {
    app.get('/health', async () => ({
        status: 'ok',
        uptime_s: Math.round(process.uptime()),
        ts: new Date().toISOString(),
    }));
}
