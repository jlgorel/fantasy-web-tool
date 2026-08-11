# Current Handoff — Draft Help, Accountability, and Next Work

> **Created:** 2026-08-10  
> **Current branch:** `dev/jgorel/addinbacktestingandproof`  
> **Status:** Phases 1, 2, A, B, and C are implemented. The first current-year
> external value source (ElBoberto) is now generated and published, with an
> exact-profile ElBoberto CheatSheet paste workflow. The working tree
> has new uncommitted source-ingestion/UI work.
> **Use this document as the authoritative starting point for the next thread.**
> `docs/DRAFT_HELP_HANDOFF.md` describes an older state and contains obsolete
> phase goals, test counts, source information, algorithm details, and proof
> numbers.

---

## 0. Read this first

The product is a live redraft/keeper snake-draft assistant. It combines:

1. A current ADP board that predicts when players will be taken.
2. Finished cross-position player values supplied by an external specialist or
   by the user.
3. A Monte Carlo engine that decides what to do with those inputs across the
   user's current and future picks.
4. A Sleeper live-draft room, a custom/manual draft room, historical draft
   tendencies, and a permanent methodology proof.

### Non-negotiable product guardrail

**Do not build, derive, blend, or default to an internally calculated VBD/VORP.**

The user explicitly does not want projection-to-VBD logic created by this
project to contaminate a Monte Carlo approach that is already working. Other
sources have more data and better methodology. The system must consume a
trusted provider's **finished cross-position Value/VORP** as its default input.

That means:

- FantasyPros DraftWizard is currently an **ADP source only**.
- Do not turn FantasyPros projections, Vegas lines, consensus rankings, or raw
  fantasy points into the default VBD/VORP.
- Do not wire `backend/app/services/draft_help/custom_vbd.py` into the default
  pipeline. It is an older scaffold and is not the product direction.
- Matching names to Sleeper IDs, selecting the provider's published league
  configuration, validating data, and normalizing file shape are allowed.
- Browser ElBoberto CheatSheet paste and manual value adjustments remain
  supported overrides for exact custom profiles.
- If no valid current value source exists, show an ADP-only board but refuse to
  produce recommendations until at least 50 external/user values are supplied.
- Prefer a source that also publishes a finished flex-aware value or companion
  field if available. Never invent a replacement baseline to fill a source gap
  without an explicit product decision.

ElBoberto is the provisional default 2026 provider. DraftSheets remains the
preferred comparison/cloud candidate. Football Absurdity was removed from the
product because its current value curve assigns zero to too many relevant
players.

---

## 1. Current validation baseline

The final validation run after Phase C cleanup reported:

- **Backend:** 518 tests passed.
- **Frontend:** 13 tests passed.
- **TypeScript:** `npx tsc --noEmit -p tsconfig.json` passed.
- **Production frontend build:** passed.
- **Whitespace/patch integrity:** `git diff --check` passed.
- **Browser validation:** Custom Draft Room loaded a historical board, displayed
  player headshots/team logos, returned confidence and middle-50% ranges, and
  showed `cached state` on an unchanged second request.
- **Website-sized sim benchmark:** approximately 2.344 seconds uncached for a
  300-player board, 60 rollouts, and an eight-player candidate request. An exact
  repeated state is served from the five-minute recommendation cache.

Commands:

```powershell
# Python environment
# c:\Users\jlgor\Documents\fantasy-web-tool\.venv\Scripts\python.exe

# Full backend suite
cd backend
$env:USE_FIXTURE_BLOBS="1"
& "../.venv/Scripts/python.exe" -m pytest -q

# Frontend validation
cd frontend
npx tsc --noEmit -p tsconfig.json
$env:CI="true"
npm test -- --watchAll=false
Remove-Item Env:CI -ErrorAction SilentlyContinue
npm run build

# Patch integrity
cd ..
git diff --check
```

The frontend test output has a non-blocking existing warning about deprecated
`ReactDOMTestUtils.act`; the tests pass.

---

## 2. Completed work

### Phase 1 — permanent draft-approach proof

Implemented in `tools/draft_proof.py` with committed-style output artifacts in
`tools/draft_proof_output/` and a frontend view in
`frontend/src/components/DraftProofView.tsx`.

The harness runs three study strategies against a shared field:

- Monte Carlo recommender.
- Greedy highest-VBD.
- Pure ADP.

It grades all three with the raw projected fantasy points of each roster's
optimal starting lineup. This keeps the **grading** currency independent: the
strategy is not declared the winner merely because it optimizes the same VBD
metric used to draft.

The Phase C rerun uses one coherent sampled field-opponent board for the whole
simulated draft. The full deterministic grid is:

- Seasons: 2022, 2023, 2024.
- Team counts: 8, 10, 12, 14.
- Formats: 1QB and superflex.
- Scoring: half PPR.
- 40 drafts per cell, 960 drafts total.
- 20 recommendation rollouts per simulated MC decision.

Latest aggregate results from `tools/draft_proof_output/summary.json`:

- MC lineup points: 1643.5 per team.
- Greedy-VBD lineup points: 1627.7.
- Pure-ADP lineup points: 1479.0.
- **MC vs greedy VBD: +15.9 points/team, 64.1% wins, 95% CI ±2.8.**
- **MC vs pure ADP: +164.5 points/team, 98.4% wins, 95% CI ±6.9.**

The claim is deliberately limited:

> Assuming the imported rankings/values are accurate, Monte Carlo treatment of
> current and future availability builds stronger projected starting lineups
> than always taking the highest current VBD, and far stronger lineups than
> drafting purely by ADP.

Do not turn this into a claim that the imported values themselves are accurate.
The 2023 slice is the softest season, so preserve the aggregate confidence
intervals and per-slice results rather than overstating every cell.

Useful files:

- `tools/draft_proof.py`
- `tools/draft_proof_output/results.csv`
- `tools/draft_proof_output/summary.json`
- `tools/draft_proof_output/draft_proof.png`
- `frontend/public/draft_proof_summary.json`
- `frontend/src/components/DraftProofView.tsx`
- `backend/tests/test_draft_proof_harness.py`

Regenerate the full proof only when simulation behavior changes:

```powershell
$env:USE_FIXTURE_BLOBS="1"
& ".venv/Scripts/python.exe" tools/draft_proof.py `
  --drafts 40 --nsims 20 --workers 8 --emit-frontend
```

The last run took about 830 seconds on this machine.

### Phase 2 — weekly projection accountability infrastructure

This phase is implemented, but its real evidence naturally accumulates only
when the in-season Azure schedules run and actual results become available.

`azure-functions/vegas_accuracy.py` contains pure, offline-testable helpers:

- `merge_week_capture()` locks the latest pregame projection per player/week.
- Thursday players are never deleted when a later Sunday scrape omits them.
- `fp_overall_rank_by_pid()` joins FantasyPros ECR to Sleeper IDs.
- `compile_review()` reports points MAE, RMSE, bias, correlation, positional-rank
  error, and Vegas-vs-FantasyPros head-to-head results weekly and season-to-date.

`azure-functions/function_app.py` wires the blob I/O:

- `capture_vegas_history()` writes `projection_history_{year}.json`.
- `build_projection_review()` writes `projection_review_{year}.json`.
- Existing in-season projection scrapes call `capture_vegas_history()`.
- `weekly_projection_accuracy_review` runs Tuesdays at 12:00 UTC after
  refreshing Sleeper actuals.

The backend exposes `GET /projection-review?year=YYYY`.

Tests:

- `backend/tests/test_vegas_accuracy.py`

Important distinction: this is accountability for the existing **weekly** Vegas
projection feature. It is not season-long draft VBD, and it must not be reused
to create an internal draft value model.

### Old Phase 3 — abandoned/backburnered

The old plan was to calculate a Vegas-weighted, season-long VBD. It was rejected
because season-long player betting markets are too sparse and the user does not
want an internally calculated VBD/VORP.

Do not revive this phase unless the user explicitly reverses that decision.

### Phase A — current ADP plus external/user values

ADP and player value are intentionally separate concepts:

- **ADP** answers when the market is likely to draft a player.
- **Value/VORP** answers how desirable the player is across positions.
- The Monte Carlo engine needs both but must not confuse them.

Current-season ADP:

- Source: FantasyPros DraftWizard mock-draft ADP.
- Parser: `azure-functions/fantasypros_adp.py`.
- Explicit route tokens: `1qb-std`, `1qb-half`, `1qb-ppr`, `2qb-std`,
  `2qb-half`, and `2qb-ppr`.
- Team counts: 8, 10, 12, 14.
- Scoring: standard, half PPR, full PPR.
- Total configurations: 24.
- DraftWizard's `round.slot` display is converted to overall pick correctly.
- Observed standard deviation, high/low, team, and drafted percentage are
  retained when available.

Production refresh:

- `refresh_draft_adp()` in `azure-functions/function_app.py` builds, validates,
  snapshots the prior healthy blob, and publishes `draft_adp_{year}.json`.
- `daily_draft_adp_refresh` runs at 10:00 UTC only in July, August, and September.
- Invalid or partial upstream output is rejected rather than replacing a healthy
  production blob.

Historical ADP:

- `azure-functions/draft_adp.py` retains the reusable historical
  FantasyFootballCalculator builder.
- `tools/build_draft_adp.py` uses FantasyPros for the current year and FFC for
  historical years.
- `tests/fixtures/blobs/` currently contains ADP fixtures for 2022, 2023, 2024,
  and 2026. The current 2026 fixture is normally ignored by Git.

Current values:

- Historical ranking fixtures exist for 2022–2025 and came from external
  BeerSheets/ElBoberto-style workbooks.
- ElBoberto is the selected provisional default external 2026 value source.
- Version 0.4 of the public 2026 workbook was discovered from the creator's
  Reddit post, downloaded from Dropbox, recalculated in desktop Excel for all
  24 supported configurations, conservatively matched to the production
  Sleeper player map, validated at 300 players/config with zero unmatched
  names, and published to Azure as `draft_rankings_2026.json`.
- `tools/refresh_elboberto_values.py` performs that complete workflow in one
  command. `--upload` validates the complete candidate, snapshots an existing
  healthy blob to `draft_rankings_2026_prev.json`, then publishes. It can read
  the Azure connection string from the environment or the ignored local Azure
  settings file without logging the credential.
- The generator still uses the provider's finished `AvgVBD`; it does not
  recreate or alter ElBoberto's VBD methodology. The current production profile
  is 4-point passing TD, six bench spots, 1 QB/2 RB/2 WR/1 TE/1 FLEX, with the
  existing 1QB-or-2QB configuration dimension.
- This refresh is automated as a local one-command workflow but still requires
  Windows desktop Excel/`xlwings`. It cannot yet run inside Azure Functions.
- Representative 12-team half-PPR source cells are pinned by regression:
  Jahmyr Gibbs 211.01, Bijan Robinson 206.53, Christian McCaffrey 169.73,
  and Derrick Henry 138.38. These are ElBoberto `AvgVBD` values, not an
  inverted rank sequence.
- The board displays independent `ADP`, provider `Source VBD`, and effective
  `Used VBD` columns. Saved pasted/provider imports can no longer silently hide
  ElBoberto: a warning reports the override count and `Use provider values`
  removes bulk imports while preserving manual edits and Avoid selections.
- ADP cache loading rejects empty/partial cached blobs and reloads once when the
  requested configuration is missing, preventing a newly available healthy ADP
  blob from leaving source metadata and player ADPs blank for five minutes.
- If only the current ADP blob exists, `rankings_config_players()` returns an
  ADP-only player pool with `vbd=None` and `fpts=None`; it invents nothing.
- `POST /draft-help/sim` rejects an ADP-only board unless at least 50 valid
  `value_overrides` are provided.

Browser-local custom value support:

- The visible bulk workflow accepts the real ElBoberto CheatSheet clipboard
  layout. It locates the contiguous `OVR / Player / Pos / VBD` block whether the
  user copies only that block or the whole used sheet.
- Matching is conservative by normalized exact name plus position. Invalid,
  duplicate, ambiguous, and unmatched rows are surfaced rather than guessed.
- Manual per-player values override pasted values.
- Football Absurdity and the generic file-upload control were removed from the
  UI. The current Football Absurdity sample matched 168 names but assigned zero
  to 98 players, collapsing too much of the utility curve for this algorithm.
- Browser storage uses a v2 key containing the exact starters, bench size, and
  passing-TD setting so values cannot leak across materially different leagues.
- Custom-room dropdowns cover QB 1–2, RB 1–3, WR 1–3, TE 0–3, FLEX 0–3, bench
  3–8, 4/6-point passing TD, 8/10/12/14 teams, three PPR settings, and optional
  superflex. Only the published profile uses centralized provider VBD; every
  mismatch disables provider VBD and requires at least 50 pasted/manual values.
- `Avoid` excludes a player from the user's candidate/pick policy.
- Manually valued players are force-evaluated as priority candidates even when
  outside the normal shortlist.
- Settings persist only in browser `localStorage`, keyed by
  season/team-count/PPR/1QB-or-superflex. They are not synced across browsers or
  devices.
- The shared player combobox renders its result list in a fixed-position portal
  with its own scroll area, so accordion and board overflow containers no longer
  clip all but the first result.
- Local backend settings load with values redacted; storage keys and other
  credentials must never be printed to logs or handoffs.

Useful files:

- `azure-functions/fantasypros_adp.py`
- `azure-functions/draft_adp.py`
- `azure-functions/function_app.py`
- `tools/build_draft_adp.py`
- `tools/build_draft_rankings.py`
- `tools/refresh_elboberto_values.py`
- `azure-functions/draft_values.py`
- `backend/app/services/draft_help/summaries.py`
- `frontend/src/utils/customDraftValues.ts`
- `backend/tests/test_draft_values_pipeline.py`
- `backend/tests/test_fantasypros_adp_pipeline.py`
- `backend/tests/test_draft_adp_pipeline.py`
- `frontend/src/utils/customDraftValues.test.ts`

### Phase B — unified live/custom Draft Room

The Draft Help page now has three top-level areas:

1. **Draft Room** — default.
2. **Draft Tendencies** — historical Your Habits, This League, and Opponents.
3. **Proof** — permanent strategy evidence.

Draft Room entry modes:

- Direct Sleeper draft ID or full Sleeper draft URL.
- Sleeper username → current redraft/keeper leagues → active/upcoming draft.
- Custom room with a manually advanced snake board.

Supported live drafts:

- Sleeper only.
- Snake drafts only.
- Redraft and keeper.
- 1QB and superflex/2QB.
- Dynasty startup and rookie drafts are rejected.
- Draft-ID-only users select their slot manually if no username resolves it.

Live behavior:

- Polls every five seconds while drafting/paused.
- Polls every 20 seconds pre-draft.
- Stops polling after completion and while the browser tab is hidden.
- Conditional metadata polling avoids re-fetching picks when `last_picked` and
  status are unchanged.
- Manual refresh remains available.
- Shows the live board, current pick, on-clock slot, picks until the user,
  upcoming user picks, roster, and collapsed recent-picks history.
- Recommendations auto-run once for each changed on-clock state only when it is
  actually the user's pick. A manual recommendation is also gated to the user's
  real turn; no speculative one/two-pick-ahead recommendation is cached as fact.
- The scoring selector can override bad/incomplete Sleeper scoring metadata.
- Multiple same-position alternatives are allowed in recommendation results.

Sleeper API lag handling:

- `Mark drafted` applies an optimistic local pick immediately.
- `Undo local pick` reverses the latest local action.
- Later Sleeper responses confirm matching local picks or reconcile conflicts.
- The UI explicitly warns users that Sleeper's public API can lag.

Visual/UI improvements:

- Searchable keyboard player combobox.
- Shared color-coded lineup builder for Live and Custom rooms.
- Lazy Sleeper-CDN player headshots and small team-logo overlays.
- Missing images hide cleanly and do not block the board.
- Available players are prioritized over pick history.

Useful files:

- `backend/app/services/draft_help/live_draft.py`
- `backend/app/services/draft_help/draft_fetch.py`
- `backend/app/routes.py`
- `frontend/src/components/LiveDraftView.tsx`
- `frontend/src/components/MockDraftView.tsx`
- `frontend/src/components/PlayerCombobox.tsx`
- `frontend/src/components/DraftPlayerAvatar.tsx`
- `frontend/src/utils/draftRoster.ts`
- `frontend/src/pages/DraftHelpPage.tsx`
- `frontend/src/api/client.ts`
- `frontend/src/types/draft.ts`
- `backend/tests/test_live_draft.py`
- `frontend/src/components/LiveDraftView.test.tsx`
- `tests/fixtures/sleeper_live_draft_1392134959602356224.json`

Small traded-pick support exists in the backend's future-pick schedule, but the
user explicitly does not want traded-pick complexity to become a priority.

### Phase C — coherent Monte Carlo, confidence, cache, and visuals

Core engine: `backend/app/services/draft_help/sim.py`.

Important model semantics:

- `SimPlayer.proj` is the externally supplied cross-position Value/VORP currency,
  not raw fantasy points.
- ADP and ADP standard deviation control opponent availability/timing.
- The user's simulated future picks maximize starter value plus geometrically
  discounted depth. Starters count 100%; the best backup at each startable
  position counts 10%, the second 1%, and the third 0.1%. An ordinary bench
  player cannot outweigh a starter upgrade, while an extreme value can beat a
  genuinely tiny starter edge.
- Candidate pool includes top ADP players, best-value and next-due players at
  each startable position, plus manually adjusted priority candidates.
- `Avoid` affects the user's picks but not opponents.
- `likely_next` is a coherent conditional continuation path, not independent
  per-slot modes that can describe an impossible draft.

Coherent opponent boards:

- `_draw_opponent_order()` samples each available player's latent priority once
  from `Normal(adp, adp_stdev)` for a rollout.
- Opponents consume that fixed order for the entire rollout and skip players
  removed by the user's picks.
- `recommend_pick()` creates exactly `n_sims` boards and reuses the same boards
  for every candidate (common random numbers).
- This removes per-opponent-pick redraw incoherence, reduces candidate-comparison
  noise, and materially improves speed.
- The obsolete `_opponent_pick()` per-pick sampler was removed.

Uncertainty/confidence output:

- Per candidate: mean total VAL (starters plus discounted depth), standard
  deviation, P25, and P75. Starter-only and depth components remain available
  separately for audit.
- Top-vs-runner-up paired differences use the shared boards.
- Response labels: `strong_edge`, `slight_edge`, `near_tie`, or `only_option`.
- Response also includes paired gap, win percentage, difference standard
  deviation, standard error, runner-up ID, and simulation count.
- The UI displays the middle-50% range and paired-rollout confidence instead of
  pretending a tiny mean difference is certain.

Exact-state cache:

- `POST /draft-help/sim` hashes the complete semantic state: year/configuration,
  slots, current/future picks, drafted IDs, roster IDs, values, Avoid/priority
  preferences, seed, `n_sims`, `top_k`, and a ranking/ADP revision fingerprint.
- Redis/fakeredis key prefix: `draft_help_sim_v4_`.
- TTL: five minutes.
- Cache failures degrade safely to normal simulation.
- Responses expose `cache_hit` for the UI.
- Any real draft-state, settings, preference, or source-data change invalidates
  the key naturally.

Regression tests cover one draw per player/board, deterministic boards, shared
boards across candidates, confidence/range output, exact-state cache hits, and
cache invalidation.

Useful files:

- `backend/app/services/draft_help/sim.py`
- `backend/app/routes.py`
- `backend/tests/test_draft_help_sim.py`
- `backend/tests/test_draft_help_summaries.py`
- `frontend/src/utils/simConfidence.ts`
- `frontend/src/types/draft.ts`
- `frontend/src/components/LiveDraftView.tsx`
- `frontend/src/components/MockDraftView.tsx`
- `tools/benchmark_draft.py`
- `tools/draft_proof.py`

---

## 3. Current API surface

### Draft board and simulation

- `GET /draft-help/rankings?year&teams&ppr&sf`
  - Returns config, source/freshness metadata, and player rows.
  - Merges the external value blob with the independent ADP blob.
  - Returns an ADP-only player pool when current values do not exist.
- `POST /draft-help/sim`
  - Inputs include year, teams, rounds, slot, PPR, superflex, starting slots,
    current pick, explicit future pick schedule, drafted IDs, roster IDs,
    `n_sims`, `top_k`, seed, value overrides, Avoid IDs, and priority IDs.
  - Route clamps rollouts to 10–400 and `top_k` to 1–12.
  - Returns recommendations, likely-next paths, uncertainty/confidence, upcoming
    picks, priority candidates, and `cache_hit`.

### Live draft

- `GET /draft-help/live/draft/<draft_id>`
  - Optional `username` or `slot` query parameters.
  - Optional `known_last_picked` and `known_status` change-detection tokens.
- `GET /draft-help/live/league/<league_id>`
  - Finds the active/paused draft first, then newest supported pre-draft room.
- Existing Sleeper user-league endpoint supports excluding dynasty leagues for
  Draft Help discovery.

### Historical tendencies

- `GET /draft-help/user/<username>/habits`
- `GET /draft-help/league/<league_id>/habits`
- `GET /draft-help/league/<league_id>/opponents`

These remain grouped under Draft Tendencies and are descriptive, not part of
live recommendation state.

### Accountability

- `GET /projection-review?year=YYYY`

---

## 4. Data, blobs, and source metadata

Primary Azure container: `fantasyjsons`.

Relevant blob names:

- `draft_rankings_{year}.json` — finished external values and optional companion
  fields, by league configuration.
- `draft_rankings_{year}_prev.json` — prior healthy external-value snapshot when
  a published rankings blob already exists.
- `draft_adp_{year}.json` — independent ADP/timing data.
- `draft_adp_{year}_prev.json` — prior healthy ADP snapshot.
- `players.json` — canonical Sleeper player identity map.
- `projection_history_{year}.json` — locked weekly projection history.
- `projection_review_{year}.json` — compiled accountability metrics.
- `player_season_scoring_{year}.json` — realized weekly scoring used by review.

Fixture mode:

- Set `USE_FIXTURE_BLOBS=1` to read from `tests/fixtures/blobs/` instead of
  Azure.
- This is the standard deterministic test/local-validation mode.
- The locally generated current-year ADP fixture can exist without appearing in
  Git status because the fixture directory is normally ignored.
- Production still needs the Azure timer to publish the current blob or a manual
  upload before the deployed backend can use it.

Source caches in `backend/app/services/draft_help/summaries.py` have a five-minute
TTL and intentionally do not negative-cache missing blobs, allowing a newly
published current-season blob to appear promptly.

Never copy credentials from `azure-functions/local.settings.json` into a handoff,
commit, test output, or chat response.

---

## 5. Working-tree state

Current branch at handoff creation:

`dev/jgorel/addinbacktestingandproof`

The work is not committed. `git status --short` reports modified tracked files
and many untracked Phase 1/2/A/B/C files.

Modified tracked files at handoff creation:

- `azure-functions/function_app.py`
- `azure-functions/trade_eval/pick_handoff.py`
- `backend/app/routes.py`
- `backend/app/services/draft_help/draft_fetch.py`
- `backend/app/services/draft_help/sim.py`
- `backend/app/services/draft_help/summaries.py`
- `backend/app/services/trade_eval/pick_handoff.py`
- `backend/tests/test_draft_help_sim.py`
- `backend/tests/test_draft_help_summaries.py`
- `backend/tests/test_trade_eval_pick_handoff.py`
- `docs/DRAFT_HELP_HANDOFF.md`
- `frontend/package.json`
- `frontend/src/api/client.ts`
- `frontend/src/components/MockDraftView.tsx`
- `frontend/src/pages/DraftHelpPage.tsx`
- `frontend/src/types/draft.ts`
- `requirements-dev.txt`
- `tools/benchmark_draft.py`
- `tools/build_draft_adp.py`

Untracked files/directories at handoff creation:

- `azure-functions/draft_adp.py`
- `azure-functions/fantasypros_adp.py`
- `azure-functions/vegas_accuracy.py`
- `backend/app/services/draft_help/live_draft.py`
- `backend/tests/test_draft_adp_pipeline.py`
- `backend/tests/test_draft_proof_harness.py`
- `backend/tests/test_fantasypros_adp_pipeline.py`
- `backend/tests/test_live_draft.py`
- `backend/tests/test_vegas_accuracy.py`
- `docs/DRAFT_HELP_CURRENT_HANDOFF.md`
- `docs/probablistic_drafting.txt`
- `frontend/public/draft_proof_summary.json`
- `frontend/src/components/DraftPlayerAvatar.tsx`
- `frontend/src/components/DraftProofView.tsx`
- `frontend/src/components/LiveDraftView.test.tsx`
- `frontend/src/components/LiveDraftView.tsx`
- `frontend/src/components/PlayerCombobox.tsx`
- `frontend/src/utils/`
- `tests/fixtures/sleeper_live_draft_1392134959602356224.json`
- `tools/draft_proof.py`
- `tools/draft_proof_output/`

This list also contains trade-inspector fixes and accountability work from the
same long-lived conversation. Do not stage only a guessed subset without first
reviewing the full diff and deciding whether to split commits by concern.

Recommended commit grouping:

1. Trade-evaluator pick-handoff bug fix and regression.
2. Phase 1 proof artifacts/UI.
3. Phase 2 projection accountability.
4. Current ADP/value framework and FantasyPros ingestion.
5. Live/custom Draft Room.
6. Phase C coherent engine/cache/confidence/visuals.
7. Handoff/documentation.

No commit was created by the assistant.

---

## 6. Known limitations and deliberate non-goals

- **ElBoberto 2026 values are live**, but the refresh currently depends on local
  desktop Excel and is not yet an unattended Azure schedule.
- DraftSheets remains the strongest cloud-oriented comparison candidate. Its
  public Google XLSX export and finished cross-position `Value` field are
  verified, but changing settings and recalculating formulas without a writable
  Google copy or desktop spreadsheet engine is not solved.
- Football Absurdity is no longer exposed in the product. Do not restore it or
  make production depend on Playwright scraping of its zero-heavy generator.
- Side-by-side source comparison and explicit simulation source selection are
  not implemented yet; the current backend still exposes one canonical value
  blob per year.
- The current published profile is 4-point passing TD. A 6-point passing-TD
  selector requires an exact-profile schema/cache/UI migration rather than
  overloading the existing 24 configuration keys.
- Any additional default source must provide finished cross-position values;
  rankings-only or projection-only sources are insufficient.
- Automated retrieval may be blocked by licensing, redistribution terms,
  authentication, unstable Google Sheets URLs, anti-bot measures, or a provider
  becoming inactive. Research these before coding around a source.
- Current custom settings are browser-local and not account-synced.
- Current source/config support is centered on 8/10/12/14 teams; standard,
  half-PPR, and PPR; 1QB and superflex.
- Live recommendations intentionally run only on the user's actual turn.
- Sleeper public API latency is mitigated, not eliminated.
- Dynasty/rookie/auction live drafts are not supported.
- Pick trades receive only enough backend treatment to produce the future user
  pick schedule; they are not a priority.
- K/DEF are excluded from the skill-position recommendation model.
- The recommendation engine still assumes the imported values are meaningful
  across positions and compatible with the selected roster configuration.
- The historical proof demonstrates decision strategy conditional on input
  quality. It does not validate a future provider's values.
- Phase 2 infrastructure needs real in-season captures before its website
  review becomes an evidence set.
- Do not spend the next phase on analytic replacement of Monte Carlo, traded-pick
  UI, season-long Vegas VBD, or internally generated projections.

---

## 7. Next phase — operationalize and compare external finished values

The first source is now published. The highest-priority remaining work is to
remove the desktop-only operational dependency and implement explicit
multi-source comparison without blending values.

### Step 1 — completed source selection/proof

Current source roles:

1. ElBoberto — provisional default; current 2026 blob published.
2. DraftSheets — comparison/cloud-fallback candidate; public export verified,
   unattended setting recalculation still unresolved.
3. Custom ElBoberto — browser-local exact-profile CheatSheet paste source.

Do not recreate these providers' man-games/VOR/VBD methodology locally merely
because cloud spreadsheet recalculation is inconvenient. Revisit an internal
implementation only after the external options are exhausted and the user makes
another explicit methodology decision.

The following criteria remain required for any additional source:

For each candidate, verify:

1. Active and regularly updated in 2026.
2. Publishes an actual cross-position Value/VORP/VAL, not only ordinal rank or
   raw projections.
3. Supports the needed league configurations: team count, scoring, 1QB vs
   superflex, and roster/flex settings.
4. Offers a stable machine-readable or reproducible export: CSV, JSON, public
   Google Sheets export, or deterministic download.
5. Permits automated retrieval and use on this website. Check licensing,
   attribution, redistribution, and terms rather than assuming a public page can
   be republished.
6. Includes reliable player identity fields or names/positions that can be
   conservatively matched to Sleeper IDs.
7. Has enough player coverage and sensible update frequency during draft season.
8. Publishes source timestamp/version metadata.
9. Does not require sending credentials through chat. If paid access is chosen,
   document only the provider and supported authentication mechanism, then store
   secrets in deployment settings.

Deliver the source survey and recommendation to the user before writing a
provider-specific scraper if the choice is not obvious.

### Step 2 — provider registry and exact-profile blobs

The adapter should output finished source values without changing their
meaning. The canonical blob should include, at minimum:

- Schema version and fantasy year.
- Provider/source name, source URL, attribution, and retrieval timestamp.
- Provider publication timestamp/version when available.
- League configuration key and the exact provider settings used.
- Player rows keyed by canonical Sleeper ID.
- Original source name/position/team for auditability.
- Finished provider Value/VORP/VAL.
- Optional provider-supplied projected points, tier, rank, auction value, or
  explicit flex value. These are companion fields only; do not derive base
  Value/VORP from them.
- Match counts, unmatched rows, ambiguous rows, and total source rows.

Keep ADP in `draft_adp_{year}.json`; do not merge timing and value provenance
into one opaque source. Move toward independently versioned provider artifacts
and exact profile matching so 4-point/6-point passing TD and future source
selection cannot silently reuse a nearby board.

### Step 3 — guarded scheduled/cloud refresh

Follow the current ADP safety pattern:

1. Fetch into a candidate object.
2. Parse with pure/injectable functions and fixture tests.
3. Conservatively match to `players.json`.
4. Validate schema, finite values, configuration coverage, player coverage,
   duplicate/ambiguous identity, source freshness, and suspicious day-over-day
   changes.
5. Reject a bad/partial/stale pull without replacing production.
6. Snapshot the previous healthy value blob.
7. Publish the candidate atomically.
8. Record source metadata for the UI.
9. Refresh daily during draft season if the provider actually updates daily;
   otherwise match the provider's publication cadence.

The target scheduled ingestion belongs in Azure Functions or another explicit
managed spreadsheet runtime. Pure parsing/validation remains independently
testable without Azure or network access. Until a headless recalculation path is
proven equivalent to Excel, the guarded local ElBoberto publisher is the
authoritative refresh mechanism.

### Step 4 — backend/frontend integration

- Make `rankings_config_players()` use the new external current-year values and
  existing independent ADP.
- Display provider attribution, freshness, configuration, and coverage.
- Add side-by-side raw provider value plus within-source overall rank, with one
  explicitly selected source supplying the simulation currency. Do not compare
  unlike raw scales as if they were interchangeable.
- Preserve ElBoberto-paste/manual/Avoid behavior as overrides, not replacements for source
  provenance.
- Keep the ADP-only safety gate when no healthy value blob exists.
- Ensure source changes invalidate the exact-state recommendation cache via its
  player revision fingerprint.
- Add fallback messaging when the source is stale or unavailable.

### Step 5 — validate source impact

- Unit-test parsing, matching, validation, snapshot behavior, and failed refresh.
- Route-test source metadata and ADP/value separation.
- Browser-test all scoring/format selectors and current-year Draft Room.
- Re-run recommendation sanity checks with the new provider values.
- Do **not** use the historical proof to claim the new source is good. The proof
  validates strategy; provider quality needs its own checks/accountability.

### After automated values

Only after the daily value source is stable:

1. Investigate an analytic probabilistic-drafting engine.
2. Treat it as an experiment alongside Monte Carlo, not an automatic
   replacement.
3. Compare latency, recommendation agreement, lineup outcomes, calibration,
   and behavior under position runs using a permanent benchmark.
4. Keep Monte Carlo unless the analytic approach matches or improves quality.

---

## 8. Fresh-thread starter prompt

Use the following in the next thread:

> Read `docs/DRAFT_HELP_CURRENT_HANDOFF.md` completely before making changes.
> ElBoberto v0.4 is the provisional default 2026 finished-value source and is
> live in `draft_rankings_2026.json`. The refresh tool with `--upload` discovers
> the current public workbook, uses desktop Excel to build
> and validate all 24 configurations, snapshots the prior healthy blob, and
> publishes it. The remaining blocker is unattended cloud recalculation;
> DraftSheets is the main comparison candidate, while unsupported profiles use
> a browser-local ElBoberto CheatSheet paste workflow. Do not recreate or blend
> provider VBD/VORP from
> projections, man-games, ECR, Vegas, or ADP unless the user explicitly reverses
> that decision. Keep FantasyPros DraftWizard as independent ADP, preserve
> manual/Avoid overrides and Phase C semantics, and design exact profiles before
> adding centralized 6-point passing TD profiles or source selection. The
> validated baseline is 518 backend tests, 13 frontend tests, clean TypeScript,
> a successful production build, and a regenerated 960-draft proof.

---

## 9. Quick reference

### Core engine

- `backend/app/services/draft_help/sim.py`
- `backend/app/services/draft_help/summaries.py`
- `backend/app/routes.py`

### Live/custom UI

- `backend/app/services/draft_help/live_draft.py`
- `frontend/src/components/LiveDraftView.tsx`
- `frontend/src/components/MockDraftView.tsx`
- `frontend/src/pages/DraftHelpPage.tsx`

### ADP/value pipeline

- `azure-functions/fantasypros_adp.py`
- `azure-functions/draft_adp.py`
- `azure-functions/function_app.py`
- `tools/build_draft_adp.py`
- `tools/build_draft_rankings.py`
- `tools/refresh_elboberto_values.py`
- `azure-functions/draft_values.py`
- `frontend/src/utils/customDraftValues.ts`

### Proof/accountability

- `tools/draft_proof.py`
- `tools/draft_proof_output/summary.json`
- `frontend/src/components/DraftProofView.tsx`
- `azure-functions/vegas_accuracy.py`
- `backend/tests/test_vegas_accuracy.py`

### Primary tests

- `backend/tests/test_draft_help_sim.py`
- `backend/tests/test_draft_help_summaries.py`
- `backend/tests/test_live_draft.py`
- `backend/tests/test_draft_adp_pipeline.py`
- `backend/tests/test_fantasypros_adp_pipeline.py`
- `backend/tests/test_draft_proof_harness.py`
- `backend/tests/test_vegas_accuracy.py`
- `frontend/src/components/LiveDraftView.test.tsx`
- `frontend/src/components/PlayerCombobox.test.tsx`
- `frontend/src/utils/customDraftValues.test.ts`
