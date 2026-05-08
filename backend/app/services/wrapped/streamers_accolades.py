"""Best Streamers accolades for the Wrapped pipeline.

Aggregates per-user starter scoring at the K and DEF positions across the
regular season. Surfaces:

    * per-user averages (kicker / defense / combined) for the table
    * league-leader for each metric (used as the section's "Best K Streamer"
      / "Best DEF Streamer" hero callouts)

Design notes:
    * "Combined" only matters when the league has both K and DEF roster
      slots; pure-K leagues see a 2-column table, etc. The pipeline tells
      us which positions to surface via ``positions_to_include``.
    * We average over the union of weeks the user actually played
      (``weeks_played``). Bye weeks where the user fielded a 0 are still
      averaged in — fielding a kicker who got benched IS bad streaming.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from app.services.wrapped.schedule import WeeklyScores


_STREAM_POSITIONS = ("K", "DEF")


@dataclass
class StreamerEntry:
    username: str
    k_avg: Optional[float] = None
    def_avg: Optional[float] = None
    combined_avg: Optional[float] = None
    weeks_counted: int = 0


@dataclass
class StreamersPayload:
    positions_included: List[str] = field(default_factory=list)
    by_user: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    best_kicker: Optional[Dict[str, Any]] = None
    best_defense: Optional[Dict[str, Any]] = None
    best_combined: Optional[Dict[str, Any]] = None


def _league_has_position(roster_groups: List[List[str]], pos: str) -> bool:
    """True iff the league has a roster slot that exclusively requires ``pos``.

    A FLEX slot doesn't count — kickers + defenses aren't FLEX-eligible in
    Sleeper. We just check if ``pos`` shows up alone in any roster slot.
    """
    return any(len(group) == 1 and group[0] == pos for group in roster_groups)


def _avg_position_starts(
    scores: WeeklyScores, username: str, position: str
) -> Optional[float]:
    """Average starter points at ``position`` across all weeks the user
    fielded a lineup. Returns ``None`` if the user never started anyone
    at this position (i.e. league doesn't roster it)."""
    by_pos = scores.user_position_starter_points_by_week.get(username, {})
    week_to_pts = by_pos.get(position) or {}
    if not week_to_pts:
        return None
    weeks_played = scores.user_score_by_week.get(username, {}).keys()
    if not weeks_played:
        return None
    total = sum(float(week_to_pts.get(w, 0.0)) for w in weeks_played)
    return round(total / len(weeks_played), 2)


def calculate_streamer_accolades(
    scores: WeeklyScores,
    roster_position_groups: List[List[str]],
) -> StreamersPayload:
    """Compute per-user K/DEF averages + section-level winners.

    Skips positions the league doesn't actually roster — a 1-K league
    won't render a DEF column, and vice versa. If the league rosters
    neither, returns an empty payload (the section gets hidden upstream).
    """
    positions_included = [
        pos for pos in _STREAM_POSITIONS
        if _league_has_position(roster_position_groups, pos)
    ]
    out = StreamersPayload(positions_included=positions_included)
    if not positions_included or not scores.usernames:
        return out

    has_k = "K" in positions_included
    has_def = "DEF" in positions_included
    show_combined = has_k and has_def

    entries: List[StreamerEntry] = []
    for user in scores.usernames:
        weeks_played = len(scores.user_score_by_week.get(user, {}))
        entry = StreamerEntry(username=user, weeks_counted=weeks_played)
        if has_k:
            entry.k_avg = _avg_position_starts(scores, user, "K")
        if has_def:
            entry.def_avg = _avg_position_starts(scores, user, "DEF")
        if show_combined:
            # Sum the two per-position averages directly. Equivalent to
            # averaging (K + DEF) per week as long as both positions share
            # the same denominator (weeks_played), which they do.
            k = entry.k_avg or 0.0
            d = entry.def_avg or 0.0
            entry.combined_avg = round(k + d, 2)
        entries.append(entry)

    out.by_user = {
        e.username: {
            "k_avg": e.k_avg,
            "def_avg": e.def_avg,
            "combined_avg": e.combined_avg,
            "weeks_counted": e.weeks_counted,
        }
        for e in entries
    }

    def _best_by(attr: str) -> Optional[Dict[str, Any]]:
        ranked = [(e.username, getattr(e, attr)) for e in entries
                  if getattr(e, attr) is not None]
        if not ranked:
            return None
        username, value = max(ranked, key=lambda x: x[1])
        return {"username": username, "average": value}

    if has_k:
        out.best_kicker = _best_by("k_avg")
    if has_def:
        out.best_defense = _best_by("def_avg")
    if show_combined:
        out.best_combined = _best_by("combined_avg")

    return out
