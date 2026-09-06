import { v4 as uuidv4 } from 'uuid';
import {
  ManualRoster,
  ManualRosterPlayer,
  ManualRosterSlot,
  ManualRosterStore,
} from '../types/player';
import { DEFAULT_MANUAL_ROSTER_LIMITS } from './manualRosterSlots';

export const MANUAL_ROSTER_STORAGE_PREFIX = 'fantasy-web-tool-manual-rosters-v1';
const VALID_SLOTS = new Set<ManualRosterSlot>([
  'QB', 'RB', 'WR', 'TE', 'REC_FLEX', 'FLEX', 'SUPER_FLEX', 'K', 'DEF', 'BN',
]);

export interface StorageLike {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
}

export const manualRosterStorageKey = (browserId: string) =>
  `${MANUAL_ROSTER_STORAGE_PREFIX}:${browserId}`;

export const emptyManualRosterStore = (): ManualRosterStore => ({
  version: 1,
  updated_at: new Date().toISOString(),
  rosters: [],
});

const validScoring = (value: unknown) => {
  if (!value || typeof value !== 'object') return null;
  const scoring = value as Record<string, unknown>;
  if (![0, 0.5, 1].includes(scoring.ppr as number)) return null;
  if (![4, 6].includes(scoring.passing_td_points as number)) return null;
  return {
    ppr: scoring.ppr as 0 | 0.5 | 1,
    passing_td_points: scoring.passing_td_points as 4 | 6,
  };
};

const sanitizeLineupLimits = (value: unknown, players: ManualRosterPlayer[]) => {
  const raw = value && typeof value === 'object' ? value as Record<string, unknown> : {};
  const limits = { ...DEFAULT_MANUAL_ROSTER_LIMITS };
  (Object.keys(limits) as Array<keyof typeof limits>).forEach((slot) => {
    const candidate = Number(raw[slot]);
    const maximum = slot === 'BN' ? 30 : 10;
    if (Number.isInteger(candidate) && candidate >= 0 && candidate <= maximum) {
      limits[slot] = candidate;
    }
  });
  players.forEach((player) => {
    const assigned = players.filter((entry) => entry.slot === player.slot).length;
    limits[player.slot] = Math.max(limits[player.slot], assigned);
  });
  return limits;
};

const sanitizePlayer = (value: unknown): ManualRosterPlayer | null => {
  if (!value || typeof value !== 'object') return null;
  const player = value as Record<string, unknown>;
  const playerId = typeof player.player_id === 'string' ? player.player_id.trim() : '';
  const name = typeof player.cached_name === 'string' ? player.cached_name.trim() : '';
  const position = typeof player.cached_position === 'string'
    ? player.cached_position.trim().toUpperCase()
    : '';
  const slot = typeof player.slot === 'string' ? player.slot.toUpperCase() as ManualRosterSlot : 'BN';
  if (!playerId || !name || !position || !VALID_SLOTS.has(slot)) return null;
  return {
    player_id: playerId,
    cached_name: name,
    cached_position: position,
    cached_team: typeof player.cached_team === 'string' ? player.cached_team : null,
    slot,
  };
};

export const sanitizeManualRoster = (value: unknown): ManualRoster | null => {
  if (!value || typeof value !== 'object') return null;
  const raw = value as Record<string, unknown>;
  const id = typeof raw.id === 'string' ? raw.id.trim() : '';
  const name = typeof raw.name === 'string' ? raw.name.trim().slice(0, 100) : '';
  const scoring = validScoring(raw.scoring);
  if (!id || !name || !scoring || !Array.isArray(raw.players)) return null;

  const seen = new Set<string>();
  const players = raw.players
    .map(sanitizePlayer)
    .filter((player): player is ManualRosterPlayer => {
      if (!player || seen.has(player.player_id)) return false;
      seen.add(player.player_id);
      return true;
    })
    .slice(0, 50);
  const now = new Date().toISOString();
  return {
    id,
    name,
    created_at: typeof raw.created_at === 'string' ? raw.created_at : now,
    updated_at: typeof raw.updated_at === 'string' ? raw.updated_at : now,
    scoring,
    lineup_limits: sanitizeLineupLimits(raw.lineup_limits, players),
    players,
  };
};

export const loadManualRosterStore = (
  storage: StorageLike,
  key: string,
): ManualRosterStore => {
  try {
    const raw = storage.getItem(key);
    if (!raw) return emptyManualRosterStore();
    const parsed = JSON.parse(raw) as Record<string, unknown>;
    if (parsed.version !== 1 || !Array.isArray(parsed.rosters)) return emptyManualRosterStore();
    const rosters = parsed.rosters
      .map(sanitizeManualRoster)
      .filter((roster): roster is ManualRoster => roster !== null);
    return {
      version: 1,
      updated_at: typeof parsed.updated_at === 'string'
        ? parsed.updated_at
        : new Date().toISOString(),
      rosters,
    };
  } catch {
    return emptyManualRosterStore();
  }
};

export const saveManualRosterStore = (
  storage: StorageLike,
  key: string,
  store: ManualRosterStore,
): boolean => {
  try {
    storage.setItem(key, JSON.stringify(store));
    return true;
  } catch {
    return false;
  }
};

export const createManualRoster = (name = 'My Manual Team'): ManualRoster => {
  const now = new Date().toISOString();
  return {
    id: uuidv4(),
    name,
    created_at: now,
    updated_at: now,
    scoring: { ppr: 0.5, passing_td_points: 4 },
    lineup_limits: { ...DEFAULT_MANUAL_ROSTER_LIMITS },
    players: [],
  };
};

export const duplicateManualRoster = (roster: ManualRoster): ManualRoster => {
  const now = new Date().toISOString();
  return {
    ...roster,
    id: uuidv4(),
    name: `${roster.name} Copy`.slice(0, 100),
    created_at: now,
    updated_at: now,
    lineup_limits: { ...roster.lineup_limits },
    players: roster.players.map((player) => ({ ...player })),
  };
};

export const exportManualRoster = (roster: ManualRoster): string => JSON.stringify({
  schema: 'fantasy-web-tool-manual-roster-v1',
  roster,
}, null, 2);

export const importManualRosters = (text: string): ManualRoster[] => {
  const parsed = JSON.parse(text) as Record<string, unknown>;
  const candidates = parsed.schema === 'fantasy-web-tool-manual-roster-v1'
    ? [parsed.roster]
    : parsed.version === 1 && Array.isArray(parsed.rosters)
      ? parsed.rosters
      : [];
  const rosters = candidates
    .map(sanitizeManualRoster)
    .filter((roster): roster is ManualRoster => roster !== null)
    .map((roster) => ({
      ...roster,
      id: uuidv4(),
      name: `${roster.name} (Imported)`.slice(0, 100),
      updated_at: new Date().toISOString(),
    }));
  if (!rosters.length) throw new Error('No valid manual rosters found in JSON');
  return rosters;
};