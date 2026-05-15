"""Value-time integral for trade evaluation.

Implements the core math described in the design notes:

.. math::
    S_a = \\int_{t_0}^{t_1} v_a(t)^k \\cdot \\mathbb{1}_{\\text{active}}(t)
    \\, dt

Where:
  * ``v_a(t)`` is the asset's value time series (KTC or FantasyCalc-equivalent
    units, whatever the value-resolver returns).
  * ``k`` (``CONCAVITY_EXPONENT``) is the superstar-premium power transform.
    Default 1.4. Bake the "5 mid guys < 1 stud" effect into the math so
    quantity-over-quality trades don't read as fair.
  * ``[t_0, t_1]`` is the holding window for the asset (trade date through
    asset-leaves-roster / today / retirement).
  * ``active`` restricts integration to in-season days (see
    :mod:`trade_eval.active_window`) so offseason "value parking" doesn't
    contribute area.

The integration is a trapezoidal sum over the active days in the holding
window with a 1-day step, fed by a forward-fill resolver over the asset's
sparse ``{date: value}`` series. That's accurate to better than 0.1%
versus a continuous integral for the curves we care about and trivial to
audit.

This module is pure -- no IO, no Azure -- so it's easy to unit test and to
reuse from notebooks.
"""
from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from .active_window import ActiveCalendar, DEFAULT_CALENDAR, _to_date, DateLike

# Default concavity exponent used to apply the superstar premium.
#
# Why 2.5: a side's score is the additive L_k norm of its assets, so an
# N-asset bundle reads as roughly ``N^(1/k) * avg_value``. With k=1.4 a
# 4-asset bundle gets a 2.7x "free" multiplier on its average value,
# which is divorced from roster reality -- you can only start 2-3 flex
# spots, so depth past that is insurance, not value. k=2.5 gives a
# 4-asset bundle a 1.74x multiplier, which empirically matches lookback
# verdicts on real trades (see tools/inspect_josh_allen_trade.py: the
# 2021 Kupp+Edmonds+Cook+Garoppolo-for-Josh-Allen trade flips to Allen
# in late 2024 at k=2.5, which matches the lived experience that JA had
# clearly won by 2024-2025).
#
# Higher (3.0+) starts ignoring meaningful depth; lower (1.5-) fails on
# the quantity-vs-quality test. 2.5 is the empirical sweet spot.
CONCAVITY_EXPONENT: float = 2.5


# ---------------------------------------------------------------------------
# Sparse time-series sampler
# ---------------------------------------------------------------------------
@dataclass
class ValueSeries:
    """A sparse, sorted ``{date: value}`` time series with forward-fill
    sampling.

    Forward fill is the right semantic here: KTC publishes daily but
    individual rookies only appear after they're drafted; older players
    who fall outside the top-500 leave the file. Either way, the *last
    known* value is the best estimate going forward until something
    changes. Returns 0.0 before the first known date (asset didn't exist
    yet from the market's perspective) unless ``initial_value`` is set.
    """

    sorted_dates: Sequence[date]
    values: Sequence[float]
    initial_value: float = 0.0
    # Optional max age (days) for forward-fill. If a sample is older than
    # this when we ask, fall back to ``stale_value``. Useful for retired
    # players whose series just stops -- we want the integral to stop
    # contributing once they're truly off the table. ``None`` disables.
    max_stale_days: Optional[int] = None
    stale_value: float = 0.0

    @classmethod
    def from_mapping(
        cls,
        series: Mapping[DateLike, float],
        *,
        initial_value: float = 0.0,
        max_stale_days: Optional[int] = None,
        stale_value: float = 0.0,
    ) -> "ValueSeries":
        """Build a ValueSeries from a ``{date_or_str: value}`` map."""
        if not series:
            return cls(sorted_dates=[], values=[],
                       initial_value=initial_value,
                       max_stale_days=max_stale_days,
                       stale_value=stale_value)
        pairs = sorted((_to_date(d), float(v)) for d, v in series.items()
                       if v is not None)
        dates = [d for d, _ in pairs]
        values = [v for _, v in pairs]
        return cls(sorted_dates=dates, values=values,
                   initial_value=initial_value,
                   max_stale_days=max_stale_days,
                   stale_value=stale_value)

    def value_on(self, d: DateLike) -> float:
        """Forward-fill sample of the series at date ``d``."""
        d = _to_date(d)
        if not self.sorted_dates:
            return self.initial_value
        # bisect_right gives the insertion point; the last value <= d is
        # at index (insertion_point - 1).
        idx = bisect_right(self.sorted_dates, d) - 1
        if idx < 0:
            return self.initial_value
        sample_date = self.sorted_dates[idx]
        if (
            self.max_stale_days is not None
            and (d - sample_date).days > self.max_stale_days
        ):
            return self.stale_value
        return self.values[idx]


# ---------------------------------------------------------------------------
# Integration
# ---------------------------------------------------------------------------
@dataclass
class IntegralResult:
    """Output of :func:`integrate_value`.

    ``score`` is the canonical number used for trade comparison. ``raw_area``
    is the un-exponentiated value-day integral (k=1) for diagnostic
    display. ``active_days`` and ``total_days`` give context for how much
    of the holding window actually counted.
    """

    score: float
    raw_area: float
    active_days: int
    total_days: int
    daily_samples: List[Tuple[date, float]] = field(default_factory=list)


def integrate_value(
    series: ValueSeries,
    start: DateLike,
    end: DateLike,
    *,
    k: float = CONCAVITY_EXPONENT,
    calendar: ActiveCalendar = DEFAULT_CALENDAR,
    keep_daily_samples: bool = False,
) -> IntegralResult:
    """Compute the value-time integral for a single asset over [start, end].

    Inactive days (offseason) contribute zero. The score is the sum of
    ``v(t)^k`` across active days, with a 1-day step. ``raw_area`` is the
    same sum without the power transform, in plain "value-days".

    ``keep_daily_samples`` populates :attr:`IntegralResult.daily_samples`
    with one (date, value) tuple per active day. Useful for plots and
    debugging; off by default to keep memory tidy.
    """
    start = _to_date(start)
    end = _to_date(end)
    if end < start:
        return IntegralResult(0.0, 0.0, 0, 0)

    score = 0.0
    raw_area = 0.0
    active_days = 0
    total_days = (end - start).days + 1
    samples: List[Tuple[date, float]] = []

    d = start
    while d <= end:
        if calendar.is_active(d):
            v = series.value_on(d)
            if v > 0.0:
                # Negative values aren't meaningful here; clamp at 0.
                score += v ** k
                raw_area += v
            active_days += 1
            if keep_daily_samples:
                samples.append((d, v))
        d += timedelta(days=1)

    return IntegralResult(
        score=score,
        raw_area=raw_area,
        active_days=active_days,
        total_days=total_days,
        daily_samples=samples,
    )


# ---------------------------------------------------------------------------
# Convenience: integrate a whole bundle of assets in one shot
# ---------------------------------------------------------------------------
def integrate_assets(
    asset_series: Mapping[str, ValueSeries],
    start: DateLike,
    end: DateLike,
    *,
    k: float = CONCAVITY_EXPONENT,
    calendar: ActiveCalendar = DEFAULT_CALENDAR,
) -> Dict[str, IntegralResult]:
    """Run :func:`integrate_value` once per asset, returning a results map."""
    return {
        asset_id: integrate_value(series, start, end, k=k, calendar=calendar)
        for asset_id, series in asset_series.items()
    }


__all__ = [
    "CONCAVITY_EXPONENT",
    "ValueSeries",
    "IntegralResult",
    "CumulativePoint",
    "integrate_value",
    "integrate_assets",
    "integrate_value_cumulative",
    "score_to_ktc_equiv",
]


# ---------------------------------------------------------------------------
# Cumulative integral (for race-chart rendering)
# ---------------------------------------------------------------------------
@dataclass
class CumulativePoint:
    """One sample of a running value-time integral.

    All three fields are the *running* totals up to and including ``d``:

    * ``score`` -- the concavity-exponentiated integral so far. This is
      what feeds the race chart, because diffs of ``ktc_equiv`` derived
      from it preserve verdict ordering exactly.
    * ``raw_area`` -- the un-exponentiated value-day area, useful for
      sanity-checking and as an "if k were 1.0" diagnostic line.
    * ``active_days`` -- count of active (in-season) days included so
      far. Required to convert ``score`` back to a KTC-equivalent rate
      via :func:`score_to_ktc_equiv`.
    """

    date: date
    score: float
    raw_area: float
    active_days: int


def integrate_value_cumulative(
    series: ValueSeries,
    start: DateLike,
    end: DateLike,
    *,
    k: float = CONCAVITY_EXPONENT,
    calendar: ActiveCalendar = DEFAULT_CALENDAR,
    step_days: int = 7,
) -> List[CumulativePoint]:
    """Return a running integral of ``series`` over ``[start, end]``.

    Same per-day loop as :func:`integrate_value` -- same calendar, same
    concavity exponent -- but emits a ``CumulativePoint`` every
    ``step_days`` days (and always at ``end``). That guarantees the
    race-chart's per-step ``ktc_equiv`` is consistent with the
    single-number verdict: at the final timestamp, ``score`` matches
    :attr:`IntegralResult.score` to floating-point precision.

    ``step_days`` controls the chart granularity. The default of 7 keeps
    a multi-year window under ~150 points (plenty for an SVG line) while
    still resolving in-season weekly waves.

    Endpoints rules:

    * The first emitted point is at ``start`` (zero everything, anchors
      the chart's left edge).
    * Points are then emitted every ``step_days`` calendar days.
    * The final point is always at ``end``, even if it doesn't fall on
      the stride -- so a race chart cannot disagree with the verdict at
      the trade-window boundary.
    """
    start = _to_date(start)
    end = _to_date(end)
    out: List[CumulativePoint] = []
    if end < start:
        return out

    stride = max(1, int(step_days))
    # Anchor the chart at the trade date with everything zero.
    out.append(CumulativePoint(date=start, score=0.0, raw_area=0.0, active_days=0))

    score = 0.0
    raw_area = 0.0
    active_days = 0
    days_since_emit = 0

    d = start
    while d <= end:
        if calendar.is_active(d):
            v = series.value_on(d)
            if v > 0.0:
                score += v ** k
                raw_area += v
            active_days += 1

        days_since_emit += 1
        if d != start and (days_since_emit >= stride or d == end):
            out.append(CumulativePoint(
                date=d,
                score=score,
                raw_area=raw_area,
                active_days=active_days,
            ))
            days_since_emit = 0
        d += timedelta(days=1)

    # Guarantee the final point lands on ``end`` (handles the case where
    # the stride coincided with end and the second branch already fired,
    # AND the case where start == end and the loop emitted nothing).
    if not out or out[-1].date != end:
        out.append(CumulativePoint(
            date=end,
            score=score,
            raw_area=raw_area,
            active_days=active_days,
        ))
    return out


# ---------------------------------------------------------------------------
# Readability transform: integral score -> KTC-equivalent value
# ---------------------------------------------------------------------------
def score_to_ktc_equiv(
    score: float, active_days: int, k: float = CONCAVITY_EXPONENT,
) -> float:
    """Convert a concavity-transformed integral score back to a KTC-equivalent
    value on the familiar ~0-9999 scale.

    Definition: the constant KTC value ``v*`` that, integrated over the same
    active-day window with the same concavity exponent ``k``, would produce
    ``score``. Solving ``score = v*^k * active_days`` for ``v*`` gives::

        v* = (score / active_days) ^ (1 / k)

    Properties (the reason the trade-evaluator UI uses this):

      * Inverse-and-monotonic: if ``score_A > score_B`` then
        ``ktc_equiv_A > ktc_equiv_B``, since ``x^(1/k)`` is monotonic for
        ``x >= 0`` and both sides divide by the same ``active_days``.
        => any "race-chart" rendered with this transform crosses on the
        exact same day the underlying verdict flips.

      * Sum-preserving across assets on the same side: a side's
        ``score = sum(score_a)`` (concavity already baked in), so applying
        the transform to the *side total* gives "how much constant KTC
        value, on average, did this side hold across active days." That's
        the number people can intuit.

    Returns ``0.0`` when ``active_days <= 0`` (degenerate window) or when
    ``score <= 0`` (no value held).
    """
    if active_days <= 0 or score <= 0.0:
        return 0.0
    return (score / float(active_days)) ** (1.0 / float(k))
