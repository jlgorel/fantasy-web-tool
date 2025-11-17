import os

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

    prop_name_to_ids_map = {
        "Receptions Over Under": (1342, 14115),
        "Passing TDs Alt Lines": (1000, 16568),
        "Passing Yards Alt Lines": (1000, 16569),
        "Interceptions Over Under": (1000, 15937),
        "Anytime Scorer": (1003, 12438),
        "Receiving Yards Alt Lines": (1342),
        "Rushing Yards Alt Lines": (1001)
        }

    prop_name_to_stat_name_map = {
        "Receptions Over Under": "Receptions",
        "Passing TDs Alt Lines": "Passing Touchdowns",
        "Passing Yards Alt Lines": "Passing Yards",
        "Interceptions Over Under": "Interceptions",
        "Anytime Scorer": "Anytime Touchdown",
        "Receiving Yards Alt Lines": "Receiving Yards",
        "Rushing Yards Alt Lines": "Rushing Yards"
    }

    fantasy_pros_to_stat_name_map = {
        "PASS_YDS" : "Passing Yards",
        "PASS_TDS": "Passing Touchdowns",
        "INTS": "Interceptions",
        "REC": "Receptions",
        "RUSH_YDS": "Rushing Yards",
        "REC_YDS": "Receiving Yards"
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
        "rush_yd": (0.1, "Rushing Yards")
    }

    def get_stat_point_multipliers(settings): 
        return {
            "Interceptions": settings["pass_int"],
            "Non Passing Touchdowns": settings["rec_td"],
            "Non Passing Touchdowns": settings["rush_td"],
            "Passing Yards": settings["pass_yd"],
            "Passing TDs": settings["pass_td"],
            "Passing Touchdowns": settings["pass_td"],
            "Rushing Yards": settings["rush_yd"],
            "Receiving Yards": settings["rec_yd"],
            "Receptions": settings["rec"],
            "TE Receptions": settings["rec"] if "bonus_rec_te" not in settings else settings["rec"] + settings["bonus_rec_te"] 
        }

    nfl_teams = {
        'NE': 'New England Patriots',
        'NYG': 'New York Giants',
        'NYJ': 'New York Jets',
        'PHI': 'Philadelphia Eagles',
        'WAS': 'Washington Commanders',
        'DAL': 'Dallas Cowboys',
        'BUF': 'Buffalo Bills',
        'MIA': 'Miami Dolphins',
        'PIT': 'Pittsburgh Steelers',
        'CIN': 'Cincinnati Bengals',
        'CLE': 'Cleveland Browns',
        'BAL': 'Baltimore Ravens',
        'TEN': 'Tennessee Titans',
        'JAX': 'Jacksonville Jaguars',
        'IND': 'Indianapolis Colts',
        'HOU': 'Houston Texans',
        'KC': 'Kansas City Chiefs',
        'LAC': 'Los Angeles Chargers',
        'LV': 'Las Vegas Raiders',
        'SEA': 'Seattle Seahawks',
        'SF': 'San Francisco 49ers',
        'LA': 'Los Angeles Rams',
        'ARI': 'Arizona Cardinals',
        'CHI': 'Chicago Bears',
        'DET': 'Detroit Lions',
        'GB': 'Green Bay Packers',
        'MIN': 'Minnesota Vikings',
        'NO': 'New Orleans Saints',
        'ATL': 'Atlanta Falcons',
        'CAR': 'Carolina Panthers',
        'TB': 'Tampa Bay Buccaneers',
        'DEN': 'Denver Broncos',
        'SD': 'San Diego Chargers'  # Note: The Chargers now play in Los Angeles but used to be in San Diego.
    }

    nfl_teams_reverse_lookup = {v: k for k, v in nfl_teams.items()}