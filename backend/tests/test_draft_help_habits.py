"""Tests for draft_help.draft_fetch (normalization) and draft_help.habits."""
from __future__ import annotations

import pytest

from app.services.draft_help.draft_fetch import (
    NormalizedDraft,
    NormalizedPick,
    infer_league_config,
    normalize_draft,
    normalize_pick,
)
from app.services.draft_help import habits
from app.services.draft_help.rankings_source import RankingPlayer


# ---------------------------------------------------------------------------
# draft_fetch.normalize_pick / normalize_draft
# ---------------------------------------------------------------------------
def test_normalize_pick_auction_and_name():
    raw = {
        "pick_no": 5,
        "round": 1,
        "draft_slot": 5,
        "roster_id": 3,
        "picked_by": "user_abc",
        "player_id": "4034",
        "metadata": {"first_name": "Christian", "last_name": "McCaffrey",
                     "position": "RB", "amount": "58"},
    }
    p = normalize_pick(raw)
    assert p.name == "Christian McCaffrey"
    assert p.position == "RB"
    assert p.amount == 58
    assert p.user_id == "user_abc"
    assert p.player_id == "4034"


def test_normalize_pick_snake_has_no_amount():
    p = normalize_pick({"pick_no": 1, "round": 1, "player_id": "1",
                        "metadata": {"first_name": "Josh", "last_name": "Allen"}})
    assert p.amount is None
    assert p.name == "Josh Allen"


def test_normalize_draft_shape():
    detail = {
        "draft_id": "d1",
        "league_id": "L1",
        "season": "2024",
        "type": "auction",
        "status": "complete",
        "settings": {"teams": 12, "rounds": 16},
        "metadata": {"scoring_type": "ppr"},
        "slot_to_roster_id": {"1": 4, "2": 7},
    }
    picks = [{"pick_no": 1, "round": 1, "player_id": "1",
              "metadata": {"first_name": "A", "last_name": "B", "position": "QB", "amount": "10"}}]
    d = normalize_draft(detail, picks)
    assert d.is_auction is True
    assert d.teams == 12 and d.rounds == 16
    assert d.slot_to_roster_id == {1: 4, 2: 7}
    assert len(d.picks) == 1 and d.picks[0].amount == 10


def test_normalize_draft_dynasty_flag():
    detail = {
        "draft_id": "d1", "league_id": "L1", "season": "2025",
        "type": "linear", "status": "complete",
        "settings": {"teams": 12, "rounds": 3},
        "metadata": {"scoring_type": "dynasty_2qb"},
        "slot_to_roster_id": {},
    }
    d = normalize_draft(detail, [])
    assert d.scoring_type == "dynasty_2qb"
    assert d.is_dynasty is True
    # A redraft draft is not flagged.
    detail["metadata"] = {"scoring_type": "half_ppr"}
    assert normalize_draft(detail, []).is_dynasty is False


@pytest.mark.parametrize(
    "rec,expected_ppr",
    [(0.0, 0.0), (0.5, 0.5), (1.0, 1.0), (0.1, 0.0), (0.6, 0.5)],
)
def test_infer_league_config_ppr_buckets(rec, expected_ppr):
    league = {"total_rosters": 12, "scoring_settings": {"rec": rec},
              "roster_positions": ["QB", "RB", "WR", "TE", "FLEX"]}
    cfg = infer_league_config(league)
    assert cfg["ppr"] == expected_ppr
    assert cfg["teams"] == 12
    assert cfg["superflex"] is False


@pytest.mark.parametrize("roster_positions", [
    ["QB", "QB", "RB", "WR"],          # two dedicated QB slots
    ["QB", "RB", "WR", "SUPER_FLEX"],  # explicit superflex slot
])
def test_infer_league_config_detects_superflex(roster_positions):
    league = {"total_rosters": 10, "scoring_settings": {"rec": 1.0},
              "roster_positions": roster_positions}
    assert infer_league_config(league)["superflex"] is True


# ---------------------------------------------------------------------------
# habits helpers
# ---------------------------------------------------------------------------
def _pick(pick_no, pos, pid, *, rnd=None, user="u1", amount=None, name=None):
    return NormalizedPick(
        pick_no=pick_no,
        round=rnd if rnd is not None else ((pick_no - 1) // 12 + 1),
        draft_slot=None,
        player_id=pid,
        name=name or f"P{pid}",
        position=pos,
        user_id=user,
        amount=amount,
    )


def test_skill_picks_excludes_k_def_and_missing_pid():
    picks = [_pick(1, "RB", "1"), _pick(2, "K", "2"), _pick(3, "DEF", "KC"),
             NormalizedPick(4, 1, None, None, position="WR")]
    out = habits.skill_picks(picks)
    assert [p.player_id for p in out] == ["1"]


def test_position_by_round_and_archetype():
    picks = [_pick(1, "RB", "1", rnd=1), _pick(2, "WR", "2", rnd=1),
             _pick(13, "WR", "3", rnd=2), _pick(25, "QB", "4", rnd=3)]
    pbr = habits.position_by_round(picks)
    assert pbr[1] == {"RB": 1, "WR": 1}
    assert pbr[2] == {"WR": 1}
    assert habits.draft_archetype(picks) == "hero_rb"  # exactly one RB in first 3


def test_archetype_zero_rb_and_rb_heavy():
    zero = [_pick(1, "WR", "1", rnd=1), _pick(2, "WR", "2", rnd=2), _pick(3, "QB", "3", rnd=3)]
    assert habits.draft_archetype(zero) == "zero_rb"
    heavy = [_pick(1, "RB", "1", rnd=1), _pick(2, "RB", "2", rnd=2), _pick(3, "RB", "3", rnd=3)]
    assert habits.draft_archetype(heavy) == "rb_heavy"


def test_position_off_board_and_nth():
    picks = [_pick(1, "QB", "1"), _pick(5, "QB", "2"), _pick(9, "RB", "3")]
    board = habits.position_off_board(picks)
    assert board["QB"] == [1, 5]
    assert habits.nth_off_board(picks, "QB", 2) == 5
    assert habits.nth_off_board(picks, "QB", 3) is None


def test_detect_runs():
    # QBs at 10,11,13 form a run (gap<=4, len>=3); a lone QB at 40 does not.
    picks = [_pick(10, "QB", "1"), _pick(11, "QB", "2"), _pick(13, "QB", "3"),
             _pick(40, "QB", "4")]
    runs = habits.detect_runs(picks, "QB", gap=4, min_len=3)
    assert len(runs) == 1
    assert runs[0]["start_pick"] == 10 and runs[0]["end_pick"] == 13 and runs[0]["count"] == 3


def test_reach_summary_vbd_value_vs_reach():
    # Board VBD curve: rank1=120 (elite) .. rank5=90 .. rank10=70 .. rank20=40.
    value = {
        "elite": RankingPlayer(player_id="elite", name="Elite", pos="RB", overall_rank=1, vbd=120.0),
        "mid": RankingPlayer(player_id="mid", name="Mid", pos="WR", overall_rank=5, vbd=90.0),
        "late": RankingPlayer(player_id="late", name="Late", pos="RB", overall_rank=10, vbd=70.0),
        "deep": RankingPlayer(player_id="deep", name="Deep", pos="WR", overall_rank=20, vbd=40.0),
    }
    # Steal: the elite (vbd 120) falls to pick 10 -> par@10 = rank10 vbd 70 -> +50.
    # Reach: a deep WR (vbd 40) taken at pick 5 -> par@5 = rank5 vbd 90 -> -50.
    picks = [_pick(10, "RB", "elite"), _pick(5, "WR", "deep")]
    s = habits.reach_summary(picks, value)
    assert s["picks_evaluated"] == 2
    assert s["avg_vbd_delta"] == pytest.approx(0.0)
    assert s["biggest_value"]["player_id"] == "elite"
    assert s["biggest_value"]["vbd_delta"] == pytest.approx(50.0)
    assert s["biggest_reach"]["player_id"] == "deep"
    assert s["biggest_reach"]["vbd_delta"] == pytest.approx(-50.0)


def test_reach_scales_with_vbd_and_ignores_late_picks():
    # Same 9-rank gap, very different VBD cost early vs late + a hard cutoff.
    value = {
        "r1": RankingPlayer(player_id="r1", name="R1", pos="RB", overall_rank=1, vbd=180.0),
        "r9": RankingPlayer(player_id="r9", name="R9", pos="WR", overall_rank=9, vbd=99.0),
        "r50": RankingPlayer(player_id="r50", name="R50", pos="RB", overall_rank=50, vbd=45.0),
        "r59": RankingPlayer(player_id="r59", name="R59", pos="WR", overall_rank=59, vbd=39.0),
        "r200": RankingPlayer(player_id="r200", name="R200", pos="WR", overall_rank=200, vbd=5.0),
    }
    # rank9 @ pick1 is a massive reach (-81); rank59 @ pick50 barely registers (-6).
    early = habits.reach_value_entries([_pick(1, "WR", "r9")], value)
    late = habits.reach_value_entries([_pick(50, "WR", "r59")], value)
    assert early[0]["vbd_delta"] == pytest.approx(-81.0)
    assert late[0]["vbd_delta"] == pytest.approx(-6.0)
    assert abs(early[0]["vbd_delta"]) > 10 * abs(late[0]["vbd_delta"])
    # A pick past the cutoff (pick 120) is ignored entirely.
    assert habits.reach_value_entries([_pick(120, "WR", "r200")], value) == []


def test_auction_spend_summary_stars_and_scrubs():
    picks = [_pick(1, "RB", "1", amount=80, user="u1"),
             _pick(2, "WR", "2", amount=70, user="u1"),
             _pick(3, "QB", "3", amount=2, user="u1"),
             _pick(4, "TE", "4", amount=1, user="u1")]
    s = habits.auction_spend_summary(picks, budget=200)
    assert s["total_spent"] == 153
    assert s["by_position"] == {"RB": 80, "WR": 70, "QB": 2, "TE": 1}
    assert s["max_bid"] == 80
    assert s["max_bid_pct_budget"] == pytest.approx(0.4)
    # top two (80+70) / 153
    assert s["stars_and_scrubs_index"] == pytest.approx(150 / 153, abs=1e-3)


def test_auction_inflation_curve_and_crash():
    # 6 WRs: first three +20% inflation, next three -20% (market crash).
    value = {str(i): RankingPlayer(player_id=str(i), name=f"W{i}", pos="WR",
                                   auction=50.0) for i in range(1, 7)}
    picks = [
        _pick(1, "WR", "1", amount=60), _pick(2, "WR", "2", amount=60),
        _pick(3, "WR", "3", amount=60), _pick(4, "WR", "4", amount=40),
        _pick(5, "WR", "5", amount=40), _pick(6, "WR", "6", amount=40),
    ]
    curve = habits.auction_inflation_curve(picks, value, "WR")
    assert len(curve) == 6
    assert curve[0]["inflation_pct"] == pytest.approx(20.0)
    crash = habits.detect_market_crash(picks, value, "WR", window=3, min_before=3)
    assert crash is not None
    assert crash["crash_after"] == 3
    assert crash["avg_inflation_before"] > 0 > crash["avg_inflation_after"]


def test_market_status_always_returns_and_flags_wr_crash():
    value = {str(i): RankingPlayer(player_id=str(i), name=f"W{i}", pos="WR", auction=50.0)
             for i in range(1, 7)}
    picks = [
        _pick(1, "WR", "1", amount=60), _pick(2, "WR", "2", amount=60),
        _pick(3, "WR", "3", amount=60), _pick(4, "WR", "4", amount=40),
        _pick(5, "WR", "5", amount=40), _pick(6, "WR", "6", amount=40),
    ]
    st = habits.market_status(picks, value, "WR")
    assert st["position"] == "WR" and st["buys_analyzed"] == 6
    assert st["crashed"] is True and st["crash_after"] == 3
    assert st["early_inflation"] > 0 > st["late_inflation"]
    # Always returns a verdict, even for a position nobody bought.
    empty = habits.market_status(picks, value, "TE")
    assert empty["buys_analyzed"] == 0 and empty["crashed"] is False


def test_elite_market_hot_start():
    value = {str(i): RankingPlayer(player_id=str(i), name=f"S{i}", pos="RB", auction=50.0)
             for i in range(1, 7)}
    # First three elites bought hot (+40%), last three cool off (-40%).
    picks = [
        _pick(1, "RB", "1", amount=70), _pick(2, "RB", "2", amount=70),
        _pick(3, "RB", "3", amount=70), _pick(4, "RB", "4", amount=30),
        _pick(5, "RB", "5", amount=30), _pick(6, "RB", "6", amount=30),
    ]
    em = habits.elite_market_curve(picks, value)
    assert em is not None and em["pattern"] == "hot_start"
    assert em["early_inflation"] > 0 > em["late_inflation"]


def test_favorite_players_across_drafts():
    d1 = [_pick(1, "RB", "1"), _pick(2, "WR", "2")]
    d2 = [_pick(1, "RB", "1"), _pick(2, "WR", "9")]
    d3 = [_pick(1, "RB", "1")]
    favs = habits.favorite_players([d1, d2, d3], min_count=2)
    assert len(favs) == 1
    assert favs[0]["player_id"] == "1" and favs[0]["count"] == 3


def test_summarize_snake_and_auction_smoke():
    value = {"1": RankingPlayer(player_id="1", name="A", pos="RB", overall_rank=3, auction=40.0)}
    snake = habits.summarize_snake([[_pick(1, "RB", "1", rnd=1)]], value)
    assert snake["draft_type"] == "snake" and snake["drafts_counted"] == 1
    auction = habits.summarize_auction([[_pick(1, "RB", "1", amount=40)]], value, budget=200)
    assert auction["draft_type"] == "auction"
    assert auction["avg_spend_by_position"].get("RB") == 40
