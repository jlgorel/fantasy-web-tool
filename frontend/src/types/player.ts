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
  // Phase 4 / TODO #5: stable user_id -> display_name map for the all-time
  // aggregator. Optional because pre-Phase-4 v4 caches don't include it.
  user_id_to_username?: { [userId: string]: string };
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

// Phase 4 / TODO #5: all-time aggregator (year=all). Each accolade is a
// crown winner across the full league_chain history. ``user_id`` is the
// stable Sleeper id (null for legacy buckets keyed by display name only).
export interface WrappedAllTimeCrown {
  username: string;
  user_id: string | null;
  years_won: number;
}

export interface WrappedAllTimeTroll {
  username: string;
  user_id: string | null;
  total_troll_value: number;
  years_counted: number;
}

export interface WrappedAllTimeEfficiency {
  username: string;
  user_id: string | null;
  avg_efficiency_pct: number;
  years_counted: number;
}

export interface WrappedAllTimeTrades {
  username: string;
  user_id: string | null;
  total_trades: number;
}

export interface WrappedAllTimeNetValue {
  username: string;
  user_id: string | null;
  net_value_gained: number;
}

export interface WrappedAllTimeAccolades {
  luckiest: WrappedAllTimeCrown | null;
  unluckiest: WrappedAllTimeCrown | null;
  worst_start_sit: WrappedAllTimeTroll | null;
  most_efficient: WrappedAllTimeEfficiency | null;
  least_efficient: WrappedAllTimeEfficiency | null;
  most_active_trader: WrappedAllTimeTrades | null;
  biggest_net_gainer: WrappedAllTimeNetValue | null;
  biggest_net_loser: WrappedAllTimeNetValue | null;
}

export interface WrappedAllTimeYear {
  year: string;
  league_id: string;
  payload: WrappedResponse;
}

export interface WrappedAllTimeResponse {
  mode: 'all_time';
  all_time: WrappedAllTimeAccolades;
  years: WrappedAllTimeYear[];
}

// Discriminated union returned by the wrapped endpoint. Per-season payloads
// don't carry a ``mode`` field; all-time payloads do.
export type WrappedApiResponse = WrappedResponse | WrappedAllTimeResponse;

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

export interface WrappedTradeAsset {
  asset_id: string;
  label: string;
  sleeper_id: string | null;
  is_pick: boolean;
  // Average raw KTC value across the asset's active holding window.
  // Lives on the familiar 0-9999 scale -- the per-row breakdown number.
  avg_ktc: number;
  active_days: number;
  // The asset's contribution to its side's integral score. Mostly for
  // debugging / tooltips -- the headline numbers are on the side + trade.
  score: number;
}

export interface WrappedTradeSide {
  username: string;
  assets: WrappedTradeAsset[];
  // Side's integral score (concavity-transformed). Used to order sides
  // within a trade; ktc_equiv is the UI-friendly version.
  total_score: number;
  // Side's KTC-equivalent total -- "what constant KTC value, held over
  // the same window, would produce this same integral?". This is the
  // number we render per side ("+8,124 KTC").
  ktc_equiv: number;
}

export interface WrappedTrade {
  week: number;
  transaction_id: string;
  // ISO date (YYYY-MM-DD) the integral window opens on.
  trade_date: string;
  // ISO date (YYYY-MM-DD) the integral window closes on.
  evaluation_end: string;
  sides: WrappedTradeSide[];
  winner: string | null;
  // Concavity exponent used. Stamped so the inspector can show the
  // setting that produced this verdict.
  k: number;
  // Active days (non-offseason) inside the integral window. Both sides
  // share this denominator, so comparisons stay apples-to-apples.
  active_days: number;
  // Winner's KTC-equivalent edge over runner-up, as a per-active-season
  // rate (e.g. +3,127 KTC/yr). Headline number on the row.
  ktc_edge_per_season: number;
  // Same edge multiplied across the full active window (e.g. +5,200 KTC
  // total over 1.7 seasons). Paired with the per-season rate.
  ktc_edge_total: number;
}

export interface WrappedUserTrades {
  num_trades: number;
  // Sum of ktc_edge_per_season gained as winner minus share of losses.
  net_ktc_per_season: number;
}

export interface WrappedTradesPayload {
  trades: WrappedTrade[];
  by_user: { [user: string]: WrappedUserTrades };
  biggest_fleecing: WrappedTrade | null;
  most_active_trader: { username: string; num_trades: number } | null;
  // Concavity exponent and inclusive evaluation date the whole section
  // was computed under (both inherited from the per-trade fields, but
  // surfaced at the top so the UI can show a single "as of X" timestamp).
  k?: number;
  evaluation_end?: string;
}


// ---------------------------------------------------------------------------
// Trade inspector (GET /wrapped/sleeper/<league>/inspect_trade)
// ---------------------------------------------------------------------------
/** One point on a side's cumulative-race curve. Both sides share the
 *  same timeline (and the same ``active_days`` denominator), so any
 *  visual line crossing equals a verdict flip. */
export interface WrappedRaceChartPoint {
  date: string;          // ISO YYYY-MM-DD
  score: number;         // running integral score
  raw_area: number;      // running raw KTC-day area (pre-concavity)
  active_days: number;   // running active-day count
  ktc_equiv: number;     // running KTC-equivalent value -- the line we plot
}

export interface WrappedRaceChartSide {
  team_label: string;
  points: WrappedRaceChartPoint[];
}

export interface WrappedRaceChart {
  trade_date: string;
  evaluation_end: string;
  k: number;
  sides: WrappedRaceChartSide[];
  /** Every date the running first place changes hands. Render these as
   *  vertical reference lines on the chart. */
  crossover_dates: string[];
}

/** Per-asset raw-KTC sparkline series. One row per asset in the trade. */
export interface WrappedPerAssetSeries {
  team_label: string;
  asset_id: string;
  label: string;
  is_pick: boolean;
  sleeper_id: string | null;
  points: Array<{ date: string; value: number }>;
}

/** Full payload for the trade inspector modal. ``trade`` mirrors the
 *  row in ``WrappedTradesPayload.trades`` so the same row header can
 *  render in both places. */
export interface WrappedInspectTrade {
  trade: WrappedTrade;
  race_chart: WrappedRaceChart;
  per_asset_series: WrappedPerAssetSeries[];
  k: number;
  evaluation_end: string;
}


// ---------------------------------------------------------------------------
// Redraft trade inspector (GET /wrapped/sleeper/<league>/inspect_trade_redraft)
// ---------------------------------------------------------------------------
/** One acquired player's contribution to a side. */
export interface WrappedRedraftAsset {
  player_id: string;
  name: string;
  position: string;
  ros_points: number;
  games_played: number;
  ros_ppg: number;
  baseline_points: number;
  vorp: number;
}

export interface WrappedRedraftSide {
  username: string;
  assets: WrappedRedraftAsset[];
  total_ros_points: number;
  total_vorp: number;
}

export interface WrappedRedraftEvaluation {
  sides: WrappedRedraftSide[];
  /** "wash" or the winning user's username. */
  verdict: string;
  /** "wash" | "close" | "decisive" */
  margin_label: string;
  margin_vorp: number;
  window: { start_week: number; end_week: number; season: number };
  scoring: { qb_score_key: string; skill_score_key: string };
}

export interface WrappedInspectRedraftTrade {
  transaction_id: string;
  trade_week: number;
  evaluation: WrappedRedraftEvaluation;
  baseline_total_points: { [position: string]: number };
}

