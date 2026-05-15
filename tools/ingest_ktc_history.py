"""Ingest historical KTC value CSVs into a single (per-format) value blob.

**DEPRECATED CLI (kept as a library).** The canonical production blob is now
``tests/fixtures/blobs/historical_KTC_rankings.json``, built by
``tools/build_historical_ktc_json.py``, which merges these same CSVs with
the daily KTC scrape from ``tools/scrape_ktc_top500_history.py``. The
runtime evaluator reads that single blob via
:func:`trade_eval.pick_handoff.flatten_value_blob` with a ``fmt`` kwarg
("1qb" / "superflex") -- no per-format files are required anymore.

This module is still imported as a **library** by
``tools/build_historical_ktc_json.py`` for its name-resolution helpers
(:data:`NAME_OVERRIDES`, :func:`normalize_name`, :func:`resolve_player`,
:func:`pick_id`, :func:`build_name_index`, :func:`parse_csv`,
:func:`is_pick_column`). The ``__main__`` CLI below still works if you
want a single-format blob for a notebook / one-off, but it should not be
wired into any production path.

Input: ``tools/sfhistorical.csv`` and ``tools/1qbhistorical.csv``.

  * Wide format. First column = ``Date`` (``YYYY-MM-DD``, descending).
  * Subsequent columns: pick names (e.g. ``2024 Mid 1st``) followed by
    player names alphabetically. **No position column** -- we match
    each player-column to a Sleeper ``player_id`` via name lookup
    against ``tests/fixtures/blobs/players.json``.
  * Empty cells = no value (player out of top 500, retired, or pick
    already used).

Output JSON shape (legacy, only when running the CLI directly)::

    {
      "format": "superflex",   # or "1qb"
      "generated_at": "<iso>",
      "players": {
        "<sleeper_id>": {
          "name": "Justin Jefferson",
          "fantasy_positions": ["WR"],
          "values": { "YYYY-MM-DD": <float>, ... }
        },
        ...
      },
      "picks": {
        "pick:2024_mid_1st": {
          "label": "2024 Mid 1st",
          "values": { "YYYY-MM-DD": <float>, ... }
        },
        ...
      },
      "unmatched_players": ["Some Name", ...]
    }

Run (legacy / advanced use only -- prefer
``tools/build_historical_ktc_json.py``)::

    python tools/ingest_ktc_history.py \
        --csv tools/sfhistorical.csv --format superflex
    python tools/ingest_ktc_history.py \
        --csv tools/1qbhistorical.csv --format 1qb
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
PLAYERS_FIXTURE = REPO_ROOT / "tests" / "fixtures" / "blobs" / "players.json"
DEFAULT_OUT_DIR = REPO_ROOT / "tests" / "fixtures" / "blobs"


# ---------------------------------------------------------------------------
# Name normalization & matching
# ---------------------------------------------------------------------------
_SUFFIX_RE = re.compile(r"\b(jr|sr|ii|iii|iv|v)\.?\b", re.IGNORECASE)
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")


def normalize_name(name: str) -> str:
    """Lowercase, strip suffixes, drop punctuation/whitespace.

    "A.J. Brown" -> "ajbrown"; "Marvin Harrison Jr." -> "marvinharrison";
    "Brian Thomas Jr." -> "brianthomas"; "Travis Etienne" -> "travisetienne".
    """
    name = unicodedata.normalize("NFKD", name)
    name = name.encode("ascii", "ignore").decode("ascii")
    name = _SUFFIX_RE.sub("", name)
    name = name.lower()
    return _NON_ALNUM_RE.sub("", name)


# Manual overrides for names that the auto-matcher gets wrong or that have
# multiple plausible Sleeper hits. Map: normalized_csv_name -> sleeper_id.
# Add new entries when ``--report-unmatched`` flags ambiguity.
#
# Most entries here exist because Sleeper uses a different first-name form
# (Gabe vs Gabriel, Chig vs Chigoziem) or because the suffix (Jr./III) we
# strip during normalization is the only thing distinguishing two real
# players in the league.
NAME_OVERRIDES: Dict[str, str] = {
    # Sleeper-spelling variants
    "andrewogletree":    "8489",   # CSV "Andrew Ogletree" -> Drew Ogletree (TE)
    "chigoziemokonkwo":  "8210",   # CSV full first name -> Chig Okonkwo (TE)
    "gabrieldavis":      "6943",   # CSV "Gabriel Davis"  -> Gabe Davis (WR)
    "jefferywilson":     "5284",   # CSV "Jeffery"        -> Jeff Wilson (RB)
    "joshpalmer":        "7670",   # CSV "Josh"           -> Joshua Palmer (WR)
    "nyheimhines":       "5347",   # CSV "Hines"          -> Nyheim Miller-Hines
    # Suffix collisions: stripping "Jr."/"III" merges with another player.
    "frankgorejr":       "11573",  # young RB, NOT the retired veteran (232)
    "kennethwalkeriii":  "8151",   # the Seahawks RB,  NOT WR Kenneth Walker (4634)
}


# ``normalize_name`` strips suffixes for the index lookup, but for the
# OVERRIDES lookup we want suffix-aware keys (so "Frank Gore Jr." and
# "Frank Gore" route to different ids). This second pass restores the
# suffix before the override probe.
def _suffix_aware_key(name: str) -> str:
    return _NON_ALNUM_RE.sub("", name.lower())


def build_name_index(
    players_blob: Dict[str, dict],
) -> Dict[str, List[Tuple[str, dict]]]:
    """Group sleeper players by normalized full_name.

    Returns {normalized_name: [(sleeper_id, player_meta), ...]} so we can
    flag ambiguous names (multiple players share a normalized name) and
    log them rather than guessing.
    """
    index: Dict[str, List[Tuple[str, dict]]] = {}
    for sid, meta in players_blob.items():
        full_name = meta.get("full_name") or ""
        if not full_name:
            continue
        # Skip non-fantasy-relevant positions outright; they pollute the
        # index (lots of generic "Chris Brown" type matches at LB, etc.).
        positions = meta.get("fantasy_positions") or []
        if not any(p in {"QB", "RB", "WR", "TE"} for p in positions):
            continue
        norm = normalize_name(full_name)
        if not norm:
            continue
        index.setdefault(norm, []).append((sid, meta))
    return index


def resolve_player(
    csv_name: str,
    name_index: Dict[str, List[Tuple[str, dict]]],
    players_blob: Dict[str, dict],
) -> Tuple[Optional[str], Optional[dict], str]:
    """Resolve a CSV column name to a (sleeper_id, player_meta, status).

    Status values:
      * ``"matched"`` -- unique match
      * ``"override"`` -- matched via :data:`NAME_OVERRIDES`
      * ``"ambiguous"`` -- multiple candidates; first one chosen but
        flagged
      * ``"unmatched"`` -- nothing close enough
    """
    norm = normalize_name(csv_name)
    suffix_key = _suffix_aware_key(csv_name)
    # Probe overrides with the suffix-aware key first, then fall back to
    # the suffix-stripped form for cases where suffix doesn't matter.
    override_sid = NAME_OVERRIDES.get(suffix_key) or NAME_OVERRIDES.get(norm)
    if override_sid is not None:
        # Pull metadata directly from the blob so we get the right
        # full_name/positions even when the override's normalized name
        # doesn't index back to the same key.
        meta = players_blob.get(override_sid)
        return override_sid, meta, "override"

    candidates = name_index.get(norm) or []
    if len(candidates) == 1:
        sid, meta = candidates[0]
        return sid, meta, "matched"
    if len(candidates) > 1:
        # Pick the one with the most recent metadata (heuristic: any with
        # a non-null fantasy_positions list, then alphabetically by id for
        # determinism). Not perfect; an override entry fixes the rare
        # case where this picks wrong.
        candidates_sorted = sorted(
            candidates,
            key=lambda c: (0 if (c[1].get("fantasy_positions") or []) else 1, c[0]),
        )
        sid, meta = candidates_sorted[0]
        return sid, meta, "ambiguous"
    return None, None, "unmatched"


# ---------------------------------------------------------------------------
# Pick handling
# ---------------------------------------------------------------------------
_PICK_RE = re.compile(
    r"^\s*(\d{4})\s+(early|mid|late)\s+(1st|2nd|3rd|4th)\s*$",
    re.IGNORECASE,
)


def is_pick_column(column_name: str) -> bool:
    return _PICK_RE.match(column_name) is not None


def pick_id(column_name: str) -> str:
    """Synthetic id for a pick column. ``"2024 Mid 1st" -> "pick:2024_mid_1st"``."""
    norm = column_name.strip().lower().replace(" ", "_")
    return f"pick:{norm}"


# ---------------------------------------------------------------------------
# CSV parsing
# ---------------------------------------------------------------------------
def parse_csv(
    csv_path: Path,
) -> Tuple[List[str], Dict[str, Dict[str, float]]]:
    """Parse a KTC historical CSV.

    Returns ``(column_names, {column_name: {date_str: value, ...}})``,
    skipping empty cells. Date strings are ``YYYY-MM-DD``.
    """
    with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.reader(f)
        header = next(reader)
        if not header or header[0].lstrip("\ufeff").strip() != "Date":
            raise ValueError(
                f"Expected first column 'Date', got {header[0]!r} in {csv_path}"
            )
        column_names = header[1:]
        per_column: Dict[str, Dict[str, float]] = {c: {} for c in column_names}
        for row in reader:
            if not row or not row[0]:
                continue
            date_str = row[0].strip()
            for i, raw in enumerate(row[1:]):
                if not raw:
                    continue
                try:
                    val = float(raw)
                except ValueError:
                    continue
                if val <= 0:
                    continue
                col = column_names[i]
                per_column[col][date_str] = val
    return column_names, per_column


# ---------------------------------------------------------------------------
# Top-level transform
# ---------------------------------------------------------------------------
def build_blob(
    csv_path: Path,
    players_blob: Dict[str, dict],
    *,
    format_label: str,
) -> Tuple[Dict[str, object], List[str], List[str]]:
    """Parse a CSV and produce the full output blob plus diagnostics.

    Returns ``(blob, ambiguous_names, unmatched_names)``.
    """
    column_names, per_column = parse_csv(csv_path)
    name_index = build_name_index(players_blob)

    players_out: Dict[str, dict] = {}
    picks_out: Dict[str, dict] = {}
    ambiguous: List[str] = []
    unmatched: List[str] = []

    for col in column_names:
        series = per_column.get(col) or {}
        if not series:
            # Column is entirely empty (every cell blank). Skip silently.
            continue
        if is_pick_column(col):
            pid = pick_id(col)
            picks_out[pid] = {
                "label": col.strip(),
                "values": series,
            }
            continue
        sid, meta, status = resolve_player(col, name_index, players_blob)
        if status == "ambiguous":
            ambiguous.append(col)
        if status == "unmatched":
            unmatched.append(col)
            continue  # don't drop into players_out without a sleeper id
        # We always have an sid here unless status was "unmatched".
        assert sid is not None
        # Merge if the same sid shows up twice (CSV header dupes -- not
        # expected, but be defensive).
        existing = players_out.get(sid)
        if existing:
            existing["values"].update(series)
            continue
        players_out[sid] = {
            "name": (meta.get("full_name") if meta else col.strip()),
            "fantasy_positions": (meta.get("fantasy_positions") if meta else []) or [],
            "values": series,
        }

    blob = {
        "format": format_label,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_file": csv_path.name,
        "n_players": len(players_out),
        "n_picks": len(picks_out),
        "n_unmatched": len(unmatched),
        "n_ambiguous": len(ambiguous),
        "players": players_out,
        "picks": picks_out,
        "ambiguous_names": ambiguous,
        "unmatched_names": unmatched,
    }
    return blob, ambiguous, unmatched


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Ingest historical KTC CSVs.")
    p.add_argument("--csv", required=True, type=Path,
                   help="Path to the historical CSV to ingest.")
    p.add_argument("--format", required=True, choices=("1qb", "superflex"),
                   help="Which KTC format this CSV represents.")
    p.add_argument("--players-fixture", type=Path, default=PLAYERS_FIXTURE,
                   help="Sleeper players.json blob used for name -> id matching.")
    p.add_argument("--out", type=Path, default=None,
                   help="Output JSON path. Defaults to "
                        "tests/fixtures/blobs/trade_eval_ktc_history_<format>.json.")
    p.add_argument("--report-unmatched", action="store_true",
                   help="Print unmatched / ambiguous CSV columns.")
    return p.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = _parse_args(argv)
    if not args.csv.exists():
        print(f"CSV not found: {args.csv}", file=sys.stderr)
        return 2
    if not args.players_fixture.exists():
        print(f"players.json fixture not found: {args.players_fixture}", file=sys.stderr)
        return 2

    with args.players_fixture.open("r", encoding="utf-8") as f:
        players_blob = json.load(f)

    blob, ambiguous, unmatched = build_blob(
        args.csv, players_blob, format_label=args.format,
    )

    out_path = args.out or (DEFAULT_OUT_DIR / f"trade_eval_ktc_history_{args.format}.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as f:
        json.dump(blob, f, ensure_ascii=False)

    n_players = blob["n_players"]
    n_picks = blob["n_picks"]
    print(f"Wrote {out_path}")
    print(f"  format={args.format}  players={n_players}  picks={n_picks}")
    print(f"  ambiguous={len(ambiguous)}  unmatched={len(unmatched)}")
    if args.report_unmatched and unmatched:
        print("  Unmatched CSV columns:")
        for n in unmatched:
            print(f"    - {n}")
    if args.report_unmatched and ambiguous:
        print("  Ambiguous CSV columns (auto-picked first):")
        for n in ambiguous:
            print(f"    - {n}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
