"""End-to-end smoke for the new Wrapped trades section + inspector.

Drives the same code path the Flask route would, but skips Redis +
Azure-blob lookups by:

  * Pointing ``KTC_HISTORICAL_BLOB_PATH`` at the local JSON fixture
    (``tests/fixtures/blobs/historical_KTC_rankings.json``).
  * Loading the Sleeper league context + transactions live (Sleeper's
    REST API is public, no auth needed).
  * Using an empty ``players_meta`` so we don't pull the 25MB players
    blob -- the integral evaluator and labels both fall back gracefully
    when sleeper meta is missing.

Default league is "The Nerd Herd" (jlgorel's 1QB dynasty, 2024 season).
Run with::

    python tools/smoke_wrapped_trades.py
    python tools/smoke_wrapped_trades.py --league 1048698084680695808
    python tools/smoke_wrapped_trades.py --inspect <transaction_id>
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
# Point the ktc_blob_loader at the local fixture BEFORE importing it so
# its module-level cache picks up the override.
os.environ.setdefault(
    "KTC_HISTORICAL_BLOB_PATH",
    str(REPO_ROOT / "tests" / "fixtures" / "blobs" / "historical_KTC_rankings.json"),
)

sys.path.insert(0, str(REPO_ROOT / "backend"))

from app.services.wrapped.league_context import load_league_context  # noqa: E402
from app.services.wrapped.transactions import fetch_league_transactions  # noqa: E402
from app.services.wrapped.trade_accolades import (  # noqa: E402
    calculate_trade_accolades, inspect_trade,
)


DEFAULT_LEAGUE = "1048379430328041472"  # The Nerd Herd, 1QB dynasty
DEFAULT_YEAR = "2024"


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--league", default=DEFAULT_LEAGUE)
    p.add_argument("--year", default=DEFAULT_YEAR)
    p.add_argument(
        "--inspect", default=None,
        help="If set, dump the full inspector payload for this transaction_id.",
    )
    p.add_argument(
        "--top", type=int, default=8,
        help="How many trades to print in the ledger summary (default 8).",
    )
    args = p.parse_args()

    print(f"Loading league context for {args.league} ({args.year})...")
    ctx = load_league_context(args.league, args.year)
    print(f"  name={ctx.league_settings.get('name')!r}")
    print(f"  is_dynasty={ctx.is_dynasty}  num_qbs={ctx.num_qbs}")

    if not ctx.is_dynasty:
        print("Not a dynasty league; trade ledger would be empty.")
        return 0

    print("\nFetching transactions...")
    tx = fetch_league_transactions(ctx)
    print(f"  {len(tx.trades)} completed trades")
    if not tx.trades:
        return 0

    print("\nComputing trade accolades (KTC value-integral lookback)...")
    section = calculate_trade_accolades(
        tx, season=int(args.year), num_qbs=ctx.num_qbs,
        league_id=args.league,
    )

    fleecing = section["biggest_fleecing"]
    if fleecing:
        print(f"\nBiggest fleecing: {fleecing['winner']} "
              f"+{fleecing['ktc_edge_per_season']:.0f} KTC/yr "
              f"in Wk {fleecing['week']} ({fleecing['transaction_id']})")
    most_active = section["most_active_trader"]
    if most_active:
        print(f"Most active: {most_active['username']} "
              f"({most_active['num_trades']} trades)")

    print(f"\nTop {args.top} trades by edge:")
    ranked = sorted(
        section["trades"], key=lambda t: t["ktc_edge_per_season"], reverse=True,
    )[: args.top]
    for t in ranked:
        winner = t["winner"] or "tie"
        edge = t["ktc_edge_per_season"]
        sides = ", ".join(
            f"{s['username']}: " + ("+ ".join(a["label"] for a in s["assets"]) or "—")
            for s in t["sides"]
        )
        print(f"  Wk {t['week']:>2}  {winner:>12}  +{edge:>5.0f}/yr  "
              f"[{t['transaction_id']}]  {sides}")

    print(f"\nBy-user net (KTC/yr):")
    by_user = sorted(
        section["by_user"].items(),
        key=lambda kv: kv[1]["net_ktc_per_season"], reverse=True,
    )
    for user, info in by_user:
        net = info["net_ktc_per_season"]
        sign = "+" if net >= 0 else ""
        print(f"  {user:>16}  {sign}{net:>6.0f}/yr  ({info['num_trades']} trades)")

    if args.inspect:
        target = next(
            (t for t in tx.trades if t.transaction_id == args.inspect), None,
        )
        if target is None:
            print(f"\n!! transaction_id {args.inspect!r} not found in this league.")
            return 1
        print(f"\nInspecting {args.inspect}...")
        payload = inspect_trade(target, season=int(args.year), num_qbs=ctx.num_qbs,
                                league_id=args.league)
        race = payload["race_chart"]
        print(f"  trade_date={race['trade_date']}  end={race['evaluation_end']}")
        print(f"  k={race['k']:.2f}  sides={len(race['sides'])}  "
              f"crossovers={race['crossover_dates']}")
        for side in race["sides"]:
            pts = side["points"]
            head, tail = pts[0], pts[-1]
            print(f"    {side['team_label']:>16}: "
                  f"start={head['ktc_equiv']:.0f}  end={tail['ktc_equiv']:.0f}  "
                  f"({len(pts)} samples)")
        print(f"\n  Per-asset rows: {len(payload['per_asset_series'])}")
        for row in payload["per_asset_series"]:
            head, tail = row["points"][0], row["points"][-1]
            print(f"    {row['team_label']:>16}  {row['label']:>28}  "
                  f"start={head['value']:.0f} -> end={tail['value']:.0f}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
