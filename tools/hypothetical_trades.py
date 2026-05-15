"""Hypothetical trade calculator -- exercise the value-integral evaluator
against the new ``historical_KTC_rankings.json`` blob.

The point: sanity-check the "5 pieces of trash for 1 star" anti-pattern.
A pure linear value sum would score 5x150-value scrubs the same as 1x750
star. With the concavity exponent k=1.4 in :mod:`trade_eval.value_integral`,
the star should win convincingly. This script makes that visible.

For each scenario we evaluate the trade at three different concavity
exponents (1.0 = linear, 1.4 = default, 2.0 = extreme) so you can watch
the margin shift and decide if the default feels right.

Usage::

    python tools/hypothetical_trades.py                        # default scenarios
    python tools/hypothetical_trades.py --format superflex     # SF instead of 1QB
    python tools/hypothetical_trades.py --trade-date 2024-09-01  # custom window
    python tools/hypothetical_trades.py --list-stars 20        # show top-N from blob
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "azure-functions"))

from trade_eval.trade_evaluator import (  # noqa: E402
    Trade, TradeAsset, TradeSide, evaluate_trade, make_blob_resolver,
)

DEFAULT_BLOB = REPO / "tests" / "fixtures" / "blobs" / "historical_KTC_rankings.json"


# ---------------------------------------------------------------------------
# Blob adapter: new shape -> flat {asset_id: {date: val}}
# ---------------------------------------------------------------------------
HIST_KEY = {"1qb": "1QB_Historical", "superflex": "SF_Historical"}


def flatten_historical(
    blob: Dict[str, Any], *, fmt: str = "1qb"
) -> Tuple[Dict[str, Dict[str, float]], Dict[str, Dict[str, Any]]]:
    """Reshape the rolling historical blob into evaluator-friendly form.

    Returns ``(flat, name_index)`` where:
      * ``flat`` is ``{key: {YYYY-MM-DD: value}}`` ready for
        :func:`make_blob_resolver`.
      * ``name_index`` maps lowercased display names to ``{key, meta}``
        for the demo's name-based lookup.
    """
    if fmt not in HIST_KEY:
        raise ValueError(f"unknown format {fmt!r} (expected 1qb or superflex)")
    hist_field = HIST_KEY[fmt]

    records = blob.get("records") or {}
    flat: Dict[str, Dict[str, float]] = {}
    name_index: Dict[str, Dict[str, Any]] = {}

    for key, rec in records.items():
        hist = rec.get(hist_field) or {}
        if not hist:
            continue
        flat[key] = hist
        # Build a name lookup for picks (label) and players (name).
        display = rec.get("name") or rec.get("label") or key
        name_index[display.lower()] = {"key": key, "meta": rec}

    return flat, name_index


# ---------------------------------------------------------------------------
# Scenario construction
# ---------------------------------------------------------------------------
@dataclass
class Scenario:
    title: str
    description: str
    side_a_label: str
    side_a_names: List[str]
    side_b_label: str
    side_b_names: List[str]


def lookup_asset(name: str, name_index: Dict[str, Dict[str, Any]]) -> TradeAsset:
    """Resolve a display name to a :class:`TradeAsset`. Raises if missing."""
    entry = name_index.get(name.lower())
    if entry is None:
        raise SystemExit(
            f"\nERROR: '{name}' not in blob. Use --list-stars to browse, "
            f"or check the spelling."
        )
    rec = entry["meta"]
    is_pick = bool(rec.get("is_pick"))
    label = rec.get("name") or rec.get("label") or entry["key"]
    if not is_pick and rec.get("position"):
        label = f"{label} ({rec['position']})"
    return TradeAsset(
        asset_id=entry["key"],
        label=label,
        sleeper_id=rec.get("sleeper_id"),
        is_pick=is_pick,
    )


def default_scenarios() -> List[Scenario]:
    """The "is this evaluator sane?" battery.

    Names are chosen to be present in our blob from 2024-2026 and easy
    for a dynasty player to gut-check.
    """
    return [
        Scenario(
            title="5-for-1: Star vs Scrubs",
            description=(
                "The classic 'fleece offer'. Five fringe roster pieces "
                "shouldn't beat one elite WR1, even though their summed "
                "raw value approaches his."
            ),
            side_a_label="Star Hoarder",
            side_a_names=["Justin Jefferson"],
            side_b_label="Quantity Merchant",
            side_b_names=[
                "Khalil Herbert",
                "Romeo Doubs",
                "Dameon Pierce",
                "Cedric Tillman",
                "Tyjae Spears",
            ],
        ),
        Scenario(
            title="3-for-2: Star+Pick vs Two-Star Package",
            description=(
                "A more realistic dynasty fleece -- you give up a top-12 "
                "asset plus a 1st for two mid-WR1 players."
            ),
            side_a_label="Consolidator",
            side_a_names=["CeeDee Lamb", "2026 Mid 1st"],
            side_b_label="Spreader",
            side_b_names=["DK Metcalf", "Garrett Wilson"],
        ),
        Scenario(
            title="2-for-1: Mid-RB1 + scrub vs Elite RB",
            description=(
                "Tests whether two-for-one even in a *position* premium "
                "(RB) gets the right answer when one side is far stronger."
            ),
            side_a_label="Star Hoarder",
            side_a_names=["Bijan Robinson"],
            side_b_label="Volume Merchant",
            side_b_names=["James Cook", "Tyjae Spears"],
        ),
        Scenario(
            title="Pure linear test: 1 star vs n*small",
            description=(
                "If we tune n high enough, a perfectly linear evaluator "
                "would call this even. With k=1.4 it shouldn't be close."
            ),
            side_a_label="Star",
            side_a_names=["Ja'Marr Chase"],
            side_b_label="Six scrubs",
            side_b_names=[
                "Khalil Herbert", "Romeo Doubs", "Cedric Tillman",
                "Tyjae Spears", "Dameon Pierce", "Tank Bigsby",
            ],
        ),
    ]


# ---------------------------------------------------------------------------
# Evaluation driver
# ---------------------------------------------------------------------------
def run_scenario(
    sc: Scenario,
    *,
    trade_date: date,
    evaluation_end: date,
    flat: Dict[str, Dict[str, float]],
    name_index: Dict[str, Dict[str, Any]],
    ks: List[float],
) -> None:
    print(f"\n{'='*78}")
    print(f"  {sc.title}")
    print(f"{'='*78}")
    print(f"  {sc.description}")
    print(f"  window: {trade_date} -> {evaluation_end}")

    a_assets = [lookup_asset(n, name_index) for n in sc.side_a_names]
    b_assets = [lookup_asset(n, name_index) for n in sc.side_b_names]

    print(f"\n  {sc.side_a_label}:")
    for a in a_assets:
        print(f"    + {a.label}")
    print(f"  {sc.side_b_label}:")
    for a in b_assets:
        print(f"    + {a.label}")

    resolver = make_blob_resolver(flat)
    # Each side's ``received_assets`` is what THEY end up holding -- so
    # ``side_a_names`` (e.g. "Justin Jefferson") belongs to side A. The
    # margin tells us whether the side ended up better off after the swap.
    trade = Trade(
        trade_date=trade_date,
        evaluation_end=evaluation_end,
        sides=[
            TradeSide(team_label=sc.side_a_label, received_assets=a_assets),
            TradeSide(team_label=sc.side_b_label, received_assets=b_assets),
        ],
    )

    # Per-k comparison row.
    print(f"\n  {'k':>4s}  "
          f"{sc.side_a_label[:22]:>22s}  "
          f"{sc.side_b_label[:22]:>22s}  "
          f"{'margin (A-B)':>14s}  "
          f"{'KTC/yr':>9s}  {'KTC total':>10s}  winner")
    print(f"  {'-'*4}  {'-'*22}  {'-'*22}  {'-'*14}  "
          f"{'-'*9}  {'-'*10}  {'-'*15}")
    for k in ks:
        result = evaluate_trade(trade, value_resolver=resolver, k=k)
        sa = next(s for s in result.sides if s.team_label == sc.side_a_label)
        sb = next(s for s in result.sides if s.team_label == sc.side_b_label)
        margin = sa.total_score - sb.total_score
        winner = result.winner_label or "(tie)"
        # Sign the readable edge by which side wins (A positive, B negative).
        signed_per_season = result.ktc_edge_per_season * (
            1 if (winner == sc.side_a_label) else
            -1 if (winner == sc.side_b_label) else 0
        )
        signed_total = result.ktc_edge_total * (
            1 if (winner == sc.side_a_label) else
            -1 if (winner == sc.side_b_label) else 0
        )
        print(f"  {k:>4.2f}  "
              f"{sa.total_score:>22,.0f}  "
              f"{sb.total_score:>22,.0f}  "
              f"{margin:>+14,.0f}  "
              f"{signed_per_season:>+9,.0f}  "
              f"{signed_total:>+10,.0f}  "
              f"{winner}")

    # Always print per-asset breakdown at the default k.
    print(f"\n  Per-asset breakdown (k={ks[0]:.2f}):")
    result = evaluate_trade(trade, value_resolver=resolver, k=ks[0])
    for side in result.sides:
        print(f"    {side.team_label}:")
        for ev in side.asset_evaluations:
            warn = "  <-- ZERO (asset not in blob window)" if ev.total_score == 0 else ""
            print(
                f"      - {ev.asset.label:<40s} "
                f"score={ev.total_score:>14,.0f}  "
                f"avg_ktc={ev.avg_ktc:>5,.0f}  "
                f"active_days={ev.integral.active_days:>4d}{warn}"
            )


# ---------------------------------------------------------------------------
# Browse helpers
# ---------------------------------------------------------------------------
def list_top_stars(
    flat: Dict[str, Dict[str, float]],
    name_index: Dict[str, Dict[str, Any]],
    *,
    n: int,
    as_of: date,
) -> None:
    """Print the top-N players by KTC value on ``as_of`` so the user can
    pick names that actually exist in the blob for their own scenarios."""
    rows: List[Tuple[float, str, str]] = []
    as_of_str = as_of.isoformat()
    for name_lc, entry in name_index.items():
        rec = entry["meta"]
        if rec.get("is_pick"):
            continue
        hist = flat.get(entry["key"]) or {}
        if not hist:
            continue
        # Forward-fill: find latest date <= as_of.
        latest = max((d for d in hist if d <= as_of_str), default=None)
        if latest is None:
            continue
        val = hist[latest]
        if val <= 0:
            continue
        label = rec.get("name") or name_lc
        pos = rec.get("position") or "??"
        rows.append((val, f"{label} ({pos})", latest))
    rows.sort(reverse=True)
    print(f"\nTop {n} players by KTC value on {as_of}:")
    for val, label, ldate in rows[:n]:
        print(f"  {val:>6.0f}  {label:<40s}  (as-of {ldate})")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def _parse_date(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--blob", type=Path, default=DEFAULT_BLOB,
                    help=f"Local historical blob (default: {DEFAULT_BLOB})")
    ap.add_argument("--format", choices=["1qb", "superflex"], default="1qb")
    ap.add_argument("--trade-date", type=_parse_date, default=date(2025, 5, 14),
                    help="When the hypothetical trade occurred. "
                         "Default: 2025-05-14 (1 year before today).")
    ap.add_argument("--evaluation-end", type=_parse_date, default=date(2026, 5, 14),
                    help="When to stop integrating. Default: 2026-05-14 (today).")
    ap.add_argument("--k", type=float, action="append",
                    help="Concavity exponent to test (repeatable). "
                         "Default tests 1.0, 1.4, 2.0.")
    ap.add_argument("--list-stars", type=int, metavar="N",
                    help="Print top-N players by value on --trade-date and exit.")
    args = ap.parse_args()

    if not args.blob.exists():
        print(f"FATAL: blob not found at {args.blob}.\n"
              f"       Run tools/build_historical_ktc_json.py first.",
              file=sys.stderr)
        return 2

    print(f"Loading {args.blob} ({args.blob.stat().st_size/1024/1024:,.1f} MB)...")
    blob = json.loads(args.blob.read_text(encoding="utf-8"))
    flat, name_index = flatten_historical(blob, fmt=args.format)
    print(f"  {len(flat):,} records with {args.format.upper()} history "
          f"({len(name_index):,} unique names)")

    if args.list_stars:
        list_top_stars(flat, name_index, n=args.list_stars, as_of=args.trade_date)
        return 0

    ks = sorted(set(args.k)) if args.k else [1.0, 1.4, 2.0]

    for sc in default_scenarios():
        try:
            run_scenario(
                sc,
                trade_date=args.trade_date,
                evaluation_end=args.evaluation_end,
                flat=flat,
                name_index=name_index,
                ks=ks,
            )
        except SystemExit as e:
            # Name not found -- report and continue rather than aborting
            # the whole run.
            print(str(e), file=sys.stderr)

    print(f"\n{'='*78}")
    print("  Done. Concavity k=1.4 is the in-code default; values >1.0")
    print("  amplify stars over quantity. The margin column shows the")
    print("  raw point-spread the evaluator assigns to each side.")
    print(f"{'='*78}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
