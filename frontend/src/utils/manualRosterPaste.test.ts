import { ManualCatalogPlayer } from '../types/player';
import { previewManualRosterPaste } from './manualRosterPaste';

const catalog: ManualCatalogPlayer[] = [
  { player_id: '4984', name: 'Josh Allen', position: 'QB', team: 'BUF' },
  { player_id: '6794', name: 'Justin Jefferson', position: 'WR', team: 'MIN' },
  { player_id: '7564', name: "Ja'Marr Chase", position: 'WR', team: 'CIN' },
  { player_id: '9509', name: 'Bijan Robinson', position: 'RB', team: 'ATL' },
  { player_id: '4046', name: 'Patrick Mahomes II', position: 'QB', team: 'KC' },
  { player_id: '5967', name: 'Tony Pollard', position: 'RB', team: 'TEN' },
  { player_id: '8136', name: 'Rachaad White', position: 'RB', team: 'TB' },
  { player_id: '7525', name: 'DeVonta Smith', position: 'WR', team: 'PHI' },
  { player_id: '7553', name: 'Kyle Pitts', position: 'TE', team: 'ATL' },
  { player_id: '7839', name: 'Evan McPherson', position: 'K', team: 'CIN' },
  { player_id: 'BUF', name: 'Buffalo Bills', position: 'DEF', team: 'BUF' },
  { player_id: '9488', name: 'Jaxon Smith-Njigba', position: 'WR', team: 'SEA' },
  { player_id: '4068', name: 'Mike Williams', position: 'WR', team: null },
  { player_id: '9990', name: 'Marvin Williams', position: 'WR', team: null },
];

it('finds multiple exact names in copied webpage text', () => {
  const preview = previewManualRosterPaste(
    'My Team\nQB Josh Allen BUF\nWR Ja’Marr Chase CIN\nRB Bijan Robinson ATL',
    catalog,
  );
  expect(preview.matches.map((match) => match.player.player_id).sort()).toEqual([
    '4984', '7564', '9509',
  ]);
  expect(preview.matches.every((match) => match.kind === 'exact')).toBe(true);
});

it('accepts only a high-confidence fuzzy typo', () => {
  const preview = previewManualRosterPaste('WR Justin Jeffersno MIN', catalog);
  expect(preview.matches).toHaveLength(1);
  expect(preview.matches[0].player.player_id).toBe('6794');
  expect(preview.matches[0].kind).toBe('fuzzy');
});

it('does not guess low-confidence copied text', () => {
  const preview = previewManualRosterPaste('Team Settings Schedule Matchup', catalog);
  expect(preview.matches).toEqual([]);
});

it('matches abbreviated roster rows, suffixes, punctuation, and D/ST labels', () => {
  const preview = previewManualRosterPaste([
    'QB\tP. Mahomes\t10',
    'RB\tT. Pollard\t7',
    'RB\tR. White\t11',
    'WR\tJ. Jefferson\t5',
    'WR\tD. Smith\t10',
    'TE\tK. Pitts\t12',
    'D/ST\tBills D/ST\t13',
    'K\tE. McPherson\t12',
    'WR\tJ. Smith-Njigba\t5',
    'FLEX\tM. Williams\t5',
    'BE\tEmpty\t—',
  ].join('\n'), catalog);

  expect(preview.matches.map((match) => match.player.player_id).sort()).toEqual([
    '4046', '5967', '7525', '7553', '7839', '8136', '9488', 'BUF', '6794',
  ].sort());
  expect(preview.ambiguous).toContain('flex m williams 5');
});