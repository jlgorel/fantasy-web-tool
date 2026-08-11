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
from app.services.draft_help import summaries as draft_help_summaries
from app.services.draft_help import sim as draft_help_sim
from app.services.draft_help import draft_fetch as draft_help_fetch
from app.services.draft_help import live_draft as draft_help_live
from app.services.draft_help.sim import sim_players_from_config_players
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
import math
import hashlib

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
                # Per-year cache is 24h; aggregating from already-cached
                # per-year payloads is cheap, so we can hold the all-time
                # roll-up for a while too.
                redis_client.set(cache_key, json.dumps(payload), ex=21600)  # 6h
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


def _cached_json(cache_key, builder, ttl_seconds):
    """Return a cached JSON payload or build+cache it. Mirrors the wrapped
    route's redis pattern; degrades gracefully when redis is unavailable."""
    redis_client = current_app.redis_client
    try:
        cached = redis_client.get(cache_key)
    except Exception:
        cached = None
    if cached:
        try:
            return json.loads(cached)
        except Exception:
            pass  # fall through to rebuild
    payload = builder()
    try:
        redis_client.set(cache_key, json.dumps(payload), ex=ttl_seconds)
    except Exception as cache_err:
        print(f"draft-help cache set failed for {cache_key}: {cache_err}")
    return payload


@main.route('/draft-help/user/<username>/habits', methods=['GET'])
def draft_help_user_habits(username):
    """Feature 1: a user's draft tendencies across all their leagues."""
    try:
        seasons = max(1, min(int(request.args.get('seasons', 3)), 6))
        cache_key = f"drafthelp_user_v2_{username}_{seasons}"
        payload = _cached_json(
            cache_key,
            lambda: draft_help_summaries.user_habits(username, seasons),
            86400,  # 24h -- completed drafts are immutable
        )
        return jsonify(payload), 200
    except Exception as e:
        print("Exception in draft_help_user_habits: " + str(e))
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@main.route('/draft-help/league/<league_id>/habits', methods=['GET'])
def draft_help_league_habits(league_id):
    """Feature 2: a league's managers' draft tendencies (this league)."""
    try:
        seasons = max(1, min(int(request.args.get('seasons', 3)), 6))
        cache_key = f"drafthelp_league_v2_{league_id}_{seasons}"
        payload = _cached_json(
            cache_key,
            lambda: draft_help_summaries.league_habits(league_id, seasons),
            86400,
        )
        return jsonify(payload), 200
    except Exception as e:
        print("Exception in draft_help_league_habits: " + str(e))
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@main.route('/draft-help/league/<league_id>/opponents', methods=['GET'])
def draft_help_opponents(league_id):
    """Feature 3: league-mates' tendencies in their OTHER leagues (slow)."""
    try:
        seasons = max(1, min(int(request.args.get('seasons', 3)), 6))
        max_leagues = max(1, min(int(request.args.get('max_leagues', 5)), 10))
        cache_key = f"drafthelp_opp_v2_{league_id}_{seasons}_{max_leagues}"
        payload = _cached_json(
            cache_key,
            lambda: draft_help_summaries.opponents_habits(league_id, seasons, max_leagues),
            86400,
        )
        return jsonify(payload), 200
    except Exception as e:
        print("Exception in draft_help_opponents: " + str(e))
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


def _parse_bool(value, default=False):
    if value is None:
        return default
    return str(value).lower() in ('1', 'true', 'yes', 'sf', 'on')


def _parse_value_overrides(raw):
    """Sanitize browser-supplied ``{player_id: value}`` overrides.

    Values are VBD/VORP-style cross-position numbers. A bounded map prevents a
    malformed upload from creating excessive work or non-finite sim values.
    """
    if not isinstance(raw, dict):
        return {}
    out = {}
    for pid, value in list(raw.items())[:500]:
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(numeric) or numeric < -10000 or numeric > 10000:
            continue
        out[str(pid)] = numeric
    return out


def _parse_avoid_ids(raw):
    if not isinstance(raw, list):
        return []
    return [str(pid) for pid in raw[:500] if pid is not None and str(pid)]


def _parse_pick_numbers(raw):
    if not isinstance(raw, list):
        return None
    out = []
    for value in raw[:100]:
        try:
            pick = int(value)
        except (TypeError, ValueError):
            continue
        if pick > 0:
            out.append(pick)
    return out


def _parse_sim_slots(raw, superflex):
    """Sanitize a client-supplied starting-slot map for the draft sim.

    Accepts ``{POS: count}`` (e.g. ``{"RB": 2, "WR": 3, "FLEX": 2}``) keeping
    only known dedicated/flex slots with positive counts (capped). Falls back to
    the superflex-aware default when nothing usable is provided. A superflex
    league always gets a SUPER_FLEX slot so QB value is modeled correctly.
    """
    default = draft_help_sim.default_starting_slots(superflex)
    if not isinstance(raw, dict) or not raw:
        return default
    allowed = set(draft_help_sim.FLEX_GROUPS) | {"QB", "RB", "WR", "TE"}
    slots = {}
    for key, val in raw.items():
        pos = str(key).upper()
        if pos not in allowed:
            continue
        try:
            count = int(val)
        except (TypeError, ValueError):
            continue
        if count > 0:
            slots[pos] = min(count, 10)
    if not slots:
        return default
    if superflex and "SUPER_FLEX" not in slots:
        slots["SUPER_FLEX"] = 1
    return slots


@main.route('/draft-help/rankings', methods=['GET'])
def draft_help_rankings():
    """Player value board for a league config (year/teams/ppr/superflex).

    Drives the mock draft board + the sim. ADP == overall_rank, proj == fpts.
    """
    try:
        year = request.args.get('year', '2024')
        teams = int(request.args.get('teams', 12))
        ppr = float(request.args.get('ppr', 0.5))
        superflex = _parse_bool(request.args.get('sf'))
        players = draft_help_summaries.rankings_config_players(year, teams, ppr, superflex)
        return jsonify({
            'year': str(year),
            'config': {'teams': teams, 'ppr': ppr, 'superflex': superflex},
            'sources': draft_help_summaries.rankings_config_sources(
                year, teams, ppr, superflex,
            ),
            'players': players,
        }), 200
    except Exception as e:
        print("Exception in draft_help_rankings: " + str(e))
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@main.route('/draft-help/sim', methods=['POST'])
def draft_help_sim_route():
    """Monte-Carlo 'who should I draft now?' for a snake mock/live draft.

    Body: {year, teams, rounds, my_slot, ppr, superflex, drafted_ids[],
    my_roster_ids[], slots?{POS:count}, current_pick?, n_sims?, top_k?, seed?}.
    """
    try:
        body = request.get_json(force=True, silent=True) or {}
        year = body.get('year', '2024')
        teams = int(body.get('teams', 12))
        rounds = int(body.get('rounds', 15))
        my_slot = int(body.get('my_slot', 1))
        ppr = float(body.get('ppr', 0.5))
        superflex = bool(body.get('superflex', False))
        use_provider_values = body.get('use_provider_values', True) is not False
        value_overrides = _parse_value_overrides(body.get('value_overrides'))
        avoid_ids = _parse_avoid_ids(body.get('avoid_ids'))
        priority_candidate_ids = _parse_avoid_ids(
            body.get('priority_candidate_ids')
        )
        my_future_pick_numbers = _parse_pick_numbers(
            body.get('my_future_pick_numbers')
        )
        if my_future_pick_numbers is not None:
            my_future_pick_numbers = sorted(my_future_pick_numbers)
        config_players = draft_help_summaries.rankings_config_players(
            year, teams, ppr, superflex,
        )
        simulation_config_players = config_players
        if not use_provider_values:
            simulation_config_players = [
                {
                    **row,
                    'vbd': None,
                    'fpts': None,
                    'auction': None,
                    'tier': None,
                }
                for row in config_players
            ]
        custom_profile = not use_provider_values
        adp_only = bool(simulation_config_players) and all(
            row.get('vbd') is None and row.get('fpts') is None
            for row in simulation_config_players
        )
        if adp_only and len(value_overrides) < 50:
            return jsonify({
                'error': 'This profile requires a custom value sheet',
                'detail': (
                    ('The selected starter/bench/scoring profile does not match '
                     'the published provider sheet. ' if custom_profile else '')
                    + 'Paste at least 50 finished player Value/VORP overrides; '
                    f'{len(value_overrides)} were supplied.'
                ),
            }), 400
        players = sim_players_from_config_players(
            simulation_config_players,
            value_overrides=value_overrides,
        )
        if not players:
            return jsonify({'error': 'no rankings for that config/year'}), 503
        slots = _parse_sim_slots(body.get('slots'), superflex)
        n_sims = max(10, min(int(body.get('n_sims', 150)), 400))
        top_k = max(1, min(int(body.get('top_k', 6)), 12))
        drafted_ids = sorted(str(pid) for pid in body.get('drafted_ids', []))
        my_roster_ids = sorted(str(pid) for pid in body.get('my_roster_ids', []))
        player_revision = [
            (
                str(row.get('player_id') or ''),
                row.get('vbd'), row.get('fpts'), row.get('adp'),
                row.get('adp_stdev'),
            )
            for row in simulation_config_players
        ]
        cache_spec = {
            'version': 4,
            'year': str(year), 'teams': teams, 'rounds': rounds,
            'my_slot': my_slot, 'ppr': ppr, 'superflex': superflex,
            'use_provider_values': use_provider_values,
            'slots': slots, 'current_pick': body.get('current_pick'),
            'n_sims': n_sims, 'top_k': top_k, 'seed': body.get('seed'),
            'drafted_ids': drafted_ids, 'my_roster_ids': my_roster_ids,
            'avoid_ids': sorted(avoid_ids),
            'priority_candidate_ids': sorted(priority_candidate_ids),
            'my_future_pick_numbers': my_future_pick_numbers,
            'value_overrides': sorted(value_overrides.items()),
            'player_revision': player_revision,
        }
        cache_digest = hashlib.sha256(json.dumps(
            cache_spec, sort_keys=True, separators=(',', ':'),
        ).encode('utf-8')).hexdigest()
        cache_key = f'draft_help_sim_v4_{cache_digest}'
        redis_client = getattr(current_app, 'redis_client', None)
        try:
            cached = redis_client.get(cache_key) if redis_client else None
        except Exception:
            cached = None
        if cached:
            try:
                payload = json.loads(cached)
                payload['cache_hit'] = True
                return jsonify(payload), 200
            except Exception:
                pass
        result = draft_help_sim.recommend_pick(
            players,
            drafted_ids=drafted_ids,
            my_roster_ids=my_roster_ids,
            teams=teams, rounds=rounds, my_slot=my_slot, slots=slots,
            current_pick=body.get('current_pick'),
            n_sims=n_sims,
            top_k=top_k,
            seed=body.get('seed'),
            avoid_ids=avoid_ids,
            priority_candidate_ids=priority_candidate_ids,
            my_future_pick_numbers=my_future_pick_numbers,
        )
        result['cache_hit'] = False
        try:
            if redis_client:
                redis_client.set(cache_key, json.dumps(result), ex=300)
        except Exception:
            pass
        return jsonify(result), 200
    except Exception as e:
        print("Exception in draft_help_sim_route: " + str(e))
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


def _live_draft_response(draft_id):
    detail = draft_help_fetch.fetch_draft_detail(str(draft_id))
    if not detail:
        return jsonify({
            'error': 'Sleeper draft data is temporarily unavailable',
            'detail': f'Could not load draft {draft_id}; retry shortly.',
        }), 503
    try:
        draft_help_live.validate_supported_draft(detail)
    except draft_help_live.LiveDraftError as exc:
        return jsonify({'error': str(exc)}), 400

    username = (request.args.get('username') or '').strip()
    user_id = draft_help_fetch.resolve_user_id(username) if username else None
    slot_raw = request.args.get('slot')
    try:
        selected_slot = int(slot_raw) if slot_raw else None
    except ValueError:
        return jsonify({'error': 'slot must be an integer'}), 400

    known_last_picked = request.args.get('known_last_picked')
    known_status = request.args.get('known_status')
    current_last_picked = detail.get('last_picked')
    current_last_picked_token = (
        'null' if current_last_picked is None else str(current_last_picked)
    )
    if (
        known_last_picked is not None
        and current_last_picked_token == known_last_picked
        and str(detail.get('status') or '') == str(known_status or '')
    ):
        return jsonify(draft_help_live.unchanged_live_response(detail)), 200

    picks = draft_help_fetch.fetch_draft_picks(str(draft_id))
    traded = draft_help_fetch.fetch_draft_traded_picks(str(draft_id))
    try:
        payload = draft_help_live.build_live_draft_state(
            detail, picks, traded,
            user_id=user_id, selected_slot=selected_slot,
        )
    except draft_help_live.LiveDraftError as exc:
        return jsonify({'error': str(exc)}), 400
    if username and not user_id:
        payload['username_warning'] = f'Sleeper username not found: {username}'
    elif username and payload.get('needs_slot'):
        payload['username_warning'] = (
            f'{username} is not assigned a slot in this draft; select one manually.'
        )
    return jsonify(payload), 200


@main.route('/draft-help/live/draft/<draft_id>', methods=['GET'])
def draft_help_live_draft(draft_id):
    """Read-only live Sleeper draft state by direct draft ID."""
    try:
        return _live_draft_response(draft_id)
    except Exception as e:
        print("Exception in draft_help_live_draft: " + str(e))
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@main.route('/draft-help/live/league/<league_id>', methods=['GET'])
def draft_help_live_league(league_id):
    """Find the active/pre-draft redraft snake draft for a selected league."""
    try:
        league = draft_help_fetch.fetch_league(league_id)
        if not league:
            return jsonify({'error': f'Sleeper league not found: {league_id}'}), 404
        if draft_help_fetch.is_dynasty_league(league):
            return jsonify({'error': 'Dynasty drafts are not supported yet.'}), 400
        selected = draft_help_live.choose_league_draft(
            draft_help_fetch.fetch_league_drafts(league_id)
        )
        if not selected or not selected.get('draft_id'):
            return jsonify({
                'error': 'No active or upcoming supported snake draft found for this league.'
            }), 404
        return _live_draft_response(str(selected['draft_id']))
    except Exception as e:
        print("Exception in draft_help_live_league: " + str(e))
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


@main.route('/projection-review', methods=['GET'])
def projection_review():
    """Return the Vegas-projection accuracy review for a season.

    Query params:
        year: 4-digit fantasy year (default = current fantasy year).

    The review blob (``projection_review_{year}.json``) is produced by the
    scraper's Tuesday ``weekly_projection_accuracy_review`` timer. Returns 404
    when no review has been generated yet (e.g. pre-Week-1).
    """
    try:
        from app.services.season import get_current_fantasy_year
        from app.services.blob_store import try_load_blob
        year = request.args.get('year') or get_current_fantasy_year()

        review = try_load_blob(f"projection_review_{year}.json")
        if not review:
            return jsonify({
                'error': 'No projection review available',
                'detail': f'projection_review_{year}.json is missing or empty',
            }), 404
        return jsonify(review), 200
    except Exception as e:
        print("Exception in projection_review: " + str(e))
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
        exclude_dynasty = _parse_bool(request.args.get('exclude_dynasty'))

        cache_key = f"sleeper_user_leagues_{username}_{year}_{int(exclude_dynasty)}"
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

        leagues = get_user_leagues(username, str(year), exclude_dynasty=exclude_dynasty)
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
