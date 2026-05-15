"""Build evaluator ``Trade`` objects from normalized Sleeper trades.

Bridge module that takes the dataclasses from
:mod:`trade_eval.sleeper_trade_loader` (pure-data view of a Sleeper
trade) and produces :class:`trade_eval.trade_evaluator.Trade` objects
ready to feed into :func:`trade_eval.trade_evaluator.evaluate_trade`.

The mapping is mostly bookkeeping:

  * Player asset_id -> Sleeper player_id (matches the KTC blob, which
    keys players by Sleeper id).
  * Pick asset_id -> KTC blob pick id like ``pick:2024_mid_1st`` (via
    :func:`trade_eval.pick_handoff.pick_blob_id`), plus a packed
    pick-handoff key in ``sleeper_id`` so the pick-aware resolver can
    look up whether the pick has been used and splice in the drafted
    player.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Callable, Dict, List, Mapping, Optional

from .pick_handoff import PickTierConfig, encode_pick_key, pick_blob_id
from .sleeper_trade_loader import (
    NormalizedTrade, NormalizedSide, NormalizedPick, SeasonContext,
)
from .trade_evaluator import Trade, TradeAsset, TradeSide


# Player-id -> human display name. Caller supplies; we just look up.
PlayerNameMap = Mapping[str, str]
# roster_id -> display label
RosterLabelMap = Mapping[int, str]


def _resolve_pick_slot(
    pick: NormalizedPick,
    chain_by_season: Mapping[str, SeasonContext],
) -> Optional[int]:
    """Return the draft slot ``pick.original_roster_id`` will/did pick at
    in ``pick.season``, by reading the prior-season draft's
    ``slot_to_roster_id``. Falls back to None when the draft hasn't
    happened yet.
    """
    ctx = chain_by_season.get(pick.season)
    if ctx and ctx.draft:
        s2r = (ctx.draft.get("slot_to_roster_id") or {})
        for slot, rid in s2r.items():
            try:
                if int(rid) == pick.original_roster_id:
                    return int(slot)
            except (TypeError, ValueError):
                continue
    return None


def _pick_label(pick: NormalizedPick, roster_labels: RosterLabelMap) -> str:
    owner = roster_labels.get(pick.original_roster_id,
                              f"roster_{pick.original_roster_id}")
    return f"{pick.season} R{pick.round} (from {owner})"


def build_trade_asset_for_player(
    player_id: str,
    *,
    player_names: PlayerNameMap = {},
) -> TradeAsset:
    label = player_names.get(player_id, player_id)
    return TradeAsset(
        asset_id=str(player_id),
        label=label,
        sleeper_id=str(player_id),
        is_pick=False,
    )


def build_trade_asset_for_pick(
    pick: NormalizedPick,
    *,
    chain_by_season: Mapping[str, SeasonContext],
    roster_labels: RosterLabelMap,
    tier_config: PickTierConfig = PickTierConfig(),
) -> TradeAsset:
    slot = _resolve_pick_slot(pick, chain_by_season)
    blob_id = pick_blob_id(pick.season, pick.round, slot, tier_config=tier_config)
    return TradeAsset(
        asset_id=blob_id,
        label=_pick_label(pick, roster_labels),
        # Pack the lookup key in ``sleeper_id`` -- the pick-aware
        # resolver decodes it to find any realized draft pick.
        sleeper_id=encode_pick_key(
            pick.season, pick.round, pick.original_roster_id),
        is_pick=True,
    )


def build_trade(
    normalized: NormalizedTrade,
    *,
    chain_by_season: Mapping[str, SeasonContext],
    roster_labels: RosterLabelMap,
    player_names: PlayerNameMap = {},
    evaluation_end: Optional[date] = None,
    tier_config: PickTierConfig = PickTierConfig(),
) -> Trade:
    """Convert a NormalizedTrade into a Trade ready for evaluate_trade."""
    if evaluation_end is None:
        evaluation_end = datetime.now(timezone.utc).date()

    sides: List[TradeSide] = []
    for side in normalized.sides:
        assets: List[TradeAsset] = []
        for pid in side.received_player_ids:
            assets.append(build_trade_asset_for_player(
                pid, player_names=player_names))
        for pick in side.received_picks:
            assets.append(build_trade_asset_for_pick(
                pick, chain_by_season=chain_by_season,
                roster_labels=roster_labels, tier_config=tier_config))
        team_label = roster_labels.get(
            side.roster_id, f"roster_{side.roster_id}")
        sides.append(TradeSide(team_label=team_label, received_assets=assets))

    return Trade(
        trade_date=normalized.trade_date.date()
        if isinstance(normalized.trade_date, datetime)
        else normalized.trade_date,
        evaluation_end=evaluation_end,
        sides=sides,
    )


def merged_roster_labels(chain: List[SeasonContext]) -> Dict[int, str]:
    """Combine per-season roster-id labels into a single map.

    Roster ids are stable across the chain (dynasty leagues preserve
    roster_id). We prefer the *newest* season's label (current team
    name) and only fall back to older seasons for rosters that no longer
    exist in the latest.
    """
    out: Dict[int, str] = {}
    # newest first in chain already
    for ctx in chain:
        from .sleeper_trade_loader import roster_to_display_name  # local import to dodge cycle
        labels = roster_to_display_name(ctx)
        for rid, name in labels.items():
            out.setdefault(rid, name)
    return out


__all__ = [
    "PlayerNameMap",
    "RosterLabelMap",
    "build_trade_asset_for_player",
    "build_trade_asset_for_pick",
    "build_trade",
    "merged_roster_labels",
]
