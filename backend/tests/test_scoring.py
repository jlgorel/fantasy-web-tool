"""Tests for app.services.scoring.calculate_potential_fantasy_score.

The function is a pure transform from
(stat_projection_dict, scoring_multipliers) -> projected_points. We test
the math at four levels:

1. Empty/missing player → 0 points and a clear "not projected" string.
2. Backup-only stats are added on top of primary stats.
3. Different scoring rules (STD / Half PPR / Full PPR) yield the right
   multiplier for receptions.
4. The boom/bust distribution is selected based on rec_points + 6pt-TD flag.
"""
from __future__ import annotations

import pytest

from app.services.scoring import (
    _player_key,
    _select_simulation_distribution,
    calculate_potential_fantasy_score,
)


def _mults(rec: float = 0.5, pass_td: float = 4.0) -> dict:
    """Minimal scoring multipliers covering every key the function may touch."""
    return {
        "Interceptions": -2.0,
        "Receiving Touchdown": 6.0,
        "Rushing Touchdown": 6.0,
        "Anytime Touchdown": 6.0,
        "Passing Yards": 0.04,
        "Passing TDs": pass_td,
        "Passing Touchdowns": pass_td,
        "Rushing Yards": 0.1,
        "Receiving Yards": 0.1,
        "Receptions": rec,
        "TE Receptions": rec,
    }


class TestPlayerKey:
    def test_strips_punct_and_lowers(self):
        assert _player_key("Ja'Marr Chase") == "jamarrchase"

    def test_unicode_alnum_kept(self):
        # Apostrophes / hyphens / dots all dropped.
        assert _player_key("D.K. Metcalf-Jr.") == "dkmetcalfjr"


class TestEmptyAndMissing:
    def test_unknown_player_returns_zero(self):
        pts, used_old, statline, boom = calculate_potential_fantasy_score(
            "Nobody", "WR", {}, {}, _mults()
        )
        assert pts == 0
        assert used_old is False
        assert "No stats projected" in statline
        assert boom is None


class TestPrimaryProjectionMath:
    def test_rb_basic_stats(self):
        proj = {
            "rbguy": {
                "Rushing Yards": 100.0,
                "Anytime Touchdown": 1.0,
                "Receptions": 4.0,
                "Receiving Yards": 30.0,
            }
        }
        # 100 * 0.1 (rush yds) + 1 * 6 (TD) + 4 * 0.5 (rec) + 30 * 0.1 = 10 + 6 + 2 + 3 = 21
        pts, _, statline, _ = calculate_potential_fantasy_score(
            "RB Guy", "RB", proj, {}, _mults(rec=0.5)
        )
        assert pts == pytest.approx(21.0)
        assert "Rushing Yards" in statline

    def test_te_uses_te_reception_multiplier(self):
        proj = {"teguy": {"Receptions": 5.0}}
        # TE Receptions multiplier = 0.75 in this league (TE-prem)
        m = _mults(rec=0.5)
        m["TE Receptions"] = 0.75
        pts, *_ = calculate_potential_fantasy_score("TE Guy", "TE", proj, {}, m)
        assert pts == pytest.approx(5.0 * 0.75)

    def test_qb_pass_td_multiplier(self):
        proj = {"qbguy": {"Passing Yards": 250.0, "Passing Touchdowns": 2.0}}
        # 250 * 0.04 + 2 * 6 = 10 + 12 = 22 in 6-pt-TD leagues
        pts, *_ = calculate_potential_fantasy_score(
            "QB Guy", "QB", proj, {}, _mults(rec=0.5, pass_td=6.0)
        )
        assert pts == pytest.approx(22.0)

    def test_non_stat_keys_excluded_from_statline(self):
        proj = {
            "guy": {
                "Rushing Yards": 50.0,
                "Opponent Rating": 4,
                "Team Name": "Some Team",
            }
        }
        _, _, statline, _ = calculate_potential_fantasy_score(
            "Guy", "RB", proj, {}, _mults()
        )
        assert "Opponent Rating" not in statline
        assert "Team Name" not in statline
        assert "Rushing Yards" in statline


class TestBackupAugment:
    def test_backup_stat_missing_from_primary_added(self):
        primary = {"guy": {"Rushing Yards": 100.0}}
        backup = {"guy": {"Receiving Yards": 50.0}}
        # 100 * 0.1 + 50 * 0.1 = 15
        pts, *_ = calculate_potential_fantasy_score("Guy", "RB", primary, backup, _mults())
        assert pts == pytest.approx(15.0)

    def test_backup_does_not_double_count(self):
        primary = {"guy": {"Rushing Yards": 100.0}}
        backup = {"guy": {"Rushing Yards": 200.0}}  # already in primary
        pts, *_ = calculate_potential_fantasy_score("Guy", "RB", primary, backup, _mults())
        assert pts == pytest.approx(10.0)


class TestSimulationSelection:
    def test_qb_selects_qb_distribution(self):
        sims = {"QB_STD": {"boom": 0.1, "bust": 0.1}, "QB_6PT": {"boom": 0.2, "bust": 0.2}}
        # 4-pt TDs -> QB_STD
        assert _select_simulation_distribution(sims, rec_points=0.0, six_point_td=False)["boom"] == 0.1
        # 6-pt TDs -> QB_6PT
        assert _select_simulation_distribution(sims, rec_points=0.0, six_point_td=True)["boom"] == 0.2

    @pytest.mark.parametrize(
        "rec_points,expected",
        [
            (0.0, "STD"),
            (0.5, "HalfPPR"),
            (1.0, "PPR"),
        ],
    )
    def test_skill_position_distribution_by_ppr(self, rec_points, expected):
        sims = {"STD": {"k": 1}, "HalfPPR": {"k": 2}, "PPR": {"k": 3}}
        marker = {"STD": 1, "HalfPPR": 2, "PPR": 3}[expected]
        out = _select_simulation_distribution(sims, rec_points=rec_points, six_point_td=False)
        assert out["k"] == marker

    def test_error_simulations_returns_none(self):
        assert _select_simulation_distribution({"error": "x"}, 0.5, False) is None

    def test_simulations_attached_to_player_output(self):
        proj = {
            "guy": {
                "Rushing Yards": 50.0,
                "Simulations": {"HalfPPR": {"boom": 0.4, "bust": 0.1, "percentiles": {}}},
            }
        }
        _, _, _, boom = calculate_potential_fantasy_score("Guy", "RB", proj, {}, _mults(rec=0.5))
        assert boom is not None
        assert boom["boom"] == 0.4
