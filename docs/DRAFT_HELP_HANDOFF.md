# Handoff — Draft Help & Season Accountability

> Created 2026-07-16. Branch: `dev/jgorel/draftstatsandhelp`.
> Scope: the **Draft Help** system (Monte-Carlo draft recommender + habit
> "Wrapped"-style accolades) and the plan for **in-season accountability /
> proven track record**. This is the authoritative state for a fresh thread —
> read top-to-bottom before touching code.
>
> The older `docs/HANDOFF.md` (Phase 4, KTC trade evaluator) is still valid for
> those systems but predates everything below. `docs/PHASE_4_PLAN.md` is stale.

---

## 0. TL;DR / where we are

- The **Draft Help** feature is built and validated but **NOT yet committed** —
  it lives entirely in the working tree (untracked `backend/app/services/draft_help/`,
  new tests, `frontend/src/pages/DraftHelpPage.tsx`, `frontend/src/components/MockDraftView.tsx`,
  `tools/benchmark_draft.py`, `tools/build_draft_*.py`, plus modified `backend/app/routes.py`).
- It has two halves:
  1. **Habit summaries** ("Wrapped for drafts"): reach/value tendencies, market
     crashes, elite hit/miss — purely descriptive, needs nothing further.
  2. **Mock-draft Monte-Carlo recommender**: "who should I draft now?" — simulates
     the rest of the draft from real ADP many times and recommends the pick that
     builds your best starting lineup. This is the piece the new goals are about.
- **463 backend tests pass.** Frontend `tsc` clean.
- The recommender has been benchmarked and **cross-validated** to beat both
  greedy "pick-highest-VBD" and pure-ADP drafting, across 2022–2024 and team
  sizes 8/10/12/14 (numbers in §4). That evidence is the seed for the new
  "proven track record" goal.

---

## 1. How to run & test

Python venv: `c:\Users\jlgor\Documents\fantasy-web-tool\.venv\Scripts\python.exe`

```powershell
# Backend (fixtures = offline blobs, no Azure needed)
cd backend; $env:USE_FIXTURE_BLOBS="1"; & "../.venv/Scripts/python.exe" run.py   # http://localhost:5000

# Backend tests (463 pass)
cd backend; & "c:/Users/jlgor/Documents/fantasy-web-tool/.venv/Scripts/python.exe" -m pytest -q

# Frontend typecheck
cd frontend; npx tsc --noEmit -p tsconfig.json

# Draft recommender benchmark (MC vs greedy-VBD; parallel)
$env:USE_FIXTURE_BLOBS="1"; & ".venv/Scripts/python.exe" tools/benchmark_draft.py --drafts 300 --nsims 20 --workers 8
```

`USE_FIXTURE_BLOBS=1` makes `load_blob` read `tests/fixtures/blobs/*.json` instead
of Azure — everything draft-related works fully offline from those fixtures.

---

## 2. The Draft Help system — architecture

### Backend package `backend/app/services/draft_help/`

- **`sim.py`** — the Monte-Carlo engine. Key pieces:
  - `SimPlayer` dataclass: `player_id, name, pos, adp, adp_stdev, proj (=VBD),
    fpts (raw projected points), flex_proj (Optional)`.
  - `sim_players_from_config_players(rows)` — builds `SimPlayer`s from ranking
    rows; sets `adp` (real ADP → fallback `overall_rank`), `proj=vbd`, `fpts`,
    and `flex_proj` (see §3 flex fix).
  - `recommend_pick(...)` — the entry point. Candidate pool = top-k by ADP ∪
    best-VBD per startable position ∪ best-ADP per startable position; for each
    candidate runs `n_sims` rollouts and averages the resulting **starting-lineup
    value**; returns ranked candidates + `likely_next` picks.
  - `_opponent_pick` — opponents draft by **Gaussian ADP**: each top-40 available
    player draws `N(adp, adp_stdev)`, earliest drawn is taken. Produces realistic
    positional runs (this is what "de-chalked" the sim).
  - `_greedy_my_pick` / `_simulate_once` — the simulated "me" fills starters then
    depth; `roster_value = _fill_lineup (starters) + small depth bonus`.
  - `_fill_lineup` — optimal starting lineup: dedicated slots by VBD, FLEX by the
    flex value (see §3). `lineup_value` = starters only (the headline "VAL").
  - `_modal_path` — coherent "your likely next picks" (conditional path, not
    per-slot argmax — fixed an earlier "3 TEs in a row" bug).
  - `_flex_replacement_baseline`, `_FLEX_GUEST_POS` — the flex fix (§3).
- **`summaries.py`** — `rankings_config_players(year, teams, ppr, sf)` builds the
  board rows and merges real ADP via `_adp_for_config`; also the habit-summary
  aggregation (reach/value/market patterns), dynasty-league exclusion.
- **`rankings_source.py`** — `RankingsRepository`/`RankingPlayer`, `config_key`
  (`"12|0.5|1qb"`), `rankings_blob_name`/`adp_blob_name`, `NameResolver` +
  `normalize_player_name`, `assign_overall_ranks` (sorts by VBD desc).
- **`habits.py`** — VBD-based reach detection, market-crash / elite hit-rate
  curves (drives the "Wrapped for drafts" accolades).
- **`draft_fetch.py`** — Sleeper/Fleaflicker draft fetch, `NormalizedDraft`,
  `is_dynasty` detection.
- **`custom_vbd.py`** — scaffold/notes for user-supplied VBD (mostly a doc stub).

### Routes (`backend/app/routes.py`)
- `GET /draft-help/rankings?year&teams&ppr&sf` — the player value board
  (returns rows with `fpts, vbd, auction, tier, pos_rank, overall_rank, adp, adp_stdev`).
- `POST /draft-help/sim` — body `{year, teams, rounds, my_slot, ppr, superflex,
  drafted_ids[], my_roster_ids[], slots?, current_pick?, n_sims?, top_k?, seed?}`
  → `recommend_pick`. Frontend sends `n_sims:60, top_k:8, seed:1`.
- Habit-summary routes (cache keys bumped to `v2`), `sleeper_user_leagues`
  (accepts `exclude_dynasty`).

### Frontend
- `frontend/src/pages/DraftHelpPage.tsx` — the page (habits + mock draft tabs).
- `frontend/src/components/MockDraftView.tsx` — interactive mock draft: board
  with sortable **ADP** and **VBD** columns, roster slots, "Recommend my pick"
  (shows VBD vs VAL so a 2nd-TE's high-VBD/low-VAL divergence is visible),
  "likely next picks", an explainer accordion.
- `frontend/src/types/draft.ts` — `RankingsPlayerRow` (`adp?, adp_stdev?`),
  `SimCandidate` (`avg_lineup, avg_depth, likely_next[]`), `SimRequest`.

---

## 3. The FLEX valuation fix (most recent work — "variant B")

**Problem the user reported:** the sim recommended stacking a 2nd TE (e.g. Dalton
Kincaid) into the FLEX over a higher-scoring WR/RB. **Root cause:** `_fill_lineup`
filled the FLEX by positional **VBD**, and TE's replacement baseline is very low,
so a mediocre TE's VBD out-punched a WR/RB that actually scores more points.

**Fix (shipped):** a flex-eligible "guest" position (currently just **TE**) is
judged in the FLEX at the shared **RB/WR points level** instead of its own low
baseline; the natural flex fillers (RB/WR, and QB in superflex) keep their own
VBD so their scarcity value isn't distorted. Dedicated slots are unchanged, so an
elite TE keeps full value in the dedicated TE slot.

- `_FLEX_GUEST_POS = frozenset({"TE"})`.
- `_flex_replacement_baseline(rows)` = `max(median(fpts−vbd) over RB, over WR)`.
- `sim_players_from_config_players`: sets `flex_proj = fpts − flex_baseline`
  **only** for guest positions; `None` otherwise (→ `_fill_lineup` uses `proj`/VBD).
- Backward-compatible: `SimPlayer` built without `fpts`/`flex_proj` (unit tests)
  falls back to VBD-in-flex.

**Why "variant B" and not pure-points-for-all-flex:** an intermediate version
(de-inflate every flex position to a single baseline) was tried and a
currency-independent pure-points diagnostic showed it caused a **real 1QB
regression** (penalized RBs in the flex, −7 pts/team). Variant B fixed that.

---

## 4. Validation evidence (the seed for "proven track record")

The benchmark (`tools/benchmark_draft.py`) plays many mock drafts where one team
uses the **Monte-Carlo recommender (MC)**, one uses **greedy highest-VBD ("VAL")**,
and the rest draft by Gaussian ADP; final rosters are graded. A separate
throwaway diagnostic also graded by **pure optimal starting-lineup points**
(currency-independent) and added an explicit **pure-ADP** drafter.

**Benchmark, variant B (200 drafts, nsims 20), combined grade:**
- ALL: **MC +12.1** (95% CI ±3.6), MC wins 70%.
- 1QB: **+14.0** (±4.4), 77%.
- Superflex: **+10.1** (±5.6), 63%.

**Cross-validation (pure starting-lineup points, 1QB half-PPR, 20 drafts/cell):**
- vs greedy-VBD: **overall +17.8 pts/team, MC wins 73%.** Positive in every season
  (2022 +18.8, 2023 +9.8, 2024 +24.8) and every team size (8 +17.1, 10 +12.4,
  12 +26.8, 14 +14.9). Only soft cell: 2022 10-team −2.0 (a tie at n=20).
  → **Not overfit to 2024.**
- vs a **pure-ADP** drafter: **MC +210.8 pts/team, wins 98%** (blowout — ADP
  drafting ignores roster construction).

**Reproducibility note:** these two cross-val/pure-points diagnostics were run as
temporary scripts (`tools/_diag_flex.py`, `tools/_diag_xval.py`) that were
**deleted**. The new "proven track record" goal should re-create a **permanent**
version of that harness (see §7 goal 2).

---

## 5. Data & pipeline

- **Rankings blobs**: `tests/fixtures/blobs/draft_rankings_{2022..2025}.json` —
  per-config player rows (`fpts, vbd, auction, tier, pos_rank, overall_rank`).
  Built by `tools/build_draft_rankings.py` from the user's crowdsourced VBD
  rankings (BeerSheets / ElBoberto style). This is the "we assume the rankings
  are accurate" input.
- **ADP blobs**: `tests/fixtures/blobs/draft_adp_{2022,2023,2024}.json` —
  `{configs: {config_key: {players: {player_id: {adp, stdev}}}}}`. Built by
  `tools/build_draft_adp.py` from FantasyFootballCalculator's JSON API
  (`https://fantasyfootballcalculator.com/api/v1/adp/{fmt}?teams=N&year=YYYY&position=all`,
  `fmt` = `ppr|half-ppr|standard|2qb`). FFC provides **stdev** for free.
  - Coverage: all of 8/10/12/14 × {1qb, sf} × {std, half, ppr} for 2022–2024;
    ~116/170/158 of 300 players matched per year (the ones that matter early/mid;
    rest fall back to VBD order for ADP).
  - **2025 has no ADP** (FFC returned Error during the 2026 offseason) → 2025
    falls back to VBD order.
- Config key format: `f"{teams}|{ppr_label}|{'sf'|'1qb'}"` (`ppr_label` ∈
  `std|half|ppr`).

---

## 6. Known limitations / open items

- Draft-help work is **uncommitted** — commit it before/along with new work.
- 2025 ADP missing (FFC gap); refresh `build_draft_adp.py` once FFC publishes.
- Superflex SUPER_FLEX still values QB by its (VBD) proj, not a QB-inclusive flex
  baseline — deliberately conservative; SF is a healthy +10.1 as-is.
- The benchmark grader mixes VBD (dedicated) + points (flex) + a small VBD bench
  term; the pure-points harness is the cleaner "true team quality" measure and
  should become the canonical one for the track-record graphs.

---

## 7. New goals (this thread → next thread)

The user's goal for the next thread: **give the tool a proven track record /
accountability throughout the season.** Four workstreams:

### Goal 1 — Fun accolades ("Wrapped for drafts")
**Needs nothing.** The habit summaries in `habits.py`/`summaries.py` already ship.

### Goal 2 — Drafting: shareable "MC beats pure-VBD" proof/graph
Turn the throwaway benchmark diagnostics into a **permanent, reproducible**
artifact that produces presentable stats + a graph showing the Monte-Carlo
drafter outperforms "just pick the highest VBD," **assuming the given rankings
are accurate**. Explicitly **not** about how real teams did — it's "if rankings
are accurate, this is the best way to draft."
- We already have the numbers (§4). Need: a committed script that emits CSV/JSON
  (per season/team-size/format margins, win rates, CIs) + a chart (matplotlib or
  a frontend chart), graded by **pure optimal starting-lineup points**.
- Include the pure-ADP baseline too (the +210 blowout is a compelling visual).

### Goal 3 — Backtest Vegas projections (research/plan)
Figure out whether historical **pregame Vegas lines** (the inputs behind the
"super accurate" projections) can be scraped and compared to actual weekly scores
vs historical expert rankings. User suspects retroactive scraping is impossible.
- Plan A: find a source of historical weekly pregame lines (player props / team
  totals) + historical expert weekly rankings + actual weekly scores.
- Plan B (fallback, likely): a **weekly capture pipeline** that stores this
  season's pregame lines + rankings + results each week and compiles an
  "accuracy review" to build trust over the season.

### Goal 4 — Vegas-weighted VBD (new feature, harder)
Blend Vegas draft/season lines into the crowdsourced VBD rankings (BeerSheets,
ElBoberto, etc.) to produce our own more-accurate rankings by weighting Vegas.
- Caveats to design around: Vegas preseason season-totals miss things rankings
  capture (rookie upside where a miss doesn't matter, injury-prone players with
  risk already baked in), and there are far fewer alt lines to triangulate a true
  distribution.

---

## 8. Prompt to hand the planner (next thread)

> I want to build in-season **accountability / a proven track record** for my
> fantasy draft+projection tool. Read `docs/DRAFT_HELP_HANDOFF.md` first for full
> context (the Monte-Carlo draft recommender is built, validated, and currently
> uncommitted on branch `dev/jgorel/draftstatsandhelp`; 463 backend tests pass).
> Plan these four workstreams — I want a plan, not code yet:
>
> 1. **Draft-approach proof (highest priority).** Build a permanent, reproducible
>    harness + graph proving that my Monte-Carlo drafter beats naive
>    "pick-the-highest-VBD" drafting **assuming the rankings are accurate** (this
>    is a methodology claim, not a claim about real team results). I already have
>    benchmark + cross-validation numbers (2022–2024, team sizes 8/10/12/14,
>    graded on pure optimal starting-lineup points): MC beats greedy-VBD +17.8
>    pts/team (73% win) and beats a pure-ADP drafter +210 pts/team (98%). The
>    diagnostics that produced these were throwaway scripts that got deleted —
>    make a committed version under `tools/` that emits CSV/JSON + a chart, and
>    decide where the chart lives (script output vs a frontend page).
> 2. **Backtest Vegas projections (research + plan).** Investigate whether
>    historical **pregame** Vegas lines (player props/team totals — the inputs
>    behind my "super accurate" projections) can be scraped and compared against
>    actual weekly scores and historical expert rankings. I suspect retroactive
>    scraping is impossible; if so, plan a **weekly capture pipeline** for the
>    upcoming season that stores pregame lines + rankings + results and compiles a
>    weekly "accuracy review." Identify concrete data sources either way.
> 3. **Vegas-weighted VBD (new feature).** Plan a way to blend Vegas draft/season
>    lines into crowdsourced VBD rankings (BeerSheets, ElBoberto) to make our own
>    more-accurate rankings by weighting Vegas in. Design around the caveats:
>    Vegas season totals miss rookie upside / baked-in injury risk, and there are
>    few alt lines to build a real distribution.
> 4. **Fun accolades:** nothing needed — already shipped; just confirm.
>
> Deliver a phased plan (dependencies, data needs, and what to build first),
> flagging anything that needs data I have to supply.
```
