import os

# Shared values (NFL teams, scoring multipliers) live in _fantasy_common.py
# which is auto-generated from shared/fantasy_common.py via tools/sync_shared.py.
# Edit the canonical file, then run that script.
from _fantasy_common import (
    get_stat_point_multipliers as _shared_get_stat_point_multipliers,
    nfl_teams as _shared_nfl_teams,
    nfl_teams_reverse_lookup as _shared_nfl_teams_reverse_lookup,
)


class Config:

    data_dir = "data"
    sleeper_dir = "sleeper"
    borischen_dir = "borischen"
    vegas_dir = "vegas"
    draftkings_dir = "draftkings_odds"
    container_name = "fantasyjsons"

    azure_storage_connection_string = os.getenv("AZURE_STORAGE_CONNECTION_STRING")
    containername = "fantasyjsons"

    boris_chen_fantasy_relevant_pos = ["K", "DEF", "DST", "QB", "TE", "WR", "RB", "Flex"]
    relevant_sleeper_keys = ["fantasy_positions", "full_name"]

    # ---- Scraper-only mappings (DraftKings prop ids, FantasyPros stat names) ---
    prop_name_to_ids_map = {
        "Receptions Over Under": (1342, 14115),
        "Passing TDs Alt Lines": (1000, 16568),
        "Passing Yards Alt Lines": (1000, 16569),
        "Interceptions Over Under": (1000, 15937),
        "Anytime Scorer": (1003, 12438),
        "Receiving Yards Alt Lines": (1342),
        "Rushing Yards Alt Lines": (1001),
    }

    prop_name_to_stat_name_map = {
        "Receptions Over Under": "Receptions",
        "Passing TDs Alt Lines": "Passing Touchdowns",
        "Passing Yards Alt Lines": "Passing Yards",
        "Interceptions Over Under": "Interceptions",
        "Anytime Scorer": "Anytime Touchdown",
        "Receiving Yards Alt Lines": "Receiving Yards",
        "Rushing Yards Alt Lines": "Rushing Yards",
    }

    fantasy_pros_to_stat_name_map = {
        "PASS_YDS": "Passing Yards",
        "PASS_TDS": "Passing Touchdowns",
        "INTS": "Interceptions",
        "REC": "Receptions",
        "RUSH_YDS": "Rushing Yards",
        "REC_YDS": "Receiving Yards",
    }

    alt_line_names = ["Passing Touchdowns", "Passing Yards", "Receiving Yards", "Rushing Yards"]

    relevant_td_outcomes = ["To Score 2 Or More", "Anytime Scorer"]

    ppr_stat_scoring = {
        "attd": (6, "Anytime Touchdown"),
        "int": (-2, "Interceptions"),
        "pass_td": (4, "Passing Touchdowns"),
        "pass_yd": (0.04, "Passing Yards"),
        "rec": (1, "Receptions"),
        "rec_yd": (0.1, "Receiving Yards"),
        "rush_yd": (0.1, "Rushing Yards"),
    }

    # Delegate to the shared module for things the backend also needs.
    get_stat_point_multipliers = staticmethod(_shared_get_stat_point_multipliers)
    nfl_teams = _shared_nfl_teams
    nfl_teams_reverse_lookup = _shared_nfl_teams_reverse_lookup
