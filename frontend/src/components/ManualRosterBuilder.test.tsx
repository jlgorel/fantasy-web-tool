import React from 'react';
import { ChakraProvider } from '@chakra-ui/react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

import { api } from '../api/client';
import { UUIDProvider } from '../context/UUIDContext';
import ManualRosterBuilder from './ManualRosterBuilder';

jest.mock('../api/client', () => ({
  api: {
    getManualPlayers: jest.fn(),
    postManualLineup: jest.fn(),
  },
}));
jest.mock('./PlayerTable', () => () => <div data-testid="player-table" />);
jest.mock('./LineupConfidence', () => () => <div data-testid="lineup-confidence" />);

const mockedApi = api as jest.Mocked<typeof api>;

const installMatchMedia = () => {
  Object.defineProperty(window, 'matchMedia', {
    writable: true,
    value: jest.fn().mockImplementation((query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addListener: jest.fn(),
      removeListener: jest.fn(),
      addEventListener: jest.fn(),
      removeEventListener: jest.fn(),
      dispatchEvent: jest.fn(),
    })),
  });
};

const renderBuilder = () => render(
  <ChakraProvider>
    <UUIDProvider>
      <ManualRosterBuilder />
    </UUIDProvider>
  </ChakraProvider>,
);

beforeEach(() => {
  window.localStorage.clear();
  jest.clearAllMocks();
  installMatchMedia();
  mockedApi.getManualPlayers.mockResolvedValue({
    players: [
      { player_id: '4984', name: 'Josh Allen', position: 'QB', team: 'BUF' },
      { player_id: '7564', name: "Ja'Marr Chase", position: 'WR', team: 'CIN' },
    ],
  });
  mockedApi.postManualLineup.mockResolvedValue({
    suggested_starts: [{ NAME: 'Josh Allen', POS: 'QB', PID: '4984' }],
    boris_optimized: [{ NAME: 'Josh Allen', POS: 'QB', PID: '4984' }],
    vegas_optimized: [{ NAME: 'Josh Allen', POS: 'QB', PID: '4984' }],
    your_lineup: [{ NAME: 'Josh Allen', POS: 'QB', PID: '4984' }],
    free_agent_recs: {},
    free_agent_model: 'not_available',
  });
});

it('creates, persists, edits, and optimizes a manual My Teams roster', async () => {
  renderBuilder();
  fireEvent.click(screen.getByRole('button', { name: 'Create Roster' }));

  const search = await screen.findByPlaceholderText(/Search players/);
  fireEvent.change(search, { target: { value: 'Josh Allen' } });
  fireEvent.click(await screen.findByRole('button', { name: /Josh Allen.*Add/ }));

  expect(screen.getByLabelText('Lineup slot for Josh Allen')).toHaveValue('QB');
  fireEvent.click(screen.getByRole('button', { name: 'Optimize This Roster' }));

  await waitFor(() => expect(mockedApi.postManualLineup).toHaveBeenCalledWith(
    expect.any(String),
    expect.objectContaining({
      name: 'My Manual Team',
      players: [{ player_id: '4984', slot: 'QB' }],
      scoring: { ppr: 0.5, passing_td_points: 4 },
      lineup_limits: expect.objectContaining({ QB: 1, RB: 2, FLEX: 1, BN: 6 }),
    }),
  ));
  expect(await screen.findByText(/Roster optimization only/)).toBeInTheDocument();

  const storageKey = Object.keys(window.localStorage).find((key) =>
    key.startsWith('fantasy-web-tool-manual-rosters-v1:'));
  expect(storageKey).toBeDefined();
  const stored = JSON.parse(window.localStorage.getItem(storageKey!) ?? '{}');
  expect(stored.rosters[0].players[0]).toMatchObject({
    player_id: '4984', cached_name: 'Josh Allen', cached_position: 'QB', cached_team: 'BUF', slot: 'QB',
  });
});