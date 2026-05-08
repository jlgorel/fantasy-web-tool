"""Tests for ``app.services.wrapped.draft``."""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Dict, List

import pytest

import app.services.wrapped.draft as draft_mod
from app.services.wrapped.draft import (
    DraftPick,
    build_picks,
    calculate_draft_accolades,
    compute_value_over_slot,
)


def _ctx(roster_to_user: Dict[int, str]) -> SimpleNamespace:
    """Minimal LeagueContext duck for build_picks."""
    return SimpleNamespace(
        league_id="L",
        roster_id_to_username=roster_to_user,
        qb_score_key="std",
        skill_score_key="half_ppr",
    )


def _scoring(rows: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Build season_scoring dict from compact tuples."""
    out: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        out[r["pid"]] = {
            "full_name": r.get("name") or r["pid"],
            "fantasy_positions": [r["pos"]],
            "scoring_data_season": {
                f"{r['key']}_points": r["points"],
            },
        }
    return out


# ---------------------------------------------------------------------------
# build_picks + compute_value_over_slot
# ---------------------------------------------------------------------------
class TestBuildAndScorePicks:
    def test_value_over_slot_orders_by_actual_vs_drafted(self):
        # Two RBs: drafted in order [A, B], finished in order [B, A].
        # A: drafted_pos_rank=1, actual_pos_rank=2, vos = 1-2 = -1 (bust)
        # B: drafted_pos_rank=2, actual_pos_rank=1, vos = 2-1 = +1 (steal)
        picks = [
            DraftPick(pick_no=1, round=1, player_id="A", username="u1",
                      position="RB", season_points=100),
            DraftPick(pick_no=2, round=1, player_id="B", username="u2",
                      position="RB", season_points=200),
        ]
        compute_value_over_slot(picks)
        a, b = picks
        assert a.drafted_pos_rank == 1 and a.actual_pos_rank == 2
        assert a.value_over_slot == -1
        assert b.drafted_pos_rank == 2 and b.actual_pos_rank == 1
        assert b.value_over_slot == 1

    def test_picks_at_different_positions_dont_interact(self):
        picks = [
            DraftPick(pick_no=1, round=1, player_id="A", username="u1",
                      position="QB", season_points=300),
            DraftPick(pick_no=2, round=1, player_id="B", username="u2",
                      position="RB", season_points=10),
        ]
        compute_value_over_slot(picks)
        # Each is the only player at their position -> rank 1 in both axes.
        assert picks[0].value_over_slot == 0
        assert picks[1].value_over_slot == 0

    def test_build_picks_uses_qb_score_key_for_qbs(self):
        ctx = _ctx({1: "alice"})
        scoring = {
            "QB1": {
                "full_name": "Q",
                "fantasy_positions": ["QB"],
                "scoring_data_season": {"std_points": 250, "half_ppr_points": 999},
            }
        }
        raw = [{"player_id": "QB1", "roster_id": 1, "pick_no": 1, "round": 1}]
        picks = build_picks(raw, ctx, scoring)
        # Should pull std_points (250), not half_ppr_points (999).
        assert picks[0].season_points == 250

    def test_build_picks_skips_unmappable_rosters(self):
        ctx = _ctx({1: "alice"})
        raw = [{"player_id": "X", "roster_id": 99, "pick_no": 1, "round": 1}]
        assert build_picks(raw, ctx, {}) == []

    def test_build_picks_falls_back_to_players_meta_for_position(self):
        """Player missing from year-specific scoring blob should still get
        his position resolved via players.json so he doesn't get bucketed
        into ``UNK``. This is the common case for early-round busts who
        miss enough of the season to fall outside the top-N scorer cutoff."""
        ctx = _ctx({1: "alice"})
        scoring: Dict[str, Any] = {}  # no year-specific data for this player
        players_meta = {
            "RB99": {
                "full_name": "Busted Back",
                "fantasy_positions": ["RB"],
            }
        }
        raw = [{"player_id": "RB99", "roster_id": 1, "pick_no": 5, "round": 1}]
        picks = build_picks(raw, ctx, scoring, players_meta)
        assert len(picks) == 1
        # Position resolved from players.json fallback, not "UNK".
        assert picks[0].position == "RB"
        # Missing scoring -> 0 pts, which is what we want (sorts to bottom).
        assert picks[0].season_points == 0.0

    def test_resolve_position_prefers_season_scoring_for_taysom_hill(self):
        """Even if players.json lists Taysom as QB, the Taysom-Hill->TE
        override should still apply when the season scoring blob carries
        the correct full_name."""
        ctx = _ctx({1: "alice"})
        scoring = {
            "TH": {
                "full_name": "Taysom Hill",
                "fantasy_positions": ["QB", "TE"],
                "scoring_data_season": {"half_ppr_points": 100},
            }
        }
        players_meta = {"TH": {"full_name": "Taysom Hill",
                               "fantasy_positions": ["QB"]}}
        raw = [{"player_id": "TH", "roster_id": 1, "pick_no": 1, "round": 1}]
        picks = build_picks(raw, ctx, scoring, players_meta)
        assert picks[0].position == "TE"


# ---------------------------------------------------------------------------
# calculate_draft_accolades
# ---------------------------------------------------------------------------
class TestCalculateDraftAccolades:
    def _picks(self):
        return [
            # Manager A: hit on B (drafted WR3, finished WR1), missed on A (drafted RB1, finished RB3)
            DraftPick(pick_no=1, round=1, player_id="A", username="alice",
                      position="RB", season_points=10),
            DraftPick(pick_no=20, round=2, player_id="B", username="alice",
                      position="WR", season_points=400),
            # Manager B
            DraftPick(pick_no=2, round=1, player_id="C", username="bob",
                      position="RB", season_points=200),
            DraftPick(pick_no=3, round=1, player_id="E", username="bob",
                      position="RB", season_points=150),
            DraftPick(pick_no=10, round=1, player_id="D", username="bob",
                      position="WR", season_points=200),
            DraftPick(pick_no=15, round=2, player_id="F", username="bob",
                      position="WR", season_points=100),
        ]

    def test_per_user_best_and_worst(self):
        picks = self._picks()
        compute_value_over_slot(picks)
        out = calculate_draft_accolades(picks)
        assert set(out.by_user.keys()) == {"alice", "bob"}
        assert out.by_user["alice"]["num_picks"] == 2
        # alice: A has vos=-1 (bust), B has vos=+1 (steal)
        assert out.by_user["alice"]["best_pick"]["player_id"] == "B"
        assert out.by_user["alice"]["worst_pick"]["player_id"] == "A"

    def test_overall_biggest_steal_and_bust(self):
        picks = self._picks()
        compute_value_over_slot(picks)
        out = calculate_draft_accolades(picks)
        # D was drafted as WR2 but finished as WR1 -> +1 vos (steal)
        # B was drafted as WR1 but finished as WR2 -> -1 vos (bust)
        # Both alice's A (RB1->RB2) and bob's C (RB2->RB1) are also +/-1.
        assert out.biggest_steal is not None
        assert out.biggest_bust is not None
        assert out.biggest_steal["value_over_slot"] >= out.biggest_bust["value_over_slot"]

    def test_mr_irrelevant_hero(self):
        picks = self._picks()
        compute_value_over_slot(picks)
        out = calculate_draft_accolades(picks, irrelevant_top_n=24)
        # All picks are top-3 at their position so all qualify; the latest
        # by pick_no is B (pick 20).
        assert out.mr_irrelevant_hero is not None
        assert out.mr_irrelevant_hero["player_id"] == "B"
        assert out.mr_irrelevant_hero["username"] == "alice"

    def test_empty_picks(self):
        out = calculate_draft_accolades([])
        assert out.by_user == {}
        assert out.biggest_steal is None
        assert out.biggest_bust is None
        assert out.mr_irrelevant_hero is None


# ---------------------------------------------------------------------------
# fetch_and_compute_draft (network surface)
# ---------------------------------------------------------------------------
class TestFetchAndCompute:
    def test_empty_drafts_returns_empty_accolades(self, monkeypatch):
        monkeypatch.setattr(draft_mod, "fetch_json", lambda url, **_: [])
        ctx = SimpleNamespace(
            league_id="L", roster_id_to_username={}, qb_score_key="std",
            skill_score_key="half_ppr",
        )
        out = draft_mod.fetch_and_compute_draft(ctx, {})
        assert out.by_user == {}

    def test_fetch_failure_returns_empty(self, monkeypatch):
        def boom(url: str, **_: Any) -> Any:
            raise RuntimeError("network down")
        monkeypatch.setattr(draft_mod, "fetch_json", boom)
        ctx = SimpleNamespace(
            league_id="L", roster_id_to_username={}, qb_score_key="std",
            skill_score_key="half_ppr",
        )
        out = draft_mod.fetch_and_compute_draft(ctx, {})
        assert out.by_user == {}
