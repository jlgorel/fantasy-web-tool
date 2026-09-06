import {
  createManualRoster,
  duplicateManualRoster,
  exportManualRoster,
  importManualRosters,
  loadManualRosterStore,
  manualRosterStorageKey,
  saveManualRosterStore,
} from './manualRosterStorage';

const memoryStorage = () => {
  const values: Record<string, string> = {};
  return {
    values,
    getItem: (key: string) => values[key] ?? null,
    setItem: (key: string, value: string) => { values[key] = value; },
  };
};

it('keys manual teams to the persistent browser identity', () => {
  expect(manualRosterStorageKey('browser-123')).toBe(
    'fantasy-web-tool-manual-rosters-v1:browser-123',
  );
});

it('persists and reloads a roster with cached player recovery fields', () => {
  const storage = memoryStorage();
  const key = manualRosterStorageKey('browser-123');
  const roster = createManualRoster('Family Team');
  roster.players.push({
    player_id: '4984',
    cached_name: 'Josh Allen',
    cached_position: 'QB',
    cached_team: 'BUF',
    slot: 'QB',
  });

  expect(saveManualRosterStore(storage, key, {
    version: 1,
    updated_at: roster.updated_at,
    rosters: [roster],
  })).toBe(true);

  const loaded = loadManualRosterStore(storage, key);
  expect(loaded.rosters[0].name).toBe('Family Team');
  expect(loaded.rosters[0].players[0]).toMatchObject({
    player_id: '4984',
    cached_name: 'Josh Allen',
    cached_position: 'QB',
    cached_team: 'BUF',
    slot: 'QB',
  });
  expect(loaded.rosters[0].lineup_limits).toMatchObject({ QB: 1, RB: 2, FLEX: 1, BN: 6 });
});

it('recovers safely from corrupt or inaccessible storage', () => {
  const storage = memoryStorage();
  const key = manualRosterStorageKey('browser-123');
  storage.values[key] = '{not-json';
  expect(loadManualRosterStore(storage, key).rosters).toEqual([]);

  const blocked = {
    getItem: () => { throw new DOMException('blocked', 'SecurityError'); },
    setItem: () => { throw new DOMException('blocked', 'SecurityError'); },
  };
  expect(loadManualRosterStore(blocked, key).rosters).toEqual([]);
  expect(saveManualRosterStore(blocked, key, loadManualRosterStore(storage, key))).toBe(false);
});

it('duplicates independently and round-trips an exported roster as a new copy', () => {
  const roster = createManualRoster('Original');
  roster.players.push({
    player_id: '7564', cached_name: "Ja'Marr Chase", cached_position: 'WR', slot: 'WR',
  });
  const duplicate = duplicateManualRoster(roster);
  expect(duplicate.id).not.toBe(roster.id);
  expect(duplicate.name).toBe('Original Copy');
  duplicate.players[0].slot = 'BN';
  expect(roster.players[0].slot).toBe('WR');

  const imported = importManualRosters(exportManualRoster(roster));
  expect(imported).toHaveLength(1);
  expect(imported[0].id).not.toBe(roster.id);
  expect(imported[0].name).toBe('Original (Imported)');
  expect(imported[0].players[0].cached_name).toBe("Ja'Marr Chase");
});

it('migrates an older export and drops its removed unavailable-player field', () => {
  const roster = createManualRoster('Legacy');
  const legacy = JSON.parse(exportManualRoster(roster));
  delete legacy.roster.lineup_limits;
  legacy.roster.players = [{
    player_id: '4046', cached_name: 'Patrick Mahomes', cached_position: 'QB', slot: 'SUPER_FLEX',
  }];
  legacy.roster.unavailable_player_ids = ['9221'];

  const imported = importManualRosters(JSON.stringify(legacy))[0];
  expect(imported.lineup_limits.SUPER_FLEX).toBe(1);
  expect(imported).not.toHaveProperty('unavailable_player_ids');
});