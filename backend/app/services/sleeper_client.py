"""Sleeper roster + league fetching."""
from __future__ import annotations

import logging
from typing import Any, Dict, List

from app.services.http_utils import fetch_json
from app.services.season import get_current_fantasy_year

logger = logging.getLogger(__name__)

_IDP_MARKERS = {"IDP_FLEX", "DB", "LB", "DL"}


def get_sleeper_rosters_for_user(username: str) -> List[Dict[str, Any]]:
    """Return roster + scoring info for every in-season Sleeper league the user
    owns a team in."""
    year_string = get_current_fantasy_year()

    user_data = fetch_json(f"https://api.sleeper.app/v1/user/{username}")
    user_id = user_data["user_id"]

    leagues_data = fetch_json(
        f"https://api.sleeper.app/v1/user/{user_id}/leagues/nfl/{year_string}"
    )

    curr_leagues = [
        {"name": league["name"], "id": league["league_id"]}
        for league in leagues_data
        if league["status"] in ("in_season", "post_season")
    ]
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

        all_owned_players: List[str] = []
        for roster in rosters:
            players = roster.get("players")
            if players:
                all_owned_players.extend(players)

        curr_rosters.append({
            "league": league["name"],
            "pids": your_roster["players"],
            "settings": scoring_settings,
            "positions": starting_pos,
            "all_owned": all_owned_players,
        })

    return curr_rosters
