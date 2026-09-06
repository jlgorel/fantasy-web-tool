import {
  ManualRosterLimits,
  ManualRosterPlayer,
  ManualRosterSlot,
} from '../types/player';

export const DEFAULT_MANUAL_ROSTER_LIMITS: ManualRosterLimits = {
  QB: 1,
  RB: 2,
  WR: 2,
  TE: 1,
  REC_FLEX: 0,
  FLEX: 1,
  SUPER_FLEX: 0,
  DEF: 1,
  K: 1,
  BN: 6,
};

export const MANUAL_SLOT_ORDER: ManualRosterSlot[] = [
  'QB', 'RB', 'WR', 'TE', 'REC_FLEX', 'FLEX', 'SUPER_FLEX', 'DEF', 'K', 'BN',
];

const eligibilityOrder = (position: string): ManualRosterSlot[] => {
  if (position === 'QB') return ['QB', 'SUPER_FLEX', 'BN'];
  if (position === 'RB') return ['RB', 'FLEX', 'SUPER_FLEX', 'BN'];
  if (position === 'WR') return ['WR', 'REC_FLEX', 'FLEX', 'SUPER_FLEX', 'BN'];
  if (position === 'TE') return ['TE', 'REC_FLEX', 'FLEX', 'SUPER_FLEX', 'BN'];
  if (position === 'DEF') return ['DEF', 'BN'];
  if (position === 'K') return ['K', 'BN'];
  return ['BN'];
};

export const availableSlotsForPosition = (
  position: string,
  limits: ManualRosterLimits,
): ManualRosterSlot[] => eligibilityOrder(position).filter((slot) => limits[slot] > 0);

export const findAutomaticSlot = (
  position: string,
  assignedPlayers: ManualRosterPlayer[],
  limits: ManualRosterLimits,
): ManualRosterSlot | null => {
  const counts = assignedPlayers.reduce<Partial<Record<ManualRosterSlot, number>>>((out, player) => {
    out[player.slot] = (out[player.slot] ?? 0) + 1;
    return out;
  }, {});
  return eligibilityOrder(position).find((slot) => (counts[slot] ?? 0) < limits[slot]) ?? null;
};

export const autoAssignManualPlayers = (
  players: ManualRosterPlayer[],
  limits: ManualRosterLimits,
): { players: ManualRosterPlayer[]; overflow: ManualRosterPlayer[] } => {
  const assigned: ManualRosterPlayer[] = [];
  const overflow: ManualRosterPlayer[] = [];
  players.forEach((player) => {
    const slot = findAutomaticSlot(player.cached_position, assigned, limits);
    if (slot) assigned.push({ ...player, slot });
    else overflow.push(player);
  });
  return { players: assigned, overflow };
};

export const sortManualRosterPlayers = (players: ManualRosterPlayer[]) => [...players].sort(
  (left, right) => {
    const slotDelta = MANUAL_SLOT_ORDER.indexOf(left.slot) - MANUAL_SLOT_ORDER.indexOf(right.slot);
    if (slotDelta) return slotDelta;
    const positionDelta = left.cached_position.localeCompare(right.cached_position);
    return positionDelta || left.cached_name.localeCompare(right.cached_name);
  },
);

export const manualSlotLabel = (slot: ManualRosterSlot): string => ({
  QB: 'QB',
  RB: 'RB',
  WR: 'WR',
  TE: 'TE',
  REC_FLEX: 'W/T',
  FLEX: 'W/R/T',
  SUPER_FLEX: 'Superflex',
  DEF: 'Defense',
  K: 'Kicker',
  BN: 'Bench',
}[slot]);