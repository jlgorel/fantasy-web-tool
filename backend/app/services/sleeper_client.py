"""Sleeper roster + league fetching."""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List

from app.services.http_utils import fetch_json
from app.services.season import get_current_fantasy_year

logger = logging.getLogger(__name__)

_IDP_MARKERS = {"IDP_FLEX", "DB", "LB", "DL"}
# Sleeper league lifecycle statuses we surface in "My Teams". `complete`
# is excluded — those are finished archives the user doesn't manage anymore.
_ACTIVE_STATUSES = ("pre_draft", "drafting", "in_season", "post_season")


def _candidate_league_years() -> List[str]:
    """Years to probe for the user's active leagues.

    During the offseason (Jan-Jul) ``get_current_fantasy_year`` returns the
    *previous* season — correct for stat lookups, but those leagues are
    now ``status=complete`` and Sleeper users have already been creating
    next year's leagues for months. Probe both years so the offseason
    user still sees their pre-draft leagues for the upcoming season.
    """
    fantasy_year = int(get_current_fantasy_year())
    calendar_year = datetime.now().year
    # De-dup while preserving order. In-season this is just [fantasy_year].
    # In offseason it becomes [fantasy_year, calendar_year] which lets us
    # surface pre_draft leagues for the upcoming season too.
    seen: List[str] = []
    for y in (fantasy_year, calendar_year):
        s = str(y)
        if s not in seen:
            seen.append(s)
    return seen


def get_sleeper_rosters_for_user(username: str) -> List[Dict[str, Any]]:
    """Return roster + scoring info for every active Sleeper league the user
    owns a team in."""
    user_data = fetch_json(f"https://api.sleeper.app/v1/user/{username}")
    user_id = user_data["user_id"]

    # Aggregate across candidate years so pre_draft leagues for next season
    # show up alongside any still-active leagues from the current one.
    seen_league_ids: set = set()
    curr_leagues: List[Dict[str, Any]] = []
    for year_string in _candidate_league_years():
        leagues_data = fetch_json(
            f"https://api.sleeper.app/v1/user/{user_id}/leagues/nfl/{year_string}"
        )
        for league in leagues_data or []:
            if league.get("status") not in _ACTIVE_STATUSES:
                continue
            lid = league["league_id"]
            if lid in seen_league_ids:
                continue
            seen_league_ids.add(lid)
            curr_leagues.append({"name": league["name"], "id": lid})
    curr_rosters: List[Dict[str, Any]] = []

    for league in curr_leagues:
        league_settings = fetch_json(f"https://api.sleeper.app/v1/league/{league['id']}")
        scoring_settings = league_settings["scoring_settings"]
        starting_pos = league_settings["roster_positions"]

        if any(marker in starting_pos for marker in _IDP_MARKERS):
            logger.info("Skipping IDP league as we don't store that data and it will cause errors")
            continue

        rosters = fetch_json(f"https://api.sleeper.app/v1/league/{league['id']}/rosters")
        your_roster = next((r for r in rosters if r["owner_id"] == user_id), None)
        if your_roster is None:
            logger.info("User not found with a roster in league %s", league["name"])
            continue
        roster_players = your_roster.get("players") or []
        if not roster_players:
            logger.info(
                "Skipping league %s because the user's roster has no players",
                league["name"],
            )
            continue

        all_owned_players: List[str] = []
        for roster in rosters:
            players = roster.get("players")
            if players:
                all_owned_players.extend(players)

        # Sleeper exposes a `starters` field on each roster — an ordered list of
        # pids matching the league's `roster_positions` slots. None entries
        # (empty slots) are filtered out. We need this for the
        # "Optimal Lineup vs. Your Lineup" comparison feature; if it's missing
        # we treat the whole league as not-comparable downstream.
        starters_raw = your_roster.get("starters") or []
        starters = [pid for pid in starters_raw if pid and pid != "0"]

        curr_rosters.append({
            "league": league["name"],
            "pids": roster_players,
            "settings": scoring_settings,
            "positions": starting_pos,
            "all_owned": all_owned_players,
            "starters": starters,
        })

    return curr_rosters
