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
    league_position_groups = prepare_position_groups_for_leagues(user_rosters, pidToPlayerDict, nameToPidDict)
    suggested_lineups, free_agent_pickups = form_suggested_starts_based_on_boris(user_rosters, league_position_groups, boris_chen_dict, nameToPidDict)

    return suggested_lineups, free_agent_pickups

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

    curr_leagues = [{"name": league["name"], "id": league["league_id"]} for league in data if league["status"] == "in_season"]
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

def prepare_position_groups_for_leagues(user_rosters, pidToPlayerDict, nameToPidDict):

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
        free_agents = [pid for name,pid in nameToPidDict.items() if pid not in roster["all_owned"]]
        for pid in free_agents:
            player = pidToPlayerDict[pid]
            position = player["fantasy_positions"][0]
            try:
                name = Config.nfl_teams[pid] if pid in Config.nfl_teams else player["full_name"]
            except:
                logger.info("Error handling player with pid " + str(pid) + ", " + str(player))
            position_groups["FA_"+position].append(name)
        
        league_position_groups[league_name] = position_groups
    return league_position_groups

def form_suggested_starts_based_on_boris(user_rosters, league_position_groups, boris_chen_tiers, nameToPidDict):

    suggested_starts = {}
    suggested_pickups_for_roster = {}

    #sportsbook_projections = load_json_from_azure_storage("sportsbook_proj.json", Config.containername, Config.azure_storage_connection_string)
    sportsbook_projections = load_json_from_azure_storage("hand_calculated_projections.json", Config.containername, Config.azure_storage_connection_string)
    backup_projections = load_json_from_azure_storage("backup_fantasypros_projections.json", Config.containername, Config.azure_storage_connection_string)
    fantasypros_data = load_json_from_azure_storage("fantasypros_data.json", Config.containername, Config.azure_storage_connection_string)

    for roster in user_rosters:
        position_groups = copy(league_position_groups[roster["league"]])
        normal_prefix, te_prefixes = get_tier_page_names_from_league_settings(roster["settings"])
        starting_positions = clean_up_pos_names(roster["positions"])
        settings = roster["settings"]
        user_players, free_agents = get_all_players_from_position_groups(position_groups)
        players_with_keys = {"User": user_players, "FA": free_agents}


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
        fa_rank_dict = {}

        for key, player_list in players_with_keys.items():
            for player in player_list:
                pos_rank_dict = {}
                if player in boris_chen_tiers:
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
                        if int(tier_rank) <= 3 and cleaned_pos_name != "TE":
                            top_tier_player_flag = True

                    if top_tier_player_flag and "Flex" not in pos_rank_dict:
                        pos_rank_dict["Flex"] = "1"
                else:
                    pos_rank_dict["Position"] = "Unranked"
                if key == "User":
                    team_rank_dict[player] = pos_rank_dict
                elif key == "FA":
                    fa_rank_dict[player] = pos_rank_dict
                

        logger.info("Building table for " + str(roster["league"]) + ".")

        roster_table = defaultdict(list)

        pos_groups_copy = deepcopy(position_groups)

        for pos_name in roster["positions"]:
            cleaned_name = clean_up_pos_names([pos_name])
            
            if cleaned_name == "WT":
                cleaned_name = "Flex"
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

            high_name, high_rank = get_highest_ranked_player_from_page(players, cleaned_name, team_rank_dict)
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

        # map free agent player names to their fantasy scoring potential:
        free_agent_positions_and_points = {}

        for player, pos_rank_dict in fa_rank_dict:
            if "RB" in pos_rank_dict:
                pos = "RB"
            elif "WR" in pos_rank_dict:
                pos = "WR"
            elif "TE" in pos_rank_dict:
                pos = "TE"
            elif "QB" in pos_rank_dict:
                pos = "QB"
            else:
                continue
            temp_dict = {"POS": pos, "NAME": player}
            if player in nameToPidDict:
                temp_dict["PID"] = nameToPidDict[player]
            else:
                logger.info("Couldnt find the pid for player " + str(player))
                continue
            projected_scoring, old_projection = calculate_potential_fantasy_score(player, pos, sportsbook_projections, backup_projections, stat_point_multipliers)
            
            # Do the stuff for positional specific pickups

            if pos in free_agent_positions_and_points:
                _, max_score = free_agent_positions_and_points[pos]
                if projected_scoring > max_score:
                    free_agent_positions_and_points[pos] = (player, round(projected_scoring, 2))
            else:
                free_agent_positions_and_points[pos] = (player, round(projected_scoring, 2))

            if pos != "QB":
                if "Flex" in free_agent_positions_and_points:
                    _, max_score = free_agent_positions_and_points["Flex"]
                    if projected_scoring > max_score:
                        free_agent_positions_and_points["Flex"] = (player, round(projected_scoring, 2))
        free_agents_as_list = []
        for position, player_and_score_tuple in free_agent_positions_and_points.items():
            temp_dict = {"POS": position, "NAME": player_and_score_tuple[0], "VEGAS": str(player_and_score_tuple[1])}
            free_agents_as_list.append(temp_dict)
        suggested_pickups_for_roster[str(roster["league"])] = free_agents_as_list

        logger.info("Best free agent pickups for each position are " + str(free_agent_positions_and_points))                    
        suggested_starts_for_roster = []

        stat_point_multipliers = Config.get_stat_point_multipliers(settings)

        for pos, player_dict_list in roster_table.items():
            for player_dict in player_dict_list:
                temp_dict = {"POS": pos, "NAME": player_dict["Name"]}
                if player_dict["Name"] in nameToPidDict:
                    temp_dict["PID"] = nameToPidDict[player_dict["Name"]]
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
                    projected_scoring, old_projection = calculate_potential_fantasy_score(player_dict["Name"], pos, sportsbook_projections, backup_projections, stat_point_multipliers)
                    temp_dict["VEGAS"] = str(round(projected_scoring, 2))
                    if old_projection:
                        temp_dict["VEGAS"] += "\t Old projection, no lines available, confirm uninjured"

                    p_info_dict = fantasypros_data[player_dict["Name"]] if player_dict["Name"] in fantasypros_data else None
                    logger.info("Getting info dict for " + player_dict["Name"])
                    if p_info_dict:
                       temp_dict["MATCHUP_RATING"] = p_info_dict["Opponent Rating"] if "Opponent Rating" in p_info_dict else "UNKNOWN"
                       temp_dict["TEAM_NAME"] = p_info_dict["Team Name"] if "Team Name" in p_info_dict else "UNKNOWN"
                else:
                    temp_dict["VEGAS"] = "No vegas scores for DEF/K"
                suggested_starts_for_roster.append(temp_dict)
        
        suggested_starts[str(roster["league"])] = suggested_starts_for_roster

    return suggested_starts, suggested_pickups_for_roster


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

def get_highest_ranked_player_from_page(list_of_players, tier_lookup, team_rank_dict):

    if len(list_of_players) == 0:
        return "None Owned", "N/A"

    high_rank = 20
    high_name = list_of_players[0]

    for player in list_of_players:
        if player in team_rank_dict and tier_lookup in team_rank_dict[player]:
            rank = team_rank_dict[player][tier_lookup]
        else:
            rank = 20
        if int(rank) < int(high_rank):
            high_rank = rank
            high_name = player
    if high_rank != 20:
        return high_name, high_rank  
    else:
        return high_name, "Unranked"

def get_all_players_from_position_groups(position_groups):
    user_players = [name for pos_group, name in position_groups.items() if "FA_" not in pos_group]
    free_agents = [name for pos_group, name in position_groups.items() if "FA_" in pos_group]
    return user_players, free_agents

def calculate_potential_fantasy_score(player, pos_group, player_stat_projections, backup_stat_projections, stat_point_multipliers):

    if pos_group == "TE":
        rec_points = stat_point_multipliers["TE Receptions"]
    else:
        rec_points = stat_point_multipliers["Receptions"]

    playerkey = ''.join(char for char in player if char.isalnum()).lower()

    p_projections = player_stat_projections[playerkey] if playerkey in player_stat_projections else {}
    backup_projections = backup_stat_projections[playerkey] if playerkey in backup_stat_projections else {}
    if len(p_projections) == 0 and len(backup_projections) == 0:
        logger.info("Didnt find " + player + " in standard or backup projections")
        return 0, False
        
    proj_points = 0
    for key, val in p_projections.items():
        if key == "Opponent Rating" or key == "Team Name":
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

    return proj_points, False