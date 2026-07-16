"""Tests for the custom-VBD scaffold (draft_help.custom_vbd)."""
from __future__ import annotations

import pytest

from app.services.draft_help import custom_vbd as cv
from app.services.draft_help.custom_vbd import PlayerProjection, ScoringSettings


def test_replacement_rank_by_position():
    ranks = cv.replacement_rank_by_position(12, {"QB": 1, "RB": 2, "WR": 2, "TE": 1, "FLEX": 1})
    assert ranks["QB"] == 12               # 12 * (1 + 0)
    assert ranks["RB"] == round(12 * 2.4)  # 2 dedicated + 0.4 of one flex = 29
    assert ranks["WR"] == 30               # 12 * (2 + 0.5)
    assert ranks["TE"] == 13               # 12 * (1 + 0.1)


def test_vbd_from_projections_subtracts_replacement_baseline():
    projs = [
        PlayerProjection("a", "A", "RB", 100),
        PlayerProjection("b", "B", "RB", 80),
        PlayerProjection("c", "C", "RB", 50),
    ]
    # 2 teams, 1 RB slot, no flex -> replacement rank 2 -> baseline = 2nd RB (80).
    vbd = cv.vbd_from_projections(projs, teams=2, slots={"RB": 1})
    assert vbd["a"] == pytest.approx(20)
    assert vbd["b"] == pytest.approx(0)
    assert vbd["c"] == pytest.approx(-30)


def test_blend_vbd_weighted_and_missing_players():
    blended = cv.blend_vbd([{"x": 10, "y": 4}, {"x": 20}], weights=[0.5, 0.5])
    assert blended["x"] == pytest.approx(15)  # (10*.5 + 20*.5) / 1.0
    assert blended["y"] == pytest.approx(4)   # only present in the first source


def test_overall_ranks_from_vbd_orders_desc():
    assert cv.overall_ranks_from_vbd({"a": 5, "b": 50, "c": 20}) == {"b": 1, "c": 2, "a": 3}


def test_vegas_implied_points_is_a_stub():
    with pytest.raises(NotImplementedError):
        cv.vegas_implied_points(
            team_totals={}, spreads={}, usage_shares={}, scoring=ScoringSettings(),
        )
