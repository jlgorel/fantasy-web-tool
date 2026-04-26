"""Fleaflicker roster fetcher.

Translates Fleaflicker's roster + scoring shape into the same internal format
produced by ``sleeper_client.get_sleeper_rosters_for_user`` so the rest of the
pipeline stays website-agnostic.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List

from app.config import Config
from app.services.http_utils import fetch_json
from app.services.season import get_current_fantasy_year

logger = logging.getLogger(__name__)

_VALID_FF_POSITIONS = {
    "QB", "RB", "WR", "TE", "WR/TE", "RB/WR/TE", "QB/RB/WR/TE", "K", "D/ST", "BN", "IR",
}
_FF_POS_RENAMES = {
    "IR": "BN",
    "QB/RB/WR/TE": "SUPER_FLEX",
    "RB/WR/TE": "FLEX",
    "WR/TE": "WT",
    "D/ST": "DEF",
}
_FF_LABEL_TO_PREFIX = {"Passing": "pass", "Rushing": "rush", "Receiving": "rec"}
_FF_VALID_ABBREVIATIONS = {"int", "td", "yd", "rec"}


def convert_ff_roster_settings(ff_roster_json: Dict[str, Any]) -> List[str]:
    """Convert Fleaflicker league roster requirements into a Sleeper-style
    ``roster_positions`` list."""
    roster_settings: List[str] = []
    for position_info in ff_roster_json["positions"]:
        pos_name = position_info["label"]
        if pos_name not in _VALID_FF_POSITIONS:
            continue
        pos_name = _FF_POS_RENAMES.get(pos_name, pos_name)
        num_started = int(position_info.get("start", position_info.get("max", 0)))
        roster_settings.extend([pos_name] * num_started)
    return roster_settings


def _resolve_player_pid(player_fullname: str, name_to_pid: Dict[str, str]) -> str | None:
    """Map a Fleaflicker player name to a Sleeper pid (or NFL team code for DST)."""
    if player_fullname in name_to_pid:
        return name_to_pid[player_fullname]
    if player_fullname in Config.nfl_teams_reverse_lookup:
        return Config.nfl_teams_reverse_lookup[player_fullname]
    logger.info("%s not in sleeper dict.", player_fullname)
    return None


def _fetch_user_roster_pids(league_id: int, team_id: int, year_string: str,
                            name_to_pid: Dict[str, str]) -> List[str]:
    roster_url = (
        f"https://www.fleaflicker.com/api/FetchRoster?sport=NFL"
        f"&league_id={league_id}&team_id={team_id}&season={year_string}"
    )
    data = fetch_json(roster_url)
    rostered: List[str] = []
    for group in data["groups"]:
        for player in group["slots"]:
            try:
                fullname = player["leaguePlayer"]["proPlayer"]["nameFull"]
            except (KeyError, TypeError):
                continue
            pid = _resolve_player_pid(fullname, name_to_pid)
            if pid is not None:
                rostered.append(pid)
    return rostered


def _fetch_all_owned_pids(league_id: int, name_to_pid: Dict[str, str]) -> List[str]:
    all_rosters_url = (
        f"https://www.fleaflicker.com/api/FetchLeagueRosters?sport=NFL&league_id={league_id}"
    )
    data = fetch_json(all_rosters_url)
    all_owned: List[str] = []
    for roster in data["rosters"]:
        for player in roster["players"]:
            try:
                fullname = player["proPlayer"]["nameFull"]
            except (KeyError, TypeError):
                continue
            pid = _resolve_player_pid(fullname, name_to_pid)
            if pid is not None:
                all_owned.append(pid)
    return all_owned


def _fetch_league_scoring(league_id: int) -> Dict[str, float]:
    league_settings_url = (
        f"https://www.fleaflicker.com/api/FetchLeagueRules?sport=NFL&league_id={league_id}"
    )
    data = fetch_json(league_settings_url)
    scoring: Dict[str, float] = {}
    for group in data["groups"]:
        prefix = _FF_LABEL_TO_PREFIX.get(group.get("label"))
        if prefix is None:
            continue
        for rule in group["scoringRules"]:
            abbrev = rule["category"]["abbreviation"].lower()
            if abbrev not in _FF_VALID_ABBREVIATIONS:
                continue
            points = rule["points"]["value"] / rule["forEvery"]
            key = "_".join([prefix, abbrev]) if abbrev != "rec" else "rec"
            scoring[key] = float(points)
    return scoring


def get_fleaflicker_rosters_and_convert_to_sleeper(
    email: str, name_to_pid: Dict[str, str]
) -> List[Dict[str, Any]]:
    year_string = get_current_fantasy_year()

    user_url = (
        f"https://www.fleaflicker.com/api/FetchUserLeagues?sport=NFL"
        f"&season={year_string}&email={email}"
    )
    user_data = fetch_json(user_url)

    league_settings = [
        {
            "league_id": league["id"],
            "league_name": league["name"],
            "team_id": league["ownedTeam"]["id"],
            "starting_pos": convert_ff_roster_settings(league["rosterRequirements"]),
        }
        for league in user_data["leagues"]
    ]

    curr_rosters: List[Dict[str, Any]] = []
    for league in league_settings:
        if league["league_name"] == "test":
            continue
        league_id = league["league_id"]
        team_id = league["team_id"]

        league["pids"] = _fetch_user_roster_pids(league_id, team_id, year_string, name_to_pid)
        league["all_owned"] = _fetch_all_owned_pids(league_id, name_to_pid)
        league["settings"] = _fetch_league_scoring(league_id)

        curr_rosters.append({
            "league": league["league_name"],
            "pids": league["pids"],
            "settings": league["settings"],
            "positions": league["starting_pos"],
            "all_owned": league["all_owned"],
        })

    return curr_rosters
