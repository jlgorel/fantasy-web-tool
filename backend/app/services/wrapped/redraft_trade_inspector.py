"""Redraft retrospective trade evaluator.

Scores a *completed* redraft trade by summing each side's rest-of-season
VORP (value over replacement player) using the actual weekly points the
acquired players produced within the trade-to-end-of-regular-season
window. Mirrors the API surface of the dynasty
:mod:`app.services.wrapped.trade_accolades.inspect_trade` but is built
on Sleeper's per-season scoring blob instead of KTC value snapshots.

Why VORP instead of raw points?
  Replacement-level players contribute ~0 VORP, so a 3-for-1 that ships
  a stud for three filler RBs typically *loses* the trade -- the filler
  is barely above the waiver baseline. This matches how published
  retrospective trade calculators score swaps.

Pure module: HTTP + Redis + blob IO all live in the route layer that
calls :func:`inspect_redraft_trade`.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Tuple

from app.services.wrapped.league_context import LeagueContext
from app.services.wrapped.transactions import Trade as WrappedTrade

logger = logging.getLogger(__name__)

# Verdict thresholds in raw VORP. "Margin" is the larger side's surplus.
# These were picked to roughly match how published retrospective trade
# calculators (FantasyPros etc) call a trade.
WASH_VORP: float = 10.0
CLOSE_VORP: float = 30.0

# When a position can't be resolved, fall back to the league's skill
# scoring key (covers most non-IDP cases).
DEFAULT_POS_FALLBACK: str = "WR"


# ---------------------------------------------------------------------------
# Output dataclasses
# ---------------------------------------------------------------------------
@dataclass
class RedraftAssetEval:
    """One acquired player's contribution to a side's score."""
    player_id: str
    name: str
    position: str
    ros_points: float
    games_played: int
    ros_ppg: float
    baseline_points: float       # baseline_ppg[pos] * games_played
    vorp: float                  # ros_points - baseline_points

    def to_dict(self) -> Dict[str, Any]:
        return {
            "player_id": self.player_id,
            "name": self.name,
            "position": self.position,
            "ros_points": round(self.ros_points, 2),
            "games_played": self.games_played,
            "ros_ppg": round(self.ros_ppg, 2),
            "baseline_points": round(self.baseline_points, 2),
            "vorp": round(self.vorp, 2),
        }


@dataclass
class RedraftSideEval:
    username: str
    assets: List[RedraftAssetEval] = field(default_factory=list)
    total_ros_points: float = 0.0
    total_vorp: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "username": self.username,
            "assets": [a.to_dict() for a in self.assets],
            "total_ros_points": round(self.total_ros_points, 2),
            "total_vorp": round(self.total_vorp, 2),
        }


@dataclass
class RedraftTradeEval:
    sides: List[RedraftSideEval]
    verdict: str                 # "wash" | username of winner
    margin_label: str            # "wash" | "close" | "decisive"
    margin_vorp: float           # winner_vorp - loser_vorp (always >= 0)
    window_start_week: int
    window_end_week: int
    season: int
    qb_score_key: str
    skill_score_key: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "sides": [s.to_dict() for s in self.sides],
            "verdict": self.verdict,
            "margin_label": self.margin_label,
            "margin_vorp": round(self.margin_vorp, 2),
            "window": {
                "start_week": self.window_start_week,
                "end_week": self.window_end_week,
                "season": self.season,
            },
            "scoring": {
                "qb_score_key": self.qb_score_key,
                "skill_score_key": self.skill_score_key,
            },
        }


# ---------------------------------------------------------------------------
# Core math
# ---------------------------------------------------------------------------
def _primary_position(player_info: Mapping[str, Any]) -> str:
    """Pick the primary fantasy position for replacement-baseline lookup.

    Mirrors the Taysom Hill carve-out in
    :func:`compute_baseline_player_scoring` so VORP math agrees with the
    rest of the wrapped pipeline.
    """
    positions = player_info.get("fantasy_positions") or []
    if not positions:
        return DEFAULT_POS_FALLBACK
    full_name = (player_info.get("full_name") or "").strip()
    primary = positions[0] if full_name != "Taysom Hill" else "TE"
    if primary not in ("QB", "RB", "WR", "TE"):
        # K / DEF aren't covered by the standard baseline; default to WR
        # so VORP is well-defined (kicker upside is tiny anyway).
        return DEFAULT_POS_FALLBACK
    return primary


def _points_key_for_pos(pos: str, qb_score_key: str, skill_score_key: str) -> str:
    """Return the weekly-points key (``half_ppr`` / ``ppr`` / ``std``)
    appropriate for the league's scoring config + the player's position.
    """
    if pos == "QB":
        # qb_score_key is ``"std"`` or ``"6pt_pass_td"``. Weekly blob
        # doesn't carry 6pt directly, so we synthesize it as
        # std + 2 * pass_td when needed.
        return qb_score_key
    return skill_score_key


def _weekly_points_for_player(
    weekly: Mapping[Any, Mapping[str, Any]],
    points_key: str,
    start_week: int,
    end_week: int,
) -> Tuple[float, int]:
    """Sum a player's weekly points in [start_week, end_week] and count
    games they actually scored in (non-zero or non-missing).

    Returns ``(total_points, games_played)``.
    """
    total = 0.0
    games = 0
    for raw_week, stats in (weekly or {}).items():
        try:
            week = int(raw_week)
        except (TypeError, ValueError):
            continue
        if week < start_week or week > end_week:
            continue
        if points_key == "6pt_pass_td":
            pts = float(stats.get("std", 0) or 0) + 2.0 * float(stats.get("pass_td", 0) or 0)
        else:
            pts = float(stats.get(points_key, 0) or 0)
        if pts != 0:
            total += pts
            games += 1
    return total, games


def _per_pos_baseline_ppg(
    baseline_total_points: Mapping[str, float],
    window_weeks: int,
) -> Dict[str, float]:
    """Convert season-total replacement points into per-game replacement
    PPG so VORP scales correctly inside an arbitrary trade window.

    Uses 17 games as the canonical season length (close enough for
    2021+ and a safe upper bound for older years; under-counting by 1
    game shifts all baselines uniformly so verdict ordering is stable).
    """
    games_per_season = 17.0
    return {pos: pts / games_per_season for pos, pts in (baseline_total_points or {}).items()}


def evaluate_redraft_trade(
    trade: WrappedTrade,
    *,
    season: int,
    season_scoring: Mapping[str, Mapping[str, Any]],
    qb_score_key: str,
    skill_score_key: str,
    baseline_total_points: Mapping[str, float],
    start_week: int,
    end_week: int,
) -> RedraftTradeEval:
    """Score a completed redraft trade by side-level rest-of-season VORP.

    All inputs are pure-data; no IO. ``baseline_total_points`` is the
    output of :func:`compute_baseline_player_scoring` (per-position
    season-total replacement points), which we rescale to per-game
    inside the function.

    Redraft-only: pick assets on ``trade.sides`` are ignored, since
    redraft leagues don't trade picks once the draft has happened.
    """
    if end_week < start_week:
        raise ValueError(f"Bad window: start_week={start_week} > end_week={end_week}")

    baseline_ppg = _per_pos_baseline_ppg(baseline_total_points, end_week - start_week + 1)

    side_evals: List[RedraftSideEval] = []
    for username, side in trade.sides.items():
        assets: List[RedraftAssetEval] = []

        for pid in side.received_player_ids:
            info = season_scoring.get(str(pid)) or {}
            pos = _primary_position(info)
            points_key = _points_key_for_pos(pos, qb_score_key, skill_score_key)
            weekly = info.get("scoring_data_weekly") or {}
            ros_points, games = _weekly_points_for_player(
                weekly, points_key, start_week, end_week
            )
            ros_ppg = (ros_points / games) if games else 0.0
            baseline_pts = baseline_ppg.get(pos, 0.0) * games
            assets.append(RedraftAssetEval(
                player_id=str(pid),
                name=info.get("full_name") or str(pid),
                position=pos,
                ros_points=ros_points,
                games_played=games,
                ros_ppg=ros_ppg,
                baseline_points=baseline_pts,
                vorp=ros_points - baseline_pts,
            ))

        total_ros = sum(a.ros_points for a in assets)
        total_vorp = sum(a.vorp for a in assets)
        side_evals.append(RedraftSideEval(
            username=username,
            assets=assets,
            total_ros_points=total_ros,
            total_vorp=total_vorp,
        ))

    # Verdict + margin (handles 2-side trades cleanly; for 3-way we
    # compare the best side to the second-best).
    sorted_sides = sorted(side_evals, key=lambda s: s.total_vorp, reverse=True)
    if len(sorted_sides) >= 2:
        margin = sorted_sides[0].total_vorp - sorted_sides[1].total_vorp
    else:
        margin = 0.0

    if abs(margin) < WASH_VORP:
        verdict = "wash"
        margin_label = "wash"
    else:
        verdict = sorted_sides[0].username
        margin_label = "close" if abs(margin) < CLOSE_VORP else "decisive"

    return RedraftTradeEval(
        sides=side_evals,
        verdict=verdict,
        margin_label=margin_label,
        margin_vorp=abs(margin),
        window_start_week=start_week,
        window_end_week=end_week,
        season=season,
        qb_score_key=qb_score_key,
        skill_score_key=skill_score_key,
    )


# ---------------------------------------------------------------------------
# IO-aware orchestrator -- called by the Flask route
# ---------------------------------------------------------------------------
def inspect_redraft_trade(
    trade: WrappedTrade,
    *,
    ctx: LeagueContext,
    season: int,
    season_scoring: Mapping[str, Mapping[str, Any]],
) -> Dict[str, Any]:
    """Produce the full inspector payload for a single redraft trade.

    Loads the baseline from the same per-season scoring blob the wrapped
    pipeline uses, picks the trade window (trade leg week + 1 through
    last_regular_season_week), and returns a JSON-ready dict.

    Raises
    ------
    RuntimeError
        If ``season_scoring`` is empty -- the inspector is useless
        without weekly points data. The HTTP route should translate this
        into a 503.
    """
    if not season_scoring:
        raise RuntimeError(
            f"player_season_scoring_{season}.json is unavailable; "
            "redraft inspector needs weekly points data."
        )

    # Local import to dodge a circular import at module-load time --
    # replacement_value imports league_context which is referenced from
    # routes.py which imports this module.
    from app.services.wrapped.replacement_value import compute_baseline_player_scoring

    baseline = compute_baseline_player_scoring(ctx, season_scoring)

    # Trade-leg-week + 1 is "the first week the new owner could start
    # the player". Cap at the last regular-season week (playoffs are
    # bracket noise we exclude on purpose).
    start_week = max(1, int(trade.week) + 1)
    end_week = int(ctx.last_regular_season_week or 17)
    if end_week < start_week:
        # Trade happened in or after the final regular-season week:
        # there's no rest-of-season window left to score. Return an
        # empty-window eval so the UI can show "trade made too late
        # to retro-score".
        end_week = start_week  # 1-week window, mostly empty

    eval_obj = evaluate_redraft_trade(
        trade,
        season=season,
        season_scoring=season_scoring,
        qb_score_key=ctx.qb_score_key,
        skill_score_key=ctx.skill_score_key,
        baseline_total_points=baseline,
        start_week=start_week,
        end_week=end_week,
    )

    return {
        "transaction_id": trade.transaction_id,
        "trade_week": trade.week,
        "evaluation": eval_obj.to_dict(),
        "baseline_total_points": {k: round(v, 2) for k, v in baseline.items()},
    }


__all__ = [
    "RedraftAssetEval",
    "RedraftSideEval",
    "RedraftTradeEval",
    "WASH_VORP",
    "CLOSE_VORP",
    "evaluate_redraft_trade",
    "inspect_redraft_trade",
]
