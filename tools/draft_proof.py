"""Draft-approach proof: Monte-Carlo drafting vs. greedy-VBD vs. pure-ADP.

This is the *permanent, reproducible* version of the throwaway cross-validation
diagnostics referenced in ``docs/DRAFT_HELP_HANDOFF.md`` (§4/§7). It exists to
back one specific, methodology-level claim:

    **If the given rankings are accurate, simulating the rest of the draft
    (Monte-Carlo) builds better starting lineups than naively taking the highest
    VBD, and far better than drafting by ADP alone.**

It is NOT a claim about how any real team finished a real season -- every team
here drafts from the *same* projections, so the only thing being measured is the
drafting *strategy*, graded on a currency-independent yardstick: the raw
projected points of each drafter's optimal starting lineup.

Setup per simulated draft
-------------------------
Three "study" seats draft against a field of ADP-with-noise opponents:
  * ``mc``     -- the Monte-Carlo recommender (``sim.recommend_pick``),
  * ``greedy`` -- greedy VBD ("VAL"): fill a starting slot with the best player,
                  then the best player whose position isn't overfilled,
  * ``adp``    -- pure ADP: always take the lowest-ADP player available.
The three study seats' draft slots are drawn without replacement each draft;
every other seat shares one coherent Gaussian-ADP board sampled at the start of
the draft. Once a study seat removes a player, field teams skip that player and
continue down the same latent board.

The grid sweeps seasons x team sizes x {1QB, superflex} at half-PPR so the
result cannot be an artifact of one season or one league size (the "not overfit
to 2024" check). Each cell runs ``--drafts`` drafts with deterministic seeds.

Outputs (committed under ``tools/draft_proof_output/``)
------------------------------------------------------
  * ``results.csv``  -- one row per (cell x draft): the three lineup-point totals.
  * ``summary.json`` -- per-cell + aggregate margins, win rates, 95% CIs. This is
                        the file the frontend "Methodology / Proof" panel reads
                        (copied into ``frontend/public/`` by ``--emit-frontend``).
  * ``draft_proof.png`` -- a chart of the margins (needs matplotlib).

Run (PowerShell)::

    $env:USE_FIXTURE_BLOBS="1"
    python tools/draft_proof.py --drafts 40 --nsims 20 --workers 8
    python tools/draft_proof.py --drafts 40 --nsims 20 --emit-frontend   # + copy json
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import random
import shutil
import statistics
import sys
import time
from multiprocessing import Pool
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

os.environ.setdefault("USE_FIXTURE_BLOBS", "1")
REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "backend"))

from app.services.draft_help import summaries, sim  # noqa: E402

OUTPUT_DIR = REPO / "tools" / "draft_proof_output"
FRONTEND_PUBLIC = REPO / "frontend" / "public" / "draft_proof_summary.json"

ROUNDS = 14
PPR = 0.5
SEASONS: Tuple[int, ...] = (2022, 2023, 2024)
TEAM_SIZES: Tuple[int, ...] = (8, 10, 12, 14)
SUPERFLEX: Tuple[bool, ...] = (False, True)

_PLAYER_CACHE: Dict[tuple, List[sim.SimPlayer]] = {}


# ---------------------------------------------------------------------------
# Board / roster helpers
# ---------------------------------------------------------------------------
def get_players(year: int, teams: int, ppr: float, sf: bool) -> List[sim.SimPlayer]:
    key = (year, teams, ppr, sf)
    if key not in _PLAYER_CACHE:
        rows = summaries.rankings_config_players(str(year), teams, ppr, sf)
        _PLAYER_CACHE[key] = sim.sim_players_from_config_players(rows)
    return _PLAYER_CACHE[key]


def slots_for(sf: bool) -> Dict[str, int]:
    s = {"QB": 1, "RB": 2, "WR": 2, "TE": 1, "FLEX": 1}
    if sf:
        s["SUPER_FLEX"] = 1
    return s


def start_capacity(slots: Dict[str, int]) -> Dict[str, int]:
    flex = slots.get("FLEX", 0) + slots.get("SUPER_FLEX", 0)
    return {
        "QB": slots.get("QB", 0) + slots.get("SUPER_FLEX", 0),
        "RB": slots.get("RB", 0) + flex,
        "WR": slots.get("WR", 0) + flex,
        "TE": slots.get("TE", 0) + flex,
    }


# ---------------------------------------------------------------------------
# The grade: currency-independent optimal starting-lineup POINTS (raw fpts).
# This deliberately ignores VBD/flex_proj (the drafters' internal currencies)
# so the comparison is "whose roster actually scores the most points", not
# "whose roster wins on the metric it optimized for".
# ---------------------------------------------------------------------------
def lineup_points(roster: Sequence[sim.SimPlayer], slots: Dict[str, int]) -> float:
    by_pos: Dict[str, List[float]] = {}
    for p in roster:
        by_pos.setdefault(p.pos, []).append(p.fpts or 0.0)
    for v in by_pos.values():
        v.sort(reverse=True)
    used: Dict[str, int] = {p: 0 for p in sim._DEDICATED}
    total = 0.0
    for pos in sim._DEDICATED:
        avail = by_pos.get(pos, [])
        for _ in range(slots.get(pos, 0)):
            if used[pos] < len(avail):
                total += avail[used[pos]]
                used[pos] += 1
    for flex, cnt in slots.items():
        eligible = sim.FLEX_GROUPS.get(flex)
        if not eligible:
            continue
        for _ in range(cnt):
            best_val: Optional[float] = None
            best_pos: Optional[str] = None
            for pos in eligible:
                avail = by_pos.get(pos, [])
                if used.get(pos, 0) < len(avail):
                    val = avail[used.get(pos, 0)]
                    if best_val is None or val > best_val:
                        best_val, best_pos = val, pos
            if best_pos is not None:
                total += best_val or 0.0
                used[best_pos] = used.get(best_pos, 0) + 1
    return total


# ---------------------------------------------------------------------------
# Pick policies
# ---------------------------------------------------------------------------
def greedy_vbd_pick(avail_by_vbd, roster, slots, caps, bench_extra):
    """Greedy VBD ("VAL"): fill an open starting slot with the best player; once
    starters are full, take the best player whose position isn't overfilled."""
    base = sim.lineup_value(roster, slots)
    for p in avail_by_vbd:
        if sim.lineup_value(roster + [p], slots) > base + 1e-9:
            return p
    counts: Dict[str, int] = {}
    for p in roster:
        counts[p.pos] = counts.get(p.pos, 0) + 1
    for p in avail_by_vbd:
        limit = caps.get(p.pos, 0) + bench_extra.get(p.pos, 1)
        if counts.get(p.pos, 0) < limit:
            return p
    return avail_by_vbd[0]


# ---------------------------------------------------------------------------
# One simulated draft -> the three lineup-point totals.
# ---------------------------------------------------------------------------
def simulate(payload):
    seed, nsims, year, teams, sf = payload
    rng = random.Random(seed)
    slots = slots_for(sf)
    caps = start_capacity(slots)
    bench_extra = {"QB": 2 if sf else 1, "TE": 1, "RB": 3, "WR": 3}

    players = get_players(year, teams, PPR, sf)
    byid = {p.player_id: p for p in players}
    mc_slot, greedy_slot, adp_slot = rng.sample(range(1, teams + 1), 3)
    opponent_order = sim._draw_opponent_order(players, rng)

    drafted: set = set()
    mc: List[str] = []
    greedy: List[str] = []
    adp: List[str] = []
    total_picks = teams * ROUNDS
    for pick in range(1, total_picks + 1):
        slot = sim.snake_slot_for_pick(pick, teams)
        avail = sorted((p for p in players if p.player_id not in drafted), key=lambda x: x.adp)
        if not avail:
            break
        if slot == mc_slot:
            out = sim.recommend_pick(
                players, drafted_ids=list(drafted), my_roster_ids=mc,
                teams=teams, rounds=ROUNDS, my_slot=mc_slot, slots=slots,
                current_pick=pick, n_sims=nsims, top_k=5, seed=seed * 9973 + pick,
            )
            rec = out.get("recommendation")
            pid = rec["player_id"] if rec else max(avail, key=lambda p: p.proj or 0.0).player_id
            drafted.add(pid)
            mc.append(pid)
        elif slot == greedy_slot:
            av = sorted(avail, key=lambda x: x.proj or 0.0, reverse=True)
            p = greedy_vbd_pick(av, [byid[i] for i in greedy], slots, caps, bench_extra)
            drafted.add(p.player_id)
            greedy.append(p.player_id)
        elif slot == adp_slot:
            p = avail[0]  # lowest ADP available
            drafted.add(p.player_id)
            adp.append(p.player_id)
        else:
            p = next(p for p in opponent_order if p.player_id not in drafted)
            drafted.add(p.player_id)

    return (
        year, teams, int(sf),
        lineup_points([byid[i] for i in mc], slots),
        lineup_points([byid[i] for i in greedy], slots),
        lineup_points([byid[i] for i in adp], slots),
    )


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------
def _ci95(values: List[float]) -> float:
    n = len(values)
    if n < 2:
        return 0.0
    se = statistics.pstdev(values) / (n ** 0.5)
    return 1.96 * se


def summarize(rows: List[tuple], label: str) -> dict:
    n = len(rows)
    if n == 0:
        return {
            "label": label, "n": 0,
            "mc_mean": 0.0, "greedy_mean": 0.0, "adp_mean": 0.0,
            "mc_vs_greedy": 0.0, "mc_vs_greedy_ci": 0.0, "mc_vs_greedy_winpct": 0.0,
            "mc_vs_adp": 0.0, "mc_vs_adp_ci": 0.0, "mc_vs_adp_winpct": 0.0,
        }
    mc = [r[3] for r in rows]
    gr = [r[4] for r in rows]
    ad = [r[5] for r in rows]
    mc_vs_greedy = [a - b for a, b in zip(mc, gr)]
    mc_vs_adp = [a - b for a, b in zip(mc, ad)]
    return {
        "label": label,
        "n": n,
        "mc_mean": round(statistics.mean(mc), 1),
        "greedy_mean": round(statistics.mean(gr), 1),
        "adp_mean": round(statistics.mean(ad), 1),
        "mc_vs_greedy": round(statistics.mean(mc_vs_greedy), 1),
        "mc_vs_greedy_ci": round(_ci95(mc_vs_greedy), 1),
        "mc_vs_greedy_winpct": round(100 * sum(1 for d in mc_vs_greedy if d > 0) / n, 1),
        "mc_vs_adp": round(statistics.mean(mc_vs_adp), 1),
        "mc_vs_adp_ci": round(_ci95(mc_vs_adp), 1),
        "mc_vs_adp_winpct": round(100 * sum(1 for d in mc_vs_adp if d > 0) / n, 1),
    }


def build_summary(results: List[tuple], meta: dict) -> dict:
    overall = summarize(results, "ALL")
    by_season = [summarize([r for r in results if r[0] == y], str(y)) for y in SEASONS]
    by_size = [summarize([r for r in results if r[1] == t], f"{t}-team") for t in TEAM_SIZES]
    by_format = [
        summarize([r for r in results if r[2] == 0], "1QB"),
        summarize([r for r in results if r[2] == 1], "Superflex"),
    ]
    # Per-cell (season x size x format) so no single cell can hide a regression.
    by_cell = []
    for y in SEASONS:
        for t in TEAM_SIZES:
            for sf in (0, 1):
                cell = [r for r in results if r[0] == y and r[1] == t and r[2] == sf]
                if cell:
                    s = summarize(cell, f"{y} {t}-team {'SF' if sf else '1QB'}")
                    s.update({"season": y, "teams": t, "superflex": bool(sf)})
                    by_cell.append(s)
    return {
        "meta": meta,
        "overall": overall,
        "by_season": by_season,
        "by_size": by_size,
        "by_format": by_format,
        "by_cell": by_cell,
    }


# ---------------------------------------------------------------------------
# Output writers
# ---------------------------------------------------------------------------
def write_csv(results: List[tuple], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["season", "teams", "superflex", "mc_points", "greedy_points", "adp_points"])
        for r in results:
            w.writerow(r)


def write_json(summary: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(summary, indent=2), encoding="utf-8")


def write_chart(summary: dict, path: Path) -> bool:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception as exc:  # noqa: BLE001
        print(f"  (skipping chart -- matplotlib unavailable: {exc})")
        return False

    seasons = summary["by_season"]
    sizes = summary["by_size"]
    ov = summary["overall"]

    fig, axes = plt.subplots(1, 3, figsize=(15, 5))
    fig.suptitle(
        "Monte-Carlo drafting vs. greedy-VBD vs. pure-ADP\n"
        "(same projections for all; graded on optimal starting-lineup points)",
        fontsize=12,
    )

    # 1) MC - greedy margin by season, with 95% CI.
    ax = axes[0]
    labels = [s["label"] for s in seasons]
    vals = [s["mc_vs_greedy"] for s in seasons]
    errs = [s["mc_vs_greedy_ci"] for s in seasons]
    ax.bar(labels, vals, yerr=errs, capsize=4, color="#2b8cbe")
    ax.axhline(0, color="#444", linewidth=0.8)
    ax.set_title(f"MC beats greedy-VBD by season\n(overall +{ov['mc_vs_greedy']} pts, {ov['mc_vs_greedy_winpct']}% win)")
    ax.set_ylabel("MC - greedy (lineup pts/team)")

    # 2) MC - greedy margin by team size.
    ax = axes[1]
    labels = [s["label"] for s in sizes]
    vals = [s["mc_vs_greedy"] for s in sizes]
    errs = [s["mc_vs_greedy_ci"] for s in sizes]
    ax.bar(labels, vals, yerr=errs, capsize=4, color="#41ab5d")
    ax.axhline(0, color="#444", linewidth=0.8)
    ax.set_title("MC beats greedy-VBD by league size")
    ax.set_ylabel("MC - greedy (lineup pts/team)")

    # 3) Absolute lineup points: MC vs greedy vs ADP (the ADP blowout).
    ax = axes[2]
    names = ["MC", "greedy-VBD", "pure-ADP"]
    means = [ov["mc_mean"], ov["greedy_mean"], ov["adp_mean"]]
    ax.bar(names, means, color=["#2b8cbe", "#41ab5d", "#d95f0e"])
    ax.set_title(f"Optimal starting-lineup points\n(MC beats ADP by +{ov['mc_vs_adp']} pts, {ov['mc_vs_adp_winpct']}% win)")
    ax.set_ylabel("lineup pts/team")

    fig.tight_layout(rect=(0, 0, 1, 0.93))
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=120)
    plt.close(fig)
    return True


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--drafts", type=int, default=40, help="drafts per grid cell")
    ap.add_argument("--nsims", type=int, default=20, help="MC rollouts per pick")
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 1))
    ap.add_argument("--seed", type=int, default=1000, help="base seed (reproducible)")
    ap.add_argument("--emit-frontend", action="store_true",
                    help="also copy summary.json into frontend/public/")
    args = ap.parse_args()

    payloads = []
    idx = 0
    for year in SEASONS:
        for teams in TEAM_SIZES:
            for sf in SUPERFLEX:
                for d in range(args.drafts):
                    payloads.append((args.seed + idx * 101 + d, args.nsims, year, teams, sf))
                idx += 1

    t0 = time.time()
    if args.workers > 1:
        with Pool(args.workers) as pool:
            results = pool.map(simulate, payloads, chunksize=4)
    else:
        results = [simulate(p) for p in payloads]
    dt = time.time() - t0

    meta = {
        "generated": time.strftime("%Y-%m-%d %H:%M:%S"),
        "drafts_per_cell": args.drafts,
        "nsims": args.nsims,
        "rounds": ROUNDS,
        "ppr": PPR,
        "seasons": list(SEASONS),
        "team_sizes": list(TEAM_SIZES),
        "formats": ["1QB", "Superflex"],
        "grade": "optimal starting-lineup points (raw projected fpts)",
        "total_drafts": len(payloads),
    }
    summary = build_summary(results, meta)

    write_csv(results, OUTPUT_DIR / "results.csv")
    write_json(summary, OUTPUT_DIR / "summary.json")
    charted = write_chart(summary, OUTPUT_DIR / "draft_proof.png")
    if args.emit_frontend:
        FRONTEND_PUBLIC.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(OUTPUT_DIR / "summary.json", FRONTEND_PUBLIC)

    ov = summary["overall"]
    print(f"\n[ALL] n={ov['n']}")
    print(f"  MC {ov['mc_mean']}  greedy {ov['greedy_mean']}  adp {ov['adp_mean']}  (lineup pts/team)")
    print(f"  MC vs greedy: +{ov['mc_vs_greedy']} +/- {ov['mc_vs_greedy_ci']}  (MC wins {ov['mc_vs_greedy_winpct']}%)")
    print(f"  MC vs ADP   : +{ov['mc_vs_adp']} +/- {ov['mc_vs_adp_ci']}  (MC wins {ov['mc_vs_adp_winpct']}%)")
    print(f"\nWrote: {OUTPUT_DIR / 'results.csv'}")
    print(f"       {OUTPUT_DIR / 'summary.json'}")
    if charted:
        print(f"       {OUTPUT_DIR / 'draft_proof.png'}")
    if args.emit_frontend:
        print(f"       {FRONTEND_PUBLIC}")
    print(f"\nDone: {len(payloads)} drafts, nsims={args.nsims}, {args.workers} workers, {dt:.0f}s.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
