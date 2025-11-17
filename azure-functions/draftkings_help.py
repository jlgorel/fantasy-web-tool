import requests
import json
import os
import time
import math
from config import Config
from random import randint
from bs4 import BeautifulSoup
from azure.storage.blob import BlobServiceClient
from collections import defaultdict
import numpy as np
from playwright.sync_api import sync_playwright


## helper methods

def load_json_from_azure_storage(blob_name, container_name, connection_string):
    # Initialize the BlobServiceClient with the provided connection string
    blob_service_client = BlobServiceClient.from_connection_string(connection_string)

    # Get the blob client
    blob_client = blob_service_client.get_blob_client(container=container_name, blob=blob_name)

    # Download the blob content
    blob_data = blob_client.download_blob()
    data = json.loads(blob_data.readall())

    return data

def normalize_name_to_sleeper(name):
    #Normalize names to look like sleeper names
    if "Sr." in name or "Jr." in name or "III" in name or "II" in name:
        name = " ".join(name.split()[:2])
    if name == "DeVon Achane":
        name = "De'Von Achane"
    elif name == "D.J. Moore":
        name = "DJ Moore"
    elif name == "Lamar Jackson (BAL)":
        name = "Lamar Jackson"
    elif name == "Gabriel Davis":
        name = "Gabe Davis"
    elif name == "Demario Douglas":
        name = "DeMario Douglas"
    elif name == "Scott Miller":
        name = "Scotty Miller"
    elif name == "Andrew Ogletree":
        name = "Drew Ogletree"
    elif name == "A.J. Barner":
        name = "AJ Barner"
    elif name == "Patrick Mahomes II":
        name = "Patrick Mahomes"
    elif name == "Marquise Brown":
        name = "Hollywood Brown"
    return name

def sample_from_ranges(ranges_probs, n_sims):
    """Sample n_sims values from { (low, high): prob } ranges."""
    ranges, probs = zip(*ranges_probs.items())
    probs = np.array(probs) / np.sum(probs)
    chosen_ranges = np.random.choice(len(ranges), size=n_sims, p=probs)

    # For each chosen range, pick midpoint (or exact value if low==high)
    values = []
    for idx in chosen_ranges:
        low, high = ranges[idx]
        if low == high:
            values.append(low)
        elif np.isinf(high):
            # Handle open-ended like (125, inf): use low*1.1 as approx
            values.append(low * 1.1)
        else:
            values.append((low + high) / 2)
    return np.array(values)

def run_player_sim(player_stats, n_sims=10000):
    """
    Run Monte Carlo sim for one player using stat_probabilities dict.
    Returns boom/bust probabilities per scoring type, plus percentiles 1–100.
    """
    # --- Detect QB ---
    is_qb = "Passing Yards" in player_stats or "Passing Touchdowns" in player_stats

    # --- Sample stats ---
    sims = { "Receptions": np.zeros(n_sims),
             "Passing Yards": np.zeros(n_sims),
             "Passing Touchdowns": np.zeros(n_sims),
             "Interceptions": np.zeros(n_sims),
             "Anytime Touchdown": np.zeros(n_sims),
             "Receiving Yards": np.zeros(n_sims),
             "Rushing Yards": np.zeros(n_sims) }

    for stat, dist in player_stats.items():
        sims[stat] = sample_from_ranges(dist, n_sims)

    # --- Scoring ---
    results = {}

    if is_qb:
        # Standard QB scoring (4pt pass TD)
        std_points = (
            sims["Passing Yards"] * 0.04 +  # 1 pt / 25 yd
            sims["Passing Touchdowns"] * 4 +
            sims["Interceptions"] * -2 +
            sims["Rushing Yards"] * 0.1 +
            sims["Receiving Yards"] * 0.1 +
            sims["Anytime Touchdown"] * 6
        )
        # 6pt QB scoring
        sixpt_points = (
            sims["Passing Yards"] * 0.04 +
            sims["Passing Touchdowns"] * 6 +
            sims["Interceptions"] * -2 +
            sims["Rushing Yards"] * 0.1 +
            sims["Receiving Yards"] * 0.1 +
            sims["Anytime Touchdown"] * 6
        )

        results["QB_STD"] = {
            "boom": float(np.mean(std_points >= 30)),
            "bust": float(np.mean(std_points <= 12)),
            "mean": float(np.mean(std_points)),
            "percentiles": {p: float(np.percentile(std_points, p)) for p in range(1, 101)}
        }
        results["QB_6PT"] = {
            "boom": float(np.mean(sixpt_points >= 35)),
            "bust": float(np.mean(sixpt_points <= 15)),
            "mean": float(np.mean(sixpt_points)),
            "percentiles": {p: float(np.percentile(sixpt_points, p)) for p in range(1, 101)}
        }

    else:
        # Non-QB scoring
        std_points = (
            sims["Receiving Yards"] * 0.1 +
            sims["Rushing Yards"] * 0.1 +
            sims["Anytime Touchdown"] * 6
        )
        half_ppr = std_points + sims["Receptions"] * 0.5
        ppr = std_points + sims["Receptions"] * 1.0

        results["STD"] = {
            "boom": float(np.mean(std_points > 18)),
            "bust": float(np.mean(std_points < 5)),
            "mean": float(np.mean(std_points)),
            "percentiles": {p: float(np.percentile(std_points, p)) for p in range(1, 101)}
        }
        results["HalfPPR"] = {
            "boom": float(np.mean(half_ppr > 22)),
            "bust": float(np.mean(half_ppr < 6)),
            "mean": float(np.mean(half_ppr)),
            "percentiles": {p: float(np.percentile(half_ppr, p)) for p in range(1, 101)}
        }
        results["PPR"] = {
            "boom": float(np.mean(ppr > 26)),
            "bust": float(np.mean(ppr < 6)),
            "mean": float(np.mean(ppr)),
            "percentiles": {p: float(np.percentile(ppr, p)) for p in range(1, 101)}
        }

    return results

def odds_to_probability(odds):
    """
    Convert American odds to implied probability.

    Parameters:
    odds (int): The American odds. Can be positive or negative.

    Returns:
    float: The implied probability (as a decimal between 0 and 1).
    """
    if odds > 0:
        # Positive odds
        probability = 100 / (odds + 100)
    else:
        # Negative odds
        probability = -odds / (-odds + 100)
    
    return probability

def expected_anytime_touchdown(odds_one_or_more, odds_two_or_more):
    """
    Calculate the expected number of touchdowns given odds of scoring 1 or more
    touchdowns and 2 or more touchdowns.

    Parameters:
    odds_one_or_more (int): American odds for scoring 1 or more touchdowns.
    odds_two_or_more (int): American odds for scoring 2 or more touchdowns.

    Returns:
    float: Expected number of touchdowns.
    """
    # Convert American odds to probability
    P1 = odds_to_probability(odds_one_or_more)
    P2 = odds_to_probability(odds_two_or_more) if odds_two_or_more != 0 else 0
    
    # Calculate the probability of exactly 1 touchdown
    P_exactly_one = P1 - P2
    
    # Expected number of touchdowns
    expected_touchdowns = P_exactly_one * 1 + P2 * 2

    exact_probs = {
        (0,0): 1-P1,
        (1,1): P_exactly_one,
        (2,2): P2
    }
    
    return expected_touchdowns, exact_probs

def over_under_projection(line, odds_over, odds_under, stat_type="generic"):
    """
    Calculate the projected number of some stat based on over/under odds,
    and return both the expected value and a probability distribution.

    Returns:
        tuple: (projected_value: float, exact_probs: dict)
    """
    # --- keep your original projected value logic ---
    probability_over = odds_to_probability(odds_over)
    probability_under = odds_to_probability(odds_under)

    total_probability = probability_over + probability_under
    if total_probability == 0:
        return None, {}

    normalized_prob_over = probability_over / total_probability
    normalized_prob_under = probability_under / total_probability

    projected_value = (
        (normalized_prob_over * math.ceil(line))
        + (normalized_prob_under * math.floor(line))
    )

    # --- build a distribution for exact_probs ---
    if stat_type == "interceptions":
        max_val = 3  # interceptions capped at 3
    elif stat_type == "receptions":
        max_val = int(max(12, math.ceil(line) + 6))  # allow some upside
    else:
        max_val = int(math.ceil(line) * 3)

    values = np.arange(0, max_val + 1)
    sigma = max(1.0, line * 0.3)  # variance grows with line
    weights = np.exp(-0.5 * ((values - line) / sigma) ** 2)

    # Split into over/under halves
    mask_over = values >= math.ceil(line)
    mask_under = ~mask_over

    weights_over = weights * mask_over
    weights_under = weights * mask_under

    if weights_over.sum() > 0:
        weights_over *= normalized_prob_over / weights_over.sum()
    if weights_under.sum() > 0:
        weights_under *= normalized_prob_under / weights_under.sum()

    weights = weights_over + weights_under
    weights /= weights.sum()

    exact_probs = {
        (int(v), int(v)): float(p)
        for v, p in zip(values, weights)
        if p > 1e-6
    }

    return projected_value, exact_probs

def calculate_exact_probs(lines, probabilities):
    sorted_lines = sorted(lines)
    exact_probs = {}

    for i, line in enumerate(sorted_lines):
        if i == len(sorted_lines)-1:
            # Probability of exactly `line` events
            exact_probs[line] = probabilities[line]  # Initial case
        else:
            # Probability of exactly `line` events
            exact_probs[line] = probabilities[line] - probabilities[sorted_lines[i + 1]]

    return exact_probs

def devig_probability(prob: float, vig: float) -> float:
    """Remove the vig from the implied probability."""
    return prob / (1 + vig)

def calculate_expected_tds(odds_dict: dict, vig: float) -> float:
    """Calculate the expected value given variable lines and vig."""
    # Step 1: Convert odds to implied probabilities and remove vig
    probabilities = {
        line: devig_probability(odds_to_probability(odds), vig)
        for line, odds in odds_dict.items()
    }

    # Step 2: Calculate the probability of 0 touchdowns
    p_0 = 1 - probabilities[min(probabilities)]  # 1+ is the minimum line

    # Step 3: Calculate the exact probabilities for each outcome
    exact_probs = calculate_exact_probs(list(odds_dict.keys()), probabilities)

    # Step 4: Calculate the expected value (EV) for touchdowns
    expected_val = 0 * p_0  # Start with 0 TDs case
    for line, prob in exact_probs.items():
        expected_val += float(line) * float(prob)

    return expected_val, exact_probs

def calculate_expected_yards(odds_dict, vig , name="default"):
    """Calculate expected yards using linear interpolation."""
    # Step 1: Convert odds to probabilities and remove vig
    probabilities = {
        yards: devig_probability(odds_to_probability(odds), vig)
        for yards, odds in odds_dict.items()
    }

    # Step 2: Sort yardage lines in ascending order
    sorted_yards = sorted(probabilities.keys())

    # Step 3: Calculate exact probabilities for each range
    exact_probs = {}
    for i in range(len(sorted_yards) - 1):
        lower = sorted_yards[i]
        upper = sorted_yards[i + 1]
        exact_probs[(lower, upper)] = probabilities[lower] - probabilities[upper]

    # Add the probability for the highest range (e.g., 125+ yards)
    exact_probs[(0, sorted_yards[0])] = 1 - probabilities[sorted_yards[0]]
    exact_probs[(sorted_yards[-1], float('inf'))] = probabilities[sorted_yards[-1]]

    # Step 4: Calculate expected value using interpolation
    expected_yards = 0
    for (lower, upper), prob in exact_probs.items():
        # Use the midpoint for finite ranges, or a reasonable guess for infinite
        if upper == float('inf'):
            midpoint = float(lower) * 1.1
        else:
            midpoint = (float(lower) + float(upper)) / 2
        expected_yards += prob * midpoint

    return expected_yards, exact_probs

def get_draftkings_data():
    all_draftkings_odds = {}

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1920, "height": 1080})
        page = context.new_page()

        for prop_name, mapping in Config.prop_name_to_ids_map.items():

            stat_name = Config.prop_name_to_stat_name_map[prop_name]

            if type(mapping) != int:
                url = "https://sportsbook-nash.draftkings.com/api/sportscontent/dkusmi/v1/leagues/88808/categories/{}/subcategories/{}?format=json".format(mapping[0], mapping[1])
            else:
                url = "https://sportsbook-nash.draftkings.com/api/sportscontent/dkusmi/v1/leagues/88808/categories/{}?format=json".format(str(mapping))

            page.goto(url)
            page.wait_for_load_state("networkidle")
            content = page.content()
            body_text = page.inner_text("body")

            try:
                json_data = json.loads(body_text)
            except json.JSONDecodeError as e:
                print("Failed to parse JSON:")
                print(e)
                continue

            all_draftkings_odds[stat_name] = json_data

            time.sleep(randint(1,3))

        browser.close()

    return all_draftkings_odds

def has_all_vegas_stats(stat_dict):
    has_all_stats = False
    note = ""
    if "Passing Yards" in stat_dict or "Passing Touchdowns" in stat_dict:
        # this is a qb
        has_all_stats = "Passing Yards" in stat_dict and "Passing Touchdowns" in stat_dict and "Interceptions" in stat_dict
        if "Anytime Touchdown" not in stat_dict or "Rushing Yards" not in stat_dict:
            note = "Missing rushing props"
    elif "Rushing Yards" in stat_dict:
        # most likely a rb
        has_all_stats = "Rushing Yards" in stat_dict and "Receptions" in stat_dict and "Receiving Yards" in stat_dict and "Anytime Touchdown" in stat_dict
    else:
        # tight end or WR
        has_all_stats = "Receiving Yards" in stat_dict and "Receptions" in stat_dict and "Anytime Touchdown" in stat_dict
    return has_all_stats, note
def form_player_projections_dict():
    all_draftkings_odds = get_draftkings_data()
    players = load_json_from_azure_storage("players.json", Config.containername, Config.azure_storage_connection_string)
    sleeper_names = [player_info["full_name"] for player_info in players.values() if "full_name" in player_info]

    expected_stats = defaultdict(dict)
    stat_probabilities = defaultdict(dict)
    
    for stat_name, data in all_draftkings_odds.items():
        if stat_name == "Anytime Touchdown":
            td_odds_dict = defaultdict(dict)
            for odds_information in data["selections"]:
                outcome = odds_information["outcomeType"]
                if outcome not in Config.relevant_td_outcomes or "participants" not in odds_information:
                    continue
                name = normalize_name_to_sleeper(odds_information["participants"][0]["name"])

                if name not in sleeper_names:
                    print(name + " is not found in the list of sleeper names that we have downloaded")
                    continue
                american_odds = odds_information["displayOdds"]["american"]
                try:
                    odds_as_int = int(american_odds)
                except ValueError:
                    odds_as_int = -1 * int(american_odds[1:])
                if "2" in outcome:
                    td_odds_dict[name]["twoplus"] = odds_as_int
                else:
                    td_odds_dict[name]["anytime"] = odds_as_int

            for name, p_odds_dict in td_odds_dict.items():
                lowercase_name = ''.join(char for char in name if char.isalnum()).lower()
                expected_stats[lowercase_name][stat_name], stat_probabilities[lowercase_name][stat_name] = expected_anytime_touchdown(odds_one_or_more=p_odds_dict["anytime"] if "anytime" in p_odds_dict else 0, odds_two_or_more=p_odds_dict["twoplus"] if "twoplus" in p_odds_dict else 0)
        elif stat_name in Config.alt_line_names:
            td_flag = False
            if "TD" in stat_name:
                td_flag = True

            lines_and_odds = defaultdict(dict)

            for odds_information in data["selections"]:
                over_yards = int(odds_information["label"].replace("+", ""))
                name = normalize_name_to_sleeper(odds_information["participants"][0]["name"])
                if name not in sleeper_names:
                    print(name + " is not found in the list of sleeper names that we have downloaded")
                    continue
                american_odds = odds_information["displayOdds"]["american"]
                try:
                    odds_as_int = int(american_odds)
                except ValueError:
                    odds_as_int = -1 * int(american_odds[1:])
                lines_and_odds[name][over_yards] = odds_as_int
            
            for name, odds_dict in lines_and_odds.items():
                lowercase_name = ''.join(char for char in name if char.isalnum()).lower()

                if td_flag:
                    expected_stats[lowercase_name][stat_name], stat_probabilities[lowercase_name][stat_name] = calculate_expected_tds(odds_dict, 0.071)
                else:
                    expected_stats[lowercase_name][stat_name], stat_probabilities[lowercase_name][stat_name] = calculate_expected_yards(odds_dict, 0.071, "default" if name != "Malik Nabers" else name)    
        else:
            temp_dict = defaultdict(dict)
            for odds_information in data["selections"]:
                outcome = odds_information["outcomeType"]
                if (outcome != "Over" and outcome != "Under") or "participants" not in odds_information:
                    print("Unexpected outcome " + outcome + " or lack of participants here.  Skipping!" )
                    continue
                name = normalize_name_to_sleeper(odds_information["participants"][0]["name"])
                if name not in sleeper_names:
                    print(name + " is not found in the list of sleeper names that we have downloaded")
                    continue
                american_odds = odds_information["displayOdds"]["american"]
                try:
                    odds_as_int = int(american_odds)
                except ValueError:
                    odds_as_int = -1 * int(american_odds[1:])
                temp_dict[name][outcome] = odds_as_int
                temp_dict[name]["Line"] = float(odds_information["points"]) 

            for name, p_odds_dict in temp_dict.items():
                lowercase_name = ''.join(char for char in name if char.isalnum()).lower()
                expected_stats[lowercase_name][stat_name], stat_probabilities[lowercase_name][stat_name] = over_under_projection(line=p_odds_dict["Line"], odds_over=p_odds_dict["Over"], odds_under=p_odds_dict["Under"])
    for player, stats in stat_probabilities.items():
        has_all_stats, note = has_all_vegas_stats(stats)
        if not has_all_stats:  # skip if no props
            expected_stats[player]["Simulations"] = {"error": "Not enough data"}
            continue
        try:
            expected_stats[player]["Simulations"] = run_player_sim(stats, n_sims=10000)
        except Exception as e:
            expected_stats[player]["Simulations"] = {"error": str(e)}
    return expected_stats

                    

