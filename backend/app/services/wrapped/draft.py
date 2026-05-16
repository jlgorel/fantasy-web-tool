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
import math
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from app.services.http_utils import fetch_json
from app.services.wrapped.league_context import LeagueContext

logger = logging.getLogger(__name__)


# A pick only qualifies as a bust if its actual positional rank slipped
# at least this many spots below the drafted positional rank. Stops
# top-of-position picks (RB1 -> RB3, WR2 -> WR4) from getting flagged
# as busts when they're really just elite seasons with a little
# variance noise on top.
_BUST_MIN_RANK_DELTA = 3


# Positions excluded from draft steal/bust accolades. Kickers and
# defenses score on a fundamentally different (and noisier) curve;
# nobody cares that you picked DST3 and got DST22. They still appear
# in the draft pick list and contribute to roster context, just not
# to "biggest steal" / "biggest bust" rankings.
_EXCLUDED_FROM_DRAFT_ACCOLADES = {"K", "DEF", "DST"}


# Startable-tier thresholds per fantasy position, sized for a typical
# 12-team 1QB league. A pick is a "real" bust only when its final
# positional rank lands outside this tier (i.e. unstartable on any
# roster). A pick is a "real" steal only when it was drafted outside
# this tier but finished inside it. This is what filters out the
# Chase WR1->WR4 false-positive bust: WR4 is still elite, not a bust.
# These thresholds intentionally err on the generous side -- a player
# at rank ``threshold + 1`` already counts as a bust candidate.
_STARTABLE_TIER: Dict[str, int] = {
    "QB": 18,
    "RB": 30,
    "WR": 40,
    "TE": 18,
}

# Per-position override for steal qualification: a pick only counts as
# a steal if it finished within this rank at its position. Defaults to
# ``_STARTABLE_TIER`` when the position is absent. TE is gated harder
# because mid-tier TEs ("Hunter Henry as TE10") are not real steals --
# only an outright top-3 finish at the position is fleece-worthy.
_STEAL_FINISH_CAP: Dict[str, int] = {
    "TE": 3,
}


def _bust_qualifies(p: "DraftPick") -> bool:
    """A pick qualifies as a bust when it was drafted as a startable
    asset at its position but finished outside the startable tier.

    The "Jeudy/Brian Thomas test": drafted with the expectation of
    weekly starts, became unrosterable. Avoids penalising elite top
    finishes that happen to slip a few ranks below their draft slot
    (Chase WR1->WR4 is not a bust).
    """
    if p.position in _EXCLUDED_FROM_DRAFT_ACCOLADES:
        return False
    threshold = _STARTABLE_TIER.get(p.position)
    if threshold is None:
        return False
    if p.drafted_pos_rank <= 0 or p.actual_pos_rank <= 0:
        return False
    # Must have been drafted within the startable tier.
    if p.drafted_pos_rank > threshold:
        return False
    # Must have finished outside the startable tier.
    if p.actual_pos_rank <= threshold:
        return False
    # Must have actually slipped (not climbed past the threshold).
    if p.actual_pos_rank <= p.drafted_pos_rank:
        return False
    return True


def _steal_qualifies(p: "DraftPick") -> bool:
    """A pick qualifies as a steal when it was drafted outside the
    startable tier but finished inside it (or at least closer to the
    top of its position). Excludes K/DEF for the same reason as busts.
    """
    if p.position in _EXCLUDED_FROM_DRAFT_ACCOLADES:
        return False
    threshold = _STARTABLE_TIER.get(p.position)
    if threshold is None:
        return False
    if p.drafted_pos_rank <= 0 or p.actual_pos_rank <= 0:
        return False
    # Must have been drafted outside the startable tier (i.e. as a
    # bench-or-worse pick) but finished inside it.
    if p.drafted_pos_rank <= threshold:
        return False
    finish_cap = _STEAL_FINISH_CAP.get(p.position, threshold)
    if p.actual_pos_rank > finish_cap:
        return False
    if p.actual_pos_rank >= p.drafted_pos_rank:
        return False
    return True


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
    # Positional-VOR delta: 1/sqrt(actual_rank) - 1/sqrt(drafted_rank),
    # scaled ×100 for readability. Positive = steal (RB18 -> RB5 is much
    # bigger than WR90 -> WR40, which the old linear delta got backwards).
    value_over_slot: float = 0.0
    # For busts only: |value_over_slot| weighted by 1/sqrt(pick_no) so
    # an RB1 bust at overall pick #1 outranks the same bust at #50.
    # Positive number; 0 when the pick isn't a bust.
    bust_score: float = 0.0


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
    ``value_over_slot``, and ``bust_score``. Mutates in place.

    Scoring model
    -------------
    Positional value follows an inverse-sqrt curve: moving from rank-N
    to rank-M at a position is worth ``1/sqrt(M) - 1/sqrt(N)``. This
    captures the intuition that rank 18 -> rank 5 (backup-tier to elite)
    is a way bigger swing than rank 90 -> rank 40 (both bench tier),
    even though the raw rank delta is smaller.

    Concretely, ``value_over_slot = (1/sqrt(actual) - 1/sqrt(drafted))
    * 100`` so the numbers are eyeball-readable (Cook RB18 -> RB5
    ≈ +20.4; WR90 -> WR40 ≈ +5.3).

    Busts (negative ``value_over_slot``) carry an additional penalty
    weight by overall pick number: ``bust_score = |value_over_slot| *
    1/sqrt(pick_no)`` so blowing the #1 overall pick hurts more than
    blowing the #50 overall pick. The "Biggest bust" leaderboard ranks
    by ``bust_score``; the "Biggest steal" leaderboard ranks by raw
    ``value_over_slot``.
    """
    def _positional_value(rank: int) -> float:
        # rank is 1-indexed and always >= 1 because we build it from
        # enumerate(..., start=1) below.
        return 1.0 / math.sqrt(rank) if rank > 0 else 0.0

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
            delta = _positional_value(p.actual_pos_rank) - _positional_value(p.drafted_pos_rank)
            p.value_over_slot = delta * 100.0
            # Bust qualification: must clear the startable-tier filter
            # so we don't flag Chase WR1->WR4 as a bust just because
            # the inverse-sqrt curve is steep at the top. See
            # ``_bust_qualifies`` for the criteria. When it qualifies,
            # bust_score weights how far below the startable threshold
            # the player landed, scaled by overall draft cost so an
            # early-round bust outranks a late-round one.
            if _bust_qualifies(p) and p.pick_no > 0:
                threshold = _STARTABLE_TIER[p.position]
                drop_below_startable = p.actual_pos_rank - threshold
                p.bust_score = drop_below_startable / math.sqrt(p.pick_no)
            else:
                p.bust_score = 0.0


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
            "bust_score": round(p.bust_score, 2),
        }

    # Per-user best/worst. Both are filtered by the same K/DEF
    # exclusion + startable-tier qualifiers used at the overall level
    # so a user's "best pick" can't be their kicker who finished K1,
    # and their "worst pick" can't be Chase finishing WR4.
    for user, user_picks in by_user.items():
        steal_candidates = [p for p in user_picks if _steal_qualifies(p)]
        if steal_candidates:
            best = max(steal_candidates, key=lambda p: p.value_over_slot)
            best_payload = _pick_payload(best)
        else:
            best_payload = None
        bust_candidates = [p for p in user_picks if p.bust_score > 0]
        if bust_candidates:
            worst = max(
                bust_candidates,
                key=lambda p: (p.bust_score, -p.value_over_slot),
            )
            worst_payload = _pick_payload(worst)
        else:
            worst_payload = None
        out.by_user[user] = {
            "best_pick": best_payload,
            "worst_pick": worst_payload,
            "num_picks": len(user_picks),
        }

    # Overall biggest steal -- only qualifying picks (drafted bench
    # tier, finished startable tier) are considered. K/DEF excluded.
    steal_candidates_overall = [p for p in picks if _steal_qualifies(p)]
    if steal_candidates_overall:
        out.biggest_steal = _pick_payload(
            max(steal_candidates_overall, key=lambda p: p.value_over_slot)
        )
    # Overall biggest bust -- only picks that finished outside their
    # position's startable tier after being drafted as a starter.
    # K/DEF excluded.
    bust_candidates_overall = [p for p in picks if p.bust_score > 0]
    if bust_candidates_overall:
        out.biggest_bust = _pick_payload(
            max(bust_candidates_overall, key=lambda p: (p.bust_score, -p.value_over_slot))
        )

    # "Mr. Irrelevant" — latest pick (highest pick_no) who finished as a
    # top-N producer at their position. Captures the late-round dart that
    # actually hit. K/DEF excluded -- a top-N kicker isn't a story.
    candidates = [
        p for p in picks
        if p.actual_pos_rank <= irrelevant_top_n
        and p.position not in _EXCLUDED_FROM_DRAFT_ACCOLADES
    ]
    if candidates:
        latest = max(candidates, key=lambda p: p.pick_no)
        out.mr_irrelevant_hero = {
            **_pick_payload(latest),
            "username": latest.username,
        }

    return out


def compute_dynasty_value_over_slot(
    picks: List[DraftPick],
    ktc_value_by_pid: Dict[str, float],
) -> None:
    """Dynasty variant of :func:`compute_value_over_slot` keyed off
    current KTC value instead of single-season fantasy points.

    Season finish is the wrong yardstick for dynasty drafts: a rookie
    WR who only sees 6 games in year 1 can still be a league-altering
    asset. What matters is the long-term value, which the KTC ranking
    proxies for. Concretely:

    * Rank all drafted players by their current (latest-known) KTC
      value, descending. That ranking is the pick's "actual" position
      among its draft class.
    * The "drafted" position is just ``pick_no`` (1-indexed overall
      draft slot) since dynasty drafts are typically rookie drafts
      where position-vs-position comparisons are noisier than overall
      asset ranking.
    * ``value_over_slot = (1/sqrt(ktc_rank) - 1/sqrt(pick_no)) * 100``.
      Positive = picked later than current value (steal). Negative =
      picked earlier than current value (bust).
    * Bust qualification mirrors the redraft path: requires
      ``ktc_rank - pick_no >= _BUST_MIN_RANK_DELTA``.

    The DraftPick fields are re-used: ``drafted_pos_rank`` stores
    pick_no and ``actual_pos_rank`` stores the KTC rank, so the same
    downstream accolade builder can consume both paths.
    """
    def _positional_value(rank: int) -> float:
        return 1.0 / math.sqrt(rank) if rank > 0 else 0.0

    # Rank all picks by current KTC value, descending. Picks with no
    # KTC record (rare: orphan ids) get pushed to the bottom.
    pids_with_value: List[Tuple[float, DraftPick]] = []
    for p in picks:
        val = float(ktc_value_by_pid.get(p.player_id, 0.0) or 0.0)
        pids_with_value.append((val, p))
    pids_with_value.sort(key=lambda tup: tup[0], reverse=True)
    for i, (_v, p) in enumerate(pids_with_value, start=1):
        p.actual_pos_rank = i
        p.drafted_pos_rank = p.pick_no  # 1:1 mapping in dynasty mode

    for p in picks:
        delta = _positional_value(p.actual_pos_rank) - _positional_value(p.drafted_pos_rank)
        p.value_over_slot = delta * 100.0
        rank_delta = p.actual_pos_rank - p.drafted_pos_rank
        if rank_delta >= _BUST_MIN_RANK_DELTA and p.value_over_slot < 0 and p.pick_no > 0:
            p.bust_score = abs(p.value_over_slot) / math.sqrt(p.pick_no)
        else:
            p.bust_score = 0.0


def _load_ktc_values_for_pids(
    pids: List[str],
    *,
    fmt: str,
) -> Dict[str, float]:
    """Look up the latest (most-recent date) KTC value for each Sleeper id.

    Returns ``{player_id: value}`` for ids that have any KTC history.
    Missing ids are simply absent from the result (caller treats them as
    zero), so callers can safely use ``dict.get(pid, 0.0)``.

    All errors are swallowed -- if the blob is missing the page still
    renders, just without KTC-driven dynasty scoring.
    """
    try:
        from app.services.wrapped.ktc_blob_loader import get_flat_blob

        flat, _meta = get_flat_blob(fmt)
        out: Dict[str, float] = {}
        for pid in pids:
            history = flat.get(pid)
            if not history:
                continue
            latest_date = max(history.keys())
            try:
                out[pid] = float(history[latest_date])
            except (TypeError, ValueError):
                continue
        return out
    except Exception as exc:  # noqa: BLE001 -- best-effort enrichment
        logger.warning(
            "KTC dynasty value lookup failed (%s); dynasty draft "
            "accolades will fall back to redraft scoring.", exc,
        )
        return {}


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

    if getattr(ctx, "is_dynasty", False):
        fmt = "superflex" if int(getattr(ctx, "num_qbs", 1) or 1) >= 2 else "1qb"
        pids = [p.player_id for p in picks]
        ktc_by_pid = _load_ktc_values_for_pids(pids, fmt=fmt)
        if ktc_by_pid:
            compute_dynasty_value_over_slot(picks, ktc_by_pid)
            return calculate_draft_accolades(picks)
        # KTC blob unavailable -- fall back to redraft scoring so the
        # section isn't blank.
        logger.info(
            "Dynasty league %s/%s: KTC values unavailable, falling back "
            "to season-points draft scoring.",
            ctx.league_id, ctx.year,
        )

    compute_value_over_slot(picks)
    return calculate_draft_accolades(picks)
