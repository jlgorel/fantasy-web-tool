"""Draft accolades for the Wrapped pipeline.

Sleeper exposes:
    GET /league/{id}/drafts        -> list of drafts (we use the first)
    GET /draft/{draft_id}/picks    -> ordered picks with pick_no, round,
                                      roster_id, player_id, picked_by, ...

We compute "value over slot" by ranking every drafted player by season
points (positional, since a QB picked at #50 isn't bad — they're a top-5
QB if they outscored every other QB taken later) and comparing to draft
position within the same position. That avoids the need for an external
ADP feed.

All accolade computation is pure over its inputs so unit tests can
hand-craft the picks/scoring fixture.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from app.services.http_utils import fetch_json
from app.services.wrapped.league_context import LeagueContext

logger = logging.getLogger(__name__)


@dataclass
class DraftPick:
    pick_no: int
    round: int
    player_id: str
    username: str
    position: str  # primary fantasy position
    season_points: float
    # Filled in by ``compute_value_over_slot`` after we've sorted the
    # picks at each position by actual production.
    actual_pos_rank: int = 0
    drafted_pos_rank: int = 0
    value_over_slot: float = 0.0


@dataclass
class DraftAccolades:
    by_user: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    biggest_steal: Optional[Dict[str, Any]] = None
    biggest_bust: Optional[Dict[str, Any]] = None
    mr_irrelevant_hero: Optional[Dict[str, Any]] = None


def _fetch_draft_picks(league_id: str) -> List[Dict[str, Any]]:
    """Return the raw picks list for the league's first draft, or [].

    Wrapped is read-only — we'd rather render an empty draft section than
    500 the whole page, so any failure here gets logged + swallowed.
    """
    try:
        drafts = fetch_json(f"https://api.sleeper.app/v1/league/{league_id}/drafts") or []
        if not drafts:
            return []
        draft_id = drafts[0].get("draft_id")
        if not draft_id:
            return []
        return fetch_json(f"https://api.sleeper.app/v1/draft/{draft_id}/picks") or []
    except Exception as e:
        logger.warning("draft picks fetch failed for %s: %s", league_id, e)
        return []


def _points_for(pid: str, season_scoring: Dict[str, Any], score_key: str) -> float:
    info = season_scoring.get(pid) or {}
    season = info.get("scoring_data_season") or {}
    val = season.get(f"{score_key}_points")
    try:
        return float(val) if val is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


def _resolve_position(
    pid: str,
    season_scoring: Dict[str, Any],
    players_meta: Optional[Dict[str, Any]] = None,
) -> str:
    """Return the player's primary fantasy position.

    Looks up ``season_scoring`` first (year-specific blob) and falls back
    to ``players_meta`` (the live ``players.json`` we keep around for
    every NFL player). The fallback matters a lot — the year-specific
    scoring blob only contains the top ~150 RB/WR, top 50 QB/TE/K, and
    top 32 DEF, so any drafted player who finished outside those cutoffs
    would otherwise be bucketed as ``UNK`` and disappear from per-position
    rankings (which is what made early-round busts like an injured RB1
    not show up as the biggest bust).
    """
    info = season_scoring.get(pid) or {}
    positions = info.get("fantasy_positions") or []
    full_name = (info.get("full_name") or "").strip()

    if not positions and players_meta:
        meta = players_meta.get(pid) or {}
        positions = meta.get("fantasy_positions") or []
        if not full_name:
            full_name = (meta.get("full_name") or "").strip()

    if not positions:
        return "UNK"
    # Special-case Taysom Hill (Sleeper lists QB first but he's a TE in
    # most leagues that played him). Mirrors the roster_accolades behavior.
    if full_name == "Taysom Hill":
        return "TE"
    return positions[0]


def build_picks(
    raw_picks: List[Dict[str, Any]],
    ctx: LeagueContext,
    season_scoring: Dict[str, Any],
    players_meta: Optional[Dict[str, Any]] = None,
) -> List[DraftPick]:
    """Project Sleeper's raw pick objects into our typed model + season pts.

    ``players_meta`` is the league-agnostic ``players.json`` snapshot. It
    only supplies a fallback for ``fantasy_positions`` when the season
    scoring blob doesn't list the player (e.g. backups, late-round darts,
    early-round busts who missed the season). It is **not** used for
    season points — a player missing from ``season_scoring`` correctly
    earns 0 points and lands at the bottom of his positional ranking.
    """
    picks: List[DraftPick] = []
    for raw in raw_picks:
        pid = str(raw.get("player_id") or "")
        if not pid:
            continue
        roster_id = raw.get("roster_id")
        username = ctx.roster_id_to_username.get(roster_id)
        if not username:
            continue
        pos = _resolve_position(pid, season_scoring, players_meta)
        score_key = ctx.qb_score_key if pos == "QB" else ctx.skill_score_key
        picks.append(
            DraftPick(
                pick_no=int(raw.get("pick_no") or 0),
                round=int(raw.get("round") or 0),
                player_id=pid,
                username=username,
                position=pos,
                season_points=_points_for(pid, season_scoring, score_key),
            )
        )
    return picks


def compute_value_over_slot(picks: List[DraftPick]) -> None:
    """Annotate each pick with ``actual_pos_rank``, ``drafted_pos_rank``,
    and ``value_over_slot`` (positive = steal, negative = bust).

    Mutates in place. Players who scored 0 (didn't play) get a large
    negative VOS that scales with how early they were taken.
    """
    by_pos: Dict[str, List[DraftPick]] = defaultdict(list)
    for p in picks:
        by_pos[p.position].append(p)

    for pos_picks in by_pos.values():
        # drafted rank = order they were picked at this position
        pos_picks_sorted_by_pick = sorted(pos_picks, key=lambda x: x.pick_no)
        for i, p in enumerate(pos_picks_sorted_by_pick, start=1):
            p.drafted_pos_rank = i

        # actual rank = order they finished by season points (descending)
        pos_picks_sorted_by_pts = sorted(
            pos_picks, key=lambda x: x.season_points, reverse=True
        )
        for i, p in enumerate(pos_picks_sorted_by_pts, start=1):
            p.actual_pos_rank = i

        for p in pos_picks:
            # Positive number = drafted later than they finished = steal.
            p.value_over_slot = float(p.drafted_pos_rank - p.actual_pos_rank)


def calculate_draft_accolades(
    picks: List[DraftPick],
    irrelevant_top_n: int = 24,
) -> DraftAccolades:
    """Build the per-user + overall draft accolades from valued picks."""
    out = DraftAccolades()
    if not picks:
        return out

    by_user: Dict[str, List[DraftPick]] = defaultdict(list)
    for p in picks:
        by_user[p.username].append(p)

    def _pick_payload(p: DraftPick) -> Dict[str, Any]:
        return {
            "player_id": p.player_id,
            "pick_no": p.pick_no,
            "round": p.round,
            "position": p.position,
            "season_points": round(p.season_points, 2),
            "drafted_pos_rank": p.drafted_pos_rank,
            "actual_pos_rank": p.actual_pos_rank,
            "value_over_slot": round(p.value_over_slot, 2),
        }

    # Per-user best/worst.
    for user, user_picks in by_user.items():
        best = max(user_picks, key=lambda p: p.value_over_slot)
        worst = min(user_picks, key=lambda p: p.value_over_slot)
        out.by_user[user] = {
            "best_pick": _pick_payload(best),
            "worst_pick": _pick_payload(worst),
            "num_picks": len(user_picks),
        }

    # Overall biggest steal / bust.
    out.biggest_steal = _pick_payload(max(picks, key=lambda p: p.value_over_slot))
    out.biggest_bust = _pick_payload(min(picks, key=lambda p: p.value_over_slot))

    # "Mr. Irrelevant" — latest pick (highest pick_no) who finished as a
    # top-N producer at their position. Captures the late-round dart that
    # actually hit. Only emit if such a player exists.
    candidates = [p for p in picks if p.actual_pos_rank <= irrelevant_top_n]
    if candidates:
        latest = max(candidates, key=lambda p: p.pick_no)
        out.mr_irrelevant_hero = {
            **_pick_payload(latest),
            "username": latest.username,
        }

    return out


def fetch_and_compute_draft(
    ctx: LeagueContext,
    season_scoring: Dict[str, Any],
    players_meta: Optional[Dict[str, Any]] = None,
) -> DraftAccolades:
    """End-to-end: fetch picks → build typed picks → annotate → accolades."""
    raw_picks = _fetch_draft_picks(ctx.league_id)
    if not raw_picks:
        return DraftAccolades()
    picks = build_picks(raw_picks, ctx, season_scoring, players_meta)
    compute_value_over_slot(picks)
    return calculate_draft_accolades(picks)
