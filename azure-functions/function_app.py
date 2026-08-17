import azure.functions as func
import hashlib
import os
import json
import re
import unicodedata
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
import draft_adp
import draft_values
import draftsheets_values
import fantasypros_adp
import vegas_accuracy
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
    from trade_eval.legacy_season_scoring import (
        build_player_scoring_data,
        attach_six_pt_passing_td_rank,
    )

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

    players_dict = data
    year = get_current_fantasy_year()

    # Pull per-position season + weekly scoring from Sleeper. Pure builder
    # lives in trade_eval/legacy_season_scoring.py so the historical
    # backfill tool can reuse it.
    player_scoring = build_player_scoring_data(year, http_get_json=load_json_from_url)
    attach_six_pt_passing_td_rank(player_scoring, players_dict)

    # Merge scoring data back onto the full players_dict (drives players.json).
    for pid, scoring in player_scoring.items():
        if pid not in players_dict:
            continue
        players_dict[pid]["scoring_data_weekly"] = scoring["scoring_data_weekly"]
        players_dict[pid]["scoring_data_season"] = scoring["scoring_data_season"]

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


def capture_vegas_history():
    """Fold this run's Vegas projections + FantasyPros ECR into a per-week,
    per-player *locked* history blob (``projection_history_{year}.json``).

    This is the accountability record: each scrape we refresh every player's
    current-week slot with their latest projected fantasy points, but a player
    who has already played and dropped out of later scrapes keeps their earlier
    line. That freezing is what lets a Thursday-night starter's projection
    survive the Sunday/Monday scrapes instead of being wiped out by a naive
    end-of-week snapshot. See ``vegas_accuracy.merge_week_capture``.
    """
    year = get_current_fantasy_year()
    week = get_current_nfl_week()

    rankings = try_download_blob_json("standard_player_rankings.json") or {}
    ranking_rows = rankings.get(vegas_accuracy.HALF_PPR_VARIANT) or []
    if not ranking_rows:
        logging.info("capture_vegas_history: no half-ppr rankings this run; skipping.")
        return

    fantasypros_data = try_download_blob_json("fantasypros_data.json") or {}
    players = try_download_blob_json("players.json") or {}
    fp_rank_by_pid = vegas_accuracy.fp_overall_rank_by_pid(fantasypros_data, players)

    history = try_download_blob_json(f"projection_history_{year}.json") or {}
    vegas_accuracy.merge_week_capture(
        history,
        week,
        ranking_rows,
        fp_rank_by_pid,
        captured_at=datetime.now(timezone.utc).isoformat(),
    )
    upload_to_azure_blob(history, f"projection_history_{year}.json")
    logging.info(
        "capture_vegas_history: locked %d players for %s week %s",
        len(history.get(str(week), {})), year, week,
    )


def build_projection_review(year: int | None = None):
    """Compile the Vegas-projection accuracy review from accumulated history +
    realized Sleeper actuals, and write ``projection_review_{year}.json``.

    Meant to run Tuesday once the prior week's results are final: it grades
    projected fantasy points vs reality, contrasts Vegas positional ranks
    against FantasyPros ECR, and rolls everything up season-to-date so a single
    ugly week doesn't hide a season-long edge.
    """
    year = year or get_current_fantasy_year()
    history = try_download_blob_json(f"projection_history_{year}.json") or {}
    if not history:
        logging.info("build_projection_review: no projection history for %s.", year)
        return None

    actuals = try_download_blob_json(f"player_season_scoring_{year}.json") or {}
    review = vegas_accuracy.compile_review(history, actuals)
    review["year"] = year
    review["generated_utc"] = datetime.now(timezone.utc).isoformat()
    upload_to_azure_blob(review, f"projection_review_{year}.json")
    logging.info(
        "build_projection_review: graded weeks %s for %s.",
        review.get("weeks"), year,
    )
    return review


def refresh_draft_adp(year: int | None = None):
    """Build and safely publish format-specific FantasyPros DraftWizard ADP.

    DraftWizard supplies current mock-draft ADP plus observed standard
    deviation for league size/scoring/1QB-or-2QB. A bad/partial upstream pull
    never overwrites the last healthy blob.
    """
    year = year or get_current_fantasy_year()
    players = try_download_blob_json("players.json") or {}
    if not players:
        logging.info("refresh_draft_adp: players.json is unavailable.")
        return None

    def fetch_text(url: str):
        return _http_get(url, timeout=30).text

    candidate = fantasypros_adp.build_fantasypros_adp_blob(
        str(year), players, fetch_text=fetch_text,
    )
    errors = draft_adp.validate_adp_blob(candidate)
    if errors:
        logging.error(
            "refresh_draft_adp: rejected %s update: %s",
            year, "; ".join(errors[:10]),
        )
        return None

    blob_name = f"draft_adp_{year}.json"
    existing = try_download_blob_json(blob_name)
    if isinstance(existing, dict) and existing.get("configs"):
        upload_to_azure_blob(existing, f"draft_adp_{year}_prev.json")
    upload_to_azure_blob(candidate, blob_name)
    logging.info(
        "refresh_draft_adp: published %s (%d configs).",
        blob_name, len(candidate.get("configs") or {}),
    )
    return candidate


def _publish_draftsheets_candidate(candidate, year):
    """Validate and merge one exact-profile candidate into provider storage."""
    profile = candidate["profile"]
    profile_id = profile["id"]
    config_keys = list(candidate.get("configs") or {})
    errors = draft_values.validate_rankings_blob(
        candidate, expected_year=year, required_keys=config_keys,
        min_players_per_config=100,
    )
    if errors:
        logging.error(
            "refresh_draftsheets_values: rejected %s update: %s",
            year, "; ".join(errors[:10]),
        )
        return None

    blob_name = draft_values.provider_profile_rankings_blob_name(
        year, draftsheets_values.PROVIDER_ID, profile_id,
    )
    profile_registry_name = (
        f"draft_value_profiles_{year}_{draftsheets_values.PROVIDER_ID}.json"
    )
    existing_profile_registry = try_download_blob_json(profile_registry_name) or {}
    profiles = dict(existing_profile_registry.get("profiles") or {})
    profiles[profile_id] = {
        "id": profile_id,
        "blob_name": blob_name,
        "profile": profile,
        "config_count": len(config_keys),
        "supported_config_keys": config_keys,
        "generated_at_utc": candidate["generated_at_utc"],
    }
    default_profile_id = existing_profile_registry.get("default_profile_id")
    if default_profile_id not in profiles:
        default_profile_id = profile_id
    profile_registry = {
        "schema_version": 1,
        "year": str(year),
        "provider": draftsheets_values.PROVIDER_ID,
        "default_profile_id": default_profile_id,
        "generated_at_utc": candidate["generated_at_utc"],
        "profiles": profiles,
    }
    errors = draft_values.validate_profile_registry(
        profile_registry, expected_year=year,
    )
    if errors:
        logging.error(
            "refresh_draftsheets_values: rejected profile registry: %s",
            "; ".join(errors),
        )
        return None

    provider_registry_name = draft_values.value_providers_registry_blob_name(year)
    existing_provider_registry = try_download_blob_json(provider_registry_name) or {}
    providers = dict(existing_provider_registry.get("providers") or {})
    providers[draftsheets_values.PROVIDER_ID] = {
        "id": draftsheets_values.PROVIDER_ID,
        "name": draftsheets_values.PROVIDER_NAME,
        "attribution": draftsheets_values.PROVIDER_NAME,
        "source_url": draftsheets_values.SOURCE_URL,
        "source_version": candidate.get("source_version"),
        "generated_at_utc": candidate["generated_at_utc"],
        "profile_registry_blob_name": profile_registry_name,
        "profile_count": len(profiles),
    }
    default_provider = existing_provider_registry.get("default_provider_id")
    if default_provider not in providers:
        default_provider = draftsheets_values.PROVIDER_ID
    provider_registry = {
        "schema_version": 1,
        "year": str(year),
        "default_provider_id": default_provider,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "providers": providers,
    }
    errors = draft_values.validate_provider_registry(
        provider_registry, expected_year=year,
    )
    if errors:
        logging.error(
            "refresh_draftsheets_values: rejected provider registry: %s",
            "; ".join(errors),
        )
        return None

    # Dependency order is part of the safety contract. Readers cannot discover
    # the provider until its exact blob and profile registry are both healthy.
    draft_values.publish_json_with_snapshot(
        candidate, blob_name,
        upload=upload_to_azure_blob, load=try_download_blob_json,
    )
    draft_values.publish_json_with_snapshot(
        profile_registry, profile_registry_name,
        upload=upload_to_azure_blob, load=try_download_blob_json,
    )
    draft_values.publish_json_with_snapshot(
        provider_registry, provider_registry_name,
        upload=upload_to_azure_blob, load=try_download_blob_json,
    )
    logging.info(
        "refresh_draftsheets_values: published %s / %s (%d players).",
        profile_id, config_keys[0],
        len(next(iter(candidate["configs"].values()))["players"]),
    )
    return candidate


def refresh_draftsheets_values(year: int | None = None):
    """Publish the provider's current public exact-profile finished values.

    The public sheet is read-only. This refresh consumes only its exposed
    Scoring and DraftSheet CSV results; it does not reproduce or change the
    provider's formulas.
    """
    year = year or get_current_fantasy_year()
    players = try_download_blob_json("players.json") or {}
    if not players:
        logging.info("refresh_draftsheets_values: players.json is unavailable.")
        return None

    scoring_csv = _http_get(draftsheets_values.SCORING_CSV_URL, timeout=60).text
    draftsheet_csv = _http_get(draftsheets_values.DRAFTSHEET_CSV_URL, timeout=60).text
    candidate = draftsheets_values.build_draftsheets_blob(
        year, players, scoring_csv, draftsheet_csv,
        resolver_factory=draftsheets_values.NameResolver,
    )
    return _publish_draftsheets_candidate(candidate, year)


def check_elboberto_value_update(year: int | None = None):
    """Detect a newly published ElBoberto workbook and expose refresh status."""
    year = year or get_current_fantasy_year()
    post = _http_get(draft_values.ELBOBERTO_POST_URL, timeout=30)
    workbook_url = draft_values.discover_elboberto_workbook_url(post.text)
    workbook = _http_get(workbook_url, timeout=120)
    content = workbook.content
    if len(content) < 100_000 or not content.startswith(b"PK"):
        raise ValueError("ElBoberto download is not a plausible workbook")
    latest_hash = hashlib.sha256(content).hexdigest()
    current = try_download_blob_json(f"draft_rankings_{year}.json") or {}
    current_hash = current.get("source_content_sha256")
    status = {
        "schema_version": 1,
        "year": str(year),
        "provider": draft_values.ELBOBERTO_PROVIDER,
        "checked_at_utc": datetime.now(timezone.utc).isoformat(),
        "latest_source_url": workbook_url,
        "latest_source_version": draft_values.elboberto_version_from_url(workbook_url),
        "latest_source_content_sha256": latest_hash,
        "latest_source_content_bytes": len(content),
        "current_source_version": current.get("source_version"),
        "current_source_content_sha256": current_hash,
        "update_available": bool(current_hash and current_hash != latest_hash),
        "refresh_mode": "desktop_excel_required",
    }
    upload_to_azure_blob(
        status,
        draft_values.value_provider_status_blob_name(
            year, draft_values.ELBOBERTO_PROVIDER,
        ),
    )
    if status["update_available"]:
        logging.warning(
            "ElBoberto %s is newer than the published profile grid; desktop Excel refresh required.",
            status.get("latest_source_version") or "workbook",
        )
    return status


def check_draftsheets_value_update(year: int | None = None):
    """Detect a changed public DraftSheets workbook without replacing profiles."""
    year = year or get_current_fantasy_year()
    workbook = _http_get(draftsheets_values.XLSX_EXPORT_URL, timeout=120)
    content = workbook.content
    if len(content) < 100_000 or not content.startswith(b"PK"):
        raise ValueError("DraftSheets download is not a plausible workbook")
    latest_hash = hashlib.sha256(content).hexdigest()
    profile_registry = try_download_blob_json(
        f"draft_value_profiles_{year}_{draftsheets_values.PROVIDER_ID}.json"
    ) or {}
    default_id = profile_registry.get("default_profile_id")
    default_entry = (profile_registry.get("profiles") or {}).get(default_id) or {}
    current = try_download_blob_json(default_entry.get("blob_name") or "") or {}
    current_hash = current.get("source_content_sha256")
    status = {
        "schema_version": 1,
        "year": str(year),
        "provider": draftsheets_values.PROVIDER_ID,
        "checked_at_utc": datetime.now(timezone.utc).isoformat(),
        "latest_source_url": draftsheets_values.XLSX_EXPORT_URL,
        "latest_source_content_sha256": latest_hash,
        "latest_source_content_bytes": len(content),
        "current_source_version": current.get("source_version"),
        "current_source_content_sha256": current_hash,
        "update_available": bool(current_hash and current_hash != latest_hash),
        "refresh_mode": "weekly_local_excel",
    }
    upload_to_azure_blob(
        status,
        draft_values.value_provider_status_blob_name(
            year, draftsheets_values.PROVIDER_ID,
        ),
    )
    if status["update_available"]:
        logging.warning(
            "DraftSheets changed; run the guarded weekly local Excel refresh."
        )
    return status


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

        # Lock this run's projections into the per-week accountability history
        # (freezes each player's last pre-game line). Non-fatal on failure.
        try:
            capture_vegas_history()
        except Exception as e:
            logging.info("capture_vegas_history failed (continuing): %s", e)

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


@app.function_name(name="weekly_projection_accuracy_review")
@app.timer_trigger(schedule="0 0 12 * * Tue", arg_name="mytimer")
def weekly_projection_accuracy_review(mytimer: func.TimerRequest) -> None:
    """Tuesday: last week's results are final, so refresh Sleeper actuals and
    recompile the Vegas-projection accuracy review."""
    logging.info('Executing weekly projection accuracy review')
    if not is_in_fantasy_season():
        logging.info("Not in football season. Skipping accuracy review.")
        return
    try:
        get_sleeper_player_data()
    except Exception as e:
        logging.info("Could not refresh sleeper actuals before review: %s", e)
    build_projection_review()


@app.function_name(name="daily_draft_adp_refresh")
@app.timer_trigger(schedule="0 0 10 * * *", arg_name="mytimer")
def daily_draft_adp_refresh(mytimer: func.TimerRequest) -> None:
    """Daily in July-September: refresh ADP without touching value sheets."""
    if not draft_adp.is_draft_season(datetime.now(timezone.utc)):
        logging.info("Outside draft season. Skipping ADP refresh.")
        return
    refresh_draft_adp()


@app.function_name(name="daily_elboberto_update_check")
@app.timer_trigger(schedule="0 0 11 * * *", arg_name="mytimer")
def daily_elboberto_update_check(mytimer: func.TimerRequest) -> None:
    """Detect provider workbook changes; regeneration still requires Excel."""
    if not draft_adp.is_draft_season(datetime.now(timezone.utc)):
        logging.info("Outside draft season. Skipping ElBoberto update check.")
        return
    check_elboberto_value_update()


@app.function_name(name="daily_draftsheets_update_check")
@app.timer_trigger(schedule="0 30 11 * * *", arg_name="mytimer")
def daily_draftsheets_update_check(mytimer: func.TimerRequest) -> None:
    """Detect public workbook changes; regeneration runs weekly on the laptop."""
    if not draft_adp.is_draft_season(datetime.now(timezone.utc)):
        logging.info("Outside draft season. Skipping DraftSheets update check.")
        return
    check_draftsheets_value_update()


# ---------------------------------------------------------------------------
# Trade-evaluator data pipeline
# ---------------------------------------------------------------------------
# Runs *separately* from the in-season DraftKings/Vegas/FantasyPros refresh
# so it can operate year-round (dynasty values move all summer; PPG updates
# only during the season). All blobs live under ``trade_eval/`` (see
# ``trade_eval/blob_layout.py``). Pure parsing/aggregation lives in the
# ``trade_eval`` package; this section is just IO + scheduling glue.
from trade_eval import (  # noqa: E402  (intentionally below `app` definition)
    sleeper_scoring as _trade_eval_sleeper_scoring,
    fantasycalc_values as _trade_eval_fc,
    ktc_scraper as _trade_eval_ktc,
    ktc_top500_daily as _trade_eval_ktc_daily,
)


def _trade_eval_http_get_json(url: str):
    """Adapter so the trade_eval modules can fetch JSON via our retry-aware
    ``_http_get`` without importing requests directly."""
    return _http_get(url, timeout=30).json()


def _trade_eval_blob_upload(data, blob_name: str) -> None:
    upload_to_azure_blob(data, blob_name, filename=blob_name)


def _trade_eval_blob_load(blob_name: str):
    return try_download_blob_json(blob_name)


def _trade_eval_fetch_page(url: str) -> str:
    """Render a JS-heavy page with Playwright and return the HTML.

    KTC's playersArray is actually inline in the static HTML, but using
    Playwright matches the existing pattern (boris_chen, fantasypros) and
    keeps us resilient if KTC ever moves to client-side rendering.
    """
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            # Small scroll to trigger any lazy chunks; cheap insurance.
            for _ in range(3):
                page.keyboard.press("End")
                time.sleep(0.5)
            return page.content()
        finally:
            browser.close()


# Cached name->sleeper_id resolver for the KTC daily appender. Built lazily
# from ``players.json`` so we only pay the load cost when a new KTC entrant
# actually shows up. Resets per warm-instance, which is fine -- players.json
# refreshes weekly anyway.
_KTC_NAME_RESOLVER_CACHE: dict = {}


def _trade_eval_build_ktc_name_resolver():
    """Return a ``name -> sleeper_id | None`` resolver backed by players.json.

    Uses a normalized-name lookup (strip suffixes / punctuation / case) over
    the four offensive skill positions. Returns the first unambiguous hit.
    """
    cache = _KTC_NAME_RESOLVER_CACHE
    if "index" not in cache:
        try:
            players = try_download_blob_json("players.json") or {}
        except Exception:
            logging.exception("KTC name resolver: players.json load failed")
            players = {}

        suffix_re = re.compile(r"\b(jr|sr|ii|iii|iv|v)\.?$", re.IGNORECASE)
        nonalnum_re = re.compile(r"[^a-z0-9]")

        def normalize(name: str) -> str:
            s = unicodedata.normalize("NFKD", name or "")
            s = s.encode("ascii", "ignore").decode("ascii")
            s = suffix_re.sub("", s).lower()
            return nonalnum_re.sub("", s)

        index: dict = {}
        for sid, meta in players.items():
            full = meta.get("full_name") or ""
            positions = meta.get("fantasy_positions") or []
            if not any(p in {"QB", "RB", "WR", "TE"} for p in positions):
                continue
            norm = normalize(full)
            if not norm:
                continue
            index.setdefault(norm, []).append(sid)
        cache["index"] = index
        cache["normalize"] = normalize

    index = cache["index"]
    normalize = cache["normalize"]

    def resolver(name: str):
        hits = index.get(normalize(name)) or []
        return hits[0] if len(hits) == 1 else None

    return resolver


def trade_eval_run_weekly() -> dict:
    """Weekly trade-evaluator refresh.

    * Snapshots FantasyCalc and KTC for every format (year-round).
    * If we're in fantasy season, also merges the current week's Sleeper
      stats into the season-summary blob.

    Returns a small status dict for logging / HTTP responses.
    """
    status: dict = {}
    try:
        status["fantasycalc"] = _trade_eval_fc.snapshot_all(
            http_get_json=_trade_eval_http_get_json,
            blob_upload=_trade_eval_blob_upload,
            blob_load=_trade_eval_blob_load,
        )
    except Exception as e:
        logging.exception("trade_eval: fantasycalc snapshot failed")
        status["fantasycalc_error"] = str(e)

    try:
        status["ktc"] = _trade_eval_ktc.snapshot_all(
            fetch_page=_trade_eval_fetch_page,
            blob_upload=_trade_eval_blob_upload,
            blob_load=_trade_eval_blob_load,
        )
    except Exception as e:
        logging.exception("trade_eval: ktc snapshot failed")
        status["ktc_error"] = str(e)

    if is_in_fantasy_season():
        try:
            season = get_current_fantasy_year()
            week = get_current_nfl_week()
            updated = _trade_eval_sleeper_scoring.update_current_week(
                season, week,
                http_get_json=_trade_eval_http_get_json,
                blob_load=_trade_eval_blob_load,
                blob_upload=_trade_eval_blob_upload,
            )
            status["sleeper_scoring"] = {
                "season": season, "week": week, "updated": updated,
            }
        except Exception as e:
            logging.exception("trade_eval: sleeper scoring update failed")
            status["sleeper_scoring_error"] = str(e)
    else:
        status["sleeper_scoring"] = "skipped (offseason)"

    status["finished_at"] = format_eastern_runtime()
    return status


def trade_eval_run_daily() -> dict:
    """Daily refresh -- FantasyCalc snapshot + KTC top-500 append."""
    status = {}
    try:
        status["fantasycalc"] = _trade_eval_fc.snapshot_all(
            http_get_json=_trade_eval_http_get_json,
            blob_upload=_trade_eval_blob_upload,
            blob_load=_trade_eval_blob_load,
        )
    except Exception as e:
        logging.exception("trade_eval: fantasycalc daily snapshot failed")
        status["fantasycalc_error"] = str(e)

    try:
        status["ktc_daily"] = _trade_eval_ktc_daily.append_daily(
            fetch_page=_trade_eval_fetch_page,
            blob_upload=_trade_eval_blob_upload,
            blob_load=_trade_eval_blob_load,
            name_resolver=_trade_eval_build_ktc_name_resolver(),
        )
    except Exception as e:
        logging.exception("trade_eval: ktc daily append failed")
        status["ktc_daily_error"] = str(e)

    status["finished_at"] = format_eastern_runtime()
    return status


# ---- HTTP triggers (manual / one-shot) -----------------------------------
@app.function_name(name="trade_eval_run")
@app.route(route="trade_eval/run", auth_level=func.AuthLevel.FUNCTION)
def trade_eval_run_http(req: func.HttpRequest) -> func.HttpResponse:
    """Manually kick the full weekly trade-evaluator refresh."""
    try:
        status = trade_eval_run_weekly()
    except Exception as e:
        logging.exception("trade_eval_run failed")
        return func.HttpResponse(
            json.dumps({"status": "error", "message": str(e)}),
            status_code=500, mimetype="application/json",
        )
    return func.HttpResponse(
        json.dumps({"status": "ok", "result": status}, default=str),
        status_code=200, mimetype="application/json",
    )


@app.function_name(name="trade_eval_bootstrap_scoring")
@app.route(route="trade_eval/bootstrap_scoring", auth_level=func.AuthLevel.FUNCTION)
def trade_eval_bootstrap_scoring_http(req: func.HttpRequest) -> func.HttpResponse:
    """One-shot historical Sleeper scoring backfill.

    Query params:
      ``start`` (default 2020), ``end`` (default current fantasy year).
    """
    try:
        start = int(req.params.get("start") or 2020)
        end = int(req.params.get("end") or get_current_fantasy_year())
        seasons = list(range(start, end + 1))
        loaded = _trade_eval_sleeper_scoring.bootstrap_history(
            seasons,
            http_get_json=_trade_eval_http_get_json,
            blob_upload=_trade_eval_blob_upload,
            blob_load=_trade_eval_blob_load,
        )
    except Exception as e:
        logging.exception("trade_eval_bootstrap_scoring failed")
        return func.HttpResponse(
            json.dumps({"status": "error", "message": str(e)}),
            status_code=500, mimetype="application/json",
        )
    return func.HttpResponse(
        json.dumps({"status": "ok", "weeks_loaded_per_season": loaded}),
        status_code=200, mimetype="application/json",
    )


@app.function_name(name="trade_eval_snapshot_fantasycalc")
@app.route(route="trade_eval/snapshot_fantasycalc", auth_level=func.AuthLevel.FUNCTION)
def trade_eval_snapshot_fantasycalc_http(req: func.HttpRequest) -> func.HttpResponse:
    try:
        counts = _trade_eval_fc.snapshot_all(
            http_get_json=_trade_eval_http_get_json,
            blob_upload=_trade_eval_blob_upload,
            blob_load=_trade_eval_blob_load,
        )
    except Exception as e:
        logging.exception("trade_eval_snapshot_fantasycalc failed")
        return func.HttpResponse(
            json.dumps({"status": "error", "message": str(e)}),
            status_code=500, mimetype="application/json",
        )
    return func.HttpResponse(
        json.dumps({"status": "ok", "counts": counts}),
        status_code=200, mimetype="application/json",
    )


@app.function_name(name="trade_eval_snapshot_ktc")
@app.route(route="trade_eval/snapshot_ktc", auth_level=func.AuthLevel.FUNCTION)
def trade_eval_snapshot_ktc_http(req: func.HttpRequest) -> func.HttpResponse:
    try:
        counts = _trade_eval_ktc.snapshot_all(
            fetch_page=_trade_eval_fetch_page,
            blob_upload=_trade_eval_blob_upload,
            blob_load=_trade_eval_blob_load,
        )
    except Exception as e:
        logging.exception("trade_eval_snapshot_ktc failed")
        return func.HttpResponse(
            json.dumps({"status": "error", "message": str(e)}),
            status_code=500, mimetype="application/json",
        )
    return func.HttpResponse(
        json.dumps({"status": "ok", "counts": counts}),
        status_code=200, mimetype="application/json",
    )


@app.function_name(name="trade_eval_append_ktc_daily")
@app.route(route="trade_eval/append_ktc_daily", auth_level=func.AuthLevel.FUNCTION)
def trade_eval_append_ktc_daily_http(req: func.HttpRequest) -> func.HttpResponse:
    """Manually trigger today's KTC top-500 append into the rolling
    ``historical_KTC_rankings.json`` blob."""
    try:
        result = _trade_eval_ktc_daily.append_daily(
            fetch_page=_trade_eval_fetch_page,
            blob_upload=_trade_eval_blob_upload,
            blob_load=_trade_eval_blob_load,
            name_resolver=_trade_eval_build_ktc_name_resolver(),
        )
    except Exception as e:
        logging.exception("trade_eval_append_ktc_daily failed")
        return func.HttpResponse(
            json.dumps({"status": "error", "message": str(e)}),
            status_code=500, mimetype="application/json",
        )
    return func.HttpResponse(
        json.dumps({"status": "ok", "result": result}, default=str),
        status_code=200, mimetype="application/json",
    )


# ---- Timer triggers ------------------------------------------------------
# Daily FantasyCalc snapshot at 09:00 UTC (~5am ET) -- year-round.
@app.function_name(name="trade_eval_daily")
@app.timer_trigger(schedule="0 0 9 * * *", arg_name="mytimer")
def trade_eval_daily_timer(mytimer: func.TimerRequest) -> None:
    logging.info("Executing trade_eval daily refresh (FantasyCalc snapshot)...")
    trade_eval_run_daily()


# Weekly full refresh: Tuesday 10:00 UTC (~6am ET).
# - Tue chosen so post-MNF stats are settled when in season.
# - Year-round; the Sleeper scoring leg self-skips out-of-season.
@app.function_name(name="trade_eval_weekly")
@app.timer_trigger(schedule="0 0 10 * * Tue", arg_name="mytimer")
def trade_eval_weekly_timer(mytimer: func.TimerRequest) -> None:
    logging.info("Executing trade_eval weekly refresh (KTC + FC + Sleeper PPG)...")
    trade_eval_run_weekly()




