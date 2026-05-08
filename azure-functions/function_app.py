import azure.functions as func
import os
import json
import requests
from random import randint, choice
from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup
from config import Config
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from collections import defaultdict
from azure.storage.blob import BlobServiceClient, BlobClient, ContainerClient
from draftkings_help import form_player_projections_dict, normalize_name_to_sleeper, form_all_projections_and_points_dict
import pytz

app = func.FunctionApp()

# ---------------------------------------------------------------------------
# Date / season helpers
# ---------------------------------------------------------------------------
# These delegate to the shared module so the Flask backend and this scraper
# agree on what "this week" / "this season" mean. Edit
# shared/fantasy_common.py and run tools/sync_shared.py to change behavior.
from _fantasy_common import (  # noqa: E402  (after app = ... by design)
    get_current_fantasy_year as _shared_get_current_fantasy_year,
    get_current_nfl_week as _shared_get_current_nfl_week,
    is_in_fantasy_season as _shared_is_in_fantasy_season,
)


def get_current_fantasy_year() -> int:
    return _shared_get_current_fantasy_year()


def is_in_fantasy_season(now: datetime | None = None) -> bool:
    return _shared_is_in_fantasy_season(now)


def get_current_nfl_week() -> int:
    return _shared_get_current_nfl_week()


def format_eastern_runtime(now: datetime | None = None) -> str:
    """Render an Eastern-Time runtime stamp in a Windows + Linux portable way.

    Note: %-m / %-d are non-portable (work on Linux/macOS, crash on Windows).
    Build month/day manually to avoid the platform difference.
    """
    eastern = pytz.timezone("America/New_York")
    et = (now.astimezone(eastern) if now else datetime.now(eastern))
    return f"{et.month}/{et.day} {et.strftime('%I:%M:%S %p %Z')}"


# ---------------------------------------------------------------------------
# Idempotency guard
# ---------------------------------------------------------------------------
# Skip a scrape run if the previous successful run finished within this many
# minutes. Keeps overlapping triggers / retries from re-doing 5+ minutes of
# Playwright + Sleeper fan-out work.
SUCCESSFUL_RUN_DEDUP_MINUTES = 8

# ---------------------------------------------------------------------------
# HTTP defaults
# ---------------------------------------------------------------------------
HTTP_TIMEOUT_SECONDS = 15
HTTP_MAX_RETRIES = 2  # 3 total attempts
_RETRYABLE_STATUS = {429, 500, 502, 503, 504}


def _http_get(url, *, timeout=HTTP_TIMEOUT_SECONDS, max_retries=HTTP_MAX_RETRIES):
    """GET with a sane timeout and exponential-backoff retries on transient
    errors. Raises on the final failure so callers can decide whether to
    surface it or swallow it."""
    last_exc = None
    for attempt in range(max_retries + 1):
        try:
            resp = requests.get(url=url, timeout=timeout)
        except requests.RequestException as e:
            last_exc = e
            if attempt < max_retries:
                time.sleep(0.5 * (2 ** attempt))
                continue
            raise

        if resp.status_code in _RETRYABLE_STATUS and attempt < max_retries:
            time.sleep(0.5 * (2 ** attempt))
            continue
        return resp

    if last_exc:
        raise last_exc
    raise RuntimeError(f"Unreachable retry loop for {url}")


def load_json_from_url(url):
    response = _http_get(url)
    return response.json()

def upload_to_azure_blob(data_dict, blob_name, filename="file"):
    connect_str = os.getenv("AZURE_STORAGE_CONNECTION_STRING")
    if not connect_str:
        raise ValueError("AZURE_STORAGE_CONNECTION_STRING environment variable not set.")

    blob_service_client = BlobServiceClient.from_connection_string(connect_str)
    container_name = Config.container_name  # Make sure this is defined in your config
    blob_client = blob_service_client.get_blob_client(container=container_name, blob=blob_name)

    # Convert the dictionary to JSON
    json_data = json.dumps(data_dict)

    blob_client.upload_blob(json_data, overwrite=True)

    logging.info(f"Uploaded {filename} to Azure Blob Storage as {blob_name}.")

def try_download_blob_json(blob_name):
    """Returns parsed JSON from a blob, or None if the blob is missing/unreadable."""
    try:
        connect_str = os.getenv("AZURE_STORAGE_CONNECTION_STRING")
        if not connect_str:
            return None
        blob_service_client = BlobServiceClient.from_connection_string(connect_str)
        blob_client = blob_service_client.get_blob_client(
            container=Config.container_name, blob=blob_name
        )
        return json.loads(blob_client.download_blob().readall())
    except Exception as e:
        logging.info(f"Could not load {blob_name} from blob storage: {e}")
        return None

def get_sleeper_owned_for_week():
    year = get_current_fantasy_year()
    week = get_current_nfl_week()
    url = "https://api.sleeper.com/players/nfl/research/regular/" + str(year) + "/" + str(week)
    resp = _http_get(url)
    data = resp.json()

    upload_to_azure_blob(data, "owned.json")

    # Also merge this week into the historic per-week blob used by the
    # League Wrapped feature. Shape: { "<pid>": { "<week>": {"owned": x,
    # "started": y} } }. Idempotent — we always overwrite the current
    # week's slot with the latest snapshot, leaving prior weeks untouched.
    history_blob_name = f"owned_history_{year}.json"
    history = try_download_blob_json(history_blob_name) or {}
    week_str = str(week)
    for pid, owned_info in data.items():
        # Some pids only show up later in the season; auto-create their dict.
        history.setdefault(pid, {})[week_str] = owned_info
    upload_to_azure_blob(history, history_blob_name)

    return True

def get_sleeper_player_data():
    url = "https://api.sleeper.app/v1/players/nfl"
    resp = _http_get(url, timeout=60)  # ~5MB payload, allow extra time
    data = resp.json()

    for pid in dict(data):
        if data[pid]["fantasy_positions"] and len(set(data[pid]["fantasy_positions"]).intersection(set(Config.boris_chen_fantasy_relevant_pos))) == 0:
            del data[pid]
            continue

        for key in dict(data[pid]):
            if key not in Config.relevant_sleeper_keys:
                del data[pid][key]

    # update with positional rankings, scoring data, etc

    players_dict = data

    positions = {
        "QB": (50,32),
        "WR": (150,70),
        "TE": (50, 50),
        "RB": (150,70),
        "DEF": (32, 32),
        "K": (50,32)
    }
    playoff_start_week = 14
    year = get_current_fantasy_year()

    season_scoring = defaultdict(dict)
    weekly_scoring = defaultdict(lambda: defaultdict(dict))

    # Parallelize season-level fetches (one per position).
    def _fetch_season(position: str):
        url = f"https://api.sleeper.com/stats/nfl/{year}?season_type=regular&position={position}&order_by=pts_half_ppr"
        return position, load_json_from_url(url)

    with ThreadPoolExecutor(max_workers=6) as pool:
        for position, payload in pool.map(_fetch_season, positions.keys()):
            num_desired_season = positions[position][0]
            season_scoring.update({
                temp_dict["player_id"]: temp_dict["stats"]
                for temp_dict in payload[:num_desired_season]
            })

    # Parallelize weekly fetches (78 calls = 13 weeks * 6 positions).
    week_position_pairs = [
        (week, position)
        for week in range(1, playoff_start_week)
        for position in positions.keys()
    ]

    def _fetch_week(args):
        week, position = args
        url = f"https://api.sleeper.com/stats/nfl/{year}/{week}?season_type=regular&position={position}&order_by=pts_half_ppr"
        return week, position, load_json_from_url(url)

    with ThreadPoolExecutor(max_workers=10) as pool:
        for week, position, payload in pool.map(_fetch_week, week_position_pairs):
            num_desired_week = positions[position][1]
            for stats in payload[:num_desired_week]:
                weekly_scoring[stats["player_id"]][week] = stats["stats"]

    for player, player_data in players_dict.items():
        if player not in weekly_scoring and player not in season_scoring:
            continue
        player_weekly_scoring = weekly_scoring[player]
        player_season_scoring = season_scoring[player]
        temp_scoring_dict = {}
        for week, scoring_data in player_weekly_scoring.items():
            temp_scoring_dict[week] = {
                "half_ppr": scoring_data["pts_half_ppr"] if "pts_half_ppr" in scoring_data else 0,
                "ppr": scoring_data["pts_ppr"] if "pts_ppr" in scoring_data else 0,
                "std": scoring_data["pts_std"] if "pts_std" in scoring_data else 0,
                "receptions": scoring_data["rec"] if "rec" in scoring_data else 0,
                "pass_td": scoring_data["pass_td"] if "pass_td" in scoring_data else 0
            }
        temp_season_scoring_dict = {
            "half_ppr_rank": player_season_scoring["pos_rank_half_ppr"] if "pos_rank_half_ppr" in player_season_scoring else 999,
            "ppr_rank": player_season_scoring["pos_rank_ppr"] if "pos_rank_ppr" in player_season_scoring else 999,
            "std_rank": player_season_scoring["pos_rank_std"] if "pos_rank_std" in player_season_scoring else 999,
            "half_ppr_points": player_season_scoring["pts_half_ppr"] if "pts_half_ppr" in player_season_scoring else 0,
            "ppr_points": player_season_scoring["pts_ppr"] if "pts_ppr" in player_season_scoring else 0,
            "std_points": player_season_scoring["pts_std"] if "pts_std" in player_season_scoring else 0,
            "receptions": player_season_scoring["rec"] if "rec" in player_season_scoring else 0
        }
        if player_data["fantasy_positions"] != None and "QB" in player_data["fantasy_positions"]:
            passing_td_bonus = player_season_scoring["pass_td"] * 2 if "pass_td" in player_season_scoring else 0
            temp_season_scoring_dict["6pt_pass_td_points"] = temp_season_scoring_dict["std_points"] + passing_td_bonus

        players_dict[player]["scoring_data_weekly"] = temp_scoring_dict
        players_dict[player]["scoring_data_season"] = temp_season_scoring_dict

    # go through and get ranks for 6 pt passing td

    qb_list_6_pt = []
    for player, info in players_dict.items():
        if info["fantasy_positions"] is None or "QB" not in info["fantasy_positions"] or "scoring_data_season" not in info:
            continue
        qb_list_6_pt.append((player, info["scoring_data_season"]["6pt_pass_td_points"]))

    qb_list_6_pt.sort(key= lambda x: x[1], reverse=True)
    print(qb_list_6_pt)
    for num, qb in enumerate(qb_list_6_pt):
        players_dict[qb[0]]["scoring_data_season"].update({
            "6pt_pass_td_rank": num,
            "6pt_pass_td_points": qb[1]
        })

    upload_to_azure_blob(players_dict, "players.json")

    # Sibling write: year-stamped slim extract of just the scoring data, used
    # by the League Wrapped feature to compute season-aware accolades like
    # best/worst draft picks and biggest add. Keeping it as its own blob (a)
    # avoids bloating players.json with year-archived data and (b) lets us
    # retain old years even when players.json gets overwritten each week.
    season_scoring_blob = {
        pid: {
            "full_name": pdata.get("full_name"),
            "fantasy_positions": pdata.get("fantasy_positions") or [],
            "scoring_data_weekly": pdata.get("scoring_data_weekly", {}),
            "scoring_data_season": pdata.get("scoring_data_season", {}),
        }
        for pid, pdata in players_dict.items()
        if pdata.get("scoring_data_season")
    }
    upload_to_azure_blob(season_scoring_blob, f"player_season_scoring_{year}.json")

    return True

def retrieve_tiers_from_soup(soup):
    object_tag = soup.find('object', {"type": "text/html"})
    if object_tag:
        data_value = object_tag.get('data')
        if data_value:
            data_response = _http_get(data_value)
            data_response.raise_for_status()
            return data_response.text
    else:
        logging.info("No object tag")

def split_text_into_tier_dict(text):
    lines = str(text).split("\n")
    tiers = []
    for line in lines:
        tiers.append(line[line.find(":") + 1:].split(","))
    tiers.remove([""])
    return tiers

def get_boris_chen_tiers():
    logging.info("Starting borischen scrape method")
    url = 'http://borischen.co'
    response = _http_get(url)
    html_content = response.text

    soup = BeautifulSoup(html_content, 'html.parser')

    sidebar_section_div = soup.find('div', class_='widget PageList')
    if sidebar_section_div:
        widget_content_div = sidebar_section_div.find('div', class_='widget-content')
        if widget_content_div:
            links = []
            a_tags = widget_content_div.find_all('a')
            for a in a_tags:
                href = a.get('href')
                text = a.get_text()
                if href:
                    for pos in Config.boris_chen_fantasy_relevant_pos:
                        if pos in str(text).split():
                            links.append((href, text))
                            break
        else:
            return {'message': 'Error - No <div class="widget-content"> found.'}
    else:
        return {'message': 'Error - No <div class="sidebar-section"> found.'}

    logging.info("Got borischen links")

    # Start Playwright session
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()

            tiers = {}
            for link, name in links:
                logging.info(f"Getting data from link {link}")
                page.goto(link)

                # Use BeautifulSoup to parse the page content
                soup = BeautifulSoup(page.content(), 'html.parser')
                text_tier = retrieve_tiers_from_soup(soup)
                tier_lines = split_text_into_tier_dict(text_tier)
                tier_dict = {}
                def fix_hollywood_brown(p_name):
                    if p_name == "Marquise Brown":
                        return "Hollywood Brown"
                    return p_name
                for num, tier in enumerate(tier_lines):
                    tier_dict[num + 1] = [fix_hollywood_brown(player.strip()) for player in tier]
                tiers[name] = tier_dict

            browser.close()
    except Exception as e:
        logging.info("Playwright failed")
        raise ValueError("Playwright not installed correctly")

    upload_to_azure_blob(tiers, "borischen_tiers.json")

    return {'message': 'Tiers scraped and saved successfully.', 'tiers': tiers}

# helper functions
def get_fantasypros_top_players():

    logging.info("Starting fantasypros scrape!")

    url = "https://www.fantasypros.com/nfl/rankings/half-point-ppr-superflex.php"

    flex_stats_url = "https://www.fantasypros.com/nfl/projections/flex.php?scoring=HALF"
    qb_stats_url = "https://www.fantasypros.com/nfl/projections/qb.php" 
    #TODO - pull down this data to use as backup when calculating scores
    #TODO - list top free agent pickups for each position based on vegas scores

    columnToStatNameDict = {}
    fantasy_pros_projections = {}

    # Get flex rankings
    response = _http_get(flex_stats_url)
    html_content = response.text

    soup = BeautifulSoup(html_content, 'html.parser')

    # Retrieve the page content and parse it with BeautifulSoup
    receiving_flag = False

    # Scrape column headers for the table
    headers = soup.select('thead tr:nth-of-type(2) th') 
    for idx, header in enumerate(headers):
        stat_name = header.find('small').text if header.find('small') else ""
        
        # Skip "POS" column
        if stat_name == "POS":
            continue
        elif stat_name == "ATT":
            receiving_flag = False
        elif stat_name == "REC":
            receiving_flag = True

        # Handle shared stat names
        if stat_name in ["YDS", "TDS"]:
            if receiving_flag:
                stat_name = "REC_" + stat_name
            else:
                stat_name = "RUSH_" + stat_name

        columnToStatNameDict[idx] = stat_name

        # Stop processing at "FPTS"
        if stat_name == "FPTS":
            break

    # Process each player in the table
    player_rows = soup.select('tbody tr[class^="mpb-player-"]')
    for player_row in player_rows:
        # Get player's name
        player_name = normalize_name_to_sleeper(player_row.select_one('.player-name').text.strip())

        # Initialize a temporary dictionary to hold the player's stats
        temp_stat_dict = {}
        stat_elements = player_row.select('td.center')

        # Iterate over each <td> element, adding its text to the temp_stat_dict
        for index, stat_element in enumerate(stat_elements):
            stat_name = columnToStatNameDict.get(index + 2)  # Use the dictionary for stat names
            if stat_name:
                stat_value = stat_element.text.strip()
                temp_stat_dict[stat_name] = stat_value

        # Add the player's stats to the main projections dictionary
        fantasy_pros_projections[player_name] = temp_stat_dict

    #get qb stats
    columnToStatNameDict = {}
    response = _http_get(qb_stats_url)
    html_content = response.text

    soup = BeautifulSoup(html_content, 'html.parser')

    # Retrieve the page content and parse it with BeautifulSoup
    # Scrape column headers for the table
    headers = soup.select('thead tr:nth-of-type(2) th') 
    rushing_flag = False

    for idx, header in enumerate(headers):
        data_column = idx
        stat_name = header.find('small').text if header.find('small') else ""
        
        if stat_name == "YDS" or stat_name == "TDS":
            if rushing_flag:
                stat_name = "RUSH_" + stat_name
            else:
                stat_name = "PASS_" + stat_name

        columnToStatNameDict[int(data_column)-1] = stat_name
        if stat_name == "INTS":
            rushing_flag = True
        if stat_name == "FPTS":
            break

    # Process each player in the table
    player_rows = soup.select('tbody tr[class^="mpb-player-"]')
    for player_row in player_rows:
        # Get player's name
        player_name = normalize_name_to_sleeper(player_row.select_one('.player-name').text.strip())

        # Initialize a temporary dictionary to hold the player's stats
        temp_stat_dict = {}
        stat_elements = player_row.select('td.center')

        # Iterate over each <td> element, adding its text to the temp_stat_dict
        for index, stat_element in enumerate(stat_elements):
            stat_name = columnToStatNameDict.get(index)  # Use the dictionary for stat names
            if stat_name:
                stat_value = stat_element.text.strip()
                temp_stat_dict[stat_name] = stat_value

        # Add the player's stats to the main projections dictionary
        fantasy_pros_projections[player_name] = temp_stat_dict

    #Transform the fantasypros data into the way we expect the data from sportsbook since it is our backup
    backup_fantasypros_data = {}
    for player, stat_projections in fantasy_pros_projections.items():
        lowercase_name = ''.join(char for char in player if char.isalnum()).lower()
        temp_dict = {}
        for stat_name, projection in stat_projections.items():
            if stat_name in Config.fantasy_pros_to_stat_name_map:
                temp_dict[Config.fantasy_pros_to_stat_name_map[stat_name]] = float(projection)
            elif stat_name == "RUSH_TDS" or stat_name == "REC_TDS":
                temp_dict["Anytime Touchdown"] = temp_dict["Anytime Touchdown"] + float(projection) if "Anytime Touchdown" in temp_dict else float(projection)

        backup_fantasypros_data[lowercase_name] = temp_dict

    upload_to_azure_blob(backup_fantasypros_data, "backup_fantasypros_projections.json")


    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        # Get overall ranking data etc
        page.goto(url)

        # Scroll the page down multiple times to fully load all players
        for _ in range(5):
            page.keyboard.press('End')
            time.sleep(2)

        # Once all content is loaded, grab the page source
        html = page.content()
        browser.close()

    # Parse the HTML with BeautifulSoup
    soup = BeautifulSoup(html, 'html.parser')
    player_rows = soup.find_all('tr', class_='player-row')
    player_info_list = {}

    for row in player_rows:
        rank_cell = row.find('td', class_='sticky-cell sticky-cell-one')
        if rank_cell:
            try:
                overall_rank = int(rank_cell.text.strip())
            except ValueError:
                continue
            if 1 <= overall_rank <= 325:
                player_cell = row.find('div', class_='player-cell player-cell__td')
                if player_cell:
                    player_name_tag = player_cell.find('a', class_='player-cell-name')
                    if player_name_tag:
                        abbreviated_name = player_name_tag.text.strip()
                        full_name = player_name_tag['fp-player-name']
                        if "Sr." in full_name or "Jr." in full_name or "III" in full_name or "II" in full_name:
                            full_name = " ".join(full_name.split()[:2])
                        full_name = normalize_name_to_sleeper(full_name)
                    team_tag = player_cell.find('span', class_='player-cell-team')
                    team_name = team_tag.text.strip('()') if team_tag else None
                    star_rating = None
                    star_cell = row.find('td', class_='matchup-star-cell')
                    if star_cell:
                        star_tag = star_cell.find('div', class_='template-stars-star')
                        if star_tag:
                            star_span = star_tag.find('span', class_='sr-only')
                            if star_span:
                                try:
                                    star_rating = int(star_span.text.split()[0])
                                except ValueError:
                                    star_rating = None

                player_info = {
                    'overall_rank': overall_rank,
                    'abbreviated_name': abbreviated_name,
                    'Team Name': team_name,
                    'Opponent Rating': star_rating
                }

                player_info_list[full_name] = player_info
    

    logging.info("Finished getting fantasypros data!")

    upload_to_azure_blob(player_info_list, "fantasypros_data.json")

    return player_info_list

def getProjectionsFromAllVegas():
    link = "https://vegasranks.pythonanywhere.com/getVegasRanks?prop=all&format=ppr"
    resp = _http_get(link, timeout=30)
    data = resp.json()

    sportsbook_proj = {}

    for projection in data:
        player_proj = {}
        player_name = ''.join(char for char in projection["player"] if char.isalnum()).lower()
        for stat in projection:
            if stat not in Config.ppr_stat_scoring:
                continue
            scoring_multiplier, full_stat_name = Config.ppr_stat_scoring[stat]
            player_proj[full_stat_name] = round(projection[stat]/scoring_multiplier, 3)
        sportsbook_proj[player_name] = player_proj

    upload_to_azure_blob(sportsbook_proj, "sportsbook_proj.json")

    return sportsbook_proj

def getDraftkingsProjections():
    # Snapshot the existing projections as "_prev" before overwriting so the
    # backend Risers/Fallers endpoint has a previous-run baseline to diff
    # against. We only do this if the existing blob looks healthy (i.e. is a
    # non-empty dict) — otherwise an off-season run with empty projections
    # would clobber a useful previous snapshot.
    try:
        existing = try_download_blob_json("hand_calculated_projections.json")
        if isinstance(existing, dict) and len(existing) > 0:
            upload_to_azure_blob(existing, "hand_calculated_projections_prev.json")
    except Exception as e:
        logging.info("Failed to snapshot prev projections (continuing): %s", e)

    player_projections = form_player_projections_dict()
    upload_to_azure_blob(player_projections, "hand_calculated_projections.json")

def form_standard_player_rankings():
    # Snapshot the existing standard rankings as "_prev" before overwriting
    # so the Risers/Fallers endpoint has a previous-run baseline. Same
    # safety guard as getDraftkingsProjections — don't clobber a healthy
    # snapshot with an empty off-season scrape.
    try:
        existing = try_download_blob_json("standard_player_rankings.json")
        if isinstance(existing, dict) and any(existing.values()):
            upload_to_azure_blob(existing, "standard_player_rankings_prev.json")
    except Exception as e:
        logging.info("Failed to snapshot prev standard rankings (continuing): %s", e)

    standard_league_projections = form_all_projections_and_points_dict()
    upload_to_azure_blob(standard_league_projections, "standard_player_rankings.json")
    
def download_necessary_fantasy_data(force: bool = False):

    now = datetime.now()
    if not force and not is_in_fantasy_season(now):
        logging.info("Not in football season. Skipping data download.")
        return

    # Idempotency guard: skip if a successful run completed within the dedup window.
    if not force:
        existing = try_download_blob_json("runinfo.json")
        if existing and existing.get("Successful"):
            last_iso = existing.get("RuntimeUtc")
            if last_iso:
                try:
                    last_run = datetime.fromisoformat(last_iso)
                    if last_run.tzinfo is None:
                        last_run = last_run.replace(tzinfo=timezone.utc)
                    delta = datetime.now(timezone.utc) - last_run
                    if delta < timedelta(minutes=SUCCESSFUL_RUN_DEDUP_MINUTES):
                        logging.info(
                            f"Skipping refresh: last successful run was {delta.total_seconds():.0f}s ago "
                            f"(< {SUCCESSFUL_RUN_DEDUP_MINUTES}m dedup window)."
                        )
                        return
                except Exception as e:
                    logging.info(f"Could not parse RuntimeUtc, proceeding with refresh: {e}")

    success = False
    try:
        print("Getting draftkings projections")
        getDraftkingsProjections()

        get_boris_chen_tiers()
        try:
            get_fantasypros_top_players()
        except Exception as e:
            logging.info("Couldn't get updated fantasypros players, matchup data might be slightly out of date")
            logging.info("Exception is " + str(e))
        getProjectionsFromAllVegas()
        form_standard_player_rankings()

        logging.info("Web scraping completed!")
        success = True
    except Exception as e:
        logging.error("Ran into error while testing, exception is " + str(e))
    finally:
        run_info = {
            "Successful": success,
            "Runtime": format_eastern_runtime(),
            "RuntimeUtc": datetime.now(timezone.utc).isoformat(),
        }
        upload_to_azure_blob(run_info, "runinfo.json")

@app.function_name(name="test_http_trigger")
@app.route(route="hello", auth_level=func.AuthLevel.FUNCTION)
def test_http_trigger(req: func.HttpRequest) -> func.HttpResponse:
    logging.info('Python HTTP trigger function processed a request.')
    force = (req.params.get("force") or "").lower() in ("1", "true", "yes")
    skip_sleeper = (req.params.get("skip_sleeper") or "").lower() in ("1", "true", "yes")

    try:
        download_necessary_fantasy_data(force=force)
        if not skip_sleeper:
            get_sleeper_owned_for_week()
    except Exception as e:
        logging.exception("test_http_trigger failed")
        return func.HttpResponse(
            json.dumps({"status": "error", "message": str(e)}),
            status_code=500,
            mimetype="application/json",
        )

    return func.HttpResponse(
        json.dumps({"status": "ok", "force": force, "skip_sleeper": skip_sleeper}),
        status_code=200,
        mimetype="application/json",
    )

#Non-game day schedule
@app.function_name(name="non_game_day_schedule")
@app.timer_trigger(schedule="0 0 13-23/3 * * Tue,Wed,Fri,Sat", arg_name="mytimer")
def non_game_day_schedule(mytimer: func.TimerRequest) -> None:
    logging.info('Executing non-game day schedule...')
    download_necessary_fantasy_data()

# Monday and Thursday schedule
@app.function_name(name="monday_thursday_hourly_schedule")
@app.timer_trigger(schedule="0 0 16,18,20,22 * * Mon,Thu", arg_name="mytimer")
def monday_thursday_schedule(mytimer: func.TimerRequest) -> None:
    logging.info('Executing Monday and Thursday schedule every other hour...')
    download_necessary_fantasy_data()

@app.function_name(name="monday_thursday_final_pregame_schedule")
@app.timer_trigger(schedule="0 0 0 * * Tue,Fri", arg_name="mytimer")
def monday_thursday_schedule_final_pregame(mytimer: func.TimerRequest) -> None:
    logging.info('Executing Monday and Thursday final pregame schedule...')
    download_necessary_fantasy_data()

@app.function_name(name="monday_thursday_six_to_seven_schedule")
@app.timer_trigger(schedule="0 30 22 * * Mon,Thu", arg_name="mytimer")
def monday_thursday_six_to_seven_schedule(mytimer: func.TimerRequest) -> None:
    logging.info('Executing Monday and Thursday schedule from 6 to 7...')
    download_necessary_fantasy_data()

@app.function_name(name="monday_thursday_schedule_pregame")
@app.timer_trigger(schedule="0 15,30,45 23 * * Mon,Thu", arg_name="mytimer")
def monday_thursday_schedule_pregame(mytimer: func.TimerRequest) -> None:
    logging.info('Executing Monday and Thursday pregame schedule...')
    download_necessary_fantasy_data()

# Sunday schedule
@app.function_name(name="sunday_schedule_hourly")
@app.timer_trigger(schedule="0 0 11-15,17-18,20 * * Sun", arg_name="mytimer")
def sunday_schedule_hourly(mytimer: func.TimerRequest) -> None:
    logging.info('Executing Sunday hourly schedule...')
    download_necessary_fantasy_data()

@app.function_name(name="sunday_schedule_eleven")
@app.timer_trigger(schedule="0 30 15 * * Sun", arg_name="mytimer")
def sunday_schedule_eleven(mytimer: func.TimerRequest) -> None:
    logging.info('Executing Sunday schedule 11:30...')
    download_necessary_fantasy_data()

@app.function_name(name="sunday_schedule_all_pregame")
@app.timer_trigger(schedule="0 0/15 16,19,23 * * Sun", arg_name="mytimer")
def sunday_schedule_all_pregame(mytimer: func.TimerRequest) -> None:
    logging.info('Executing Sunday schedule leading up to 1/4/8 oclock games...')
    download_necessary_fantasy_data()

@app.function_name(name="sunday_schedule_evening")
@app.timer_trigger(schedule="0 0 21-22 * * Sun", arg_name="mytimer")
def sunday_schedule_evening(mytimer: func.TimerRequest) -> None:
    logging.info('Executing Sunday schedule evening...')
    download_necessary_fantasy_data()

@app.function_name(name="sunday_schedule_snf_pregame")
@app.timer_trigger(schedule="0 05 0 * * Mon", arg_name="mytimer")
def sunday_schedule_snf_pregame(mytimer: func.TimerRequest) -> None:
    logging.info('Executing Sunday schedule pregame SNF...')
    download_necessary_fantasy_data()

@app.function_name(name="weekly_sleeper_update")
@app.timer_trigger(schedule="0 0 5 * * Sun", arg_name="mytimer")
def sleeper_player_update(mytimer: func.TimerRequest) -> None:
    logging.info('Executing sleeper player update')
    get_sleeper_player_data()
    get_sleeper_owned_for_week()


