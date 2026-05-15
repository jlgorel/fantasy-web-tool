"""Active-window helper for the trade-evaluator value integral.

Per the design notes, we only integrate player value during the *active*
portion of the year -- training camp through the Super Bowl. The dead
window between mid-February and mid-July (free agency, combine, draft,
spring) is skipped because:

  1. Values barely move during it (everyone holds), so the integral
     contribution would be roughly constant per asset and wash out of
     the per-side margin anyway.
  2. The whole point of the integral is "what value did you actually
     hold when it mattered". Holding Aaron Jones for a March is not
     materially the same as holding him in Week 8.

Defaults:
  * Window start: ``Jul 15`` -- two weeks before typical training camp
    opens, captures the late-summer ramp where values shift on
    depth-chart news.
  * Window end:   ``Feb 15`` of the following calendar year -- after
    the Super Bowl, before combine moves the market.

Both can be overridden when constructing :class:`ActiveCalendar`.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Iterable, Iterator, List, Tuple, Union

# Defaults chosen so a season-Y window is roughly
#     Jul 15, Y  ->  Feb 15, Y+1
DEFAULT_WINDOW_START_MONTH: int = 7
DEFAULT_WINDOW_START_DAY: int = 15
DEFAULT_WINDOW_END_MONTH: int = 2
DEFAULT_WINDOW_END_DAY: int = 15


DateLike = Union[date, datetime, str]


def _to_date(value: DateLike) -> date:
    """Normalize a ``date | datetime | 'YYYY-MM-DD'`` input to ``date``."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        return datetime.strptime(value, "%Y-%m-%d").date()
    raise TypeError(f"Unsupported date input: {value!r}")


@dataclass(frozen=True)
class ActiveCalendar:
    """Defines the active integration windows.

    A *season-Y window* is the half-open interval [start_Y, end_{Y+1}],
    where ``start_Y`` is ``(month=start_month, day=start_day, year=Y)`` and
    ``end_{Y+1}`` is ``(month=end_month, day=end_day, year=Y+1)``. We use
    inclusive bounds on both ends -- a one-day step integral doesn't
    benefit from half-open semantics and inclusive is easier to reason
    about.
    """

    start_month: int = DEFAULT_WINDOW_START_MONTH
    start_day: int = DEFAULT_WINDOW_START_DAY
    end_month: int = DEFAULT_WINDOW_END_MONTH
    end_day: int = DEFAULT_WINDOW_END_DAY

    def season_window(self, season_year: int) -> Tuple[date, date]:
        """Return the (start, end) dates for the given season year."""
        start = date(season_year, self.start_month, self.start_day)
        end = date(season_year + 1, self.end_month, self.end_day)
        return start, end

    def is_active(self, d: DateLike) -> bool:
        """True if ``d`` falls inside any season window."""
        d = _to_date(d)
        # A date d belongs to season-Y window if either:
        #   1. d >= (start_month, start_day) of year d.year  (so season_year = d.year)
        #   2. d <= (end_month, end_day) of year d.year       (so season_year = d.year - 1)
        # We just check both candidate seasons.
        for candidate_season in (d.year - 1, d.year):
            start, end = self.season_window(candidate_season)
            if start <= d <= end:
                return True
        return False

    def iter_active_days(
        self, start: DateLike, end: DateLike
    ) -> Iterator[date]:
        """Yield every active day in the inclusive range [start, end]."""
        start = _to_date(start)
        end = _to_date(end)
        if start > end:
            return
        d = start
        while d <= end:
            if self.is_active(d):
                yield d
            d += timedelta(days=1)

    def active_intervals(
        self, start: DateLike, end: DateLike
    ) -> List[Tuple[date, date]]:
        """Return contiguous active sub-intervals inside [start, end].

        Useful for callers that want to integrate piecewise (one trapezoid
        per active stretch) rather than one-day-at-a-time.
        """
        start = _to_date(start)
        end = _to_date(end)
        out: List[Tuple[date, date]] = []
        if start > end:
            return out

        cur_start: date | None = None
        prev: date | None = None
        for d in self.iter_active_days(start, end):
            if cur_start is None:
                cur_start = d
                prev = d
                continue
            assert prev is not None
            if (d - prev).days == 1:
                prev = d
                continue
            # Gap: close current interval, start new one.
            out.append((cur_start, prev))
            cur_start = d
            prev = d
        if cur_start is not None and prev is not None:
            out.append((cur_start, prev))
        return out


# Module-level default for callers that don't want to construct one.
DEFAULT_CALENDAR = ActiveCalendar()


def is_active(d: DateLike, calendar: ActiveCalendar = DEFAULT_CALENDAR) -> bool:
    return calendar.is_active(d)


def iter_active_days(
    start: DateLike, end: DateLike, calendar: ActiveCalendar = DEFAULT_CALENDAR
) -> Iterator[date]:
    return calendar.iter_active_days(start, end)


__all__ = [
    "DEFAULT_WINDOW_START_MONTH",
    "DEFAULT_WINDOW_START_DAY",
    "DEFAULT_WINDOW_END_MONTH",
    "DEFAULT_WINDOW_END_DAY",
    "ActiveCalendar",
    "DEFAULT_CALENDAR",
    "is_active",
    "iter_active_days",
]
