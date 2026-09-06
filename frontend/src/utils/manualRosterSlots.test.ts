import { ManualRosterPlayer } from '../types/player';
import {
  DEFAULT_MANUAL_ROSTER_LIMITS,
  autoAssignManualPlayers,
  findAutomaticSlot,
  sortManualRosterPlayers,
} from './manualRosterSlots';

const player = (id: string, name: string, position: string, slot: ManualRosterPlayer['slot'] = 'BN') => ({
  player_id: id,
  cached_name: name,
  cached_position: position,
  slot,
});

it('fills main positions, applicable flexes, then bench', () => {
  const limits = {
    ...DEFAULT_MANUAL_ROSTER_LIMITS,
    RB: 1,
    FLEX: 1,
    SUPER_FLEX: 1,
    BN: 1,
  };
  const assigned = autoAssignManualPlayers([
    player('1', 'RB One', 'RB'),
    player('2', 'RB Two', 'RB'),
    player('3', 'RB Three', 'RB'),
    player('4', 'RB Four', 'RB'),
  ], limits);
  expect(assigned.players.map((entry) => entry.slot)).toEqual(['RB', 'FLEX', 'SUPER_FLEX', 'BN']);
  expect(assigned.overflow).toEqual([]);
  expect(findAutomaticSlot('RB', assigned.players, limits)).toBeNull();
});

it('uses W/T for receivers before W/R/T and Superflex', () => {
  const limits = {
    ...DEFAULT_MANUAL_ROSTER_LIMITS,
    WR: 1,
    REC_FLEX: 1,
    FLEX: 1,
    SUPER_FLEX: 1,
  };
  const assigned = autoAssignManualPlayers([
    player('1', 'WR One', 'WR'),
    player('2', 'WR Two', 'WR'),
    player('3', 'WR Three', 'WR'),
    player('4', 'WR Four', 'WR'),
  ], limits);
  expect(assigned.players.map((entry) => entry.slot)).toEqual([
    'WR', 'REC_FLEX', 'FLEX', 'SUPER_FLEX',
  ]);
});

it('sorts the visible roster into standard lineup order', () => {
  const sorted = sortManualRosterPlayers([
    player('1', 'Bench', 'RB', 'BN'),
    player('2', 'Kicker', 'K', 'K'),
    player('3', 'Flex', 'RB', 'FLEX'),
    player('4', 'Quarterback', 'QB', 'QB'),
    player('5', 'Defense', 'DEF', 'DEF'),
    player('6', 'Receiver', 'WR', 'WR'),
  ]);
  expect(sorted.map((entry) => entry.slot)).toEqual(['QB', 'WR', 'FLEX', 'DEF', 'K', 'BN']);
});