"""Build a suggested starting lineup per league.

Two ranking modes are supported via the ``mode`` parameter:

* ``"boris"`` (default) — Boris Chen tier first, Flex tier as tiebreaker, Vegas
  projection as final tiebreaker. Behavior is preserved bit-for-bit from the
  original ``form_suggested_starts_based_on_boris`` so the existing
  /load-league-data response shape is unchanged.
* ``"vegas"`` — highest Vegas projected points first, falls back to Boris tier
  + Flex tier when Vegas projections are tied or missing (off-season,
  injured, no DraftKings line).
"""
from __future__ import annotations

import logging
from collections import defaultdict
from copy import copy, deepcopy
from typing import Any, Dict, List, Literal, Tuple

from app.config import Config
from app.services.blob_store import load_blob
from app.services.boris_chen import get_tier_page_names_from_league_settings
from app.services.scoring import calculate_potential_fantasy_score

logger = logging.getLogger(__name__)

LineupMode = Literal["boris", "vegas"]


# ---------------------------------------------------------------------------
# Position-name helpers
# ---------------------------------------------------------------------------
def clean_up_pos_names(pos_names):
    """Normalize roster position names.

    Returns a single string when called with one position, otherwise a
    ``defaultdict(int)`` of position->count. ``"BN"`` is dropped.
    """
    cleaned_pos: Dict[str, int] = defaultdict(int)
    for pos in pos_names:
        if pos == "BN":
            continue
        if pos == "FLEX":
            cleaned_pos["Flex"] += 1
        elif pos == "SUPER_FLEX":
            cleaned_pos["SF"] += 1
        elif pos == "REC_FLEX":
            cleaned_pos["WT"] += 1
        elif pos == "DEF":
            cleaned_pos["DST"] += 1
        else:
            cleaned_pos[pos] += 1

    if len(cleaned_pos) == 0:
        return "BN"
    if len(cleaned_pos) == 1:
        return next(iter(cleaned_pos))
    return cleaned_pos


def list_players_for_pos_name(pos_groups: Dict[str, List[str]], pos_name: str
                              ) -> Tuple[List[str], List[str]]:
    """Return ``(eligible_players, source_positions)`` for a slot."""
    if pos_name == "Flex":
        pos_to_add = ["WR", "TE", "RB"]
    elif pos_name == "WT":
        pos_to_add = ["WR", "TE"]
    elif pos_name == "SF":
        pos_to_add = ["QB"]
    else:
        pos_to_add = [pos_name]

    players: List[str] = []
    for pos in pos_to_add:
        players.extend(pos_groups[pos])
    return players, pos_to_add


def get_all_players_from_position_groups(position_groups: Dict[str, List[str]]) -> List[str]:
    players: List[str] = []
    for names in position_groups.values():
        players.extend(names)
    return players


# ---------------------------------------------------------------------------
# Lineup ranking
# ---------------------------------------------------------------------------
def get_highest_ranked_player_from_page(
    list_of_players: List[str],
    pos_name: str,
    team_rank_dict: Dict[str, Dict[str, str]],
    sportsbook_projections: Dict[str, Any],
    backup_projections: Dict[str, Any],
    stat_point_multipliers: Dict[str, float],
    mode: LineupMode = "boris",
):
    """Pick the best player according to ``mode``.

    ``mode="boris"`` (default — legacy behavior):
      1. Lowest Boris Chen tier at the given position
      2. If tied, lowest Flex tier (if available)
      3. If still tied, highest Vegas projection

    ``mode="vegas"``:
      1. Highest Vegas projected points
      2. If tied (or both 0 — common off-season), lowest Boris Chen positional
         tier as a sanity-preserving tiebreaker
      3. If still tied, lowest Flex tier
    """
    if len(list_of_players) == 0:
        return "None Owned", "N/A"

    best_player = None
    best_tier = float("inf")
    best_flex = float("inf")
    best_proj = -float("inf")

    for player in list_of_players:
        ranks = team_rank_dict.get(player, {})
        tier = int(ranks[pos_name]) if pos_name in ranks else 999
        flex = int(ranks["Flex"]) if "Flex" in ranks else 999

        projected_points, _, _, _ = calculate_potential_fantasy_score(
            player, pos_name, sportsbook_projections, backup_projections, stat_point_multipliers
        )

        if mode == "vegas":
            better = (
                projected_points > best_proj
                or (projected_points == best_proj and tier < best_tier)
                or (
                    projected_points == best_proj
                    and tier == best_tier
                    and flex < best_flex
                )
            )
        else:
            better = (
                tier < best_tier
                or (tier == best_tier and flex < best_flex)
                or (tier == best_tier and flex == best_flex and projected_points > best_proj)
            )

        if better:
            best_player = player
            best_tier = tier
            best_flex = flex
            best_proj = projected_points

    if best_player:
        return best_player, best_tier
    return list_of_players[0], "Unranked"


# ---------------------------------------------------------------------------
# Suggested-start builder
# ---------------------------------------------------------------------------
def _build_team_rank_dict(
    position_groups: Dict[str, List[str]],
    boris_chen_tiers: Dict[str, Dict[str, str]],
    tiers_to_lookup: set,
    normal_prefix: str,
    te_prefix: str,
) -> Dict[str, Dict[str, str]]:
    team_rank_dict: Dict[str, Dict[str, str]] = {}
    for player in get_all_players_from_position_groups(position_groups):
        pos_rank_dict: Dict[str, str] = {}
        # Use .get to avoid mutating the defaultdict
        player_tiers = boris_chen_tiers.get(player, {})
        tiers_for_player = tiers_to_lookup.intersection(player_tiers)
        if not tiers_for_player:
            pos_rank_dict["Position"] = "Unranked"

        # RBs/WRs with a top-4 positional tier get auto-promoted to Flex tier 1
        # if they don't already have a Flex tier.
        top_tier_player_flag = False
        for tier in tiers_for_player:
            tier_rank = player_tiers[tier]
            cleaned_pos_name = tier
            for prefix in (normal_prefix, te_prefix):
                cleaned_pos_name = cleaned_pos_name.replace(prefix, "")
            pos_rank_dict[cleaned_pos_name] = tier_rank
            if int(tier_rank) <= 4 and cleaned_pos_name != "TE":
                top_tier_player_flag = True

        if top_tier_player_flag and "Flex" not in pos_rank_dict:
            pos_rank_dict["Flex"] = "1"

        team_rank_dict[player] = pos_rank_dict
    return team_rank_dict


def _resolve_slot_position(pos_name: str, pos_groups_copy: Dict[str, List[str]]) -> str:
    cleaned_name = clean_up_pos_names([pos_name])
    if cleaned_name == "WT":
        return "WR"
    if cleaned_name == "SF":
        return "QB"
    if cleaned_name == "DST":
        return "DEF"
    if cleaned_name == "BN":
        try:
            return next(iter(pos_groups_copy.keys()))
        except StopIteration:
            logger.info("pos groups copy is empty.  Lemme check %s", pos_groups_copy)
            return "BN"
    return cleaned_name


def _format_starter_entry(
    pos: str,
    player_dict: Dict[str, Any],
    name_to_pid: Dict[str, str],
    player_data: Dict[str, Any],
    fantasypros_data: Dict[str, Any],
    sportsbook_projections: Dict[str, Any],
    backup_projections: Dict[str, Any],
    stat_point_multipliers: Dict[str, float],
) -> Dict[str, Any]:
    name = player_dict["Name"]
    temp_dict: Dict[str, Any] = {"POS": pos, "NAME": name}

    if name in name_to_pid:
        pid = name_to_pid[name]
        temp_dict["PID"] = pid
        try:
            temp_dict["REALLIFE_POS"] = player_data[pid]["fantasy_positions"][0]
        except (KeyError, IndexError, TypeError):
            logger.info("Probably a defense %s", name)
            temp_dict["REALLIFE_POS"] = "DEF"
    else:
        if name in Config.nfl_teams_reverse_lookup:
            temp_dict["TEAM"] = Config.nfl_teams_reverse_lookup[name]
        else:
            logger.info(
                "I'm guessing that this is cause you don't have a defense or kicker. Checking : %s",
                pos,
            )

    for tier, ranking in player_dict["Tiers"].items():
        if "Flex" not in tier:
            temp_dict["POS_RANK"] = str(ranking)
        else:
            temp_dict["FLEX"] = str(ranking)

    if pos not in ("DST", "DEF", "K"):
        projected_scoring, old_projection, statline, boom_bust = calculate_potential_fantasy_score(
            name, pos, sportsbook_projections, backup_projections, stat_point_multipliers
        )
        temp_dict["VEGAS"] = str(round(projected_scoring, 2))
        temp_dict["VEGAS_STATS"] = statline
        if boom_bust is not None:
            temp_dict["BOOM"] = round(boom_bust["boom"] * 100, 2)
            temp_dict["BUST"] = round(boom_bust["bust"] * 100, 2)
            temp_dict["PERCENTILES"] = boom_bust["percentiles"]
        else:
            temp_dict["BOOM"] = "N/A. Not enough vegas props"
            temp_dict["BUST"] = "N/A"
            temp_dict["PERCENTILES"] = "N/A"
        if old_projection:
            temp_dict["VEGAS"] += "\t Old projection, no lines available, confirm uninjured"

        p_info_dict = fantasypros_data.get(name)
        logger.info("Getting info dict for %s", name)
        if p_info_dict:
            temp_dict["MATCHUP_RATING"] = p_info_dict.get("Opponent Rating", "UNKNOWN")
            temp_dict["TEAM_NAME"] = p_info_dict.get("Team Name", "UNKNOWN")
    else:
        temp_dict["VEGAS"] = "N/A"

    return temp_dict


def _annotate_qb_stacks(starters: List[Dict[str, Any]]) -> None:
    """Mutate starter rows to mark same-NFL-team QB+WR/TE stacks.

    For every starting QB (POS != BN, REALLIFE_POS == QB) we find any
    starting WR/TE on the same NFL team and set ``STACK_WITH_QB`` + a
    human-readable ``STACK_QB_NAME``. Bench rows are skipped intentionally
    (the badge is meant to highlight active stacks the user has rolled out).
    """
    qb_team_to_name: Dict[str, str] = {}
    for row in starters:
        if row.get("POS") == "BN":
            continue
        if row.get("REALLIFE_POS") != "QB":
            continue
        team = row.get("TEAM_NAME")
        if team and team != "UNKNOWN":
            qb_team_to_name[team] = row.get("NAME", "QB")

    if not qb_team_to_name:
        return

    for row in starters:
        if row.get("POS") == "BN":
            continue
        if row.get("REALLIFE_POS") not in ("WR", "TE"):
            continue
        team = row.get("TEAM_NAME")
        if team and team in qb_team_to_name:
            row["STACK_WITH_QB"] = True
            row["STACK_QB_NAME"] = qb_team_to_name[team]


def form_suggested_starts_based_on_boris(
    user_rosters: List[Dict[str, Any]],
    league_position_groups: Dict[str, Dict[str, List[str]]],
    boris_chen_tiers: Dict[str, Dict[str, str]],
    name_to_pid: Dict[str, str],
    mode: LineupMode = "boris",
) -> Dict[str, List[Dict[str, Any]]]:
    suggested_starts: Dict[str, List[Dict[str, Any]]] = {}

    sportsbook_projections = load_blob("hand_calculated_projections.json")
    backup_projections = load_blob("backup_fantasypros_projections.json")
    fantasypros_data = load_blob("fantasypros_data.json")
    player_data = load_blob("players.json")

    for roster in user_rosters:
        position_groups = copy(league_position_groups[roster["league"]])
        normal_prefix, te_prefix = get_tier_page_names_from_league_settings(roster["settings"])
        starting_positions = clean_up_pos_names(roster["positions"])
        settings = roster["settings"]

        tiers_to_lookup: set = set()
        for pos_name in starting_positions:
            if pos_name in ("RB", "WR", "Flex"):
                tiers_to_lookup.add(normal_prefix + pos_name)
            elif pos_name == "TE":
                tiers_to_lookup.add(te_prefix + pos_name)
            elif pos_name == "WT":
                tiers_to_lookup.add(normal_prefix + "Flex")
            else:
                tiers_to_lookup.add(pos_name)

        team_rank_dict = _build_team_rank_dict(
            position_groups, boris_chen_tiers, tiers_to_lookup, normal_prefix, te_prefix
        )

        logger.info("Building table for %s.", roster["league"])

        roster_table: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        pos_groups_copy = deepcopy(position_groups)
        full_roster_positions = deepcopy(roster["positions"])
        if len(roster["pids"]) > len(full_roster_positions):
            full_roster_positions.extend(
                ["BN"] * (1 + len(roster["pids"]) - len(roster["positions"]))
            )

        stat_point_multipliers = Config.get_stat_point_multipliers(settings)

        for pos_name in full_roster_positions:
            cleaned_name = _resolve_slot_position(pos_name, pos_groups_copy)
            players, pos_added = list_players_for_pos_name(pos_groups_copy, cleaned_name)

            high_name, _high_rank = get_highest_ranked_player_from_page(
                players,
                cleaned_name,
                team_rank_dict,
                sportsbook_projections,
                backup_projections,
                stat_point_multipliers,
                mode=mode,
            )

            roster_table[pos_name].append({
                "Name": high_name,
                "Tiers": team_rank_dict[high_name]
                if high_name in team_rank_dict
                else {cleaned_name: "Unranked"},
            })
            for pos in pos_added:
                if high_name in pos_groups_copy[pos]:
                    if len(pos_groups_copy[pos]) == 1:
                        del pos_groups_copy[pos]
                    else:
                        pos_groups_copy[pos].remove(high_name)

        # leftover IR / taxi-squad players go to bench
        for position, player_list in pos_groups_copy.items():
            for player in player_list:
                roster_table["BN"].append({"Name": player, "Tiers": {position: "Unranked"}})

        suggested_starts_for_roster: List[Dict[str, Any]] = []
        for pos, player_dict_list in roster_table.items():
            for player_dict in player_dict_list:
                suggested_starts_for_roster.append(
                    _format_starter_entry(
                        pos,
                        player_dict,
                        name_to_pid,
                        player_data,
                        fantasypros_data,
                        sportsbook_projections,
                        backup_projections,
                        stat_point_multipliers,
                    )
                )

        _annotate_qb_stacks(suggested_starts_for_roster)

        suggested_starts[str(roster["league"])] = suggested_starts_for_roster

    return suggested_starts
