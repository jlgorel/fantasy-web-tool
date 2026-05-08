"""Tests for ``app.services.wrapped.streamers_accolades``."""
from __future__ import annotations

from collections import defaultdict
from typing import List

import pytest

from app.services.wrapped.schedule import WeeklyScores
from app.services.wrapped.streamers_accolades import calculate_streamer_accolades


def _scores_with(
    user_to_pos_to_week_pts: dict,
    weeks_played: List[int] = [1, 2, 3],
) -> WeeklyScores:
    """Build a minimal WeeklyScores with both per-week totals and the
    per-position starter buckets populated.

    ``user_to_pos_to_week_pts`` is e.g. ``{"alice": {"K": {1: 10, 2: 5}}}``.
    We also seed ``user_score_by_week`` so ``weeks_played`` resolves.
    """
    out = WeeklyScores()
    for user, pos_to_weeks in user_to_pos_to_week_pts.items():
        for w in weeks_played:
            out.user_score_by_week[user][w] = 100.0  # arbitrary
        for pos, week_to_pts in pos_to_weeks.items():
            for w, pts in week_to_pts.items():
                out.user_position_starter_points_by_week[user][pos][w] = pts
    return out


_K_DEF_LEAGUE = [["QB"], ["RB"], ["RB"], ["WR"], ["WR"], ["TE"], ["K"], ["DEF"]]
_K_ONLY_LEAGUE = [["QB"], ["RB"], ["WR"], ["TE"], ["K"]]
_NO_K_NO_DEF_LEAGUE = [["QB"], ["RB"], ["WR"], ["TE"]]


class TestStreamerAccolades:
    def test_includes_both_positions_when_league_has_both(self):
        scores = _scores_with({
            "alice": {"K": {1: 10, 2: 8, 3: 12}, "DEF": {1: 5, 2: 7, 3: 6}},
            "bob":   {"K": {1: 4, 2: 4, 3: 4},   "DEF": {1: 3, 2: 3, 3: 3}},
        })
        out = calculate_streamer_accolades(scores, _K_DEF_LEAGUE)
        assert out.positions_included == ["K", "DEF"]
        # alice averages: K = 30/3 = 10, DEF = 18/3 = 6, combined = 16
        assert out.by_user["alice"]["k_avg"] == 10.0
        assert out.by_user["alice"]["def_avg"] == 6.0
        assert out.by_user["alice"]["combined_avg"] == 16.0
        # winners
        assert out.best_kicker == {"username": "alice", "average": 10.0}
        assert out.best_defense == {"username": "alice", "average": 6.0}
        assert out.best_combined == {"username": "alice", "average": 16.0}

    def test_k_only_league_has_no_def_or_combined(self):
        scores = _scores_with({
            "alice": {"K": {1: 10, 2: 10, 3: 10}},
        })
        out = calculate_streamer_accolades(scores, _K_ONLY_LEAGUE)
        assert out.positions_included == ["K"]
        assert out.best_kicker is not None
        assert out.best_defense is None
        assert out.best_combined is None
        assert out.by_user["alice"]["def_avg"] is None
        assert out.by_user["alice"]["combined_avg"] is None

    def test_league_without_k_or_def_returns_empty(self):
        scores = _scores_with({"alice": {}})
        out = calculate_streamer_accolades(scores, _NO_K_NO_DEF_LEAGUE)
        assert out.positions_included == []
        assert out.by_user == {}
        assert out.best_kicker is None
        assert out.best_defense is None
        assert out.best_combined is None

    def test_missing_weeks_count_as_zero_for_average(self):
        """If a user only fielded a kicker in 1 of 3 played weeks, the
        denominator is still 3 (weeks played), not 1."""
        scores = _scores_with(
            {"alice": {"K": {1: 9}}},
            weeks_played=[1, 2, 3],
        )
        out = calculate_streamer_accolades(scores, _K_ONLY_LEAGUE)
        # 9 total / 3 weeks played = 3.0
        assert out.by_user["alice"]["k_avg"] == 3.0

    def test_winner_picks_highest_value(self):
        scores = _scores_with({
            "alice": {"K": {1: 5, 2: 5, 3: 5}, "DEF": {1: 5, 2: 5, 3: 5}},
            "bob":   {"K": {1: 9, 2: 9, 3: 9}, "DEF": {1: 1, 2: 1, 3: 1}},
        })
        out = calculate_streamer_accolades(scores, _K_DEF_LEAGUE)
        # bob wins K, alice wins DEF, combined goes to whoever has higher sum.
        assert out.best_kicker["username"] == "bob"
        assert out.best_defense["username"] == "alice"
        # alice combined = 5+5=10, bob combined = 9+1=10. Tie -> max picks
        # whichever sort order delivers; assert one of them.
        assert out.best_combined["username"] in {"alice", "bob"}
        assert out.best_combined["average"] == 10.0

    def test_user_with_no_starts_at_position_gets_none(self):
        """Defensive: user who never started a kicker (e.g. team folded
        before the season) doesn't crash and reports None for that pos."""
        scores = WeeklyScores()
        # alice played weeks but never started a K.
        scores.user_score_by_week["alice"][1] = 100.0
        scores.user_score_by_week["alice"][2] = 100.0
        scores.user_position_starter_points_by_week["alice"]["DEF"][1] = 5
        scores.user_position_starter_points_by_week["alice"]["DEF"][2] = 5
        out = calculate_streamer_accolades(scores, _K_DEF_LEAGUE)
        assert out.by_user["alice"]["k_avg"] is None
        assert out.by_user["alice"]["def_avg"] == 5.0
        # combined uses 0 + 5 = 5 (k_avg None falls back to 0).
        assert out.by_user["alice"]["combined_avg"] == 5.0
