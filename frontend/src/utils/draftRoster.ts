import { RankingsPlayerRow } from '../types/draft';

export const FLEX_ELIGIBLE: Record<string, string[]> = {
  FLEX: ['RB', 'WR', 'TE'],
  REC_FLEX: ['WR', 'TE'],
  WRRB_FLEX: ['RB', 'WR'],
  SUPER_FLEX: ['QB', 'RB', 'WR', 'TE'],
};

export const POS_COLOR: Record<string, string> = {
  QB: 'purple', RB: 'green', WR: 'blue', TE: 'orange',
};

export const SLOT_COLOR: Record<string, string> = {
  QB: 'purple', RB: 'green', WR: 'blue', TE: 'orange',
  FLEX: 'teal', REC_FLEX: 'cyan', WRRB_FLEX: 'cyan',
  SUPER_FLEX: 'pink', BN: 'gray',
};

export interface RosterSlot {
  type: string;
  label: string;
  player?: RankingsPlayerRow;
}

/** Assign a roster to dedicated/flex starters, then bench, by value. */
export function buildRosterSlots(
  rosterIds: string[],
  byId: Record<string, RankingsPlayerRow>,
  starterSlots: Record<string, number>,
  rounds: number,
): RosterSlot[] {
  const byPos: Record<string, RankingsPlayerRow[]> = {};
  rosterIds.forEach((pid) => {
    const player = byId[pid];
    if (!player) return;
    (byPos[player.pos] ||= []).push(player);
  });
  Object.values(byPos).forEach((positionPlayers) => positionPlayers.sort(
    (a, b) => (b.vbd ?? -9999) - (a.vbd ?? -9999),
  ));

  const used = new Set<string>();
  const take = (positions: string[]): RankingsPlayerRow | undefined => {
    let best: RankingsPlayerRow | undefined;
    positions.forEach((pos) => {
      const candidate = (byPos[pos] || []).find((p) => !used.has(p.player_id));
      if (candidate && (!best || (candidate.vbd ?? -9999) > (best.vbd ?? -9999))) {
        best = candidate;
      }
    });
    if (best) used.add(best.player_id);
    return best;
  };

  const order = ['QB', 'RB', 'WR', 'TE', 'FLEX', 'REC_FLEX', 'WRRB_FLEX', 'SUPER_FLEX'];
  const slots: RosterSlot[] = [];
  let starterCount = 0;
  order.forEach((type) => {
    const count = starterSlots[type] || 0;
    for (let i = 0; i < count; i += 1) {
      starterCount += 1;
      slots.push({
        type,
        label: type === 'SUPER_FLEX' ? 'SF' : type === 'REC_FLEX' ? 'RF' : type,
        player: take(FLEX_ELIGIBLE[type] || [type]),
      });
    }
  });

  const leftovers = rosterIds
    .map((pid) => byId[pid])
    .filter((player): player is RankingsPlayerRow => !!player && !used.has(player.player_id));
  const benchCount = Math.max(rounds - starterCount, leftovers.length);
  for (let i = 0; i < benchCount; i += 1) {
    slots.push({ type: 'BN', label: 'BN', player: leftovers[i] });
  }
  return slots;
}
