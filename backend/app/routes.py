from flask import request, Blueprint, jsonify, current_app
from app.services.sleeper_service import cache_sleeper_user_info, load_json_from_azure_storage, get_overall_rankings
from app.services.waiver_wire import get_waiver_wire
from app.services.risers_fallers import get_risers_fallers
from app.services.wrapped import compute_wrapped
from app.services.wrapped.all_time import build_all_time_payload
from app.services.wrapped.league_context import load_league_context
from app.services.wrapped.transactions import fetch_league_transactions
from app.services.wrapped.trade_accolades import inspect_trade as inspect_trade_payload
from app.services.wrapped.redraft_trade_inspector import inspect_redraft_trade
from app.services.blob_store import load_blob
from app.services.player_detail import get_player_detail
from app.services.sleeper_league_lookup import (
    get_league_season_chain,
    get_user_leagues,
    resolve_league_for_year,
)
import traceback
from app.config import Config
import json

main = Blueprint('main', __name__)
    
@main.route('/load-sleeper-info', methods=['POST'])
def load_sleeper_info():
    try:
        data = request.get_json()
        name = data.get('name')
        try:
            website = data.get('website')
        except:
            website = "Sleeper"
        user_uuid = request.headers.get('X-User-UUID', 'TESTUSER')
        
        if not name:
            return jsonify({'error': 'Username is required'}), 400
        
        suggested_lineups, free_agent_recs = cache_sleeper_user_info(name, user_uuid, website)

        cache_key = f"boris_data_{user_uuid}"

        fa_cache_key = f"free_agents_{user_uuid}"

        redis_client = current_app.redis_client

        try:
            redis_client.set(cache_key, json.dumps(suggested_lineups), ex=900)  # Timeout set to 300 seconds
            redis_client.set(fa_cache_key, json.dumps(free_agent_recs), ex=900)
        except Exception as e:
            print("Ran into exception setting cache. Exception is " + str(e))
            tb_str = traceback.format_exc()
            return jsonify({'message': 'Error, ' + tb_str}), 500

        return jsonify({'message': 'Data cached successfully', 'cache_key': cache_key, 'league_names': suggested_lineups, "free_agents": free_agent_recs}), 200
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

@main.route('/overall-ranks', methods=['GET'])
def overall_rankings():
    overall_ranking_data = get_overall_rankings()
    return jsonify({"overall_rankings": overall_ranking_data}), 200


@main.route('/waiver-wire', methods=['GET'])
def waiver_wire():
    """League-agnostic top low-owned players per position.

    Query params:
      - variant: scoring variant key (default halfppr_4ptpass)
      - max_owned: max ownership pct to include (default 50)
      - top_n: rows per position (default 15)
    """
    variant = request.args.get('variant', 'halfppr_4ptpass')
    try:
        max_owned = float(request.args.get('max_owned', 50))
    except ValueError:
        max_owned = 50.0
    try:
        top_n = int(request.args.get('top_n', 15))
    except ValueError:
        top_n = 15
    payload = get_waiver_wire(variant=variant, max_owned_pct=max_owned, top_n=top_n)
    return jsonify(payload), 200


@main.route('/risers-fallers', methods=['GET'])
def risers_fallers():
    """Top movers since the previous scrape, per scoring variant."""
    variant = request.args.get('variant', 'halfppr_4ptpass')
    try:
        top_n = int(request.args.get('top_n', 10))
    except ValueError:
        top_n = 10
    payload = get_risers_fallers(variant=variant, top_n=top_n)
    return jsonify(payload), 200


@main.route('/load-league-data', methods=['GET'])
def load_league_data():
    user_uuid = request.headers.get('X-User-UUID', 'TESTUSER')
    league = request.args.get('league')

    if not league:
        return jsonify({'error': 'League parameter is required'}), 400

    cache_key = f"boris_data_{user_uuid}"
    fa_cache_key = f"free_agents_{user_uuid}"

    redis_client = current_app.redis_client

    user_data = redis_client.get(cache_key)
    free_agent_data = redis_client.get(fa_cache_key)

    if not user_data:
        return jsonify({'error': 'No data found for the specified user',
                        'cache_key': cache_key}), 404
    else:
        jsonified_data = json.loads(user_data)

    if not user_data:
        return jsonify({'error': 'No free agent data found for the specified user',
                        'cache_key': cache_key}), 404
    else:
        jsonified_fa_data = json.loads(free_agent_data)

    league_data = jsonified_data.get(league)
    free_agent_recs = jsonified_fa_data.get(league)

    if not league_data:
        return jsonify({'error': 'No data found for the specified league',
                        'cache_key': cache_key,
                        'jsonified_data': jsonified_data}), 404

    # league_data is now a dict { boris_optimized, vegas_optimized, your_lineup }.
    # Keep `suggested_starts` (== boris_optimized) as the legacy field so any
    # older client builds that haven't picked up the new keys keep working;
    # new clients consume the explicit *_optimized fields.
    return jsonify({
        "suggested_starts": league_data.get("boris_optimized", []),
        "boris_optimized": league_data.get("boris_optimized", []),
        "vegas_optimized": league_data.get("vegas_optimized", []),
        "your_lineup": league_data.get("your_lineup"),
        "free_agent_recs": free_agent_recs,
    }), 200

@main.route('/load-last-run-info', methods=['GET'])
def load_last_run_info():
    run_info = load_json_from_azure_storage("runinfo.json", Config.containername, Config.azure_storage_connection_string)
    return jsonify(run_info), 200


@main.route('/wrapped/sleeper/<league_id>', methods=['GET'])
def wrapped_sleeper(league_id):
    """Return Fantasy Wrapped payload for a Sleeper league.

    Query params:
        year: 4-digit fantasy year (e.g. "2024") or "all" (not yet implemented).
    """
    try:
        year = request.args.get('year', '2024')
        if year == 'all':
            cache_key = f"wrapped_all_v1_sleeper_{league_id}"
            redis_client = current_app.redis_client
            try:
                cached = redis_client.get(cache_key)
            except Exception:
                cached = None
            if cached:
                try:
                    return jsonify(json.loads(cached)), 200
                except Exception:
                    pass  # fall through to recompute

            payload = build_all_time_payload(league_id)

            try:
                # Shorter TTL than per-year — small payload-of-payloads but
                # we want changes to per-year caches to propagate quickly.
                redis_client.set(cache_key, json.dumps(payload), ex=3600)  # 1h
            except Exception as cache_err:
                print(f"Wrapped all-time cache set failed: {cache_err}")

            return jsonify(payload), 200

        # v4: payload now includes the Phase-4 `streamers` section.
        cache_key = f"wrapped_v4_sleeper_{league_id}_{year}"
        redis_client = current_app.redis_client

        try:
            cached = redis_client.get(cache_key)
        except Exception:
            cached = None
        if cached:
            try:
                return jsonify(json.loads(cached)), 200
            except Exception:
                pass  # fall through to recompute

        payload = compute_wrapped(league_id, year)

        try:
            redis_client.set(cache_key, json.dumps(payload), ex=86400)  # 24h
        except Exception as cache_err:
            print(f"Wrapped cache set failed: {cache_err}")

        return jsonify(payload), 200
    except Exception as e:
        print("Exception in wrapped_sleeper: " + str(e))
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@main.route('/wrapped/sleeper/<league_id>/inspect_trade', methods=['GET'])
def wrapped_inspect_trade(league_id):
    """Return the full trade-inspector payload for a single trade.

    Query params:
        transaction_id: Sleeper transaction id (required).
        year:           4-digit fantasy year the trade lives in (default "2024").

    Returns ``{trade, race_chart, per_asset_series, k, evaluation_end}``.
    Dynasty-only -- redraft leagues 400 since the KTC integral is the
    wrong tool for short-window swap evaluation.
    """
    try:
        transaction_id = request.args.get('transaction_id')
        if not transaction_id:
            return jsonify({'error': 'transaction_id is required'}), 400
        year = request.args.get('year', '2024')

        # Cache the payload per (league, year, transaction). Trades are
        # immutable post-acceptance so a long TTL is safe; we just want
        # to amortize the blob load + per-asset sampling cost.
        cache_key = f"wrapped_inspect_v1_{league_id}_{year}_{transaction_id}"
        redis_client = current_app.redis_client
        try:
            cached = redis_client.get(cache_key)
        except Exception:
            cached = None
        if cached:
            try:
                return jsonify(json.loads(cached)), 200
            except Exception:
                pass  # fall through to recompute

        ctx = load_league_context(league_id, year)
        if not ctx.is_dynasty:
            return jsonify({
                'error': 'Trade inspector is dynasty-only',
                'is_dynasty': False,
            }), 400

        transactions = fetch_league_transactions(ctx)
        trade = next(
            (t for t in transactions.trades if t.transaction_id == transaction_id),
            None,
        )
        if trade is None:
            return jsonify({
                'error': f'Trade {transaction_id} not found in league {league_id}',
            }), 404

        players_meta = load_blob("players.json") or {}

        try:
            payload = inspect_trade_payload(
                trade,
                season=int(year),
                num_qbs=ctx.num_qbs,
                players_meta=players_meta,
                league_id=league_id,
            )
        except Exception as exc:
            # The KTC historical blob is the most common failure here;
            # surface a 503 so the UI can show "value history unavailable"
            # instead of a generic crash.
            print(f"inspect_trade payload build failed: {exc}")
            traceback.print_exc()
            return jsonify({
                'error': 'Trade value history unavailable',
                'detail': str(exc),
            }), 503

        try:
            redis_client.set(cache_key, json.dumps(payload), ex=86400)  # 24h
        except Exception as cache_err:
            print(f"Inspect-trade cache set failed: {cache_err}")

        return jsonify(payload), 200
    except Exception as e:
        print("Exception in wrapped_inspect_trade: " + str(e))
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@main.route('/wrapped/sleeper/<league_id>/inspect_trade_redraft', methods=['GET'])
def wrapped_inspect_trade_redraft(league_id):
    """Retrospective redraft trade evaluation.

    Query params:
        transaction_id: Sleeper transaction id (required).
        year:           4-digit fantasy year the trade lives in (default "2024").

    Returns ``{transaction_id, trade_week, evaluation, baseline_total_points}``.
    Redraft-only -- dynasty leagues 400 since they should use the KTC
    value-integral ``inspect_trade`` endpoint instead.

    503 when ``player_season_scoring_{year}.json`` is missing from blob
    storage (run ``tools/bootstrap_historical_sleeper.py`` to backfill).
    """
    try:
        transaction_id = request.args.get('transaction_id')
        if not transaction_id:
            return jsonify({'error': 'transaction_id is required'}), 400
        year = request.args.get('year', '2024')

        cache_key = f"wrapped_inspect_redraft_v1_{league_id}_{year}_{transaction_id}"
        redis_client = current_app.redis_client
        try:
            cached = redis_client.get(cache_key)
        except Exception:
            cached = None
        if cached:
            try:
                return jsonify(json.loads(cached)), 200
            except Exception:
                pass

        ctx = load_league_context(league_id, year)
        if ctx.is_dynasty:
            return jsonify({
                'error': 'Redraft inspector is for redraft leagues only; '
                         'use /inspect_trade for dynasty.',
                'is_dynasty': True,
            }), 400

        transactions = fetch_league_transactions(ctx)
        trade = next(
            (t for t in transactions.trades if t.transaction_id == transaction_id),
            None,
        )
        if trade is None:
            return jsonify({
                'error': f'Trade {transaction_id} not found in league {league_id}',
            }), 404

        season_scoring = load_blob(f"player_season_scoring_{year}.json") or {}
        if not season_scoring:
            return jsonify({
                'error': 'Season scoring data unavailable for redraft inspector',
                'detail': f'player_season_scoring_{year}.json is missing or empty',
            }), 503

        try:
            payload = inspect_redraft_trade(
                trade,
                ctx=ctx,
                season=int(year),
                season_scoring=season_scoring,
            )
        except RuntimeError as exc:
            return jsonify({
                'error': 'Redraft inspector failed',
                'detail': str(exc),
            }), 503

        try:
            redis_client.set(cache_key, json.dumps(payload), ex=86400)  # 24h
        except Exception as cache_err:
            print(f"Inspect-trade-redraft cache set failed: {cache_err}")

        return jsonify(payload), 200
    except Exception as e:
        print("Exception in wrapped_inspect_trade_redraft: " + str(e))
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@main.route('/player/<player_id>', methods=['GET'])
def player_detail(player_id):
    """Return aggregated metadata + scoring + ownership history for a player."""
    try:
        cache_key = f"player_detail_v1_{player_id}"
        redis_client = current_app.redis_client

        try:
            cached = redis_client.get(cache_key)
        except Exception:
            cached = None
        if cached:
            try:
                return jsonify(json.loads(cached)), 200
            except Exception:
                pass

        payload = get_player_detail(player_id)
        if payload is None:
            return jsonify({'error': f'Unknown player_id: {player_id}'}), 404

        try:
            redis_client.set(cache_key, json.dumps(payload), ex=3600)  # 1h
        except Exception as cache_err:
            print(f"Player detail cache set failed: {cache_err}")

        return jsonify(payload), 200
    except Exception as e:
        print("Exception in player_detail: " + str(e))
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@main.route('/sleeper/user/<username>/leagues', methods=['GET'])
def sleeper_user_leagues(username):
    """List a Sleeper user's NFL leagues for a given year.

    Query params:
        year: 4-digit fantasy year (default = current fantasy year).
    """
    try:
        from app.services.season import get_current_fantasy_year
        year = request.args.get('year') or get_current_fantasy_year()

        cache_key = f"sleeper_user_leagues_{username}_{year}"
        redis_client = current_app.redis_client
        try:
            cached = redis_client.get(cache_key)
        except Exception:
            cached = None
        if cached:
            try:
                return jsonify(json.loads(cached)), 200
            except Exception:
                pass

        leagues = get_user_leagues(username, str(year))
        payload = {'username': username, 'year': str(year), 'leagues': leagues}

        try:
            redis_client.set(cache_key, json.dumps(payload), ex=300)  # 5 min
        except Exception as cache_err:
            print(f"sleeper_user_leagues cache set failed: {cache_err}")

        return jsonify(payload), 200
    except Exception as e:
        print("Exception in sleeper_user_leagues: " + str(e))
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@main.route('/sleeper/league/<league_id>/resolve', methods=['GET'])
def sleeper_league_resolve(league_id):
    """Resolve a current league_id to its historical id for ?year=YYYY.

    Walks Sleeper's previous_league_id chain. Returns ``{league_id: null}``
    if the league didn't exist that year.
    """
    try:
        year = request.args.get('year')
        if not year:
            return jsonify({'error': "Query param 'year' is required"}), 400

        cache_key = f"sleeper_league_resolve_{league_id}_{year}"
        redis_client = current_app.redis_client
        try:
            cached = redis_client.get(cache_key)
        except Exception:
            cached = None
        if cached:
            try:
                return jsonify(json.loads(cached)), 200
            except Exception:
                pass

        resolved = resolve_league_for_year(league_id, str(year))
        payload = {
            'requested_league_id': league_id,
            'requested_year': str(year),
            'league_id': resolved,
        }

        try:
            redis_client.set(cache_key, json.dumps(payload), ex=3600)  # 1h
        except Exception as cache_err:
            print(f"sleeper_league_resolve cache set failed: {cache_err}")

        return jsonify(payload), 200
    except Exception as e:
        print("Exception in sleeper_league_resolve: " + str(e))
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@main.route('/sleeper/league/<league_id>/seasons', methods=['GET'])
def sleeper_league_seasons(league_id):
    """List every season this league has on Sleeper, newest first.

    Walks the ``previous_league_id`` chain. Used by the Wrapped page to
    populate its year dropdown with only the years that actually exist
    for this league.
    """
    try:
        cache_key = f"sleeper_league_seasons_{league_id}"
        redis_client = current_app.redis_client
        try:
            cached = redis_client.get(cache_key)
        except Exception:
            cached = None
        if cached:
            try:
                return jsonify(json.loads(cached)), 200
            except Exception:
                pass

        seasons = get_league_season_chain(league_id)
        payload = {'requested_league_id': league_id, 'seasons': seasons}

        try:
            redis_client.set(cache_key, json.dumps(payload), ex=3600)  # 1h
        except Exception as cache_err:
            print(f"sleeper_league_seasons cache set failed: {cache_err}")

        return jsonify(payload), 200
    except Exception as e:
        print("Exception in sleeper_league_seasons: " + str(e))
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500
