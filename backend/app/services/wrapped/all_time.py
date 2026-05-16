"""All-time aggregator for the Wrapped feature (``year=all``).

Walks the ``previous_league_id`` chain via
:func:`app.services.sleeper_league_lookup.get_league_season_chain`, calls
:func:`compute_wrapped` per season (each is independently Redis-cached
upstream so repeated all-time hits are cheap once warm), then rolls a
handful of cross-year accolades up to the league level.

Design notes:

* Sleeper ``user_id``\s are stable; ``display_name``\s are not. We bucket
  every per-year stat by ``user_id`` whenever the per-year payload exposes
  ``meta.user_id_to_username`` (added in this same Phase-4 commit). For
  legacy v4 caches that pre-date that field we fall back to keying by the
  display_name itself, prefixed with ``"name:"`` so it can't collide with
  a real user_id. The output always renders the *most recent* display name
  we saw for each bucket (chain is iterated newest-first, first
  occurrence wins).

* All-time accolades are built only from data already present in the
  per-year payload. Adding a new aggregate here does NOT require
  recomputing seasons.

Public surface:
    build_all_time_payload(league_id) -> dict
"""
from __future__ import annotations

import logging
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional, Tuple

from app.services.sleeper_league_lookup import get_league_season_chain
from app.services.wrapped.pipeline import compute_wrapped

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _user_key_and_name(
    payload: Dict[str, Any], username: str
) -> Tuple[str, str]:
    """Return ``(bucket_key, display_name)`` for ``username`` within a single
    season's payload.

    ``bucket_key`` is the Sleeper ``user_id`` when we can resolve it;
    otherwise it's ``f"name:{username}"`` so legacy display-name buckets
    never collide with real user_ids.
    """
    mapping: Dict[str, str] = (
        ((payload.get("meta") or {}).get("user_id_to_username")) or {}
    )
    for uid, name in mapping.items():
        if name == username:
            return uid, username
    return f"name:{username}", username


def _resolved_user_id(key: str) -> Optional[str]:
    """Extract the Sleeper user_id from a bucket key, or ``None`` if the
    bucket is a display-name fallback."""
    return None if key.startswith("name:") else key


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------
def _empty_aggregates() -> Dict[str, Any]:
    return {
        "luckiest": None,
        "unluckiest": None,
        "worst_start_sit": None,
        "most_efficient": None,
        "least_efficient": None,
        "most_active_trader": None,
        "biggest_net_gainer": None,
        "biggest_net_loser": None,
    }


def _aggregate(years: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Roll cross-year accolades. ``years`` is the newest-first list of
    ``{"year": ..., "league_id": ..., "payload": ...}`` entries."""
    # Luck: sum raw lucky-win / unlucky-loss counts across seasons.
    # ``_seasons`` tracks how many seasons each user appears in so the
    # display can read "23 lucky wins (3 seasons)".
    lucky_total: Dict[str, int] = defaultdict(int)
    lucky_seasons: Dict[str, int] = defaultdict(int)
    unlucky_total: Dict[str, int] = defaultdict(int)
    unlucky_seasons: Dict[str, int] = defaultdict(int)
    troll_total: Dict[str, float] = defaultdict(float)
    troll_years: Dict[str, int] = defaultdict(int)
    eff_sum: Dict[str, float] = defaultdict(float)
    eff_n: Dict[str, int] = defaultdict(int)
    trade_count: Dict[str, int] = defaultdict(int)
    trade_net: Dict[str, float] = defaultdict(float)

    # Most-recent display name wins. Chain is newest-first, so we keep the
    # *first* name we observe per bucket.
    display_for_key: Dict[str, str] = {}

    def _remember(key: str, name: str) -> None:
        display_for_key.setdefault(key, name)

    for entry in years:
        payload = entry.get("payload") or {}

        # --- Luck: sum raw per-user counts (new shape) with graceful
        #     fallback to crown-counting for legacy cached payloads ----
        luck = (payload.get("schedule") or {}).get("luck") or {}
        luck_by_user = luck.get("by_user") or {}
        if luck_by_user:
            for uname, stats in luck_by_user.items():
                key, name = _user_key_and_name(payload, uname)
                lw = int((stats or {}).get("lucky_wins") or 0)
                ul = int((stats or {}).get("unlucky_losses") or 0)
                if lw > 0:
                    lucky_total[key] += lw
                    lucky_seasons[key] += 1
                if ul > 0:
                    unlucky_total[key] += ul
                    unlucky_seasons[key] += 1
                if lw > 0 or ul > 0:
                    _remember(key, name)
        else:
            # Legacy payload: only the crown winners are exposed. Fall
            # back to attributing the winner's full count to them.
            for slot, total_dest, seasons_dest in (
                ("luckiest", lucky_total, lucky_seasons),
                ("unluckiest", unlucky_total, unlucky_seasons),
            ):
                winner = luck.get(slot) or {}
                uname = winner.get("username")
                count = int(winner.get("count") or 0)
                if uname and count > 0:
                    key, name = _user_key_and_name(payload, uname)
                    total_dest[key] += count
                    seasons_dest[key] += 1
                    _remember(key, name)

        # --- Manager efficiency (per-user pct, avg over years) --------
        eff_by_user = (
            (payload.get("schedule") or {}).get("manager_efficiency") or {}
        ).get("by_user") or {}
        for uname, pct in eff_by_user.items():
            try:
                pct_f = float(pct)
            except (TypeError, ValueError):
                continue
            key, name = _user_key_and_name(payload, uname)
            eff_sum[key] += pct_f
            eff_n[key] += 1
            _remember(key, name)

        # --- Worst start/sit (sum of positive troll values) ----------
        troll_by = (payload.get("roster_moves") or {}).get("troll") or {}
        for uname, troll_entry in troll_by.items():
            if not troll_entry:
                continue
            tv = troll_entry.get("troll_value") or 0
            try:
                tv_f = float(tv)
            except (TypeError, ValueError):
                continue
            if tv_f <= 0:
                continue
            key, name = _user_key_and_name(payload, uname)
            troll_total[key] += tv_f
            troll_years[key] += 1
            _remember(key, name)

        # --- Trades (counts + net value) -----------------------------
        trades_by = (payload.get("trades") or {}).get("by_user") or {}
        for uname, t in trades_by.items():
            key, name = _user_key_and_name(payload, uname)
            try:
                trade_count[key] += int(t.get("num_trades") or 0)
            except (TypeError, ValueError):
                pass
            try:
                # Per-year aggregators emit ``net_ktc_per_season`` (dynasty:
                # trade_accolades.py; redraft: redraft_trades_section.py).
                # Read with a fallback to the historical (incorrect) name
                # in case a legacy cached payload is still around.
                net = t.get("net_ktc_per_season")
                if net is None:
                    net = t.get("net_value_gained")
                trade_net[key] += float(net or 0.0)
            except (TypeError, ValueError):
                pass
            _remember(key, name)

    out: Dict[str, Any] = _empty_aggregates()

    def _sum_winner(
        totals: Dict[str, int],
        seasons: Dict[str, int],
        total_field: str,
    ) -> Optional[Dict[str, Any]]:
        """Pick the user with the highest summed raw count. Returns
        ``{username, user_id, <total_field>, seasons}`` or ``None``."""
        if not totals:
            return None
        max_total = max(totals.values())
        if max_total <= 0:
            return None
        candidates = sorted(
            (k for k, v in totals.items() if v == max_total),
            key=lambda k: display_for_key.get(k, k),
        )
        winner = candidates[0]
        return {
            "username": display_for_key.get(winner, winner),
            "user_id": _resolved_user_id(winner),
            total_field: int(totals[winner]),
            "seasons": int(seasons.get(winner, 0)),
        }

    out["luckiest"] = _sum_winner(lucky_total, lucky_seasons, "lucky_wins")
    out["unluckiest"] = _sum_winner(unlucky_total, unlucky_seasons, "unlucky_losses")

    if troll_total:
        max_total = max(troll_total.values())
        winner = sorted(
            (k for k, v in troll_total.items() if v == max_total),
            key=lambda k: display_for_key.get(k, k),
        )[0]
        out["worst_start_sit"] = {
            "username": display_for_key.get(winner, winner),
            "user_id": _resolved_user_id(winner),
            "total_troll_value": round(troll_total[winner], 2),
            "years_counted": troll_years[winner],
        }

    if eff_sum:
        avg = {k: eff_sum[k] / eff_n[k] for k in eff_sum if eff_n[k] > 0}
        if avg:
            best = max(avg, key=lambda k: avg[k])
            worst = min(avg, key=lambda k: avg[k])
            out["most_efficient"] = {
                "username": display_for_key.get(best, best),
                "user_id": _resolved_user_id(best),
                "avg_efficiency_pct": round(avg[best], 2),
                "years_counted": eff_n[best],
            }
            out["least_efficient"] = {
                "username": display_for_key.get(worst, worst),
                "user_id": _resolved_user_id(worst),
                "avg_efficiency_pct": round(avg[worst], 2),
                "years_counted": eff_n[worst],
            }

    if trade_count:
        winner = max(trade_count, key=lambda k: trade_count[k])
        if trade_count[winner] > 0:
            out["most_active_trader"] = {
                "username": display_for_key.get(winner, winner),
                "user_id": _resolved_user_id(winner),
                "total_trades": int(trade_count[winner]),
            }

    if trade_net:
        gain_key = max(trade_net, key=lambda k: trade_net[k])
        loss_key = min(trade_net, key=lambda k: trade_net[k])
        if trade_net[gain_key] > 0:
            out["biggest_net_gainer"] = {
                "username": display_for_key.get(gain_key, gain_key),
                "user_id": _resolved_user_id(gain_key),
                "net_ktc_per_season": round(trade_net[gain_key], 1),
            }
        if trade_net[loss_key] < 0:
            out["biggest_net_loser"] = {
                "username": display_for_key.get(loss_key, loss_key),
                "user_id": _resolved_user_id(loss_key),
                "net_ktc_per_season": round(trade_net[loss_key], 1),
            }

    return out


# ---------------------------------------------------------------------------
# Public entry
# ---------------------------------------------------------------------------
def build_all_time_payload(league_id: str) -> Dict[str, Any]:
    """Build the all-time Wrapped payload for a league.

    Returns ``{"mode": "all_time", "all_time": {...}, "years": [...]}``.
    Years are ordered newest-first to match
    :func:`get_league_season_chain` and the per-year dropdown UX. Each
    entry's ``payload`` is the full :func:`compute_wrapped` output for
    that season — the frontend can re-render the existing per-year
    sections without a second backend call.

    A degenerate single-season chain still produces a valid all-time
    payload; the aggregates simply collapse to that one year's numbers.
    """
    chain = get_league_season_chain(str(league_id))
    if not chain:
        return {"mode": "all_time", "all_time": _empty_aggregates(), "years": []}

    # Per-year fetches are independently Redis-cached upstream, but cold
    # paths still pay the full IO cost (Sleeper transactions, weekly
    # scores, KTC blob load). Fan out in a thread pool so an 8-season
    # dynasty cold-load isn't 8 × 8s serial = 60s+.
    tasks: List[Tuple[str, str]] = []
    for entry in chain:
        season = str(entry.get("season"))
        lid = str(entry.get("league_id"))
        if not season or not lid:
            continue
        tasks.append((season, lid))

    if not tasks:
        return {"mode": "all_time", "all_time": _empty_aggregates(), "years": []}

    # Cap workers to avoid hammering Sleeper / blob storage on huge
    # chains. 8 is plenty for typical 6-10 season dynasty histories.
    max_workers = min(8, len(tasks))
    results: Dict[str, Dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        future_to_key = {
            ex.submit(compute_wrapped, lid, season): (season, lid)
            for season, lid in tasks
        }
        for fut in as_completed(future_to_key):
            season, lid = future_to_key[fut]
            try:
                results[season] = fut.result()
            except Exception as e:
                # One bad season shouldn't kill the all-time view.
                logger.warning(
                    "all_time: compute_wrapped failed for %s/%s: %s",
                    lid, season, e,
                )

    # Re-order back to chain order (newest-first) so the UI dropdown
    # and aggregator both see seasons in the canonical sequence.
    years_payloads: List[Dict[str, Any]] = []
    for season, lid in tasks:
        payload = results.get(season)
        if payload is None:
            continue
        years_payloads.append(
            {"year": season, "league_id": lid, "payload": payload}
        )

    return {
        "mode": "all_time",
        "all_time": _aggregate(years_payloads),
        "years": years_payloads,
    }
