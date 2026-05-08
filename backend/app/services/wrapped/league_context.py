"""League-context loader for the Wrapped pipeline.

Encapsulates the four Sleeper REST calls every Wrapped run starts with:
    * ``GET /league/{id}``          — settings + roster_positions
    * ``GET /league/{id}/users``    — user_id → display_name map
    * ``GET /league/{id}/rosters``  — roster_id → owner_id map
    * ``GET /league/{id}/drafts``   — first draft's ``scoring_type`` (used to
      detect dynasty + 2QB without inferring it from positions)

Returned in a single ``LeagueContext`` dataclass so the rest of the pipeline
can lean on attribute access instead of dragging four dicts around.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List

from app.services.http_utils import fetch_json


@dataclass(frozen=True)
class LeagueContext:
    """Static context for a league + season — all data here is invariant
    once the season is locked, so the entire context object is safely
    cacheable in Redis under one key."""

    league_id: str
    year: str
    league_settings: Dict[str, Any]
    user_id_to_username: Dict[str, str]
    roster_id_to_username: Dict[int, str]
    is_dynasty: bool
    num_qbs: str  # "1" or "2", matches FantasyCalc's expected query value
    playoff_week_start: int
    last_scored_week: int
    # Inclusive upper bound for regular-season iteration. Mirrors the
    # original analyzer's ``self.week_to_loop_to`` minus 1, but exposed
    # as the inclusive last week so loops read as ``range(1, last_week+1)``.
    last_regular_season_week: int
    roster_positions_groups: List[List[str]]  # e.g. [["QB"], ["RB"], ["RB","WR","TE"], ...]
    scoring_settings: Dict[str, float]
    total_rosters: int
    qb_score_key: str  # "std" or "6pt_pass_td"
    skill_score_key: str  # "std" / "half_ppr" / "ppr"
    # Username -> list of pids currently rostered. Used by Phase-2 roster-move
    # accolades (worst_drop / best_add need to know who's still on each team).
    current_rosters: Dict[str, List[str]]


_FLEX_GROUPS: Dict[str, List[str]] = {
    "FLEX": ["RB", "WR", "TE"],
    "REC_FLEX": ["WR", "TE"],
    "SUPER_FLEX": ["QB", "WR", "TE", "RB"],
    "SUPER_FLEX_2": ["QB", "WR", "TE", "RB"],  # tolerate misspellings/variants
    "WRRB_FLEX": ["RB", "WR"],
}


def _pos_to_group(pos: str) -> List[str]:
    return _FLEX_GROUPS.get(pos, [pos])


def _detect_qb_score_key(scoring_settings: Dict[str, float]) -> str:
    return "6pt_pass_td" if scoring_settings.get("pass_td", 4) > 5 else "std"


def _detect_skill_score_key(scoring_settings: Dict[str, float]) -> str:
    rec = scoring_settings.get("rec", 0) or 0
    if rec < 0.25:
        return "std"
    if rec < 0.75:
        return "half_ppr"
    return "ppr"


def _detect_dynasty_and_qbs(league_id: str) -> tuple[bool, str]:
    """Read the league's first draft and look at ``metadata.scoring_type``.

    Returns ``(is_dynasty, num_qbs)`` where ``num_qbs`` is the string
    ``"1"`` or ``"2"`` (matches FantasyCalc's expected query parameter).
    Falls back to ``(False, "1")`` when the draft endpoint or metadata
    is missing — never raises, since Wrapped is read-only and degrading
    gracefully is preferred to 500ing.
    """
    try:
        drafts = fetch_json(f"https://api.sleeper.app/v1/league/{league_id}/drafts")
        if not drafts:
            return False, "1"
        scoring_type = drafts[0].get("metadata", {}).get("scoring_type", "")
    except Exception:
        return False, "1"
    return ("dynasty" in scoring_type), ("2" if "2qb" in scoring_type else "1")


def load_league_context(league_id: str, year: str) -> LeagueContext:
    """Fetch and assemble the static context for one league + season."""
    settings = fetch_json(f"https://api.sleeper.app/v1/league/{league_id}")
    users = fetch_json(f"https://api.sleeper.app/v1/league/{league_id}/users")
    rosters = fetch_json(f"https://api.sleeper.app/v1/league/{league_id}/rosters")

    user_id_to_username = {u["user_id"]: u["display_name"] for u in users}
    roster_id_to_username: Dict[int, str] = {}
    current_rosters: Dict[str, List[str]] = {}
    for r in rosters:
        owner = r.get("owner_id")
        # Orphaned/empty rosters (no owner) get a synthetic placeholder so
        # downstream lookups don't KeyError. They show up in matchups with
        # 0-pt scores and are visually filtered on the frontend.
        roster_id_to_username[r["roster_id"]] = (
            user_id_to_username.get(owner) or f"<roster-{r['roster_id']}>"
        )
        current_rosters[roster_id_to_username[r["roster_id"]]] = list(r.get("players") or [])

    sleeper_settings = settings.get("settings", {}) or {}
    scoring = settings.get("scoring_settings", {}) or {}
    roster_positions = settings.get("roster_positions", []) or []
    roster_groups = [_pos_to_group(p) for p in roster_positions if p != "BN"]

    # Sleeper is finicky about which key holds the current week. ``last_scored_leg``
    # is updated as games complete; ``leg`` is the current week number whether
    # it's been scored or not. Use last_scored_leg, fall back to leg if absent.
    playoff_start = int(sleeper_settings.get("playoff_week_start", 15) or 15)
    last_scored = int(sleeper_settings.get("last_scored_leg", 0) or 0)
    if last_scored == 0:
        last_scored = int(sleeper_settings.get("leg", 0) or 0)
    last_regular_season_week = max(0, min(playoff_start - 1, last_scored))

    is_dynasty, num_qbs = _detect_dynasty_and_qbs(league_id)

    return LeagueContext(
        league_id=str(league_id),
        year=str(year),
        league_settings=settings,
        user_id_to_username=user_id_to_username,
        roster_id_to_username=roster_id_to_username,
        is_dynasty=is_dynasty,
        num_qbs=num_qbs,
        playoff_week_start=playoff_start,
        last_scored_week=last_scored,
        last_regular_season_week=last_regular_season_week,
        roster_positions_groups=roster_groups,
        scoring_settings=scoring,
        total_rosters=int(settings.get("total_rosters", 0) or 0),
        qb_score_key=_detect_qb_score_key(scoring),
        skill_score_key=_detect_skill_score_key(scoring),
        current_rosters=current_rosters,
    )
