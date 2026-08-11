"""Benchmark: Monte-Carlo draft recommender vs. greedy VBD ("VAL") drafting.

Runs many mock drafts. In each draft:
  * one team drafts via the Monte-Carlo recommender (sim.recommend_pick),
  * one team drafts purely by VBD ("VAL": fill starters by value, then bench
    without overfilling a position),
  * every other team drafts by real ADP with Gaussian noise.

League size (8/12), 1QB vs superflex, season (2023/2024) and the two test
teams' draft slots are randomized each draft. Final rosters are graded
heavily toward starters with a smaller bench-depth term. Reports whether the
Monte-Carlo team builds meaningfully better teams than greedy VBD.

Run (PowerShell)::

    $env:USE_FIXTURE_BLOBS="1"
    python tools/benchmark_draft.py --drafts 1000 --nsims 25 --workers 8
"""
from __future__ import annotations

import argparse
import os
import random
import statistics
import sys
import time
from multiprocessing import Pool
from pathlib import Path

os.environ.setdefault("USE_FIXTURE_BLOBS", "1")
REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "backend"))

from app.services.draft_help import summaries, sim  # noqa: E402

ROUNDS = 14
BENCH_W = 0.30  # bench-depth weight in the combined grade (starters dominate)

_PLAYER_CACHE: dict = {}


def get_players(year, teams, ppr, sf):
    key = (year, teams, ppr, sf)
    if key not in _PLAYER_CACHE:
        rows = summaries.rankings_config_players(str(year), teams, ppr, sf)
        _PLAYER_CACHE[key] = sim.sim_players_from_config_players(rows)
    return _PLAYER_CACHE[key]


def slots_for(sf):
    s = {"QB": 1, "RB": 2, "WR": 2, "TE": 1, "FLEX": 1}
    if sf:
        s["SUPER_FLEX"] = 1
    return s


def start_capacity(slots):
    flex = slots.get("FLEX", 0) + slots.get("SUPER_FLEX", 0)
    return {
        "QB": slots.get("QB", 0) + slots.get("SUPER_FLEX", 0),
        "RB": slots.get("RB", 0) + flex,
        "WR": slots.get("WR", 0) + flex,
        "TE": slots.get("TE", 0) + flex,
    }


def val_pick(avail_by_vbd, roster, slots, caps, bench_extra):
    """Greedy VBD: fill an open starting slot with the best player; once starters
    are full, take the best player whose position isn't overfilled."""
    base = sim.lineup_value(roster, slots)
    for p in avail_by_vbd:
        if sim.lineup_value(roster + [p], slots) > base + 1e-9:
            return p  # improves the starting lineup -> fills a need
    counts: dict = {}
    for p in roster:
        counts[p.pos] = counts.get(p.pos, 0) + 1
    for p in avail_by_vbd:
        limit = caps.get(p.pos, 0) + bench_extra.get(p.pos, 1)
        if counts.get(p.pos, 0) < limit:
            return p
    return avail_by_vbd[0]


def grade(roster, slots, bench_w=BENCH_W):
    """(combined, starters, bench). Starters = optimal starting-lineup VBD;
    bench = top-2 backups per startable position (decayed), positive only."""
    starters, _by_pos, used = sim._fill_lineup(roster, slots)
    startable = sim._startable_positions(slots)
    bench = 0.0
    for pos in startable:
        vals = sorted([p.proj for p in roster if p.pos == pos], reverse=True)
        u = used.get(pos, 0)
        for j, wt in enumerate((1.0, 0.5)):
            idx = u + j
            if idx < len(vals) and vals[idx] > 0:
                bench += wt * vals[idx]
    return starters + bench_w * bench, starters, bench


def simulate(payload):
    seed, nsims = payload
    rng = random.Random(seed)
    teams = rng.choice([8, 12])
    sf = bool(seed % 2)          # balanced 1QB / superflex
    year = rng.choice([2023, 2024])
    ppr = 0.5
    slots = slots_for(sf)
    caps = start_capacity(slots)
    bench_extra = {"QB": 1, "TE": 1, "RB": 3, "WR": 3}
    if sf:
        bench_extra["QB"] = 2

    players = get_players(year, teams, ppr, sf)
    byid = {p.player_id: p for p in players}
    mc_slot, val_slot = rng.sample(range(1, teams + 1), 2)
    opponent_order = sim._draw_opponent_order(players, rng)

    drafted: set = set()
    mc: list = []
    val: list = []
    total = teams * ROUNDS
    for pick in range(1, total + 1):
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
            pid = rec["player_id"] if rec else max(avail, key=lambda p: p.proj).player_id
            drafted.add(pid)
            mc.append(pid)
        elif slot == val_slot:
            av = sorted(avail, key=lambda x: x.proj, reverse=True)
            p = val_pick(av, [byid[i] for i in val], slots, caps, bench_extra)
            drafted.add(p.player_id)
            val.append(p.player_id)
        else:
            p = next(p for p in opponent_order if p.player_id not in drafted)
            drafted.add(p.player_id)

    g_mc = grade([byid[i] for i in mc], slots)
    g_val = grade([byid[i] for i in val], slots)
    return (teams, int(sf), year, *g_mc, *g_val)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--drafts", type=int, default=1000)
    ap.add_argument("--nsims", type=int, default=25)
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 1))
    args = ap.parse_args()

    t0 = time.time()
    payloads = [(s, args.nsims) for s in range(args.drafts)]
    if args.workers > 1:
        with Pool(args.workers) as pool:
            results = pool.map(simulate, payloads, chunksize=4)
    else:
        results = [simulate(p) for p in payloads]
    dt = time.time() - t0

    # results: teams, sf, year, mc_comb, mc_start, mc_bench, val_comb, val_start, val_bench
    def report(rows, label):
        n = len(rows)
        mc_c = [r[3] for r in rows]; mc_s = [r[4] for r in rows]
        vl_c = [r[6] for r in rows]; vl_s = [r[7] for r in rows]
        comb_margin = [a - b for a, b in zip(mc_c, vl_c)]
        start_margin = [a - b for a, b in zip(mc_s, vl_s)]
        mc_win = sum(1 for d in comb_margin if d > 0)
        print(f"\n[{label}]  n={n}")
        print(f"  combined grade: MC {statistics.mean(mc_c):7.1f}  vs VAL {statistics.mean(vl_c):7.1f}"
              f"   margin {statistics.mean(comb_margin):+6.1f}  (MC wins {100*mc_win/n:.0f}%)")
        print(f"  starters only : MC {statistics.mean(mc_s):7.1f}  vs VAL {statistics.mean(vl_s):7.1f}"
              f"   margin {statistics.mean(start_margin):+6.1f}")
        if n > 1:
            sd = statistics.pstdev(comb_margin)
            se = sd / (n ** 0.5)
            print(f"  combined margin stdev {sd:.1f}, ~95% CI {statistics.mean(comb_margin):+.1f} +/- {1.96*se:.1f}")

    report(results, "ALL")
    report([r for r in results if r[1] == 1], "Superflex (2QB)")
    report([r for r in results if r[1] == 0], "1QB")
    print(f"\nDone: {args.drafts} drafts, nsims={args.nsims}, {args.workers} workers, {dt:.0f}s "
          f"({dt/args.drafts*1000:.0f} ms/draft).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
