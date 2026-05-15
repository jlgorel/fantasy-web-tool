import os

# Shared values (NFL teams, scoring multipliers) live in app/_fantasy_common.py
# which is auto-generated from shared/fantasy_common.py via tools/sync_shared.py.
# Edit the canonical file, then run that script.
from app._fantasy_common import (
    get_stat_point_multipliers as _shared_get_stat_point_multipliers,
    nfl_teams as _shared_nfl_teams,
    nfl_teams_reverse_lookup as _shared_nfl_teams_reverse_lookup,
)


class Config:

    data_dir = "data"
    sleeper_dir = "sleeper"
    borischen_dir = "borischen"
    vegas_dir = "vegas"
    app_dir = "app"
    draftkings_dir = "draftkings_odds"

    azure_storage_connection_string = os.getenv("AZURE_STORAGE_CONNECTION_STRING")
    containername = "fantasyjsons"

    boris_chen_fantasy_relevant_pos = ["K", "DEF", "DST", "QB", "TE", "WR", "RB", "Flex"]
    relevant_sleeper_keys = ["fantasy_positions", "full_name"]

    # NB: backend doesn't currently consume prop_name_to_ids_map or
    # relevant_td_outcomes; the scraper (azure-functions) is the canonical
    # owner of those scraping-only configs.
    prop_name_to_ids_map = {
        "Receptions Over Under": (1342, 14115),
        "Receiving Yards Over Under": (1342, 14114),
        "Passing Yards Over Under": (1000, 9524),
        "Passing TDs Over Under": (1000, 9525),
        "Interceptions Over Under": (1000, 15937),
        "Rushing Yards Over Under": (1001, 9514),
        "Anytime Scorer": (1003, 12438),
    }
    relevant_td_outcomes = ["To Score 2 Or More", "Anytime Scorer"]

    # Delegate to the shared module for things the scraper also needs to know.
    get_stat_point_multipliers = staticmethod(_shared_get_stat_point_multipliers)
    nfl_teams = _shared_nfl_teams
    nfl_teams_reverse_lookup = _shared_nfl_teams_reverse_lookup
