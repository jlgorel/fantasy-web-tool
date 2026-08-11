import { RankingsResponse } from '../types/draft';

type ValueProfile = NonNullable<
  NonNullable<RankingsResponse['sources']>['values']
>['profile'];

export const RESERVED_K_DEF_ROUNDS = 2;

export function normalizedStarterSlots(
  starterSlots: Record<string, number>,
  superflex: boolean,
): Record<string, number> {
  const out: Record<string, number> = {};
  Object.entries(starterSlots).forEach(([slot, raw]) => {
    const count = Number(raw) || 0;
    if (count > 0) out[slot] = count;
  });
  if (superflex) out.SUPER_FLEX = 1;
  return out;
}

export function profileStorageSignature(
  starterSlots: Record<string, number>,
  superflex: boolean,
  benchSize: number,
  passingTd: number,
): string {
  const slots = normalizedStarterSlots(starterSlots, superflex);
  const slotPart = Object.entries(slots)
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([slot, count]) => `${slot}${count}`)
    .join('-');
  return `${slotPart}:bn${benchSize}:ptd${passingTd}`;
}

export function centralizedProfileId(
  starterSlots: Record<string, number>,
  superflex: boolean,
  benchSize: number,
  passingTd: number,
): string | null {
  const qb = Number(starterSlots.QB || 0);
  const rb = Number(starterSlots.RB || 0);
  const wr = Number(starterSlots.WR || 0);
  const te = Number(starterSlots.TE || 0);
  const flex = Number(starterSlots.FLEX || 0);
  const unsupportedFlex = ['REC_FLEX', 'WRRB_FLEX'].some(
    (slot) => Number(starterSlots[slot] || 0) > 0,
  );
  if (
    qb !== 1 || rb !== 2 || ![2, 3].includes(wr) || te !== 1
    || ![1, 2].includes(flex) || ![5, 6, 7].includes(benchSize)
    || ![4, 6].includes(passingTd) || unsupportedFlex
  ) return null;
  return `qb1-rb2-wr${wr}-te1-flex${flex}-bn${benchSize}-ptd${passingTd}`;
}

export function providerProfileMatches(
  profile: ValueProfile,
  starterSlots: Record<string, number>,
  benchSize: number,
  passingTd: number,
): boolean {
  if (!profile) return true;
  if (profile.passing_td != null && profile.passing_td !== passingTd) return false;
  if (profile.bench_size != null && profile.bench_size !== benchSize) return false;
  const expected = profile.starters || {};
  const compared = new Set([...Object.keys(expected), 'QB', 'RB', 'WR', 'TE', 'FLEX']);
  return Array.from(compared).every(
    (slot) => Number(expected[slot] || 0) === Number(starterSlots[slot] || 0),
  );
}

export function derivedRounds(
  starterSlots: Record<string, number>,
  superflex: boolean,
  benchSize: number,
): number {
  const skillStarters = Object.values(normalizedStarterSlots(starterSlots, superflex))
    .reduce((total, count) => total + count, 0);
  return skillStarters + benchSize + RESERVED_K_DEF_ROUNDS;
}
