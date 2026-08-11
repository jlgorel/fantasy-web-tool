"""Tests for the Monte-Carlo draft sim engine (draft_help.sim)."""
from __future__ import annotations

import pytest

from app.services.draft_help import sim
from app.services.draft_help.sim import (
    SimPlayer,
    default_starting_slots,
    lineup_value,
    my_upcoming_picks,
    recommend_pick,
    sim_players_from_config_players,
    slots_from_roster_positions,
    snake_slot_for_pick,
)


def _p(pid, pos, proj, adp=None):
    return SimPlayer(player_id=pid, name=f"N{pid}", pos=pos, adp=adp if adp is not None else int(pid), proj=proj)


# ---------------------------------------------------------------------------
# lineup_value
# ---------------------------------------------------------------------------
def test_lineup_value_dedicated_plus_flex():
    roster = [_p("1", "QB", 20), _p("2", "RB", 15), _p("3", "RB", 12),
              _p("4", "WR", 10), _p("5", "WR", 8), _p("6", "TE", 5),
              _p("7", "RB", 11)]  # extra RB -> FLEX
    slots = {"QB": 1, "RB": 2, "WR": 2, "TE": 1, "FLEX": 1}
    # 20 + (15+12) + (10+8) + 5 + flex best leftover (RB 11) = 81
    assert lineup_value(roster, slots) == pytest.approx(81)


def test_lineup_value_superflex_uses_extra_qb():
    roster = [_p("1", "QB", 20), _p("2", "QB", 18), _p("3", "RB", 15), _p("4", "WR", 10)]
    slots = {"QB": 1, "RB": 1, "WR": 1, "SUPER_FLEX": 1}
    # QB20 + RB15 + WR10 + SF best leftover (QB18) = 63
    assert lineup_value(roster, slots) == pytest.approx(63)


def test_lineup_value_missing_positions_partial():
    roster = [_p("1", "RB", 15)]
    slots = {"QB": 1, "RB": 2, "WR": 2, "TE": 1, "FLEX": 1}
    assert lineup_value(roster, slots) == pytest.approx(15)


# ---------------------------------------------------------------------------
# FLEX scored by points, not positional VBD: once your TE slot is filled, a 2nd
# TE competes for the FLEX on raw points and shouldn't out-punch a higher-
# scoring WR/RB just because TE's own replacement baseline is low.
# ---------------------------------------------------------------------------
def _row(pid, pos, fpts, vbd, adp=None):
    return {"player_id": pid, "name": pid, "pos": pos,
            "fpts": fpts, "vbd": vbd, "adp": adp, "overall_rank": None}


def test_flex_slot_scored_by_points_not_positional_vbd():
    slots = {"TE": 1, "FLEX": 1}
    te_starter = SimPlayer(player_id="teA", name="TE A", pos="TE", adp=1.0, proj=15.0, fpts=15.0, flex_proj=12.0)
    te_second = SimPlayer(player_id="teB", name="TE B", pos="TE", adp=2.0, proj=9.0, fpts=9.0, flex_proj=4.0)
    wr_flex = SimPlayer(player_id="wrC", name="WR C", pos="WR", adp=3.0, proj=7.0, fpts=7.0, flex_proj=6.0)
    roster = [te_starter, te_second, wr_flex]
    # TE slot = teA (15); FLEX by points -> WR (flex_proj 6) over 2nd TE (flex_proj 4).
    assert lineup_value(roster, slots) == pytest.approx(15 + 6)
    # Scored by VBD it would wrongly take the 2nd TE (proj 9).
    assert lineup_value(roster, slots) < 15 + 9


def test_flex_falls_back_to_vbd_without_fpts():
    # Players built without flex_proj (synthetic rosters/tests) keep the old rule.
    slots = {"TE": 1, "FLEX": 1}
    roster = [_p("teA", "TE", 15, adp=1), _p("teB", "TE", 9, adp=2), _p("wrC", "WR", 7, adp=3)]
    assert lineup_value(roster, slots) == pytest.approx(15 + 9)  # flex takes higher VBD


def test_config_players_flex_proj_only_deinflates_te_guest():
    rows = [
        _row("rb1", "RB", 180.0, 80.0), _row("rb2", "RB", 150.0, 50.0),  # RB baseline 100
        _row("wr1", "WR", 190.0, 80.0), _row("wr2", "WR", 160.0, 50.0),  # WR baseline 110
        _row("te1", "TE", 130.0, 60.0),  # TE baseline 70, but flex uses RB/WR level
    ]
    players = {p.player_id: p for p in sim_players_from_config_players(rows)}
    # flex_baseline = max(RB 100, WR 110) = 110. Only the TE guest is de-inflated.
    te = players["te1"]
    assert te.proj == pytest.approx(60.0)                 # dedicated TE keeps full VBD
    assert te.flex_proj == pytest.approx(130.0 - 110.0)   # TE flex value = points over 110
    assert te.flex_proj < te.proj                         # de-inflated in the flex
    # RB/WR keep their own VBD in the flex (flex_proj None -> _fill_lineup uses proj).
    assert players["wr2"].flex_proj is None
    assert players["rb2"].flex_proj is None


def test_recommend_pick_does_not_stack_second_te_over_higher_scoring_wr():
    rows = [
        _row("rb1", "RB", 180.0, 80.0), _row("rb2", "RB", 150.0, 50.0),
        _row("wr1", "WR", 175.0, 75.0),                    # baseline setters (all 100)
        _row("teA", "TE", 180.0, 110.0, adp=1.0),          # my elite starting TE
        _row("teB", "TE", 130.0, 60.0, adp=2.0),           # 2nd TE: higher VBD, fewer points
        _row("wrC", "WR", 145.0, 45.0, adp=3.0),           # WR: lower VBD, more points
    ]
    players = sim_players_from_config_players(rows)
    slots = {"TE": 1, "FLEX": 1}
    # rounds=1 -> a single upcoming pick, so the rec is a pure flex-fill decision.
    out = recommend_pick(
        players, drafted_ids=["teA", "rb1", "rb2", "wr1"], my_roster_ids=["teA"],
        teams=12, rounds=1, my_slot=1, slots=slots, current_pick=1,
        n_sims=5, top_k=6, seed=1,
    )
    assert out["recommendation"]["player_id"] == "wrC"
    cand = {c["player_id"]: c for c in out["candidates"]}
    assert cand["wrC"]["avg_lineup"] > cand["teB"]["avg_lineup"]


# ---------------------------------------------------------------------------
# snake order
# ---------------------------------------------------------------------------
def test_snake_slot_for_pick_12team():
    assert snake_slot_for_pick(1, 12) == 1
    assert snake_slot_for_pick(12, 12) == 12
    assert snake_slot_for_pick(13, 12) == 12  # round 2 reverses
    assert snake_slot_for_pick(24, 12) == 1
    assert snake_slot_for_pick(25, 12) == 1   # round 3 forward again


def test_my_upcoming_picks_snake():
    # 12-team, slot 3 -> picks 3, 22, 27, 46, ...
    picks = my_upcoming_picks(1, 12, 4, 3)
    assert picks[:4] == [3, 22, 27, 46]


# ---------------------------------------------------------------------------
# slots derivation
# ---------------------------------------------------------------------------
def test_default_starting_slots():
    assert default_starting_slots(False) == {"QB": 1, "RB": 2, "WR": 2, "TE": 1, "FLEX": 1}
    assert default_starting_slots(True)["SUPER_FLEX"] == 1


def test_slots_from_roster_positions_ignores_bench_k_def():
    rp = ["QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "SUPER_FLEX", "K", "DEF", "BN", "BN", "IR"]
    slots = slots_from_roster_positions(rp)
    assert slots == {"QB": 1, "RB": 2, "WR": 2, "TE": 1, "FLEX": 1, "SUPER_FLEX": 1}


# ---------------------------------------------------------------------------
# recommend_pick
# ---------------------------------------------------------------------------
def _universe(n=30):
    # adp == overall_rank; projection decreases with adp, mixed positions.
    positions = ["RB", "WR", "QB", "TE"]
    players = []
    for i in range(1, n + 1):
        players.append(SimPlayer(
            player_id=str(i), name=f"P{i}", pos=positions[i % 4],
            adp=float(i), proj=float(200 - i * 3),
        ))
    return players


def test_recommend_pick_is_deterministic_with_seed():
    players = _universe()
    kwargs = dict(teams=12, rounds=14, my_slot=1, n_sims=30, top_k=5, seed=7)
    r1 = recommend_pick(players, drafted_ids=[], my_roster_ids=[], **kwargs)
    r2 = recommend_pick(players, drafted_ids=[], my_roster_ids=[], **kwargs)
    assert r1 == r2
    assert r1["recommendation"] is not None
    # Recommendation is drawn from the top_k available by ADP.
    assert r1["recommendation"]["player_id"] in {c["player_id"] for c in r1["candidates"]}


def test_recommend_pick_prefers_dominant_player():
    players = _universe()
    # Make player "3" a generational value: huge projection, still top-k by ADP.
    for p in players:
        if p.player_id == "3":
            p.proj = 100000.0
    out = recommend_pick(players, drafted_ids=[], my_roster_ids=[],
                         teams=12, rounds=14, my_slot=1, n_sims=20, top_k=5, seed=1)
    assert out["recommendation"]["player_id"] == "3"


def test_recommend_pick_no_upcoming_returns_empty():
    players = _universe()
    # Draft is over (current_pick beyond board) -> no upcoming picks.
    out = recommend_pick(players, drafted_ids=[], my_roster_ids=[],
                         teams=12, rounds=1, my_slot=1, current_pick=999)
    assert out["recommendation"] is None
    assert out["candidates"] == []


def test_recommend_pick_uses_explicit_future_pick_schedule():
    players = _universe(40)
    out = recommend_pick(
        players, drafted_ids=[], my_roster_ids=[],
        teams=12, rounds=4, my_slot=2, current_pick=8,
        my_future_pick_numbers=[22, 46], n_sims=5, top_k=4, seed=1,
    )
    assert out["my_upcoming_picks"] == [22, 46]


def test_recommend_pick_excludes_drafted():
    players = _universe()
    drafted = {"1", "2", "3"}
    out = recommend_pick(players, drafted_ids=drafted, my_roster_ids=[],
                        teams=12, rounds=14, my_slot=1, n_sims=10, top_k=5, seed=1)
    cand_ids = {c["player_id"] for c in out["candidates"]}
    assert cand_ids.isdisjoint(drafted)


def test_recommend_pick_excludes_avoided_from_user_candidates():
    players = _universe()
    out = recommend_pick(
        players, drafted_ids=[], my_roster_ids=[],
        teams=12, rounds=14, my_slot=1, n_sims=10, top_k=5, seed=1,
        avoid_ids=["1", "2", "3"],
    )
    candidate_ids = {c["player_id"] for c in out["candidates"]}
    assert candidate_ids.isdisjoint({"1", "2", "3"})
    assert out["recommendation"]["player_id"] not in {"1", "2", "3"}


def test_avoided_player_remains_available_to_opponents(monkeypatch):
    players = _universe(12)
    saw_avoided_available = []
    original = sim._draw_opponent_order

    def recording_board(available, rng):
        if any(p.player_id == "2" for p in available):
            saw_avoided_available.append(True)
        return original(available, rng)

    monkeypatch.setattr(sim, "_draw_opponent_order", recording_board)
    recommend_pick(
        players, drafted_ids=[], my_roster_ids=[],
        teams=4, rounds=3, my_slot=1, slots={"RB": 1},
        n_sims=2, top_k=2, show_top=2, seed=2, avoid_ids=["2"],
    )
    # Avoid is a user preference, not a claim that the player vanished from
    # the real board. Opponents must still be able to select that player.
    assert saw_avoided_available


def test_recommend_pick_considers_each_position_but_caps_display():
    # Top of the board is all RB/WR; the only QB sits well outside the top_k.
    players = [
        SimPlayer(player_id=str(i), name=f"{'RB' if i % 2 else 'WR'}{i}",
                  pos="RB" if i % 2 else "WR", adp=float(i), proj=float(120 - i))
        for i in range(1, 25)
    ]
    players.append(SimPlayer(player_id="qb", name="Lone QB", pos="QB", adp=22.0, proj=80.0))
    base = dict(drafted_ids=[], my_roster_ids=[], teams=10, rounds=10, my_slot=1,
                slots={"QB": 1, "RB": 2, "WR": 2, "FLEX": 1}, n_sims=10, top_k=6, seed=1)
    # Considered: with a big show_top the lone QB (ADP 22, past top_k=6) is evaluated.
    full = recommend_pick(players, show_top=50, **base)
    assert "qb" in {c["player_id"] for c in full["candidates"]}
    # Displayed: the default caps shown candidates to the top 5 by value.
    shown = recommend_pick(players, **base)
    assert len(shown["candidates"]) <= 5


def test_manual_priority_player_is_evaluated_even_when_deep_by_adp():
    players = _universe(40)
    target = players[-1]
    target.proj = 180.0
    out = recommend_pick(
        players, drafted_ids=[], my_roster_ids=[],
        teams=12, rounds=14, my_slot=1, n_sims=10, top_k=4,
        show_top=5, seed=1, priority_candidate_ids=[target.player_id],
    )
    assert target.player_id in {
        row["player_id"] for row in out["priority_candidates"]
    }


# ---------------------------------------------------------------------------
# starters-first scoring + depth + likely-next
# ---------------------------------------------------------------------------
def test_lineup_value_is_starters_only_roster_value_adds_discounted_depth():
    slots = {"RB": 1}
    roster = [_p("1", "RB", 20), _p("2", "RB", 10)]
    # VAL counts only the starter; the bench RB never shows up in lineup_value.
    assert lineup_value(roster, slots) == pytest.approx(20)
    full = sim.roster_value(roster, slots)
    # roster_value adds the bench RB at a discount (DEPTH_WEIGHT), not its full value.
    assert full == pytest.approx(20 + 10 * sim.DEPTH_WEIGHT)
    assert 20 < full < 30


def test_depth_only_counts_startable_positions():
    # No RB slot at all -> a spare RB is not "depth" (you can never start it).
    roster = [_p("1", "WR", 30), _p("2", "RB", 25)]
    assert sim.roster_value(roster, {"WR": 1}) == pytest.approx(30)


def test_recommend_pick_returns_likely_next_and_depth():
    players = _universe(40)
    out = recommend_pick(players, drafted_ids=[], my_roster_ids=[],
                         teams=12, rounds=14, my_slot=1, n_sims=20, top_k=4, seed=3)
    top = out["candidates"][0]
    assert "avg_depth" in top
    assert top["lineup_stdev"] >= 0
    assert top["lineup_p25"] <= top["lineup_p75"]
    assert isinstance(top["likely_next"], list) and top["likely_next"]
    # likely_next is per upcoming pick slot, in pick order, each the modal player there.
    pick_nos = [lp["pick_no"] for lp in top["likely_next"]]
    assert pick_nos == sorted(pick_nos)
    assert pick_nos[0] == out["my_upcoming_picks"][1]  # first entry == my very next pick
    for lp in top["likely_next"]:
        assert {"pick_no", "player_id", "name", "pos", "pct"} <= set(lp)
        assert 0.0 <= lp["pct"] <= 1.0
    confidence = out["recommendation_confidence"]
    assert confidence["label"] in {"near_tie", "slight_edge", "strong_edge"}
    assert 0.0 <= confidence["win_pct"] <= 1.0
    assert confidence["sims"] == 20


def test_recommend_pick_respects_custom_slots():
    # RBs and WRs interleaved with identical value curves...
    players = [
        SimPlayer(player_id=str(i), name=f"{'RB' if i % 2 == 0 else 'WR'}{i}",
                  pos="RB" if i % 2 == 0 else "WR", adp=float(i), proj=float(200 - i))
        for i in range(1, 41)
    ]
    # ...but only WRs can start -> the rec must be a WR.
    out = recommend_pick(players, drafted_ids=[], my_roster_ids=[],
                         teams=12, rounds=10, my_slot=1, slots={"WR": 3},
                         n_sims=20, top_k=6, seed=1)
    assert out["recommendation"]["pos"] == "WR"


# ---------------------------------------------------------------------------
# sim_players_from_config_players
# ---------------------------------------------------------------------------
def test_sim_players_from_config_players():
    cfg_players = [
        {"player_id": "1", "name": "Josh Allen", "pos": "QB", "overall_rank": 3, "fpts": 360.5},
        {"player_id": None, "name": "skip"},  # missing id -> skipped
    ]
    sps = sim_players_from_config_players(cfg_players)
    assert len(sps) == 1
    assert sps[0].adp == 3.0 and sps[0].proj == pytest.approx(360.5)


def test_sim_players_custom_value_overrides_vbd_and_flex_value():
    rows = [
        _row("te1", "TE", 150.0, 40.0, adp=8.0),
        _row("rb1", "RB", 180.0, 60.0, adp=9.0),
        _row("wr1", "WR", 170.0, 50.0, adp=10.0),
    ]
    players = {
        p.player_id: p
        for p in sim_players_from_config_players(rows, {"te1": 77.5})
    }
    assert players["te1"].proj == pytest.approx(77.5)
    assert players["te1"].flex_proj == pytest.approx(77.5)
    assert players["rb1"].proj == pytest.approx(60.0)


def test_sim_players_prefers_vbd_as_value_currency():
    sps = sim_players_from_config_players([
        {"player_id": "1", "name": "A", "pos": "RB", "overall_rank": 1, "fpts": 300, "vbd": 120},
        {"player_id": "2", "name": "B", "pos": "QB", "overall_rank": 2, "fpts": 380},  # no vbd
    ])
    assert sps[0].proj == pytest.approx(120)   # VBD is used when present
    assert sps[1].proj == pytest.approx(380)   # falls back to raw points


def test_sim_players_uses_real_adp_with_modeled_stdev_fallback():
    sps = sim_players_from_config_players([
        {"player_id": "1", "name": "A", "pos": "RB", "overall_rank": 10, "vbd": 100,
         "adp": 2.5, "adp_stdev": 1.1},
        {"player_id": "2", "name": "B", "pos": "WR", "overall_rank": 20, "vbd": 80},  # no adp
    ])
    by_id = {p.player_id: p for p in sps}
    assert by_id["1"].adp == pytest.approx(2.5) and by_id["1"].adp_stdev == pytest.approx(1.1)
    # No ADP -> fall back to overall_rank with a positive modeled stdev.
    assert by_id["2"].adp == pytest.approx(20.0) and by_id["2"].adp_stdev > 0


def test_opponent_order_draws_every_player_once_and_is_deterministic():
    import random as _random
    board = _universe(25)

    class CountingRandom:
        def __init__(self):
            self.calls = []

        def gauss(self, mean, stdev):
            self.calls.append((mean, stdev))
            return mean

    counting = CountingRandom()
    ordered = sim._draw_opponent_order(board, counting)
    assert len(counting.calls) == len(board)
    assert [p.player_id for p in ordered] == [p.player_id for p in sorted(board, key=lambda p: p.adp)]

    first = sim._draw_opponent_order(board, _random.Random(7))
    second = sim._draw_opponent_order(board, _random.Random(7))
    assert [p.player_id for p in first] == [p.player_id for p in second]


def test_recommend_pick_draws_shared_boards_once_per_sim(monkeypatch):
    players = _universe(30)
    calls = []
    original = sim._draw_opponent_order

    def recording_board(available, rng):
        calls.append(len(available))
        return original(available, rng)

    monkeypatch.setattr(sim, "_draw_opponent_order", recording_board)
    out = recommend_pick(
        players, drafted_ids=[], my_roster_ids=[],
        teams=10, rounds=8, my_slot=1, n_sims=12, top_k=5, seed=3,
    )
    assert len(calls) == 12  # not 12 multiplied by candidate count
    assert out["recommendation_confidence"]["sims"] == 12
