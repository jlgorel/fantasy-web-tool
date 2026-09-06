"""Backwards-compatible facade for the legacy sleeper_service module.

The original monolithic file has been split into focused modules under
app.services (blob_store, scoring, lineup_optimizer, free_agents, etc.).
This module re-exports the public symbols still imported by app.routes so
the refactor is a no-op at the call site. Prefer importing from the new
modules directly in any new code.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Tuple

from app.services.blob_store import (
    load_blob,
    load_json_from_azure_storage,
    normalize_players_positions,
)
from app.services.boris_chen import (
    get_tier_page_names_from_league_settings,
    prepare_boris_chen_tier_dict,
)
from app.services.fleaflicker_client import (
    convert_ff_roster_settings,
    get_fleaflicker_rosters_and_convert_to_sleeper,
)
from app.services.free_agents import form_top_free_agents_parallel
from app.services.http_utils import fetch_json
from app.services.lineup_compare import annotate_lineup_deltas, build_your_lineup
from app.services.lineup_optimizer import (
    clean_up_pos_names,
    form_suggested_starts_based_on_boris,
    get_all_players_from_position_groups,
    get_highest_ranked_player_from_page,
    list_players_for_pos_name,
)
from app.services.player_data import (
    prepare_pid_to_name_dict,
    prepare_position_groups_for_leagues,
)
from app.services.rankings import get_overall_rankings
from app.services.scoring import calculate_potential_fantasy_score
from app.services.season import get_current_fantasy_year
from app.services.sleeper_client import get_sleeper_rosters_for_user

logger = logging.getLogger(__name__)


def build_lineup_recommendations(
    user_rosters: List[Dict[str, Any]],
    *,
    include_free_agents: bool = True,
) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, Dict[str, List[Dict[str, Any]]]]]:
    """Run normalized rosters through the shared My Teams lineup pipeline."""
    pid_to_player, name_to_pid = prepare_pid_to_name_dict()

    nonempty_rosters = []
    for roster in user_rosters:
        if roster.get("pids"):
            nonempty_rosters.append(roster)
        else:
            logger.info(
                "Skipping league %s because the user's roster has no players",
                roster.get("league", "unknown"),
            )
    user_rosters = nonempty_rosters

    boris_chen_dict = prepare_boris_chen_tier_dict()
    league_position_groups = prepare_position_groups_for_leagues(user_rosters, pid_to_player)

    boris_lineups = form_suggested_starts_based_on_boris(
        user_rosters, league_position_groups, boris_chen_dict, name_to_pid, mode="boris"
    )
    vegas_lineups = form_suggested_starts_based_on_boris(
        user_rosters, league_position_groups, boris_chen_dict, name_to_pid, mode="vegas"
    )
    your_lineups = build_your_lineup(user_rosters, name_to_pid, boris_chen_dict)

    combined: Dict[str, Dict[str, Any]] = {}
    for league_name in boris_lineups.keys():
        boris = boris_lineups[league_name]
        vegas = vegas_lineups.get(league_name, [])
        yours = your_lineups.get(league_name)

        if yours:
            annotate_lineup_deltas(boris, yours)
            annotate_lineup_deltas(vegas, yours)

        combined[league_name] = {
            "boris_optimized": boris,
            "vegas_optimized": vegas,
            "your_lineup": yours,
        }

    free_agents = (
        form_top_free_agents_parallel(user_rosters, name_to_pid)
        if include_free_agents
        else {str(roster["league"]): {} for roster in user_rosters}
    )
    return combined, free_agents


def cache_sleeper_user_info(
    username: str, user_uuid: str, website_name: str = "Sleeper"
) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, Dict[str, List[Dict[str, Any]]]]]:
    """Build suggested lineups + free agent recs for a user.

    Single entry point used by /load-sleeper-info. Pulls the user's rosters
    from the requested website (Sleeper or Fleaflicker), then runs the full
    Boris Chen + Vegas projection pipeline.

    The returned lineup dict has shape::

        {
          "League Name": {
            "boris_optimized": [player_rows...],   # primary, always present
            "vegas_optimized": [player_rows...],   # always present
            "your_lineup":     [player_rows...] | None,  # None for Fleaflicker
          },
          ...
        }

    The new shape is wrapper-compatible: callers that only need league names
    just iterate the top-level keys, same as before.
    """
    if website_name == "Sleeper":
        user_rosters = get_sleeper_rosters_for_user(username)
    elif website_name == "Fleaflicker":
        _pid_to_player, name_to_pid = prepare_pid_to_name_dict()
        user_rosters = get_fleaflicker_rosters_and_convert_to_sleeper(username, name_to_pid)
    else:
        raise ValueError("Unsupported website " + repr(website_name))
    return build_lineup_recommendations(user_rosters)


__all__ = [
    "build_lineup_recommendations",
    "cache_sleeper_user_info",
    "load_blob",
    "load_json_from_azure_storage",
    "normalize_players_positions",
    "fetch_json",
    "get_current_fantasy_year",
    "get_sleeper_rosters_for_user",
    "convert_ff_roster_settings",
    "get_fleaflicker_rosters_and_convert_to_sleeper",
    "prepare_pid_to_name_dict",
    "prepare_position_groups_for_leagues",
    "prepare_boris_chen_tier_dict",
    "get_tier_page_names_from_league_settings",
    "calculate_potential_fantasy_score",
    "form_suggested_starts_based_on_boris",
    "get_highest_ranked_player_from_page",
    "list_players_for_pos_name",
    "clean_up_pos_names",
    "get_all_players_from_position_groups",
    "form_top_free_agents_parallel",
    "get_overall_rankings",
]
