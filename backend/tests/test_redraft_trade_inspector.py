"""Tests for the redraft retrospective trade evaluator.

Pure-function tests: we synthesize ``Trade`` objects + season-scoring
dicts directly, no Sleeper IO. The IO-aware ``inspect_redraft_trade``
orchestrator gets covered by a small smoke test against a synthetic
``LeagueContext``.
"""
from __future__ import annotations

from typing import Any, Dict, List

import pytest

from app.services.wrapped.redraft_trade_inspector import (
    CLOSE_VORP,
    WASH_VORP,
    evaluate_redraft_trade,
    inspect_redraft_trade,
)
from app.services.wrapped.transactions import Trade, TradeSide


# ---------------------------------------------------------------------------
# Synthesis helpers
# ---------------------------------------------------------------------------
def make_player(
    pid: str,
    name: str,
    position: str,
    weekly: Dict[int, float],
    *,
    points_key: str = "half_ppr",
) -> Dict[str, Any]:
    """Build a season-scoring entry with one points key populated."""
    weekly_blob = {
        str(w): {
            "half_ppr": 0.0, "ppr": 0.0, "std": 0.0,
            "receptions": 0, "pass_td": 0,
        }
        for w in weekly
    }
    for w, pts in weekly.items():
        weekly_blob[str(w)][points_key] = pts
    return {
        "full_name": name,
        "fantasy_positions": [position],
        "scoring_data_weekly": weekly_blob,
        "scoring_data_season": {},
    }


def make_trade(week: int, sides_dict: Dict[str, List[str]]) -> Trade:
    """``sides_dict`` is ``{username: [pid, pid, ...]}``."""
    sides = {
        user: TradeSide(username=user, received_player_ids=list(pids), received_picks=[])
        for user, pids in sides_dict.items()
    }
    return Trade(week=week, transaction_id=f"tx_w{week}", sides=sides, status_updated_ms=None)


# Baseline that matches a stock 12-team / 1 QB / 2 RB / 2 WR / 1 TE / 1 FLEX
# league well enough for these tests. Season totals -> per-game divided by 17
# inside the evaluator.
DEFAULT_BASELINE_SEASON = {
    "QB": 17 * 14.0,   # 14 ppg QB baseline
    "RB": 17 * 8.0,    # 8 ppg RB baseline
    "WR": 17 * 9.0,    # 9 ppg WR baseline
    "TE": 17 * 6.0,    # 6 ppg TE baseline
}


def run(
    trade: Trade,
    scoring: Dict[str, Dict[str, Any]],
    *,
    start: int = 5,
    end: int = 17,
    skill_key: str = "half_ppr",
):
    return evaluate_redraft_trade(
        trade,
        season=2024,
        season_scoring=scoring,
        qb_score_key="std",
        skill_score_key=skill_key,
        baseline_total_points=DEFAULT_BASELINE_SEASON,
        start_week=start,
        end_week=end,
    )


# ---------------------------------------------------------------------------
# Verdict shape
# ---------------------------------------------------------------------------
class TestVerdicts:
    def test_clean_one_for_one_winner(self):
        # alice gets a stud WR (~22 ppg), bob gets a mid WR (~9 ppg).
        scoring = {
            "wr_stud": make_player("wr_stud", "Stud WR", "WR",
                                   {w: 22.0 for w in range(5, 18)}),
            "wr_mid":  make_player("wr_mid", "Mid WR", "WR",
                                   {w: 9.0 for w in range(5, 18)}),
        }
        trade = make_trade(4, {"alice": ["wr_stud"], "bob": ["wr_mid"]})
        result = run(trade, scoring)
        assert result.verdict == "alice"
        assert result.margin_label == "decisive"
        # Alice ~ (22-9)*13 = 169; bob ~ 0. Margin >> CLOSE_VORP.
        assert result.margin_vorp > CLOSE_VORP

    def test_three_for_one_filler_loses(self):
        """Canonical 3-for-1 trap: alice ships a stud, bob ships three
        waiver-tier RBs (right at the baseline). The stud's VORP should
        beat the sum of three ~0-VORP fillers."""
        scoring = {
            "rb_stud": make_player("rb_stud", "Stud RB", "RB",
                                   {w: 20.0 for w in range(5, 18)}),
            "rb_f1":   make_player("rb_f1", "Filler 1", "RB",
                                   {w: 8.0 for w in range(5, 18)}),  # == baseline
            "rb_f2":   make_player("rb_f2", "Filler 2", "RB",
                                   {w: 8.0 for w in range(5, 18)}),
            "rb_f3":   make_player("rb_f3", "Filler 3", "RB",
                                   {w: 8.0 for w in range(5, 18)}),
        }
        # alice GAVE the stud, RECEIVED three fillers; bob the reverse.
        trade = make_trade(4, {
            "alice": ["rb_f1", "rb_f2", "rb_f3"],  # alice received the fillers
            "bob": ["rb_stud"],                    # bob received the stud
        })
        result = run(trade, scoring)
        assert result.verdict == "bob"
        # Fillers' VORP is ~0; stud's VORP is (20-8)*13 = 156. Decisive.
        assert result.margin_label == "decisive"

    def test_three_for_one_legit_stack_wins(self):
        """If the 3 players are NOT filler -- they each clear baseline by
        a healthy margin -- a 3-pack can legitimately beat one stud."""
        scoring = {
            "wr_stud": make_player("wr_stud", "Stud WR", "WR",
                                   {w: 18.0 for w in range(5, 18)}),
            "wr_a":    make_player("wr_a", "Solid A", "WR",
                                   {w: 14.0 for w in range(5, 18)}),
            "wr_b":    make_player("wr_b", "Solid B", "WR",
                                   {w: 13.0 for w in range(5, 18)}),
            "wr_c":    make_player("wr_c", "Solid C", "WR",
                                   {w: 12.0 for w in range(5, 18)}),
        }
        trade = make_trade(4, {
            "alice": ["wr_a", "wr_b", "wr_c"],
            "bob": ["wr_stud"],
        })
        result = run(trade, scoring)
        # alice: ((14-9) + (13-9) + (12-9)) * 13 = 156
        # bob:   (18-9) * 13 = 117
        assert result.verdict == "alice"
        assert result.margin_label == "decisive"

    def test_wash_within_threshold(self):
        scoring = {
            "wr1": make_player("wr1", "WR1", "WR",
                               {w: 12.0 for w in range(5, 18)}),
            "wr2": make_player("wr2", "WR2", "WR",
                               {w: 12.2 for w in range(5, 18)}),
        }
        trade = make_trade(4, {"alice": ["wr1"], "bob": ["wr2"]})
        result = run(trade, scoring)
        # |delta| = 0.2 * 13 = 2.6 < WASH_VORP
        assert result.verdict == "wash"
        assert result.margin_label == "wash"
        assert result.margin_vorp < WASH_VORP

    def test_mid_season_trade_window_math(self):
        """Trade in week 8 -> window is weeks 9..17 (9 weeks). Earlier
        scoring data is ignored even if it exists."""
        weekly = {w: 25.0 for w in range(1, 18)}  # full season of 25 ppg
        scoring = {
            "qb_a": make_player("qb_a", "QB A", "QB", weekly, points_key="std"),
            "qb_b": make_player("qb_b", "QB B", "QB",
                                {w: 10.0 for w in range(1, 18)}, points_key="std"),
        }
        trade = make_trade(8, {"alice": ["qb_a"], "bob": ["qb_b"]})
        result = run(trade, scoring, start=9, end=17)
        # alice ros_points should be 25 * 9 = 225 (only weeks 9..17 count)
        alice = next(s for s in result.sides if s.username == "alice")
        assert alice.assets[0].games_played == 9
        assert alice.assets[0].ros_points == pytest.approx(225.0)

    def test_injured_player_reduces_games_and_baseline(self):
        """A player who only scored in 3 of 13 window weeks gets
        baseline_points = baseline_ppg * 3, NOT baseline_ppg * 13."""
        scoring = {
            "rb_injured": make_player("rb_injured", "Hurt RB", "RB",
                                      {5: 15.0, 6: 12.0, 7: 14.0}),
        }
        trade = make_trade(4, {
            "alice": ["rb_injured"],
            "bob": [],
        })
        result = run(trade, scoring)
        alice = next(s for s in result.sides if s.username == "alice")
        asset = alice.assets[0]
        assert asset.games_played == 3
        # Baseline = 8.0 ppg * 3 games = 24. Points = 15+12+14 = 41.
        # VORP = 41 - 24 = 17.
        assert asset.baseline_points == pytest.approx(24.0)
        assert asset.vorp == pytest.approx(17.0)

    def test_qb_uses_qb_scoring_key(self):
        """QBs use ``qb_score_key`` (std / 6pt_pass_td), not skill_score_key."""
        scoring = {
            # All points in the std bucket; half_ppr is 0.
            "qb_a": make_player("qb_a", "QB A", "QB",
                                {w: 20.0 for w in range(5, 18)}, points_key="std"),
        }
        trade = make_trade(4, {"alice": ["qb_a"], "bob": []})
        result = run(trade, scoring)
        alice = next(s for s in result.sides if s.username == "alice")
        # QB picked up std-scoring; should produce non-zero ros_points
        # even though skill_key='half_ppr' would have given 0.
        assert alice.assets[0].ros_points > 0

    def test_unknown_player_yields_zero_vorp(self):
        """A pid missing from the scoring blob should produce a 0-VORP
        asset rather than crashing."""
        trade = make_trade(4, {"alice": ["ghost_pid"], "bob": []})
        result = run(trade, {})
        alice = next(s for s in result.sides if s.username == "alice")
        assert alice.assets[0].vorp == 0.0
        assert alice.assets[0].games_played == 0


# ---------------------------------------------------------------------------
# IO-aware orchestrator
# ---------------------------------------------------------------------------
class _FakeCtx:
    """Minimal stand-in for LeagueContext (just the fields the inspector
    + replacement_value read)."""
    def __init__(self):
        self.is_dynasty = False
        self.last_regular_season_week = 17
        self.roster_positions_groups = [["QB"], ["RB"], ["RB"], ["WR"], ["WR"], ["TE"], ["RB", "WR", "TE"]]
        self.total_rosters = 12
        self.qb_score_key = "std"
        self.skill_score_key = "half_ppr"


class TestOrchestrator:
    def test_inspect_redraft_trade_smoke(self):
        # 36 RBs so baseline rank lookup finds something at rank ~28.
        scoring = {}
        for i in range(1, 50):
            scoring[f"rb{i}"] = make_player(
                f"rb{i}", f"RB {i}", "RB",
                {w: max(2.0, 25.0 - 0.5 * i) for w in range(1, 18)},
            )
            scoring[f"rb{i}"]["scoring_data_season"] = {
                "half_ppr_rank": i, "half_ppr_points": max(34.0, 425.0 - 8.5 * i),
            }

        trade = make_trade(4, {"alice": ["rb1"], "bob": ["rb20"]})
        payload = inspect_redraft_trade(
            trade, ctx=_FakeCtx(), season=2024, season_scoring=scoring
        )
        assert payload["transaction_id"] == trade.transaction_id
        assert payload["trade_week"] == 4
        assert payload["evaluation"]["window"]["start_week"] == 5
        assert payload["evaluation"]["window"]["end_week"] == 17
        assert payload["evaluation"]["verdict"] == "alice"  # rb1 >> rb20

    def test_missing_scoring_blob_raises(self):
        trade = make_trade(4, {"alice": ["wr1"], "bob": ["wr2"]})
        with pytest.raises(RuntimeError, match="player_season_scoring"):
            inspect_redraft_trade(
                trade, ctx=_FakeCtx(), season=2024, season_scoring={}
            )
