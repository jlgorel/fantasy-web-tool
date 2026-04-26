"""Top free-agent recommendations per league, parallelized per position."""
from __future__ import annotations

import heapq
import logging
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List

from app.config import Config
from app.services.blob_store import load_blob
from app.services.scoring import calculate_potential_fantasy_score

logger = logging.getLogger(__name__)

_FA_POSITIONS = ("QB", "RB", "WR", "TE")


def _collect_unowned_players(
    player_data: Dict[str, Any],
    owned_data: Dict[str, Any],
    league_owned: set,
) -> Dict[str, List[tuple]]:
    by_pos: Dict[str, List[tuple]] = defaultdict(list)
    for pid, pdata in player_data.items():
        if pid in league_owned or "full_name" not in pdata or pid not in owned_data:
            continue
        positions = pdata.get("fantasy_positions") or []
        if not positions:
            continue
        # players.json was already normalized in blob_store (Travis Hunter -> WR
        # first), so positions[0] is the canonical fantasy position.
        pos = positions[0]
        if pos not in _FA_POSITIONS:
            continue
        by_pos[pos].append((pid, pdata["full_name"], pos))
    return by_pos


def form_top_free_agents_parallel(
    user_rosters: List[Dict[str, Any]],
    name_to_pid: Dict[str, str],
    max_workers: int = 8,
) -> Dict[str, Dict[str, List[Dict[str, Any]]]]:
    """Return ``{league: {pos: [top_3_fa_dicts]}}``.

    Same shape as ``form_suggested_starts_based_on_boris`` per-player entries.
    """
    sportsbook_projections = load_blob("hand_calculated_projections.json")
    backup_projections = load_blob("backup_fantasypros_projections.json")
    fantasypros_data = load_blob("fantasypros_data.json")
    player_data = load_blob("players.json")
    owned_data = load_blob("owned.json")

    free_agents_by_league: Dict[str, Dict[str, List[Dict[str, Any]]]] = {}

    for roster in user_rosters:
        league_name = roster["league"]
        league_owned = set(roster["all_owned"])
        stat_point_multipliers = Config.get_stat_point_multipliers(roster["settings"])

        fa_by_pos = _collect_unowned_players(player_data, owned_data, league_owned)
        top_free_agents: Dict[str, List[Dict[str, Any]]] = defaultdict(list)

        def score_player(pid_name_pos):
            pid, name, pos = pid_name_pos
            proj_points, old_proj, statline, boom_bust = calculate_potential_fantasy_score(
                name, pos, sportsbook_projections, backup_projections, stat_point_multipliers
            )
            return proj_points, pid, name, pos, statline, boom_bust, old_proj

        for pos in _FA_POSITIONS:
            candidates = fa_by_pos.get(pos, [])
            if not candidates:
                continue
            scored: List[tuple] = []
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = {executor.submit(score_player, c): c for c in candidates}
                for future in as_completed(futures):
                    scored.append(future.result())

            top3 = heapq.nlargest(3, scored, key=lambda x: x[0])
            for proj, pid, name, p_pos, statline, boom_bust, old_proj in top3:
                temp_dict: Dict[str, Any] = {
                    "POS": p_pos,
                    "NAME": name,
                    "PID": pid,
                    "REALLIFE_POS": p_pos,
                    "VEGAS": str(round(proj, 2)) + ("\t Old projection" if old_proj else ""),
                    "VEGAS_STATS": statline,
                }
                if boom_bust:
                    temp_dict["BOOM"] = round(boom_bust["boom"] * 100, 2)
                    temp_dict["BUST"] = round(boom_bust["bust"] * 100, 2)
                    temp_dict["PERCENTILES"] = boom_bust["percentiles"]
                else:
                    temp_dict["BOOM"] = "N/A"
                    temp_dict["BUST"] = "N/A"
                    temp_dict["PERCENTILES"] = "N/A"

                p_info_dict = fantasypros_data.get(name)
                if p_info_dict:
                    temp_dict["MATCHUP_RATING"] = p_info_dict.get("Opponent Rating", "UNKNOWN")
                    temp_dict["TEAM_NAME"] = p_info_dict.get("Team Name", "UNKNOWN")

                top_free_agents[p_pos].append(temp_dict)

        free_agents_by_league[league_name] = top_free_agents

    return free_agents_by_league
