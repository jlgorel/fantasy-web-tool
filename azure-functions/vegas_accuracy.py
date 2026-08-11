"""Pure (no-Azure, no-Playwright) helpers for the Vegas projection
accountability pipeline.

Two responsibilities live here, both as side-effect-free functions so they can
be unit-tested offline against fixtures:

1. ``merge_week_capture`` -- the weekly *locking* capture. Each scrape run we
   fold the latest ``standard_player_rankings.json`` (projected fantasy points
   per player) and ``fantasypros_data.json`` (ECR overall rank) into a frozen
   per-week history blob. The merge is deliberately additive: a player captured
   earlier in the week (e.g. a Thursday-night starter) is *never* dropped or
   zeroed just because a later Sunday scrape no longer lists them. Each player's
   week slot is only refreshed while they still carry a live projection, so the
   value naturally freezes at their last pre-game line.

2. ``compile_review`` -- the Tuesday accuracy review. Given the accumulated
   history plus the realized ``player_season_scoring_{year}.json`` actuals, it
   scores how close the Vegas projections were (points + positional ranks) and
   contrasts the Vegas positional ranks against FantasyPros ECR, both for the
   most recent week and pooled season-to-date.

The blob I/O + timer wiring lives in ``function_app.py``; this module stays
importable without the Azure Functions runtime so the backend test-suite can
exercise the logic directly.
"""
from __future__ import annotations

import math
from typing import Any, Dict, Iterable, List, Optional, Tuple

# The half-PPR / 4-pt-passing-TD variant key emitted by
# ``form_all_projections_and_points_dict`` into ``standard_player_rankings.json``.
HALF_PPR_VARIANT = "halfppr_4ptpass"

# Fantasy-relevant skill positions we grade positional ranks for.
GRADED_POSITIONS = ("QB", "RB", "WR", "TE")


# ---------------------------------------------------------------------------
# Capture (weekly locking merge)
# ---------------------------------------------------------------------------
def fp_overall_rank_by_pid(
    fantasypros_data: Dict[str, Any],
    players: Dict[str, Any],
) -> Dict[str, int]:
    """Map Sleeper ``pid -> FantasyPros overall_rank``.

    ``fantasypros_data.json`` is keyed by full player name and carries no PID,
    so we join it to ``players.json`` (``{pid: {full_name, ...}}``) via a
    normalized-name index. Names that don't resolve are skipped.
    """
    name_to_pid: Dict[str, str] = {}
    for pid, pdata in (players or {}).items():
        name = (pdata or {}).get("full_name")
        if name:
            name_to_pid[_norm_name(name)] = pid

    out: Dict[str, int] = {}
    for name, info in (fantasypros_data or {}).items():
        rank = (info or {}).get("overall_rank")
        if rank is None:
            continue
        pid = name_to_pid.get(_norm_name(name))
        if pid is None:
            continue
        try:
            out[pid] = int(rank)
        except (TypeError, ValueError):
            continue
    return out


def merge_week_capture(
    history: Dict[str, Any],
    week: int,
    ranking_rows: Iterable[Dict[str, Any]],
    fp_rank_by_pid: Optional[Dict[str, int]] = None,
    *,
    captured_at: Optional[str] = None,
) -> Dict[str, Any]:
    """Fold this run's projections into ``history[week]`` in place and return it.

    ``ranking_rows`` is the ``standard_player_rankings.json`` list for the
    half-PPR variant: ``[{PID, NAME, POS, VEGAS, ...}, ...]``. ``fp_rank_by_pid``
    maps ``pid -> FantasyPros overall_rank`` for the same run.

    Locking semantics (the whole point of this function):
      * We only refresh a player's projected points when the current run carries
        a live projection (``VEGAS > 0``). A player who has already played and
        dropped out of later scrapes keeps their previously captured line.
      * ECR is merged independently -- a player may have Vegas but no ECR or vice
        versa, and whichever is present-and-valid this run updates its own field.
      * Nothing is ever deleted, so the week bucket only grows toward the frozen
        "last pre-game line" for every player who appeared at any point.
    """
    week_key = str(week)
    bucket: Dict[str, Any] = history.setdefault(week_key, {})
    fp_rank_by_pid = fp_rank_by_pid or {}

    for row in ranking_rows or []:
        pid = row.get("PID")
        if not pid:
            continue
        try:
            proj = float(row.get("VEGAS", 0) or 0)
        except (TypeError, ValueError):
            proj = 0.0
        # A player with no live line this run contributes nothing; their earlier
        # locked value (if any) is preserved untouched.
        if proj <= 0:
            continue
        entry = bucket.setdefault(pid, {})
        entry["name"] = row.get("NAME") or entry.get("name")
        entry["pos"] = row.get("POS") or entry.get("pos")
        entry["proj_half_ppr"] = round(proj, 2)
        if captured_at is not None:
            entry["proj_locked_at"] = captured_at

    for pid, rank in fp_rank_by_pid.items():
        try:
            rank_int = int(rank)
        except (TypeError, ValueError):
            continue
        entry = bucket.setdefault(pid, {})
        entry["ecr_overall"] = rank_int
        if captured_at is not None:
            entry["ecr_locked_at"] = captured_at

    return history


# ---------------------------------------------------------------------------
# Review (Tuesday accuracy compiler)
# ---------------------------------------------------------------------------
def compile_review(
    history: Dict[str, Any],
    actuals: Dict[str, Any],
    *,
    upto_week: Optional[int] = None,
) -> Dict[str, Any]:
    """Score the locked Vegas projections against realized results.

    ``history`` is the ``projection_history_{year}.json`` blob
    (``{week: {pid: {proj_half_ppr, ecr_overall, pos, name}}}``). ``actuals`` is
    ``player_season_scoring_{year}.json``
    (``{pid: {scoring_data_weekly: {week: {half_ppr, ...}}}}``).

    Returns ``{latest_week, weeks: [...], weekly: {week: metrics}, season}``
    where each metrics block reports points accuracy (MAE / RMSE / bias /
    correlation) and a Vegas-vs-FantasyPros positional-rank head-to-head.
    """
    week_nums = sorted(int(w) for w in history.keys())
    if upto_week is not None:
        week_nums = [w for w in week_nums if w <= int(upto_week)]

    weekly: Dict[str, Any] = {}
    # Pools for the season-to-date aggregate.
    season_point_rows: List[Tuple[float, float]] = []  # (proj, actual)
    season_vegas_rank_err: List[float] = []
    season_ecr_rank_err: List[float] = []
    season_h2h_vegas_err: List[float] = []
    season_h2h_ecr_err: List[float] = []

    graded_weeks: List[int] = []
    for week in week_nums:
        rows = _rows_for_week(history[str(week)], actuals, week)
        if not rows:
            continue
        graded_weeks.append(week)

        point_rows = [(r["proj"], r["actual"]) for r in rows if r["proj"] is not None]
        season_point_rows.extend(point_rows)

        rank_block = _positional_rank_block(rows)
        season_vegas_rank_err.extend(rank_block["_vegas_err"])
        season_ecr_rank_err.extend(rank_block["_ecr_err"])
        season_h2h_vegas_err.extend(rank_block["_h2h_vegas_err"])
        season_h2h_ecr_err.extend(rank_block["_h2h_ecr_err"])

        weekly[str(week)] = {
            "week": week,
            "n_players": len(rows),
            "points": _points_metrics(point_rows),
            "ranks": _public_rank_block(rank_block),
        }

    season = {
        "n_weeks": len(graded_weeks),
        "n_player_weeks": len(season_point_rows),
        "points": _points_metrics(season_point_rows),
        "ranks": {
            "vegas_rank_mae": _mean(season_vegas_rank_err),
            "ecr_rank_mae": _mean(season_ecr_rank_err),
            "head_to_head": _head_to_head_summary(
                season_h2h_vegas_err, season_h2h_ecr_err
            ),
        },
    }

    return {
        "latest_week": graded_weeks[-1] if graded_weeks else None,
        "weeks": graded_weeks,
        "weekly": weekly,
        "season": season,
    }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------
def _rows_for_week(
    week_history: Dict[str, Any],
    actuals: Dict[str, Any],
    week: int,
) -> List[Dict[str, Any]]:
    """Join a week's locked projections to realized half-PPR points.

    Only players who actually recorded a score that week (i.e. appear in the
    Sleeper weekly actuals) are graded -- a player projected then inactive
    shouldn't count as a "miss".
    """
    rows: List[Dict[str, Any]] = []
    for pid, entry in (week_history or {}).items():
        actual = _actual_half_ppr(actuals, pid, week)
        if actual is None:
            continue
        pos = (entry or {}).get("pos") or _actual_pos(actuals, pid)
        if pos not in GRADED_POSITIONS:
            continue
        proj = (entry or {}).get("proj_half_ppr")
        rows.append({
            "pid": pid,
            "pos": pos,
            "proj": float(proj) if proj is not None else None,
            "ecr": (entry or {}).get("ecr_overall"),
            "actual": float(actual),
        })
    return rows


def _positional_rank_block(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Compute per-position rank errors for Vegas vs FantasyPros vs reality.

    For each graded position we rank the players who scored that week by their
    actual points (truth), by their Vegas projection, and by their ECR overall
    rank, then measure each source's mean absolute rank error against truth. The
    head-to-head lists are restricted to players who carry *both* a Vegas
    projection and an ECR so the comparison is apples-to-apples.
    """
    vegas_err: List[float] = []
    ecr_err: List[float] = []
    h2h_vegas_err: List[float] = []
    h2h_ecr_err: List[float] = []
    by_position: Dict[str, Any] = {}

    for pos in GRADED_POSITIONS:
        pos_rows = [r for r in rows if r["pos"] == pos]
        if not pos_rows:
            continue

        actual_rank = _rank_map(
            [(r["pid"], r["actual"]) for r in pos_rows], higher_is_better=True
        )
        with_proj = [r for r in pos_rows if r["proj"] is not None]
        vegas_rank = _rank_map(
            [(r["pid"], r["proj"]) for r in with_proj], higher_is_better=True
        )
        with_ecr = [r for r in pos_rows if r["ecr"] is not None]
        ecr_rank = _rank_map(
            [(r["pid"], r["ecr"]) for r in with_ecr], higher_is_better=False
        )

        pos_vegas_err = [
            abs(vegas_rank[r["pid"]] - actual_rank[r["pid"]]) for r in with_proj
        ]
        pos_ecr_err = [
            abs(ecr_rank[r["pid"]] - actual_rank[r["pid"]]) for r in with_ecr
        ]
        vegas_err.extend(pos_vegas_err)
        ecr_err.extend(pos_ecr_err)

        h2h_ids = [
            r["pid"] for r in pos_rows if r["proj"] is not None and r["ecr"] is not None
        ]
        pos_h2h_vegas = [abs(vegas_rank[p] - actual_rank[p]) for p in h2h_ids]
        pos_h2h_ecr = [abs(ecr_rank[p] - actual_rank[p]) for p in h2h_ids]
        h2h_vegas_err.extend(pos_h2h_vegas)
        h2h_ecr_err.extend(pos_h2h_ecr)

        by_position[pos] = {
            "n": len(pos_rows),
            "vegas_rank_mae": _mean(pos_vegas_err),
            "ecr_rank_mae": _mean(pos_ecr_err),
            "head_to_head": _head_to_head_summary(pos_h2h_vegas, pos_h2h_ecr),
        }

    return {
        "by_position": by_position,
        "_vegas_err": vegas_err,
        "_ecr_err": ecr_err,
        "_h2h_vegas_err": h2h_vegas_err,
        "_h2h_ecr_err": h2h_ecr_err,
    }


def _public_rank_block(block: Dict[str, Any]) -> Dict[str, Any]:
    """Strip the internal pooled-error lists from a rank block for output."""
    return {
        "by_position": block["by_position"],
        "vegas_rank_mae": _mean(block["_vegas_err"]),
        "ecr_rank_mae": _mean(block["_ecr_err"]),
        "head_to_head": _head_to_head_summary(
            block["_h2h_vegas_err"], block["_h2h_ecr_err"]
        ),
    }


def _head_to_head_summary(
    vegas_err: List[float], ecr_err: List[float]
) -> Dict[str, Any]:
    """Summarize a matched Vegas-vs-ECR positional-rank comparison."""
    vegas_mae = _mean(vegas_err)
    ecr_mae = _mean(ecr_err)
    better = None
    margin = None
    if vegas_mae is not None and ecr_mae is not None:
        margin = round(ecr_mae - vegas_mae, 3)
        if margin > 0:
            better = "vegas"
        elif margin < 0:
            better = "ecr"
        else:
            better = "tie"
    return {
        "n": len(vegas_err),
        "vegas_rank_mae": vegas_mae,
        "ecr_rank_mae": ecr_mae,
        # Positive => Vegas ranks were closer to reality than FantasyPros ECR.
        "vegas_better_by": margin,
        "winner": better,
    }


def _points_metrics(rows: List[Tuple[float, float]]) -> Dict[str, Any]:
    """MAE / RMSE / bias / Pearson correlation for (proj, actual) pairs."""
    n = len(rows)
    if n == 0:
        return {
            "n": 0,
            "mae": None,
            "rmse": None,
            "bias": None,
            "correlation": None,
        }
    errors = [proj - actual for proj, actual in rows]
    mae = sum(abs(e) for e in errors) / n
    rmse = math.sqrt(sum(e * e for e in errors) / n)
    bias = sum(errors) / n
    return {
        "n": n,
        "mae": round(mae, 3),
        "rmse": round(rmse, 3),
        # Positive bias => Vegas projected higher than players actually scored.
        "bias": round(bias, 3),
        "correlation": _pearson([p for p, _ in rows], [a for _, a in rows]),
    }


def _rank_map(
    pairs: List[Tuple[str, float]], *, higher_is_better: bool
) -> Dict[str, int]:
    """Rank ids 1..N by value; ties broken by id for determinism."""
    ordered = sorted(
        pairs, key=lambda kv: (kv[1], kv[0]), reverse=higher_is_better
    )
    return {pid: idx + 1 for idx, (pid, _) in enumerate(ordered)}


def _actual_half_ppr(
    actuals: Dict[str, Any], pid: str, week: int
) -> Optional[float]:
    weekly = ((actuals or {}).get(pid) or {}).get("scoring_data_weekly") or {}
    stats = weekly.get(str(week))
    if stats is None:
        stats = weekly.get(week)
    if not isinstance(stats, dict):
        return None
    val = stats.get("half_ppr")
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _actual_pos(actuals: Dict[str, Any], pid: str) -> Optional[str]:
    positions = ((actuals or {}).get(pid) or {}).get("fantasy_positions") or []
    return positions[0] if positions else None


def _pearson(xs: List[float], ys: List[float]) -> Optional[float]:
    n = len(xs)
    if n < 2:
        return None
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    cov = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    var_x = sum((x - mean_x) ** 2 for x in xs)
    var_y = sum((y - mean_y) ** 2 for y in ys)
    if var_x <= 0 or var_y <= 0:
        return None
    return round(cov / math.sqrt(var_x * var_y), 3)


def _mean(values: List[float]) -> Optional[float]:
    if not values:
        return None
    return round(sum(values) / len(values), 3)


def _norm_name(name: str) -> str:
    return "".join(ch for ch in (name or "") if ch.isalnum()).lower()
