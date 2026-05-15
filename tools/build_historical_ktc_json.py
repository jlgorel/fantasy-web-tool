"""Merge CSV historicals + per-slug scraped history into one canonical blob.

Inputs:
  * ``tools/1qbhistorical.csv`` and ``tools/sfhistorical.csv``
    -- weekly Google Sheet history, name-keyed, dating back to ~2020.
  * ``tools/scraped_ktc_history/<slug>.json``
    -- 500 per-player files produced by ``scrape_ktc_top500_history.py``,
       each with daily ``oneQB`` + ``superflex`` value histories from KTC.

Output: ``tests/fixtures/blobs/historical_KTC_rankings.json``::

    {
      "<sleeper_id>": {
        "name": "Bijan Robinson",
        "position": "RB", "team": "ATL",
        "ktc_player_id": 1414, "ktc_slug": "bijan-robinson-1414",
        "fantasy_positions": ["RB"],
        "1QB_Historical": { "2023-03-10": 7890, ..., "2026-05-13": 9999 },
        "SF_Historical":  { "2023-03-10": 7651, ..., "2026-05-13": 9997 }
      },
      "pick:2026_early_1st": {
        "label": "2026 Early 1st",
        "ktc_player_id": 1527,
        "ktc_slug": "2026-early-1st-1527",
        "is_pick": true,
        "1QB_Historical": { ... },
        "SF_Historical":  { ... }
      },
      ...
    }

Merge rules:
  * Daily scrape values always win over weekly CSV values when dates collide.
  * CSV-only dates (older than the ~3-year scrape window) are kept.
  * Scrape-only slugs not present in CSV (2025+ rookies, recent picks) appear
    with scrape data only.
  * CSV-only entries (retired vets dropped from KTC top-500) appear with CSV
    data only.

Picks: CSV columns like "2024 Mid 1st" map to ``pick:2024_mid_1st``. Scraped
pick slugs like ``2024-mid-1st-1234`` map to the same key, so the two streams
merge cleanly.

Run::

    python tools/build_historical_ktc_json.py
"""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Reuse helpers from the existing CSV ingester.
from ingest_ktc_history import (
    build_name_index,
    is_pick_column,
    normalize_name,
    parse_csv,
    pick_id,
    resolve_player,
)

REPO = Path(__file__).resolve().parent.parent
CSV_1QB = REPO / "tools" / "1qbhistorical.csv"
CSV_SF = REPO / "tools" / "sfhistorical.csv"
SCRAPE_DIR = REPO / "tools" / "scraped_ktc_history"
PLAYERS_FIXTURE = REPO / "tests" / "fixtures" / "blobs" / "players.json"
OUT_PATH = REPO / "tests" / "fixtures" / "blobs" / "historical_KTC_rankings.json"

# Match a scraped pick slug like "2026-early-1st-1527" -> ("2026","early","1st")
_PICK_SLUG_RE = re.compile(r"^(\d{4})-(early|mid|late)-(1st|2nd|3rd|4th)-\d+$", re.IGNORECASE)


def pick_key_for_slug(slug: str) -> str | None:
    m = _PICK_SLUG_RE.match(slug)
    if not m:
        return None
    year, tier, rnd = m.group(1), m.group(2).lower(), m.group(3).lower()
    return f"pick:{year}_{tier}_{rnd}"


def merge_dated(dst: dict[str, float], src: dict[str, float], *, src_wins: bool) -> None:
    if src_wins:
        dst.update(src)
    else:
        for k, v in src.items():
            dst.setdefault(k, v)


def load_csv_streams() -> dict[str, dict[str, dict]]:
    """Parse both CSVs into per-column date->value maps.

    Returns ``{"1qb": {col: {date: v}}, "superflex": {col: {date: v}}}``.
    """
    print("Loading CSVs...")
    out: dict[str, dict[str, dict]] = {}
    for fmt, path in [("1qb", CSV_1QB), ("superflex", CSV_SF)]:
        cols, per_col = parse_csv(path)
        nonempty = {c: v for c, v in per_col.items() if v}
        print(f"  {fmt:9s}: {len(cols)} columns, {len(nonempty)} non-empty")
        out[fmt] = nonempty
    return out


def load_scraped_slugs() -> dict[str, dict]:
    """Load every per-slug JSON from the scrape output dir."""
    print(f"Loading scraped slugs from {SCRAPE_DIR}...")
    files = sorted(p for p in SCRAPE_DIR.glob("*.json") if p.name != "_manifest.json")
    out: dict[str, dict] = {}
    for p in files:
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            print(f"  SKIP {p.name}: {e}", file=sys.stderr)
            continue
        slug = data.get("slug") or p.stem
        out[slug] = data
    print(f"  loaded {len(out)} slugs")
    return out


def main() -> int:
    print("== Building historical_KTC_rankings.json ==\n")

    if not PLAYERS_FIXTURE.exists():
        print(f"FATAL: {PLAYERS_FIXTURE} missing", file=sys.stderr)
        return 2
    players_blob = json.loads(PLAYERS_FIXTURE.read_text(encoding="utf-8"))
    name_index = build_name_index(players_blob)
    print(f"sleeper players.json: {len(players_blob)} rows, "
          f"{len(name_index)} normalized-name keys\n")

    csv_streams = load_csv_streams()
    scraped = load_scraped_slugs()
    print()

    # Final output: id -> record
    out: dict[str, dict[str, Any]] = {}
    csv_unmatched: list[str] = []
    csv_ambiguous: list[str] = []

    # --------- Step 1: seed from scraped (authoritative daily history) ---
    for slug, data in scraped.items():
        is_pick = bool(data.get("is_pick"))
        name = data.get("name") or ""
        if is_pick:
            key = pick_key_for_slug(slug)
            if key is None:
                # Slug didn't match pick regex -- shouldn't happen for RDP,
                # but fall back to slug-based id.
                key = f"pick:{slug}"
            record = out.setdefault(key, {
                "label": name,
                "ktc_player_id": data.get("ktc_player_id"),
                "ktc_slug": slug,
                "is_pick": True,
                "1QB_Historical": {},
                "SF_Historical": {},
            })
        else:
            sid, meta, status = resolve_player(name, name_index, players_blob)
            if status == "unmatched":
                # Use slug as fallback key; downstream can still find by name.
                key = f"ktc:{data.get('ktc_player_id') or slug}"
            else:
                key = sid  # type: ignore[assignment]
            record = out.setdefault(key, {
                "name": (meta.get("full_name") if meta else name),
                "position": data.get("position"),
                "team": data.get("team"),
                "age": data.get("age"),
                "fantasy_positions": (meta.get("fantasy_positions") if meta else []) or [],
                "ktc_player_id": data.get("ktc_player_id"),
                "ktc_slug": slug,
                "sleeper_id": sid if status != "unmatched" else None,
                "1QB_Historical": {},
                "SF_Historical": {},
            })
        # Always overwrite with scraped daily values (authoritative).
        record["1QB_Historical"].update(data.get("oneQB", {}).get("value_history", {}))
        record["SF_Historical"].update(data.get("superflex", {}).get("value_history", {}))

    print(f"  after scrape merge: {len(out)} ids")

    # --------- Step 2: fold in CSV history (weekly, older window) --------
    # Build a reverse map: sleeper_id -> existing record (for fast attach)
    # and a normalized-name -> existing record fallback for unmatched cases.
    sid_to_key: dict[str, str] = {}
    name_to_key: dict[str, str] = {}
    for k, rec in out.items():
        sid = rec.get("sleeper_id")
        if sid:
            sid_to_key[sid] = k
        n = rec.get("name") or rec.get("label") or ""
        if n:
            name_to_key.setdefault(normalize_name(n), k)

    def ensure_player_record(csv_col: str) -> str | None:
        """Return the out[] key for this CSV player column, creating if needed."""
        sid, meta, status = resolve_player(csv_col, name_index, players_blob)
        if status == "ambiguous":
            csv_ambiguous.append(csv_col)
        if status == "unmatched":
            # Try a name-fuzzy hit against existing records (scraped slugs
            # may have slightly different display names than CSV).
            k = name_to_key.get(normalize_name(csv_col))
            if k:
                return k
            csv_unmatched.append(csv_col)
            return None
        assert sid is not None
        k = sid_to_key.get(sid)
        if k is None:
            # CSV-only player (retired vet, dropped from KTC top-500).
            k = sid
            out[k] = {
                "name": (meta.get("full_name") if meta else csv_col.strip()),
                "position": None,
                "team": None,
                "age": None,
                "fantasy_positions": (meta.get("fantasy_positions") if meta else []) or [],
                "ktc_player_id": None,
                "ktc_slug": None,
                "sleeper_id": sid,
                "1QB_Historical": {},
                "SF_Historical": {},
            }
            sid_to_key[sid] = k
            name_to_key.setdefault(
                normalize_name(meta.get("full_name") if meta else csv_col), k
            )
        return k

    def ensure_pick_record(csv_col: str) -> str:
        key = pick_id(csv_col)  # already "pick:2024_mid_1st" shape
        if key not in out:
            out[key] = {
                "label": csv_col.strip(),
                "ktc_player_id": None,
                "ktc_slug": None,
                "is_pick": True,
                "1QB_Historical": {},
                "SF_Historical": {},
            }
        return key

    csv_only_players = 0
    for fmt_label, hist_key in [("1qb", "1QB_Historical"), ("superflex", "SF_Historical")]:
        streams = csv_streams[fmt_label]
        before = len(out)
        for col, series in streams.items():
            if not series:
                continue
            if is_pick_column(col):
                k = ensure_pick_record(col)
            else:
                k = ensure_player_record(col)
            if k is None:
                continue
            # Scraped daily values win on overlap -- setdefault leaves them
            # untouched, only adds new dates that the scrape didn't cover.
            target = out[k][hist_key]
            for date_str, val in series.items():
                target.setdefault(date_str, val)
        csv_only_players = max(csv_only_players, len(out) - before)
    print(f"  after CSV merge: {len(out)} ids "
          f"(added ~{csv_only_players} csv-only entries)")

    # --------- Stats / output --------------------------------------------
    n_players = sum(1 for r in out.values() if not r.get("is_pick"))
    n_picks = sum(1 for r in out.values() if r.get("is_pick"))
    n_with_sleeper_id = sum(1 for r in out.values() if r.get("sleeper_id"))
    n_only_csv = sum(
        1 for r in out.values()
        if not r.get("ktc_slug") and not r.get("is_pick")
    )
    n_only_scrape = sum(
        1 for r in out.values()
        if r.get("ktc_slug") and not r.get("is_pick")
        and len(r["1QB_Historical"]) + len(r["SF_Historical"]) > 0
        and not any(d < "2023-01-01" for d in r["1QB_Historical"])
    )
    sample = next(
        (r for k, r in out.items() if k == "1814" or (r.get("name") == "Bijan Robinson")),
        None,
    )

    # Stable, compact JSON keyed by id with date subdicts. Use indent=None
    # for the date maps (one line each) to keep file size manageable.
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    blob = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "n_records": len(out),
        "n_players": n_players,
        "n_picks": n_picks,
        "n_with_sleeper_id": n_with_sleeper_id,
        "n_csv_only_entries": n_only_csv,
        "csv_ambiguous": csv_ambiguous,
        "csv_unmatched": csv_unmatched,
        "records": out,
    }
    OUT_PATH.write_text(json.dumps(blob, ensure_ascii=False), encoding="utf-8")
    size = OUT_PATH.stat().st_size

    print()
    print(f"Wrote {OUT_PATH}")
    print(f"  size: {size/1024:,.0f} KB ({size:,} bytes)")
    print(f"  total records: {len(out)}")
    print(f"    players: {n_players}  (with sleeper_id: {n_with_sleeper_id})")
    print(f"    picks:   {n_picks}")
    print(f"    csv-only (no ktc slug): {n_only_csv}")
    print(f"  csv ambiguous: {len(csv_ambiguous)}")
    print(f"  csv unmatched: {len(csv_unmatched)}")
    if csv_unmatched:
        for n in csv_unmatched[:10]:
            print(f"    - {n}")
        if len(csv_unmatched) > 10:
            print(f"    ... +{len(csv_unmatched)-10} more")

    if sample is not None:
        dates_1qb = sorted(sample["1QB_Historical"].keys())
        print(f"\n  sample: {sample.get('name')!r}  ktc_id={sample.get('ktc_player_id')}")
        print(f"    1QB history: {len(dates_1qb)} dates "
              f"{dates_1qb[0]} -> {dates_1qb[-1]}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
