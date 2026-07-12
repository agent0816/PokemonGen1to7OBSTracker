import { z } from 'zod';

// Slot-Struktur ist am Payload aus backend/classes/overlay_server.py::_build_full_payload
// orientiert und bewusst schmal gehalten, damit ein volles 6er-Team unter 5 KB bleibt.
// Frontend-Only-Felder (sprite_url, item_url, badge_url) sind hier nicht enthalten —
// die Extension löst Sprite-URLs anhand von dexnr/form/shiny/female clientseitig
// auf (Assets aus dem Extension-Bundle oder von einer whitelisteten Domain).

export const StatusSchema = z.object({
    freeze: z.boolean().optional(),
    burn: z.boolean().optional(),
    para: z.boolean().optional(),
    poison: z.boolean().optional(),
    toxic: z.boolean().optional(),
    sleep: z.boolean().optional(),
}).partial();

export const TeamSlotSchema = z.object({
    slot: z.number().int().min(0).max(5),
    dexnr: z.number().int().min(0),
    identity_key: z.string(),
    nickname: z.string().optional(),
    lvl: z.number().int().min(0).max(255).optional(),
    shiny: z.boolean().optional(),
    female: z.boolean().optional(),
    form: z.string().optional(),
    item: z.string().optional(),
    cur_hp: z.number().int().min(0).optional(),
    max_hp: z.number().int().min(0).optional(),
    status: StatusSchema.optional(),
    link_state: z.string().optional(),
    link_id: z.number().int().optional(),
    link_hidden: z.boolean().optional(),
    personality: z.number().int().optional(),
});
export type TeamSlot = z.infer<typeof TeamSlotSchema>;

export const BadgeSchema = z.object({
    index: z.number().int().min(0),
    earned: z.boolean(),
});
export type Badge = z.infer<typeof BadgeSchema>;

export const PlayerTeamSchema = z.object({
    player_id: z.number().int(),
    player_name: z.string(),
    edition: z.number().int(),
    badge_region: z.string(),
    badges_raw: z.number().int(),
    badges: z.array(BadgeSchema),
    team: z.array(TeamSlotSchema).max(6),
});
export type PlayerTeam = z.infer<typeof PlayerTeamSchema>;

export const BagItemSchema = z.object({
    id: z.union([z.string(), z.number()]),
    count: z.number().int().min(0),
});
export type BagItem = z.infer<typeof BagItemSchema>;

export const PlayerBagSchema = z.object({
    player_id: z.number().int(),
    pockets: z.record(z.string(), z.array(BagItemSchema)),
});
export type PlayerBag = z.infer<typeof PlayerBagSchema>;

export const PokedexProgressSchema = z.object({
    player_id: z.number().int(),
    // Pro Generation: {seen, caught}
    generations: z.record(
        z.string(),
        z.object({
            seen: z.number().int().min(0),
            caught: z.number().int().min(0),
        }),
    ),
});
export type PokedexProgress = z.infer<typeof PokedexProgressSchema>;

export const PlayerSwitchSchema = z.object({
    active_player_id: z.number().int(),
});
export type PlayerSwitch = z.infer<typeof PlayerSwitchSchema>;

export const SessionEndSchema = z.object({
    reason: z.string().optional(),
});
export type SessionEnd = z.infer<typeof SessionEndSchema>;

// Kompletter State pro Channel — wird beim GET /state/:channelId ausgeliefert.
// PubSub-Nachrichten sind Deltas (siehe PubSubMessage).
export interface ChannelState {
    channel_id: string;
    updated_at: number;
    teams: Record<number, PlayerTeam>;
    bags: Record<number, PlayerBag>;
    pokedex: Record<number, PokedexProgress>;
    active_player_id: number | null;
    session_ended: boolean;
}

export type PubSubMessage =
    | { type: 'team_update'; payload: PlayerTeam }
    | { type: 'bag_update'; payload: PlayerBag }
    | { type: 'pokedex_update'; payload: PokedexProgress }
    | { type: 'player_switch'; payload: PlayerSwitch }
    | { type: 'session_end'; payload: SessionEnd };

export type PubSubMessageType = PubSubMessage['type'];
