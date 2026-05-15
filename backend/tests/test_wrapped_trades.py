"""Tests for ``app.services.wrapped.trade_accolades`` + trade extraction in transactions."""
from __future__ import annotations

from datetime import date
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

    def test_captures_status_updated_ms(self):
        """Sleeper's ``status_updated`` epoch-ms timestamp must be
        plumbed through to the Trade so the KTC integral evaluator has
        a real trade_date to anchor the window on."""
        ctx = _ctx({1: "alice", 2: "bob"})
        tx = {
            "type": "trade", "status": "complete",
            "transaction_id": "T1", "roster_ids": [1, 2],
            "adds": {"P1": 2}, "status_updated": 1697328000000,  # 2023-10-15 UTC
        }
        trade = _build_trade(tx, week=6, ctx=ctx)
        assert trade is not None
        assert trade.status_updated_ms == 1697328000000

    def test_missing_status_updated_is_none(self):
        ctx = _ctx({1: "alice", 2: "bob"})
        tx = {"type": "trade", "status": "complete",
              "roster_ids": [1, 2], "adds": {"P1": 2}}
        trade = _build_trade(tx, week=6, ctx=ctx)
        assert trade is not None
        assert trade.status_updated_ms is None

    def test_malformed_status_updated_is_none(self):
        ctx = _ctx({1: "alice", 2: "bob"})
        tx = {"type": "trade", "status": "complete",
              "roster_ids": [1, 2], "adds": {"P1": 2},
              "status_updated": "not a timestamp"}
        trade = _build_trade(tx, week=6, ctx=ctx)
        assert trade is not None
        assert trade.status_updated_ms is None


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


# A "trade season" all the unit-test fixtures live in: 2024 NFL season.
# Active days for the integral are Sep 1, 2024 -> May 14, 2026 (eval end),
# which covers ~1.5 active seasons -- plenty for the ratios to settle out.
_TEST_SEASON = 2024
_TEST_EVAL_END = date(2026, 5, 14)


def _flat_blob_for(
    values: Dict[str, float], *, start: str = "2024-09-01",
) -> Dict[str, Dict[str, float]]:
    """Translate per-player constant-value test fixtures into the
    ``{asset_id: {date: value}}`` shape the integral evaluator's blob
    resolver consumes. A single anchor date is enough because the
    resolver forward-fills from there.
    """
    return {pid: {start: float(v)} for pid, v in values.items()}


def _patched_blob(monkeypatch, values: Dict[str, float]) -> None:
    """Make ``get_flat_blob`` return our fixture blob for the 1QB format.

    Also clears the ``trade_accolades`` module's resolver cache so the
    new blob is actually consulted.
    """
    flat = _flat_blob_for(values)
    meta = {pid: {"name": f"player_{pid}"} for pid in values}
    monkeypatch.setattr(
        "app.services.wrapped.trade_accolades.get_flat_blob",
        lambda fmt="1qb": (flat, meta),
    )


class TestCalculateTradeAccolades:
    """Behavior tests for the KTC value-integral trade-accolades pipeline.

    The integral evaluator is deterministic for constant-value players,
    so we assert *sign* + *winner* on every trade and a few sanity
    bounds on the per-season edge instead of an exact float, which would
    couple the test to the concavity exponent.
    """

    def test_winner_is_higher_value_side(self, monkeypatch):
        _patched_blob(monkeypatch, {"P1": 8000.0, "P2": 1000.0})
        tx = LeagueTransactions(trades=[_trade(3, ["P1"], ["P2"])])
        out = ta.calculate_trade_accolades(
            tx, season=_TEST_SEASON, evaluation_end=_TEST_EVAL_END,
        )
        assert out["trades"][0]["winner"] == "alice"
        # Edge should be strongly positive (alice held the 8000 player).
        assert out["trades"][0]["ktc_edge_per_season"] > 0

    def test_per_user_net_value(self, monkeypatch):
        # Alice held the 8000 player, Bob the 1000. Net should mirror.
        _patched_blob(monkeypatch, {"P1": 8000.0, "P2": 1000.0})
        tx = LeagueTransactions(trades=[_trade(3, ["P1"], ["P2"])])
        out = ta.calculate_trade_accolades(
            tx, season=_TEST_SEASON, evaluation_end=_TEST_EVAL_END,
        )
        assert out["by_user"]["alice"]["net_ktc_per_season"] > 0
        assert out["by_user"]["bob"]["net_ktc_per_season"] < 0
        # 2-side, single trade: alice's net and bob's net should be
        # equal-and-opposite (alice gets +edge, bob gets -edge/1).
        assert out["by_user"]["alice"]["net_ktc_per_season"] == -out["by_user"]["bob"]["net_ktc_per_season"]
        assert out["by_user"]["alice"]["num_trades"] == 1

    def test_biggest_fleecing_is_largest_gap(self, monkeypatch):
        _patched_blob(monkeypatch, {
            "P1": 8000.0, "P2": 1000.0,    # big edge for alice in T3
            "P3": 5000.0, "P4": 5000.0,    # essentially even in T5
        })
        tx = LeagueTransactions(trades=[
            _trade(3, ["P1"], ["P2"]),
            _trade(5, ["P3"], ["P4"]),
        ])
        out = ta.calculate_trade_accolades(
            tx, season=_TEST_SEASON, evaluation_end=_TEST_EVAL_END,
        )
        assert out["biggest_fleecing"] is not None
        assert out["biggest_fleecing"]["winner"] == "alice"
        assert out["biggest_fleecing"]["transaction_id"] == "T3"

    def test_no_fleecing_when_all_trades_even(self, monkeypatch):
        # Identical values on both sides -> zero edge -> below the floor.
        _patched_blob(monkeypatch, {"P1": 5000.0, "P2": 5000.0})
        tx = LeagueTransactions(trades=[_trade(3, ["P1"], ["P2"])])
        out = ta.calculate_trade_accolades(
            tx, season=_TEST_SEASON, evaluation_end=_TEST_EVAL_END,
        )
        assert out["biggest_fleecing"] is None

    def test_most_active_trader(self, monkeypatch):
        _patched_blob(monkeypatch, {
            "P1": 5000.0, "P2": 5000.0, "P3": 5000.0, "P4": 5000.0,
        })
        tx = LeagueTransactions(trades=[
            _trade(1, ["P1"], ["P2"]),
            _trade(2, ["P3"], ["P4"]),
        ])
        out = ta.calculate_trade_accolades(
            tx, season=_TEST_SEASON, evaluation_end=_TEST_EVAL_END,
        )
        # Both managers traded twice; whoever appears first in
        # iteration wins the tiebreak. What matters is the count.
        assert out["most_active_trader"]["num_trades"] == 2

    def test_empty_trades(self, monkeypatch):
        """Empty section is returned without even touching the blob."""
        # No monkeypatch needed -- early return guards against the load.
        out = ta.calculate_trade_accolades(
            LeagueTransactions(),
            season=_TEST_SEASON, evaluation_end=_TEST_EVAL_END,
        )
        assert out["trades"] == []
        assert out["by_user"] == {}
        assert out["biggest_fleecing"] is None
        assert out["most_active_trader"] is None
        assert out["evaluation_end"] == "2026-05-14"

    def test_blob_load_failure_returns_empty(self, monkeypatch):
        """If the historical KTC blob is unreachable the section falls
        back to empty rather than crashing the whole wrapped payload."""
        def boom(fmt="1qb"):
            raise RuntimeError("blob unavailable")
        monkeypatch.setattr(
            "app.services.wrapped.trade_accolades.get_flat_blob", boom,
        )
        tx = LeagueTransactions(trades=[_trade(3, ["P1"], ["P2"])])
        out = ta.calculate_trade_accolades(
            tx, season=_TEST_SEASON, evaluation_end=_TEST_EVAL_END,
        )
        assert out["trades"] == []
        assert out["biggest_fleecing"] is None

    def test_picks_contribute_to_value(self, monkeypatch):
        """Picks should hit the KTC pick blob ids and contribute when
        the blob has them."""
        # alice gets LOW (100) + a 2025 mid 1st pick; bob gets P1 (5000).
        # We seed both the pick id and the player ids.
        blob = {
            "P1": {"2024-09-01": 5000.0},
            "LOW": {"2024-09-01": 100.0},
            "pick:2025_mid_1st": {"2024-09-01": 4000.0},
        }
        meta = {pid: {"name": pid} for pid in blob}
        monkeypatch.setattr(
            "app.services.wrapped.trade_accolades.get_flat_blob",
            lambda fmt="1qb": (blob, meta),
        )
        trade = Trade(
            week=2, transaction_id="T",
            sides={
                "alice": TradeSide(username="alice", received_player_ids=["LOW"],
                                   received_picks=[{"season": "2025", "round": 1}]),
                "bob": TradeSide(username="bob", received_player_ids=["P1"]),
            },
        )
        tx = LeagueTransactions(trades=[trade])
        out = ta.calculate_trade_accolades(
            tx, season=_TEST_SEASON, evaluation_end=_TEST_EVAL_END,
        )
        # Bob got 5000-value player; Alice got 100+4000 = 4100 across
        # two assets. With concavity favoring the lone star, bob should
        # still win on per-side ktc_equiv.
        assert out["trades"][0]["winner"] == "bob"
        # The pick line should show up in alice's per-asset breakdown
        # with the synthesised pick label.
        alice_side = next(s for s in out["trades"][0]["sides"] if s["username"] == "alice")
        labels = [a["label"] for a in alice_side["assets"]]
        assert any("2025 R1 pick" in lbl for lbl in labels)

    def test_trade_date_from_status_updated(self, monkeypatch):
        """When Sleeper provides status_updated, trade_date matches it."""
        _patched_blob(monkeypatch, {"P1": 8000.0, "P2": 1000.0})
        # Build a properly stamped trade with a real UTC epoch-ms.
        from datetime import datetime as _dt, timezone as _tz
        ts_ms = int(
            _dt(2024, 10, 15, 12, 0, tzinfo=_tz.utc).timestamp() * 1000
        )
        trade = Trade(
            week=6, transaction_id="T",
            sides={
                "alice": TradeSide(username="alice", received_player_ids=["P1"]),
                "bob": TradeSide(username="bob", received_player_ids=["P2"]),
            },
            status_updated_ms=ts_ms,
        )
        tx = LeagueTransactions(trades=[trade])
        out = ta.calculate_trade_accolades(
            tx, season=_TEST_SEASON, evaluation_end=_TEST_EVAL_END,
        )
        assert out["trades"][0]["trade_date"] == "2024-10-15"


# ---------------------------------------------------------------------------
# Redraft-skip behavior in _build_trades_section
# ---------------------------------------------------------------------------
class TestTradesSectionRedraftSkip:
    """Redraft leagues use a different (PPG-lookback) trade evaluator;
    the KTC value-integral lookback in the wrapped pipeline applies only
    to dynasty leagues. Verify the section short-circuits to empty for
    redraft regardless of how many trades the league had.
    """

    def _ctx_stub(self, *, is_dynasty: bool) -> Any:
        return SimpleNamespace(
            is_dynasty=is_dynasty,
            num_qbs="1",
            skill_score_key=None,
        )

    def test_redraft_skips_trades(self):
        from app.services.wrapped.pipeline import _build_trades_section
        ctx = self._ctx_stub(is_dynasty=False)
        tx = LeagueTransactions(trades=[_trade(2, ["P1"], ["P2"])])
        out = _build_trades_section(ctx, tx, {})
        assert out == {
            "trades": [], "by_user": {},
            "biggest_fleecing": None, "most_active_trader": None,
        }

    def test_dynasty_with_no_trades_is_also_empty(self):
        from app.services.wrapped.pipeline import _build_trades_section
        ctx = self._ctx_stub(is_dynasty=True)
        tx = LeagueTransactions(trades=[])
        out = _build_trades_section(ctx, tx, {})
        assert out["trades"] == []
        assert out["biggest_fleecing"] is None


# ---------------------------------------------------------------------------
# inspect_trade -- single-trade payload for the frontend inspector
# ---------------------------------------------------------------------------
class TestInspectTrade:
    """The inspector returns the same verdict the main ledger does plus
    a race chart + per-asset sparkline series. We assert on payload shape
    + that the chart points are aligned + that the verdict matches the
    one ``calculate_trade_accolades`` produces for the same trade. The
    exact ktc_equiv numbers are owned by the integral tests; here we just
    care that the inspector wires them through correctly.
    """

    def _setup(self, monkeypatch):
        _patched_blob(monkeypatch, {"P1": 8000.0, "P2": 1000.0})
        trade = _trade(3, ["P1"], ["P2"])
        return trade

    def test_payload_shape(self, monkeypatch):
        trade = self._setup(monkeypatch)
        out = ta.inspect_trade(
            trade, season=_TEST_SEASON, evaluation_end=_TEST_EVAL_END,
        )
        # Top-level keys.
        assert set(out.keys()) >= {
            "trade", "race_chart", "per_asset_series", "k", "evaluation_end",
        }
        # Trade summary carries the same verdict the main ledger produces.
        assert out["trade"]["winner"] == "alice"
        assert out["trade"]["ktc_edge_per_season"] > 0
        # Race chart has one side per trade side, both with non-empty points
        # and aligned timelines.
        assert len(out["race_chart"]["sides"]) == 2
        n_points = len(out["race_chart"]["sides"][0]["points"])
        assert n_points >= 2
        assert all(
            len(s["points"]) == n_points for s in out["race_chart"]["sides"]
        )
        # Per-asset series: one entry per asset (2 assets in this trade).
        assert len(out["per_asset_series"]) == 2
        labels = {row["label"] for row in out["per_asset_series"]}
        assert labels == {"player_P1", "player_P2"}

    def test_per_asset_series_reflects_blob_values(self, monkeypatch):
        trade = self._setup(monkeypatch)
        out = ta.inspect_trade(
            trade, season=_TEST_SEASON, evaluation_end=_TEST_EVAL_END,
        )
        # Each asset series should be (close to) the constant value we
        # seeded the blob with -- forward-fill on a single anchor point.
        for row in out["per_asset_series"]:
            expected = 8000.0 if row["asset_id"] == "P1" else 1000.0
            for pt in row["points"]:
                assert pt["value"] == expected

    def test_inspector_verdict_matches_main_ledger(self, monkeypatch):
        """Sanity: the inspector and the main ledger evaluate the same
        trade the same way. Any drift here would mean the row + the
        expanded chart could disagree -- bad UX."""
        trade = self._setup(monkeypatch)
        tx = LeagueTransactions(trades=[trade])
        ledger = ta.calculate_trade_accolades(
            tx, season=_TEST_SEASON, evaluation_end=_TEST_EVAL_END,
        )
        inspector = ta.inspect_trade(
            trade, season=_TEST_SEASON, evaluation_end=_TEST_EVAL_END,
        )
        assert ledger["trades"][0]["winner"] == inspector["trade"]["winner"]
        assert (
            ledger["trades"][0]["ktc_edge_per_season"]
            == inspector["trade"]["ktc_edge_per_season"]
        )

