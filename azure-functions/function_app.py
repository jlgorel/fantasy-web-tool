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
from datetime import datetime
from collections import defaultdict
from azure.storage.blob import BlobServiceClient, BlobClient, ContainerClient


app = func.FunctionApp()

def load_config():
    config_path = os.path.join(os.path.dirname(__file__), 'config.json')
    with open(config_path) as config_file:
        config = json.load(config_file)
    return config

def install_browsers_if_needed(localtest = False):
    if not os.path.exists("/home/site/wwwroot/browsers") and not localtest:
        with sync_playwright() as p:
            p.install()

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

def get_sleeper_player_data():
    url = "https://api.sleeper.app/v1/players/nfl"
    resp = requests.get(url=url)
    data = resp.json()

    for pid in dict(data):
        if data[pid]["fantasy_positions"] and len(set(data[pid]["fantasy_positions"]).intersection(set(Config.boris_chen_fantasy_relevant_pos))) == 0:
            del data[pid]
            continue

        for key in dict(data[pid]):
            if key not in Config.relevant_sleeper_keys:
                del data[pid][key]

    upload_to_azure_blob(data, "players.json")

    return True

def retrieve_tiers_from_soup(soup):
    object_tag = soup.find('object', {"type": "text/html"})
    if object_tag:
        data_value = object_tag.get('data')
        if data_value:
            data_response = requests.get(data_value)
            data_response.raise_for_status()
            return data_response.text
    else:
        print("No object tag")

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
    response = requests.get(url)
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
                    for pos in ['QB', 'RB', 'WR', 'TE']:  # Assuming these are relevant positions
                        if pos in str(text).split():
                            links.append((href, text))
                            break
        else:
            return {'message': 'Error - No <div class="widget-content"> found.'}
    else:
        return {'message': 'Error - No <div class="sidebar-section"> found.'}

    logging.info("Got borischen links")

    # Start Playwright session
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
            for num, tier in enumerate(tier_lines):
                tier_dict[num + 1] = [player.strip() for player in tier]
            tiers[name] = tier_dict

        browser.close()

    upload_to_azure_blob(tiers, "borischen_tiers.json")

    return {'message': 'Tiers scraped and saved successfully.', 'tiers': tiers}

# helper functions
def get_fantasypros_top_players():

    logging.info("Starting fantasypros scrape!")

    url = "https://www.fantasypros.com/nfl/rankings/half-point-ppr-superflex.php"
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
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
    resp = requests.get(link)
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
    
def download_necessary_fantasy_data():

    now = datetime.now()
    if not (now.month >= 9 or (now.month == 1 and now.day <= 31)):
        logging.info("Not in football season. Skipping data download.")
        return
    
    install_browsers_if_needed()

    boris_chen_result = get_boris_chen_tiers()
    player_info_list = get_fantasypros_top_players()
    sleeper_result = get_sleeper_player_data()
    sportsbook_player_ranking = getProjectionsFromAllVegas()

    logging.info("Web scraping completed!")

# Non-game day schedule
@app.function_name(name="non_game_day_schedule")
@app.timer_trigger(schedule="0 0 9-23/3 * * Tue,Wed,Fri,Sat", arg_name="mytimer")
def non_game_day_schedule(mytimer: func.TimerRequest) -> None:
    logging.info('Executing non-game day schedule...')
    download_necessary_fantasy_data()

# Monday and Thursday schedule
@app.function_name(name="monday_thursday_hourly_schedule")
@app.timer_trigger(schedule="0 0 12,14,16,18,20 * * Mon,Thu", arg_name="mytimer")
def monday_thursday_schedule(mytimer: func.TimerRequest) -> None:
    logging.info('Executing Monday and Thursday schedule every other hour...')
    download_necessary_fantasy_data()

@app.function_name(name="monday_thursday_six_to_seven_schedule")
@app.timer_trigger(schedule="0 30 18 * * Mon,Thu", arg_name="mytimer")
def monday_thursday_six_to_seven_schedule(mytimer: func.TimerRequest) -> None:
    logging.info('Executing Monday and Thursday schedule from 6 to 7...')
    download_necessary_fantasy_data()

@app.function_name(name="monday_thursday_schedule_pregame")
@app.timer_trigger(schedule="0 15,30,45 19 * * Mon,Thu", arg_name="mytimer")
def monday_thursday_schedule_pregame(mytimer: func.TimerRequest) -> None:
    logging.info('Executing Monday and Thursday pregame schedule...')
    download_necessary_fantasy_data()

# Sunday schedule

@app.function_name(name="sunday_schedule_hourly")
@app.timer_trigger(schedule="0 0 7-11,13-14,16 * * Sun", arg_name="mytimer")
def sunday_schedule_hourly(mytimer: func.TimerRequest) -> None:
    logging.info('Executing Sunday hourly schedule...')
    download_necessary_fantasy_data()

@app.function_name(name="sunday_schedule_eleven")
@app.timer_trigger(schedule="0 30 11 * * Sun", arg_name="mytimer")
def sunday_schedule_eleven(mytimer: func.TimerRequest) -> None:
    logging.info('Executing Sunday schedule 11:30...')
    download_necessary_fantasy_data()


@app.function_name(name="sunday_schedule_all_pregame")
@app.timer_trigger(schedule="0 0/15 12,15,19 * * Sun", arg_name="mytimer")
def sunday_schedule_all_pregame(mytimer: func.TimerRequest) -> None:
    logging.info('Executing Sunday schedule leading up to 1/4/8 oclock games games...')
    download_necessary_fantasy_data()


@app.function_name(name="sunday_schedule_evening")
@app.timer_trigger(schedule="0 0 17-18 * * Sun", arg_name="mytimer")
def sunday_schedule_evening(mytimer: func.TimerRequest) -> None:
    logging.info('Executing Sunday schedule evening...')
    download_necessary_fantasy_data()


@app.function_name(name="sunday_schedule_snf_pregame")
@app.timer_trigger(schedule="0 10 20 * * Sun", arg_name="mytimer")
def sunday_schedule_snf_pregame(mytimer: func.TimerRequest) -> None:
    logging.info('Executing Sunday schedule pregame SNF...')
    download_necessary_fantasy_data()



