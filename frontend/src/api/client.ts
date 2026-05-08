// Single source of truth for backend HTTP calls.
// Replaces the three duplicate `API_BASE` declarations and ad-hoc fetch logic
// previously scattered across pages and components.

import {
  CachedStartsResponse,
  LeagueDataResponse,
  LoadSleeperInfoResponse,
  OverallRankingsPayload,
  PlayerDetailResponse,
  RisersFallersResponse,
  RunInfoResponse,
  SleeperLeagueResolveResponse,
  SleeperLeagueSeasonsResponse,
  SleeperUserLeaguesResponse,
  WaiverWireResponse,
  WebsiteName,
  WrappedResponse,
} from '../types/player';

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
    throw new ApiError(response.status, `Request to ${path} failed: ${response.status}`);
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

  getWrappedSleeper(leagueId: string, year?: string): Promise<WrappedResponse> {
    const qs = year ? `?year=${encodeURIComponent(year)}` : '';
    return request<WrappedResponse>(`/wrapped/sleeper/${encodeURIComponent(leagueId)}${qs}`);
  },

  getSleeperUserLeagues(username: string, year?: string): Promise<SleeperUserLeaguesResponse> {
    const qs = year ? `?year=${encodeURIComponent(year)}` : '';
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
};

export { ApiError };
