"""Top-level orchestrator for the Wrapped feature.

Public surface:
    compute_wrapped(league_id, year) -> dict

The payload is built up in phase modules so each new accolade category is a
sibling key — never a reshape — keeping the frontend forwards-compatible.

Currently wired:
* Phase 1 — ``schedule`` (luck, consistency, manager efficiency, etc).
* Phase 2 — ``roster_moves`` (troll, early pickup, late drop, best add,
  worst drop).
* Phase 3 (next) — ``draft`` + ``trades``.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from app.services.blob_store import load_blob, try_load_blob
from app.services.season import get_current_fantasy_year
from app.services.wrapped.draft import fetch_and_compute_draft
from app.services.wrapped.fantasy_calc import get_player_values
from app.services.wrapped.league_context import LeagueContext, load_league_context
from app.services.wrapped.replacement_value import compute_baseline_player_scoring
from app.services.wrapped.roster_accolades import (
    calculate_roster_accolades,
    calculate_troll_metric,
)
from app.services.wrapped.schedule import WeeklyScores, fetch_weekly_scores
from app.services.wrapped.schedule_accolades import (
    calculate_best_and_worst_manager,
    calculate_biggest_falloff_and_come_up,
    calculate_consistencies,
    calculate_each_users_best_and_worst_schedule,
    calculate_hypothetical_records,
    calculate_luckiest_and_unluckiest,
    calculate_weekly_best_ball_records,
)
from app.services.wrapped.transactions import LeagueTransactions, fetch_league_transactions
from app.services.wrapped.trade_accolades import calculate_trade_accolades
from app.services.wrapped.streamers_accolades import calculate_streamer_accolades

logger = logging.getLogger(__name__)

# Sentinel value the route layer can pass to mean "every year on record".
ALL_SUPPORTED_YEAR_HINT = "all"


def _build_schedule_section(weekly_scores: WeeklyScores) -> Dict[str, Any]:
    """Phase-1 schedule accolades. Pure over WeeklyScores."""
    hypothetical_matrix: Dict[str, Dict[str, Dict[str, int]]] = {
        username: calculate_hypothetical_records(username, weekly_scores)
        for username in weekly_scores.usernames
    }
    return {
        "best_ball_records": calculate_weekly_best_ball_records(weekly_scores),
        "luck": calculate_luckiest_and_unluckiest(weekly_scores),
        "consistency": calculate_consistencies(weekly_scores),
        "manager_efficiency": calculate_best_and_worst_manager(weekly_scores),
        "falloff_comeup": calculate_biggest_falloff_and_come_up(weekly_scores),
        "best_worst_schedule": calculate_each_users_best_and_worst_schedule(weekly_scores),
        "hypothetical_matrix": hypothetical_matrix,
        "weekly_scores": {
            user: {str(w): pts for w, pts in byweek.items()}
            for user, byweek in weekly_scores.user_score_by_week.items()
        },
        "median_scores": {str(w): pts for w, pts in weekly_scores.median_scores.items()},
    }


def _build_roster_moves_section(
    ctx: LeagueContext,
    weekly_scores: WeeklyScores,
    players_meta: Dict[str, Dict[str, Any]],
    season_scoring: Dict[str, Dict[str, Any]],
    ownership_history: Dict[str, Dict[str, Dict[str, float]]],
    transactions: LeagueTransactions,
) -> Dict[str, Any]:
    """Phase-2 roster-move accolades.

    Each sub-accolade degrades independently rather than the section
    blanking wholesale when one input is missing:

    * troll only needs live matchups (always available).
    * best_add / worst_drop need season scoring. We fall back to the
      latest ``players.json`` when the per-year scoring blob hasn't been
      captured (common for past seasons + the very start of a new one).
    * early_pickup / late_drop need ownership history; if it's missing
      they come back ``None`` but the rest of the section still renders.
    """
    troll = calculate_troll_metric(weekly_scores, players_meta)

    # Fall back to players.json (which carries the same ``scoring_data_*``
    # shape) when the year-specific scoring blob isn't there. Only useful
    # for the current season — past seasons would get wrong values, so we
    # leave them empty rather than mislead.
    effective_scoring = season_scoring or (
        players_meta if str(ctx.year) == str(get_current_fantasy_year()) else {}
    )

    if not effective_scoring:
        logger.info(
            "Wrapped: no season scoring available for %s/%s — best_add /"
            " worst_drop will be empty",
            ctx.league_id,
            ctx.year,
        )
        return {"troll": troll, "by_user": {}}

    baseline = compute_baseline_player_scoring(ctx, effective_scoring)
    by_user = calculate_roster_accolades(
        ctx_current_rosters=ctx.current_rosters,
        transactions=transactions,
        ownership_history=ownership_history,  # may be empty; downstream handles it
        season_scoring=effective_scoring,
        qb_score_key=ctx.qb_score_key,
        skill_score_key=ctx.skill_score_key,
        baseline=baseline,
        current_week=ctx.last_regular_season_week or 1,
    )

    return {
        "troll": troll,
        "by_user": by_user,
        "baseline_player_scoring": {k: round(v, 2) for k, v in baseline.items()},
    }


def _build_draft_section(
    ctx: LeagueContext,
    season_scoring: Dict[str, Dict[str, Any]],
    players_meta: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    """Phase-3 draft accolades.

    Falls back to ``players.json`` for the current season the same way
    roster_moves does. Past seasons without a captured scoring blob get
    an empty section rather than wrong rankings derived from current data.
    """
    effective_scoring = season_scoring or (
        players_meta if str(ctx.year) == str(get_current_fantasy_year()) else {}
    )
    if not effective_scoring:
        logger.info(
            "Wrapped: no season scoring for %s/%s — draft accolades empty",
            ctx.league_id,
            ctx.year,
        )
        return {"by_user": {}, "biggest_steal": None, "biggest_bust": None,
                "mr_irrelevant_hero": None}

    accolades = fetch_and_compute_draft(ctx, effective_scoring, players_meta)
    # Hydrate player names alongside ids — the frontend doesn't have
    # players.json on hand and we'd rather render names than ID strings.
    def _hydrate(payload: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if not payload:
            return payload
        pid = payload.get("player_id")
        if pid:
            meta = players_meta.get(pid) or effective_scoring.get(pid) or {}
            payload = {**payload, "name": meta.get("full_name") or pid}
        return payload

    by_user = {
        user: {
            "best_pick": _hydrate(rec.get("best_pick")),
            "worst_pick": _hydrate(rec.get("worst_pick")),
            "num_picks": rec.get("num_picks"),
        }
        for user, rec in accolades.by_user.items()
    }
    return {
        "by_user": by_user,
        "biggest_steal": _hydrate(accolades.biggest_steal),
        "biggest_bust": _hydrate(accolades.biggest_bust),
        "mr_irrelevant_hero": _hydrate(accolades.mr_irrelevant_hero),
    }


def _build_trades_section(
    ctx: LeagueContext,
    transactions: LeagueTransactions,
    players_meta: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    """Phase-3 trade accolades. FantasyCalc-valued trades + winners.

    Empty section when the league had no completed trades. FantasyCalc
    failure degrades to "every player worth 0" — the trade list still
    renders so the user can see who traded with whom even without values.
    """
    if not transactions.trades:
        return {"trades": [], "by_user": {}, "biggest_fleecing": None,
                "most_active_trader": None}

    player_values = get_player_values(
        is_dynasty=ctx.is_dynasty,
        num_qbs=ctx.num_qbs,
        skill_score_key=ctx.skill_score_key,
    )
    return calculate_trade_accolades(transactions, player_values, players_meta)


def _build_streamers_section(
    ctx: LeagueContext,
    weekly_scores: WeeklyScores,
) -> Dict[str, Any]:
    """Phase-4 streamers section: per-user K + DEF averages.

    Pure over the already-collected ``WeeklyScores`` (the per-position
    starter scoring is bucketed in there). Returns an empty section if
    the league rosters neither K nor DEF.
    """
    payload = calculate_streamer_accolades(
        weekly_scores, ctx.roster_positions_groups
    )
    return {
        "positions_included": payload.positions_included,
        "by_user": payload.by_user,
        "best_kicker": payload.best_kicker,
        "best_defense": payload.best_defense,
        "best_combined": payload.best_combined,
    }


def _build_payload(league_id: str, year: str) -> Dict[str, Any]:
    """Compute the full Wrapped payload for a single (league, year)."""
    ctx = load_league_context(league_id, year)

    # ``players.json`` is always the latest snapshot. The per-year scoring /
    # ownership blobs are written by the scraper as the season progresses;
    # they're optional and gracefully absent for past seasons we never
    # captured.
    players_meta = load_blob("players.json") or {}
    season_scoring = try_load_blob(f"player_season_scoring_{year}.json") or {}
    ownership_history = try_load_blob(f"owned_history_{year}.json") or {}

    weekly_scores = fetch_weekly_scores(ctx, players_meta)
    # Single fan-out across all regular-season weeks; both roster_moves
    # and trades sections consume this.
    transactions = fetch_league_transactions(ctx)

    return {
        "meta": {
            "league_id": ctx.league_id,
            "league_name": ctx.league_settings.get("name"),
            "year": ctx.year,
            "is_dynasty": ctx.is_dynasty,
            "num_qbs": ctx.num_qbs,
            "weeks_played": weekly_scores.weeks_played,
            "playoff_week_start": ctx.playoff_week_start,
            "scoring_keys": {"qb": ctx.qb_score_key, "skill": ctx.skill_score_key},
            "users": sorted(weekly_scores.usernames),
        },
        "schedule": _build_schedule_section(weekly_scores),
        "roster_moves": _build_roster_moves_section(
            ctx, weekly_scores, players_meta, season_scoring, ownership_history,
            transactions,
        ),
        "draft": _build_draft_section(ctx, season_scoring, players_meta),
        "trades": _build_trades_section(ctx, transactions, players_meta),
        "streamers": _build_streamers_section(ctx, weekly_scores),
    }


def compute_wrapped(league_id: str, year: str) -> Dict[str, Any]:
    """Build the Wrapped payload for one league + year.

    The route layer is responsible for caching this result in Redis — this
    function is intentionally a pure read pipeline so it's trivially testable
    against captured fixtures.
    """
    return _build_payload(str(league_id), str(year))
