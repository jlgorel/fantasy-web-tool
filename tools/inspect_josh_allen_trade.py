"""Inspect a real-world trade: Kupp + Edmonds + D. Cook + Garoppolo for
Josh Allen, October 2021.

Why this trade matters for the inspector:

* It looked like a slam-dunk win for the seller in the short term -- 4
  productive 2021 starters for one (then) ascending QB.
* Four-plus years later it's an obvious win the other way: Josh Allen is
  still elite, the other four are retired, traded around, or off the
  league entirely.
* That makes it a perfect test of the race-chart hypothesis: somewhere
  between Oct 2021 and today (May 2026), the cumulative value-time
  integral for the Allen side has to overtake the Kupp-Edmonds-Cook-
  Garoppolo side. Where, exactly, was the crossover?

Run::

    python tools/inspect_josh_allen_trade.py
    python tools/inspect_josh_allen_trade.py --k 1.4 --k 1.5

Prints a verdict summary at every k, the chart's crossover date(s), and
the running ktc_equiv every 4 weeks so you can eyeball the race.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, List

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "azure-functions"))

from trade_eval.trade_evaluator import (  # noqa: E402
    Trade, TradeAsset, TradeSide,
    build_race_chart, evaluate_trade, make_blob_resolver,
)

DEFAULT_BLOB = REPO / "tests" / "fixtures" / "blobs" / "historical_KTC_rankings.json"

# The actual trade.
TRADE_DATE = date(2021, 10, 15)
DEFAULT_END = date(2026, 5, 14)

# Player names as they appear in the blob's ``name`` field.
SIDE_RECEIVED_ALLEN = ["Josh Allen"]
SIDE_RECEIVED_PACKAGE = [
    "Cooper Kupp",
    "Chase Edmonds",
    "Dalvin Cook",
    "Jimmy Garoppolo",
]


def find_asset_id(blob_records: Dict[str, dict], player_name: str) -> str:
    """Locate the record key (== sleeper_id-ish string) for a player name."""
    for key, rec in blob_records.items():
        if rec.get("name", "").lower() == player_name.lower():
            return key
    raise SystemExit(f"FATAL: '{player_name}' not in blob.")


def build_flat_resolver(blob: dict, fmt: str):
    """Adapter: extract ``{asset_id: {date: val}}`` from the new blob shape."""
    key = "1QB_Historical" if fmt == "1qb" else "SF_Historical"
    flat = {
        record_key: rec[key]
        for record_key, rec in blob["records"].items()
        if rec.get(key)
    }
    return make_blob_resolver(flat)


def fmt_dollars(x: float) -> str:
    return f"{x:>+8,.0f}"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--blob", type=Path, default=DEFAULT_BLOB)
    ap.add_argument("--format", choices=["1qb", "superflex"], default="1qb")
    ap.add_argument("--end", type=lambda s: datetime.strptime(s, "%Y-%m-%d").date(),
                    default=DEFAULT_END, help="Evaluation end date.")
    ap.add_argument("--k", type=float, action="append",
                    help="Concavity exponent (repeatable). Default: 1.4, 1.5.")
    ap.add_argument("--sample-every", type=int, default=28,
                    help="Stride (days) for printing the running race "
                         "alongside the chart. Default 28 = ~monthly.")
    args = ap.parse_args()

    if not args.blob.exists():
        print(f"FATAL: blob not found: {args.blob}", file=sys.stderr)
        return 2
    ks = sorted(set(args.k)) if args.k else [1.4, 1.5]

    print(f"Loading {args.blob} ({args.blob.stat().st_size/1024/1024:,.1f} MB)...")
    blob = json.loads(args.blob.read_text(encoding="utf-8"))
    recs = blob["records"]
    resolver = build_flat_resolver(blob, args.format)

    # Resolve names -> asset keys.
    allen_id = find_asset_id(recs, SIDE_RECEIVED_ALLEN[0])
    package_ids = [find_asset_id(recs, n) for n in SIDE_RECEIVED_PACKAGE]

    trade = Trade(
        trade_date=TRADE_DATE,
        evaluation_end=args.end,
        sides=[
            TradeSide(team_label="Allen Side",
                      received_assets=[TradeAsset(allen_id, label="Josh Allen")]),
            TradeSide(team_label="Package Side",
                      received_assets=[
                          TradeAsset(pid, label=name)
                          for pid, name in zip(package_ids, SIDE_RECEIVED_PACKAGE)
                      ]),
        ],
    )

    print(f"\nTrade: {TRADE_DATE} -> {args.end}  ({args.format.upper()})")
    print("  Allen Side:    Josh Allen")
    print("  Package Side:  " + ", ".join(SIDE_RECEIVED_PACKAGE))
    print()

    for k in ks:
        print(f"{'='*78}")
        print(f"  k = {k:.2f}")
        print(f"{'='*78}")

        verdict = evaluate_trade(trade, value_resolver=resolver, k=k)
        chart = build_race_chart(
            trade, value_resolver=resolver, k=k, step_days=7,
        )
        a, p = chart.sides[0], chart.sides[1]
        a_final, p_final = a.points[-1], p.points[-1]

        print(f"\n  Verdict at {args.end}:  winner = {verdict.winner_label}")
        print(f"    Allen side    ktc_equiv = {a_final.ktc_equiv:>7,.0f}")
        print(f"    Package side  ktc_equiv = {p_final.ktc_equiv:>7,.0f}")
        print(f"    edge per season = {verdict.ktc_edge_per_season:>+8,.0f} KTC/yr")
        print(f"    edge total      = {verdict.ktc_edge_total:>+8,.0f} KTC "
              f"(over {verdict.active_days} active days "
              f"~ {verdict.active_days/216.0:.2f} seasons)")

        # The headline answer.
        if chart.crossover_dates:
            print(f"\n  Crossover dates (running leader changes):")
            for cd in chart.crossover_dates:
                print(f"    -> {cd}  "
                      f"({(cd - TRADE_DATE).days} days after the trade, "
                      f"{(cd - TRADE_DATE).days / 365.25:.2f} years)")
        else:
            print("\n  No crossover -- one side led every sampled day.")

        # Running race table at a coarser stride for readability.
        print(f"\n  Race (every ~{args.sample_every} days):")
        print(f"    {'date':<12}  {'Allen':>8}  {'Package':>8}  "
              f"{'leader':<14}  {'a_active':>8}")
        last_emit = None
        for i, (ap_, pp_) in enumerate(zip(a.points, p.points)):
            if last_emit is None or (ap_.date - last_emit).days >= args.sample_every \
                    or i == len(a.points) - 1:
                leader = ("Allen" if ap_.score > pp_.score
                          else "Package" if pp_.score > ap_.score
                          else "tie")
                print(f"    {ap_.date.isoformat():<12}  "
                      f"{ap_.ktc_equiv:>8,.0f}  "
                      f"{pp_.ktc_equiv:>8,.0f}  "
                      f"{leader:<14}  {ap_.active_days:>8d}")
                last_emit = ap_.date
        print()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
