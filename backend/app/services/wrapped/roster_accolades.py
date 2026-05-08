"""Roster-move accolades: troll metric + early-pickup / late-drop / best-add /
worst-drop.

All functions are pure over their inputs so they can be unit-tested without
spinning up the live Sleeper API. The pipeline layer (``pipeline.py``) is
responsible for assembling the right inputs from ``WeeklyScores``,
``LeagueTransactions``, the ownership-history blob, and the season-scoring
blob.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, List, Optional, Tuple

from app.services.wrapped.schedule import WeeklyScores
from app.services.wrapped.transactions import LeagueTransactions


# Bench-vs-start min thresholds. Original analyzer used 2 starts which
# surfaced too many small-sample noise picks. Bumped per design discussion.
_MIN_TROLL_STARTS = 4
_MIN_TROLL_BENCHES = 1

# Weeks 1-6 count as an "early" pickup. Anything later we treat as
# normal mid-season churn.
_EARLY_WEEK_MAX = 6


def calculate_troll_metric(
    scores: WeeklyScores,
    players_meta: Dict[str, Dict[str, Any]],
    min_starts: int = _MIN_TROLL_STARTS,
    min_benches: int = _MIN_TROLL_BENCHES,
) -> Dict[str, Optional[Dict[str, Any]]]:
    """For each user, find the player with the largest
    ``bench_avg - start_avg`` gap.

    A high troll value means "this manager kept benching this guy who put
    up points on the bench, then started him on his lower-scoring days".
    """
    out: Dict[str, Optional[Dict[str, Any]]] = {}
    for username, by_player in scores.user_player_start_sit_points.items():
        best_value = -1e9
        best_entry: Optional[Dict[str, Any]] = None
        for pid, sp in by_player.items():
            starts = sp.get("start") or []
            benches = sp.get("bench") or []
            if len(starts) < min_starts or len(benches) < min_benches:
                continue
            start_avg = sum(starts) / len(starts)
            bench_avg = sum(benches) / len(benches)
            troll_value = bench_avg - start_avg
            if troll_value > best_value:
                best_value = troll_value
                pmeta = players_meta.get(pid) or {}
                best_entry = {
                    "player_id": pid,
                    "name": pmeta.get("full_name") or pid,
                    "num_start": len(starts),
                    "num_bench": len(benches),
                    "start_avg": round(start_avg, 2),
                    "bench_avg": round(bench_avg, 2),
                    "troll_value": round(troll_value, 2),
                }
        # Only emit users with a qualifying troll candidate; skipping noisy
        # ``None`` entries keeps the frontend list lean.
        if best_entry is not None and best_entry["troll_value"] > 0:
            out[username] = best_entry
        else:
            out[username] = None
    return out


def _value_over_baseline(
    pid: str,
    season_scoring: Dict[str, Dict],
    qb_score_key: str,
    skill_score_key: str,
    baseline: Dict[str, float],
) -> Optional[Tuple[str, str, float]]:
    """Return ``(name, position, value_over_baseline)`` for ``pid``, or None
    if we can't score them. Skips K/DEF since baselines aren't meaningful."""
    info = season_scoring.get(pid) or {}
    positions = info.get("fantasy_positions") or []
    if not positions:
        return None
    full_name = (info.get("full_name") or "").strip()
    pos = positions[0] if full_name != "Taysom Hill" else "TE"
    if pos in ("DEF", "K"):
        return None
    if pos not in baseline:
        return None
    points_key = (
        f"{qb_score_key}_points" if pos == "QB" else f"{skill_score_key}_points"
    )
    season = info.get("scoring_data_season") or {}
    points = season.get(points_key)
    if points is None:
        return None
    try:
        return full_name or pid, pos, float(points) - float(baseline[pos])
    except (TypeError, ValueError):
        return None


def _ownership_pct_for_week(
    pid: str,
    week: int,
    ownership_history: Dict[str, Dict[str, Dict[str, float]]],
) -> Optional[float]:
    """Look up Sleeper-reported ownership % for this pid in this week.

    Returns ``None`` if the data isn't available — callers treat that as
    "unknown" and skip the candidate rather than guess.
    """
    by_week = ownership_history.get(pid)
    if not by_week:
        return None
    val = by_week.get(str(week))
    if val is None:
        return None
    try:
        return float(val.get("owned"))
    except (TypeError, ValueError):
        return None


def calculate_roster_accolades(
    ctx_current_rosters: Dict[str, List[str]],
    transactions: LeagueTransactions,
    ownership_history: Dict[str, Dict[str, Dict[str, float]]],
    season_scoring: Dict[str, Dict],
    qb_score_key: str,
    skill_score_key: str,
    baseline: Dict[str, float],
    current_week: int,
    significantly_owned_threshold: float = 65.0,
) -> Dict[str, Dict[str, Any]]:
    """Return ``{username: {early_pickup, late_drop, best_add, worst_drop}}``.

    See module docstring for the criteria. All four sub-fields are optional:
    a user with no qualifying drops will get ``worst_drop = {}``.
    """
    out: Dict[str, Dict[str, Any]] = {}
    last_added_by = transactions.last_added_by
    player_tx = transactions.player_transactions

    # Pre-compute helper lookups.
    last_drop_by_user: Dict[str, List[Tuple[str, int]]] = defaultdict(list)
    drops_by_user: Dict[str, List[str]] = defaultdict(list)
    for pid, events in player_tx.items():
        if not events:
            continue
        last_event = events[-1]
        if last_event[0] == "Drop":
            # last_drop_by_user records (pid, week) for the user who
            # most recently let go of this player.
            last_drop_by_user[last_event[2]].append((pid, last_event[1]))
        # drops_by_user collects every Drop by this user — even if they
        # later re-added the player. The "did not end up with" filter
        # below removes re-acquisitions.
        for ev_type, _wk, ev_user in events:
            if ev_type == "Drop":
                drops_by_user[ev_user].append(pid)

    for username, roster in ctx_current_rosters.items():
        roster_set = set(roster)

        # ---------- early_pickup ----------
        early_pickup: Optional[Dict[str, Any]] = None
        best_owned_pct = float("inf")
        for pid in roster:
            added = last_added_by.get(pid)
            if not added or added[0] != username:
                continue
            week = added[1]
            if not isinstance(week, int) or week < 1 or week > _EARLY_WEEK_MAX:
                continue
            # Must be significantly owned now (current week or
            # last available data point).
            owned_now = _ownership_pct_for_week(pid, current_week, ownership_history)
            # Be lenient: if this week's data isn't in yet, fall back to
            # the latest week we DO have — caller often runs mid-Tuesday
            # before the Tue refresh hits.
            if owned_now is None and ownership_history.get(pid):
                last_known = max(
                    (int(w) for w in ownership_history[pid].keys() if w.isdigit()),
                    default=0,
                )
                if last_known:
                    owned_now = _ownership_pct_for_week(pid, last_known, ownership_history)
            if owned_now is None or owned_now < significantly_owned_threshold:
                continue
            owned_then = _ownership_pct_for_week(pid, week, ownership_history)
            if owned_then is None:
                continue
            if owned_then < best_owned_pct:
                best_owned_pct = owned_then
                pmeta = season_scoring.get(pid) or {}
                early_pickup = {
                    "player_id": pid,
                    "name": pmeta.get("full_name") or pid,
                    "week_added": week,
                    "owned_pct_when_added": round(owned_then, 1),
                    "owned_pct_now": round(owned_now, 1),
                }

        # ---------- late_drop ----------
        late_drop: Optional[Dict[str, Any]] = None
        lowest_owned_pct = float("inf")
        for pid, week in last_drop_by_user.get(username, []):
            owned_at_drop = _ownership_pct_for_week(pid, week, ownership_history)
            if owned_at_drop is None:
                continue
            if owned_at_drop < lowest_owned_pct:
                lowest_owned_pct = owned_at_drop
                pmeta = season_scoring.get(pid) or {}
                late_drop = {
                    "player_id": pid,
                    "name": pmeta.get("full_name") or pid,
                    "week_dropped": week,
                    "owned_pct_at_drop": round(owned_at_drop, 1),
                }

        # ---------- best_add ----------
        # Players currently rostered by this user that they added (not drafted).
        best_add: Optional[Dict[str, Any]] = None
        best_add_value = -1e9
        for pid in roster:
            added = last_added_by.get(pid)
            if not added or added[0] != username:
                continue
            # Skip preseason / N/A labels — those are draft acquisitions
            # not waiver pickups.
            if not isinstance(added[1], int):
                continue
            valued = _value_over_baseline(
                pid, season_scoring, qb_score_key, skill_score_key, baseline
            )
            if valued is None:
                continue
            name, pos, val = valued
            if val > best_add_value:
                best_add_value = val
                best_add = {
                    "player_id": pid,
                    "name": name,
                    "position": pos,
                    "value_over_baseline": round(val, 2),
                    "week_added": added[1],
                }

        # ---------- worst_drop ----------
        # For each position, find the dropped player with highest value-
        # over-baseline that the user does NOT currently own.
        worst_drop_by_pos: Dict[str, Dict[str, Any]] = {}
        seen_pids: set[str] = set()
        for pid in drops_by_user.get(username, []):
            if pid in seen_pids or pid in roster_set:
                continue
            seen_pids.add(pid)
            valued = _value_over_baseline(
                pid, season_scoring, qb_score_key, skill_score_key, baseline
            )
            if valued is None:
                continue
            name, pos, val = valued
            existing = worst_drop_by_pos.get(pos)
            if existing is None or val > existing["value_over_baseline"]:
                worst_drop_by_pos[pos] = {
                    "player_id": pid,
                    "name": name,
                    "value_over_baseline": round(val, 2),
                }

        out[username] = {
            "early_pickup": early_pickup,
            "late_drop": late_drop,
            "best_add": best_add,
            "worst_drop": worst_drop_by_pos,
        }
    return out
