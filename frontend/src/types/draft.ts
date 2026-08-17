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
  adp_stdev_source?: 'observed' | 'modeled' | 'missing' | null;
  adp_sample_size?: number | null;
  adp_high?: number | null;
  adp_low?: number | null;
  provider_values?: Record<string, {
    value?: number | null;
    rank?: number | null;
    points?: number | null;
    tier?: string | null;
  }>;
}

export interface RankingsResponse {
  year: string;
  config: { teams: number; ppr: number; superflex: boolean };
  sources?: {
    values?: {
      source?: string | null;
      provider?: string | null;
      source_url?: string | null;
      source_version?: string | null;
      generated_at_utc?: string | null;
      retrieved_at_utc?: string | null;
      attribution?: string | null;
      source_revision?: string | null;
      selected_provider_id?: string | null;
      available_providers?: Array<{
        id: string;
        name?: string;
        attribution?: string | null;
        source_url?: string | null;
        source_version?: string | null;
        generated_at_utc?: string | null;
        profile_count?: number;
        available?: boolean;
        status?: {
          checked_at_utc?: string;
          latest_source_version?: string | null;
          current_source_version?: string | null;
          update_available?: boolean;
          refresh_mode?: string;
        } | null;
      }>;
      requested_profile_id?: string | null;
      available_profiles?: Array<{
        id: string;
        blob_name: string;
        profile: {
          id?: string;
          passing_td?: number;
          bench_size?: number;
          starters?: Record<string, number>;
          superflex_mode?: string;
        };
        config_count?: number;
        generated_at_utc?: string;
      }>;
      profile?: {
        passing_td?: number;
        bench_size?: number;
        starters?: Record<string, number>;
        superflex_mode?: string;
      } | null;
    };
    adp?: {
      source?: string | null;
      generated_at_utc?: string | null;
      format?: string | null;
      total_drafts?: number | null;
      matched?: number | null;
      total?: number | null;
    };
  };
  players: RankingsPlayerRow[];
}

export interface CustomValueEntry {
  value?: number;
  avoid?: boolean;
  source: 'upload' | 'elboberto_paste' | 'manual';
}

export interface CustomDraftSettings {
  version: 1;
  updated_at: string;
  entries: Record<string, CustomValueEntry>;
}

export interface CustomCsvMatch {
  row: number;
  input_name?: string;
  input_position?: string;
  input_player_id?: string;
  value?: number;
  player_id?: string;
  matched_name?: string;
  error?: string;
}

export interface CustomCsvPreview {
  matches: CustomCsvMatch[];
  errors: string[];
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
  avg_value: number;
  avg_lineup: number;
  avg_depth?: number;
  value_stdev?: number;
  value_p25?: number;
  value_p75?: number;
  lineup_stdev?: number;
  lineup_p25?: number;
  lineup_p75?: number;
  likely_next?: LikelyPick[];
  sims: number;
}

export interface SimRecommendationConfidence {
  label: 'near_tie' | 'slight_edge' | 'strong_edge' | 'only_option';
  gap: number | null;
  win_pct: number;
  difference_stdev: number;
  standard_error: number;
  runner_up_player_id: string | null;
  sims: number;
}

export interface SimResponse {
  current_pick: number;
  my_upcoming_picks?: number[];
  candidates: SimCandidate[];
  recommendation: SimCandidate | null;
  priority_candidates?: SimCandidate[];
  recommendation_confidence?: SimRecommendationConfidence;
  cache_hit?: boolean;
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
  value_overrides?: Record<string, number>;
  use_provider_values?: boolean;
  profile_id?: string;
  simulation_provider_id?: string;
  bench_size?: number;
  passing_td?: number;
  avoid_ids?: string[];
  priority_candidate_ids?: string[];
  my_future_pick_numbers?: number[];
}

// --- Read-only Sleeper live draft lobby -----------------------------------
export interface LiveDraftConfig {
  teams: number;
  rounds: number;
  bench_size: number;
  ppr: number;
  superflex: boolean;
  slots: Record<string, number>;
}

export interface LiveDraftPick {
  pick_no: number;
  round: number;
  draft_slot: number;
  player_id: string;
  name: string;
  pos?: string | null;
  team?: string | null;
  picked_by?: string | null;
  is_keeper: boolean;
  optimistic?: boolean;
  optimistic_owner_is_user?: boolean;
}

export interface LiveDraftState {
  changed: boolean;
  draft_id: string;
  league_id?: string | null;
  name?: string;
  season?: string;
  status: string;
  last_picked?: number | null;
  pick_timer_seconds?: number | null;
  config?: LiveDraftConfig;
  available_slots?: number[];
  needs_slot?: boolean;
  user_slot?: number | null;
  current_pick?: number;
  total_picks?: number;
  on_clock_slot?: number | null;
  is_user_pick?: boolean;
  picks_until_user?: number | null;
  my_upcoming_picks?: number[];
  drafted_ids?: string[];
  my_roster_ids?: string[];
  picks?: LiveDraftPick[];
  poll_interval_ms?: number | null;
  username_warning?: string;
}

export interface LiveDraftPollOptions {
  username?: string;
  slot?: number;
  knownLastPicked?: number | null;
  knownStatus?: string;
}
