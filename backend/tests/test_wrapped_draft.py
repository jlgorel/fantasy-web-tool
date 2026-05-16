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
        # Four RBs so the rank-delta is meaningful. Drafted in order
        # [A, B, C, D]; finished by points desc -> [B, C, D, A].
        # A: drafted_pos_rank=1, actual_pos_rank=4 -> (1/2 - 1)*100 = -50
        # B: drafted_pos_rank=2, actual_pos_rank=1 -> (1 - 1/sqrt2)*100 ~ +29.29
        #
        # Note: A is NOT a bust under the new rules (RB4 is still
        # comfortably inside the startable tier). VOS math is the only
        # thing this test exercises.
        picks = [
            DraftPick(pick_no=1, round=1, player_id="A", username="u1",
                      position="RB", season_points=10),
            DraftPick(pick_no=2, round=1, player_id="B", username="u2",
                      position="RB", season_points=400),
            DraftPick(pick_no=3, round=1, player_id="C", username="u3",
                      position="RB", season_points=300),
            DraftPick(pick_no=4, round=1, player_id="D", username="u4",
                      position="RB", season_points=200),
        ]
        compute_value_over_slot(picks)
        a, b = picks[0], picks[1]
        assert a.drafted_pos_rank == 1 and a.actual_pos_rank == 4
        assert a.value_over_slot == pytest.approx(-50.0, abs=0.05)
        assert b.drafted_pos_rank == 2 and b.actual_pos_rank == 1
        assert b.value_over_slot == pytest.approx(29.29, abs=0.05)
        # RB4 is still startable -> NOT a bust under the new rules.
        assert a.bust_score == 0.0
        assert b.bust_score == 0.0

    def test_positional_value_curve_favors_top_tier_movement(self):
        """Cook-style: an RB drafted RB18 who finishes RB5 should score
        a bigger steal than a WR drafted WR90 who finishes WR40, even
        though the raw rank delta is much smaller (13 vs 50). The
        inverse-sqrt curve captures the elite-tier value cliff."""
        # Construct N RBs where RB#18 (drafted slot 18) ends up the 5th
        # best scorer at the position. Trick: assign points so the order
        # is RB1..RB4, then RB18, then everyone else by index.
        rbs = []
        for i in range(1, 19):
            if i <= 4:
                pts = 1000 - i  # top-4 get the best points
            elif i == 18:
                pts = 900  # 5th best
            else:
                pts = 100 - i  # below the top 5
            rbs.append(DraftPick(pick_no=i, round=1, player_id=f"RB{i}",
                                 username="u", position="RB",
                                 season_points=pts))
        # 90 WRs where WR#90 finishes 40th: top-39 get best points, WR90
        # next, rest below.
        wrs = []
        for i in range(1, 91):
            if i <= 39:
                pts = 2000 - i  # top-39 best
            elif i == 90:
                pts = 1900  # 40th best
            else:
                pts = 50 - i  # below the top 40
            wrs.append(DraftPick(pick_no=100 + i, round=10, player_id=f"WR{i}",
                                 username="u", position="WR",
                                 season_points=pts))

        compute_value_over_slot(rbs + wrs)
        cook = next(p for p in rbs if p.player_id == "RB18")
        late_wr = next(p for p in wrs if p.player_id == "WR90")
        assert cook.actual_pos_rank == 5
        assert cook.drafted_pos_rank == 18
        assert late_wr.actual_pos_rank == 40
        assert late_wr.drafted_pos_rank == 90
        # The whole point: Cook's value_over_slot beats the deep-round WR.
        assert cook.value_over_slot > late_wr.value_over_slot

    def test_bust_weighted_by_overall_pick_number(self):
        """Two RBs both bust to outside the startable tier; the one
        drafted earlier overall should carry a higher bust score.
        """
        # 35 RBs total. EarlyBust at overall pick #1 (RB1) and
        # LateBust at overall pick #49 (RB30, after 28 RB-filler
        # drafted between them) -- both within drafted_pos_rank<=30.
        # Five more high-scoring RB-filler drafted after LateBust push
        # both busts outside the actual_pos_rank<=30 tier.
        picks = []
        picks.append(DraftPick(pick_no=1, round=1, player_id="EarlyBust",
                               username="u", position="RB", season_points=1))
        for i in range(2, 30):  # 28 RB-filler drafted RB2..RB29
            picks.append(DraftPick(
                pick_no=i, round=1, player_id=f"EarlyFill_{i}",
                username="u", position="RB", season_points=1000 - i,
            ))
        picks.append(DraftPick(pick_no=49, round=5, player_id="LateBust",
                               username="u", position="RB", season_points=2))
        for i in range(50, 56):  # 6 more high-scoring RB-filler
            picks.append(DraftPick(
                pick_no=i, round=5, player_id=f"LateFill_{i}",
                username="u", position="RB", season_points=1000 - i,
            ))
        compute_value_over_slot(picks)
        early = next(p for p in picks if p.player_id == "EarlyBust")
        late = next(p for p in picks if p.player_id == "LateBust")
        assert early.drafted_pos_rank <= 30
        assert late.drafted_pos_rank <= 30
        assert early.actual_pos_rank > 30
        assert late.actual_pos_rank > 30
        assert early.bust_score > 0
        assert late.bust_score > 0
        assert early.bust_score > late.bust_score

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
        """Realistic fixture: enough RBs/WRs to exercise the
        startable-tier filter (RB threshold=30, WR threshold=40).
        Alice owns the bust (RB drafted #1, finishes RB35) and the
        steal (WR drafted WR45, finishes WR5). Bob owns positional
        filler that ranks comfortably inside the startable tier.
        """
        picks = []
        # Alice's two notable picks.
        picks.append(DraftPick(pick_no=1, round=1, player_id="A_bust",
                               username="alice", position="RB",
                               season_points=1))
        picks.append(DraftPick(pick_no=120, round=10, player_id="A_steal",
                               username="alice", position="WR",
                               season_points=800))
        # 34 RB filler: drafted in order, scored high so they fill
        # ranks 1-34 ahead of A_bust.
        for i in range(2, 36):
            picks.append(DraftPick(
                pick_no=i, round=(i // 12) + 1,
                player_id=f"RBfill_{i}", username="bob", position="RB",
                season_points=2000 - i,
            ))
        # 44 WR filler. 4 score above A_steal (top WRs), the rest
        # score below so A_steal lands at WR5.
        for i in range(1, 45):
            if i <= 4:
                pts = 1000 - i
            else:
                pts = 700 - i
            picks.append(DraftPick(
                pick_no=40 + i, round=4,
                player_id=f"WRfill_{i}", username="bob", position="WR",
                season_points=pts,
            ))
        return picks

    def test_per_user_best_and_worst(self):
        picks = self._picks()
        compute_value_over_slot(picks)
        out = calculate_draft_accolades(picks)
        assert set(out.by_user.keys()) == {"alice", "bob"}
        # Alice's two picks: WR steal + RB bust.
        assert out.by_user["alice"]["best_pick"]["player_id"] == "A_steal"
        assert out.by_user["alice"]["worst_pick"]["player_id"] == "A_bust"
        # Bob's filler may include tiny boundary steals/busts (one rank
        # above/below the startable threshold caused by A_steal's
        # insertion at WR5) but never with the magnitude of Alice's
        # headline picks.
        alice_best_vos = out.by_user["alice"]["best_pick"]["value_over_slot"]
        alice_worst_bust = out.by_user["alice"]["worst_pick"]["bust_score"]
        bob_best = out.by_user["bob"]["best_pick"]
        bob_worst = out.by_user["bob"]["worst_pick"]
        if bob_best is not None:
            assert bob_best["value_over_slot"] < alice_best_vos
        if bob_worst is not None:
            assert bob_worst["bust_score"] < alice_worst_bust

    def test_overall_biggest_steal_and_bust(self):
        picks = self._picks()
        compute_value_over_slot(picks)
        out = calculate_draft_accolades(picks)
        assert out.biggest_steal is not None
        assert out.biggest_bust is not None
        assert out.biggest_steal["player_id"] == "A_steal"
        assert out.biggest_bust["player_id"] == "A_bust"
        assert out.biggest_steal["value_over_slot"] > 0
        assert out.biggest_bust["bust_score"] > 0

    def test_kicker_excluded_from_accolades(self):
        """A kicker who massively misses his draft slot must NOT show
        up in steal/bust leaderboards -- K/DEF are excluded outright."""
        # K drafted #20 (would be a "K1") who finishes dead last among
        # kickers, layered on top of the standard fixture.
        picks = self._picks() + [
            DraftPick(pick_no=20, round=2, player_id="K1", username="alice",
                      position="K", season_points=1),
            DraftPick(pick_no=21, round=2, player_id="K2", username="bob",
                      position="K", season_points=200),
        ]
        compute_value_over_slot(picks)
        out = calculate_draft_accolades(picks)
        if out.biggest_bust is not None:
            assert out.biggest_bust["position"] not in {"K", "DEF", "DST"}
        if out.biggest_steal is not None:
            assert out.biggest_steal["position"] not in {"K", "DEF", "DST"}

    def test_mr_irrelevant_hero(self):
        picks = self._picks()
        compute_value_over_slot(picks)
        out = calculate_draft_accolades(picks, irrelevant_top_n=24)
        # A_steal is the latest-picked player (#120) who finished top-24
        # at his position (WR5).
        assert out.mr_irrelevant_hero is not None
        assert out.mr_irrelevant_hero["player_id"] == "A_steal"
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


# ---------------------------------------------------------------------------
# compute_dynasty_value_over_slot
# ---------------------------------------------------------------------------
class TestDynastyValueOverSlot:
    def test_dynasty_ranks_by_ktc_value_not_season_points(self):
        """Dynasty drafts rank picks by current KTC value (overall),
        not by season points within a position. A player drafted #1 with
        a high KTC value is a steal even if he missed his rookie year."""
        # 4 dynasty picks. Player A drafted #1 has the highest KTC, but
        # zero season points (rookie redshirt). Player D drafted #4 has
        # a low KTC. KTC ranks: A=1, B=2, C=3, D=4. Pick order matches.
        picks = [
            DraftPick(pick_no=1, round=1, player_id="A", username="u",
                      position="RB", season_points=0),
            DraftPick(pick_no=2, round=1, player_id="B", username="u",
                      position="WR", season_points=0),
            DraftPick(pick_no=3, round=1, player_id="C", username="u",
                      position="WR", season_points=0),
            DraftPick(pick_no=4, round=1, player_id="D", username="u",
                      position="RB", season_points=0),
        ]
        ktc = {"A": 9000.0, "B": 8000.0, "C": 7000.0, "D": 6000.0}
        draft_mod.compute_dynasty_value_over_slot(picks, ktc)
        # KTC rank matches pick_no => zero VOS, zero bust score for all.
        for p in picks:
            assert p.actual_pos_rank == p.pick_no
            assert p.value_over_slot == pytest.approx(0.0, abs=0.01)
            assert p.bust_score == 0.0

    def test_dynasty_flags_early_pick_with_low_ktc_as_bust(self):
        """The 7th overall pick whose current KTC value is 30th in the
        draft is a huge bust -- matches the user's stated heuristic."""
        # Construct 10 picks: pick #1 has the worst KTC value (rank 10
        # by value), pick #10 has the best KTC value (rank 1).
        picks = [
            DraftPick(pick_no=i, round=1, player_id=f"P{i}", username="u",
                      position="RB", season_points=0)
            for i in range(1, 11)
        ]
        # Mapping: P1 -> lowest KTC (1000), P10 -> highest (10000).
        ktc = {f"P{i}": float(1000 * i) for i in range(1, 11)}
        draft_mod.compute_dynasty_value_over_slot(picks, ktc)
        p1 = picks[0]  # drafted #1, KTC rank 10
        p10 = picks[9]  # drafted #10, KTC rank 1
        # P1: actual_rank=10, drafted=1 -> bust
        assert p1.actual_pos_rank == 10
        assert p1.value_over_slot < 0
        assert p1.bust_score > 0
        # P10: actual_rank=1, drafted=10 -> steal
        assert p10.actual_pos_rank == 1
        assert p10.value_over_slot > 0
        assert p10.bust_score == 0.0

    def test_dynasty_ignores_position_buckets(self):
        """Dynasty path collapses all positions into one ranking, unlike
        the redraft path which buckets by fantasy position."""
        picks = [
            # RB drafted #1, KTC ranks below the WR.
            DraftPick(pick_no=1, round=1, player_id="A", username="u",
                      position="RB", season_points=300),
            # WR drafted #2, much higher KTC.
            DraftPick(pick_no=2, round=1, player_id="B", username="u",
                      position="WR", season_points=10),
        ]
        ktc = {"A": 3000.0, "B": 9000.0}
        draft_mod.compute_dynasty_value_over_slot(picks, ktc)
        # B has higher KTC -> ranks 1 overall. A ranks 2.
        a, b = picks
        assert b.actual_pos_rank == 1
        assert a.actual_pos_rank == 2
        # Note: A drafted=1, actual=2 -> tiny delta, NOT a bust
        # (rank_delta=1 is below the threshold).
        assert a.bust_score == 0.0
