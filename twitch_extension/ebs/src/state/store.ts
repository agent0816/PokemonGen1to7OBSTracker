import type {
    ChannelState,
    PlayerBag,
    PlayerTeam,
    PokedexProgress,
} from '../types/payloads.js';

// In-Memory-Store. Bei Neustart des EBS ist der State weg — Tracker sendet
// beim nächsten Update ohnehin den aktuellen Stand. Ein persistenter Store
// (Redis o. Ä.) ist ausdrücklich Roadmap, siehe README.

const emptyChannel = (channelId: string): ChannelState => ({
    channel_id: channelId,
    updated_at: Date.now(),
    teams: {},
    bags: {},
    pokedex: {},
    active_player_id: null,
    session_ended: false,
});

export class StateStore {
    private readonly channels = new Map<string, ChannelState>();

    get(channelId: string): ChannelState {
        let s = this.channels.get(channelId);
        if (!s) {
            s = emptyChannel(channelId);
            this.channels.set(channelId, s);
        }
        return s;
    }

    updateTeam(channelId: string, team: PlayerTeam): ChannelState {
        const s = this.get(channelId);
        s.teams[team.player_id] = team;
        s.session_ended = false;
        s.updated_at = Date.now();
        return s;
    }

    updateBag(channelId: string, bag: PlayerBag): ChannelState {
        const s = this.get(channelId);
        s.bags[bag.player_id] = bag;
        s.session_ended = false;
        s.updated_at = Date.now();
        return s;
    }

    updatePokedex(channelId: string, dex: PokedexProgress): ChannelState {
        const s = this.get(channelId);
        s.pokedex[dex.player_id] = dex;
        s.session_ended = false;
        s.updated_at = Date.now();
        return s;
    }

    setActivePlayer(channelId: string, playerId: number): ChannelState {
        const s = this.get(channelId);
        s.active_player_id = playerId;
        s.updated_at = Date.now();
        return s;
    }

    endSession(channelId: string): ChannelState {
        const s = this.get(channelId);
        s.session_ended = true;
        s.updated_at = Date.now();
        return s;
    }
}
