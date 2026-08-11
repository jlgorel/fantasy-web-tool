"""Smoke test for the draft-approach proof harness (tools/draft_proof.py).

Keeps the permanent proof *reproducible/wired-up* -- it verifies the harness
imports, a single tiny draft runs offline from fixtures, the grade is the raw
currency-independent starting-lineup points, and the summary/writers round-trip.

It deliberately does NOT re-establish the statistical "MC beats greedy/ADP by X"
claim -- that needs hundreds of drafts and lives in the committed full-sweep
artifact (tools/draft_proof_output/summary.json), not in the test suite. So this
runs one 8-team draft at nsims=2 and stays well under a second of sim work.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("USE_FIXTURE_BLOBS", "1")
_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO / "tools"))

draft_proof = pytest.importorskip("draft_proof")


def test_lineup_points_grade_is_currency_independent():
    """The grade sums raw fpts of the optimal lineup, ignoring VBD/flex_proj."""
    players = draft_proof.get_players(2024, 12, draft_proof.PPR, False)
    slots = draft_proof.slots_for(False)
    pts = draft_proof.lineup_points(players[:20], slots)
    assert pts > 0
    # Adding more players can only help (optimal lineup is monotonic in roster).
    assert draft_proof.lineup_points(players[:25], slots) >= pts


def test_harness_runs_and_summary_round_trips(tmp_path):
    # One cheap draft: smallest league, minimal rollouts. We only assert it
    # produces sane, positive lineup points and that aggregation/writers work --
    # not the (noisy at n=1) margin sign.
    row = draft_proof.simulate((1234, 2, 2024, 8, False))
    year, teams, sf, mc_pts, greedy_pts, adp_pts = row
    assert (year, teams, sf) == (2024, 8, 0)
    assert mc_pts > 0 and greedy_pts > 0 and adp_pts > 0

    results = [row]
    summary = draft_proof.build_summary(results, {"total_drafts": 1})
    for key in ("overall", "by_season", "by_size", "by_format", "by_cell"):
        assert key in summary
    assert summary["overall"]["n"] == 1
    assert summary["by_cell"], "expected the one populated per-cell row"

    csv_path = tmp_path / "results.csv"
    json_path = tmp_path / "summary.json"
    draft_proof.write_csv(results, csv_path)
    draft_proof.write_json(summary, json_path)
    assert csv_path.exists() and "mc_points" in csv_path.read_text(encoding="utf-8")
    assert json_path.exists() and '"overall"' in json_path.read_text(encoding="utf-8")
