// Shared player + ranking types used across components and pages.

export type WebsiteName = 'Sleeper' | 'Fleaflicker';

export interface Player {
  NAME: string;
  POS: string;
  POS_RANK?: string;
  FLEX?: string;
  PID?: string;
  TEAM?: string;
  TEAM_NAME?: string;
  VEGAS?: string;
  VEGAS_STATS?: string;
  MATCHUP_RATING?: string;
  REALLIFE_POS?: string;
  BOOM?: string | number;
  BUST?: string | number;
  PERCENTILES?: string | Record<string, number>;
}

export type FreeAgentRecs = { [position: string]: Player[] };

// ---- Overall rankings (powers /ranks) ----------------------------------------

export type StatValueRaw = number | string | null;

export interface PlayerProjDict {
  [statName: string]: StatValueRaw;
}

export interface PlayerRow {
  NAME: string;
  PID?: string;
  POS?: string;
  PROJ?: PlayerProjDict;
  VEGAS?: number;
}

export interface OverallRankingsPayload {
  overall_rankings: {
    [variantKey: string]: PlayerRow[];
  };
}

// ---- Backend response shapes ------------------------------------------------

export interface LoadSleeperInfoResponse {
  message: string;
  cache_key: string;
  league_names: { [league: string]: Player[] };
  free_agents: { [league: string]: FreeAgentRecs };
}

export interface LeagueDataResponse {
  suggested_starts: Player[];
  free_agent_recs: FreeAgentRecs;
}

export interface CachedStartsResponse {
  league_names: string[];
}

export interface RunInfoResponse {
  Runtime?: string;
  Successful?: boolean;
  [key: string]: unknown;
}
