# Phase 4 Plan & Progress

> Last updated: 2026-05-08. Branch: `dev/jgorel/newfeatures`.
> Test count after Phase-4 work so far: **215 backend tests passing**.

This file is the authoritative checklist for the in-flight Phase 4 work
(plus a couple of bug fixes that landed alongside it). When picking up in a
new thread, read this top-to-bottom before touching code.

---

## Status snapshot

| # | Item | State |
|---|---|---|
| 1 | Remove noisy weekly scoring chart from PlayerDetailPage | ✅ done |
| 2 | Link player names to detail page (Full Details link in expanded row) | ✅ done |
| 3 | Best Streamers backend (K/DEF avg + tests, cache v3→v4) | ✅ done |
| 4 | Best Streamers frontend (sortable table + hero cards in WrappedPage) | ✅ done |
| 5 | All-time aggregator (`year=all`) | ⏳ next up |
| 6 | Trade Analyzer (FantasyCalc value + season points) | ⬜ |
| 7 | Start/Sit Comparator (drawer on PlayerDetailPage) | ⬜ |

Side bug fix that landed mid-Phase-4: offseason "My Teams" came back empty
because `get_current_fantasy_year()` returned the just-finished season
whose leagues are now `status=complete`. Fix in
`backend/app/services/sleeper_client.py` probes both fantasy_year and
calendar_year, accepts `pre_draft|drafting|in_season|post_season`, dedups
by league_id.

---

## Files written/touched in Phase 4 so far

### Backend
- **`backend/app/services/wrapped/schedule.py`** — `WeeklyScores` gained
  `user_position_starter_points_by_week: Dict[user, Dict[pos, Dict[week, float]]]`.
  Bucketed at write-time inside `_process_week_matchups` using
  `players_meta[pid].fantasy_positions[0]`. Multi-starter same-pos sums.
- **`backend/app/services/wrapped/streamers_accolades.py`** (NEW)
  - `_STREAM_POSITIONS = ("K", "DEF")`
  - `StreamerEntry`, `StreamersPayload` dataclasses
  - `_league_has_position(roster_groups, pos)` — only true for sole-element groups
  - `_avg_position_starts(scores, user, pos)` — denominator = weeks_played (NOT weeks-with-points; benched-K weeks count, that's the whole point of "best streamer")
  - `calculate_streamer_accolades(scores, roster_position_groups)` — entry point
- **`backend/app/services/wrapped/pipeline.py`** — added
  `_build_streamers_section(ctx, weekly_scores)` and wired
  `"streamers": _build_streamers_section(...)` into `_build_payload`.
- **`backend/app/routes.py`** — wrapped cache version bumped
  `wrapped_v3_sleeper_…` → `wrapped_v4_sleeper_…`. **Don't bump again
  unless the streamers payload shape changes.**
- **`backend/tests/test_streamers_accolades.py`** (NEW) — 6 tests covering:
  both positions, K-only, no-K-no-DEF, missing weeks (zero-fills),
  winner picks max (K/DEF/combined), user with no starts at a position.
- **`backend/app/services/sleeper_client.py`** — offseason fix described above.

### Frontend
- **`frontend/src/pages/PlayerDetailPage.tsx`** — removed weekly scoring
  `<LineChart>`, `buildScoringSeries`, `SCORING_VARIANTS`,
  `PlayerDetailScoringYear` import. Kept the season summary stat cards and
  the ownership/start-rate `<LineChart>`.
- **`frontend/src/components/PlayerTable.tsx`**
  - Player name reverted to plain `<Text>` (clicking row toggles expanded panel — original behavior).
  - Added "Full Details →" `<RouterLink to={'/player/${PID}'}>` at the bottom-right of the expanded panel, with `onClick={e => e.stopPropagation()}`.
  - `import { Link as RouterLink } from 'react-router-dom';` is present.
- **`frontend/src/types/player.ts`** — added:
  ```ts
  export interface WrappedStreamerEntry { k_avg, def_avg, combined_avg: number|null; weeks_counted: number; }
  export interface WrappedStreamerWinner { username: string; average: number; }
  export interface WrappedStreamersPayload {
    positions_included: string[]; // subset of ["K","DEF"]
    by_user: { [user: string]: WrappedStreamerEntry };
    best_kicker: WrappedStreamerWinner | null;
    best_defense: WrappedStreamerWinner | null;
    best_combined: WrappedStreamerWinner | null;
  }
  // and `streamers?: WrappedStreamersPayload` on `WrappedResponse`
  ```
- **`frontend/src/pages/WrappedPage.tsx`** — added `StreamersSection`
  component (sortable table, hero accolade cards). Default sort =
  combined when both K+DEF, else whichever exists. Nulls sort to bottom.
  Rendered conditionally after the trades section.

---

## Streamers payload shape (already shipped)

```jsonc
"streamers": {
  "positions_included": ["K", "DEF"],         // subset of these two
  "by_user": {
    "alice": { "k_avg": 7.5, "def_avg": 9.2, "combined_avg": 16.7, "weeks_counted": 14 }
  },
  "best_kicker":   { "username": "alice", "average": 8.1 } | null,
  "best_defense":  { "username": "bob",   "average": 10.3 } | null,
  "best_combined": { "username": "alice", "average": 16.7 } | null
}
```

Section is hidden if `positions_included` is empty (league rosters neither K nor DEF).

---

## TODO #5 — All-time aggregator (`year=all`)

**User intent (verbatim):** "Aggregator - all time records for the header
- such as who has been luckiest over all the years, unluckiest, who has
been the worst start sit person, etc etc."

### Backend plan

- Currently `/wrapped/sleeper/<league_id>?year=all` returns 501 (or
  similar). Replace with:
  1. Use existing `get_league_season_chain(league_id)` (in
     `app.services.sleeper_league_lookup`) to walk `previous_league_id`
     back as far as it goes. Each entry has `{league_id, year}`.
  2. For each entry, call `compute_wrapped(prev_league_id, year)` —
     this is already individually Redis-cached as `wrapped_v4_…` so
     repeated all-time hits are cheap after the first warm-up.
  3. Build an `all_time` aggregator section with:
     - `luckiest`, `unluckiest` — sum of luck deltas across all seasons by username (or count of "luckiest finishes")
     - `worst_start_sit` — sum of `troll_value` totals per user across years (already exposed in roster_moves accolades)
     - Optional: `best_drafter` (cumulative draft VOR), `most_consistent`, `most_active_trader` count
  4. Return shape:
     ```jsonc
     {
       "mode": "all_time",
       "all_time": {
         "luckiest": {"username": "...", "value": ..., "years_counted": N},
         "unluckiest": {...},
         "worst_start_sit": {...},
         /* etc */
       },
       "years": [
         { "year": "2024", "league_id": "...", "payload": <existing WrappedResponse> },
         { "year": "2023", "league_id": "...", "payload": <...> }
       ]
     }
     ```
  5. **Username matching across years is non-trivial** — Sleeper user_ids
     are stable but display_names can change. Aggregate by `user_id`
     where possible, fall back to current display_name.

- Place new logic in `backend/app/services/wrapped/all_time.py`
  (helpers + dataclass).
- Wire into `routes.py` where the existing wrapped route handles `year`.
- Tests: `backend/tests/test_wrapped_all_time.py` covering:
  - Single-year chain (degenerate — should still work, all_time == year[0])
  - Multi-year chain, simple aggregator math
  - Username-changes-mid-chain handled via user_id

### Frontend plan
- WrappedPage already takes `year` from the dropdown. Add an "All Time"
  option that drives `year=all`.
- When `payload.mode === 'all_time'`, render:
  - New top hero strip: all-time accolade cards (luckiest, unluckiest, worst start/sit, most active trader, etc.)
  - Year picker / accordion below to expand each year's existing sections (re-use the same render code by extracting current sections into a `<YearSections payload={...} />` component).

---

## TODO #6 — Trade Analyzer

**User intent (verbatim):** "Trade analyzer... lets honestly just use
fantasy calc value and points."

### Backend plan
- New endpoint: `POST /trade/analyze`
  ```jsonc
  // body
  { "league_id": "...", "year": "2025",
    "side_a": ["pid1","pid2"], "side_b": ["pid3","pid4"] }
  ```
- For each side, compute totals using helpers we already have:
  - **FantasyCalc value:** existing module pulls cached values keyed by `(is_dynasty, num_qbs)`. Reuse the same lookup the trades section uses.
  - **Season points:** use the league's effective scoring chain (we already do this for drafts/trades).
- Response shape:
  ```jsonc
  {
    "side_a": { "fantasy_calc_total": ..., "season_points_total": ..., "players": [{pid,name,fc_value,season_pts}, ...] },
    "side_b": { ... },
    "value_gap": ...,         // side_a - side_b on FC
    "points_gap": ...,
    "winner": "side_a" | "side_b" | "even"
  }
  ```
- Tests: empty sides, picks-only sides, FantasyCalc misses, scoring fallback chain.

### Frontend plan
- New page `/trade` (route in `App.tsx`).
- Two side panels (A vs B), player picker autocomplete sourced from the
  existing `players.json` blob endpoint (or a slim `/players/search` if perf bites).
- Result panel shows totals, gap, winner.
- Probably needs league + year selector at top (default to user's first
  league + current fantasy year).

---

## TODO #7 — Start/Sit Comparator

**User intent:** drawer on PlayerDetailPage at `/player/PID` that lets
you compare to a second player via `?compare=PID2`.

### Frontend plan
- On `PlayerDetailPage`, add a "Compare to…" search input. Selecting a
  player updates the URL to `?compare=<pid>`.
- When `compare` is set:
  - Fetch both player payloads in parallel
  - Render a side-by-side layout: stat cards for both, charts overlaid (extend `LineChart` to accept multi-series — already does)
  - Highlight better/worse per metric

### Backend plan
- `/player/<pid>` endpoint already exists. No new endpoint required —
  frontend just hits it twice.
- Possibly add a `?include=` parameter if we want a compact "compare-only"
  payload, but not necessary for v1.

---

## Conventions / gotchas to remember

- **Wrapped Redis cache key** is `wrapped_v{N}_sleeper_{league_id}_{year}`.
  Bump N whenever payload shape changes. Currently **v4**.
- **`get_current_fantasy_year()`** returns Jan-Jul → previous year. For
  "list user's leagues" use BOTH that and `datetime.now().year` (see
  `_candidate_league_years()` in `sleeper_client.py`).
- **Username vs user_id** — display names change across seasons. When
  aggregating across years, key by user_id, render by current display_name.
- **`players_meta`** is loaded from `players.json` in pipeline; only
  current-season blob is current. Historical years should use
  `player_season_scoring_{year}.json`. Already handled in pipeline via
  `if str(ctx.year) == str(get_current_fantasy_year())`.
- **Frontend RouterLink inside row** — always `onClick={e => e.stopPropagation()}` so it doesn't trigger the parent row's expand/collapse handler.
- **Test count baseline:** 215 passing as of streamers section. If a
  PR drops below that without adding equal coverage, something regressed.

---

## When picking this up in a fresh thread

Open this file. Then:
1. `cd backend && python -m pytest -q --tb=short` — should be 215 passing.
2. Move TODO #5 to in-progress.
3. Follow the "All-time aggregator" plan above.

Last in-flight item: starting #5. No work in progress on it yet.
