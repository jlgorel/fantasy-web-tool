"""Build per-season ADP blobs for draft simulation.

The current season uses format/team-size-specific FantasyPros DraftWizard mock
ADP (including observed standard deviation). Historical backfills retain the
FantasyFootballCalculator API, which reaches back to 2007:

    https://fantasyfootballcalculator.com/api/v1/adp/{format}?teams=N&year=YYYY

Current draftable players resolve against ``players.json`` and produce an
ADP-only pool; users can attach uploaded Value/VORP numbers without inheriting
stale prior-year projections. Historical ADP pairs onto each existing
``draft_rankings_{year}.json`` universe. Both paths write
``draft_adp_{year}.json``.
The draft sim then drives opponents off real ADP (with variance) instead of
VBD order, which is what stops the recommender from being "chalk".

Format mapping (FFC has no PPR split for superflex, so 2QB ADP is reused for
every superflex PPR variant):

    superflex            -> "2qb"
    1QB, full PPR        -> "ppr"
    1QB, half PPR        -> "half-ppr"
    1QB, standard (0)    -> "standard"

This is a LOCAL backfill helper (hits the network); it is intentionally not a
pytest target. The pure read side lives in ``rankings_source`` + ``summaries``.

Usage (PowerShell)::

    python tools/build_draft_adp.py                 # all years -> fixtures
    python tools/build_draft_adp.py --years 2024    # one year
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any, Dict, Optional

REPO = Path(__file__).resolve().parent.parent

# Load the pure helpers from the module file (avoid importing the Flask app pkg).
import importlib.util as _ilu  # noqa: E402

_RS_PATH = REPO / "backend" / "app" / "services" / "draft_help" / "rankings_source.py"
_spec = _ilu.spec_from_file_location("draft_rankings_source", _RS_PATH)
rankings_source = _ilu.module_from_spec(_spec)
sys.modules[_spec.name] = rankings_source
_spec.loader.exec_module(rankings_source)

SCHEMA_VERSION = rankings_source.SCHEMA_VERSION
normalize_player_name = rankings_source.normalize_player_name
rankings_blob_name = rankings_source.rankings_blob_name
adp_blob_name = rankings_source.adp_blob_name

# The production Azure Function and this local CLI share one pure builder.
_ADP_CORE_PATH = REPO / "azure-functions" / "draft_adp.py"
_adp_spec = _ilu.spec_from_file_location("draft_adp_core", _ADP_CORE_PATH)
draft_adp_core = _ilu.module_from_spec(_adp_spec)
sys.modules[_adp_spec.name] = draft_adp_core
_adp_spec.loader.exec_module(draft_adp_core)
sys.modules.setdefault("draft_adp", draft_adp_core)

_FP_ADP_PATH = REPO / "azure-functions" / "fantasypros_adp.py"
_fp_spec = _ilu.spec_from_file_location("fantasypros_adp_core", _FP_ADP_PATH)
fantasypros_adp_core = _ilu.module_from_spec(_fp_spec)
sys.modules[_fp_spec.name] = fantasypros_adp_core
_fp_spec.loader.exec_module(fantasypros_adp_core)

DEFAULT_BLOB_DIR = REPO / "tests" / "fixtures" / "blobs"
DEFAULT_YEARS = ("2022", "2023", "2024", "2025", "2026")

ffc_format = draft_adp_core.ffc_format
ffc_name_map = draft_adp_core.ffc_name_map


def fetch_ffc(fmt: str, teams: int, year: str) -> Optional[Dict[str, Any]]:
    url = draft_adp_core.ffc_url(fmt, teams, year)
    req = urllib.request.Request(url, headers={"User-Agent": "fantasy-web-tool/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.load(resp)
    except Exception as exc:  # noqa: BLE001 -- degrade gracefully on any failure
        print(f"      fetch failed ({fmt}, {teams}t, {year}): {exc}")
        return None
    if data.get("status") != "Success" or not data.get("players"):
        return None
    return data


def build_year(year: str, blob_dir: Path) -> Optional[Dict[str, Any]]:
    rankings_path = blob_dir / rankings_blob_name(year)
    rankings = (
        json.loads(rankings_path.read_text(encoding="utf-8"))
        if rankings_path.exists() else None
    )

    def fetch_url(url: str) -> Optional[Dict[str, Any]]:
        req = urllib.request.Request(url, headers={"User-Agent": "fantasy-web-tool/1.0"})
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.load(resp)
        except Exception as exc:  # noqa: BLE001
            print(f"      fetch failed ({url}): {exc}")
            return None
        finally:
            time.sleep(0.4)  # be polite to FFC
        return data

    is_current_year = str(year) == str(_dt.datetime.now().year)
    if is_current_year:
        players_path = blob_dir / "players.json"
        if not players_path.exists():
            print(f"  SKIP {year}: players.json not found")
            return None
        players = json.loads(players_path.read_text(encoding="utf-8"))

        def fetch_fp(url: str) -> Optional[str]:
            req = urllib.request.Request(
                url, headers={"User-Agent": "fantasy-web-tool/1.0"}
            )
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    return resp.read().decode("utf-8", errors="replace")
            except Exception as exc:  # noqa: BLE001
                print(f"      fetch failed ({url}): {exc}")
                return None
            finally:
                time.sleep(0.4)

        print("  current season: using FantasyPros DraftWizard ADP")
        blob = fantasypros_adp_core.build_fantasypros_adp_blob(
            str(year), players, fetch_text=fetch_fp,
        )
    elif rankings is not None:
        blob = draft_adp_core.build_adp_blob(
            str(year), rankings, fetch_json=fetch_url,
        )
    else:
        players_path = blob_dir / "players.json"
        if not players_path.exists():
            print(
                f"  SKIP {year}: neither {rankings_path.name} nor players.json found"
            )
            return None
        print(f"  {rankings_path.name} absent; building ADP-only player pool")
        players = json.loads(players_path.read_text(encoding="utf-8"))
        blob = draft_adp_core.build_adp_blob_from_players(
            str(year), players, fetch_json=fetch_url,
        )
    errors = draft_adp_core.validate_adp_blob(blob)
    if errors:
        print("  REJECTED: " + "; ".join(errors[:10]))
        return None
    for cfg_key, cfg in blob["configs"].items():
        print(
            f"    {cfg_key:<10} fmt={cfg['format']:<9} "
            f"matched {cfg['matched']:>3}/{cfg['total']}"
        )
    return blob


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--years", nargs="+", default=list(DEFAULT_YEARS))
    ap.add_argument("--blob-dir", type=Path, default=DEFAULT_BLOB_DIR)
    args = ap.parse_args()

    args.blob_dir.mkdir(parents=True, exist_ok=True)
    wrote = 0
    for year in args.years:
        print(f"Year {year}:")
        blob = build_year(str(year), args.blob_dir)
        if blob is None:
            continue
        out = args.blob_dir / adp_blob_name(year)
        out.write_text(json.dumps(blob, indent=0), encoding="utf-8")
        n = sum(c["matched"] for c in blob["configs"].values())
        print(f"  -> wrote {out} ({len(blob['configs'])} configs, {n} ADP rows)")
        wrote += 1

    print(f"Done. Wrote {wrote} ADP blob(s).")
    return 0 if wrote else 1


if __name__ == "__main__":
    raise SystemExit(main())
