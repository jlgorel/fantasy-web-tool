"""Replacement-value calculator (a.k.a. "baseline player").

For each fantasy position we compute the points scored by the *replacement-
level* player in this league — the player ranked just below the last
starter that all teams could field. ``best_add`` and ``worst_drop`` use the
delta vs. this baseline to surface adds/drops that mattered.

Mirrors ``SleeperLeagueAnalyzer.get_baseline_players_and_scores`` but is
now a pure function so it's easy to unit-test without spinning up the
whole Sleeper context.
"""
from __future__ import annotations

import math
from collections import defaultdict
from typing import Dict, List

from app.services.wrapped.league_context import LeagueContext


def _scoring_keys_for_pos(qb_score_key: str, skill_score_key: str, pos: str) -> tuple[str, str]:
    """Return (rank_key, points_key) for a fantasy position."""
    if pos == "QB":
        return f"{qb_score_key}_rank", f"{qb_score_key}_points"
    return f"{skill_score_key}_rank", f"{skill_score_key}_points"


def _count_groups(roster_groups: List[List[str]]) -> Dict[str, int]:
    """Count starting slots by role.

    Heuristic: a group is identified by what it represents in the lineup,
    not by literal Sleeper position name (which varies — FLEX, REC_FLEX,
    SUPER_FLEX, etc).

    * ``["QB"]`` -> qb
    * ``["RB"]`` -> rb
    * ``["WR"]`` -> wr
    * ``["TE"]`` -> te
    * Any group containing both RB and WR but **not** QB -> flex
      (these contribute 1/3 to RB baseline, 2/3 to WR baseline by
      convention — matches the original analyzer.)
    * Other shapes (REC_FLEX = WR/TE; SUPER_FLEX = QB/RB/WR/TE) are
      ignored from baseline math; the four canonical positions cover the
      vast majority of leagues, and contributing FLEX-type slots biases
      the baseline toward standard expectations.
    """
    counts = {"qb": 0, "rb": 0, "wr": 0, "te": 0, "flex": 0}
    for group in roster_groups:
        gset = set(group)
        if group == ["QB"]:
            counts["qb"] += 1
        elif group == ["RB"]:
            counts["rb"] += 1
        elif group == ["WR"]:
            counts["wr"] += 1
        elif group == ["TE"]:
            counts["te"] += 1
        elif "RB" in gset and "WR" in gset and "QB" not in gset:
            counts["flex"] += 1
    return counts


def compute_baseline_player_scoring(
    ctx: LeagueContext,
    season_scoring: Dict[str, Dict],
) -> Dict[str, float]:
    """Return a dict mapping ``"QB" | "RB" | "WR" | "TE"`` to the
    replacement-level points-scored for that position in this league.

    ``season_scoring`` is the year-stamped ``player_season_scoring_{year}.json``
    blob shape (or any subset that carries each player's
    ``fantasy_positions`` + ``scoring_data_season``).
    """
    teams = ctx.total_rosters or 0
    counts = _count_groups(ctx.roster_positions_groups)

    # Position rank -> points scored. Built once per call so a 500-player
    # blob is iterated once.
    pos_rank_to_points: Dict[str, Dict[int, float]] = defaultdict(dict)
    for _pid, info in (season_scoring or {}).items():
        positions = info.get("fantasy_positions") or []
        if not positions:
            continue
        # Taysom Hill is a known one-off — fantasy_positions[0] is "QB" but
        # he's almost universally rostered as a TE in scoring.
        full_name = (info.get("full_name") or "").strip()
        primary = positions[0] if full_name != "Taysom Hill" else "TE"
        if primary not in ("QB", "RB", "WR", "TE"):
            continue
        rank_key, points_key = _scoring_keys_for_pos(
            ctx.qb_score_key, ctx.skill_score_key, primary
        )
        season = info.get("scoring_data_season") or {}
        rank = season.get(rank_key)
        points = season.get(points_key)
        if rank is None or points is None:
            continue
        try:
            pos_rank_to_points[primary][int(rank)] = float(points)
        except (TypeError, ValueError):
            continue

    # Replacement rank = first player below the last "starter" everyone
    # could field. e.g. 12 teams * 1 RB + 12 * 1/3 FLEX (≈ 4) = rank 17 →
    # baseline = the player at rank 17.
    baseline_rank = {
        "QB": counts["qb"] * teams + 1,
        "RB": math.ceil(counts["rb"] * teams + counts["flex"] * (1 / 3) * teams) + 1,
        "WR": math.ceil(counts["wr"] * teams + counts["flex"] * (2 / 3) * teams) + 1,
        "TE": counts["te"] * teams + 1,
    }

    baseline_points: Dict[str, float] = {}
    for pos, target_rank in baseline_rank.items():
        baseline_points[pos] = float(pos_rank_to_points.get(pos, {}).get(target_rank, 0.0))
    return baseline_points
