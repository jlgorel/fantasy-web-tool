"""Sleeper league trade loader.

Pulls a dynasty league's full multi-season history (via the
``previous_league_id`` chain) and normalizes every completed trade into a
shape the trade evaluator can consume.

The HTTP transport is injected as a callable so tests pass saved JSON
fixtures and prod code passes a real ``urllib`` / ``requests`` adapter.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

HttpGetJson = Callable[[str], Any]

# Sleeper exposes most reads on api.sleeper.app/v1/...
_BASE = "https://api.sleeper.app/v1"


# ---------------------------------------------------------------------------
# Raw fetchers
# ---------------------------------------------------------------------------
def fetch_league(http: HttpGetJson, league_id: str) -> Dict[str, Any]:
    return http(f"{_BASE}/league/{league_id}")


def fetch_rosters(http: HttpGetJson, league_id: str) -> List[Dict[str, Any]]:
    return http(f"{_BASE}/league/{league_id}/rosters") or []


def fetch_users(http: HttpGetJson, league_id: str) -> List[Dict[str, Any]]:
    return http(f"{_BASE}/league/{league_id}/users") or []


def fetch_draft(http: HttpGetJson, draft_id: str) -> Dict[str, Any]:
    return http(f"{_BASE}/draft/{draft_id}")


def fetch_draft_picks(http: HttpGetJson, draft_id: str) -> List[Dict[str, Any]]:
    return http(f"{_BASE}/draft/{draft_id}/picks") or []


def fetch_week_transactions(
    http: HttpGetJson, league_id: str, week: int
) -> List[Dict[str, Any]]:
    return http(f"{_BASE}/league/{league_id}/transactions/{week}") or []


# ---------------------------------------------------------------------------
# League chain
# ---------------------------------------------------------------------------
@dataclass
class SeasonContext:
    """Everything we need for one season of a dynasty chain."""
    season: str
    league_id: str
    draft_id: Optional[str]
    league: Dict[str, Any]
    rosters: List[Dict[str, Any]]
    users: List[Dict[str, Any]]
    draft: Optional[Dict[str, Any]]
    draft_picks: List[Dict[str, Any]]
    trades: List[Dict[str, Any]]


def load_league_chain(
    starting_league_id: str,
    *,
    http: HttpGetJson,
    weeks_to_scan: range = range(0, 19),
) -> List[SeasonContext]:
    """Walk backwards from ``starting_league_id`` via ``previous_league_id``
    and load every season of context. Returns newest-first.
    """
    seasons: List[SeasonContext] = []
    lid: Optional[str] = starting_league_id
    while lid:
        league = fetch_league(http, lid)
        draft_id = league.get("draft_id")
        rosters = fetch_rosters(http, lid)
        users = fetch_users(http, lid)
        draft = fetch_draft(http, draft_id) if draft_id else None
        draft_picks = fetch_draft_picks(http, draft_id) if draft_id else []
        trades: List[Dict[str, Any]] = []
        for week in weeks_to_scan:
            try:
                txns = fetch_week_transactions(http, lid, week)
            except Exception:
                continue
            for t in txns:
                if t.get("type") == "trade" and t.get("status") == "complete":
                    trades.append(t)
        seasons.append(SeasonContext(
            season=str(league.get("season")),
            league_id=lid,
            draft_id=draft_id,
            league=league,
            rosters=rosters,
            users=users,
            draft=draft,
            draft_picks=draft_picks,
            trades=trades,
        ))
        lid = league.get("previous_league_id")
    return seasons


# ---------------------------------------------------------------------------
# Roster <-> user lookup (display names)
# ---------------------------------------------------------------------------
def roster_to_display_name(season_ctx: SeasonContext) -> Dict[int, str]:
    """Map roster_id -> a human label (team_name or display_name)."""
    user_by_id = {u.get("user_id"): u for u in season_ctx.users}
    out: Dict[int, str] = {}
    for r in season_ctx.rosters:
        rid = r.get("roster_id")
        owner = user_by_id.get(r.get("owner_id"))
        if not owner:
            out[rid] = f"roster_{rid}"
            continue
        # Team name (set in league) takes priority over display name.
        team_name = (owner.get("metadata") or {}).get("team_name")
        label = team_name or owner.get("display_name") or f"roster_{rid}"
        out[rid] = label
    return out


# ---------------------------------------------------------------------------
# Normalized trade representation
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class NormalizedPick:
    """Pick descriptor in a normalized trade."""
    season: str
    round: int
    original_roster_id: int


@dataclass
class NormalizedSide:
    """One side of a normalized trade -- what this roster *received*."""
    roster_id: int
    received_player_ids: List[str] = field(default_factory=list)
    received_picks: List[NormalizedPick] = field(default_factory=list)


@dataclass
class NormalizedTrade:
    """A trade normalized for evaluator consumption.

    ``trade_date`` is built from Sleeper's ``status_updated`` (preferred,
    when the trade actually completed) or ``created`` as a fallback. Both
    are unix milliseconds in Sleeper land.
    """
    trade_id: str
    season: str
    league_id: str
    leg: int
    trade_date: datetime
    sides: List[NormalizedSide]


def _ms_to_dt(ms: Optional[int]) -> Optional[datetime]:
    if ms is None:
        return None
    return datetime.fromtimestamp(int(ms) / 1000.0, tz=timezone.utc)


def normalize_trade(
    raw: Dict[str, Any], *, season: str, league_id: str
) -> Optional[NormalizedTrade]:
    """Convert one Sleeper trade payload into a NormalizedTrade.

    Returns ``None`` if the trade is malformed (no rosters, no assets).
    """
    roster_ids = raw.get("roster_ids") or []
    if not roster_ids:
        return None

    # Initialize per-roster side.
    sides: Dict[int, NormalizedSide] = {
        int(rid): NormalizedSide(roster_id=int(rid)) for rid in roster_ids
    }

    # Players: adds = {player_id: roster_id} of the receiver.
    for pid, rid in (raw.get("adds") or {}).items():
        rid = int(rid)
        sides.setdefault(rid, NormalizedSide(roster_id=rid)).received_player_ids.append(str(pid))

    # Picks. In Sleeper trade payloads:
    #   roster_id          = the *original* owning roster -- determines
    #                        the draft slot. This is the pick's identity.
    #   previous_owner_id  = whoever held the pick right before this trade.
    #   owner_id           = whoever holds the pick after this trade.
    # The receiver in *this* trade is ``owner_id``; the pick's identity
    # for draft-slot lookup is ``roster_id``. Conflating roster_id and
    # previous_owner_id silently collapses two distinct picks (e.g.,
    # Messiah's own 2023 1st and Roster 7's 2023 1st that Messiah held)
    # into a single asset, so we keep them separate.
    for pick in (raw.get("draft_picks") or []):
        new_owner_raw = pick.get("owner_id")
        if new_owner_raw is None:
            continue
        try:
            new_owner = int(new_owner_raw)
            original = int(pick.get("roster_id"))
            rnd = int(pick.get("round"))
        except (TypeError, ValueError):
            continue
        season_str = str(pick.get("season"))
        sides.setdefault(new_owner, NormalizedSide(roster_id=new_owner)).received_picks.append(
            NormalizedPick(season=season_str, round=rnd, original_roster_id=original)
        )

    # Drop trivial sides (received nothing). Shouldn't happen in real
    # Sleeper data but guard anyway.
    side_list = [s for s in sides.values()
                 if s.received_player_ids or s.received_picks]
    if len(side_list) < 2:
        return None

    when = _ms_to_dt(raw.get("status_updated")) or _ms_to_dt(raw.get("created"))
    if when is None:
        return None

    return NormalizedTrade(
        trade_id=str(raw.get("transaction_id") or ""),
        season=season,
        league_id=league_id,
        leg=int(raw.get("leg") or 0),
        trade_date=when,
        sides=side_list,
    )


def normalize_all_trades(chain: List[SeasonContext]) -> List[NormalizedTrade]:
    """Normalize every completed trade across the chain (newest first)."""
    out: List[NormalizedTrade] = []
    for ctx in chain:
        for raw in ctx.trades:
            nt = normalize_trade(raw, season=ctx.season, league_id=ctx.league_id)
            if nt is not None:
                out.append(nt)
    return out


# ---------------------------------------------------------------------------
# Pick -> realized-player lookup table
# ---------------------------------------------------------------------------
def build_pick_to_player(
    chain: List[SeasonContext],
) -> Dict[Tuple[str, int, int], Dict[str, Any]]:
    """Build a lookup ``{(season, round, original_roster_id) -> info}``.

    ``info`` shape::

        {
          "player_id": "<sleeper_id>",
          "pick_no": 12,
          "draft_slot": 2,
          "draft_date": datetime,   # status_updated of draft, or last_picked
          "drafted_first": "Marvin", "drafted_last": "Harrison",
        }

    Picks whose drafts haven't happened yet are simply absent from the
    table. The evaluator's pick-aware resolver then falls back to the
    plain pick KTC series for those.
    """
    out: Dict[Tuple[str, int, int], Dict[str, Any]] = {}
    for ctx in chain:
        if not ctx.draft or not ctx.draft_picks:
            continue
        draft = ctx.draft
        slot_to_roster = draft.get("slot_to_roster_id") or {}
        # Reverse: original_roster_id (int) -> draft_slot (int).
        roster_to_slot: Dict[int, int] = {
            int(rid): int(slot) for slot, rid in slot_to_roster.items()
        }
        # Pull a single "draft completed at" datetime. ``last_picked`` is
        # the most accurate; fall back to start_time. Both are unix ms.
        draft_dt = (_ms_to_dt(draft.get("last_picked"))
                    or _ms_to_dt(draft.get("start_time")))
        # Index realized picks by (round, draft_slot) -- both ints.
        picks_by_slot: Dict[Tuple[int, int], Dict[str, Any]] = {}
        for p in ctx.draft_picks:
            try:
                rnd = int(p.get("round"))
                slot = int(p.get("draft_slot"))
            except (TypeError, ValueError):
                continue
            picks_by_slot[(rnd, slot)] = p

        for orig_rid, slot in roster_to_slot.items():
            for rnd in range(1, int(draft.get("settings", {}).get("rounds") or 0) + 1):
                pick = picks_by_slot.get((rnd, slot))
                if not pick or not pick.get("player_id"):
                    continue
                meta = pick.get("metadata") or {}
                out[(str(ctx.season), rnd, orig_rid)] = {
                    "player_id": str(pick["player_id"]),
                    "pick_no": pick.get("pick_no"),
                    "draft_slot": slot,
                    "draft_date": draft_dt,
                    "drafted_first": meta.get("first_name"),
                    "drafted_last": meta.get("last_name"),
                    "drafted_position": meta.get("position"),
                }
    return out


__all__ = [
    "HttpGetJson",
    "SeasonContext",
    "fetch_league",
    "fetch_rosters",
    "fetch_users",
    "fetch_draft",
    "fetch_draft_picks",
    "fetch_week_transactions",
    "load_league_chain",
    "roster_to_display_name",
    "NormalizedPick",
    "NormalizedSide",
    "NormalizedTrade",
    "normalize_trade",
    "normalize_all_trades",
    "build_pick_to_player",
]
