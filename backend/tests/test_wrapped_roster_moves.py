"""Tests for the Phase-2 Wrapped roster-move accolades.

Covers:
* ``calculate_troll_metric`` against synthetic ``WeeklyScores``.
* ``compute_baseline_player_scoring`` over a hand-built season-scoring blob.
* ``calculate_roster_accolades`` end-to-end (early_pickup, late_drop,
  best_add, worst_drop) with synthetic transactions + ownership.

All tests are pure-function — no network mocking required.
"""
from __future__ import annotations

from typing import Dict, List

import pytest

from app.services.wrapped.replacement_value import compute_baseline_player_scoring
from app.services.wrapped.roster_accolades import (
    calculate_roster_accolades,
    calculate_troll_metric,
)
from app.services.wrapped.schedule import WeeklyScores
from app.services.wrapped.transactions import LeagueTransactions


# ---------------------------------------------------------------------------
# Troll metric
# ---------------------------------------------------------------------------
def test_troll_metric_picks_largest_bench_minus_start_gap():
    scores = WeeklyScores()
    # alice: player p1 — started a lot but bench-pop scored higher.
    scores.user_player_start_sit_points["alice"]["p1"]["start"] = [4.0, 5.0, 6.0, 5.0]
    scores.user_player_start_sit_points["alice"]["p1"]["bench"] = [25.0, 30.0]
    # alice: player p2 — also a candidate but smaller gap.
    scores.user_player_start_sit_points["alice"]["p2"]["start"] = [10.0, 11.0, 12.0, 13.0]
    scores.user_player_start_sit_points["alice"]["p2"]["bench"] = [15.0]
    # bob: player p3 — no troll, started > bench.
    scores.user_player_start_sit_points["bob"]["p3"]["start"] = [20.0, 22.0, 18.0, 21.0]
    scores.user_player_start_sit_points["bob"]["p3"]["bench"] = [3.0, 4.0]

    players = {
        "p1": {"full_name": "Trolly McTroll", "fantasy_positions": ["WR"]},
        "p2": {"full_name": "Smol Troll", "fantasy_positions": ["RB"]},
        "p3": {"full_name": "No Troll", "fantasy_positions": ["RB"]},
    }
    out = calculate_troll_metric(scores, players)

    assert out["alice"] is not None
    assert out["alice"]["name"] == "Trolly McTroll"
    # bench_avg 27.5 - start_avg 5.0 = 22.5
    assert out["alice"]["troll_value"] == pytest.approx(22.5)
    assert out["alice"]["num_start"] == 4
    assert out["alice"]["num_bench"] == 2

    # bob's only player has start>bench → no positive troll value → None.
    assert out["bob"] is None


def test_troll_metric_respects_min_starts_floor():
    """A player with only 3 starts shouldn't qualify (default floor is 4)."""
    scores = WeeklyScores()
    scores.user_player_start_sit_points["alice"]["p1"]["start"] = [1.0, 2.0, 3.0]
    scores.user_player_start_sit_points["alice"]["p1"]["bench"] = [50.0]
    out = calculate_troll_metric(scores, {"p1": {"full_name": "Sample Size"}})
    assert out["alice"] is None


# ---------------------------------------------------------------------------
# Replacement value
# ---------------------------------------------------------------------------
class _FakeCtx:
    def __init__(
        self,
        roster_groups: List[List[str]],
        total_rosters: int,
        qb_key: str = "std",
        skill_key: str = "half_ppr",
    ):
        self.roster_positions_groups = roster_groups
        self.total_rosters = total_rosters
        self.qb_score_key = qb_key
        self.skill_score_key = skill_key


def _scoring_player(rank: int, points: float, pos: str, qb_key="std", skill_key="half_ppr") -> dict:
    rk_key = f"{qb_key}_rank" if pos == "QB" else f"{skill_key}_rank"
    pt_key = rk_key.replace("rank", "points")
    return {
        "full_name": f"P{pos}{rank}",
        "fantasy_positions": [pos],
        "scoring_data_season": {rk_key: rank, pt_key: points},
    }


def test_replacement_value_typical_12_team_1qb_2flex():
    """12 teams, 1 QB / 2 RB / 2 WR / 1 TE / 2 FLEX. Expected baselines:

      QB  -> rank 12*1 + 1 = 13
      RB  -> ceil(2*12 + 2*(1/3)*12) + 1 = ceil(24+8)+1 = 33
      WR  -> ceil(2*12 + 2*(2/3)*12) + 1 = ceil(24+16)+1 = 41
      TE  -> 1*12 + 1 = 13
    """
    ctx = _FakeCtx(
        roster_groups=[
            ["QB"], ["RB"], ["RB"], ["WR"], ["WR"], ["TE"],
            ["RB", "WR", "TE"], ["RB", "WR", "TE"],
        ],
        total_rosters=12,
    )
    season = {
        "qb13": _scoring_player(13, 250.0, "QB"),
        "rb33": _scoring_player(33, 110.0, "RB"),
        "wr41": _scoring_player(41, 95.0, "WR"),
        "te13": _scoring_player(13, 80.0, "TE"),
        # noise
        "qb1": _scoring_player(1, 400.0, "QB"),
    }
    baseline = compute_baseline_player_scoring(ctx, season)
    assert baseline["QB"] == pytest.approx(250.0)
    assert baseline["RB"] == pytest.approx(110.0)
    assert baseline["WR"] == pytest.approx(95.0)
    assert baseline["TE"] == pytest.approx(80.0)


def test_replacement_value_returns_zero_when_target_rank_missing():
    ctx = _FakeCtx(roster_groups=[["QB"]], total_rosters=10)
    # QB at rank 11 needed, but blob has none.
    baseline = compute_baseline_player_scoring(ctx, {})
    assert baseline["QB"] == 0.0
    assert baseline["RB"] == 0.0


# ---------------------------------------------------------------------------
# Roster accolades — synthetic end-to-end
# ---------------------------------------------------------------------------
def _ownership(pid_weeks: Dict[str, Dict[int, float]]) -> Dict[str, Dict[str, Dict[str, float]]]:
    """Compact builder: pid -> week -> owned% becomes the nested shape."""
    return {
        pid: {str(w): {"owned": pct, "started": pct / 4.0} for w, pct in by_w.items()}
        for pid, by_w in pid_weeks.items()
    }


def test_roster_accolades_picks_correct_buckets():
    """One alice, one bob. Checks each accolade bucket independently."""
    rosters = {
        "alice": ["wr1", "rb_kept_add", "rb_drafted"],
        "bob": ["wr2"],
    }

    transactions = LeagueTransactions()
    # alice added wr1 in week 2 (early-pickup candidate, started low-owned,
    # now widely owned).
    transactions.player_transactions["wr1"] = [("Add", 2, "alice")]
    # alice added rb_kept_add in week 8 (NOT early — out of early window).
    transactions.player_transactions["rb_kept_add"] = [("Add", 8, "alice")]
    # alice dropped rb_dropped_low_owned in week 5 then never picked back up
    # — that's a late_drop candidate (low owned at drop time).
    transactions.player_transactions["rb_dropped_low_owned"] = [("Drop", 5, "alice")]
    # alice dropped wr_dropped_high_value in week 6 — never came back —
    # worst_drop candidate.
    transactions.player_transactions["wr_dropped_high_value"] = [("Drop", 6, "alice")]
    # bob added wr2 in week 3 — early-pickup candidate.
    transactions.player_transactions["wr2"] = [("Add", 3, "bob")]
    transactions.last_added_by = {
        "wr1": ("alice", 2),
        "rb_kept_add": ("alice", 8),
        "wr2": ("bob", 3),
    }

    season_scoring = {
        "wr1": {
            "full_name": "Sleeper Pickup",
            "fantasy_positions": ["WR"],
            "scoring_data_season": {"half_ppr_points": 180.0, "half_ppr_rank": 15},
        },
        "rb_kept_add": {
            "full_name": "Late Add",
            "fantasy_positions": ["RB"],
            "scoring_data_season": {"half_ppr_points": 200.0, "half_ppr_rank": 8},
        },
        "rb_drafted": {
            "full_name": "Draft Holdover",
            "fantasy_positions": ["RB"],
            "scoring_data_season": {"half_ppr_points": 150.0, "half_ppr_rank": 20},
        },
        "rb_dropped_low_owned": {
            "full_name": "Cut and Forgotten",
            "fantasy_positions": ["RB"],
            "scoring_data_season": {"half_ppr_points": 30.0, "half_ppr_rank": 200},
        },
        "wr_dropped_high_value": {
            "full_name": "Whoops",
            "fantasy_positions": ["WR"],
            "scoring_data_season": {"half_ppr_points": 220.0, "half_ppr_rank": 5},
        },
        "wr2": {
            "full_name": "Bob's Pickup",
            "fantasy_positions": ["WR"],
            "scoring_data_season": {"half_ppr_points": 170.0, "half_ppr_rank": 18},
        },
    }
    ownership = _ownership({
        "wr1": {2: 12.0, 14: 80.0},   # added at 12% owned, now 80% — qualifies
        "wr2": {3: 22.0, 14: 75.0},   # ditto for bob
        "rb_dropped_low_owned": {5: 8.0},     # very low ownership when dropped
        "wr_dropped_high_value": {6: 60.0},
    })
    baseline = {"QB": 250.0, "RB": 100.0, "WR": 90.0, "TE": 60.0}

    out = calculate_roster_accolades(
        ctx_current_rosters=rosters,
        transactions=transactions,
        ownership_history=ownership,
        season_scoring=season_scoring,
        qb_score_key="std",
        skill_score_key="half_ppr",
        baseline=baseline,
        current_week=14,
    )

    # ----- alice -----
    a = out["alice"]
    # early_pickup: wr1 — was 12% owned at add, now 80% owned.
    assert a["early_pickup"] is not None
    assert a["early_pickup"]["name"] == "Sleeper Pickup"
    assert a["early_pickup"]["week_added"] == 2
    assert a["early_pickup"]["owned_pct_when_added"] == pytest.approx(12.0)

    # late_drop: lowest-owned drop = rb_dropped_low_owned at 8% owned.
    assert a["late_drop"] is not None
    assert a["late_drop"]["name"] == "Cut and Forgotten"
    assert a["late_drop"]["owned_pct_at_drop"] == pytest.approx(8.0)

    # best_add: rb_kept_add (200 pts vs RB baseline 100) beats wr1 (180 vs WR 90).
    assert a["best_add"] is not None
    assert a["best_add"]["name"] == "Late Add"
    assert a["best_add"]["value_over_baseline"] == pytest.approx(100.0)

    # worst_drop[WR]: wr_dropped_high_value (220 - 90 = 130 over baseline).
    assert "WR" in a["worst_drop"]
    assert a["worst_drop"]["WR"]["name"] == "Whoops"
    assert a["worst_drop"]["WR"]["value_over_baseline"] == pytest.approx(130.0)
    # No qualifying RB drop (the only RB drop ended up on no roster but is
    # below baseline) — but it's still recorded as the highest-value RB drop.
    assert "RB" in a["worst_drop"]

    # ----- bob -----
    b = out["bob"]
    assert b["early_pickup"] is not None
    assert b["early_pickup"]["name"] == "Bob's Pickup"
    # bob has no drops, so worst_drop is empty.
    assert b["worst_drop"] == {}
    assert b["late_drop"] is None


def test_roster_accolades_skips_drafted_players_for_best_add():
    """A player whose ``last_added_by`` is missing from transactions should
    NOT appear in best_add — they were drafted, not added off waivers."""
    rosters = {"alice": ["drafted_qb"]}
    transactions = LeagueTransactions()  # empty
    season_scoring = {
        "drafted_qb": {
            "full_name": "Draft Pick",
            "fantasy_positions": ["QB"],
            "scoring_data_season": {"std_points": 400.0, "std_rank": 1},
        }
    }
    out = calculate_roster_accolades(
        ctx_current_rosters=rosters,
        transactions=transactions,
        ownership_history={},
        season_scoring=season_scoring,
        qb_score_key="std",
        skill_score_key="half_ppr",
        baseline={"QB": 250.0, "RB": 100.0, "WR": 90.0, "TE": 60.0},
        current_week=14,
    )
    assert out["alice"]["best_add"] is None
    assert out["alice"]["early_pickup"] is None


# ---------------------------------------------------------------------------
# Transactions resolver
# ---------------------------------------------------------------------------
def test_last_added_by_picks_most_recent_add():
    """A player added in week 2, dropped in week 5, re-added by another user
    in week 7 — last_added_by should be the week-7 user."""
    from app.services.wrapped.transactions import _resolve_last_added_by

    out = LeagueTransactions()
    out.player_transactions["pid"] = [
        ("Add", 2, "alice"),
        ("Drop", 5, "alice"),
        ("Add", 7, "bob"),
    ]
    _resolve_last_added_by(out)
    assert out.last_added_by["pid"] == ("bob", 7)


def test_last_added_by_omits_pids_with_only_drops():
    from app.services.wrapped.transactions import _resolve_last_added_by

    out = LeagueTransactions()
    out.player_transactions["pid"] = [("Drop", 3, "alice")]
    _resolve_last_added_by(out)
    assert "pid" not in out.last_added_by
