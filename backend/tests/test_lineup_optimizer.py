"""Tests for the position-name normalizer and ranking-mode logic in
app.services.lineup_optimizer."""
from __future__ import annotations

from collections import defaultdict

import pytest

from app.services.lineup_optimizer import (
    _annotate_qb_stacks,
    clean_up_pos_names,
    get_highest_ranked_player_from_page,
    list_players_for_pos_name,
)


# ---------------------------------------------------------------------------
# clean_up_pos_names
# ---------------------------------------------------------------------------
class TestCleanUpPosNames:
    def test_single_position_returns_string(self):
        # When all bench is stripped and only one slot remains, callers expect
        # the bare position name back as a string.
        assert clean_up_pos_names(["QB", "BN", "BN"]) == "QB"

    def test_only_bench_returns_BN(self):
        assert clean_up_pos_names(["BN", "BN"]) == "BN"

    def test_flex_super_flex_aliases(self):
        out = clean_up_pos_names(["FLEX", "SUPER_FLEX", "REC_FLEX", "DEF"])
        assert isinstance(out, defaultdict)
        assert out["Flex"] == 1
        assert out["SF"] == 1
        assert out["WT"] == 1
        assert out["DST"] == 1

    def test_counts_repeats(self):
        out = clean_up_pos_names(["RB", "RB", "WR", "WR", "WR", "BN"])
        assert dict(out) == {"RB": 2, "WR": 3}


class TestListPlayersForPosName:
    def test_flex_aggregates_wr_te_rb(self):
        groups = {"WR": ["Puka"], "TE": ["Kelce"], "RB": ["CMC"]}
        players, src = list_players_for_pos_name(groups, "Flex")
        assert set(players) == {"Puka", "Kelce", "CMC"}
        assert set(src) == {"WR", "TE", "RB"}

    def test_qb_only_pulls_qb(self):
        groups = {"QB": ["Mahomes"], "WR": ["Puka"]}
        players, src = list_players_for_pos_name(defaultdict(list, groups), "QB")
        assert players == ["Mahomes"]
        assert src == ["QB"]


# ---------------------------------------------------------------------------
# get_highest_ranked_player_from_page — Boris vs Vegas modes
# ---------------------------------------------------------------------------
class TestRankerModes:
    """The ranker is the heart of the lineup optimizer. We feed it a fake
    projection blob and a fake tier dict and assert each mode picks the
    expected player."""

    @pytest.fixture()
    def mults(self):
        return {
            "Interceptions": -2.0,
            "Receiving Touchdown": 6.0,
            "Rushing Touchdown": 6.0,
            "Anytime Touchdown": 6.0,
            "Passing Yards": 0.04,
            "Passing TDs": 4.0,
            "Passing Touchdowns": 4.0,
            "Rushing Yards": 0.1,
            "Receiving Yards": 0.1,
            "Receptions": 0.5,
            "TE Receptions": 0.5,
        }

    @pytest.fixture()
    def projections(self):
        # Player 'low_tier_high_vegas' projects 25 pts, 'high_tier_low_vegas' 8
        return {
            "lowtierhighvegas": {"Rushing Yards": 250.0},  # 25 pts
            "hightierlowvegas": {"Rushing Yards": 80.0},   # 8 pts
        }

    @pytest.fixture()
    def tier_dict(self):
        # high_tier_low_vegas is in tier 1 (best); low_tier_high_vegas tier 5
        return {
            "high tier low vegas": {"RB": "1"},
            "low tier high vegas": {"RB": "5"},
        }

    def test_boris_mode_picks_better_tier(self, projections, tier_dict, mults):
        name, tier = get_highest_ranked_player_from_page(
            ["high tier low vegas", "low tier high vegas"],
            "RB",
            tier_dict,
            projections,
            {},
            mults,
            mode="boris",
        )
        # Boris-tier-first → tier 1 wins despite lower Vegas
        assert name == "high tier low vegas"
        assert tier == 1

    def test_vegas_mode_picks_higher_projection(self, projections, tier_dict, mults):
        name, _ = get_highest_ranked_player_from_page(
            ["high tier low vegas", "low tier high vegas"],
            "RB",
            tier_dict,
            projections,
            {},
            mults,
            mode="vegas",
        )
        # Vegas-first → 25 pts wins over 8 pts even with worse Boris tier
        assert name == "low tier high vegas"

    def test_vegas_mode_ties_break_on_boris(self, mults):
        # Both project zero (off-season). Mode='vegas' should fall through to
        # Boris tier as tiebreaker.
        proj = {}  # neither player has projection -> both 0
        tiers = {"a": {"RB": "1"}, "b": {"RB": "8"}}
        name, _ = get_highest_ranked_player_from_page(
            ["a", "b"], "RB", tiers, proj, {}, mults, mode="vegas"
        )
        assert name == "a"

    def test_empty_player_list_returns_sentinel(self, projections, tier_dict, mults):
        name, tier = get_highest_ranked_player_from_page(
            [], "RB", tier_dict, projections, {}, mults
        )
        assert name == "None Owned"
        assert tier == "N/A"

    def test_unknown_player_falls_back_unranked(self, mults):
        name, tier = get_highest_ranked_player_from_page(
            ["unknown player"], "RB", {}, {}, {}, mults
        )
        # Player has no tier, no projection — but is the only option.
        assert name == "unknown player"
        # Either a numeric tier (999) or the "Unranked" sentinel; matches
        # legacy behavior of returning *something* selectable.
        assert tier in (999, "Unranked", float("inf"))


# ---------------------------------------------------------------------------
# QB Stack annotation
# ---------------------------------------------------------------------------
class TestAnnotateStacks:
    def test_marks_wr_te_on_qb_team(self, make_row):
        rows = [
            make_row("QB", "Patrick Mahomes", reallife="QB", team_name="Kansas City Chiefs"),
            make_row("WR", "Rashee Rice", reallife="WR", team_name="Kansas City Chiefs"),
            make_row("TE", "Travis Kelce", reallife="TE", team_name="Kansas City Chiefs"),
            make_row("WR", "Justin Jefferson", reallife="WR", team_name="Minnesota Vikings"),
        ]
        _annotate_qb_stacks(rows)
        rice = next(r for r in rows if r["NAME"] == "Rashee Rice")
        kelce = next(r for r in rows if r["NAME"] == "Travis Kelce")
        jj = next(r for r in rows if r["NAME"] == "Justin Jefferson")
        assert rice.get("STACK_WITH_QB") is True
        assert rice["STACK_QB_NAME"] == "Patrick Mahomes"
        assert kelce.get("STACK_WITH_QB") is True
        # Different NFL team — no stack
        assert "STACK_WITH_QB" not in jj

    def test_bench_rows_skipped(self, make_row):
        rows = [
            make_row("QB", "Mahomes", reallife="QB", team_name="Kansas City Chiefs"),
            # Bench WR on the same team should NOT get the badge — the badge
            # is meant for active roll-out stacks.
            {**make_row("BN", "Rashee Rice", reallife="WR", team_name="Kansas City Chiefs"), "POS": "BN"},
        ]
        _annotate_qb_stacks(rows)
        bench_rice = next(r for r in rows if r["NAME"] == "Rashee Rice")
        assert "STACK_WITH_QB" not in bench_rice

    def test_no_qb_no_annotations(self, make_row):
        rows = [
            make_row("WR", "Justin Jefferson", reallife="WR", team_name="Minnesota Vikings"),
        ]
        _annotate_qb_stacks(rows)  # should not raise
        assert "STACK_WITH_QB" not in rows[0]
