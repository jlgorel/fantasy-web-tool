import {
  avoidedPlayerIds,
  customDraftStorageKey,
  customValueMap,
  loadCustomDraftSettings,
  parseCsvRows,
  previewCustomValuesCsv,
  saveCustomDraftSettings,
} from './customDraftValues';
import { CustomDraftSettings, RankingsPlayerRow } from '../types/draft';

const players: RankingsPlayerRow[] = [
  { player_id: '1', name: 'Ja\'Marr Chase', pos: 'WR', vbd: 100 },
  { player_id: '2', name: 'Bijan Robinson', pos: 'RB', vbd: 90 },
  { player_id: '3', name: 'Marvin Harrison Jr.', pos: 'WR', vbd: 80 },
];

it('parses quoted CSV fields and escaped quotes', () => {
  expect(parseCsvRows('name,value\n"Last, First",12\n"A ""Nickname"" B",4')).toEqual([
    ['name', 'value'],
    ['Last, First', '12'],
    ['A "Nickname" B', '4'],
  ]);
});

it('matches exact normalized names, suffixes and player ids', () => {
  const preview = previewCustomValuesCsv(
    'Player Name,Position,VORP,player_id\nJa’Marr Chase,WR,123,\nMarvin Harrison,WR,44,\nwrong name,RB,55,2',
    players,
  );
  expect(preview.errors).toEqual([]);
  expect(preview.matches.map((m) => [m.player_id, m.value])).toEqual([
    ['1', 123],
    ['3', 44],
    ['2', 55],
  ]);
});

it('reports malformed, unmatched and duplicate rows without guessing', () => {
  const preview = previewCustomValuesCsv(
    'name,pos,value\nUnknown,RB,20\nBijan Robinson,RB,nope\nBijan Robinson,RB,40\nBijan Robinson,RB,41',
    players,
  );
  expect(preview.matches[0].error).toBe('No exact player match');
  expect(preview.matches[1].error).toContain('Invalid value');
  expect(preview.matches[2].player_id).toBe('2');
  expect(preview.matches[3].error).toBe('Duplicate player in CSV');
});

it('persists sanitized values and avoid preferences', () => {
  const memory: Record<string, string> = {};
  const storage = {
    getItem: (key: string) => memory[key] ?? null,
    setItem: (key: string, value: string) => { memory[key] = value; },
  };
  const key = customDraftStorageKey('2026', 12, 0.5, false);
  const settings: CustomDraftSettings = {
    version: 1,
    updated_at: '2026-08-08T00:00:00Z',
    entries: {
      '1': { value: 111, source: 'upload' },
      '2': { avoid: true, source: 'manual' },
    },
  };
  saveCustomDraftSettings(storage, key, settings);
  const loaded = loadCustomDraftSettings(storage, key);
  expect(customValueMap(loaded)).toEqual({ '1': 111 });
  expect(avoidedPlayerIds(loaded)).toEqual(['2']);
});
