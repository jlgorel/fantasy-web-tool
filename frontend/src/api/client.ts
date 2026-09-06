// Single source of truth for backend HTTP calls.
// Replaces the three duplicate `API_BASE` declarations and ad-hoc fetch logic
// previously scattered across pages and components.

import {
  CachedStartsResponse,
  LeagueDataResponse,
  LoadSleeperInfoResponse,
  ManualLineupRequest,
  ManualPlayerCatalogResponse,
  OverallRankingsPayload,
  PlayerDetailResponse,
  RisersFallersResponse,
  RunInfoResponse,
  SleeperLeagueResolveResponse,
  SleeperLeagueSeasonsResponse,
  SleeperUserLeaguesResponse,
  WaiverWireResponse,
  WebsiteName,
  WrappedApiResponse,
  WrappedInspectRedraftTrade,
  WrappedInspectTrade,
} from '../types/player';
import {
  LeagueHabitsResponse,
  LiveDraftPollOptions,
  LiveDraftState,
  OpponentsHabitsResponse,
  RankingsResponse,
  SimRequest,
  SimResponse,
  UserHabitsResponse,
} from '../types/draft';

if (!process.env.REACT_APP_API_BASE_URL) {
  throw new Error('REACT_APP_API_BASE_URL is not set!');
}

export const API_BASE = process.env.REACT_APP_API_BASE_URL;

class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(API_BASE + path, init);
  if (!response.ok) {
    let detail = '';
    try {
      const payload = await response.json();
      detail = payload?.detail || payload?.error || payload?.message || '';
    } catch (_error) {
      // Non-JSON upstream failures keep the generic status message.
    }
    throw new ApiError(
      response.status,
      detail || `Request to ${path} failed: ${response.status}`,
    );
  }
  return response.json() as Promise<T>;
}

const uuidHeaders = (uuid: string) => ({ 'X-User-UUID': uuid });

export const api = {
  loadLastRunInfo(): Promise<RunInfoResponse> {
    return request<RunInfoResponse>('/load-last-run-info');
  },

  loadSleeperInfo(uuid: string, name: string, website: WebsiteName): Promise<LoadSleeperInfoResponse> {
    return request<LoadSleeperInfoResponse>('/load-sleeper-info', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...uuidHeaders(uuid) },
      body: JSON.stringify({ name, website }),
    });
  },

  loadCachedStarts(uuid: string): Promise<CachedStartsResponse> {
    return request<CachedStartsResponse>('/load-cached-starts', {
      headers: uuidHeaders(uuid),
    });
  },

  loadLeagueData(uuid: string, league: string): Promise<LeagueDataResponse> {
    const url = `/load-league-data?league=${encodeURIComponent(league)}`;
    return request<LeagueDataResponse>(url, { headers: uuidHeaders(uuid) });
  },

  getManualPlayers(): Promise<ManualPlayerCatalogResponse> {
    return request<ManualPlayerCatalogResponse>('/manual/players');
  },

  postManualLineup(uuid: string, body: ManualLineupRequest): Promise<LeagueDataResponse> {
    return request<LeagueDataResponse>('/manual/lineup', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...uuidHeaders(uuid) },
      body: JSON.stringify(body),
    });
  },

  getOverallRankings(): Promise<OverallRankingsPayload> {
    return request<OverallRankingsPayload>('/overall-ranks');
  },

  getWaiverWire(opts: { variant?: string; maxOwned?: number; topN?: number } = {}): Promise<WaiverWireResponse> {
    const params = new URLSearchParams();
    if (opts.variant) params.set('variant', opts.variant);
    if (opts.maxOwned != null) params.set('max_owned', String(opts.maxOwned));
    if (opts.topN != null) params.set('top_n', String(opts.topN));
    const qs = params.toString();
    return request<WaiverWireResponse>(`/waiver-wire${qs ? `?${qs}` : ''}`);
  },

  getRisersFallers(opts: { variant?: string; topN?: number } = {}): Promise<RisersFallersResponse> {
    const params = new URLSearchParams();
    if (opts.variant) params.set('variant', opts.variant);
    if (opts.topN != null) params.set('top_n', String(opts.topN));
    const qs = params.toString();
    return request<RisersFallersResponse>(`/risers-fallers${qs ? `?${qs}` : ''}`);
  },

  getPlayerDetail(playerId: string): Promise<PlayerDetailResponse> {
    return request<PlayerDetailResponse>(`/player/${encodeURIComponent(playerId)}`);
  },

  getWrappedSleeper(leagueId: string, year?: string): Promise<WrappedApiResponse> {
    const qs = year ? `?year=${encodeURIComponent(year)}` : '';
    return request<WrappedApiResponse>(`/wrapped/sleeper/${encodeURIComponent(leagueId)}${qs}`);
  },

  /**
   * Single-trade inspector payload: verdict + race chart + per-asset
   * sparkline series. The race chart's crossover dates are the dates on
   * which the verdict actually flipped — render them as vertical guide
   * lines so users can see *when* the trade went bad (or good).
   */
  getWrappedInspectTrade(
    leagueId: string, transactionId: string, year?: string,
  ): Promise<WrappedInspectTrade> {
    const params = new URLSearchParams({ transaction_id: transactionId });
    if (year) params.set('year', year);
    return request<WrappedInspectTrade>(
      `/wrapped/sleeper/${encodeURIComponent(leagueId)}/inspect_trade?${params.toString()}`,
    );
  },

  /**
   * Redraft retrospective trade evaluator: scores who won a completed
   * redraft trade based on the rest-of-season VORP each side produced.
   * Backend 400s for dynasty leagues; the parent must dispatch on
   * `is_dynasty` before calling.
   */
  getWrappedInspectTradeRedraft(
    leagueId: string, transactionId: string, year?: string,
  ): Promise<WrappedInspectRedraftTrade> {
    const params = new URLSearchParams({ transaction_id: transactionId });
    if (year) params.set('year', year);
    return request<WrappedInspectRedraftTrade>(
      `/wrapped/sleeper/${encodeURIComponent(leagueId)}/inspect_trade_redraft?${params.toString()}`,
    );
  },

  getSleeperUserLeagues(
    username: string, year?: string, excludeDynasty?: boolean,
  ): Promise<SleeperUserLeaguesResponse> {
    const params = new URLSearchParams();
    if (year) params.set('year', year);
    if (excludeDynasty) params.set('exclude_dynasty', '1');
    const qs = params.toString() ? `?${params.toString()}` : '';
    return request<SleeperUserLeaguesResponse>(
      `/sleeper/user/${encodeURIComponent(username)}/leagues${qs}`
    );
  },

  resolveSleeperLeague(leagueId: string, year: string): Promise<SleeperLeagueResolveResponse> {
    return request<SleeperLeagueResolveResponse>(
      `/sleeper/league/${encodeURIComponent(leagueId)}/resolve?year=${encodeURIComponent(year)}`
    );
  },

  getSleeperLeagueSeasons(leagueId: string): Promise<SleeperLeagueSeasonsResponse> {
    return request<SleeperLeagueSeasonsResponse>(
      `/sleeper/league/${encodeURIComponent(leagueId)}/seasons`
    );
  },

  // --- Draft Help -----------------------------------------------------
  getDraftHelpUserHabits(username: string, seasons?: number): Promise<UserHabitsResponse> {
    const qs = seasons != null ? `?seasons=${seasons}` : '';
    return request<UserHabitsResponse>(
      `/draft-help/user/${encodeURIComponent(username)}/habits${qs}`
    );
  },

  getDraftHelpLeagueHabits(leagueId: string, seasons?: number): Promise<LeagueHabitsResponse> {
    const qs = seasons != null ? `?seasons=${seasons}` : '';
    return request<LeagueHabitsResponse>(
      `/draft-help/league/${encodeURIComponent(leagueId)}/habits${qs}`
    );
  },

  getDraftHelpOpponents(
    leagueId: string, seasons?: number, maxLeagues?: number,
  ): Promise<OpponentsHabitsResponse> {
    const params = new URLSearchParams();
    if (seasons != null) params.set('seasons', String(seasons));
    if (maxLeagues != null) params.set('max_leagues', String(maxLeagues));
    const qs = params.toString();
    return request<OpponentsHabitsResponse>(
      `/draft-help/league/${encodeURIComponent(leagueId)}/opponents${qs ? `?${qs}` : ''}`
    );
  },

  getDraftHelpRankings(
    year: string, teams: number, ppr: number, superflex: boolean,
    profileId?: string | null, providerId?: string | null,
  ): Promise<RankingsResponse> {
    const params = new URLSearchParams({
      year, teams: String(teams), ppr: String(ppr), sf: superflex ? '1' : '0',
    });
    if (profileId) params.set('profile', profileId);
    if (providerId) params.set('provider', providerId);
    return request<RankingsResponse>(`/draft-help/rankings?${params.toString()}`);
  },

  postDraftHelpSim(body: SimRequest): Promise<SimResponse> {
    return request<SimResponse>('/draft-help/sim', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
  },

  getLiveDraft(
    draftId: string, options: LiveDraftPollOptions = {},
  ): Promise<LiveDraftState> {
    const params = new URLSearchParams();
    if (options.username) params.set('username', options.username);
    if (options.slot != null) params.set('slot', String(options.slot));
    if (options.knownLastPicked !== undefined) {
      params.set(
        'known_last_picked',
        options.knownLastPicked === null ? 'null' : String(options.knownLastPicked),
      );
    }
    if (options.knownStatus) params.set('known_status', options.knownStatus);
    const query = params.toString();
    return request<LiveDraftState>(
      `/draft-help/live/draft/${encodeURIComponent(draftId)}${query ? `?${query}` : ''}`,
    );
  },

  getLiveLeagueDraft(
    leagueId: string, username?: string,
  ): Promise<LiveDraftState> {
    const query = username
      ? `?username=${encodeURIComponent(username)}`
      : '';
    return request<LiveDraftState>(
      `/draft-help/live/league/${encodeURIComponent(leagueId)}${query}`,
    );
  },
};

export { ApiError };
