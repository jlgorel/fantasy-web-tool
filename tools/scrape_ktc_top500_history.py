"""One-shot scraper: pull full daily value history for KTC's top 500 dynasty assets.

For each slug discovered from the rankings page, fetch the player page and
brace-extract two server-rendered JS vars:

    var playerOneQB     = { overallValue, overallRankHistory, positionalRankHistory, ... }
    var playerSuperflex = { ... same shape ... }

Each ``overallValue`` is a list of ``{"d":"YYMMDD","v":<int>}`` daily points,
typically 1000+ entries per player going back to 2023-03.

Output (resumable, per slug):
    tools/scraped_ktc_history/<slug>.json

Each file::

    {
      "slug": "bijan-robinson-1414",
      "ktc_player_id": 1414,
      "name": "Bijan Robinson",
      "position": "RB",
      "team": "ATL",
      "age": 24.3,
      "is_pick": false,
      "scraped_at_utc": "...",
      "oneQB": {
        "value_history":  {"2023-03-10": 7890, ...},
        "overall_rank_history": {"2023-03-10": 7, ...},
        "positional_rank_history": {"2023-03-10": 2, ...}
      },
      "superflex": { ... same shape ... }
    }

Re-runs skip slugs already present (unless --force).

Polite: 1.5s delay between requests by default (~13 min for 500 slugs).
Idempotent and crash-safe: each slug is written as it completes.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
OUT_DIR = REPO / "tools" / "scraped_ktc_history"
OUT_DIR.mkdir(parents=True, exist_ok=True)
MANIFEST = OUT_DIR / "_manifest.json"

RANKINGS_URL = "https://keeptradecut.com/dynasty-rankings?format={fmt}&filters=QB|WR|RB|TE|RDP&p=0"
PLAYER_URL = "https://keeptradecut.com/dynasty-rankings/players/{slug}"

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

PLAYERS_ARRAY_RE = re.compile(r"var\s+playersArray\s*=\s*(\[.*?\])\s*;", re.DOTALL)


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------
def http_get(url: str, *, timeout: int = 30, retries: int = 3, backoff: float = 4.0) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html,*/*"})
    last_err: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read().decode("utf-8", errors="replace")
        except (urllib.error.URLError, TimeoutError) as e:
            last_err = e
            if attempt < retries:
                time.sleep(backoff * attempt)
    raise RuntimeError(f"GET {url} failed after {retries} attempts: {last_err}")


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------
def slice_var_object(html: str, name: str) -> str:
    """Brace-match ``var <name> = { ... }`` and return the JSON text."""
    needle = f"var {name} = "
    i = html.find(needle)
    if i < 0:
        raise ValueError(f"{name!r} not found")
    i += len(needle)
    if html[i] != "{":
        raise ValueError(f"unexpected start char {html[i]!r} after {name!r}")
    depth = 0
    j = i
    in_str = False
    esc = False
    while j < len(html):
        c = html[j]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
        else:
            if c == '"':
                in_str = True
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    return html[i : j + 1]
        j += 1
    raise ValueError(f"unterminated object for {name!r}")


def yymmdd_to_iso(s: str) -> str:
    """KTC ``251220`` -> ``2025-12-20``. Assumes 20YY."""
    return _dt.date(2000 + int(s[:2]), int(s[2:4]), int(s[4:6])).isoformat()


def history_to_dated_map(arr: Any) -> dict[str, int | float]:
    if not isinstance(arr, list):
        return {}
    out: dict[str, int | float] = {}
    for e in arr:
        if not isinstance(e, dict):
            continue
        d, v = e.get("d"), e.get("v")
        if not isinstance(d, str) or len(d) != 6 or not d.isdigit() or v is None:
            continue
        try:
            out[yymmdd_to_iso(d)] = v
        except ValueError:
            continue
    return out


def extract_player_record(html: str, ranking_row: dict[str, Any]) -> dict[str, Any]:
    """Pull both formats' histories. ``ranking_row`` supplies bio metadata."""
    formats: dict[str, dict[str, Any]] = {}
    for fmt_key, var_name in (("oneQB", "playerOneQB"), ("superflex", "playerSuperflex")):
        obj = json.loads(slice_var_object(html, var_name))
        formats[fmt_key] = {
            "value_history": history_to_dated_map(obj.get("overallValue")),
            "overall_rank_history": history_to_dated_map(obj.get("overallRankHistory")),
            "positional_rank_history": history_to_dated_map(obj.get("positionalRankHistory")),
        }
    name = ranking_row.get("playerName", "")
    position = ranking_row.get("position")
    return {
        "slug": ranking_row.get("slug"),
        "ktc_player_id": ranking_row.get("playerID"),
        "name": name,
        "position": position,
        "team": ranking_row.get("team"),
        "age": ranking_row.get("age"),
        "is_pick": position == "RDP",
        "scraped_at_utc": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "oneQB": formats["oneQB"],
        "superflex": formats["superflex"],
    }


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------
def discover_top_n(verbose: bool = True) -> list[dict[str, Any]]:
    """Fetch both rankings pages, return deduped union (preserving 1QB order)."""
    rows: dict[str, dict[str, Any]] = {}
    for fmt in (1, 2):
        url = RANKINGS_URL.format(fmt=fmt)
        if verbose:
            print(f"  GET {url}")
        html = http_get(url)
        m = PLAYERS_ARRAY_RE.search(html)
        if not m:
            raise RuntimeError(f"playersArray not found at format={fmt}")
        arr = json.loads(m.group(1))
        for p in arr:
            slug = p.get("slug")
            if slug and slug not in rows:
                rows[slug] = p
    return list(rows.values())


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--delay", type=float, default=1.5, help="seconds between player requests")
    ap.add_argument("--force", action="store_true", help="re-scrape slugs even if cached")
    ap.add_argument("--limit", type=int, default=None, help="cap number of slugs (for testing)")
    ap.add_argument("--only", default=None, help="comma-separated slug substring filter")
    args = ap.parse_args()

    print(f"Output dir: {OUT_DIR}")
    print("Discovering top players from KTC rankings (1QB + SF)...")
    rows = discover_top_n()
    print(f"  union: {len(rows)} slugs")

    if args.only:
        needles = [s.strip().lower() for s in args.only.split(",") if s.strip()]
        rows = [r for r in rows if any(n in r["slug"].lower() for n in needles)]
        print(f"  filtered by --only: {len(rows)}")
    if args.limit:
        rows = rows[: args.limit]
        print(f"  capped at --limit {args.limit}")

    manifest: dict[str, Any] = {}
    if MANIFEST.exists():
        try:
            manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            manifest = {}

    successes = 0
    skips = 0
    failures: list[tuple[str, str]] = []
    t0 = time.monotonic()

    for i, row in enumerate(rows, 1):
        slug = row["slug"]
        out_path = OUT_DIR / f"{slug}.json"
        if out_path.exists() and not args.force:
            skips += 1
            if i % 50 == 0:
                print(f"[{i}/{len(rows)}] (cached) {slug}")
            continue
        url = PLAYER_URL.format(slug=slug)
        try:
            html = http_get(url)
            record = extract_player_record(html, row)
            out_path.write_text(json.dumps(record, indent=2), encoding="utf-8")
            n1 = len(record["oneQB"]["value_history"])
            n2 = len(record["superflex"]["value_history"])
            elapsed = time.monotonic() - t0
            rate = (i - skips) / elapsed if elapsed > 0 else 0
            eta = (len(rows) - i) / rate if rate > 0 else 0
            print(
                f"[{i}/{len(rows)}] {slug:50s}  1QB={n1:4d} SF={n2:4d}  "
                f"({elapsed:6.1f}s elapsed, ETA {eta/60:5.1f}m)"
            )
            successes += 1
            manifest[slug] = {
                "ktc_player_id": record["ktc_player_id"],
                "name": record["name"],
                "position": record["position"],
                "is_pick": record["is_pick"],
                "n_oneQB": n1,
                "n_sf": n2,
                "scraped_at_utc": record["scraped_at_utc"],
            }
            if successes % 25 == 0:
                MANIFEST.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        except Exception as e:  # noqa: BLE001
            print(f"[{i}/{len(rows)}] FAIL {slug}: {e}", file=sys.stderr)
            failures.append((slug, str(e)))
        time.sleep(args.delay)

    MANIFEST.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"\nDone. success={successes} skipped={skips} failed={len(failures)}")
    if failures:
        print("\nFailures:")
        for slug, err in failures[:20]:
            print(f"  {slug}: {err}")
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
