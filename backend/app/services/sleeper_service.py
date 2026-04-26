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


def cache_sleeper_user_info(
    username: str, user_uuid: str, website_name: str = "Sleeper"
) -> Tuple[Dict[str, List[Dict[str, Any]]], Dict[str, Dict[str, List[Dict[str, Any]]]]]:
    """Build suggested lineups + free agent recs for a user.

    Single entry point used by /load-sleeper-info. Pulls the user's rosters
    from the requested website (Sleeper or Fleaflicker), then runs the full
    Boris Chen + Vegas projection pipeline.
    """
    pid_to_player, name_to_pid = prepare_pid_to_name_dict()

    if website_name == "Sleeper":
        user_rosters = get_sleeper_rosters_for_user(username)
    elif website_name == "Fleaflicker":
        user_rosters = get_fleaflicker_rosters_and_convert_to_sleeper(username, name_to_pid)
    else:
        raise ValueError("Unsupported website " + repr(website_name))

    boris_chen_dict = prepare_boris_chen_tier_dict()
    league_position_groups = prepare_position_groups_for_leagues(user_rosters, pid_to_player)
    suggested_lineups = form_suggested_starts_based_on_boris(
        user_rosters, league_position_groups, boris_chen_dict, name_to_pid
    )
    free_agents = form_top_free_agents_parallel(user_rosters, name_to_pid)
    return suggested_lineups, free_agents


__all__ = [
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
