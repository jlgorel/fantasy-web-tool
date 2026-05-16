"""Builder for the legacy ``player_season_scoring_{year}.json`` blob.

This is the per-season slim scoring blob that the Flask backend's Wrapped
pipeline reads to compute season-aware accolades (best_add / worst_drop /
draft pick grades / etc). The shape is owned by
``backend/app/services/wrapped/pipeline.py`` -- changing keys here breaks
the contract.

Extracted from ``function_app.get_sleeper_player_data`` so the same builder
can drive both:

  * the in-season weekly scrape (current behavior), and
  * a one-shot historical backfill driven by
    ``tools/bootstrap_historical_sleeper.py``.

The builder is pure (HTTP injected) so it's easily unit-testable.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Dict, Iterable, Optional, Tuple

HttpGetJson = Callable[[str], Any]
PlayersMeta = Dict[str, Dict[str, Any]]
PlayerScoring = Dict[str, Dict[str, Any]]

# Per-position (top-N-season, top-N-week) caps. Mirrors the original cap
# table in function_app.get_sleeper_player_data so the historical backfill
# produces the same shape the wrapped pipeline expects today.
POSITION_CAPS: Dict[str, Tuple[int, int]] = {
    "QB":  (50, 32),
    "WR":  (150, 70),
    "TE":  (50, 50),
    "RB":  (150, 70),
    "DEF": (32, 32),
    "K":   (50, 32),
}

# Default last week we pull (range end, exclusive). Weeks 1..13 covers the
# pre-17-game regular season + early playoff weeks; matches the original
# code's ``range(1, playoff_start_week=14)``.
DEFAULT_PLAYOFF_START_WEEK: int = 14


def _season_url(year: int, position: str) -> str:
    return (
        f"https://api.sleeper.com/stats/nfl/{year}"
        f"?season_type=regular&position={position}&order_by=pts_half_ppr"
    )


def _week_url(year: int, week: int, position: str) -> str:
    return (
        f"https://api.sleeper.com/stats/nfl/{year}/{week}"
        f"?season_type=regular&position={position}&order_by=pts_half_ppr"
    )


def build_player_scoring_data(
    year: int,
    *,
    http_get_json: HttpGetJson,
    playoff_start_week: int = DEFAULT_PLAYOFF_START_WEEK,
    position_caps: Optional[Dict[str, Tuple[int, int]]] = None,
    max_workers: int = 10,
) -> PlayerScoring:
    """Fetch per-position Sleeper season + weekly stats and return a
    ``{player_id: {scoring_data_weekly, scoring_data_season}}`` map.

    Pulls the top-N rows per position (cap table) for the season summary
    and for each regular-season week. The returned dict only contains
    players who showed up in at least one weekly or season payload.
    """
    caps = position_caps or POSITION_CAPS

    season_scoring: Dict[str, Dict[str, Any]] = {}
    weekly_scoring: Dict[str, Dict[int, Dict[str, Any]]] = defaultdict(dict)

    def _fetch_season(position: str) -> Tuple[str, list]:
        return position, http_get_json(_season_url(year, position))

    with ThreadPoolExecutor(max_workers=min(max_workers, len(caps))) as pool:
        for position, payload in pool.map(_fetch_season, caps.keys()):
            num_desired = caps[position][0]
            if not isinstance(payload, list):
                logging.info("Sleeper season payload not a list for %s/%s", year, position)
                continue
            for row in payload[:num_desired]:
                pid = row.get("player_id")
                if pid:
                    season_scoring[pid] = row.get("stats") or {}

    week_position_pairs = [
        (week, position)
        for week in range(1, playoff_start_week)
        for position in caps.keys()
    ]

    def _fetch_week(args: Tuple[int, str]) -> Tuple[int, str, list]:
        week, position = args
        return week, position, http_get_json(_week_url(year, week, position))

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        for week, position, payload in pool.map(_fetch_week, week_position_pairs):
            num_desired = caps[position][1]
            if not isinstance(payload, list):
                logging.info(
                    "Sleeper week payload not a list for %s/%s/%s", year, week, position
                )
                continue
            for row in payload[:num_desired]:
                pid = row.get("player_id")
                if pid:
                    weekly_scoring[pid][week] = row.get("stats") or {}

    out: PlayerScoring = {}
    for pid in set(weekly_scoring) | set(season_scoring):
        player_weekly = weekly_scoring.get(pid, {})
        player_season = season_scoring.get(pid, {})

        weekly: Dict[int, Dict[str, Any]] = {}
        for week, stats in player_weekly.items():
            weekly[week] = {
                "half_ppr": stats.get("pts_half_ppr", 0),
                "ppr":      stats.get("pts_ppr", 0),
                "std":      stats.get("pts_std", 0),
                "receptions": stats.get("rec", 0),
                "pass_td":  stats.get("pass_td", 0),
            }

        season: Dict[str, Any] = {
            "half_ppr_rank":   player_season.get("pos_rank_half_ppr", 999),
            "ppr_rank":        player_season.get("pos_rank_ppr", 999),
            "std_rank":        player_season.get("pos_rank_std", 999),
            "half_ppr_points": player_season.get("pts_half_ppr", 0),
            "ppr_points":      player_season.get("pts_ppr", 0),
            "std_points":      player_season.get("pts_std", 0),
            "receptions":      player_season.get("rec", 0),
        }
        out[pid] = {
            "scoring_data_weekly": weekly,
            "scoring_data_season": season,
            # Carried internally for the 6pt-passing-TD ranking pass; not
            # written to the final blob (matches the original behavior of
            # reading ``pass_td`` from the raw season payload).
            "_raw_season_pass_td": player_season.get("pass_td", 0),
        }
    return out


def attach_six_pt_passing_td_rank(
    player_scoring: PlayerScoring,
    players_meta: PlayersMeta,
) -> None:
    """In-place: add ``6pt_pass_td_points`` + ``6pt_pass_td_rank`` to QBs.

    Mirrors the original ranking pass in ``get_sleeper_player_data``. QBs
    are ranked by ``std_points + 2 * pass_td`` (i.e. 6pt passing TDs).
    """
    qb_list: list = []
    for pid, scoring in player_scoring.items():
        meta = players_meta.get(pid) or {}
        positions = meta.get("fantasy_positions") or []
        if "QB" not in positions:
            continue
        pass_td_total = scoring.get("_raw_season_pass_td", 0) or 0
        std_points = scoring["scoring_data_season"].get("std_points", 0) or 0
        six_pt = std_points + pass_td_total * 2
        scoring["scoring_data_season"]["6pt_pass_td_points"] = six_pt
        qb_list.append((pid, six_pt))

    qb_list.sort(key=lambda x: x[1], reverse=True)
    for rank, (pid, points) in enumerate(qb_list):
        player_scoring[pid]["scoring_data_season"]["6pt_pass_td_rank"] = rank
        player_scoring[pid]["scoring_data_season"]["6pt_pass_td_points"] = points


def build_legacy_season_scoring_blob(
    year: int,
    players_meta: PlayersMeta,
    *,
    http_get_json: HttpGetJson,
    playoff_start_week: int = DEFAULT_PLAYOFF_START_WEEK,
    position_caps: Optional[Dict[str, Tuple[int, int]]] = None,
    max_workers: int = 10,
) -> Dict[str, Dict[str, Any]]:
    """Produce the full ``player_season_scoring_{year}.json`` blob.

    ``players_meta`` is a ``{pid: {full_name, fantasy_positions, ...}}`` map,
    typically a slim view of the current ``/players/nfl`` snapshot. Players
    who scored historically but aren't in ``players_meta`` (e.g. retired
    pre-snapshot) still appear in the output with ``full_name=None`` and
    ``fantasy_positions=[]`` so historical accolades degrade gracefully.

    Output shape (per player)::

        {
          "full_name": "Patrick Mahomes",
          "fantasy_positions": ["QB"],
          "scoring_data_weekly": {1: {half_ppr, ppr, std, receptions, pass_td}, ...},
          "scoring_data_season": {half_ppr_rank, half_ppr_points, ...,
                                  6pt_pass_td_rank?, 6pt_pass_td_points?}
        }
    """
    scoring = build_player_scoring_data(
        year,
        http_get_json=http_get_json,
        playoff_start_week=playoff_start_week,
        position_caps=position_caps,
        max_workers=max_workers,
    )
    attach_six_pt_passing_td_rank(scoring, players_meta)

    blob: Dict[str, Dict[str, Any]] = {}
    for pid, score in scoring.items():
        meta = players_meta.get(pid) or {}
        blob[pid] = {
            "full_name": meta.get("full_name"),
            "fantasy_positions": meta.get("fantasy_positions") or [],
            "scoring_data_weekly": score["scoring_data_weekly"],
            "scoring_data_season": score["scoring_data_season"],
        }
    return blob


__all__ = [
    "POSITION_CAPS",
    "DEFAULT_PLAYOFF_START_WEEK",
    "build_player_scoring_data",
    "attach_six_pt_passing_td_rank",
    "build_legacy_season_scoring_blob",
]
