"""KTC value-integral trade accolades.

Dynasty-only. Replaces the older FantasyCalc-static-value summary with a
KTC value-time integral evaluation, so the winner is decided by "who
held more value over the rest of the lookback window" instead of "who
got the higher FantasyCalc number at the moment of the trade." That is
far more honest for trades that look one way at the deadline and a
different way a year later -- the classic Josh-Allen-for-veterans case.

Per trade we produce:

* ``ktc_edge_per_season`` -- the headline KTC-equivalent edge for the
  winner, as a per-active-season rate (e.g. ``+3,127``).
* ``ktc_edge_total`` -- the same edge scaled over the trade's full
  active-day window.
* ``winner`` -- the username of the winning side, or ``None`` for ties.
* Per-side ``ktc_equiv`` + per-asset ``avg_ktc`` for the inspector UI.

Rolled up across the league:

* ``biggest_fleecing`` -- trade with the largest ``ktc_edge_per_season``,
  with a small floor (>= 50 KTC/yr) to avoid crowning a coin-flip.
* ``by_user`` -- per-user trade count + net ktc_edge_per_season gained
  across all their trades.
* ``most_active_trader`` -- highest trade count.

The historical KTC value blob is loaded once per process via
:mod:`app.services.wrapped.ktc_blob_loader`. Trades referencing players
or picks the blob does not recognise treat that asset as worth zero --
it still appears in the per-side breakdown by name, just without
contributing to the integral.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Mapping, Optional

from app.services.trade_eval.pick_handoff import encode_pick_key, pick_blob_id
from app.services.trade_eval.trade_evaluator import (
    Trade as IntegralTrade,
    TradeAsset,
    TradeSide as IntegralSide,
    build_race_chart,
    evaluate_trade,
    make_blob_resolver,
)
from app.services.trade_eval.pick_handoff import make_pick_aware_resolver
from app.services.trade_eval.value_integral import CONCAVITY_EXPONENT
from app.services.http_utils import fetch_json
from datetime import timedelta
from typing import Tuple
from app.services.wrapped.ktc_blob_loader import get_flat_blob
from app.services.wrapped.transactions import LeagueTransactions, Trade

logger = logging.getLogger(__name__)

# Floor below which we don't crown a "biggest fleecing" -- under ~50
# KTC/yr the integral can't really distinguish luck from skill at our
# sampling resolution, so we treat near-even trades as ties for the
# accolade.
_FLEECING_FLOOR: float = 50.0


# ---------------------------------------------------------------------------
# Trade-date inference
# ---------------------------------------------------------------------------
def _trade_date_from_status(
    status_updated_ms: Optional[int], season: int, week: int,
) -> date:
    """Best-effort calendar date for the trade.

    Prefers Sleeper's ``status_updated`` (epoch-ms, accurate). Falls
    back to ``Sep 1 + 7*(week-1)`` of ``season`` so we still produce a
    sensible window for trades whose Sleeper transaction predates the
    ``status_updated`` field.
    """
    if status_updated_ms:
        try:
            return datetime.fromtimestamp(
                status_updated_ms / 1000.0, tz=timezone.utc,
            ).date()
        except (OSError, OverflowError, ValueError):
            pass
    # Sleeper weeks are 1-indexed and NFL Week 1 is roughly the first
    # week of September. Good enough for the integral window anchor.
    day = min(1 + 7 * max(week - 1, 0), 28)
    return date(int(season), 9, day)


# ---------------------------------------------------------------------------
# Asset construction
# ---------------------------------------------------------------------------
def _build_asset_for_player(
    sleeper_id: str,
    blob_meta: Mapping[str, Mapping[str, Any]],
    players_meta: Mapping[str, Mapping[str, Any]],
) -> TradeAsset:
    """Build a TradeAsset for a Sleeper player, labeled from whichever
    metadata source has a name."""
    label_from_blob = (blob_meta.get(sleeper_id) or {}).get("name")
    label_from_sleeper = (players_meta.get(sleeper_id) or {}).get("full_name")
    label = label_from_blob or label_from_sleeper or sleeper_id
    return TradeAsset(
        asset_id=str(sleeper_id),
        label=label,
        sleeper_id=str(sleeper_id),
        is_pick=False,
    )


def _build_asset_for_pick(pick: Mapping[str, Any]) -> TradeAsset:
    """Build a TradeAsset for a draft pick.

    The blob keys picks by ``pick:YYYY_tier_round`` (tier inferred from
    the original owner's projected draft slot). At trade time we only
    know the round + season, so we use the "mid" tier as the default --
    same convention the trade_eval pick handoff module uses when no
    slot is supplied. Picks for seasons not in the blob will evaluate
    as zero value, which is fine: the per-asset line still shows the
    pick by name in the UI.
    """
    season = str(pick.get("season") or "")
    rnd = int(pick.get("round") or 0)
    if not season or not rnd:
        return TradeAsset(
            asset_id=f"pick:unknown_{season}_{rnd}",
            label=f"{season or '?'} R{rnd or '?'} pick",
            sleeper_id=None, is_pick=True,
        )
    blob_id = pick_blob_id(season, rnd, slot=None)  # tier="mid"
    label = f"{season} R{rnd} pick"
    return TradeAsset(
        asset_id=blob_id,
        label=label,
        sleeper_id=encode_pick_key(season, rnd, pick.get("original_roster_id") or 0),
        is_pick=True,
    )


def _to_integral_trade(
    trade: Trade,
    *,
    season: int,
    evaluation_end: date,
    blob_meta: Mapping[str, Mapping[str, Any]],
    players_meta: Mapping[str, Mapping[str, Any]],
) -> IntegralTrade:
    """Convert a wrapped-pipeline Trade into the evaluator's Trade."""
    trade_date = _trade_date_from_status(
        trade.status_updated_ms, season=season, week=trade.week,
    )
    sides: List[IntegralSide] = []
    for username, side in trade.sides.items():
        assets: List[TradeAsset] = []
        for pid in side.received_player_ids:
            assets.append(_build_asset_for_player(pid, blob_meta, players_meta))
        for pick in side.received_picks:
            assets.append(_build_asset_for_pick(pick))
        sides.append(IntegralSide(team_label=username, received_assets=assets))
    return IntegralTrade(
        trade_date=trade_date,
        evaluation_end=evaluation_end,
        sides=sides,
    )


# ---------------------------------------------------------------------------
# Pick handoff: realize picks into the players they actually became
# ---------------------------------------------------------------------------
def _build_pick_handoff_table(
    starting_league_id: str,
) -> Dict[Tuple[str, int, int], Dict[str, Any]]:
    """Walk the dynasty chain and build ``(season, round, orig_rid) -> info``.

    ``info`` carries ``player_id`` + ``draft_date`` so that
    :func:`make_pick_aware_resolver` can splice the drafted player's KTC
    line in on draft day. Picks whose drafts haven't happened yet (or
    that lie in seasons we couldn't fetch) are simply absent -- the
    resolver falls back to the plain pick KTC series for those.

    All failures are swallowed and produce an empty table: pick handoff
    is purely a value-fidelity improvement; if Sleeper is unreachable
    or the chain is malformed we still want the rest of the wrapped
    page to render, just with picks valued as raw picks.
    """
    try:
        # Local import to avoid pulling the chain loader into every
        # trade-accolades module load (it's only needed when we have a
        # live league_id, which tests don't supply).
        from app.services.trade_eval.sleeper_trade_loader import (
            build_pick_to_player,
            load_league_chain,
        )

        # ``weeks_to_scan=range(0, 1)`` keeps the chain walk cheap: we
        # only need league + draft + draft_picks per season, not the
        # full trade history (which the trade evaluator already has via
        # Sleeper transactions). One transactions call per season is
        # negligible.
        chain = load_league_chain(
            starting_league_id,
            http=fetch_json,
            weeks_to_scan=range(0, 1),
        )
        return build_pick_to_player(chain)
    except Exception as exc:  # noqa: BLE001 -- best-effort enrichment
        logger.warning(
            "Pick handoff table build failed for league %s (%s); "
            "picks will evaluate as raw pick values.",
            starting_league_id, exc,
        )
        return {}


# ---------------------------------------------------------------------------
# Per-trade summary
# ---------------------------------------------------------------------------
def _summarize_trade(
    trade: Trade,
    *,
    season: int,
    evaluation_end: date,
    resolver,
    blob_meta: Mapping[str, Mapping[str, Any]],
    players_meta: Mapping[str, Mapping[str, Any]],
    k: float,
) -> Dict[str, Any]:
    """Render one trade with per-side KTC-integral results + winner."""
    integral_trade = _to_integral_trade(
        trade, season=season, evaluation_end=evaluation_end,
        blob_meta=blob_meta, players_meta=players_meta,
    )
    result = evaluate_trade(integral_trade, value_resolver=resolver, k=k)

    sides_payload: List[Dict[str, Any]] = []
    for side_eval in result.sides:
        assets_payload: List[Dict[str, Any]] = []
        for ae in side_eval.asset_evaluations:
            assets_payload.append({
                "asset_id": ae.asset.asset_id,
                "label": ae.asset.label,
                "sleeper_id": ae.asset.sleeper_id,
                "is_pick": ae.asset.is_pick,
                # raw_area / active_days, i.e. constant-equivalent
                # plain-KTC average -- intuitive for per-row display.
                "avg_ktc": round(ae.avg_ktc, 0),
                "active_days": ae.integral.active_days,
                "score": round(ae.total_score, 1),
            })
        sides_payload.append({
            "username": side_eval.team_label,
            "assets": assets_payload,
            "total_score": round(side_eval.total_score, 1),
            "ktc_equiv": round(side_eval.ktc_equiv, 0),
        })

    sides_payload.sort(key=lambda s: s["total_score"], reverse=True)
    return {
        "week": trade.week,
        "transaction_id": trade.transaction_id,
        "trade_date": integral_trade.trade_date.isoformat(),
        "evaluation_end": integral_trade.evaluation_end.isoformat(),
        "sides": sides_payload,
        "winner": result.winner_label,
        "k": result.k,
        "active_days": result.active_days,
        "ktc_edge_per_season": round(result.ktc_edge_per_season, 0),
        "ktc_edge_total": round(result.ktc_edge_total, 0),
    }


# ---------------------------------------------------------------------------
# Top-level entry point
# ---------------------------------------------------------------------------
def calculate_trade_accolades(
    transactions: LeagueTransactions,
    *,
    season: int,
    evaluation_end: Optional[date] = None,
    num_qbs: str = "1",
    players_meta: Optional[Mapping[str, Mapping[str, Any]]] = None,
    k: float = CONCAVITY_EXPONENT,
    league_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Build the trades-section payload using the KTC integral evaluator.

    Parameters
    ----------
    transactions:
        Output of the wrapped pipeline's transactions fetcher.
    season:
        Wrapped-page year. Used to derive a fallback trade_date for
        trades that have no Sleeper ``status_updated``.
    evaluation_end:
        Stop the integral on this date. Defaults to "today" so every
        trade in the league is evaluated up to the present, regardless
        of which season it happened in -- the lookback semantic the
        dynasty trade ledger wants.
    num_qbs:
        ``"1"`` or ``"2"``; selects which KTC history series to use.
    players_meta:
        Sleeper player meta dict so we can label assets even when the
        blob has never seen them. Optional but recommended.
    k:
        Concavity exponent. Defaults to :data:`CONCAVITY_EXPONENT`.

    Returns
    -------
    dict
        ``{trades, by_user, biggest_fleecing, most_active_trader, k,
        evaluation_end}``. Empty section when the league has no trades
        or the blob is unavailable.
    """
    if not transactions.trades:
        return _empty_section(k=k, evaluation_end=evaluation_end)

    fmt = "superflex" if str(num_qbs).strip() == "2" else "1qb"

    try:
        flat, blob_meta = get_flat_blob(fmt)
    except Exception as exc:  # noqa: BLE001 -- the loader can fail in many ways
        logger.warning(
            "KTC historical blob unavailable (%s); skipping trade evaluation.",
            exc,
        )
        return _empty_section(k=k, evaluation_end=evaluation_end)

    resolver = make_blob_resolver(flat)
    # Pick handoff: if we know the starting league_id we can splice
    # drafted-player KTC lines in on draft day, so a "first-round pick"
    # in a Feb 2024 trade evaluates as Marvin Harrison Jr. from April
    # 2024 forward, not as a generic R1 pick. Tests don't supply
    # league_id so this is a no-op there.
    if league_id:
        pick_table = _build_pick_handoff_table(league_id)
        if pick_table:
            resolver = make_pick_aware_resolver(resolver, pick_table)
    eval_end = evaluation_end or datetime.now(timezone.utc).date()
    players_meta = players_meta or {}

    summaries = [
        _summarize_trade(
            t, season=season, evaluation_end=eval_end,
            resolver=resolver, blob_meta=blob_meta,
            players_meta=players_meta, k=k,
        )
        for t in transactions.trades
    ]

    # Per-user rollup. Net "KTC/yr gained" = sum of edges where you're
    # the winner, minus the share of edges where you're a loser. For
    # 2-sided trades that's just a signed delta per trade.
    by_user_net: Dict[str, float] = defaultdict(float)
    by_user_count: Dict[str, int] = defaultdict(int)
    for summ in summaries:
        winner = summ["winner"]
        edge = float(summ["ktc_edge_per_season"])
        sides = summ["sides"]
        for s in sides:
            by_user_count[s["username"]] += 1
        if winner is None:
            continue
        losers = [side["username"] for side in sides if side["username"] != winner]
        if not losers:
            continue
        by_user_net[winner] += edge
        loss_each = edge / len(losers)
        for loser in losers:
            by_user_net[loser] -= loss_each

    by_user = {
        user: {
            "num_trades": by_user_count[user],
            "net_ktc_per_season": round(by_user_net[user], 0),
        }
        for user in by_user_count
    }

    biggest_fleecing: Optional[Dict[str, Any]] = None
    if summaries:
        candidate = max(summaries, key=lambda s: s["ktc_edge_per_season"])
        if candidate["ktc_edge_per_season"] >= _FLEECING_FLOOR:
            biggest_fleecing = candidate

    most_active_trader: Optional[Dict[str, Any]] = None
    if by_user_count:
        user, count = max(by_user_count.items(), key=lambda kv: kv[1])
        most_active_trader = {"username": user, "num_trades": count}

    return {
        "trades": summaries,
        "by_user": by_user,
        "biggest_fleecing": biggest_fleecing,
        "most_active_trader": most_active_trader,
        "k": k,
        "evaluation_end": eval_end.isoformat(),
    }


def _empty_section(*, k: float, evaluation_end: Optional[date]) -> Dict[str, Any]:
    """Standard empty-section shape so callers can rely on a consistent
    set of keys."""
    end = (evaluation_end or datetime.now(timezone.utc).date()).isoformat()
    return {
        "trades": [],
        "by_user": {},
        "biggest_fleecing": None,
        "most_active_trader": None,
        "k": k,
        "evaluation_end": end,
    }


__all__ = ["calculate_trade_accolades", "inspect_trade"]


# ---------------------------------------------------------------------------
# Single-trade inspector
# ---------------------------------------------------------------------------
# Sampling cadence (in days) for the per-asset raw-KTC sparkline series.
# Matches the race chart's default step so the front-end can stack both
# charts on a common x-axis without aligning timestamps server-side.
_INSPECT_STEP_DAYS: int = 7


def _per_asset_series(
    integral_trade: IntegralTrade,
    *,
    resolver,
    step_days: int = _INSPECT_STEP_DAYS,
) -> List[Dict[str, Any]]:
    """Sample each asset's raw KTC value across the trade's holding window.

    Returned shape (one entry per asset, grouped under its owning side):
    ``{team_label, asset_id, label, points: [{date, value}, ...]}``.
    Points are evenly spaced at ``step_days`` plus the closing
    ``evaluation_end`` so the line terminates on the verdict date.
    """
    start = integral_trade.trade_date
    end = integral_trade.evaluation_end
    if end < start:
        end = start

    # Build the timeline once -- every asset shares it since holding
    # windows are pinned to trade_date->evaluation_end in this view
    # (the inspector intentionally ignores ``held_until`` rerouting so
    # the user can see the raw "what was this player worth" curve).
    timeline: List[date] = []
    d = start
    while d <= end:
        timeline.append(d)
        d += timedelta(days=step_days)
    if not timeline or timeline[-1] != end:
        timeline.append(end)

    out: List[Dict[str, Any]] = []
    for side in integral_trade.sides:
        for asset in side.received_assets:
            series = resolver(asset)
            points = [
                {"date": pt.isoformat(), "value": round(series.value_on(pt), 0)}
                for pt in timeline
            ]
            out.append({
                "team_label": side.team_label,
                "asset_id": asset.asset_id,
                "label": asset.label,
                "is_pick": asset.is_pick,
                "sleeper_id": asset.sleeper_id,
                "points": points,
            })
    return out


def inspect_trade(
    trade: Trade,
    *,
    season: int,
    evaluation_end: Optional[date] = None,
    num_qbs: str = "1",
    players_meta: Optional[Mapping[str, Mapping[str, Any]]] = None,
    k: float = CONCAVITY_EXPONENT,
    step_days: int = _INSPECT_STEP_DAYS,
    league_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Return the full inspector payload for one trade.

    Combines :func:`_summarize_trade`'s verdict + per-side breakdown
    with the cumulative race chart (built via
    :func:`trade_eval.trade_evaluator.build_race_chart`) and a per-asset
    raw-KTC sparkline series. The front-end TradeInspector renders all
    three together.

    Returns
    -------
    dict
        ``{trade, race_chart, per_asset_series, k, evaluation_end}``.
        ``trade`` matches the shape produced by
        :func:`calculate_trade_accolades` per-trade so the front-end can
        share the same row component.

    Raises
    ------
    RuntimeError
        If the KTC historical blob cannot be loaded. The caller (HTTP
        route) should translate this into a 503 -- the inspector is
        useless without value data.
    """
    fmt = "superflex" if str(num_qbs).strip() == "2" else "1qb"
    flat, blob_meta = get_flat_blob(fmt)  # may raise; caller handles
    resolver = make_blob_resolver(flat)
    # Same pick handoff wrapping as the bulk endpoint -- the inspector
    # must agree with the trade ledger or the inline chart will tell a
    # different story than the verdict above it.
    if league_id:
        pick_table = _build_pick_handoff_table(league_id)
        if pick_table:
            resolver = make_pick_aware_resolver(resolver, pick_table)
    eval_end = evaluation_end or datetime.now(timezone.utc).date()
    players_meta = players_meta or {}

    # Verdict + per-side numbers -- this is the same payload row the
    # main trades section already exposes, so the front-end can render
    # the headline identically.
    summary = _summarize_trade(
        trade, season=season, evaluation_end=eval_end,
        resolver=resolver, blob_meta=blob_meta,
        players_meta=players_meta, k=k,
    )

    # Race chart -- same evaluator the verdict came from, so any visible
    # crossover on the chart corresponds 1:1 to a verdict flip.
    integral_trade = _to_integral_trade(
        trade, season=season, evaluation_end=eval_end,
        blob_meta=blob_meta, players_meta=players_meta,
    )
    race = build_race_chart(
        integral_trade, value_resolver=resolver, k=k, step_days=step_days,
    )

    per_asset = _per_asset_series(
        integral_trade, resolver=resolver, step_days=step_days,
    )

    return {
        "trade": summary,
        "race_chart": race.to_dict(),
        "per_asset_series": per_asset,
        "k": k,
        "evaluation_end": eval_end.isoformat(),
    }
