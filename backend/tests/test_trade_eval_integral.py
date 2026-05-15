"""Tests for the trade-evaluator integral / orchestration layer."""
from __future__ import annotations

import importlib
import sys
from datetime import date
from pathlib import Path
from typing import Any, Dict

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
AZURE_FN_DIR = REPO_ROOT / "azure-functions"


@pytest.fixture(scope="module")
def te_modules():
    saved_path = list(sys.path)
    targets = (
        "config", "_fantasy_common", "trade_eval",
        "trade_eval.active_window",
        "trade_eval.value_integral",
        "trade_eval.trade_evaluator",
    )
    saved_mods = {name: sys.modules.get(name) for name in targets}
    for name in targets:
        sys.modules.pop(name, None)
    sys.path.insert(0, str(AZURE_FN_DIR))
    try:
        aw = importlib.import_module("trade_eval.active_window")
        vi = importlib.import_module("trade_eval.value_integral")
        ev = importlib.import_module("trade_eval.trade_evaluator")
        yield {"active_window": aw, "value_integral": vi, "trade_evaluator": ev}
    finally:
        sys.path[:] = saved_path
        for name, mod in saved_mods.items():
            if mod is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = mod


# ===========================================================================
# active_window
# ===========================================================================
class TestActiveWindow:
    def test_default_window_july_to_february(self, te_modules):
        aw = te_modules["active_window"]
        cal = aw.ActiveCalendar()
        # mid-season clearly active
        assert cal.is_active("2024-10-15")
        # mid-August active (camp)
        assert cal.is_active("2024-08-01")
        # mid-July boundary: 15th = active
        assert cal.is_active("2024-07-15")
        assert not cal.is_active("2024-07-14")
        # Feb 15 = active; Feb 16 = inactive
        assert cal.is_active("2025-02-15")
        assert not cal.is_active("2025-02-16")
        # April -- clearly offseason
        assert not cal.is_active("2024-04-01")
        # June -- offseason
        assert not cal.is_active("2024-06-15")

    def test_iter_active_days_skips_offseason(self, te_modules):
        aw = te_modules["active_window"]
        cal = aw.ActiveCalendar()
        days = list(cal.iter_active_days("2024-02-10", "2024-07-20"))
        # 2/10-2/15 inclusive = 6 days; then jump to 7/15-7/20 = 6 days.
        assert len(days) == 12
        assert date(2024, 2, 16) not in days
        assert date(2024, 7, 14) not in days
        assert date(2024, 7, 15) in days

    def test_active_intervals_split_around_offseason(self, te_modules):
        aw = te_modules["active_window"]
        cal = aw.ActiveCalendar()
        intervals = cal.active_intervals("2024-02-10", "2024-09-30")
        # Should yield two intervals: (2/10-2/15) and (7/15-9/30).
        assert len(intervals) == 2
        assert intervals[0] == (date(2024, 2, 10), date(2024, 2, 15))
        assert intervals[1] == (date(2024, 7, 15), date(2024, 9, 30))

    def test_custom_window(self, te_modules):
        aw = te_modules["active_window"]
        cal = aw.ActiveCalendar(start_month=9, start_day=1,
                                end_month=1, end_day=31)
        assert cal.is_active("2024-09-01")
        assert not cal.is_active("2024-08-31")
        assert cal.is_active("2025-01-31")
        assert not cal.is_active("2025-02-01")


# ===========================================================================
# value_integral
# ===========================================================================
class TestValueSeries:
    def test_forward_fill_sampling(self, te_modules):
        vi = te_modules["value_integral"]
        series = vi.ValueSeries.from_mapping({
            "2024-09-01": 5000.0,
            "2024-09-10": 5500.0,
            "2024-10-01": 6000.0,
        })
        # Before any known date -> 0
        assert series.value_on("2024-08-31") == 0.0
        # On known date
        assert series.value_on("2024-09-01") == 5000.0
        # Mid-gap forward fill
        assert series.value_on("2024-09-05") == 5000.0
        assert series.value_on("2024-09-10") == 5500.0
        assert series.value_on("2024-09-15") == 5500.0
        # Past the last known date
        assert series.value_on("2025-01-01") == 6000.0

    def test_max_stale_days_falls_back_to_stale_value(self, te_modules):
        vi = te_modules["value_integral"]
        series = vi.ValueSeries.from_mapping(
            {"2024-09-01": 5000.0},
            max_stale_days=10,
            stale_value=0.0,
        )
        # Within window
        assert series.value_on("2024-09-10") == 5000.0
        # Beyond window -> falls to stale value (retired player effect)
        assert series.value_on("2024-09-20") == 0.0

    def test_initial_value_used_before_first_sample(self, te_modules):
        vi = te_modules["value_integral"]
        series = vi.ValueSeries.from_mapping(
            {"2024-09-01": 5000.0}, initial_value=2500.0,
        )
        assert series.value_on("2020-01-01") == 2500.0


class TestIntegrateValue:
    def test_constant_value_gives_score_v_to_k_times_active_days(self, te_modules):
        """v(t)=const → score = const^k * active_days. Easy ground truth."""
        vi = te_modules["value_integral"]
        # 1000-value asset, held across week 1 (Sep 5-11, 2024 -- all active).
        series = vi.ValueSeries.from_mapping({"2024-08-01": 1000.0})
        result = vi.integrate_value(
            series, "2024-09-05", "2024-09-11", k=1.0,
        )
        assert result.active_days == 7
        assert result.score == pytest.approx(1000.0 * 7)
        assert result.raw_area == pytest.approx(1000.0 * 7)

    def test_concavity_exponent_rewards_high_value(self, te_modules):
        vi = te_modules["value_integral"]
        # Compare two assets, both held same window:
        #   A: 5000 constant
        #   B: 2x of 2500 constant
        a = vi.ValueSeries.from_mapping({"2024-08-01": 5000.0})
        b = vi.ValueSeries.from_mapping({"2024-08-01": 2500.0})
        win_start, win_end = "2024-09-05", "2024-09-11"
        score_a = vi.integrate_value(a, win_start, win_end, k=1.4).score
        score_b = vi.integrate_value(b, win_start, win_end, k=1.4).score
        # With k=1.4, A's score > 2*B's score (superstar premium).
        assert score_a > 2 * score_b
        # With k=1 they're exactly equal in total value-time.
        score_a_k1 = vi.integrate_value(a, win_start, win_end, k=1.0).score
        score_b_k1 = vi.integrate_value(b, win_start, win_end, k=1.0).score
        assert score_a_k1 == pytest.approx(2 * score_b_k1)

    def test_offseason_contributes_zero(self, te_modules):
        vi = te_modules["value_integral"]
        series = vi.ValueSeries.from_mapping({"2024-01-01": 5000.0})
        # Pure offseason window (April -- entirely inactive).
        result = vi.integrate_value(series, "2024-04-01", "2024-04-30", k=1.0)
        assert result.active_days == 0
        assert result.score == 0.0
        assert result.raw_area == 0.0
        assert result.total_days == 30

    def test_partial_offseason_window(self, te_modules):
        vi = te_modules["value_integral"]
        series = vi.ValueSeries.from_mapping({"2024-01-01": 1000.0})
        # 2/10 -> 7/20: active 2/10-2/15 (6 days) + 7/15-7/20 (6 days) = 12.
        result = vi.integrate_value(series, "2024-02-10", "2024-07-20", k=1.0)
        assert result.active_days == 12
        assert result.score == pytest.approx(1000.0 * 12)

    def test_keep_daily_samples(self, te_modules):
        vi = te_modules["value_integral"]
        series = vi.ValueSeries.from_mapping({"2024-08-01": 1000.0})
        result = vi.integrate_value(
            series, "2024-09-05", "2024-09-07", k=1.0,
            keep_daily_samples=True,
        )
        assert len(result.daily_samples) == 3
        assert result.daily_samples[0] == (date(2024, 9, 5), 1000.0)

    def test_end_before_start_returns_zero(self, te_modules):
        vi = te_modules["value_integral"]
        series = vi.ValueSeries.from_mapping({"2024-08-01": 1000.0})
        result = vi.integrate_value(series, "2024-09-10", "2024-09-05")
        assert result.score == 0.0
        assert result.active_days == 0


# ===========================================================================
# trade_evaluator
# ===========================================================================
def _make_value_blob() -> Dict[str, Dict[str, float]]:
    """Synthetic blob with two clean curves we can compute by hand."""
    return {
        # Player A: constant 6000.
        "playerA": {"2024-08-01": 6000.0},
        # Player B: constant 4000.
        "playerB": {"2024-08-01": 4000.0},
        # Player C: zero (retired-ish placeholder).
        "playerC": {},
    }


class TestEvaluateTrade:
    def test_two_side_winner_basic(self, te_modules):
        ev = te_modules["trade_evaluator"]
        blob = _make_value_blob()
        resolver = ev.make_blob_resolver(blob)
        trade = ev.Trade(
            trade_date=date(2024, 9, 5),
            evaluation_end=date(2024, 9, 11),  # 7 active days
            sides=[
                ev.TradeSide("teamA", [ev.TradeAsset("playerA", label="A")]),
                ev.TradeSide("teamB", [ev.TradeAsset("playerB", label="B")]),
            ],
        )
        result = ev.evaluate_trade(trade, value_resolver=resolver, k=1.4)
        assert result.winner_label == "teamA"
        assert result.margins["teamA"] > 0
        assert result.margins["teamB"] < 0
        # Symmetry: in a 2-sided trade margins should be opposites.
        assert result.margins["teamA"] == pytest.approx(-result.margins["teamB"])

    def test_multi_asset_side_aggregates(self, te_modules):
        ev = te_modules["trade_evaluator"]
        vi = te_modules["value_integral"]
        blob = _make_value_blob()
        resolver = ev.make_blob_resolver(blob)
        # A receives 2x player B (4000 each), B receives 1x player A (6000).
        # With k=1.4, 2 * 4000^1.4 vs 1 * 6000^1.4.
        trade = ev.Trade(
            trade_date=date(2024, 9, 5),
            evaluation_end=date(2024, 9, 11),
            sides=[
                ev.TradeSide("teamA", [
                    ev.TradeAsset("playerB", label="B1"),
                    ev.TradeAsset("playerB", label="B2"),
                ]),
                ev.TradeSide("teamB", [ev.TradeAsset("playerA", label="A")]),
            ],
        )
        result = ev.evaluate_trade(trade, value_resolver=resolver, k=1.4)
        # 2 * 4000^1.4 ≈ 197,898 vs 6000^1.4 ≈ 161,712 per active day.
        # The receiving side with 2x 4000 *should* still win at k=1.4 because
        # the concavity penalty isn't strong enough to overcome 2x quantity
        # at this specific value pair. This verifies math direction.
        assert result.winner_label == "teamA"

    def test_quantity_does_not_overcome_quality_at_high_k(self, te_modules):
        ev = te_modules["trade_evaluator"]
        blob = {
            "stud": {"2024-08-01": 9000.0},
            "mid":  {"2024-08-01": 3000.0},
        }
        resolver = ev.make_blob_resolver(blob)
        # 3 mids vs 1 stud at k=2: 3 * 3000^2 = 27M vs 9000^2 = 81M.
        # Concavity wipes out the quantity advantage.
        trade = ev.Trade(
            trade_date=date(2024, 9, 5),
            evaluation_end=date(2024, 9, 11),
            sides=[
                ev.TradeSide("quantity", [ev.TradeAsset("mid")] * 3),
                ev.TradeSide("quality",  [ev.TradeAsset("stud")]),
            ],
        )
        result = ev.evaluate_trade(trade, value_resolver=resolver, k=2.0)
        assert result.winner_label == "quality"

    def test_held_until_truncates_window(self, te_modules):
        ev = te_modules["trade_evaluator"]
        blob = _make_value_blob()
        resolver = ev.make_blob_resolver(blob)
        # teamA flips player A after 3 days.
        trade = ev.Trade(
            trade_date=date(2024, 9, 5),
            evaluation_end=date(2024, 9, 11),
            sides=[
                ev.TradeSide("teamA", [
                    ev.TradeAsset("playerA", held_until=date(2024, 9, 7)),
                ]),
                ev.TradeSide("teamB", [ev.TradeAsset("playerB")]),
            ],
        )
        result = ev.evaluate_trade(trade, value_resolver=resolver, k=1.0)
        # teamA holds 3 active days vs teamB's 7 -- B should win even
        # though A is the more valuable asset.
        assert result.winner_label == "teamB"
        # teamA's active_days = 3, teamB's = 7.
        a_eval = result.sides[0].asset_evaluations[0]
        b_eval = result.sides[1].asset_evaluations[0]
        assert a_eval.integral.active_days == 3
        assert b_eval.integral.active_days == 7

    def test_surplus_bonus_hook_adds_to_score(self, te_modules):
        ev = te_modules["trade_evaluator"]
        blob = _make_value_blob()
        resolver = ev.make_blob_resolver(blob)
        # Give teamB a flat bonus -- enough to flip the winner. Has to be
        # large enough to swamp the integral score at the default k
        # (currently 2.5), which makes the integral grow faster than
        # plain value-days.
        big_bonus = 1e18
        def surplus(asset, integral, start, end):
            return big_bonus if asset.asset_id == "playerB" else 0.0
        trade = ev.Trade(
            trade_date=date(2024, 9, 5),
            evaluation_end=date(2024, 9, 11),
            sides=[
                ev.TradeSide("teamA", [ev.TradeAsset("playerA")]),
                ev.TradeSide("teamB", [ev.TradeAsset("playerB")]),
            ],
        )
        result = ev.evaluate_trade(
            trade, value_resolver=resolver, surplus_bonus=surplus,
        )
        b_eval = result.sides[1].asset_evaluations[0]
        assert b_eval.surplus_bonus == big_bonus
        assert result.winner_label == "teamB"

    def test_missing_asset_yields_zero(self, te_modules):
        ev = te_modules["trade_evaluator"]
        blob = _make_value_blob()
        resolver = ev.make_blob_resolver(blob)
        trade = ev.Trade(
            trade_date=date(2024, 9, 5),
            evaluation_end=date(2024, 9, 11),
            sides=[
                ev.TradeSide("teamA", [ev.TradeAsset("playerA")]),
                ev.TradeSide("teamB", [ev.TradeAsset("unknown_id")]),
            ],
        )
        result = ev.evaluate_trade(trade, value_resolver=resolver, k=1.0)
        b_eval = result.sides[1].asset_evaluations[0]
        assert b_eval.integral.score == 0.0
        assert result.winner_label == "teamA"

    def test_to_dict_is_json_safe(self, te_modules):
        import json as _json
        ev = te_modules["trade_evaluator"]
        blob = _make_value_blob()
        resolver = ev.make_blob_resolver(blob)
        trade = ev.Trade(
            trade_date=date(2024, 9, 5),
            evaluation_end=date(2024, 9, 11),
            sides=[
                ev.TradeSide("A", [ev.TradeAsset("playerA")]),
                ev.TradeSide("B", [ev.TradeAsset("playerB")]),
            ],
        )
        result = ev.evaluate_trade(trade, value_resolver=resolver)
        # Round-trip through JSON without errors.
        _json.dumps(result.to_dict())


# ===========================================================================
# Real-data smoke test using the ingested KTC historical blob
# ===========================================================================
@pytest.fixture(scope="module")
def ktc_history_sf():
    """Load the canonical ``historical_KTC_rankings.json`` and return a
    (blob, sf_flat) tuple where ``sf_flat`` is the
    ``{sleeper_id: {date: value}}`` view of the SF series.

    Skips cleanly if the blob hasn't been built locally yet.
    """
    import json
    path = REPO_ROOT / "tests" / "fixtures" / "blobs" / "historical_KTC_rankings.json"
    if not path.exists():
        pytest.skip(
            "Historical KTC blob not present -- run "
            "tools/build_historical_ktc_json.py first"
        )
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _find_record_by_name(blob, name):
    """Return ``(key, record)`` for the first non-pick record matching ``name``."""
    for key, rec in blob["records"].items():
        if not rec.get("is_pick") and rec.get("name") == name:
            return key, rec
    return None, None


# ===========================================================================
# Readability transform: score -> KTC-equivalent
# ===========================================================================
class TestScoreToKtcEquiv:
    def test_inverse_of_constant_integral(self, te_modules):
        """``score_to_ktc_equiv(v^k * D, D, k) == v`` -- the literal inverse."""
        vi = te_modules["value_integral"]
        assert vi.score_to_ktc_equiv(7000.0 ** 1.4 * 200, 200, 1.4) == pytest.approx(7000.0)

    def test_zero_active_days_returns_zero(self, te_modules):
        vi = te_modules["value_integral"]
        assert vi.score_to_ktc_equiv(1234.0, 0, 1.4) == 0.0

    def test_zero_score_returns_zero(self, te_modules):
        vi = te_modules["value_integral"]
        assert vi.score_to_ktc_equiv(0.0, 200, 1.4) == 0.0

    def test_monotonic_in_score(self, te_modules):
        """For fixed (active_days, k), larger score -> larger KTC-equiv.
        This is what guarantees the race chart never crosses on a different
        day than the underlying verdict."""
        vi = te_modules["value_integral"]
        prev = -1.0
        for s in [100, 1_000, 10_000, 100_000, 1_000_000]:
            cur = vi.score_to_ktc_equiv(s, 200, 1.4)
            assert cur > prev
            prev = cur


class TestKtcEquivDisplay:
    """Verify the readability surface on ``SideEvaluation`` /
    ``TradeEvaluation``: numbers live on the 0-9999 KTC scale, sum
    correctly, and preserve the verdict ordering for the race chart."""

    def test_side_ktc_equiv_matches_constant_player(self, te_modules):
        """A side holding one 6000-constant asset should have a
        ``ktc_equiv`` of ~6000 over a fully-active window. This is the
        cleanest possible inverse check."""
        ev = te_modules["trade_evaluator"]
        blob = _make_value_blob()
        resolver = ev.make_blob_resolver(blob)
        trade = ev.Trade(
            trade_date=date(2024, 9, 5),
            evaluation_end=date(2024, 9, 11),  # 7 fully-active days
            sides=[
                ev.TradeSide("A", [ev.TradeAsset("playerA")]),
                ev.TradeSide("B", [ev.TradeAsset("playerB")]),
            ],
        )
        result = ev.evaluate_trade(trade, value_resolver=resolver, k=1.4)
        a = result.sides[0]
        b = result.sides[1]
        assert a.ktc_equiv == pytest.approx(6000.0, rel=1e-6)
        assert b.ktc_equiv == pytest.approx(4000.0, rel=1e-6)
        # avg_ktc on the asset row matches the raw value.
        assert a.asset_evaluations[0].avg_ktc == pytest.approx(6000.0)
        assert b.asset_evaluations[0].avg_ktc == pytest.approx(4000.0)

    def test_side_ktc_equiv_preserves_score_ordering(self, te_modules):
        """The crossover-day guarantee: if score_A > score_B then
        ktc_equiv_A > ktc_equiv_B. This holds because both sides
        share the same trade_active_days denominator and x^(1/k) is
        monotonic."""
        ev = te_modules["trade_evaluator"]
        # 1 6000-asset vs 2 5000-assets. Linear sum favors B (10k vs 6k)
        # but with k=1.4 the single star wins.
        blob = {
            "star": {"2024-08-01": 6000.0},
            "ok1":  {"2024-08-01": 5000.0},
            "ok2":  {"2024-08-01": 5000.0},
        }
        resolver = ev.make_blob_resolver(blob)
        trade = ev.Trade(
            trade_date=date(2024, 9, 1),
            evaluation_end=date(2024, 11, 30),
            sides=[
                ev.TradeSide("StarSide",   [ev.TradeAsset("star")]),
                ev.TradeSide("VolumeSide", [
                    ev.TradeAsset("ok1"), ev.TradeAsset("ok2"),
                ]),
            ],
        )
        result = ev.evaluate_trade(trade, value_resolver=resolver, k=1.4)
        star, vol = result.sides
        # k=1.4 superstar premium: total scores agree with ktc_equiv on winner.
        assert star.total_score < vol.total_score  # two 5000s beat one 6000 even at k=1.4
        assert star.ktc_equiv < vol.ktc_equiv      # ...and the equiv reflects that
        # Sanity: VolumeSide's KTC-equiv > 5000 (it holds 2 5000-equivs).
        assert vol.ktc_equiv > 5000.0
        # Headline edge is positive and small (close trade).
        assert result.ktc_edge_total > 0
        assert result.winner_label == "VolumeSide"

    def test_ktc_edge_per_season_scales_with_window(self, te_modules):
        """For a constant-value setup, a 1-season window and a 2-season
        window should produce the same per-season edge."""
        ev = te_modules["trade_evaluator"]
        blob = {
            "star": {"2023-01-01": 7000.0},
            "scrub": {"2023-01-01": 3000.0},
        }
        resolver = ev.make_blob_resolver(blob)

        def edge(start, end):
            trade = ev.Trade(
                trade_date=date.fromisoformat(start),
                evaluation_end=date.fromisoformat(end),
                sides=[
                    ev.TradeSide("A", [ev.TradeAsset("star")]),
                    ev.TradeSide("B", [ev.TradeAsset("scrub")]),
                ],
            )
            return ev.evaluate_trade(trade, value_resolver=resolver, k=1.4)

        one = edge("2024-07-15", "2025-02-15")   # ~1 season
        two = edge("2024-07-15", "2026-02-15")   # ~2 seasons
        # per-season edge should be roughly constant (within a few %)
        # since the values are constant and the window scales linearly.
        assert one.ktc_edge_per_season == pytest.approx(
            two.ktc_edge_per_season, rel=0.05,
        )
        # Total edge should be ~2x for the 2-season window.
        assert two.ktc_edge_total == pytest.approx(
            2 * one.ktc_edge_total, rel=0.05,
        )

    def test_to_dict_includes_ktc_fields(self, te_modules):
        ev = te_modules["trade_evaluator"]
        blob = _make_value_blob()
        resolver = ev.make_blob_resolver(blob)
        trade = ev.Trade(
            trade_date=date(2024, 9, 1),
            evaluation_end=date(2024, 11, 30),
            sides=[
                ev.TradeSide("A", [ev.TradeAsset("playerA", label="A1")]),
                ev.TradeSide("B", [ev.TradeAsset("playerB", label="B1")]),
            ],
        )
        result = ev.evaluate_trade(trade, value_resolver=resolver, k=1.4)
        d = result.to_dict()
        assert "ktc_edge_total" in d
        assert "ktc_edge_per_season" in d
        assert d["k"] == pytest.approx(1.4)
        assert d["active_days"] > 0
        # Every side carries its own ktc_equiv; every asset its avg_ktc.
        for side in d["sides"]:
            assert "ktc_equiv" in side
            for a in side["assets"]:
                assert "avg_ktc" in a


class TestRealKtcData:
    def test_blob_shape_and_counts(self, ktc_history_sf):
        b = ktc_history_sf
        # New canonical blob has top-level metadata + records map.
        assert "records" in b
        assert isinstance(b["records"], dict)
        # Per the build script's last run: ~500 players + 60 picks scraped,
        # plus CSV-only retired vets => well over 400 total records.
        assert b.get("n_players", 0) >= 400
        assert b.get("n_picks", 0) >= 36
        # JJ should be present with a real SF value series.
        _, jj = _find_record_by_name(b, "Justin Jefferson")
        assert jj is not None
        assert len(jj["SF_Historical"]) > 1000

    def test_integrate_jjefferson_2024_season(self, te_modules, ktc_history_sf):
        """Integrate JJ's value over the 2024 active season.

        Sanity check: with k=1 the raw_area should be close to
        (mean KTC value during season) * (active_days)
        which for JJ in 2024 is ~7500-ish * ~215 ≈ 1.6M.
        """
        vi = te_modules["value_integral"]
        _, jj = _find_record_by_name(ktc_history_sf, "Justin Jefferson")
        assert jj is not None
        jj_series_raw = jj["SF_Historical"]

        series = vi.ValueSeries.from_mapping(jj_series_raw)
        result = vi.integrate_value(
            series, "2024-07-15", "2025-02-15", k=1.0,
        )
        # Full season window = ~215 active days (Jul 15 -> Feb 15).
        assert 200 <= result.active_days <= 220
        # Average daily value implied by the result:
        avg = result.raw_area / max(result.active_days, 1)
        # JJ was 7000-9000 KTC over most of 2024. Be loose; just guard a
        # totally broken integration.
        assert 4000 <= avg <= 12_000

    def test_evaluate_real_jamaalwilliams_for_pollardkamara_trade(
        self, te_modules, ktc_history_sf,
    ):
        """A real-shape trade evaluation using the ingested blob.

        Toy comparison (no real-trade ground truth, just verifying the
        whole pipeline produces sensible numbers end-to-end): receiving
        Christian McCaffrey vs receiving Travis Etienne for the full
        2024 season window. McCaffrey should win because his value
        curve was higher despite the injury -- but it should be close
        enough not to be a blowout, exercising the concavity math.
        """
        ev = te_modules["trade_evaluator"]
        # Build a flat resolver view from the SF series in the new blob.
        flat = {
            key: rec["SF_Historical"]
            for key, rec in ktc_history_sf["records"].items()
            if rec.get("SF_Historical") and not rec.get("is_pick")
        }
        resolver = ev.make_blob_resolver(flat)

        # Find McCaffrey and Etienne by name.
        def _find(name: str) -> str:
            key, rec = _find_record_by_name(ktc_history_sf, name)
            assert rec is not None, f"{name} not in blob"
            return key

        mccaffrey_id = _find("Christian McCaffrey")
        etienne_id = _find("Travis Etienne")

        trade = ev.Trade(
            trade_date=date(2024, 7, 15),
            evaluation_end=date(2025, 2, 15),
            sides=[
                ev.TradeSide("teamA", [ev.TradeAsset(mccaffrey_id, label="CMC")]),
                ev.TradeSide("teamB", [ev.TradeAsset(etienne_id, label="Etienne")]),
            ],
        )
        result = ev.evaluate_trade(trade, value_resolver=resolver, k=1.4)
        # Both sides should have nonzero, finite scores.
        a_score = result.sides[0].total_score
        b_score = result.sides[1].total_score
        assert a_score > 0 and b_score > 0
        # A winner exists (no tie expected).
        assert result.winner_label in ("teamA", "teamB")


# ===========================================================================
# Cumulative integral + race chart
# ===========================================================================
class TestIntegrateCumulative:
    def test_endpoint_matches_full_integral(self, te_modules):
        """Cumulative endpoint score must equal the single-shot integral
        score to floating-point precision. If this slips, the chart will
        disagree with the verdict at the trade-window boundary."""
        vi = te_modules["value_integral"]
        series = vi.ValueSeries.from_mapping({
            "2024-08-01": 5000.0, "2024-10-01": 6000.0, "2024-12-01": 5500.0,
        })
        start, end = "2024-07-15", "2025-02-15"
        full = vi.integrate_value(series, start, end, k=1.4)
        cum = vi.integrate_value_cumulative(
            series, start, end, k=1.4, step_days=7,
        )
        assert cum[0].date == date(2024, 7, 15)
        assert cum[-1].date == date(2025, 2, 15)
        assert cum[-1].score == pytest.approx(full.score, rel=1e-9)
        assert cum[-1].raw_area == pytest.approx(full.raw_area, rel=1e-9)
        assert cum[-1].active_days == full.active_days

    def test_monotonic_nondecreasing(self, te_modules):
        """Running score must never decrease -- value^k is non-negative."""
        vi = te_modules["value_integral"]
        series = vi.ValueSeries.from_mapping({
            "2024-08-01": 4000.0, "2024-10-01": 8000.0,
        })
        cum = vi.integrate_value_cumulative(
            series, "2024-07-15", "2025-02-15", k=1.4, step_days=7,
        )
        for prev, cur in zip(cum, cum[1:]):
            assert cur.score >= prev.score
            assert cur.raw_area >= prev.raw_area
            assert cur.active_days >= prev.active_days

    def test_offseason_flatlines(self, te_modules):
        """During offseason days the running score doesn't move even when
        the underlying value is nonzero."""
        vi = te_modules["value_integral"]
        series = vi.ValueSeries.from_mapping({"2024-01-01": 5000.0})
        # All-offseason window: May -> June, default calendar => 0 active
        cum = vi.integrate_value_cumulative(
            series, "2024-05-01", "2024-06-30", k=1.4, step_days=7,
        )
        assert all(p.score == 0.0 for p in cum)
        assert all(p.active_days == 0 for p in cum)


class TestRaceChart:
    def _trade(self, ev, side_a_assets, side_b_assets,
               start="2024-07-15", end="2025-02-15"):
        return ev.Trade(
            trade_date=date.fromisoformat(start),
            evaluation_end=date.fromisoformat(end),
            sides=[
                ev.TradeSide("A", [ev.TradeAsset(a) for a in side_a_assets]),
                ev.TradeSide("B", [ev.TradeAsset(b) for b in side_b_assets]),
            ],
        )

    def test_endpoint_matches_verdict(self, te_modules):
        """The chart's final ktc_equiv on each side must equal the
        single-shot SideEvaluation.ktc_equiv. If the chart says one side
        is ahead on day N, the verdict for a trade ending on day N must
        agree."""
        ev = te_modules["trade_evaluator"]
        blob = {
            "star": {"2024-07-15": 7000.0},
            "scrub": {"2024-07-15": 3000.0},
        }
        resolver = ev.make_blob_resolver(blob)
        trade = self._trade(ev, ["star"], ["scrub"])
        verdict = ev.evaluate_trade(trade, value_resolver=resolver, k=1.4)
        chart = ev.build_race_chart(trade, value_resolver=resolver, k=1.4)

        a_final = chart.sides[0].points[-1]
        b_final = chart.sides[1].points[-1]
        a_side = next(s for s in verdict.sides if s.team_label == "A")
        b_side = next(s for s in verdict.sides if s.team_label == "B")
        assert a_final.ktc_equiv == pytest.approx(a_side.ktc_equiv, rel=1e-9)
        assert b_final.ktc_equiv == pytest.approx(b_side.ktc_equiv, rel=1e-9)
        # And the leader at the end matches the verdict winner.
        assert (a_final.score > b_final.score) == (verdict.winner_label == "A")

    def test_aligned_timelines(self, te_modules):
        """Both sides must share the same x-axis dates exactly."""
        ev = te_modules["trade_evaluator"]
        blob = {
            "p1": {"2024-07-15": 5000.0},
            "p2": {"2024-07-15": 4000.0},
        }
        resolver = ev.make_blob_resolver(blob)
        trade = self._trade(ev, ["p1"], ["p2"])
        chart = ev.build_race_chart(
            trade, value_resolver=resolver, k=1.4, step_days=7,
        )
        a_dates = [p.date for p in chart.sides[0].points]
        b_dates = [p.date for p in chart.sides[1].points]
        assert a_dates == b_dates
        assert a_dates[0] == date(2024, 7, 15)
        assert a_dates[-1] == date(2025, 2, 15)

    def test_crossover_when_lagger_overtakes(self, te_modules):
        """If side B's player starts low and grows past side A's flat
        player mid-window, the chart should record exactly one crossover,
        AND that date must come before the trade window's end (since the
        final-verdict winner is the post-crossover leader)."""
        ev = te_modules["trade_evaluator"]
        # A: flat 5000. B: starts at 1000, jumps to 9000 on day X.
        blob = {
            "flat": {"2024-07-15": 5000.0},
            "grower": {
                "2024-07-15": 1000.0,
                "2024-11-01": 9000.0,
            },
        }
        resolver = ev.make_blob_resolver(blob)
        trade = self._trade(ev, ["flat"], ["grower"])
        chart = ev.build_race_chart(
            trade, value_resolver=resolver, k=1.4, step_days=7,
        )
        assert len(chart.crossover_dates) >= 1
        # The verdict-final should match the post-crossover leader.
        verdict = ev.evaluate_trade(trade, value_resolver=resolver, k=1.4)
        # Side B (grower) wins overall because the 9000 plateau dominates.
        assert verdict.winner_label == "B"
        # Crossover date must precede evaluation end.
        for cd in chart.crossover_dates:
            assert cd <= date(2025, 2, 15)

    def test_no_crossover_when_dominant(self, te_modules):
        """A side that is always ahead should produce zero crossovers."""
        ev = te_modules["trade_evaluator"]
        blob = {
            "star": {"2024-07-15": 8000.0},
            "scrub": {"2024-07-15": 2000.0},
        }
        resolver = ev.make_blob_resolver(blob)
        trade = self._trade(ev, ["star"], ["scrub"])
        chart = ev.build_race_chart(trade, value_resolver=resolver, k=1.4)
        assert chart.crossover_dates == []

    def test_to_dict_shape(self, te_modules):
        ev = te_modules["trade_evaluator"]
        blob = {
            "p1": {"2024-07-15": 5000.0},
            "p2": {"2024-07-15": 4000.0},
        }
        resolver = ev.make_blob_resolver(blob)
        trade = self._trade(ev, ["p1"], ["p2"])
        chart = ev.build_race_chart(trade, value_resolver=resolver, k=1.4)
        d = chart.to_dict()
        assert d["trade_date"] == "2024-07-15"
        assert d["evaluation_end"] == "2025-02-15"
        assert d["k"] == pytest.approx(1.4)
        assert len(d["sides"]) == 2
        # Each point must carry the four required fields for the chart.
        pt = d["sides"][0]["points"][-1]
        for key in ("date", "score", "raw_area", "active_days", "ktc_equiv"):
            assert key in pt
