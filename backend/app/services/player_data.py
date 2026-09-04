"""Player catalog helpers (pid<->name mappings, league position groups)."""
from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any, Dict, List, Tuple

from app.config import Config
from app.services.blob_store import load_blob

logger = logging.getLogger(__name__)


def prepare_pid_to_name_dict() -> Tuple[Dict[str, Any], Dict[str, str]]:
    """Load players.json and build pid->player and full_name->pid lookups."""
    pid_to_player: Dict[str, Any] = {}
    name_to_pid: Dict[str, str] = {}

    data = load_blob("players.json")
    for pid, pdata in data.items():
        pid_to_player[pid] = pdata
        if "full_name" in pdata:
            name_to_pid[pdata["full_name"]] = pid

    return pid_to_player, name_to_pid


def prepare_position_groups_for_leagues(
    user_rosters: List[Dict[str, Any]], pid_to_player: Dict[str, Any]
) -> Dict[str, Dict[str, List[str]]]:
    """For each league return ``{position: [player_name, ...]}`` for the user's roster."""
    league_position_groups: Dict[str, Dict[str, List[str]]] = {}

    for roster in user_rosters:
        league_name = roster["league"]
        position_groups: Dict[str, List[str]] = defaultdict(list)
        for pid in roster.get("pids") or []:
            player = pid_to_player.get(pid)
            if player is None:
                logger.info("Player pid %s missing from players.json", pid)
                continue
            try:
                position = player["fantasy_positions"][0]
            except (KeyError, IndexError, TypeError):
                logger.info("Error handling player with pid %s, %s", pid, player)
                continue
            name = Config.nfl_teams[pid] if pid in Config.nfl_teams else player.get("full_name")
            if name is None:
                continue
            position_groups[position].append(name)
        league_position_groups[league_name] = position_groups

    return league_position_groups
