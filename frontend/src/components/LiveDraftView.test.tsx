import React from 'react';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import LiveDraftView from './LiveDraftView';
import { api } from '../api/client';

jest.mock('../api/client', () => ({
  api: {
    getLiveDraft: jest.fn(),
    getLiveLeagueDraft: jest.fn(),
    getSleeperUserLeagues: jest.fn(),
    getDraftHelpRankings: jest.fn(),
    postDraftHelpSim: jest.fn(),
  },
}));

const mockedApi = api as jest.Mocked<typeof api>;

const fullState: any = {
  changed: true,
  draft_id: '1392134959602356224',
  name: 'All Star League',
  season: '2026',
  status: 'paused',
  last_picked: 1786273359868,
  config: {
    teams: 12, rounds: 15, bench_size: 6, ppr: 0.5, superflex: false,
    slots: { QB: 1, RB: 2, WR: 2, TE: 1, FLEX: 1 },
  },
  available_slots: [1, 2, 3],
  needs_slot: false,
  user_slot: 2,
  current_pick: 8,
  total_picks: 180,
  on_clock_slot: 8,
  is_user_pick: false,
  picks_until_user: 15,
  my_upcoming_picks: [23, 26],
  drafted_ids: ['9509', '9221'],
  my_roster_ids: ['9221'],
  picks: [
    { pick_no: 1, round: 1, draft_slot: 1, player_id: '9509', name: 'Bijan Robinson', pos: 'RB', is_keeper: false },
    { pick_no: 2, round: 1, draft_slot: 2, player_id: '9221', name: 'Jahmyr Gibbs', pos: 'RB', is_keeper: false },
  ],
  poll_interval_ms: 5000,
};

beforeEach(() => {
  jest.useFakeTimers();
  localStorage.clear();
  mockedApi.getDraftHelpRankings.mockResolvedValue({
    year: '2026', config: { teams: 12, ppr: 0.5, superflex: false },
    sources: { values: { source: 'custom upload required' }, adp: { source: 'fantasypros_draftwizard' } },
    players: [],
  });
});

afterEach(() => {
  jest.useRealTimers();
  jest.clearAllMocks();
});

it('connects by draft URL and renders supplied paused state', async () => {
  mockedApi.getLiveDraft.mockResolvedValue(fullState);
  render(<LiveDraftView />);
  fireEvent.change(screen.getByPlaceholderText('1392134959602356224'), {
    target: { value: 'https://sleeper.app/draft/nfl/1392134959602356224' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Connect' }));

  expect(await screen.findByText('All Star League')).toBeInTheDocument();
  expect(screen.getByText('15 picks until you')).toBeInTheDocument();
  expect(screen.getAllByText('Jahmyr Gibbs').length).toBeGreaterThanOrEqual(1);
  expect(mockedApi.getLiveDraft).toHaveBeenCalledWith(
    '1392134959602356224',
    expect.objectContaining({ slot: undefined }),
  );
});

it('polls conditionally with last_picked and status', async () => {
  mockedApi.getLiveDraft
    .mockResolvedValueOnce(fullState)
    .mockResolvedValue({
      changed: false,
      draft_id: fullState.draft_id,
      status: 'paused',
      last_picked: fullState.last_picked,
      poll_interval_ms: 5000,
    } as any);
  render(<LiveDraftView />);
  fireEvent.change(screen.getByPlaceholderText('1392134959602356224'), {
    target: { value: fullState.draft_id },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Connect' }));
  await screen.findByText('All Star League');

  await act(async () => { jest.advanceTimersByTime(5000); });
  await waitFor(() => expect(mockedApi.getLiveDraft).toHaveBeenCalledTimes(2));
  expect(mockedApi.getLiveDraft).toHaveBeenLastCalledWith(
    fullState.draft_id,
    expect.objectContaining({
      knownLastPicked: fullState.last_picked,
      knownStatus: 'paused',
    }),
  );
  await act(async () => { jest.advanceTimersByTime(5000); });
  await waitFor(() => expect(mockedApi.getLiveDraft).toHaveBeenCalledTimes(3));
});

it('filters manual value player options with dynamic search', async () => {
  jest.useRealTimers();
  mockedApi.getLiveDraft.mockResolvedValue(fullState);
  mockedApi.getDraftHelpRankings.mockResolvedValue({
    year: '2026', config: { teams: 12, ppr: 0.5, superflex: false },
    players: [
      { player_id: '9221', name: 'Jahmyr Gibbs', pos: 'RB', adp: 1.4 },
      { player_id: '9509', name: 'Bijan Robinson', pos: 'RB', adp: 2.2 },
    ],
  });
  render(<LiveDraftView />);
  fireEvent.change(screen.getByPlaceholderText('1392134959602356224'), {
    target: { value: fullState.draft_id },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Connect' }));
  await screen.findByText('All Star League');
  await waitFor(() => expect(mockedApi.getDraftHelpRankings).toHaveBeenCalled());
  fireEvent.click(screen.getByRole('button', { name: /Live draft values/ }));
  const search = await screen.findByPlaceholderText('Search and select player…');
  fireEvent.change(search, {
    target: { value: 'Bijan' },
  });
  expect(screen.getByRole('option', {
    name: /Bijan Robinson/, hidden: true,
  })).toBeInTheDocument();
  expect(screen.queryByRole('option', {
    name: /Jahmyr Gibbs/, hidden: true,
  })).not.toBeInTheDocument();
});

it('allows scoring override and optimistic manual draft with undo', async () => {
  mockedApi.getLiveDraft.mockResolvedValue(fullState);
  mockedApi.getDraftHelpRankings.mockResolvedValue({
    year: '2026', config: { teams: 12, ppr: 0.5, superflex: false },
    sources: { values: { source: 'default' }, adp: { source: 'fantasypros_draftwizard' } },
    players: [
      { player_id: '9221', name: 'Jahmyr Gibbs', pos: 'RB', adp: 1.4, vbd: 100 },
      { player_id: '9509', name: 'Bijan Robinson', pos: 'RB', adp: 2.0, vbd: 99 },
      { player_id: 'other', name: 'Available Player', pos: 'WR', adp: 8, vbd: 80 },
    ],
  });
  render(<LiveDraftView />);
  fireEvent.change(screen.getByPlaceholderText('1392134959602356224'), {
    target: { value: fullState.draft_id },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Connect' }));
  await screen.findByText('Available Player');

  fireEvent.change(screen.getByLabelText('ADP scoring source'), {
    target: { value: '1' },
  });
  await waitFor(() => expect(mockedApi.getDraftHelpRankings).toHaveBeenCalledWith(
    '2026', 12, 1, false, 'qb1-rb2-wr2-te1-flex1-bn6-ptd4', 'elboberto',
  ));

  fireEvent.click(screen.getByRole('button', { name: 'Mark drafted' }));
  expect(screen.getByText('Pick 9/180')).toBeInTheDocument();
  expect(screen.getByText('1 awaiting Sleeper')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Undo local pick' }));
  expect(screen.getByText('Pick 8/180')).toBeInTheDocument();
});

it('reloads the exact board when the simulation provider changes', async () => {
  jest.useRealTimers();
  mockedApi.getLiveDraft.mockResolvedValue(fullState);
  mockedApi.getDraftHelpRankings.mockResolvedValue({
    year: '2026', config: { teams: 12, ppr: 0.5, superflex: false },
    sources: {
      values: {
        source: 'ElBoberto',
        available_providers: [
          { id: 'elboberto', name: 'ElBoberto', available: true },
          { id: 'draftsheets', name: 'DraftSheets', available: true },
        ],
      },
      adp: { source: 'fantasypros_draftwizard' },
    },
    players: [{
      player_id: 'other', name: 'Available Player', pos: 'WR', adp: 8, vbd: 80,
      provider_values: {
        elboberto: { value: 80, rank: 1 },
        draftsheets: { value: 55, rank: 2 },
      },
    }],
  });
  render(<LiveDraftView />);
  fireEvent.change(screen.getByPlaceholderText('1392134959602356224'), {
    target: { value: fullState.draft_id },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Connect' }));
  const provider = await screen.findByLabelText('Simulation value provider');
  fireEvent.change(provider, { target: { value: 'draftsheets' } });
  await waitFor(() => expect(mockedApi.getDraftHelpRankings).toHaveBeenCalledWith(
    '2026', 12, 0.5, false,
    'qb1-rb2-wr2-te1-flex1-bn6-ptd4', 'draftsheets',
  ));
});

it('reconciles a manually advanced pick when Sleeper confirms it', async () => {
  const confirmedPick = {
    pick_no: 8, round: 1, draft_slot: 8, player_id: 'other',
    name: 'Available Player', pos: 'WR', is_keeper: false,
  };
  mockedApi.getLiveDraft
    .mockResolvedValueOnce(fullState)
    .mockResolvedValueOnce({
      ...fullState,
      last_picked: fullState.last_picked + 1,
      current_pick: 9,
      on_clock_slot: 9,
      drafted_ids: [...fullState.drafted_ids, 'other'],
      picks: [...fullState.picks, confirmedPick],
    });
  mockedApi.getDraftHelpRankings.mockResolvedValue({
    year: '2026', config: { teams: 12, ppr: 0.5, superflex: false },
    sources: { values: { source: 'default' }, adp: { source: 'fantasypros_draftwizard' } },
    players: [
      { player_id: 'other', name: 'Available Player', pos: 'WR', adp: 8, vbd: 80 },
    ],
  });
  render(<LiveDraftView />);
  fireEvent.change(screen.getByPlaceholderText('1392134959602356224'), {
    target: { value: fullState.draft_id },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Connect' }));
  await screen.findByText('Available Player');
  fireEvent.click(screen.getByRole('button', { name: 'Mark drafted' }));
  expect(screen.getByText('1 awaiting Sleeper')).toBeInTheDocument();

  await act(async () => { jest.advanceTimersByTime(5000); });
  await waitFor(() => expect(screen.queryByText('1 awaiting Sleeper')).not.toBeInTheDocument());
  expect(screen.getByText('Pick 9/180')).toBeInTheDocument();
});
