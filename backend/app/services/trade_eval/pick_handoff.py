"""Pick -> drafted-player handoff for the trade evaluator.

Thought 3 from the design notes: when a draft pick has been used, its
value time series should switch from the *pick's* KTC line to the
*drafted player's* KTC line on draft day. A 2024 1st that was used on
Marvin Harrison Jr. should be evaluated as the pick up until draft day
and as MHJ from draft day forward -- splicing those two series gives the
correct integral without any policy hacks.

This module is pure: it takes a base value-resolver, a pick-handoff
table (produced by :func:`trade_eval.sleeper_trade_loader.build_pick_to_player`),
and returns a wrapped resolver that does the splicing on demand.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Callable, Dict, Optional, Tuple

from .trade_evaluator import TradeAsset, ValueResolver
from .value_integral import ValueSeries


PickHandoffTable = Dict[Tuple[str, int, int], Dict[str, Any]]


# ---------------------------------------------------------------------------
# Pure splicing primitive
# ---------------------------------------------------------------------------
def splice_series(
    pre: ValueSeries,
    post: ValueSeries,
    cutoff: date,
) -> ValueSeries:
    """Return a new ValueSeries that uses ``pre`` strictly before
    ``cutoff`` and ``post`` from ``cutoff`` onward.

    Implementation: walk both inputs' sorted dates, keep ``pre`` samples
    with ``d < cutoff``, and prepend a synthetic ``cutoff`` sample. The
    synthetic sample forward-fills the drafted player's value when it has
    historical data on or before draft day; otherwise it preserves the last
    pick value until the player first appears in KTC. Then keep all ``post``
    samples with ``d >= cutoff``.

    The ``initial_value`` and stale-handling are inherited from ``pre``
    (asset existed as a pick before its drafted player did).
    """
    pre_dates = [d for d in pre.sorted_dates if d < cutoff]
    pre_values = [pre.values[i] for i, d in enumerate(pre.sorted_dates)
                  if d < cutoff]

    # Post values on/after cutoff. If the drafted player has no KTC sample yet
    # at the cutoff, retain the pick's last known value until KTC first lists
    # the player; never backdate a later player value into the draft date.
    post_dates = [d for d in post.sorted_dates if d >= cutoff]
    post_values = [post.values[i] for i, d in enumerate(post.sorted_dates)
                   if d >= cutoff]
    if not post_dates or post_dates[0] != cutoff:
        # Drafted player may not have a KTC entry on the exact draft day.
        # ``value_on`` only uses samples at or before cutoff, so it cannot
        # accidentally apply a later player value retroactively.
        has_post_sample_by_cutoff = bool(
            post.sorted_dates and post.sorted_dates[0] <= cutoff
        )
        seed_value = post.value_on(cutoff)
        if not has_post_sample_by_cutoff and pre_values:
            seed_value = pre_values[-1]
        post_dates = [cutoff] + post_dates
        post_values = [seed_value] + post_values

    spliced_dates = pre_dates + post_dates
    spliced_values = pre_values + post_values
    return ValueSeries(
        sorted_dates=spliced_dates,
        values=spliced_values,
        initial_value=pre.initial_value,
        max_stale_days=pre.max_stale_days,
        stale_value=pre.stale_value,
    )


# ---------------------------------------------------------------------------
# Resolver wrapping
# ---------------------------------------------------------------------------
def _coerce_cutoff(draft_date: Any) -> Optional[date]:
    if draft_date is None:
        return None
    if isinstance(draft_date, date) and not isinstance(draft_date, datetime):
        return draft_date
    if isinstance(draft_date, datetime):
        return draft_date.astimezone(timezone.utc).date()
    if isinstance(draft_date, str):
        try:
            return datetime.fromisoformat(draft_date.replace("Z", "+00:00")).date()
        except ValueError:
            return None
    return None


def make_pick_aware_resolver(
    base_resolver: ValueResolver,
    pick_table: PickHandoffTable,
) -> ValueResolver:
    """Wrap ``base_resolver`` so that pick assets get spliced into the
    drafted player on draft day, when the pick has been used.

    Asset convention: a pick asset must have ``is_pick=True`` and carry
    the following entries in ``asset.label`` *parsed* metadata? No --
    Python frozen dataclass doesn't permit adding fields. We instead
    encode the lookup key in ``asset.asset_id`` using the synthetic id
    convention ``pick:<season>_<tier>_<round_suffix>`` for the *blob*
    lookup and stash the (season, round, original_roster_id) tuple
    inside ``asset.sleeper_id`` slot using the string format
    ``"pickkey:<season>:<round>:<original_roster_id>"``.

    The Sleeper trade loader builds assets with this convention so the
    same resolver chain works in both the unit tests (where assets are
    constructed by hand) and the real-league smoke test.

    Picks that aren't in ``pick_table`` (future picks, picks from drafts
    that haven't happened yet) fall through to the base resolver
    unchanged. That's the right behavior -- their value series is just
    the pick's KTC line.
    """
    # Multiple actual picks can share a generic KTC blob id such as
    # ``pick:2023_mid_2nd``. Their ``sleeper_id`` carries the original-roster
    # pick key and distinguishes which drafted player the generic pick became.
    # Cache each realized pick separately so one cannot inherit another's
    # post-draft player series.
    cache: Dict[Tuple[str, Optional[str]], ValueSeries] = {}

    def resolver(asset: TradeAsset) -> ValueSeries:
        cache_key = (
            asset.asset_id,
            asset.sleeper_id if asset.is_pick else None,
        )
        if cache_key in cache:
            return cache[cache_key]
        base_series = base_resolver(asset)
        if not asset.is_pick:
            cache[cache_key] = base_series
            return base_series

        key = parse_pick_key(asset.sleeper_id)
        if key is None:
            cache[cache_key] = base_series
            return base_series

        info = pick_table.get(key)
        if not info:
            cache[cache_key] = base_series
            return base_series

        cutoff = _coerce_cutoff(info.get("draft_date"))
        drafted_player_id = info.get("player_id")
        if cutoff is None or not drafted_player_id:
            cache[cache_key] = base_series
            return base_series

        # Pull the drafted player's series via the base resolver.
        player_asset = TradeAsset(
            asset_id=str(drafted_player_id),
            label=f"drafted={drafted_player_id}",
            sleeper_id=str(drafted_player_id),
            is_pick=False,
        )
        player_series = base_resolver(player_asset)
        spliced = splice_series(base_series, player_series, cutoff)
        cache[cache_key] = spliced
        return spliced

    return resolver


# ---------------------------------------------------------------------------
# Pick-key (de)serialization
# ---------------------------------------------------------------------------
def encode_pick_key(season: str, round_: int, original_roster_id: int) -> str:
    """Pack (season, round, original_roster_id) into a single string the
    TradeAsset can carry through the evaluator without needing extra
    fields."""
    return f"pickkey:{season}:{round_}:{original_roster_id}"


def parse_pick_key(raw: Optional[str]) -> Optional[Tuple[str, int, int]]:
    if not raw or not raw.startswith("pickkey:"):
        return None
    parts = raw.split(":")
    if len(parts) != 4:
        return None
    try:
        return parts[1], int(parts[2]), int(parts[3])
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Flat-blob helper (KTC ingest writes nested {players, picks})
# ---------------------------------------------------------------------------
# Map ``fmt`` -> field name in the new ``historical_KTC_rankings.json`` blob.
_NEW_FMT_TO_FIELD = {
    "1qb": "1QB_Historical",
    "superflex": "SF_Historical",
}


def flatten_value_blob(
    blob: Dict[str, Any], *, fmt: str = "1qb",
) -> Dict[str, Dict[str, float]]:
    """Convert a KTC value blob into the flat ``{asset_id: {date: value}}``
    shape that :func:`trade_eval.trade_evaluator.make_blob_resolver` expects.

    Two blob layouts are supported:

      * **New canonical** (``trade_eval/values/ktc/historical_KTC_rankings.json``):
        ``{"records": {"<key>": {"1QB_Historical": {date: v}, "SF_Historical":
        {date: v}, ...}}}``. Built by ``tools/build_historical_ktc_json.py``
        and appended daily by :mod:`trade_eval.ktc_top500_daily`. Use the
        ``fmt`` kwarg ("1qb" or "superflex") to pick which series to expose.

      * **Legacy nested** (``trade_eval_ktc_history_<fmt>.json``):
        ``{"players": {"<sleeper_id>": {"values": {date: v}}},
        "picks": {"<pick_id>": {"values": {date: v}}}}``. Written by the
        retired ``tools/ingest_ktc_history.py`` CLI. ``fmt`` is ignored
        for this shape since each legacy file is already format-specific.

    Also tolerates a pre-wrapped player/pick payload that's already a
    flat ``{date: value}`` dict, for hand-crafted test fixtures.
    """
    # ---- New canonical shape: top-level "records" map -----------------
    if isinstance(blob.get("records"), dict):
        field = _NEW_FMT_TO_FIELD.get(fmt)
        if field is None:
            raise ValueError(
                f"unknown fmt {fmt!r} (expected one of {list(_NEW_FMT_TO_FIELD)})"
            )
        flat: Dict[str, Dict[str, float]] = {}
        for key, rec in blob["records"].items():
            if not isinstance(rec, dict):
                continue
            hist = rec.get(field)
            if isinstance(hist, dict) and hist:
                flat[str(key)] = dict(hist)
        return flat

    # ---- Legacy nested shape -----------------------------------------
    def _values_of(payload: Any) -> Optional[Dict[str, float]]:
        if not isinstance(payload, dict):
            return None
        if "values" in payload and isinstance(payload["values"], dict):
            return payload["values"]
        # Already-flat shape: every key looks like a YYYY-MM-DD string.
        if payload and all(
            isinstance(k, str) and len(k) == 10 and k[4] == "-"
            for k in payload.keys()
        ):
            return payload  # type: ignore[return-value]
        return None

    flat = {}
    for pid, payload in (blob.get("players") or {}).items():
        values = _values_of(payload)
        if values:
            flat[str(pid)] = dict(values)
    for pick_id, payload in (blob.get("picks") or {}).items():
        values = _values_of(payload)
        if values:
            flat[str(pick_id)] = dict(values)
    return flat


# ---------------------------------------------------------------------------
# Pick-tier descriptor builders
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class PickTierConfig:
    """How to bucket draft slots into early/mid/late tiers for the KTC
    pick-id convention. Defaults are tuned for a 10-team league with
    slots 1-3 early, 4-7 mid, 8-10 late, matching the historical CSV.
    """
    n_teams: int = 10
    early_slots: int = 3       # slots 1..3 -> "early"
    mid_slots: int = 4         # slots 4..7 -> "mid"
    # late = remainder

    def slot_to_tier(self, slot: int) -> str:
        if slot <= self.early_slots:
            return "early"
        if slot <= self.early_slots + self.mid_slots:
            return "mid"
        return "late"


_ROUND_SUFFIX = {1: "1st", 2: "2nd", 3: "3rd", 4: "4th", 5: "5th"}


def pick_blob_id(
    season: str, round_: int, slot: Optional[int],
    *, tier_config: PickTierConfig = PickTierConfig(),
) -> str:
    """Build the ``pick:YYYY_tier_Nth`` id used in the KTC blob.

    When ``slot`` is unknown (future pick, no draft yet) we conservatively
    return the ``mid`` tier -- it's the league-average KTC and avoids
    biasing the eval before the pick is realized.
    """
    suffix = _ROUND_SUFFIX.get(round_, f"{round_}th")
    if slot is None:
        tier = "mid"
    else:
        tier = tier_config.slot_to_tier(slot)
    return f"pick:{season}_{tier}_{suffix}"


__all__ = [
    "PickHandoffTable",
    "PickTierConfig",
    "splice_series",
    "make_pick_aware_resolver",
    "encode_pick_key",
    "parse_pick_key",
    "flatten_value_blob",
    "pick_blob_id",
]
