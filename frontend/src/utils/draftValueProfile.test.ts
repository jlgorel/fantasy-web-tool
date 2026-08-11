import {
  centralizedProfileId,
  derivedRounds,
  profileStorageSignature,
  providerProfileMatches,
} from './draftValueProfile';

const profile = {
  passing_td: 4,
  bench_size: 6,
  starters: { QB: 1, RB: 2, WR: 2, TE: 1, FLEX: 1 },
  superflex_mode: '2qb',
};

it('matches only the exact published starter, bench and passing-TD profile', () => {
  const starters = { QB: 1, RB: 2, WR: 2, TE: 1, FLEX: 1 };
  expect(providerProfileMatches(profile, starters, 6, 4)).toBe(true);
  expect(providerProfileMatches(profile, { ...starters, RB: 3 }, 6, 4)).toBe(false);
  expect(providerProfileMatches(profile, starters, 7, 4)).toBe(false);
  expect(providerProfileMatches(profile, starters, 6, 6)).toBe(false);
});

it('selects only curated centralized profiles', () => {
  expect(centralizedProfileId(
    { QB: 1, RB: 2, WR: 3, TE: 1, FLEX: 2 }, false, 7, 6,
  )).toBe('qb1-rb2-wr3-te1-flex2-bn7-ptd6');
  expect(centralizedProfileId(
    { QB: 1, RB: 3, WR: 3, TE: 1, FLEX: 2 }, false, 7, 6,
  )).toBeNull();
  expect(centralizedProfileId(
    { QB: 2, RB: 2, WR: 3, TE: 1, FLEX: 2 }, true, 7, 6,
  )).toBeNull();
});

it('keys browser values to the exact profile and derives K/DEF-inclusive rounds', () => {
  const starters = { QB: 1, RB: 2, WR: 2, TE: 1, FLEX: 1 };
  const base = profileStorageSignature(starters, false, 6, 4);
  expect(base).toContain('QB1');
  expect(base).toContain('bn6:ptd4');
  expect(profileStorageSignature({ ...starters, FLEX: 2 }, false, 6, 4)).not.toBe(base);
  expect(derivedRounds(starters, false, 6)).toBe(15);
  expect(derivedRounds(starters, true, 6)).toBe(16);
});
