# Project handoff — `fantasy-web-tool`

> Last updated: 2026-05-15. Branch: `dev/jgorel/newfeatures`.
> Authoritative state-of-the-repo for fresh threads. Read this top-to-bottom
> before touching code. `docs/PHASE_4_PLAN.md` is **stale** (pre-dates the
> KTC trade evaluator) — defer to this file when they conflict.

---

## Repo layout

- `backend/` — Flask app, Redis-cached, blob-fed. Public API consumed by frontend.
- `frontend/` — React + Chakra UI. Pages: `HomePage`, `RankingsPage`,
  `PlayerDetailPage`, `WrappedLandingPage`, `WrappedPage`.
- `azure-functions/` — scheduled scrapers (Boris Chen, DraftKings, Sleeper
  scoring, KTC, FantasyCalc) that write JSON blobs to Azure Storage.
- `tools/` — local helpers: fixtures, `sync_shared.py`,
  `smoke_evaluate_league.py`.
- `shared/fantasy_common.py` — duplicated to `backend/app/_fantasy_common.py`
  and `azure-functions/_fantasy_common.py` via `tools/sync_shared.py`
  (guarded by `test_shared_in_sync.py`).
- `docs/PHASE_4_PLAN.md` — historical; do not rely on for current state.

---

## Current baselines

- **339 backend tests passing** (`cd backend && python -m pytest -q`).
- Frontend: `npx tsc --noEmit -p tsconfig.json` clean. No jest suite in CI.

If a PR drops below 339 without commensurate new coverage, something
regressed.

---

## Phase 4 status

| # | Item | State |
|---|---|---|
| 1 | Remove noisy weekly scoring chart from `PlayerDetailPage` | ✅ done |
| 2 | "Full Details →" link in `PlayerTable` expanded row | ✅ done |
| 3 | Best Streamers backend (K/DEF avg + tests) | ✅ done |
| 4 | Best Streamers frontend (sortable table + hero cards in `WrappedPage`) | ✅ done |
| 5 | All-time aggregator (`?year=all`) — backend + frontend | ✅ done |
| 6 | Trade analyzer | ⚠️ **superseded** — became the KTC value-integral evaluator (see below). The originally-planned "FantasyCalc value + season points" simple analyzer was abandoned in favor of this more rigorous approach. |
| 7 | Start/Sit Comparator (drawer on `PlayerDetailPage` via `?compare=PID2`) | ⬜ not started |

---

## Major systems landed since the original Phase-4 plan

### KTC value-integral trade evaluator
Replaced the simple FantasyCalc-snapshot trade analyzer with a per-day
value-over-time integral driven by historical KTC snapshots.

**Backend package** `backend/app/services/trade_eval/`:
- `value_integral.py` — core math
- `active_window.py` — `ActiveCalendar`, `is_active`, `iter_active_days`
  (defines the dynasty-relevant calendar window for integration)
- `trade_evaluator.py` — dataclasses `TradeAsset`, `TradeSide`, `Trade`,
  `AssetEvaluation`, `SideEvaluation`, `TradeEvaluation`, `RaceChart{,Point,Side}`.
  Public fns: `evaluate_asset`, `evaluate_trade`, `make_blob_resolver`,
  `build_race_chart`.
- `pick_handoff.py` — splices in the value of whoever a future pick actually
  became (via `make_pick_aware_resolver`, `splice_series`,
  `encode_pick_key`/`parse_pick_key`).
- `sleeper_trade_loader.py` — `SeasonContext`, `NormalizedTrade`/`Side`/`Pick`,
  `load_league_chain`, `normalize_all_trades`, `build_pick_to_player`.
- `sleeper_trade_adapter.py` — turns normalized Sleeper trades into evaluator
  `Trade` objects (`build_trade`, `merged_roster_labels`).
- `sleeper_scoring.py` — Sleeper historical & in-season scoring scraper
  (`build_season`, `update_current_week`, `bootstrap_history`).
- `fantasycalc_values.py` — FantasyCalc daily snapshot scraper
  (`snapshot_format`, `snapshot_all`).
- `ktc_scraper.py` — weekly KTC full-board snapshot
  (`snapshot_format`, `snapshot_all`, `parse_html`).
- `ktc_top500_daily.py` — daily top-500 appender (`append_daily`).
- `blob_layout.py` — central blob path conventions.

**Wiring into Wrapped:**
- `backend/app/services/wrapped/trade_accolades.py` — rewritten to use the
  value-integral evaluator. Public surface: `calculate_trade_accolades`,
  `inspect_trade`. Still emits `biggest_fleecing` + `most_active_trader`.
- `backend/app/services/wrapped/ktc_blob_loader.py` — cached loader for the
  historical KTC blob: `get_raw_blob`, `get_flat_blob`,
  `find_asset_id_by_sleeper_id`, `reset_cache`.

**Route:**
- `GET /wrapped/sleeper/<league_id>/inspect_trade?transaction_id=...&year=...`
  - Returns `{trade, race_chart, per_asset_series, k, evaluation_end}`.
  - **Dynasty-only**: returns 400 for redraft (KTC integral is the wrong
    tool for short-window swap evaluation).
  - Cached at `wrapped_inspect_v1_{league}_{year}_{txn}` for 24h.
  - Returns 503 if the KTC historical blob is unavailable (so UI can show
    "value history unavailable" rather than crash).

**Frontend:**
- `frontend/src/components/TradeInspector.tsx` — race chart (recharts) plus
  per-asset sparklines, fed by `/inspect_trade`. Used inline from the
  Wrapped trades section.

**Tests:**
- `test_trade_eval_integral.py`
- `test_trade_eval_pick_handoff.py`
- `test_trade_eval_scrapers.py`

**Azure Functions scrapers** (also mirrored under
`backend/app/services/trade_eval/`):
- `azure-functions/trade_eval/ktc_scraper.py` — weekly snapshot
- `azure-functions/trade_eval/ktc_top500_daily.py` — daily appender
- `azure-functions/trade_eval/trade_evaluator.py`

### All-time aggregator (`?year=all`)
- `backend/app/services/wrapped/all_time.py` — `build_all_time_payload`
  walks `get_league_season_chain()` newest-first, calls cached
  `compute_wrapped()` per season, aggregates by Sleeper `user_id`
  (`name:<display>` fallback for legacy caches), keeps most-recent
  display name per bucket. One bad season is logged and skipped.
- Route: `/wrapped/sleeper/<league_id>?year=all`. Cached at
  `wrapped_all_v1_sleeper_{league_id}` for 1h.
- Per-season payload now carries `meta.user_id_to_username` (additive; no
  cache version bump).
- Accolades emitted: `luckiest`, `unluckiest` (years_won crowns),
  `worst_start_sit` (sum positive troll values), `most_efficient` /
  `least_efficient` (avg manager-efficiency pct), `most_active_trader`
  (sum trade counts), `biggest_net_gainer` / `biggest_net_loser` (sum
  FantasyCalc net value gained).
- Frontend (`frontend/src/pages/WrappedPage.tsx`): extracted
  `YearSections` (pure over `WrappedResponse`); added `AllTimeAccolades`
  (8 crown cards with emoji) and `AllTimeView` (hero strip + Chakra
  `Accordion` of `YearSections` per year). Dropdown gains an
  `"All time"` entry. `handleYearChange` skips `resolveSleeperLeague`
  for `?year=all`.
- Tests: `backend/tests/test_wrapped_all_time.py` (7 tests).

### Best Streamers (K + DEF)
- `backend/app/services/wrapped/streamers_accolades.py`
- `WeeklyScores.user_position_starter_points_by_week` populated in
  `schedule.py`
- Wrapped cache bumped v3 → **v4** (still current; do NOT bump again
  unless the per-season payload shape changes)
- Frontend: `StreamersSection` in `WrappedPage.tsx`, sortable table +
  hero accolade cards

### Offseason "My Teams" fix
- `backend/app/services/sleeper_client.py` — `_candidate_league_years()`
  probes both fantasy_year and calendar_year, accepts
  `pre_draft|drafting|in_season|post_season`, dedups by `league_id`.

---

## What's still planned

### TODO #7 — Start/Sit Comparator (only remaining Phase-4 item)
**User intent:** drawer on `PlayerDetailPage` at `/player/PID` that lets
you compare to a second player via `?compare=PID2`.

**Frontend plan:**
- Add a "Compare to…" search input on `PlayerDetailPage`. Selecting a
  player updates the URL to `?compare=<pid>`.
- When `compare` is set:
  - Fetch both player payloads in parallel.
  - Render side-by-side stat cards.
  - Overlay charts (`LineChart` already supports multi-series).
  - Highlight better/worse per metric.

**Backend plan:**
- `/player/<pid>` already exists. No new endpoint required — frontend
  hits it twice.
- Could add a `?include=` slim payload later if perf bites; not v1.

### Likely follow-ups (not formally planned)
- Standalone trade-analyzer page (`/trade` route) for building
  hypothetical trades, not just inspecting historical ones. The
  evaluator + `TradeInspector` component already exist; just need a
  player picker + route.
- Surface `inspect_trade` from the per-trade row in the Wrapped trades
  table if not already done.

---

## Conventions / gotchas

### Caching
- **Per-season Wrapped key**: `wrapped_v{N}_sleeper_{league_id}_{year}`.
  Bump N **only** when the per-season payload shape changes. Currently
  **v4**.
- **All-time Wrapped key**: `wrapped_all_v1_sleeper_{league_id}` — 1h TTL
  (shorter than per-year so refreshed per-year caches propagate quickly).
- **Trade inspector key**: `wrapped_inspect_v1_{league}_{year}_{txn}` —
  24h TTL. Trades are immutable post-acceptance so a long TTL is safe.
- **Player detail key**: `player_detail_v1_{player_id}` — 1h TTL.

### Sleeper year handling
- `get_current_fantasy_year()` returns previous calendar year Jan–Jul.
  For "list user's leagues", probe both fantasy_year and calendar_year
  (see `_candidate_league_years()`).
- For per-year scoring blobs, prefer `player_season_scoring_{year}.json`.
  `players.json` is current-season only — using it for past seasons
  yields wrong values, so pipeline degrades to an empty section instead.

### User identity across seasons
- Sleeper `user_id`s are stable; display names mutate.
- Aggregate by `user_id`, render by the most recent display name.
- For legacy caches without `meta.user_id_to_username`, fall back to
  bucketing by display name with prefix `name:` so it can't collide
  with a real user_id.

### Trade evaluator
- Dynasty-only. Redraft leagues 400 on `/inspect_trade`.
- KTC blob load failure → 503 with `{error, detail}`, not a crash.
- Pick handoff splices the picked player's value into the pick's series
  on/after the draft date.

### Frontend
- `RouterLink` inside a clickable row: always
  `onClick={e => e.stopPropagation()}`.
- Chakra `AccordionButton` text inherits an unstyled color and looked
  white in our theme — `AllTimeView` sets explicit `color="gray.800"` on
  the year box and `color="gray.600"` on `AccordionIcon`.

### File encoding
- **Never** write files in this repo via PowerShell
  `Set-Content -Encoding utf8` — it round-trips through cp1252 and
  mojibakes emoji + em-dashes. Use Python
  (`Path.write_bytes(text.encode('utf-8'))`) or the file-editing tools
  for any file containing non-ASCII.

### Shared module sync
- Changes to `shared/fantasy_common.py` must be propagated via
  `python tools/sync_shared.py`. `test_shared_in_sync.py` guards this.

---

## When picking up

1. `cd backend && python -m pytest -q` — expect **339 passing**.
2. `cd frontend && npx tsc --noEmit` — expect clean.
3. Decide direction:
   - **TODO #7 (Start/Sit Comparator)** — only remaining formal Phase-4 item.
   - **Standalone trade analyzer page** — leverages KTC evaluator + existing
     `TradeInspector` component.
   - Whatever new direction the user requests.
4. Update this file (`docs/HANDOFF.md`) as the source-of-truth when work
   lands. Leave `docs/PHASE_4_PLAN.md` as historical context only.
