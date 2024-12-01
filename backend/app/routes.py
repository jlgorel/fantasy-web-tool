from flask import request, Blueprint, jsonify, current_app
from app.services.sleeper_service import cache_sleeper_user_info, load_json_from_azure_storage
import traceback
from app.config import Config
import json

main = Blueprint('main', __name__)
    
@main.route('/load-sleeper-info', methods=['POST'])
def load_sleeper_info():
    try:
        data = request.get_json()
        name = data.get('name')
        user_uuid = request.headers.get('X-User-UUID', 'TESTUSER')
        
        if not name:
            return jsonify({'error': 'Username is required'}), 400
        
        suggested_lineups = cache_sleeper_user_info(name, user_uuid)

        cache_key = f"boris_data_{user_uuid}"

        redis_client = current_app.redis_client

        try:
            redis_client.set(cache_key, json.dumps(suggested_lineups), ex=900)  # Timeout set to 300 seconds
        except Exception as e:
            print("Ran into exception setting cache. Exception is " + str(e))
            tb_str = traceback.format_exc()
            return jsonify({'message': 'Error, ' + tb_str}), 500

        return jsonify({'message': 'Data cached successfully', 'cache_key': cache_key, 'league_names': suggested_lineups}), 200
    except Exception as e:
        print("Exception was " + str(e))
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500

@main.route('/load-cached-starts', methods=['GET'])
def load_cached_starts():
    user_uuid = request.headers.get('X-User-UUID', 'TESTUSER')
    cache_key = f"boris_data_{user_uuid}"

    redis_client = current_app.redis_client

    cached_data = redis_client.get(cache_key)

    if not cached_data:
        return jsonify({'message': 'Nothing has been cached for this user yet. Have you hit the load roster button?',
                        'cache_key': cache_key}), 404
    else:
        cached_start_recommendations = json.loads(cached_data)

    return jsonify({'league_names': list(cached_start_recommendations.keys())}), 200

@main.route('/load-league-data', methods=['GET'])
def load_league_data():
    user_uuid = request.headers.get('X-User-UUID', 'TESTUSER')
    league = request.args.get('league')

    if not league:
        return jsonify({'error': 'League parameter is required'}), 400

    cache_key = f"boris_data_{user_uuid}"
    redis_client = current_app.redis_client

    user_data = redis_client.get(cache_key)

    if not user_data:
        return jsonify({'error': 'No data found for the specified user',
                        'cache_key': cache_key}), 404
    else:
        jsonified_data = json.loads(user_data)

    league_data = jsonified_data.get(league)

    if not league_data:
        return jsonify({'error': 'No data found for the specified league',
                        'cache_key': cache_key,
                        'jsonified_data': jsonified_data}), 404

    return jsonify(league_data), 200

@main.route('/load-last-run-info', methods=['GET'])
def load_last_run_info():
    run_info = load_json_from_azure_storage("runinfo.json", Config.containername, Config.azure_storage_connection_string)
    return jsonify(run_info), 200




