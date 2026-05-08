"""Trade accolades for the Wrapped pipeline.

Each trade has 2+ sides; we sum FantasyCalc trade values per side, identify
a winner, and roll up into:

* Per-trade summary (week, sides + values + winner)
* Overall: ``biggest_fleecing`` (largest single-trade value gap)
* Per-user net trade value gained across all trades
* ``most_active_trader`` (highest trade count)

Draft picks included in trades use a flat replacement value per round
because FantasyCalc's pick-value endpoint requires extra parameters and
the v1 frontend doesn't visualize picks separately yet.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any, Dict, List, Optional

from app.services.wrapped.transactions import LeagueTransactions, Trade, TradeSide

logger = logging.getLogger(__name__)


# Rough redraft pick values — order-of-magnitude smaller than top-tier
# player values (#1 overall ≈ 10000) so a player-for-player trade dwarfs
# a single mid-round pick swap.
_PICK_VALUE_BY_ROUND: Dict[int, float] = {
    1: 4500.0,
    2: 1800.0,
    3: 800.0,
    4: 400.0,
    5: 200.0,
}


def _value_for_pick(pick: Dict[str, Any]) -> float:
    rnd = pick.get("round") or 0
    return _PICK_VALUE_BY_ROUND.get(int(rnd), 100.0)


def _value_side(
    side: TradeSide,
    player_values: Dict[str, float],
    player_meta: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    """Sum a side's haul. Returns a dict with player breakdown so the
    frontend can show the names that drove the valuation."""
    players_payload: List[Dict[str, Any]] = []
    total = 0.0
    for pid in side.received_player_ids:
        v = float(player_values.get(pid, 0.0))
        total += v
        meta = player_meta.get(pid) or {}
        players_payload.append(
            {
                "player_id": pid,
                "name": meta.get("full_name") or pid,
                "value": round(v, 1),
            }
        )
    picks_payload: List[Dict[str, Any]] = []
    for pick in side.received_picks:
        v = _value_for_pick(pick)
        total += v
        picks_payload.append(
            {
                "season": pick.get("season"),
                "round": pick.get("round"),
                "value": round(v, 1),
            }
        )
    return {
        "username": side.username,
        "players": players_payload,
        "picks": picks_payload,
        "total_value": round(total, 1),
    }


def _summarize_trade(
    trade: Trade,
    player_values: Dict[str, float],
    player_meta: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    """Render one trade with per-side values + winner."""
    sides_payload = [
        _value_side(side, player_values, player_meta) for side in trade.sides.values()
    ]
    sides_payload.sort(key=lambda s: s["total_value"], reverse=True)
    winner = sides_payload[0]["username"] if sides_payload else None
    if len(sides_payload) >= 2:
        gap = sides_payload[0]["total_value"] - sides_payload[1]["total_value"]
    else:
        gap = 0.0
    return {
        "week": trade.week,
        "transaction_id": trade.transaction_id,
        "sides": sides_payload,
        "winner": winner,
        "value_gap": round(gap, 1),
    }


def calculate_trade_accolades(
    transactions: LeagueTransactions,
    player_values: Dict[str, float],
    player_meta: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    """Build the full trades section payload.

    Returns a dict with ``trades`` list + per-user net value + overall
    leaders. Empty section (just ``trades: []``) when the league had no
    completed trades or FantasyCalc data is unavailable.
    """
    if not transactions.trades:
        return {
            "trades": [],
            "by_user": {},
            "biggest_fleecing": None,
            "most_active_trader": None,
        }

    summaries = [
        _summarize_trade(t, player_values, player_meta) for t in transactions.trades
    ]

    # Per-user roll-up: trade count + net value gained vs the average of
    # the other sides in each trade. Net = side_value - avg(other_sides).
    by_user_net: Dict[str, float] = defaultdict(float)
    by_user_count: Dict[str, int] = defaultdict(int)
    for summary in summaries:
        sides = summary["sides"]
        if not sides:
            continue
        total_val = sum(s["total_value"] for s in sides)
        for s in sides:
            others_avg = (total_val - s["total_value"]) / max(len(sides) - 1, 1)
            by_user_net[s["username"]] += s["total_value"] - others_avg
            by_user_count[s["username"]] += 1

    by_user = {
        user: {
            "num_trades": by_user_count[user],
            "net_value_gained": round(by_user_net[user], 1),
        }
        for user in by_user_count
    }

    biggest_fleecing: Optional[Dict[str, Any]] = None
    if summaries:
        biggest_fleecing = max(summaries, key=lambda s: s["value_gap"])
        # Don't crown a "fleecing" if the gap is basically nothing — most
        # leagues do at least one even swap and we shouldn't flag those.
        if biggest_fleecing["value_gap"] < 1.0:
            biggest_fleecing = None

    most_active_trader: Optional[Dict[str, Any]] = None
    if by_user_count:
        user, count = max(by_user_count.items(), key=lambda kv: kv[1])
        most_active_trader = {"username": user, "num_trades": count}

    return {
        "trades": summaries,
        "by_user": by_user,
        "biggest_fleecing": biggest_fleecing,
        "most_active_trader": most_active_trader,
    }
