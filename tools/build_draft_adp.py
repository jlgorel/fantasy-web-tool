"""Build per-season ADP blobs from FantasyFootballCalculator (FFC).

FFC exposes a clean, documented JSON ADP API -- historical (back to 2007),
per-format, per-team-size, and including a real standard deviation:

    https://fantasyfootballcalculator.com/api/v1/adp/{format}?teams=N&year=YYYY

We pair that ADP onto the *existing* ``draft_rankings_{year}.json`` player
universe (matched by normalized name + position, so the ``player_id`` keys line
up with the rankings/sim by construction) and write ``draft_adp_{year}.json``.
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

DEFAULT_BLOB_DIR = REPO / "tests" / "fixtures" / "blobs"
DEFAULT_YEARS = ("2022", "2023", "2024", "2025")

FFC_URL = "https://fantasyfootballcalculator.com/api/v1/adp/{fmt}?teams={teams}&year={year}&position=all"
_SKIP_POS = {"PK", "DEF", "K", "DST", "D/ST"}
# FFC ADP is published from ~12-team drafts; smaller/larger pools fall back to it.
_FALLBACK_TEAMS = 12


def ffc_format(ppr: float, superflex: bool) -> str:
    if superflex:
        return "2qb"
    if ppr >= 1.0:
        return "ppr"
    if ppr <= 0.0:
        return "standard"
    return "half-ppr"


def fetch_ffc(fmt: str, teams: int, year: str) -> Optional[Dict[str, Any]]:
    url = FFC_URL.format(fmt=fmt, teams=teams, year=year)
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


def ffc_name_map(data: Dict[str, Any]) -> Dict[Any, Dict[str, float]]:
    """Build ``{(norm_name, pos): {adp, stdev}}`` + name-only fallback keys."""
    out: Dict[Any, Dict[str, float]] = {}
    for pl in data.get("players", []):
        pos = (pl.get("position") or "").upper()
        if pos in _SKIP_POS:
            continue
        nm = normalize_player_name(pl.get("name"))
        if not nm:
            continue
        entry = {"adp": round(float(pl["adp"]), 2), "stdev": round(float(pl.get("stdev") or 0.0), 2)}
        out.setdefault((nm, pos), entry)
        out.setdefault((nm, None), entry)  # name-only fallback (different pos labels)
    return out


def build_year(year: str, blob_dir: Path) -> Optional[Dict[str, Any]]:
    rankings_path = blob_dir / rankings_blob_name(year)
    if not rankings_path.exists():
        print(f"  SKIP {year}: {rankings_path.name} not found")
        return None
    rankings = json.loads(rankings_path.read_text(encoding="utf-8"))

    ffc_cache: Dict[Any, Optional[Dict[str, Any]]] = {}
    configs_out: Dict[str, Any] = {}

    for cfg_key, cfg in rankings.get("configs", {}).items():
        teams = int(cfg.get("teams") or _FALLBACK_TEAMS)
        ppr = float(cfg.get("ppr") or 0.0)
        superflex = bool(cfg.get("superflex"))
        fmt = ffc_format(ppr, superflex)

        cache_key = (fmt, teams)
        if cache_key not in ffc_cache:
            data = fetch_ffc(fmt, teams, year)
            if data is None and teams != _FALLBACK_TEAMS:
                data = fetch_ffc(fmt, _FALLBACK_TEAMS, year)
            ffc_cache[cache_key] = data
            time.sleep(0.4)  # be polite to FFC
        data = ffc_cache[cache_key]

        name_map = ffc_name_map(data) if data else {}
        players_out: Dict[str, Dict[str, float]] = {}
        for p in cfg.get("players", []):
            pid = p.get("player_id")
            if not pid:
                continue
            nm = normalize_player_name(p.get("name"))
            pos = (p.get("pos") or "").upper()
            hit = name_map.get((nm, pos)) or name_map.get((nm, None))
            if hit:
                players_out[str(pid)] = hit

        total = len(cfg.get("players", []))
        configs_out[cfg_key] = {
            "teams": teams,
            "ppr": ppr,
            "superflex": superflex,
            "format": fmt,
            "total_drafts": (data or {}).get("meta", {}).get("total_drafts"),
            "matched": len(players_out),
            "total": total,
            "players": players_out,
        }
        print(f"    {cfg_key:<10} fmt={fmt:<9} matched {len(players_out):>3}/{total}")

    return {
        "schema_version": SCHEMA_VERSION,
        "year": str(year),
        "source": "fantasyfootballcalculator",
        "generated_at_utc": _dt.datetime.utcnow().isoformat() + "Z",
        "configs": configs_out,
    }


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
