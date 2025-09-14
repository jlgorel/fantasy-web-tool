import requests
import os
import json
from flask import jsonify
from app.config import Config
from datetime import datetime
from collections import defaultdict
from azure.storage.blob import BlobServiceClient
from copy import copy, deepcopy
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def load_json_from_azure_storage(blob_name, container_name, connection_string):
    # Initialize the BlobServiceClient with the provided connection string
    print(blob_name)
    print(container_name)
    print(connection_string)
    blob_service_client = BlobServiceClient.from_connection_string(connection_string)

    # Get the blob client
    blob_client = blob_service_client.get_blob_client(container=container_name, blob=blob_name)

    # Download the blob content
    blob_data = blob_client.download_blob()
    data = json.loads(blob_data.readall())

    return data

def fetch_json(url):
    resp = requests.get(url)
    if resp.status_code == 200:
        return resp.json()
    else:
        logger.error(f"Error fetching {url}: {resp.status_code}")
        return None

def cache_sleeper_user_info(username, user_uuid):

    user_rosters = get_rosters_for_user(username)
    pidToPlayerDict, nameToPidDict = prepare_pid_to_name_dict(user_rosters)
    boris_chen_dict = prepare_boris_chen_tier_dict()
    league_position_groups = prepare_position_groups_for_leagues(user_rosters, pidToPlayerDict)
    suggested_lineups = form_suggested_starts_based_on_boris(user_rosters, league_position_groups, boris_chen_dict, nameToPidDict)

    return suggested_lineups

def get_rosters_for_user(username):
    # Get the current date and time
    now = datetime.now()

    # Extract the year as a string
    year_string = now.strftime("%Y")

    current_month = now.month

    #deal with early 2025 years
    if int(current_month) <=7:
        year_string = str(int(year_string) - 1)

    url = "https://api.sleeper.app/v1/user/{}".format(username)

    data = fetch_json(url)
    user_id = data["user_id"]

    url = "https://api.sleeper.app/v1/user/{}/leagues/nfl/{}".format(user_id, year_string)
    data = fetch_json(url)

    curr_leagues = [{"name": league["name"], "id": league["league_id"]} for league in data if league["status"] in ["in_season", "post_season"]]
    curr_rosters = []

    for league in curr_leagues:
        url = "https://api.sleeper.app/v1/league/{}".format(league["id"])
        league_settings = fetch_json(url)

        scoring_settings = league_settings["scoring_settings"]
        starting_pos = league_settings["roster_positions"]

        if "IDP_FLEX" in starting_pos or "DB" in starting_pos or "LB" in starting_pos or "DL" in starting_pos:
            logger.info("Skipping IDP league as we don't store that data and it will cause errors")
            continue

        url = "https://api.sleeper.app/v1/league/{}/rosters".format(league["id"])
        data = fetch_json(url)

        your_roster = next((roster for roster in data if roster["owner_id"] == user_id), None)
        if your_roster is None:
            logging.info("User not found with a roster in league " + str(league["name"]))
            continue
        all_owned_players = []

        for roster in data:
            all_owned_players.extend([player for player in roster["players"]])

        curr_rosters.append({"league": league["name"], "pids": your_roster["players"], "settings": scoring_settings, "positions": starting_pos, "all_owned": all_owned_players})
    
    return curr_rosters
    
def prepare_pid_to_name_dict(user_rosters):
    pids = set()

    for roster in user_rosters:
        pids = pids.union(set(roster["pids"]))

    pidToPlayerDict = {}
    nameToPidDict = {}

    data = load_json_from_azure_storage("players.json", Config.containername, Config.azure_storage_connection_string)
        
    for pid in pids:
        pidToPlayerDict[pid] = data[pid]
        if "full_name" in data[pid]:
            nameToPidDict[data[pid]["full_name"]] = pid

    return pidToPlayerDict, nameToPidDict

def prepare_boris_chen_tier_dict():

    data = load_json_from_azure_storage("borischen_tiers.json", Config.containername, Config.azure_storage_connection_string)
    player_tiers = defaultdict(dict)
    for pos_ranking in data:
        for tier_num in data[pos_ranking]:
            for name in data[pos_ranking][tier_num]:
                if len(name.split()) >= 3:
                    if "Sr." in name or "Jr." in name or "III" in name or "II" in name:
                        shortened_name = " ".join(name.split()[:2])
                        player_tiers[shortened_name][pos_ranking] = tier_num
                player_tiers[name][pos_ranking] = tier_num

    return player_tiers

def prepare_position_groups_for_leagues(user_rosters, pidToPlayerDict):

    league_position_groups = {}

    for roster in user_rosters:
        league_name = roster["league"]
        position_groups = defaultdict(list)
        for pid in roster["pids"]:
            player = pidToPlayerDict[pid]
            position = player["fantasy_positions"][0]
            try:
                name = Config.nfl_teams[pid] if pid in Config.nfl_teams else player["full_name"]
            except:
                logger.info("Error handling player with pid " + str(pid) + ", " + str(player))
            position_groups[position].append(name)
        league_position_groups[league_name] = position_groups
    return league_position_groups

def form_suggested_starts_based_on_boris(user_rosters, league_position_groups, boris_chen_tiers, nameToPidDict):

    suggested_starts = {}

    #sportsbook_projections = load_json_from_azure_storage("sportsbook_proj.json", Config.containername, Config.azure_storage_connection_string)
    sportsbook_projections = load_json_from_azure_storage("hand_calculated_projections.json", Config.containername, Config.azure_storage_connection_string)
    backup_projections = load_json_from_azure_storage("backup_fantasypros_projections.json", Config.containername, Config.azure_storage_connection_string)
    fantasypros_data = load_json_from_azure_storage("fantasypros_data.json", Config.containername, Config.azure_storage_connection_string)
    player_data = load_json_from_azure_storage("players.json", Config.containername, Config.azure_storage_connection_string)

    for roster in user_rosters:
        position_groups = copy(league_position_groups[roster["league"]])
        normal_prefix, te_prefixes = get_tier_page_names_from_league_settings(roster["settings"])
        starting_positions = clean_up_pos_names(roster["positions"])
        #free_agents = [name for name,pid in nameToPidDict.items() if pid not in roster["all_owned"]]
        settings = roster["settings"]

        tiers_to_lookup = set()
        for pos_name, num_of_pos in starting_positions.items():
            if pos_name in ["RB", "WR", "Flex"]:
                tiers_to_lookup.add(normal_prefix + pos_name)
            elif pos_name == "TE":
                tiers_to_lookup.add(te_prefixes + pos_name)
            elif pos_name == "WT":
                tiers_to_lookup.add(normal_prefix + "Flex")
            else:
                tiers_to_lookup.add(pos_name)

        team_rank_dict = {}

        for player in get_all_players_from_position_groups(position_groups):
            pos_rank_dict = {}
            tiers_for_player = tiers_to_lookup.intersection(boris_chen_tiers[player])
            if len(tiers_for_player) == 0:
                pos_rank_dict["Position"] = "Unranked"

            # We will manually add RB/WR with a tier of <= 3 for their position to be rank 1 flex if their flex ranking DNE
            top_tier_player_flag = False
            for tier in tiers_for_player:
                tier_rank = boris_chen_tiers[player][tier]
                cleaned_pos_name = tier
                for prefix in [normal_prefix, te_prefixes]:
                    cleaned_pos_name = cleaned_pos_name.replace(prefix, "")
                pos_rank_dict[cleaned_pos_name] = tier_rank
                if int(tier_rank) <= 4 and cleaned_pos_name != "TE":
                    top_tier_player_flag = True

            if top_tier_player_flag and "Flex" not in pos_rank_dict:
                pos_rank_dict["Flex"] = "1"
            
            team_rank_dict[player] = pos_rank_dict

        logger.info("Building table for " + str(roster["league"]) + ".")

        roster_table = defaultdict(list)

        pos_groups_copy = deepcopy(position_groups)
        full_roster_positions = deepcopy(roster["positions"])
        if len(roster['pids']) > len(full_roster_positions):
            full_roster_positions.extend(["BN"]*(1 + len(roster['pids']) - len(roster["positions"])))

        stat_point_multipliers = Config.get_stat_point_multipliers(settings)

        for pos_name in full_roster_positions:
            cleaned_name = clean_up_pos_names([pos_name])
            
            if cleaned_name == "WT":
                cleaned_name = "WR"
            elif cleaned_name == "SF":
                cleaned_name = "QB"
            elif cleaned_name == "DST":
                cleaned_name = "DEF"
            elif cleaned_name == "BN":
                try:
                    cleaned_name = next(iter(pos_groups_copy.keys()))
                except:
                    logger.info("I believe pos groups copy is probably empty.  Lemme check " + str(pos_groups_copy))

            players, pos_added = list_players_for_pos_name(pos_groups_copy, cleaned_name)

            high_name, high_rank = get_highest_ranked_player_from_page(
                players,
                cleaned_name,
                team_rank_dict,
                sportsbook_projections,
                backup_projections,
                stat_point_multipliers
            )

            roster_table[pos_name].append({"Name": high_name, "Tiers": team_rank_dict[high_name] if high_name in team_rank_dict else {cleaned_name: "Unranked"}})
            for pos in pos_added:
                if high_name in pos_groups_copy[pos]:
                    if len(pos_groups_copy[pos]) == 1:
                        del pos_groups_copy[pos]
                    else:
                        pos_groups_copy[pos].remove(high_name)

        # deal with leagues with players on IR or taxi squad etc.
        for position, player_list in pos_groups_copy.items():
            for player in player_list:
                cleaned_name = next(iter(pos_groups_copy.keys()))
                roster_table["BN"].append({"Name": player, "Tiers": {position: "Unranked"}})

                    
        suggested_starts_for_roster = []

        for pos, player_dict_list in roster_table.items():
            for player_dict in player_dict_list:
                temp_dict = {"POS": pos, "NAME": player_dict["Name"]}
                if player_dict["Name"] in nameToPidDict:
                    temp_dict["PID"] = nameToPidDict[player_dict["Name"]]
                    try:
                        temp_dict["REALLIFE_POS"] = player_data[temp_dict["PID"]]["fantasy_positions"][0]
                    except:
                        logger.info("Probably a defense" + str(player_dict["Name"]))
                        temp_dict["REALLIFE_POS"] = "DEF"
                else:
                    try:
                        temp_dict["TEAM"] = Config.nfl_teams_reverse_lookup[player_dict["Name"]]
                    except:
                        logger.info("I'm guessing that this is cause you don't have a defense or kicker.  Checking : " + str(pos))
                for tier, ranking in player_dict["Tiers"].items():
                    if "Flex" not in tier:
                        temp_dict["POS_RANK"] = str(ranking)
                    else:
                        temp_dict["FLEX"] = str(ranking)
                if pos != "DST" and pos != "DEF" and pos != "K":
                    projected_scoring, old_projection, statline, boom_bust = calculate_potential_fantasy_score(player_dict["Name"], pos, sportsbook_projections, backup_projections, stat_point_multipliers)
                    temp_dict["VEGAS"] = str(round(projected_scoring, 2))
                    temp_dict["VEGAS_STATS"] = statline
                    if boom_bust is not None:
                        temp_dict["BOOM"] = round(boom_bust["boom"] * 100, 2)
                        temp_dict["BUST"] = round(boom_bust["bust"] * 100, 2)
                    else:
                        temp_dict["BOOM"] = "N/A. Not enough vegas props"
                        temp_dict["BUST"] = "N/A"
                    if old_projection:
                        temp_dict["VEGAS"] += "\t Old projection, no lines available, confirm uninjured"

                    p_info_dict = fantasypros_data[player_dict["Name"]] if player_dict["Name"] in fantasypros_data else None
                    logger.info("Getting info dict for " + player_dict["Name"])
                    if p_info_dict:
                       temp_dict["MATCHUP_RATING"] = p_info_dict["Opponent Rating"] if "Opponent Rating" in p_info_dict else "UNKNOWN"
                       temp_dict["TEAM_NAME"] = p_info_dict["Team Name"] if "Team Name" in p_info_dict else "UNKNOWN"
                else:
                    temp_dict["VEGAS"] = "N/A"
                suggested_starts_for_roster.append(temp_dict)
        
        suggested_starts[str(roster["league"])] = suggested_starts_for_roster

    return suggested_starts


# Will round non standard TE Premium settings to either 0.5 PPR or full PPR depending.
def get_tier_page_names_from_league_settings(settings):
    ppr = settings["rec"]
    if "bonus_rec_te" in settings:
        te_ppr = ppr + settings["bonus_rec_te"] 
    else:
        te_ppr = ppr

    if ppr == 0:
        rb_wr_flex_prefix = ""
    elif ppr == 0.5:
        rb_wr_flex_prefix = "0.5 PPR "
    elif ppr >= 1:
        rb_wr_flex_prefix = "PPR "

    if te_ppr == 0:
        te_prefix = ""
    elif te_ppr < 0.25:
        te_prefix = ""
    elif te_ppr <= 0.5:
        te_prefix = "0.5 PPR "
    elif te_ppr < 0.75:
        te_prefix = "0.5 PPR "
    else:
        te_prefix = "PPR "

    return rb_wr_flex_prefix, te_prefix

# Returns a list of position names if given a list, and a single name otherwise
def clean_up_pos_names(pos_names):
    cleaned_pos = defaultdict(int)
    for pos in pos_names:
        if pos == "BN":
            continue
        elif pos == "FLEX":
            cleaned_pos["Flex"]+=1
        elif pos == "SUPER_FLEX":
            cleaned_pos["SF"]+=1
        elif pos == "REC_FLEX":
            cleaned_pos["WT"]+=1
        elif pos == "DEF":
            cleaned_pos["DST"]+=1
        else:
            cleaned_pos[pos]+=1
    
    if len(cleaned_pos) == 0:
        return "BN"
    elif len(cleaned_pos) == 1:
        return next(iter(cleaned_pos))
    return cleaned_pos

def list_players_for_pos_name(pos_groups, pos_name):
    if pos_name == "Flex":
        pos_to_add = ["WR", "TE", "RB"]
    elif pos_name == "WT":
        pos_to_add = ["WR", "TE"]
    elif pos_name == "SF":
        pos_to_add = ["QB"]
    else:
        pos_to_add = [pos_name]

    players = []

    for pos in pos_to_add:
        players.extend(pos_groups[pos])

    return players, pos_to_add

def get_highest_ranked_player_from_page(
    list_of_players,
    pos_name,
    team_rank_dict,
    sportsbook_projections,
    backup_projections,
    stat_point_multipliers
):
    """
    Pick the best player by:
      1. Lowest Boris Chen tier at the given position
      2. If tied, lowest Flex tier (if available)
      3. If still tied, highest Vegas projection
    """
    if len(list_of_players) == 0:
        return "None Owned", "N/A"

    best_player = None
    best_tier = float("inf")
    best_flex = float("inf")
    best_proj = -float("inf")

    for player in list_of_players:
        # Step 1: Tier rank
        tier = int(team_rank_dict[player][pos_name]) if (player in team_rank_dict and pos_name in team_rank_dict[player]) else 999

        # Step 2: Flex rank (optional injection)
        flex = int(team_rank_dict[player]["Flex"]) if (player in team_rank_dict and "Flex" in team_rank_dict[player]) else 999

        # Step 3: Vegas projection
        projected_points, _, _, _ = calculate_potential_fantasy_score(
            player, pos_name, sportsbook_projections, backup_projections, stat_point_multipliers
        )

        # Compare: first by tier, then flex, then Vegas
        if (
            tier < best_tier
            or (tier == best_tier and flex < best_flex)
            or (tier == best_tier and flex == best_flex and projected_points > best_proj)
        ):
            best_player = player
            best_tier = tier
            best_flex = flex
            best_proj = projected_points

    if best_player:
        return best_player, best_tier
    else:
        return list_of_players[0], "Unranked"


def get_all_players_from_position_groups(position_groups):
    players = []
    for names in position_groups.values():
        players.extend(names)
    return players

def calculate_potential_fantasy_score(player, pos_group, player_stat_projections, backup_stat_projections, stat_point_multipliers):

    if pos_group == "TE":
        rec_points = stat_point_multipliers["TE Receptions"]
    else:
        rec_points = stat_point_multipliers["Receptions"]

    playerkey = ''.join(char for char in player if char.isalnum()).lower()

    p_projections = player_stat_projections[playerkey] if playerkey in player_stat_projections else {}
    backup_projections = backup_stat_projections[playerkey] if playerkey in backup_stat_projections else {}
    statline = ", ".join([str(key) + ": " + str(round(proj,2)) for key, proj in p_projections.items() if key not in ["Opponent Rating", "Team Name", "Simulations"]])
    if len(p_projections) == 0 and len(backup_projections) == 0:
        logger.info("Didnt find " + player + " in standard or backup projections")
        return 0, False, "No stats projected for player.", None
    

    if stat_point_multipliers["Passing Touchdowns"] > 4:
        six_point_td = True
    else:
        six_point_td = False

    proj_points = 0
    boom_bust_probabilities = None
    for key, val in p_projections.items():
        if key == "Opponent Rating" or key == "Team Name":
            continue
        if key == "Simulations":
            boom_bust = val
            if "error" in boom_bust:
                boom_bust_probabilities = None
            elif "QB_6PT" in boom_bust or "QB_STD" in boom_bust:
                if six_point_td:
                    boom_bust_probabilities = boom_bust["QB_6PT"]
                else:
                    boom_bust_probabilities = boom_bust["QB_STD"]
            else:
                if rec_points < 0.3:
                    boom_bust_probabilities = boom_bust["STD"]
                elif rec_points >= 0.3 and rec_points < 0.75:
                    boom_bust_probabilities = boom_bust["HalfPPR"]
                else:
                    boom_bust_probabilities = boom_bust["PPR"]
            continue
        if key == "Receptions":
            proj_points += float(val) * rec_points
        else:
            proj_points += float(val) * stat_point_multipliers[key]
    
    try:
        missing_projections = [key for key in backup_projections if key not in p_projections]
        logger.info("Backup projections loaded.  The projections missing from that were " + ", ".join([key for key in missing_projections]))
        for key in missing_projections:
            if key == "Opponent Rating" or key == "Team Name":
                continue
            if key == "Receptions":
                proj_points += float(backup_projections[key]) * rec_points
            else:
                proj_points += float(backup_projections[key]) * stat_point_multipliers[key]
    except Exception as e:
        logger.info("Exception was " + str(e))

    return proj_points, False, statline, boom_bust_probabilities