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
  // Set on starting WR/TEs whose NFL team matches a starting QB on the same
  // fantasy team. Drives the "Stack with <QB>" badge.
  STACK_WITH_QB?: boolean;
  STACK_QB_NAME?: string;
  // Set on optimizer-promoted starters (vs the user's actual lineup) when
  // the optimizer slotted in a player who was on the bench in the user's
  // lineup. Indicates the bench-to-starter swap and the demoted player.
  DELTA_VS_YOUR_LINEUP?: number;
  DELTA_VS_PLAYER?: string;
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
  // Monte-Carlo derived. Probabilities in [0, 1]; null when sim was missing.
  BOOM?: number | null;
  BUST?: number | null;
  SIM_MEAN?: number | null;
  // 10th / 90th percentile fantasy points from the Monte-Carlo sim.
  // Used by the "Highest ceiling" / "Safest floor" leaderboard sorts.
  P10?: number | null;
  P90?: number | null;
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
  // Legacy alias for `boris_optimized`. Kept for backwards compatibility.
  suggested_starts: Player[];
  boris_optimized?: Player[];
  vegas_optimized?: Player[];
  // null for leagues whose backing service doesn't expose user-set starters
  // (e.g. Fleaflicker today).
  your_lineup?: Player[] | null;
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

// ---- Waiver wire cheat sheet -------------------------------------------------

export interface WaiverWireRow extends PlayerRow {
  OWNED_PCT?: number;
  STARTED_PCT?: number;
}

export interface WaiverWireResponse {
  variant: string;
  max_owned_pct: number;
  top_n: number;
  by_position: {
    [pos: string]: WaiverWireRow[];
  };
}

// ---- Risers / fallers -------------------------------------------------------

export interface MoverRow {
  PID: string;
  NAME: string;
  POS?: string;
  VEGAS: number;
  PREV_VEGAS: number;
  DELTA: number;
  DELTA_PCT: number | null;
  P10?: number | null;
  P90?: number | null;
  BOOM?: number | null;
  BUST?: number | null;
}

export interface RisersFallersResponse {
  variant: string;
  available: boolean;
  message?: string;
  risers: MoverRow[];
  fallers: MoverRow[];
}

// ---- Player Detail Page (/player/:pid) -------------------------------------

export interface PlayerDetailMeta {
  player_id: string;
  full_name: string | null;
  fantasy_positions: string[];
}

export interface PlayerDetailWeeklyEntry {
  half_ppr_points?: number;
  ppr_points?: number;
  std_points?: number;
  [k: string]: number | undefined;
}

export interface PlayerDetailSeasonEntry {
  half_ppr_points?: number;
  ppr_points?: number;
  std_points?: number;
  half_ppr_rank?: number;
  ppr_rank?: number;
  std_rank?: number;
  receptions?: number;
  [k: string]: number | undefined;
}

export interface PlayerDetailScoringYear {
  weekly: { [week: string]: PlayerDetailWeeklyEntry };
  season: PlayerDetailSeasonEntry;
}

export interface PlayerDetailOwnershipWeek {
  owned: number;
  started: number;
}

export interface PlayerDetailResponse {
  meta: PlayerDetailMeta;
  scoring: { [year: string]: PlayerDetailScoringYear };
  ownership: { [year: string]: { [week: string]: PlayerDetailOwnershipWeek } };
  available_years: string[];
}

// ---- League Wrapped (/wrapped/sleeper/:leagueId) ---------------------------

export interface WrappedRecord {
  wins: number;
  losses: number;
}

export interface WrappedMeta {
  league_id: string;
  league_name: string | null;
  year: string;
  is_dynasty: boolean;
  num_qbs: string;
  weeks_played: number[];
  playoff_week_start: number;
  scoring_keys: { qb: string; skill: string };
  users: string[];
}

export interface WrappedLuckEntry {
  username: string | null;
  count: number;
}

export interface WrappedConsistencyEntry {
  username: string;
  mad: number;
  mean: number;
}

export interface WrappedManagerEntry {
  username: string;
  efficiency_pct: number;
}

export interface WrappedFalloffEntry {
  username: string;
  delta: number;
}

export interface WrappedSchedulePayload {
  best_ball_records: { [user: string]: WrappedRecord };
  luck: {
    luckiest: WrappedLuckEntry;
    unluckiest: WrappedLuckEntry;
  };
  consistency: {
    most_consistent: WrappedConsistencyEntry | null;
    least_consistent: WrappedConsistencyEntry | null;
  };
  manager_efficiency: {
    most_efficient: WrappedManagerEntry | null;
    least_efficient: WrappedManagerEntry | null;
    by_user?: { [user: string]: number };
  };
  falloff_comeup: {
    biggest_come_up: WrappedFalloffEntry | null;
    biggest_falloff: WrappedFalloffEntry | null;
    by_user?: { [user: string]: { first_half_avg: number; second_half_avg: number } };
  };
  best_worst_schedule: {
    [user: string]: {
      best: { vs_schedule_of: string; record: WrappedRecord };
      worst: { vs_schedule_of: string; record: WrappedRecord };
    };
  };
  hypothetical_matrix: {
    [target: string]: { [opponent: string]: WrappedRecord };
  };
  weekly_scores: { [user: string]: { [week: string]: number } };
  median_scores: { [week: string]: number };
}

export interface WrappedResponse {
  meta: WrappedMeta;
  schedule: WrappedSchedulePayload;
  roster_moves?: WrappedRosterMovesPayload;
  draft?: WrappedDraftPayload;
  trades?: WrappedTradesPayload;
  streamers?: WrappedStreamersPayload;
}

// Phase 4: best-streamers section.
export interface WrappedStreamerEntry {
  k_avg: number | null;
  def_avg: number | null;
  combined_avg: number | null;
  weeks_counted: number;
}

export interface WrappedStreamerWinner {
  username: string;
  average: number;
}

export interface WrappedStreamersPayload {
  positions_included: string[]; // subset of ["K", "DEF"]
  by_user: { [user: string]: WrappedStreamerEntry };
  best_kicker: WrappedStreamerWinner | null;
  best_defense: WrappedStreamerWinner | null;
  best_combined: WrappedStreamerWinner | null;
}

// Phase 2: roster-move accolades.
export interface WrappedTrollEntry {
  player_id: string;
  name: string;
  num_start: number;
  num_bench: number;
  start_avg: number;
  bench_avg: number;
  troll_value: number;
}

export interface WrappedEarlyPickup {
  player_id: string;
  name: string;
  week_added: number;
  owned_pct_when_added: number;
  owned_pct_now: number;
}

export interface WrappedLateDrop {
  player_id: string;
  name: string;
  week_dropped: number;
  owned_pct_at_drop: number;
}

export interface WrappedBestAdd {
  player_id: string;
  name: string;
  position: string;
  value_over_baseline: number;
  week_added: number;
}

export interface WrappedWorstDropEntry {
  player_id: string;
  name: string;
  value_over_baseline: number;
}

export interface WrappedUserRosterMoves {
  early_pickup: WrappedEarlyPickup | null;
  late_drop: WrappedLateDrop | null;
  best_add: WrappedBestAdd | null;
  worst_drop: { [position: string]: WrappedWorstDropEntry };
}

export interface WrappedRosterMovesPayload {
  troll: { [user: string]: WrappedTrollEntry | null };
  by_user: { [user: string]: WrappedUserRosterMoves };
  baseline_player_scoring?: { [position: string]: number };
}

// Phase 2.5: Sleeper league discovery + cross-season resolution.
export interface SleeperLeagueSummary {
  league_id: string;
  name: string | null;
  season: string | null;
  previous_league_id: string | null;
  total_rosters: number | null;
  status: string | null;
}

export interface SleeperUserLeaguesResponse {
  username: string;
  year: string;
  leagues: SleeperLeagueSummary[];
}

export interface SleeperLeagueResolveResponse {
  requested_league_id: string;
  requested_year: string;
  league_id: string | null;
}


export interface SleeperLeagueSeason {
  season: string;
  league_id: string;
}

export interface SleeperLeagueSeasonsResponse {
  requested_league_id: string;
  seasons: SleeperLeagueSeason[];
}


// Phase 3: Draft + Trade accolades.
export interface WrappedDraftPick {
  player_id: string;
  name?: string;
  pick_no: number;
  round: number;
  position: string;
  season_points: number;
  drafted_pos_rank: number;
  actual_pos_rank: number;
  value_over_slot: number;
  username?: string;
}

export interface WrappedUserDraft {
  best_pick: WrappedDraftPick | null;
  worst_pick: WrappedDraftPick | null;
  num_picks: number;
}

export interface WrappedDraftPayload {
  by_user: { [user: string]: WrappedUserDraft };
  biggest_steal: WrappedDraftPick | null;
  biggest_bust: WrappedDraftPick | null;
  mr_irrelevant_hero: WrappedDraftPick | null;
}

export interface WrappedTradePlayer {
  player_id: string;
  name: string;
  value: number;
}

export interface WrappedTradePick {
  season: string | null;
  round: number | null;
  value: number;
}

export interface WrappedTradeSide {
  username: string;
  players: WrappedTradePlayer[];
  picks: WrappedTradePick[];
  total_value: number;
}

export interface WrappedTrade {
  week: number;
  transaction_id: string;
  sides: WrappedTradeSide[];
  winner: string | null;
  value_gap: number;
}

export interface WrappedUserTrades {
  num_trades: number;
  net_value_gained: number;
}

export interface WrappedTradesPayload {
  trades: WrappedTrade[];
  by_user: { [user: string]: WrappedUserTrades };
  biggest_fleecing: WrappedTrade | null;
  most_active_trader: { username: string; num_trades: number } | null;
}

