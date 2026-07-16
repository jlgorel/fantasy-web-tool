// Types for the Draft Help tab payloads (backend: /draft-help/*).

export interface ReachEntry {
  player_id: string;
  name?: string | null;
  position: string;
  pick_no: number;
  expected_overall_rank?: number | null;
  player_vbd: number;
  par_vbd: number;
  vbd_delta: number;
}

export interface ReachSummary {
  picks_evaluated: number;
  avg_vbd_delta?: number;
  avg_by_position?: Record<string, number>;
  biggest_reach?: ReachEntry;
  biggest_value?: ReachEntry;
}

export interface SnakeSummary {
  drafts_counted: number;
  position_by_round: Record<string, Record<string, number>>;
  early_round_mix: Record<string, number>;
  archetypes: Record<string, number>;
  reach: ReachSummary;
}

export interface InflationRow {
  order: number;
  pick_no: number;
  player_id?: string | null;
  name?: string | null;
  position: string;
  expected: number | null;
  actual: number;
  inflation_pct: number | null;
}

export interface AuctionSummary {
  drafts_counted: number;
  avg_spend_by_position: Record<string, number>;
  avg_stars_and_scrubs_index: number;
  avg_max_bid_pct_budget: number;
  inflation_curve: InflationRow[];
}

export interface FavoritePlayer {
  player_id: string;
  name?: string | null;
  position: string;
  count: number;
}

export interface ManagerSummary {
  snake?: SnakeSummary;
  auction?: AuctionSummary;
  favorites?: FavoritePlayer[];
}

export interface NamedManagerSummary extends ManagerSummary {
  username: string;
}

export interface PositionRun {
  start_pick: number;
  end_pick: number;
  count: number;
}

export interface MarketStatus {
  position: string;
  buys_analyzed: number;
  crashed: boolean;
  crash_after: number | null;
  crash_pick_no: number | null;
  avg_inflation_before: number | null;
  avg_inflation_after: number | null;
  early_inflation: number | null;
  late_inflation: number | null;
}

export interface AggregatedMarketStatus {
  drafts_analyzed: number;
  crashed_in: number;
  crashed: boolean;
  latest: MarketStatus | null;
}

export interface EliteMarket {
  drafts_analyzed: number;
  early_inflation: number;
  late_inflation: number;
  diff: number;
  pattern: 'hot_start' | 'cold_start' | 'flat';
  hot_starts: number;
  cold_starts: number;
}

export interface LeagueWide {
  draft_type?: string;
  drafts_analyzed?: number;
  runs?: Record<string, PositionRun[]>;
  first_five_off_board?: Record<string, number[]>;
  market_crash?: Record<string, AggregatedMarketStatus>;
  elite_market?: EliteMarket | null;
}

export interface LeagueSeasonInfo {
  season: string;
  league_id: string;
  config: { teams: number | null; ppr: number; superflex: boolean };
  drafts: number;
}

export interface LeagueHabitsResponse {
  feature: 'league_habits';
  league_id: string;
  seasons: LeagueSeasonInfo[];
  managers: Record<string, NamedManagerSummary>;
  league_wide: LeagueWide;
}

export interface UserHabitsResponse {
  feature: 'user_habits';
  username: string;
  user_id?: string;
  leagues_scanned?: number;
  summary?: ManagerSummary;
  error?: string;
}

export interface OpponentEntry {
  username: string;
  leagues_scanned: number;
  summary: ManagerSummary;
}

export interface OpponentsHabitsResponse {
  feature: 'opponents_habits';
  league_id: string;
  warning: string;
  caps: { max_leagues_per_opponent: number; seasons: number };
  opponents: Record<string, OpponentEntry>;
}

// --- Mock draft board + Monte-Carlo sim -----------------------------------
export interface RankingsPlayerRow {
  player_id: string;
  name: string;
  pos: string;
  team?: string | null;
  bye?: number | null;
  fpts?: number | null;
  auction?: number | null;
  vbd?: number | null;
  tier?: string | null;
  pos_rank?: number | null;
  overall_rank?: number | null;
  adp?: number | null;
  adp_stdev?: number | null;
}

export interface RankingsResponse {
  year: string;
  config: { teams: number; ppr: number; superflex: boolean };
  players: RankingsPlayerRow[];
}

export interface LikelyPick {
  pick_no: number;
  player_id: string;
  name: string;
  pos: string;
  pct: number;
}

export interface SimCandidate {
  player_id: string;
  name: string;
  pos: string;
  adp: number;
  proj: number;
  avg_lineup: number;
  avg_depth?: number;
  likely_next?: LikelyPick[];
  sims: number;
}

export interface SimResponse {
  current_pick: number;
  my_upcoming_picks?: number[];
  candidates: SimCandidate[];
  recommendation: SimCandidate | null;
}

export interface SimRequest {
  year: string;
  teams: number;
  rounds: number;
  my_slot: number;
  ppr: number;
  superflex: boolean;
  drafted_ids: string[];
  my_roster_ids: string[];
  slots?: Record<string, number>;
  current_pick?: number;
  n_sims?: number;
  top_k?: number;
  seed?: number;
}
