"""Parallel matchups fetcher for the Wrapped pipeline.

Builds the same per-week tables the original ``SleeperLeagueAnalyzer`` did
in its ``_process_weekly_scores`` loop, but with concurrent HTTP fan-out so
a 14-week season completes in ~1 second instead of ~3.

All output is keyed by **username** (display name) rather than roster_id —
the frontend renders user names, and keying here means each accolade
function can stay pure.
"""
from __future__ import annotations

import logging
import statistics
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Dict, List, Tuple

from app.services.http_utils import fetch_json
from app.services.wrapped.league_context import LeagueContext

logger = logging.getLogger(__name__)


@dataclass
class WeeklyScores:
    """Aggregated per-week scoring tables. All dicts keyed by username.

    Attribute names match the SleeperLeagueAnalyzer fields so accolade
    functions can be ported verbatim.
    """

    user_score_by_week: Dict[str, Dict[int, float]] = field(default_factory=lambda: defaultdict(dict))
    opponent_score_by_week: Dict[str, Dict[int, float]] = field(default_factory=lambda: defaultdict(dict))
    user_results_by_week: Dict[str, Dict[int, str]] = field(default_factory=lambda: defaultdict(dict))
    user_best_ball_score_by_week: Dict[str, Dict[int, float]] = field(default_factory=lambda: defaultdict(dict))
    median_scores: Dict[int, float] = field(default_factory=dict)
    # username -> player_id -> {"start": [pts...], "bench": [pts...]}
    user_player_start_sit_points: Dict[str, Dict[str, Dict[str, List[float]]]] = field(
        default_factory=lambda: defaultdict(lambda: defaultdict(lambda: {"start": [], "bench": []}))
    )
    # username -> position -> week -> sum of starter points at that position
    # in that week. Used by the "Best streamers" section to roll up per-user
    # K + DEF averages across the season. Bucketed at write-time so we don't
    # have to re-walk every starter and re-resolve positions later.
    user_position_starter_points_by_week: Dict[str, Dict[str, Dict[int, float]]] = field(
        default_factory=lambda: defaultdict(lambda: defaultdict(dict))
    )

    @property
    def usernames(self) -> List[str]:
        return list(self.user_score_by_week.keys())

    @property
    def weeks_played(self) -> List[int]:
        if not self.user_score_by_week:
            return []
        # Take the union; some users may have a bye-style 0 score we still
        # want represented. Sorted for stable iteration in tests.
        weeks: set[int] = set()
        for byweek in self.user_score_by_week.values():
            weeks.update(byweek.keys())
        return sorted(weeks)


def _calculate_optimal_lineup(
    sorted_player_lists: Dict[str, List[Tuple[str, float]]],
    roster_position_groups: List[List[str]],
) -> Tuple[List[Tuple[str, float]], float]:
    """Greedy best-ball: walk roster slots in order, take the highest-scoring
    eligible player for each slot, and remove them from the pool.

    Mirrors the original analyzer's ``_calculate_optimal_lineup`` exactly so
    the ``user_best_ball_score_by_week`` totals stay comparable to the legacy
    output.

    ``sorted_player_lists`` is **mutated** (``pop(0)`` on the chosen list);
    callers that need to reuse it should pass a deep copy.
    """
    optimal_lineup: List[Tuple[str, float]] = []
    max_score = 0.0

    for position_group in roster_position_groups:
        best_points = -100.0
        best_player_tuple: Tuple[str, float] = ("N/A", 0.0)
        best_player_position = "none"

        for position in position_group:
            bucket = sorted_player_lists.get(position) or []
            if bucket:
                candidate = bucket[0]
                if candidate[1] > best_points:
                    best_points = candidate[1]
                    best_player_tuple = candidate
                    best_player_position = position
            else:
                if best_points == -100.0:
                    best_points = 0.0

        optimal_lineup.append(best_player_tuple)
        if best_points > 0:
            max_score += best_points
        if best_player_position in sorted_player_lists and sorted_player_lists[best_player_position]:
            sorted_player_lists[best_player_position].pop(0)

    return optimal_lineup, max_score


def _process_week_matchups(
    matchups: List[Dict[str, Any]],
    week: int,
    ctx: LeagueContext,
    players_meta: Dict[str, Dict[str, Any]],
    out: WeeklyScores,
) -> None:
    """Mutate ``out`` with the results of a single week. Pure aside from
    that mutation; can be called in any order across weeks."""
    weekly_score_list: List[float] = []
    matchup_pairs: Dict[int, List[Tuple[str, float]]] = defaultdict(list)

    for matchup in matchups:
        roster_id = matchup["roster_id"]
        username = ctx.roster_id_to_username.get(roster_id, f"<roster-{roster_id}>")
        points = float(matchup.get("points") or 0.0)
        weekly_score_list.append(points)

        out.user_score_by_week[username][week] = points

        matchup_id = matchup.get("matchup_id")
        if matchup_id is not None:
            matchup_pairs[matchup_id].append((username, points))

        # Build a {pid: {Points, Position}} dict for best-ball + start-sit.
        starters = matchup.get("starters") or []
        all_players = matchup.get("players") or []
        bench_players = list(set(all_players) - set(starters))
        players_points = matchup.get("players_points") or {}

        sorted_player_lists: Dict[str, List[Tuple[str, float]]] = defaultdict(list)
        for pid in all_players:
            if not pid or pid == "0":
                continue
            pmeta = players_meta.get(pid) or {}
            positions = pmeta.get("fantasy_positions") or []
            position = positions[0] if positions else "UNK"
            name = pmeta.get("full_name") or pid
            score = float(players_points.get(pid, 0) or 0)
            sorted_player_lists[position].append((name, score))

        for bucket in sorted_player_lists.values():
            bucket.sort(key=lambda x: x[1], reverse=True)

        for pid in starters:
            if not pid or pid == "0":
                continue
            score = float(players_points.get(pid, 0) or 0)
            out.user_player_start_sit_points[username][pid]["start"].append(score)
            # Roll up by position so the streamers section can average
            # K / DEF starter scoring across the season without re-walking
            # players_meta later. Multiple starters at the same position
            # (e.g. two RBs + a flex RB) sum into the same week bucket.
            pmeta = players_meta.get(pid) or {}
            positions_for_pid = pmeta.get("fantasy_positions") or []
            if positions_for_pid:
                primary_pos = positions_for_pid[0]
                bucket = out.user_position_starter_points_by_week[username][primary_pos]
                bucket[week] = bucket.get(week, 0.0) + score
        for pid in bench_players:
            if not pid or pid == "0":
                continue
            score = float(players_points.get(pid, 0) or 0)
            # Skip true byes / inactives so they don't fool the troll metric.
            if score == 0.0:
                continue
            out.user_player_start_sit_points[username][pid]["bench"].append(score)

        _, best_ball_score = _calculate_optimal_lineup(
            sorted_player_lists, ctx.roster_positions_groups
        )
        out.user_best_ball_score_by_week[username][week] = best_ball_score

    # Pair up opponents.
    for pair in matchup_pairs.values():
        if len(pair) != 2:
            continue
        (u1, p1), (u2, p2) = pair
        out.opponent_score_by_week[u1][week] = p2
        out.opponent_score_by_week[u2][week] = p1
        if p1 == p2:
            out.user_results_by_week[u1][week] = "T"
            out.user_results_by_week[u2][week] = "T"
        else:
            out.user_results_by_week[u1][week] = "W" if p1 > p2 else "L"
            out.user_results_by_week[u2][week] = "W" if p2 > p1 else "L"

    if weekly_score_list:
        out.median_scores[week] = float(statistics.median(weekly_score_list))


def fetch_weekly_scores(
    ctx: LeagueContext, players_meta: Dict[str, Dict[str, Any]]
) -> WeeklyScores:
    """Fetch all weeks' matchups in parallel and build the WeeklyScores."""
    out = WeeklyScores()
    weeks = list(range(1, ctx.last_regular_season_week + 1))
    if not weeks:
        logger.info("Wrapped: no regular-season weeks scored yet for %s", ctx.league_id)
        return out

    def _fetch_one(week: int) -> Tuple[int, List[Dict[str, Any]]]:
        url = f"https://api.sleeper.app/v1/league/{ctx.league_id}/matchups/{week}"
        return week, (fetch_json(url) or [])

    # Sleeper handles ~10 concurrent calls fine; cap at 8 to be polite.
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(_fetch_one, weeks))

    # Process in week order so any tie-breaker logic that depends on
    # iteration order (none today, but keeps tests stable) is deterministic.
    results.sort(key=lambda x: x[0])
    for week, matchups in results:
        _process_week_matchups(matchups, week, ctx, players_meta, out)

    return out
