"""Redraft trade ledger builder for the Wrapped pipeline.

Produces the per-trade list + summary accolades a redraft league's
Wrapped page renders, mirroring the dynasty ``calculate_trade_accolades``
output shape so the frontend ledger can share the same row component.

Scoring strategy: VORP over the rest-of-season window (trade week + 1
through last regular-season week), computed by
:mod:`app.services.wrapped.redraft_trade_inspector.evaluate_redraft_trade`.
The per-side ``ktc_equiv`` field is repurposed to carry total VORP so the
existing frontend row can render it without per-row schema changes; the
section header re-labels it as "VORP" for redraft.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any, Dict, List, Mapping

from app.services.wrapped.league_context import LeagueContext
from app.services.wrapped.redraft_trade_inspector import (
    evaluate_redraft_trade,
)
from app.services.wrapped.replacement_value import (
    compute_baseline_player_scoring,
)
from app.services.wrapped.transactions import LeagueTransactions, Trade

logger = logging.getLogger(__name__)


def _empty_section() -> Dict[str, Any]:
    return {
        "trades": [],
        "by_user": {},
        "biggest_fleecing": None,
        "most_active_trader": None,
        "scoring_mode": "redraft_vorp",
    }


def _summarize_redraft_trade(
    trade: Trade,
    *,
    ctx: LeagueContext,
    season: int,
    season_scoring: Mapping[str, Mapping[str, Any]],
    baseline_total_points: Mapping[str, float],
    players_meta: Mapping[str, Mapping[str, Any]],
) -> Dict[str, Any]:
    """Render one redraft trade in the same shape the dynasty path uses.

    ``ktc_equiv`` carries total VORP, ``ktc_edge_per_season`` carries the
    side-margin VORP. The frontend re-labels these for redraft.
    """
    start_week = max(1, int(trade.week) + 1)
    end_week = int(ctx.last_regular_season_week or 17)
    if end_week < start_week:
        end_week = start_week  # trade after final regular-season week

    eval_obj = evaluate_redraft_trade(
        trade,
        season=season,
        season_scoring=season_scoring,
        qb_score_key=ctx.qb_score_key,
        skill_score_key=ctx.skill_score_key,
        baseline_total_points=baseline_total_points,
        start_week=start_week,
        end_week=end_week,
    )

    sides_payload: List[Dict[str, Any]] = []
    for side_eval in eval_obj.sides:
        assets_payload = [{
            "asset_id": a.player_id,
            "label": a.name,
            "sleeper_id": a.player_id,
            "is_pick": False,
            # avg_ktc field is repurposed as ROS PPG so the per-row
            # column still has a meaningful per-asset number.
            "avg_ktc": round(a.ros_ppg, 1),
            "active_days": a.games_played,
            "score": round(a.vorp, 1),
        } for a in side_eval.assets]
        sides_payload.append({
            "username": side_eval.username,
            "assets": assets_payload,
            "total_score": round(side_eval.total_vorp, 1),
            # ktc_equiv reused as total VORP for the redraft frontend.
            "ktc_equiv": round(side_eval.total_vorp, 1),
        })

    sides_payload.sort(key=lambda s: s["total_score"], reverse=True)

    margin = eval_obj.margin_vorp if eval_obj.verdict != "wash" else 0.0
    return {
        "week": trade.week,
        "transaction_id": trade.transaction_id,
        "trade_date": None,
        "evaluation_end": None,
        "sides": sides_payload,
        "winner": eval_obj.verdict if eval_obj.verdict != "wash" else "",
        "k": 1.0,
        "active_days": (end_week - start_week + 1),
        "ktc_edge_per_season": round(margin, 1),
        "ktc_edge_total": round(margin, 1),
        "margin_label": eval_obj.margin_label,
    }


def build_redraft_trades_section(
    ctx: LeagueContext,
    transactions: LeagueTransactions,
    season_scoring: Mapping[str, Mapping[str, Any]],
    players_meta: Mapping[str, Mapping[str, Any]],
) -> Dict[str, Any]:
    """Build the wrapped ``trades`` payload for a redraft league.

    Returns an empty section when there are no completed trades or the
    per-year scoring blob is unavailable. The latter case still surfaces
    the trades list with zero-VORP placeholders so the ledger isn't
    invisible -- the inspector route will then 503 on click.
    """
    if not transactions.trades:
        return _empty_section()
    if not season_scoring:
        logger.info(
            "Redraft trades: no season scoring for %s/%s; emitting basic ledger only",
            ctx.league_id, ctx.year,
        )

    try:
        season_int = int(ctx.year)
    except (TypeError, ValueError):
        season_int = 0

    baseline = (
        compute_baseline_player_scoring(ctx, season_scoring)
        if season_scoring else {"QB": 0.0, "RB": 0.0, "WR": 0.0, "TE": 0.0}
    )

    trades_payload: List[Dict[str, Any]] = []
    by_user_trades: Dict[str, int] = defaultdict(int)
    by_user_net_vorp: Dict[str, float] = defaultdict(float)
    biggest: Dict[str, Any] = None  # type: ignore[assignment]

    for trade in transactions.trades:
        try:
            row = _summarize_redraft_trade(
                trade,
                ctx=ctx,
                season=season_int,
                season_scoring=season_scoring,
                baseline_total_points=baseline,
                players_meta=players_meta,
            )
        except Exception:
            logger.exception(
                "Redraft trade summary failed for txn %s; skipping",
                trade.transaction_id,
            )
            continue

        trades_payload.append(row)
        for side in row["sides"]:
            user = side["username"]
            by_user_trades[user] += 1
            by_user_net_vorp[user] += float(side["total_score"])

        # Track biggest margin trade as "biggest_fleecing" analog.
        if row["winner"] and (
            biggest is None
            or row["ktc_edge_per_season"] > biggest["ktc_edge_per_season"]
        ):
            biggest = row

    by_user_payload = {
        user: {
            "num_trades": by_user_trades[user],
            "net_ktc_per_season": round(by_user_net_vorp[user], 1),
        }
        for user in by_user_trades
    }
    most_active = None
    if by_user_trades:
        top_user = max(by_user_trades.items(), key=lambda kv: kv[1])
        most_active = {"username": top_user[0], "num_trades": top_user[1]}

    return {
        "trades": trades_payload,
        "by_user": by_user_payload,
        "biggest_fleecing": biggest,
        "most_active_trader": most_active,
        "scoring_mode": "redraft_vorp",
    }


__all__ = ["build_redraft_trades_section"]
