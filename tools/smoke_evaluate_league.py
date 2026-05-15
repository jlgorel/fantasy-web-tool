"""Run the trade evaluator over every completed trade in a saved
Sleeper-league fixture and print a per-trade verdict report.

Usage::

    python tools/smoke_evaluate_league.py
    python tools/smoke_evaluate_league.py --league 1312205344964898816 \\
        --blob tests/fixtures/blobs/historical_KTC_rankings.json \\
        --format 1qb \\
        --evaluation-end 2026-05-09

The fixture must already exist at
``tests/fixtures/sleeper_league/<league_id>_chain/data.json``. We added
one for league 1312205344964898816 ("The Nerd Herd") -- the 1QB dynasty
chain back through 2022.

This is intentionally a tool / smoke script, not a unit test: it dumps
verdicts to stdout so the user can spot-check whether the evaluator
agrees with their gut. Per-asset breakdowns are included for any trade
where requested via ``--verbose``.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "azure-functions"))

from trade_eval.pick_handoff import (  # noqa: E402
    flatten_value_blob, make_pick_aware_resolver,
)
from trade_eval.sleeper_trade_adapter import (  # noqa: E402
    build_trade, merged_roster_labels,
)
from trade_eval.sleeper_trade_loader import (  # noqa: E402
    SeasonContext, build_pick_to_player, normalize_all_trades,
)
from trade_eval.trade_evaluator import (  # noqa: E402
    evaluate_trade, make_blob_resolver,
)


def load_chain(league_id: str) -> List[SeasonContext]:
    path = REPO_ROOT / "tests" / "fixtures" / "sleeper_league" / \
        f"{league_id}_chain" / "data.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    return [SeasonContext(**s) for s in raw["chain"]]


def load_player_names_from_chain(chain: List[SeasonContext]) -> Dict[str, str]:
    """Build a sleeper_id -> 'First Last (POS)' map from each season's
    draft picks (cheapest place to find every recent player without an
    extra API call)."""
    names: Dict[str, str] = {}
    for ctx in chain:
        for pick in ctx.draft_picks or []:
            pid = str(pick.get("player_id") or "")
            if not pid:
                continue
            meta = pick.get("metadata") or {}
            first = meta.get("first_name") or ""
            last = meta.get("last_name") or ""
            pos = meta.get("position") or ""
            label = f"{first} {last}".strip() or pid
            if pos:
                label = f"{label} ({pos})"
            names.setdefault(pid, label)
    return names


def format_trade(eval_result, *, verbose: bool = False) -> str:
    lines = [
        f"  Date: {eval_result.trade.trade_date.isoformat()}  "
        f"Winner: {eval_result.winner_label or '(tie)'}"
    ]
    for side in eval_result.sides:
        margin = eval_result.margins.get(side.team_label, 0.0)
        flag = "WIN" if side.team_label == eval_result.winner_label else "   "
        lines.append(
            f"    [{flag}] {side.team_label:<22s}  "
            f"score={side.total_score:>14,.0f}  "
            f"margin={margin:>+14,.0f}"
        )
        if verbose:
            for ev in side.asset_evaluations:
                lines.append(
                    f"          - {ev.asset.label:<40s} "
                    f"score={ev.total_score:>12,.0f}  "
                    f"active_days={ev.integral.active_days:>4d}"
                )
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--league", default="1312205344964898816",
                    help="League id whose fixture to evaluate")
    ap.add_argument("--blob",
                    default=str(REPO_ROOT / "tests" / "fixtures" / "blobs" /
                                "historical_KTC_rankings.json"),
                    help="Path to the historical KTC value blob "
                         "(new canonical: historical_KTC_rankings.json; "
                         "legacy: trade_eval_ktc_history_<fmt>.json)")
    ap.add_argument("--format", default="1qb", choices=["1qb", "superflex"],
                    help="Which value series to use from the canonical blob. "
                         "Ignored for legacy single-format blobs.")
    ap.add_argument("--evaluation-end", default=None,
                    help="ISO date for evaluation horizon (default: today)")
    ap.add_argument("--verbose", action="store_true",
                    help="Print per-asset breakdown for every trade")
    ap.add_argument("--top", type=int, default=10,
                    help="Print the top-N lopsided trades by abs margin")
    args = ap.parse_args()

    eval_end: Optional[date]
    if args.evaluation_end:
        eval_end = date.fromisoformat(args.evaluation_end)
    else:
        eval_end = datetime.now(timezone.utc).date()

    print(f"== Trade smoke eval: league {args.league} ==")
    print(f"   blob: {args.blob}  (fmt={args.format})")
    print(f"   evaluation_end: {eval_end.isoformat()}")

    chain = load_chain(args.league)
    print(f"   seasons in chain: {[c.season for c in chain]}")

    blob = json.loads(Path(args.blob).read_text(encoding="utf-8"))
    flat = flatten_value_blob(blob, fmt=args.format)
    print(f"   players in blob: {sum(1 for k in flat if not k.startswith('pick:'))}")
    print(f"   picks in blob:   {sum(1 for k in flat if k.startswith('pick:'))}")

    base_resolver = make_blob_resolver(flat, max_stale_days=45)
    pick_table = build_pick_to_player(chain)
    print(f"   realized picks in handoff table: {len(pick_table)}")
    resolver = make_pick_aware_resolver(base_resolver, pick_table)

    roster_labels = merged_roster_labels(chain)
    player_names = load_player_names_from_chain(chain)

    normalized = normalize_all_trades(chain)
    print(f"   normalized trades: {len(normalized)}")

    results = []
    missing_assets: Counter = Counter()
    for nt in normalized:
        trade = build_trade(
            nt,
            chain_by_season={c.season: c for c in chain},
            roster_labels=roster_labels,
            player_names=player_names,
            evaluation_end=eval_end,
        )
        result = evaluate_trade(trade, value_resolver=resolver)
        results.append((nt, result))
        # Track assets with zero value to flag coverage gaps.
        for side in result.sides:
            for ev in side.asset_evaluations:
                if ev.integral.score == 0:
                    missing_assets[ev.asset.label or ev.asset.asset_id] += 1

    # Aggregate winner counts.
    winners: Counter = Counter()
    for _, r in results:
        winners[r.winner_label or "(tie)"] += 1

    print("\n-- Wins by team --")
    for team, n in winners.most_common():
        print(f"   {team:<24s}  {n}")

    # Top N by |margin|, exclude single-side trades where margin=0.
    def best_margin(r):
        if not r.margins:
            return 0.0
        return max(abs(m) for m in r.margins.values())

    sorted_results = sorted(results, key=lambda pair: best_margin(pair[1]),
                            reverse=True)

    print(f"\n-- Top {args.top} most lopsided trades --")
    for nt, r in sorted_results[:args.top]:
        print(f"\n[{nt.season}] trade_id={nt.trade_id} leg={nt.leg}")
        print(format_trade(r, verbose=True))

    if args.verbose:
        print("\n-- All trades --")
        for nt, r in results:
            print(f"\n[{nt.season}] trade_id={nt.trade_id} leg={nt.leg}")
            print(format_trade(r, verbose=True))

    if missing_assets:
        print(f"\n-- Top assets with zero KTC coverage ({len(missing_assets)} unique) --")
        for label, n in missing_assets.most_common(20):
            print(f"   {label:<40s}  appears in {n} sides with score=0")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
