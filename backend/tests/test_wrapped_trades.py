"""Tests for ``app.services.wrapped.trade_accolades`` + trade extraction in transactions."""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Dict, List

import app.services.wrapped.trade_accolades as ta
from app.services.wrapped.transactions import (
    LeagueTransactions,
    Trade,
    TradeSide,
    _build_trade,
)


def _ctx(roster_to_user: Dict[int, str]) -> SimpleNamespace:
    return SimpleNamespace(roster_id_to_username=roster_to_user)


# ---------------------------------------------------------------------------
# _build_trade
# ---------------------------------------------------------------------------
class TestBuildTrade:
    def test_two_player_swap(self):
        ctx = _ctx({1: "alice", 2: "bob"})
        tx = {
            "type": "trade",
            "status": "complete",
            "transaction_id": "T1",
            "roster_ids": [1, 2],
            "adds": {"P1": 2, "P2": 1},  # alice gets P2, bob gets P1
            "drops": {"P1": 1, "P2": 2},
        }
        trade = _build_trade(tx, week=4, ctx=ctx)
        assert trade is not None
        assert trade.week == 4
        assert trade.transaction_id == "T1"
        assert trade.sides["alice"].received_player_ids == ["P2"]
        assert trade.sides["bob"].received_player_ids == ["P1"]

    def test_includes_draft_picks(self):
        ctx = _ctx({1: "alice", 2: "bob"})
        tx = {
            "type": "trade",
            "status": "complete",
            "roster_ids": [1, 2],
            "adds": {"P1": 1},
            "draft_picks": [
                {"season": "2025", "round": 1, "roster_id": 2,
                 "previous_owner_id": 2, "owner_id": 1},
            ],
        }
        trade = _build_trade(tx, week=2, ctx=ctx)
        assert trade is not None
        assert len(trade.sides["alice"].received_picks) == 1
        assert trade.sides["alice"].received_picks[0]["round"] == 1
        assert trade.sides["bob"].received_picks == []

    def test_returns_none_for_solo_trade(self):
        ctx = _ctx({1: "alice"})
        tx = {"type": "trade", "status": "complete", "roster_ids": [1]}
        assert _build_trade(tx, week=1, ctx=ctx) is None

    def test_returns_none_when_nothing_exchanged(self):
        ctx = _ctx({1: "alice", 2: "bob"})
        tx = {"type": "trade", "status": "complete", "roster_ids": [1, 2]}
        assert _build_trade(tx, week=1, ctx=ctx) is None

    def test_returns_none_for_unmappable_roster(self):
        ctx = _ctx({1: "alice"})  # no entry for roster 2
        tx = {"type": "trade", "status": "complete", "roster_ids": [1, 2],
              "adds": {"P1": 1}}
        assert _build_trade(tx, week=1, ctx=ctx) is None


# ---------------------------------------------------------------------------
# calculate_trade_accolades
# ---------------------------------------------------------------------------
def _trade(week: int, alice_gets: List[str], bob_gets: List[str]) -> Trade:
    return Trade(
        week=week,
        transaction_id=f"T{week}",
        sides={
            "alice": TradeSide(username="alice", received_player_ids=alice_gets),
            "bob": TradeSide(username="bob", received_player_ids=bob_gets),
        },
    )


class TestCalculateTradeAccolades:
    def test_winner_is_higher_value_side(self):
        tx = LeagueTransactions(trades=[_trade(3, ["P1"], ["P2"])])
        values = {"P1": 8000.0, "P2": 1000.0}
        meta = {"P1": {"full_name": "Star"}, "P2": {"full_name": "Scrub"}}
        out = ta.calculate_trade_accolades(tx, values, meta)
        assert out["trades"][0]["winner"] == "alice"
        assert out["trades"][0]["value_gap"] == 7000.0

    def test_per_user_net_value(self):
        # Alice fleeces Bob: gets 8000, gives 1000.
        tx = LeagueTransactions(trades=[_trade(3, ["P1"], ["P2"])])
        values = {"P1": 8000.0, "P2": 1000.0}
        out = ta.calculate_trade_accolades(tx, values, {})
        # net = own_value - avg(others). With 2 sides and totals 8000/1000:
        # alice net = 8000 - 1000 = 7000; bob net = 1000 - 8000 = -7000.
        assert out["by_user"]["alice"]["net_value_gained"] == 7000.0
        assert out["by_user"]["bob"]["net_value_gained"] == -7000.0
        assert out["by_user"]["alice"]["num_trades"] == 1

    def test_biggest_fleecing_is_largest_gap(self):
        tx = LeagueTransactions(trades=[
            _trade(3, ["P1"], ["P2"]),  # gap 7000
            _trade(5, ["P3"], ["P4"]),  # gap 100
        ])
        values = {"P1": 8000, "P2": 1000, "P3": 500, "P4": 400}
        out = ta.calculate_trade_accolades(tx, values, {})
        assert out["biggest_fleecing"]["value_gap"] == 7000.0
        assert out["biggest_fleecing"]["winner"] == "alice"

    def test_no_fleecing_when_all_trades_even(self):
        # A perfectly even trade shouldn't get crowned.
        tx = LeagueTransactions(trades=[_trade(3, ["P1"], ["P2"])])
        values = {"P1": 5000, "P2": 5000}
        out = ta.calculate_trade_accolades(tx, values, {})
        assert out["biggest_fleecing"] is None

    def test_most_active_trader(self):
        tx = LeagueTransactions(trades=[
            _trade(1, ["P1"], ["P2"]),
            _trade(2, ["P3"], ["P4"]),
        ])
        out = ta.calculate_trade_accolades(tx, {}, {})
        # Both alice and bob in 2 trades — first by dict iteration wins;
        # what matters is the count is correct.
        assert out["most_active_trader"]["num_trades"] == 2

    def test_empty_trades(self):
        out = ta.calculate_trade_accolades(LeagueTransactions(), {}, {})
        assert out == {
            "trades": [], "by_user": {}, "biggest_fleecing": None,
            "most_active_trader": None,
        }

    def test_picks_contribute_to_value(self):
        # Bob trades P_lowvalue + a 1st-round pick for Alice's P1.
        trade = Trade(
            week=2, transaction_id="T",
            sides={
                "alice": TradeSide(username="alice", received_player_ids=["LOW"],
                                   received_picks=[{"season": "2025", "round": 1}]),
                "bob": TradeSide(username="bob", received_player_ids=["P1"]),
            },
        )
        tx = LeagueTransactions(trades=[trade])
        values = {"P1": 5000, "LOW": 100}
        out = ta.calculate_trade_accolades(tx, values, {})
        # alice got 100 (player) + 4500 (1st) = 4600; bob got 5000.
        # bob still wins by 400.
        assert out["trades"][0]["winner"] == "bob"
        assert out["trades"][0]["value_gap"] == 400.0
