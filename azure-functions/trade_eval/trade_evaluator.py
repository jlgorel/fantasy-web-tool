"""Trade evaluator: turn a trade descriptor into per-side scores.

Pulls together :mod:`trade_eval.value_integral` and the active-window
calendar. Designed so the caller injects everything stateful:

  * **Value resolver** -- ``(TradeAsset) -> ValueSeries``. The ingest
    blob is loaded once at app start; the resolver looks each asset up
    in it. Picks that have already been used should be resolved by the
    caller to the drafted player (Thought 3 from the design notes).
  * **Surplus bonus hook** -- optional callable returning an additive
    bonus per asset. The default is "no bonus", which gives a pure
    value-integral evaluation. Once the peak-KTC-vs-PPG regression
    lands it gets plugged in here.

This keeps the orchestration layer free of IO and cheap to test against
synthetic data.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Callable, Dict, List, Optional, Protocol, Sequence

from .active_window import ActiveCalendar, DEFAULT_CALENDAR, _to_date, DateLike
from .value_integral import (
    CONCAVITY_EXPONENT,
    CumulativePoint,
    IntegralResult,
    ValueSeries,
    integrate_value,
    integrate_value_cumulative,
    score_to_ktc_equiv,
)


# ---------------------------------------------------------------------------
# Trade descriptor types
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class TradeAsset:
    """A single asset that changed hands in a trade.

    ``asset_id`` is the unified key used by the value-resolver. For real
    NFL players this is the Sleeper player_id. For unused picks it's a
    synthetic id like ``"pick:2025_mid_1st"``. For *used* picks the
    caller should resolve to the drafted player's Sleeper id and set
    ``is_pick=False`` -- per Thought 3 in the design notes, a 1st you
    used on JJ McCarthy is evaluated as JJ McCarthy, not as a pick.

    ``held_until`` is the date the asset left the receiving team's
    roster. ``None`` means "still held at evaluation_end". Once we have
    the chained-trade logic this gets populated automatically; for now
    it lets callers supply it manually.
    """

    asset_id: str
    label: str = ""              # human-readable, for output / debugging
    sleeper_id: Optional[str] = None
    is_pick: bool = False
    held_until: Optional[date] = None


@dataclass(frozen=True)
class TradeSide:
    """One side of a trade. ``team_label`` is just for display; the
    evaluator doesn't care about identity."""

    team_label: str
    received_assets: Sequence[TradeAsset]


@dataclass(frozen=True)
class Trade:
    """A complete trade event to evaluate.

    All sides must have at least one received asset. ``evaluation_end``
    caps each asset's holding window. Default evaluation_end = today is
    set by the caller.
    """

    trade_date: date
    evaluation_end: date
    sides: Sequence[TradeSide]


# ---------------------------------------------------------------------------
# Resolver protocols
# ---------------------------------------------------------------------------
class ValueResolver(Protocol):
    """Callable that produces the value time series for an asset.

    Implementations will typically read from the unified KTC/FantasyCalc
    value blob built by the data pipeline. Returning an empty
    ``ValueSeries`` for an unknown asset is fine -- the integral will
    just be 0 and the caller can flag it as missing.
    """

    def __call__(self, asset: TradeAsset) -> ValueSeries: ...


# Surplus bonus signature: given the asset, the integral result we just
# computed, and the holding window, return an additive bonus (in
# value-day units, same scale as ``IntegralResult.score``). Default
# implementation returns 0 -- the bonus regression isn't implemented yet.
SurplusBonusFn = Callable[
    [TradeAsset, IntegralResult, date, date], float
]


def _no_bonus(
    asset: TradeAsset, result: IntegralResult, start: date, end: date
) -> float:
    return 0.0


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------
# Approximate number of active days in one season window. The default
# calendar (Jul 15 -> Feb 15) yields 216 calendar days, all active (no
# offseason gap inside). Used to convert "score over the window" into a
# per-active-season rate for the human-readable summary.
ACTIVE_DAYS_PER_SEASON: float = 216.0


@dataclass
class AssetEvaluation:
    asset: TradeAsset
    holding_start: date
    holding_end: date
    integral: IntegralResult
    surplus_bonus: float
    total_score: float        # integral.score + surplus_bonus

    @property
    def asset_id(self) -> str:
        return self.asset.asset_id

    @property
    def avg_ktc(self) -> float:
        """Average raw KTC value across the asset's active holding days.

        This is the per-asset readout used in the breakdown rows: it's the
        un-exponentiated daily mean, so it lives on the familiar 0-9999
        KTC scale and matches the per-day KTC values users see on the
        sparkline. Not used for any verdict comparison -- side totals use
        :attr:`SideEvaluation.ktc_equiv` instead, which preserves the
        concavity ordering.
        """
        if self.integral.active_days <= 0:
            return 0.0
        return self.integral.raw_area / self.integral.active_days


@dataclass
class SideEvaluation:
    side: TradeSide
    asset_evaluations: List[AssetEvaluation]
    total_score: float
    # Concavity exponent + trade-level active-day window (same for every
    # side in the trade). Both are needed to convert the side total back
    # to the KTC-equivalent scale. They're stamped here at construction
    # time so callers don't have to thread them through manually.
    k: float = CONCAVITY_EXPONENT
    trade_active_days: int = 0

    @property
    def team_label(self) -> str:
        return self.side.team_label

    @property
    def ktc_equiv(self) -> float:
        """Side's score expressed as a KTC-equivalent value.

        This is the headline number the trade-inspector UI shows per side.
        See :func:`trade_eval.value_integral.score_to_ktc_equiv` for the
        math; the short version is "the constant KTC value that, held for
        the same active window, would produce this score." Because both
        sides share the same ``trade_active_days`` denominator, comparing
        ``ktc_equiv`` between sides preserves the verdict ordering.
        """
        return score_to_ktc_equiv(
            self.total_score, self.trade_active_days, self.k,
        )


@dataclass
class TradeEvaluation:
    """Top-level result. ``margins[team_label]`` is that side's score
    minus the average of all *other* sides -- a positive number means
    that side won."""

    trade: Trade
    sides: List[SideEvaluation]
    margins: Dict[str, float]
    winner_label: Optional[str]
    # Trade-level active-day window: same value lives on every SideEvaluation
    # but the top-level result also surfaces it for the UI.
    k: float = CONCAVITY_EXPONENT
    active_days: int = 0

    @property
    def ktc_edge_per_season(self) -> float:
        """Winner's ``ktc_equiv`` minus runner-up's, expressed as a
        per-active-season rate.

        Because ``ktc_equiv`` is itself a per-active-day rate (with the
        concavity undone), its diff is naturally a rate too. For constant
        value-curves this number is independent of the window length --
        it tells you "how much KTC-equivalent value, per season, did the
        winning side hold over the loser." This is the headline number
        for the inspector ("+4,012 KTC/yr").

        Returns ``0.0`` for ties or single-side trades. Always non-negative.
        """
        if len(self.sides) < 2:
            return 0.0
        ranked = sorted((s.ktc_equiv for s in self.sides), reverse=True)
        return float(ranked[0] - ranked[1])

    @property
    def ktc_edge_total(self) -> float:
        """The per-season rate multiplied by the number of active seasons
        in the trade window.

        This is the cumulative "+X KTC over the length of the trade" number
        the inspector pairs with the per-season rate. For a 1-season trade
        it equals :attr:`ktc_edge_per_season`; for a 3-season trade it's
        roughly 3x.
        """
        if self.active_days <= 0:
            return 0.0
        seasons = self.active_days / ACTIVE_DAYS_PER_SEASON
        return self.ktc_edge_per_season * seasons

    def to_dict(self) -> Dict[str, object]:
        """JSON-friendly view, useful for the eventual Flask endpoint."""
        return {
            "trade_date": self.trade.trade_date.isoformat(),
            "evaluation_end": self.trade.evaluation_end.isoformat(),
            "winner": self.winner_label,
            "k": self.k,
            "active_days": self.active_days,
            # KTC-equivalent headline numbers (UI-friendly).
            "ktc_edge_total": self.ktc_edge_total,
            "ktc_edge_per_season": self.ktc_edge_per_season,
            "sides": [
                {
                    "team_label": side.team_label,
                    "total_score": side.total_score,
                    "ktc_equiv": side.ktc_equiv,
                    "margin": self.margins.get(side.team_label, 0.0),
                    "assets": [
                        {
                            "asset_id": ev.asset_id,
                            "label": ev.asset.label,
                            "holding_start": ev.holding_start.isoformat(),
                            "holding_end": ev.holding_end.isoformat(),
                            "score": ev.total_score,
                            "integral_score": ev.integral.score,
                            "raw_area": ev.integral.raw_area,
                            "avg_ktc": ev.avg_ktc,
                            "surplus_bonus": ev.surplus_bonus,
                            "active_days": ev.integral.active_days,
                        }
                        for ev in side.asset_evaluations
                    ],
                }
                for side in self.sides
            ],
        }


# ---------------------------------------------------------------------------
# Core evaluation
# ---------------------------------------------------------------------------
def _holding_window(
    trade: Trade, asset: TradeAsset
) -> tuple[date, date]:
    """Compute (start, end) for an asset's holding window."""
    end = asset.held_until if asset.held_until is not None else trade.evaluation_end
    # Defensive: clamp end to evaluation_end (an asset can't be held past
    # the evaluation horizon, even if the caller misfilled).
    if end > trade.evaluation_end:
        end = trade.evaluation_end
    start = trade.trade_date
    return start, end


def evaluate_asset(
    asset: TradeAsset,
    *,
    trade: Trade,
    value_resolver: ValueResolver,
    surplus_bonus: SurplusBonusFn = _no_bonus,
    k: float = CONCAVITY_EXPONENT,
    calendar: ActiveCalendar = DEFAULT_CALENDAR,
) -> AssetEvaluation:
    start, end = _holding_window(trade, asset)
    series = value_resolver(asset)
    integral = integrate_value(series, start, end, k=k, calendar=calendar)
    bonus = float(surplus_bonus(asset, integral, start, end) or 0.0)
    return AssetEvaluation(
        asset=asset,
        holding_start=start,
        holding_end=end,
        integral=integral,
        surplus_bonus=bonus,
        total_score=integral.score + bonus,
    )


def evaluate_trade(
    trade: Trade,
    *,
    value_resolver: ValueResolver,
    surplus_bonus: SurplusBonusFn = _no_bonus,
    k: float = CONCAVITY_EXPONENT,
    calendar: ActiveCalendar = DEFAULT_CALENDAR,
) -> TradeEvaluation:
    """Evaluate every asset in every side and compute per-side margins.

    Margin for side ``i`` is ``score_i - mean(scores of other sides)``.
    For the common 2-side trade this collapses to "side A's margin = A - B"
    and "side B's margin = B - A", which is what people expect to see.
    """
    if not trade.sides:
        raise ValueError("Trade has no sides")

    # Trade-level active-day window: same denominator both sides use to
    # convert score -> KTC-equivalent. Computed once from the calendar so
    # offseason days don't pad it.
    trade_active_days = sum(
        1 for _ in calendar.iter_active_days(trade.trade_date, trade.evaluation_end)
    )

    side_evals: List[SideEvaluation] = []
    for side in trade.sides:
        asset_evals = [
            evaluate_asset(
                a, trade=trade, value_resolver=value_resolver,
                surplus_bonus=surplus_bonus, k=k, calendar=calendar,
            )
            for a in side.received_assets
        ]
        side_evals.append(SideEvaluation(
            side=side,
            asset_evaluations=asset_evals,
            total_score=sum(ev.total_score for ev in asset_evals),
            k=k,
            trade_active_days=trade_active_days,
        ))

    # Margin: each side vs the average of every *other* side.
    n = len(side_evals)
    margins: Dict[str, float] = {}
    if n == 1:
        margins[side_evals[0].team_label] = 0.0
    else:
        for i, side in enumerate(side_evals):
            others = [s.total_score for j, s in enumerate(side_evals) if j != i]
            margins[side.team_label] = side.total_score - (sum(others) / len(others))

    # Winner = strictly highest score; ties => no winner.
    sorted_sides = sorted(side_evals, key=lambda s: s.total_score, reverse=True)
    winner: Optional[str] = None
    if len(sorted_sides) >= 2 and sorted_sides[0].total_score > sorted_sides[1].total_score:
        winner = sorted_sides[0].team_label
    elif len(sorted_sides) == 1:
        winner = sorted_sides[0].team_label

    return TradeEvaluation(
        trade=trade,
        sides=side_evals,
        margins=margins,
        winner_label=winner,
        k=k,
        active_days=trade_active_days,
    )


# ---------------------------------------------------------------------------
# Convenience: build a value resolver from a flat ``{asset_id: {date: val}}``
# blob (the shape the historical KTC ingest produces)
# ---------------------------------------------------------------------------
def make_blob_resolver(
    value_blob: Dict[str, Dict[str, float]],
    *,
    initial_value: float = 0.0,
    max_stale_days: Optional[int] = None,
    stale_value: float = 0.0,
) -> ValueResolver:
    """Build a :class:`ValueResolver` backed by an in-memory blob.

    The blob is the shape the ingest script writes:
    ``{ asset_id: { "YYYY-MM-DD": value, ... } }``.
    """
    # Pre-build ValueSeries lazily so we don't pay for unused assets.
    _cache: Dict[str, ValueSeries] = {}

    def resolver(asset: TradeAsset) -> ValueSeries:
        aid = asset.asset_id
        if aid in _cache:
            return _cache[aid]
        raw = value_blob.get(aid)
        if raw is None and asset.sleeper_id:
            raw = value_blob.get(asset.sleeper_id)
        if raw is None:
            series = ValueSeries(sorted_dates=[], values=[],
                                 initial_value=initial_value,
                                 max_stale_days=max_stale_days,
                                 stale_value=stale_value)
        else:
            series = ValueSeries.from_mapping(
                raw, initial_value=initial_value,
                max_stale_days=max_stale_days, stale_value=stale_value,
            )
        _cache[aid] = series
        return series

    return resolver


# ---------------------------------------------------------------------------
# Race-chart builder
# ---------------------------------------------------------------------------
@dataclass
class RaceChartPoint:
    """One sample of a side's running KTC-equivalent value across the trade
    window.

    ``ktc_equiv`` is the inverse of the concavity transform applied to the
    running ``score`` (see :func:`score_to_ktc_equiv`). Because that
    transform is strictly monotonic in ``score`` for a fixed ``active_days``,
    *the chart's crossover day equals the verdict-flip day for the
    underlying integral*. That equivalence is the entire point of using
    this number for the race chart.
    """

    date: date
    score: float
    raw_area: float
    active_days: int
    ktc_equiv: float

    def to_dict(self) -> Dict[str, object]:
        return {
            "date": self.date.isoformat(),
            "score": self.score,
            "raw_area": self.raw_area,
            "active_days": self.active_days,
            "ktc_equiv": self.ktc_equiv,
        }


@dataclass
class RaceChartSide:
    """One side's full running series across the trade window."""

    team_label: str
    points: List[RaceChartPoint]

    def to_dict(self) -> Dict[str, object]:
        return {
            "team_label": self.team_label,
            "points": [p.to_dict() for p in self.points],
        }


@dataclass
class RaceChart:
    """Both sides' running series, aligned on identical timestamps, plus
    every date the running winner changes hands.

    The frontend renders this as two lines + crossover markers. Because
    both sides share the same per-step ``active_days`` denominator and
    the same concavity ``k``, any visible line crossing corresponds 1-to-1
    with the underlying integral verdict crossing.
    """

    trade_date: date
    evaluation_end: date
    k: float
    sides: List[RaceChartSide]
    crossover_dates: List[date]

    def to_dict(self) -> Dict[str, object]:
        return {
            "trade_date": self.trade_date.isoformat(),
            "evaluation_end": self.evaluation_end.isoformat(),
            "k": self.k,
            "sides": [s.to_dict() for s in self.sides],
            "crossover_dates": [d.isoformat() for d in self.crossover_dates],
        }


def _accumulate_side_points(
    side_assets: Sequence[TradeAsset],
    *,
    trade: Trade,
    value_resolver: ValueResolver,
    k: float,
    calendar: ActiveCalendar,
    step_days: int,
) -> List[CumulativePoint]:
    """Sum per-asset cumulative integrals into one series for a side.

    Each asset's holding window is clipped to its trade-side membership
    via :func:`_holding_window`, but the *chart* series is anchored on the
    trade-level ``[trade_date, evaluation_end]`` so both sides line up.
    Asset contributions outside their holding window read as zero, which
    is the same semantic the single-shot evaluator uses.
    """
    chart_start = trade.trade_date
    chart_end = trade.evaluation_end

    # Per-asset cumulative samples, padded to the chart timeline.
    per_asset: List[List[CumulativePoint]] = []
    for asset in side_assets:
        hold_start, hold_end = _holding_window(trade, asset)
        # Clip the integration to where the asset actually counts...
        asset_pts = integrate_value_cumulative(
            value_resolver(asset),
            hold_start,
            hold_end,
            k=k, calendar=calendar, step_days=step_days,
        )
        per_asset.append(asset_pts)

    # Build the master timeline from the chart bounds with the same
    # stride logic so all sides align exactly.
    timeline: List[date] = _chart_timeline(chart_start, chart_end, step_days)

    # For each timeline date, sum the latest score/raw_area/active_days
    # from each asset (forward-fill on the asset's own running totals).
    combined: List[CumulativePoint] = []
    for t in timeline:
        score = 0.0
        raw_area = 0.0
        # active_days is shared across assets on the chart timeline
        # (it's a property of the trade calendar, not the asset).
        # Compute it once from the trade-level walker.
        active_days = sum(
            1 for _ in calendar.iter_active_days(chart_start, t)
        )
        for pts in per_asset:
            sample = _running_at_or_before(pts, t)
            if sample is not None:
                score += sample.score
                raw_area += sample.raw_area
        combined.append(CumulativePoint(
            date=t, score=score, raw_area=raw_area, active_days=active_days,
        ))
    return combined


def _chart_timeline(start: date, end: date, step_days: int) -> List[date]:
    """The shared x-axis: ``start``, ``start + step``, ..., always ending at
    ``end`` (even when stride misses).
    """
    if end < start:
        return []
    stride = max(1, int(step_days))
    out = [start]
    d = start
    while True:
        d = d + _days(stride)
        if d >= end:
            break
        out.append(d)
    if out[-1] != end:
        out.append(end)
    return out


def _days(n: int):
    # Tiny helper kept module-local so we don't sprinkle timedelta imports.
    from datetime import timedelta
    return timedelta(days=n)


def _running_at_or_before(
    pts: Sequence[CumulativePoint], target: date,
) -> Optional[CumulativePoint]:
    """Return the latest cumulative point with ``date <= target``, or None
    if ``target`` precedes the asset's holding window.

    The cumulative list is already sorted ascending by date, so a linear
    scan is fine for the small N we use here (per-asset, weekly-sampled).
    """
    latest: Optional[CumulativePoint] = None
    for p in pts:
        if p.date <= target:
            latest = p
        else:
            break
    return latest


def build_race_chart(
    trade: Trade,
    *,
    value_resolver: ValueResolver,
    k: float = CONCAVITY_EXPONENT,
    calendar: ActiveCalendar = DEFAULT_CALENDAR,
    step_days: int = 7,
) -> RaceChart:
    """Produce a two-line (or N-line) running race chart for ``trade``.

    The chart's per-side ``ktc_equiv`` is derived from the same
    concavity-transformed integral the single-shot evaluator uses, so by
    construction the lines cross on the exact same day the verdict would
    flip. ``crossover_dates`` lists every such flip across the window.

    For 2-side trades the crossover set is the meaningful one. For 3+
    sides we report every date the running *first place* changes (so a
    third side overtaking second wouldn't register -- it doesn't change
    the verdict).
    """
    if not trade.sides:
        raise ValueError("Trade has no sides")

    sides_out: List[RaceChartSide] = []
    per_side_points: List[List[CumulativePoint]] = []
    for side in trade.sides:
        combined = _accumulate_side_points(
            side.received_assets,
            trade=trade, value_resolver=value_resolver,
            k=k, calendar=calendar, step_days=step_days,
        )
        per_side_points.append(combined)
        sides_out.append(RaceChartSide(
            team_label=side.team_label,
            points=[
                RaceChartPoint(
                    date=cp.date,
                    score=cp.score,
                    raw_area=cp.raw_area,
                    active_days=cp.active_days,
                    ktc_equiv=score_to_ktc_equiv(cp.score, cp.active_days, k=k),
                )
                for cp in combined
            ],
        ))

    crossovers = _find_crossovers(per_side_points)

    return RaceChart(
        trade_date=trade.trade_date,
        evaluation_end=trade.evaluation_end,
        k=k,
        sides=sides_out,
        crossover_dates=crossovers,
    )


def _find_crossovers(
    per_side_points: Sequence[Sequence[CumulativePoint]],
) -> List[date]:
    """Return every chart-timeline date where the *leading* side changes.

    Uses ``score`` (not ``ktc_equiv``) since the transform is monotonic
    and they cross at the same place; ``score`` saves an exp/log per
    point. Ties at the trade-date zero anchor are ignored.
    """
    if len(per_side_points) < 2:
        return []
    n_points = len(per_side_points[0])
    # All sides share the same timeline length by construction.
    out: List[date] = []
    prev_leader: Optional[int] = None
    for i in range(n_points):
        scores = [side[i].score for side in per_side_points]
        # Skip the all-zero anchor.
        if all(s == 0.0 for s in scores):
            continue
        leader = max(range(len(scores)), key=lambda j: scores[j])
        # Treat exact ties as "no change" so a flat opening doesn't
        # produce phantom crossovers.
        top = scores[leader]
        if sum(1 for s in scores if s == top) > 1:
            continue
        if prev_leader is not None and leader != prev_leader:
            out.append(per_side_points[0][i].date)
        prev_leader = leader
    return out


__all__ = [
    "TradeAsset",
    "TradeSide",
    "Trade",
    "ValueResolver",
    "SurplusBonusFn",
    "AssetEvaluation",
    "SideEvaluation",
    "TradeEvaluation",
    "evaluate_asset",
    "evaluate_trade",
    "make_blob_resolver",
    "RaceChartPoint",
    "RaceChartSide",
    "RaceChart",
    "build_race_chart",
    "ACTIVE_DAYS_PER_SEASON",
]
