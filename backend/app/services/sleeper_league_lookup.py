"""Sleeper league discovery + cross-season resolution.

Used by the Wrapped landing flow so the user can:

  1. Enter their Sleeper username.
  2. Pick a current-year league from a dropdown.
  3. Pick a year to "wrap" — we walk the league's ``previous_league_id``
     chain to map (current_league_id, target_year) -> historical league_id.

Sleeper assigns a brand-new league_id every season for keeper / dynasty /
redraft leagues, but exposes a back-pointer (``previous_league_id``) on
each league document. Walking that chain is the only way to translate a
"this year's ID" into "the same league two years ago."
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from app.services.http_utils import fetch_json

logger = logging.getLogger(__name__)

# Cap how far back we'll walk to avoid runaway requests against a malformed
# previous_league_id chain. 10 seasons is well past what any real user has.
_MAX_PREVIOUS_HOPS = 10


def get_user_leagues(username: str, year: str) -> List[Dict[str, Any]]:
    """List the user's Sleeper leagues for a given year.

    Returns ``[]`` if the user doesn't exist or has no leagues that year.
    Output rows are projected down to the fields the dropdown actually
    needs — keeps the JSON payload small over the wire.
    """
    if not username:
        return []
    user = fetch_json(f"https://api.sleeper.app/v1/user/{username}")
    if not user or not user.get("user_id"):
        return []
    user_id = user["user_id"]

    leagues = fetch_json(
        f"https://api.sleeper.app/v1/user/{user_id}/leagues/nfl/{year}"
    ) or []
    return [
        {
            "league_id": lg.get("league_id"),
            "name": lg.get("name"),
            "season": lg.get("season"),
            "previous_league_id": lg.get("previous_league_id"),
            "total_rosters": lg.get("total_rosters"),
            "status": lg.get("status"),
        }
        for lg in leagues
        if lg.get("league_id")
    ]


def resolve_league_for_year(league_id: str, target_year: str) -> Optional[str]:
    """Map a current league_id to its historical id for ``target_year``.

    Walks the ``previous_league_id`` chain backwards from the current league
    until either ``season == target_year`` is found or we hit a leg with no
    previous pointer. Returns ``None`` if the chain doesn't reach back that
    far (i.e. the league simply didn't exist in ``target_year``).

    If the requested year matches the input league's own season we return
    the input league_id unchanged — saves a network call for the common
    "default year" case on the wrapped page.
    """
    if not league_id or not target_year:
        return None
    target_year_str = str(target_year)

    current_id: Optional[str] = str(league_id)
    for _ in range(_MAX_PREVIOUS_HOPS):
        if current_id is None:
            return None
        try:
            league = fetch_json(f"https://api.sleeper.app/v1/league/{current_id}")
        except Exception as e:
            logger.info("resolve_league_for_year: fetch failed for %s: %s", current_id, e)
            return None
        if not league:
            return None
        season = str(league.get("season") or "")
        if season == target_year_str:
            return current_id
        # Sleeper returns "0" or null for the very first season of a chain.
        prev = league.get("previous_league_id")
        if not prev or str(prev) in ("0", "None"):
            return None
        current_id = str(prev)
    return None


def get_league_season_chain(league_id: str) -> List[Dict[str, Any]]:
    """Walk the ``previous_league_id`` chain and return every season we hit.

    Output is ordered newest-first: ``[{"season": "2026", "league_id": "..."},
    {"season": "2025", ...}, ...]``. Used by the Wrapped page to populate
    its year dropdown with only the seasons that actually exist for this
    league — leagues founded in 2024 won't offer 2023, and leagues that
    have run since 2018 will offer all seven seasons.

    Walking the chain costs one Sleeper call per season (cached upstream
    in Redis for an hour), so this is cheap.
    """
    out: List[Dict[str, Any]] = []
    if not league_id:
        return out
    seen: set[str] = set()
    current_id: Optional[str] = str(league_id)
    for _ in range(_MAX_PREVIOUS_HOPS):
        if current_id is None or current_id in seen:
            break
        seen.add(current_id)
        try:
            league = fetch_json(f"https://api.sleeper.app/v1/league/{current_id}")
        except Exception as e:
            logger.info("get_league_season_chain: fetch failed for %s: %s", current_id, e)
            break
        if not league:
            break
        season = league.get("season")
        if season:
            out.append({"season": str(season), "league_id": current_id})
        prev = league.get("previous_league_id")
        if not prev or str(prev) in ("0", "None"):
            break
        current_id = str(prev)
    return out
