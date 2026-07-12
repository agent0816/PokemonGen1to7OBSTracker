import { loadConfig } from './config.js';
import { buildServer } from './server.js';

async function main(): Promise<void> {
    const cfg = loadConfig();
    const app = await buildServer();
    try {
        await app.listen({ port: cfg.port, host: cfg.host });
    } catch (err) {
        app.log.error({ err }, 'Server-Start fehlgeschlagen');
        process.exit(1);
    }

    const shutdown = async (signal: string): Promise<void> => {
        app.log.info({ signal }, 'Shutdown angefordert');
        try {
            await app.close();
            process.exit(0);
        } catch (err) {
            app.log.error({ err }, 'Shutdown fehlgeschlagen');
            process.exit(1);
        }
    };
    process.once('SIGINT', () => void shutdown('SIGINT'));
    process.once('SIGTERM', () => void shutdown('SIGTERM'));
}

void main();
