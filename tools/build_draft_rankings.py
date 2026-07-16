"""Build normalized draft-ranking blobs from the dynamic ranking spreadsheets.

The ``tests/fixtures/drafthelp/{year}Rankings.xlsm`` workbooks are dynamic
calculators: fill in the ``LeagueInfo`` sheet (team count, auction budget,
superflex, PPR) and Excel recomputes per-league auction $, VBD, tiers and an
overall value order on the position tabs. This tool drives Excel via
``xlwings`` across the full league-config grid (team sizes x PPR x superflex),
extracts each player's values, normalizes names to Sleeper player ids and
writes a ``draft_rankings_{year}.json`` blob.

This is a LOCAL one-time backfill helper: ``xlwings`` needs Excel installed and
cannot run in Azure/CI. It is intentionally not a pytest target -- the pure
parsing logic it relies on lives in
``backend/app/services/draft_help/rankings_source.py`` and is unit tested there.

Usage (PowerShell)::

    python tools/build_draft_rankings.py                  # all years -> fixtures
    python tools/build_draft_rankings.py --years 2024     # one year
    python tools/build_draft_rankings.py --teams 12 --ppr 0.5 --sf  # one config (debug)
    python tools/build_draft_rankings.py --visible        # watch Excel work
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO = Path(__file__).resolve().parent.parent

# Load the pure parsing/normalization helpers directly from the module file.
# Importing ``app.services.draft_help.rankings_source`` would pull in the Flask
# app package (``app/__init__.py``); this tool only needs the standalone module,
# so we load it by path to stay independent of the backend's runtime deps.
import importlib.util as _ilu  # noqa: E402

_RS_PATH = REPO / "backend" / "app" / "services" / "draft_help" / "rankings_source.py"
_spec = _ilu.spec_from_file_location("draft_rankings_source", _RS_PATH)
rankings_source = _ilu.module_from_spec(_spec)
sys.modules[_spec.name] = rankings_source  # let dataclasses resolve forward refs
_spec.loader.exec_module(rankings_source)

DEFAULT_AUCTION_BUDGET = rankings_source.DEFAULT_AUCTION_BUDGET
RANKED_POSITIONS = rankings_source.RANKED_POSITIONS
SCHEMA_VERSION = rankings_source.SCHEMA_VERSION
SUPPORTED_PPR = rankings_source.SUPPORTED_PPR
SUPPORTED_SUPERFLEX = rankings_source.SUPPORTED_SUPERFLEX
SUPPORTED_TEAM_SIZES = rankings_source.SUPPORTED_TEAM_SIZES
NameResolver = rankings_source.NameResolver
assign_overall_ranks = rankings_source.assign_overall_ranks
config_key = rankings_source.config_key
parse_position_sheet = rankings_source.parse_position_sheet
rankings_blob_name = rankings_source.rankings_blob_name

DEFAULT_SOURCE_DIR = REPO / "tests" / "fixtures" / "drafthelp"
DEFAULT_OUT_DIR = REPO / "tests" / "fixtures" / "blobs"
DEFAULT_PLAYERS = REPO / "tests" / "fixtures" / "blobs" / "players.json"
DEFAULT_YEARS = ("2022", "2023", "2024", "2025")

# LeagueInfo input cells (confirmed against 2024Rankings.xlsm).
CELL_TEAMS = "F2"
CELL_BUDGET = "F3"
CELL_QB_STARTERS = "D3"   # 2 == superflex/2QB, 1 == 1QB
CELL_REC_WR = "N5"
CELL_REC_RB = "J15"
CELL_REC_TE = "N15"

# Generous lower bound for reading position-sheet data rows; the parser stops
# at the first blank player cell. Widest sheet (WR) tops out around row 213.
_MAX_DATA_ROW = 260
_LAST_COL = 27  # COL_TIER (26, 0-based) + 1


def _load_resolver(players_path: Path) -> NameResolver:
    data = json.loads(players_path.read_text(encoding="utf-8"))
    return NameResolver(data)


def _read_position_rows(ws) -> List[List[Any]]:
    """Read a position sheet's data block (row 3..N) as a list of rows."""
    block = ws.range((3, 1), (_MAX_DATA_ROW, _LAST_COL)).value
    if block is None:
        return []
    # A single-row range comes back flat; normalize to list-of-lists.
    if block and not isinstance(block[0], list):
        block = [block]
    return block


def _build_config(
    wb, resolver: NameResolver, teams: int, ppr: float, superflex: bool,
    budget: int, max_overall: int,
) -> Dict[str, Any]:
    """Set LeagueInfo inputs, recalc, and extract one configuration."""
    li = wb.sheets["LeagueInfo"]
    li.range(CELL_TEAMS).value = teams
    li.range(CELL_BUDGET).value = budget
    li.range(CELL_QB_STARTERS).value = 2 if superflex else 1
    li.range(CELL_REC_WR).value = ppr
    li.range(CELL_REC_RB).value = ppr
    li.range(CELL_REC_TE).value = ppr
    wb.app.calculate()

    all_players = []
    unmatched: List[str] = []
    for pos in RANKED_POSITIONS:
        rows = _read_position_rows(wb.sheets[pos])
        players, missed = parse_position_sheet(pos, rows, resolver)
        all_players.extend(players)
        unmatched.extend(missed)

    ranked = assign_overall_ranks(all_players)
    if max_overall and max_overall > 0:
        ranked = [p for p in ranked if (p.overall_rank or 0) <= max_overall]
    return {
        "key": config_key(teams, ppr, superflex),
        "teams": teams,
        "ppr": ppr,
        "superflex": superflex,
        "budget": budget,
        "players": [p.to_dict() for p in ranked],
        "unmatched": unmatched,
    }


def build_year(
    year: str,
    source_dir: Path,
    resolver: NameResolver,
    configs: List[Dict[str, Any]],
    budget: int,
    max_overall: int,
    visible: bool,
) -> Optional[Dict[str, Any]]:
    """Drive Excel for one workbook across the requested configurations."""
    import xlwings as xw

    src = source_dir / f"{year}Rankings.xlsm"
    if not src.exists():
        print(f"  SKIP {year}: workbook not found at {src}")
        return None

    print(f"  Opening {src.name} ...")
    app = xw.App(visible=visible, add_book=False)
    app.display_alerts = False
    app.screen_updating = False
    blob_configs: Dict[str, Any] = {}
    unmatched_by_key: Dict[str, List[str]] = {}
    try:
        wb = app.books.open(str(src), update_links=False)
        for i, cfg in enumerate(configs, start=1):
            built = _build_config(
                wb, resolver, cfg["teams"], cfg["ppr"], cfg["superflex"], budget, max_overall
            )
            key = built.pop("key")
            unmatched = built.pop("unmatched")
            blob_configs[key] = built
            if unmatched:
                unmatched_by_key[key] = sorted(set(unmatched))
            print(
                f"    [{i:>2}/{len(configs)}] {key:<10} "
                f"players={len(built['players']):>3} unmatched={len(set(unmatched))}"
            )
        wb.close()
    finally:
        app.quit()

    return {
        "schema_version": SCHEMA_VERSION,
        "year": str(year),
        "source_file": src.name,
        "budget": budget,
        "generated_at_utc": _dt.datetime.utcnow().isoformat() + "Z",
        "configs": blob_configs,
        "unmatched_names": unmatched_by_key,
    }


def _resolve_configs(args) -> List[Dict[str, Any]]:
    teams = [args.teams] if args.teams else list(SUPPORTED_TEAM_SIZES)
    pprs = [args.ppr] if args.ppr is not None else list(SUPPORTED_PPR)
    if args.sf and args.one_qb:
        sfs = list(SUPPORTED_SUPERFLEX)
    elif args.sf:
        sfs = [True]
    elif args.one_qb:
        sfs = [False]
    else:
        sfs = list(SUPPORTED_SUPERFLEX)
    return [
        {"teams": t, "ppr": p, "superflex": sf}
        for t in teams
        for p in pprs
        for sf in sfs
    ]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--years", nargs="+", default=list(DEFAULT_YEARS))
    ap.add_argument("--source-dir", type=Path, default=DEFAULT_SOURCE_DIR)
    ap.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    ap.add_argument("--players", type=Path, default=DEFAULT_PLAYERS)
    ap.add_argument("--budget", type=int, default=DEFAULT_AUCTION_BUDGET)
    ap.add_argument("--max-overall", type=int, default=300,
                    help="Keep only the top-N players by overall value (0 = all). "
                         "Default 300 covers every realistically-drafted player.")
    ap.add_argument("--teams", type=int, help="Restrict to a single team size (debug).")
    ap.add_argument("--ppr", type=float, help="Restrict to a single PPR value (debug).")
    ap.add_argument("--sf", action="store_true", help="Include superflex configs.")
    ap.add_argument("--one-qb", action="store_true", help="Include 1QB configs.")
    ap.add_argument("--visible", action="store_true", help="Show Excel while running.")
    args = ap.parse_args()

    if not args.players.exists():
        print(f"FATAL: players.json not found at {args.players}", file=sys.stderr)
        return 2

    resolver = _load_resolver(args.players)
    configs = _resolve_configs(args)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    print(f"Configs per year: {len(configs)} | budget=${args.budget}")

    wrote = 0
    for year in args.years:
        print(f"Year {year}:")
        blob = build_year(year, args.source_dir, resolver, configs, args.budget, args.max_overall, args.visible)
        if blob is None:
            continue
        out = args.out_dir / rankings_blob_name(year)
        out.write_text(json.dumps(blob, indent=0), encoding="utf-8")
        n_players = sum(len(c["players"]) for c in blob["configs"].values())
        print(f"  -> wrote {out} ({len(blob['configs'])} configs, {n_players} player rows)")
        wrote += 1

    print(f"Done. Wrote {wrote} blob(s).")
    return 0 if wrote else 1


if __name__ == "__main__":
    raise SystemExit(main())
