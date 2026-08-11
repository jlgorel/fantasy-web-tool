"""Tests for the pure Vegas-projection accountability helpers in
``azure-functions/vegas_accuracy.py``.

The module has no Azure/Playwright imports, so we just add ``azure-functions/``
to ``sys.path`` and import it directly. We focus on the two behaviors that
matter: the weekly *locking* merge (a Thursday-night player must not be wiped
out by later Sunday scrapes) and the accuracy-review compiler.
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
AZURE_FN_DIR = REPO_ROOT / "azure-functions"


@pytest.fixture(scope="module")
def va():
    saved_path = list(sys.path)
    saved = sys.modules.get("vegas_accuracy")
    sys.modules.pop("vegas_accuracy", None)
    sys.path.insert(0, str(AZURE_FN_DIR))
    try:
        yield importlib.import_module("vegas_accuracy")
    finally:
        sys.path[:] = saved_path
        if saved is None:
            sys.modules.pop("vegas_accuracy", None)
        else:
            sys.modules["vegas_accuracy"] = saved


def _rows(*triples):
    """Build ``standard_player_rankings`` half-ppr rows from (pid, pos, vegas)."""
    return [
        {"PID": pid, "NAME": f"Player {pid}", "POS": pos, "VEGAS": vegas}
        for pid, pos, vegas in triples
    ]


# ---------------------------------------------------------------------------
# Locking merge
# ---------------------------------------------------------------------------
def test_merge_locks_thursday_player_against_later_scrape(va):
    history = {}

    # Thursday scrape: TNF starter "tnf" has a live line.
    va.merge_week_capture(
        history, 5, _rows(("tnf", "RB", 14.0), ("sun", "WR", 10.0)), {}
    )
    # Sunday scrape: TNF game is over, "tnf" no longer appears; "sun" line moved.
    va.merge_week_capture(history, 5, _rows(("sun", "WR", 12.5)), {})

    bucket = history["5"]
    # The Thursday player is preserved at their last pre-game line.
    assert bucket["tnf"]["proj_half_ppr"] == 14.0
    # The Sunday player was refreshed to the newer line.
    assert bucket["sun"]["proj_half_ppr"] == 12.5


def test_merge_ignores_zero_projection(va):
    history = {}
    va.merge_week_capture(history, 1, _rows(("a", "RB", 9.0)), {})
    # A later run reports the player with no live line (0) — must not clobber.
    va.merge_week_capture(history, 1, _rows(("a", "RB", 0.0)), {})
    assert history["1"]["a"]["proj_half_ppr"] == 9.0


def test_merge_ecr_independent_of_vegas(va):
    history = {}
    va.merge_week_capture(history, 1, _rows(("a", "WR", 8.0)), {"a": 12, "b": 3})
    bucket = history["1"]
    assert bucket["a"]["ecr_overall"] == 12
    # Player "b" has an ECR but no Vegas line yet — still recorded.
    assert bucket["b"]["ecr_overall"] == 3
    assert "proj_half_ppr" not in bucket["b"]


def test_fp_overall_rank_by_pid_joins_on_normalized_name(va):
    players = {
        "111": {"full_name": "Ja'Marr Chase", "fantasy_positions": ["WR"]},
        "222": {"full_name": "Bijan Robinson", "fantasy_positions": ["RB"]},
    }
    fp = {
        "Ja'Marr Chase": {"overall_rank": 4},
        "Bijan Robinson": {"overall_rank": 2},
        "Unknown Guy": {"overall_rank": 99},
    }
    out = va.fp_overall_rank_by_pid(fp, players)
    assert out == {"111": 4, "222": 2}


# ---------------------------------------------------------------------------
# Review compiler
# ---------------------------------------------------------------------------
def _actuals(week, *triples):
    """Build ``player_season_scoring`` from (pid, pos, half_ppr)."""
    out = {}
    for pid, pos, pts in triples:
        out[pid] = {
            "fantasy_positions": [pos],
            "scoring_data_weekly": {str(week): {"half_ppr": pts}},
        }
    return out


def test_compile_review_scores_points_and_ranks(va):
    # Week 1 history: three RBs with Vegas projections + ECR overall ranks.
    history = {
        "1": {
            "a": {"pos": "RB", "proj_half_ppr": 20.0, "ecr_overall": 1},
            "b": {"pos": "RB", "proj_half_ppr": 15.0, "ecr_overall": 2},
            "c": {"pos": "RB", "proj_half_ppr": 10.0, "ecr_overall": 3},
        }
    }
    # Reality: Vegas order (a>b>c) is perfect; ECR would also say a>b>c.
    actuals = _actuals(1, ("a", "RB", 22.0), ("b", "RB", 14.0), ("c", "RB", 8.0))

    review = va.compile_review(history, actuals)

    assert review["latest_week"] == 1
    wk = review["weekly"]["1"]
    assert wk["points"]["n"] == 3
    # Perfect ordering => zero rank error for Vegas.
    assert wk["ranks"]["vegas_rank_mae"] == 0.0
    assert wk["ranks"]["by_position"]["RB"]["vegas_rank_mae"] == 0.0
    # Season pool mirrors the single week here.
    assert review["season"]["n_player_weeks"] == 3
    assert review["season"]["points"]["mae"] is not None


def test_compile_review_head_to_head_prefers_better_source(va):
    # Vegas ranks players correctly; ECR ranks them backwards.
    history = {
        "1": {
            "a": {"pos": "WR", "proj_half_ppr": 25.0, "ecr_overall": 3},
            "b": {"pos": "WR", "proj_half_ppr": 18.0, "ecr_overall": 2},
            "c": {"pos": "WR", "proj_half_ppr": 12.0, "ecr_overall": 1},
        }
    }
    actuals = _actuals(1, ("a", "WR", 26.0), ("b", "WR", 17.0), ("c", "WR", 9.0))

    review = va.compile_review(history, actuals)
    h2h = review["weekly"]["1"]["ranks"]["head_to_head"]
    assert h2h["winner"] == "vegas"
    assert h2h["vegas_better_by"] > 0


def test_compile_review_skips_players_without_actuals(va):
    history = {
        "1": {
            "a": {"pos": "RB", "proj_half_ppr": 20.0},
            "dnp": {"pos": "RB", "proj_half_ppr": 12.0},
        }
    }
    # Only "a" recorded a score; "dnp" was inactive.
    actuals = _actuals(1, ("a", "RB", 18.0))
    review = va.compile_review(history, actuals)
    assert review["weekly"]["1"]["points"]["n"] == 1


def test_compile_review_respects_upto_week(va):
    history = {
        "1": {"a": {"pos": "RB", "proj_half_ppr": 20.0}},
        "2": {"a": {"pos": "RB", "proj_half_ppr": 21.0}},
    }
    actuals = {
        "a": {
            "fantasy_positions": ["RB"],
            "scoring_data_weekly": {"1": {"half_ppr": 18.0}, "2": {"half_ppr": 19.0}},
        }
    }
    review = va.compile_review(history, actuals, upto_week=1)
    assert review["weeks"] == [1]
    assert "2" not in review["weekly"]
