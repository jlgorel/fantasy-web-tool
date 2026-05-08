"""Parallel transactions fetcher for the Wrapped pipeline.

Builds a per-player transaction history out of Sleeper's
``/league/{id}/transactions/{week}`` endpoint. Sleeper exposes one transaction
list per week (regular season + playoff weeks), so we fan out a small
ThreadPoolExecutor and merge results.

Two complementary views are returned:

* ``player_transactions[pid]`` is the full ordered list of
  ``("Add"|"Drop", week, username)`` tuples for a player. Used by the
  worst-drop / late-drop accolades that need to know whether a user
  *eventually* let go of a player.
* ``last_added_by[pid]`` is the most recent ``(username, week_or_label)``
  add for a player, scanning newest week first. Used by the early-pickup
  and best-add accolades that care only about the current owner.

Trades are deliberately ignored here — they live in their own Phase-3
pipeline because their value calc requires FantasyCalc lookups and the
shape (multi-side adds + draft-pick assets) is fundamentally different.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from app.services.http_utils import fetch_json
from app.services.wrapped.league_context import LeagueContext

logger = logging.getLogger(__name__)


# Tuple shape: ("Add"|"Drop", week_int, username).
TransactionEvent = Tuple[str, int, str]


@dataclass
class TradeSide:
    """One manager's side of a trade."""

    username: str
    received_player_ids: List[str] = field(default_factory=list)
    received_picks: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class Trade:
    """A single completed trade between two or more managers.

    Sleeper's transaction shape is a flat ``adds``/``drops`` dict keyed on
    pid -> roster_id. We invert that into a ``sides`` map so the trade
    valuation pipeline can score each manager's haul independently.
    """

    week: int
    transaction_id: str
    sides: Dict[str, TradeSide] = field(default_factory=dict)


@dataclass
class LeagueTransactions:
    player_transactions: Dict[str, List[TransactionEvent]] = field(default_factory=lambda: defaultdict(list))
    # pid -> (username, week_int_or_label). Label is "Preseason" for drafted
    # players (Phase 3 will populate that). For Phase 2 we leave it empty
    # until we wire the draft endpoint in.
    last_added_by: Dict[str, Tuple[str, Any]] = field(default_factory=dict)
    # Phase 3 — completed trades. Empty list when a league has no trades.
    trades: List[Trade] = field(default_factory=list)


def _process_week_transactions(
    transactions: List[Dict[str, Any]],
    week: int,
    ctx: LeagueContext,
    out: LeagueTransactions,
) -> None:
    """Mutate ``out`` with one week's worth of transactions.

    Free-agent adds + completed waiver claims feed ``player_transactions``;
    completed trades feed ``out.trades`` (Phase 3). Pending waivers and
    failed/voided transactions are ignored.
    """
    for tx in transactions or []:
        tx_type = tx.get("type")
        if tx_type == "free_agent" or (tx_type == "waiver" and tx.get("status") == "complete"):
            roster_ids = tx.get("roster_ids") or []
            if not roster_ids:
                continue
            username = ctx.roster_id_to_username.get(roster_ids[0])
            if not username:
                continue

            drops = tx.get("drops") or {}
            for pid in drops.keys():
                out.player_transactions[pid].append(("Drop", week, username))

            adds = tx.get("adds") or {}
            for pid in adds.keys():
                out.player_transactions[pid].append(("Add", week, username))
        elif tx_type == "trade" and tx.get("status") == "complete":
            trade = _build_trade(tx, week, ctx)
            if trade is not None:
                out.trades.append(trade)


def _build_trade(
    tx: Dict[str, Any], week: int, ctx: LeagueContext
) -> Optional[Trade]:
    """Convert one Sleeper trade transaction into a ``Trade`` dataclass.

    Returns None for malformed trades (no roster_ids, unmappable rosters,
    or zero assets exchanged) — keeps the trade list clean.
    """
    roster_ids = tx.get("roster_ids") or []
    if len(roster_ids) < 2:
        return None

    sides: Dict[str, TradeSide] = {}
    for rid in roster_ids:
        username = ctx.roster_id_to_username.get(rid)
        if not username:
            return None
        sides.setdefault(username, TradeSide(username=username))

    # ``adds`` is keyed pid -> roster_id of the receiving team.
    for pid, rid in (tx.get("adds") or {}).items():
        username = ctx.roster_id_to_username.get(rid)
        if username and username in sides:
            sides[username].received_player_ids.append(str(pid))

    # ``draft_picks`` is a list with explicit owner_id (= receiving team).
    for pick in tx.get("draft_picks") or []:
        owner_id = pick.get("owner_id")
        username = ctx.roster_id_to_username.get(owner_id)
        if not username or username not in sides:
            continue
        sides[username].received_picks.append(
            {
                "season": str(pick.get("season") or ""),
                "round": int(pick.get("round") or 0),
                "original_roster_id": pick.get("roster_id"),
            }
        )

    # Drop trades where nobody actually received anything (defensive).
    if not any(s.received_player_ids or s.received_picks for s in sides.values()):
        return None

    return Trade(
        week=week,
        transaction_id=str(tx.get("transaction_id") or ""),
        sides=sides,
    )


def _resolve_last_added_by(out: LeagueTransactions) -> None:
    """Walk each pid's transaction list newest-first; first ``Add`` wins.

    A player who was added then dropped then re-added shows up in both
    rosters' transaction lists; whichever team most recently added them
    is the current owner per Sleeper's transaction stream.
    """
    for pid, events in out.player_transactions.items():
        for event_type, week, username in reversed(events):
            if event_type == "Add":
                out.last_added_by[pid] = (username, week)
                break


def fetch_league_transactions(ctx: LeagueContext) -> LeagueTransactions:
    """Fan out one fetch per regular-season week and merge into a
    ``LeagueTransactions``."""
    out = LeagueTransactions()
    weeks = list(range(1, ctx.last_regular_season_week + 1))
    if not weeks:
        return out

    def _fetch_one(week: int) -> Tuple[int, List[Dict[str, Any]]]:
        url = f"https://api.sleeper.app/v1/league/{ctx.league_id}/transactions/{week}"
        try:
            return week, (fetch_json(url) or [])
        except Exception as e:
            logger.warning("Wrapped transactions fetch failed for week %d: %s", week, e)
            return week, []

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(_fetch_one, weeks))

    # Process in week order so each pid's transaction list is naturally
    # chronological. Crucial for ``_resolve_last_added_by``.
    results.sort(key=lambda x: x[0])
    for week, transactions in results:
        _process_week_transactions(transactions, week, ctx, out)

    _resolve_last_added_by(out)
    return out
