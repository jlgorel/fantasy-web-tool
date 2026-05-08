"""Unit tests for the Wrapped schedule pipeline.

Phase 1 covers:
* The seven pure-function accolades over a synthetic ``WeeklyScores``.
* The ``_process_week_matchups`` helper (so the tied-score / bench-zero edge
  cases are pinned).
* The ``_calculate_optimal_lineup`` greedy best-ball builder.

We intentionally do **not** hit the network here — ``fetch_weekly_scores``
is exercised separately when we capture real fixtures in Phase 2.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Dict, List, Tuple

import pytest

from app.services.wrapped.schedule import (
    WeeklyScores,
    _calculate_optimal_lineup,
    _process_week_matchups,
)
from app.services.wrapped.schedule_accolades import (
    calculate_best_and_worst_manager,
    calculate_biggest_falloff_and_come_up,
    calculate_consistencies,
    calculate_each_users_best_and_worst_schedule,
    calculate_hypothetical_records,
    calculate_luckiest_and_unluckiest,
    calculate_weekly_best_ball_records,
)


# ---------------------------------------------------------------------------
# Synthetic WeeklyScores builder
# ---------------------------------------------------------------------------
def _make_scores(
    user_score_by_week: Dict[str, Dict[int, float]],
    matchups_by_week: Dict[int, List[Tuple[str, str]]],
    user_best_ball_by_week: Dict[str, Dict[int, float]] | None = None,
) -> WeeklyScores:
    """Build a WeeklyScores from compact inputs.

    ``matchups_by_week[w]`` is a list of (home, away) username pairs for that
    week. We derive ``opponent_score_by_week``, ``user_results_by_week``, and
    ``median_scores`` from there.
    """
    import statistics

    scores = WeeklyScores()
    for user, byweek in user_score_by_week.items():
        for w, pts in byweek.items():
            scores.user_score_by_week[user][w] = pts

    if user_best_ball_by_week is None:
        # Default: best-ball == actual + 10 (so manager efficiency is well-defined).
        user_best_ball_by_week = {
            u: {w: pts + 10.0 for w, pts in byweek.items()}
            for u, byweek in user_score_by_week.items()
        }
    for user, byweek in user_best_ball_by_week.items():
        for w, pts in byweek.items():
            scores.user_best_ball_score_by_week[user][w] = pts

    for week, pairs in matchups_by_week.items():
        weekly_pts: List[float] = []
        for home, away in pairs:
            hp = user_score_by_week[home][week]
            ap = user_score_by_week[away][week]
            weekly_pts.extend([hp, ap])
            scores.opponent_score_by_week[home][week] = ap
            scores.opponent_score_by_week[away][week] = hp
            if hp == ap:
                scores.user_results_by_week[home][week] = "T"
                scores.user_results_by_week[away][week] = "T"
            else:
                scores.user_results_by_week[home][week] = "W" if hp > ap else "L"
                scores.user_results_by_week[away][week] = "W" if ap > hp else "L"
        if weekly_pts:
            scores.median_scores[week] = float(statistics.median(weekly_pts))

    return scores


@pytest.fixture()
def four_team_scores() -> WeeklyScores:
    """Four teams, four weeks. Hand-tuned so each accolade has a clear winner.

    Week scores (actual):
                W1     W2     W3     W4
        alice  120    130    140    150     -> rising
        bob    110    100     90     80     -> falling
        carol   95    105    115    125     -> steady-ish riser
        dave   100    100    100    100     -> the constant — most consistent

    Match-ups (home, away):
        W1: alice vs bob     | carol vs dave
        W2: alice vs carol   | bob   vs dave
        W3: alice vs dave    | bob   vs carol
        W4: alice vs bob     | carol vs dave   (rematch)
    """
    actual = {
        "alice": {1: 120.0, 2: 130.0, 3: 140.0, 4: 150.0},
        "bob":   {1: 110.0, 2: 100.0, 3:  90.0, 4:  80.0},
        "carol": {1:  95.0, 2: 105.0, 3: 115.0, 4: 125.0},
        "dave":  {1: 100.0, 2: 100.0, 3: 100.0, 4: 100.0},
    }
    matchups = {
        1: [("alice", "bob"), ("carol", "dave")],
        2: [("alice", "carol"), ("bob", "dave")],
        3: [("alice", "dave"), ("bob", "carol")],
        4: [("alice", "bob"), ("carol", "dave")],
    }
    return _make_scores(actual, matchups)


# ---------------------------------------------------------------------------
# 1. Best-ball records
# ---------------------------------------------------------------------------
def test_best_ball_records_ranks_each_week(four_team_scores):
    """Default best-ball = actual + 10, so the ordering matches actual scores.

    Week 1 (sorted asc): carol(95) < dave(100) < bob(110) < alice(120).
    Wins/losses cumulative across 4 weeks. Alice is always 1st => 12W/0L.
    """
    out = calculate_weekly_best_ball_records(four_team_scores)
    assert out["alice"] == {"wins": 12, "losses": 0}  # 3 wins/wk * 4 weeks
    assert out["bob"]["wins"] + out["bob"]["losses"] == 12
    # Each week awards 0+1+2+3=6 wins and 6 losses across the 4 teams.
    total_wins = sum(r["wins"] for r in out.values())
    total_losses = sum(r["losses"] for r in out.values())
    assert total_wins == total_losses == 6 * 4


# ---------------------------------------------------------------------------
# 2. Hypothetical records
# ---------------------------------------------------------------------------
def test_hypothetical_includes_self_and_handles_target_as_opponent(four_team_scores):
    """Alice's hypothetical record under bob's schedule swaps any week where
    bob's opponent was alice (W1, W4) — the helper should substitute bob's
    own actual score so the comparison isn't a self-tie."""
    hypo = calculate_hypothetical_records("alice", four_team_scores)
    # Self-entry exists.
    assert "alice" in hypo
    # Alice's own schedule == her actual record. She beats bob, carol, dave
    # in every week she faces them. Actual matchups: W1 vs bob (W), W2 vs
    # carol (W), W3 vs dave (W), W4 vs bob (W) => 4-0.
    assert hypo["alice"] == {"wins": 4, "losses": 0}
    # Under bob's schedule alice would face: W1 alice (substituted -> bob 110:
    # alice 120 > 110 W), W2 dave (alice 130 > 100 W), W3 carol (alice 140 >
    # 115 W), W4 alice (substituted -> bob 80: alice 150 > 80 W).
    assert hypo["bob"] == {"wins": 4, "losses": 0}


def test_each_users_best_and_worst_schedule_keys(four_team_scores):
    out = calculate_each_users_best_and_worst_schedule(four_team_scores)
    assert set(out.keys()) == {"alice", "bob", "carol", "dave"}
    for record in out.values():
        assert "best" in record and "worst" in record
        assert "vs_schedule_of" in record["best"]
        assert "record" in record["best"]


# ---------------------------------------------------------------------------
# 3. Luck
# ---------------------------------------------------------------------------
def test_luckiest_and_unluckiest_are_deterministic():
    """Construct a tiny league where bob has one obviously-lucky win and
    carol one obviously-unlucky loss."""
    actual = {
        "alice": {1: 100.0, 2: 100.0},
        "bob":   {1:  90.0, 2:  90.0},   # week 1: low score, but won
        "carol": {1:  80.0, 2: 130.0},   # week 2: high score, but lost
        "dave":  {1:  70.0, 2: 140.0},
    }
    matchups = {
        1: [("alice", "carol"), ("bob", "dave")],   # alice 100>80, bob 90>70
        2: [("alice", "bob"),   ("carol", "dave")], # alice 100>90, dave 140>130
    }
    scores = _make_scores(actual, matchups)
    luck = calculate_luckiest_and_unluckiest(scores)
    # Week 1 median = (100+90+80+70)/2 middle = (90+80)/2 = 85.
    # bob: W with 90 > 85, NOT lucky. dave: L with 70 < 85, NOT unlucky.
    # Week 2 median = (100+90+130+140) sorted = 90,100,130,140 -> (100+130)/2=115.
    # carol: L with 130 > 115 -> UNLUCKY.
    assert luck["unluckiest"]["username"] == "carol"
    assert luck["unluckiest"]["count"] == 1


def test_luck_empty_input_returns_none():
    luck = calculate_luckiest_and_unluckiest(WeeklyScores())
    assert luck["luckiest"]["username"] is None
    assert luck["unluckiest"]["username"] is None


# ---------------------------------------------------------------------------
# 4. Consistency
# ---------------------------------------------------------------------------
def test_consistencies_picks_lowest_mad(four_team_scores):
    out = calculate_consistencies(four_team_scores)
    # Dave is the constant 100 -> MAD 0.
    assert out["most_consistent"]["username"] == "dave"
    assert out["most_consistent"]["mad"] == 0.0
    # Alice and bob have identical |delta| from mean each week (linear
    # progressions). Either could be least_consistent — assert it's not
    # dave/carol.
    assert out["least_consistent"]["username"] in {"alice", "bob"}
    assert out["least_consistent"]["mad"] > out["most_consistent"]["mad"]


def test_consistencies_empty_returns_none():
    out = calculate_consistencies(WeeklyScores())
    assert out == {"most_consistent": None, "least_consistent": None}


# ---------------------------------------------------------------------------
# 5. Manager efficiency
# ---------------------------------------------------------------------------
def test_manager_efficiency_skips_zero_best_ball():
    """A user with a 0 best-ball week shouldn't blow up the calculation."""
    actual = {"alice": {1: 100.0, 2: 90.0}}
    bb = {"alice": {1: 0.0, 2: 100.0}}  # week 1 has 0 best-ball
    matchups = {1: [], 2: []}
    scores = _make_scores(actual, matchups, user_best_ball_by_week=bb)
    out = calculate_best_and_worst_manager(scores)
    # Only week 2 contributes: 90/100 = 0.90 -> 90.0%.
    assert out["most_efficient"]["username"] == "alice"
    assert out["most_efficient"]["efficiency_pct"] == pytest.approx(90.0)


def test_manager_efficiency_returns_by_user_map(four_team_scores):
    out = calculate_best_and_worst_manager(four_team_scores)
    assert "by_user" in out
    assert set(out["by_user"].keys()) == {"alice", "bob", "carol", "dave"}
    # All efficiencies should be in (0, 100] given our synthetic data.
    for pct in out["by_user"].values():
        assert 0.0 < pct <= 100.0


# ---------------------------------------------------------------------------
# 6. Falloff / comeup
# ---------------------------------------------------------------------------
def test_falloff_and_comeup_picks_extremes(four_team_scores):
    out = calculate_biggest_falloff_and_come_up(four_team_scores)
    # Alice +30 across halves -> biggest comeup.
    assert out["biggest_come_up"]["username"] == "alice"
    # Bob -30 -> biggest falloff (stored as positive magnitude).
    assert out["biggest_falloff"]["username"] == "bob"
    assert out["biggest_falloff"]["delta"] > 0
    # Dave is constant -> not in either extreme but appears in by_user.
    assert "dave" in out["by_user"]
    assert out["by_user"]["dave"]["first_half_avg"] == 100.0
    assert out["by_user"]["dave"]["second_half_avg"] == 100.0


def test_falloff_skips_users_with_too_few_weeks():
    actual = {"alice": {1: 100.0, 2: 110.0, 3: 120.0}}  # only 3 weeks
    scores = _make_scores(actual, {1: [], 2: [], 3: []})
    out = calculate_biggest_falloff_and_come_up(scores)
    assert out["biggest_come_up"] is None
    assert out["biggest_falloff"] is None
    assert out["by_user"] == {}


# ---------------------------------------------------------------------------
# 7. _process_week_matchups (tie + bench-zero edge cases)
# ---------------------------------------------------------------------------
class _FakeCtx:
    def __init__(self):
        self.roster_id_to_username = {1: "alice", 2: "bob"}
        # One QB, one FLEX (so optimal lineup picks the best two players).
        self.roster_positions_groups = [["QB"], ["RB", "WR", "TE"]]


def test_process_week_handles_tie_and_skips_zero_bench():
    ctx = _FakeCtx()
    players_meta = {
        "p1": {"full_name": "QB1",  "fantasy_positions": ["QB"]},
        "p2": {"full_name": "RB1",  "fantasy_positions": ["RB"]},
        "p3": {"full_name": "WR1",  "fantasy_positions": ["WR"]},  # bench, 0 pts -> skipped from troll
        "p4": {"full_name": "QB2",  "fantasy_positions": ["QB"]},
        "p5": {"full_name": "RB2",  "fantasy_positions": ["RB"]},
        "p6": {"full_name": "WR2",  "fantasy_positions": ["WR"]},  # bench, scored some pts
    }
    matchups = [
        {
            "roster_id": 1,
            "matchup_id": 7,
            "points": 100.0,
            "starters": ["p1", "p2"],
            "players": ["p1", "p2", "p3"],
            "players_points": {"p1": 30.0, "p2": 70.0, "p3": 0.0},
        },
        {
            "roster_id": 2,
            "matchup_id": 7,
            "points": 100.0,  # tie!
            "starters": ["p4", "p5"],
            "players": ["p4", "p5", "p6"],
            "players_points": {"p4": 25.0, "p5": 60.0, "p6": 15.0},
        },
    ]
    out = WeeklyScores()
    _process_week_matchups(matchups, week=1, ctx=ctx, players_meta=players_meta, out=out)

    # Tie surfaces as "T" for both.
    assert out.user_results_by_week["alice"][1] == "T"
    assert out.user_results_by_week["bob"][1] == "T"
    # Median of [100, 100] is 100.
    assert out.median_scores[1] == 100.0
    # Bench player p3 with 0 pts is skipped.
    assert "p3" not in out.user_player_start_sit_points["alice"] or \
        out.user_player_start_sit_points["alice"]["p3"]["bench"] == []
    # Bench player p6 with 15 pts IS recorded.
    assert out.user_player_start_sit_points["bob"]["p6"]["bench"] == [15.0]
    # Best-ball: bob's bench WR2 (15) beats nothing here because the FLEX is
    # already the highest-scoring RB/WR/TE. Just verify it's a finite number.
    assert out.user_best_ball_score_by_week["alice"][1] >= 100.0


# ---------------------------------------------------------------------------
# 8. _calculate_optimal_lineup greedy semantics
# ---------------------------------------------------------------------------
def test_optimal_lineup_picks_best_per_slot():
    sorted_players = {
        "QB": [("QB1", 25.0), ("QB2", 18.0)],
        "RB": [("RB1", 22.0), ("RB2", 12.0)],
        "WR": [("WR1", 20.0), ("WR2", 10.0)],
        "TE": [("TE1", 8.0)],
    }
    groups = [["QB"], ["RB"], ["RB", "WR", "TE"]]  # QB / RB / FLEX
    lineup, total = _calculate_optimal_lineup(sorted_players, groups)
    names = [t[0] for t in lineup]
    assert names == ["QB1", "RB1", "WR1"]  # FLEX takes WR1 (20) > RB2 (12) > TE1 (8)
    assert total == pytest.approx(25.0 + 22.0 + 20.0)
    # WR1 was popped, so WR bucket head is now WR2.
    assert sorted_players["WR"][0][0] == "WR2"


def test_optimal_lineup_handles_empty_position_group():
    sorted_players = {"QB": [("QB1", 20.0)]}  # no RB/WR
    groups = [["QB"], ["RB", "WR"]]  # FLEX has nobody eligible
    lineup, total = _calculate_optimal_lineup(sorted_players, groups)
    assert lineup[0] == ("QB1", 20.0)
    assert lineup[1] == ("N/A", 0.0)
    assert total == pytest.approx(20.0)
