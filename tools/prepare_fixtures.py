"""Prep the local fixture blobs for offline development.

Run with the repo root as cwd:

    python tools/prepare_fixtures.py

What it does:

1. Backfills the new ``P10`` / ``P90`` percentile fields onto each row in
   ``tests/fixtures/blobs/standard_player_rankings.json``. The snapshot was
   generated before that scraper change, so the offline UI would otherwise
   render empty ceiling/floor cells. We pull the values back out of the
   ``Simulations`` blocks already present in
   ``hand_calculated_projections.json``.

2. Synthesizes ``hand_calculated_projections_prev.json`` — a deterministic
   perturbation of the current projections used by the Risers/Fallers feature
   (#5) so we have something to diff against in offline mode.

This script is idempotent: re-running it just rewrites the same outputs.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any, Dict

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_DIR = REPO_ROOT / "tests" / "fixtures" / "blobs"

PROJ_FILE = FIXTURE_DIR / "hand_calculated_projections.json"
RANK_FILE = FIXTURE_DIR / "standard_player_rankings.json"
PREV_FILE = FIXTURE_DIR / "hand_calculated_projections_prev.json"
PREV_RANK_FILE = FIXTURE_DIR / "standard_player_rankings_prev.json"


# Same logic as ``_sim_key`` in azure-functions/draftkings_help.py — kept in
# sync so the augmentation produces values identical to a real scraper run.
def _sim_key(ppr_label: str, pass_td_pts: int, is_qb: bool) -> str:
    if is_qb:
        return "QB_6PT" if pass_td_pts == 6 else "QB_STD"
    return {"std": "STD", "halfppr": "HalfPPR", "fullppr": "PPR"}[ppr_label]


def _player_key(name: str) -> str:
    return "".join(ch for ch in name if ch.isalnum()).lower()


def _variant_to_parts(key: str) -> tuple[str, int]:
    # e.g. "halfppr_6ptpass" -> ("halfppr", 6)
    ppr, td = key.split("_")
    return ppr, int(td.replace("ptpass", ""))


def augment_rankings_with_percentiles() -> None:
    if not PROJ_FILE.exists() or not RANK_FILE.exists():
        raise SystemExit(
            f"Missing fixtures. Expected both {PROJ_FILE} and {RANK_FILE} to exist."
        )

    projections: Dict[str, Any] = json.loads(PROJ_FILE.read_text(encoding="utf-8"))
    rankings: Dict[str, Any] = json.loads(RANK_FILE.read_text(encoding="utf-8"))

    augmented_total = 0
    skipped_no_sim = 0

    for variant_key, rows in rankings.items():
        try:
            ppr_label, td_pts = _variant_to_parts(variant_key)
        except Exception:
            print(f"Skipping unknown variant {variant_key}")
            continue

        for row in rows:
            # If both already exist we still recompute to stay idempotent.
            name = row.get("NAME") or ""
            pos = row.get("POS") or "UNK"
            sim_block = (projections.get(_player_key(name), {}) or {}).get("Simulations") or {}
            if not sim_block or "error" in sim_block:
                row["P10"] = None
                row["P90"] = None
                skipped_no_sim += 1
                continue
            stat_block = sim_block.get(_sim_key(ppr_label, td_pts, is_qb=(pos == "QB")))
            if not isinstance(stat_block, dict):
                row["P10"] = None
                row["P90"] = None
                skipped_no_sim += 1
                continue
            pcts = stat_block.get("percentiles") or {}
            p10 = pcts.get(10) if 10 in pcts else pcts.get("10")
            p90 = pcts.get(90) if 90 in pcts else pcts.get("90")
            row["P10"] = round(p10, 2) if isinstance(p10, (int, float)) else None
            row["P90"] = round(p90, 2) if isinstance(p90, (int, float)) else None
            if row["P10"] is not None or row["P90"] is not None:
                augmented_total += 1

    RANK_FILE.write_text(json.dumps(rankings, separators=(",", ":")), encoding="utf-8")
    print(
        f"[rankings] Wrote {RANK_FILE.name}: "
        f"{augmented_total} rows got P10/P90, {skipped_no_sim} had no usable sim."
    )


def synthesize_prev_projections() -> None:
    """Produce a 'previous run' snapshot with deterministic ±X% nudges so the
    Risers/Fallers diff has something interesting to display offline.

    We keep the perturbation deterministic (hash-of-name-driven) so re-runs
    of this script yield the same output and tests are reproducible.
    """
    if not PROJ_FILE.exists():
        raise SystemExit(f"Missing fixture {PROJ_FILE}")

    current: Dict[str, Any] = json.loads(PROJ_FILE.read_text(encoding="utf-8"))
    prev = copy.deepcopy(current)

    nudged = 0
    for player_key, stats in prev.items():
        if not isinstance(stats, dict):
            continue
        # Use a deterministic float in [-0.20, 0.20] derived from the player key.
        h = int(hashlib.md5(player_key.encode("utf-8")).hexdigest()[:8], 16)
        # Map the lowest 16 bits to [-0.20, 0.20], skip ~25% of players.
        bucket = (h & 0xFFFF) / 0xFFFF  # [0, 1]
        if bucket < 0.25:
            continue
        # Map remaining [0.25, 1.0] to [-0.20, 0.20]
        delta_pct = ((bucket - 0.25) / 0.75) * 0.40 - 0.20

        for stat_name, val in list(stats.items()):
            if stat_name == "Simulations":
                # Don't bother re-running sims; the diff feature only looks at
                # Vegas point totals derived from these primary stats.
                continue
            if isinstance(val, (int, float)):
                stats[stat_name] = round(val * (1.0 + delta_pct), 4)
        nudged += 1

    PREV_FILE.write_text(json.dumps(prev, separators=(",", ":")), encoding="utf-8")
    print(f"[prev]    Wrote {PREV_FILE.name}: nudged {nudged} players.")


def synthesize_prev_rankings() -> None:
    """Build standard_player_rankings_prev.json by perturbing VEGAS / P10 / P90
    on the current rankings snapshot. Same deterministic hash-driven scheme as
    ``synthesize_prev_projections`` so a player's current vs prev delta lines
    up roughly with their projection delta.

    Required so the Risers/Fallers backend can diff per-variant Vegas points
    without having to rerun the full scoring pipeline at request time.
    """
    if not RANK_FILE.exists():
        raise SystemExit(f"Missing fixture {RANK_FILE}")

    current: Dict[str, Any] = json.loads(RANK_FILE.read_text(encoding="utf-8"))
    prev = copy.deepcopy(current)

    nudged = 0
    for variant_key, rows in prev.items():
        if not isinstance(rows, list):
            continue
        for row in rows:
            name = row.get("NAME") or ""
            key = "".join(ch for ch in name if ch.isalnum()).lower()
            if not key:
                continue
            h = int(hashlib.md5(key.encode("utf-8")).hexdigest()[:8], 16)
            bucket = (h & 0xFFFF) / 0xFFFF
            if bucket < 0.25:
                continue
            delta_pct = ((bucket - 0.25) / 0.75) * 0.40 - 0.20

            for field in ("VEGAS", "P10", "P90", "SIM_MEAN"):
                v = row.get(field)
                if isinstance(v, (int, float)):
                    row[field] = round(v * (1.0 + delta_pct), 2)
            nudged += 1

    PREV_RANK_FILE.write_text(json.dumps(prev, separators=(",", ":")), encoding="utf-8")
    print(f"[prev]    Wrote {PREV_RANK_FILE.name}: nudged {nudged} rows.")


def main() -> None:
    print(f"Repo root:    {REPO_ROOT}")
    print(f"Fixture dir:  {FIXTURE_DIR}")
    augment_rankings_with_percentiles()
    synthesize_prev_projections()
    synthesize_prev_rankings()
    print("Done. Set USE_FIXTURE_BLOBS=1 in your backend env to use these locally.")


if __name__ == "__main__":
    main()
