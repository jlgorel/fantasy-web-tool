"""Daily top-500 KTC value appender.

Companion to the one-shot historical scrape (``tools/scrape_ktc_top500_history.py``
+ ``tools/build_historical_ktc_json.py``) that produced the canonical
:func:`blob_layout.ktc_historical_blob` blob. This module runs daily, fetches
KTC's current top-500 for both formats, and appends today's value to each
player's ``1QB_Historical`` / ``SF_Historical`` map.

Behavior:
  * Players present in today's scrape get today's value appended.
  * Records we *already had* (from historical seed) but absent from today's
    scrape get a ``0`` for today -- this preserves the time-series rhythm
    so the value-integral calculator can distinguish "dropped from top-500"
    from "missing data". Only records that have ever been scraped (i.e.
    have a ``ktc_player_id``) get zero-filled; CSV-only retired vets stay
    frozen.
  * Brand-new entrants (slugs we've never seen) get a fresh record with a
    single-day series. ``sleeper_id`` is resolved via the injected
    ``name_resolver`` if provided.

The shape of the rolling blob is documented on
:func:`blob_layout.ktc_historical_blob`.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional

from . import blob_layout, ktc_scraper

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Type aliases (IO injected for testability)
# ---------------------------------------------------------------------------
PageFetcher = Callable[[str], str]                  # url -> rendered HTML
BlobUpload = Callable[[Dict[str, Any], str], None]
BlobLoad = Callable[[str], Optional[Dict[str, Any]]]
NameResolver = Callable[[str], Optional[str]]       # name -> sleeper_id or None


# ---------------------------------------------------------------------------
# Pick name -> canonical key
# ---------------------------------------------------------------------------
# Matches "2026 Mid 1st" (the form KTC's playersArray uses for picks).
_PICK_NAME_RE = re.compile(
    r"^\s*(\d{4})\s+(early|mid|late)\s+(1st|2nd|3rd|4th)\s*$",
    re.IGNORECASE,
)


def pick_key_from_name(name: str) -> Optional[str]:
    """``"2026 Mid 1st"`` -> ``"pick:2026_mid_1st"``. Returns None if no match."""
    m = _PICK_NAME_RE.match(name or "")
    if not m:
        return None
    year, tier, rnd = m.group(1), m.group(2).lower(), m.group(3).lower()
    return f"pick:{year}_{tier}_{rnd}"


# ---------------------------------------------------------------------------
# Append logic
# ---------------------------------------------------------------------------
_HIST_KEY = {"1qb": "1QB_Historical", "superflex": "SF_Historical"}


def append_daily(
    *,
    fetch_page: PageFetcher,
    blob_upload: BlobUpload,
    blob_load: BlobLoad,
    name_resolver: Optional[NameResolver] = None,
    date_iso: Optional[str] = None,
    min_acceptable_rows: int = 400,
) -> Dict[str, Any]:
    """Scrape today's top-500 and append values to the rolling historical blob.

    ``min_acceptable_rows`` is a sanity guard: if either format returns far
    fewer rows than expected we abort rather than write a corrupt day.

    Returns a status dict suitable for logging / HTTP response.
    """
    date_iso = date_iso or datetime.now(timezone.utc).date().isoformat()

    # --- 1. Scrape both formats ------------------------------------------
    scrapes: Dict[str, Dict[str, Dict[str, Any]]] = {}
    for fmt_key, fmt_id in blob_layout.KTC_FORMATS:
        url = ktc_scraper.page_url(fmt_id)
        html = fetch_page(url)
        scrapes[fmt_key] = ktc_scraper.parse_html(html, fmt_id)

    counts_scraped = {k: len(v) for k, v in scrapes.items()}
    for fmt_key, parsed in scrapes.items():
        if len(parsed) < min_acceptable_rows:
            logger.warning(
                "KTC daily scrape returned only %d rows for %s -- below %d "
                "threshold; aborting append to protect historical blob.",
                len(parsed), fmt_key, min_acceptable_rows,
            )
            return {
                "status": "skipped_low_rows",
                "date": date_iso,
                "scraped": counts_scraped,
            }

    # --- 2. Load existing rolling blob ----------------------------------
    blob_path = blob_layout.ktc_historical_blob()
    blob = blob_load(blob_path) or {}
    records: Dict[str, Dict[str, Any]] = blob.setdefault("records", {})

    # Reverse index: ktc_player_id (as str) -> record key.
    ktc_to_key: Dict[str, str] = {}
    for k, rec in records.items():
        kid = rec.get("ktc_player_id")
        if kid is not None:
            ktc_to_key[str(kid)] = k

    # --- 3. Walk every scraped player ------------------------------------
    counts = {
        "new_entrants": 0,
        "appended": 0,
        "zero_filled": 0,
        "ambiguous_picks": 0,
    }
    seen_keys: set[str] = set()

    all_ktc_ids = set(scrapes["1qb"].keys()) | set(scrapes["superflex"].keys())
    for ktc_id in all_ktc_ids:
        # Prefer the 1QB row as the "representative" metadata row.
        row = scrapes["1qb"].get(ktc_id) or scrapes["superflex"].get(ktc_id) or {}
        is_pick = bool(row.get("is_pick"))
        name = row.get("name") or ""

        key = ktc_to_key.get(ktc_id)
        if key is None:
            # New entrant -- derive a stable record key.
            if is_pick:
                pkey = pick_key_from_name(name)
                if pkey is None:
                    # Pick row with a name we can't parse (very rare). Use a
                    # ktc-prefixed fallback so it doesn't collide with players.
                    pkey = f"pick:ktc:{ktc_id}"
                    counts["ambiguous_picks"] += 1
                key = pkey
                # If a pick record already exists at this key (e.g. seeded
                # from CSV with no ktc_player_id), enrich it.
                rec = records.get(key)
                if rec is None:
                    rec = {
                        "label": name,
                        "ktc_player_id": int(ktc_id),
                        "ktc_slug": None,
                        "is_pick": True,
                        "1QB_Historical": {},
                        "SF_Historical": {},
                    }
                    records[key] = rec
                    counts["new_entrants"] += 1
                else:
                    rec.setdefault("ktc_player_id", int(ktc_id))
                    rec.setdefault("is_pick", True)
                    rec.setdefault("1QB_Historical", {})
                    rec.setdefault("SF_Historical", {})
            else:
                # Player. Resolve sleeper_id from row if KTC gave us one, else
                # try the injected name resolver, else fall back to ktc:N key.
                sid = row.get("sleeper_id")
                if not sid and name_resolver is not None:
                    try:
                        sid = name_resolver(name)
                    except Exception:
                        logger.exception(
                            "name_resolver raised for new entrant %r", name)
                        sid = None
                key = sid or f"ktc:{ktc_id}"
                rec = records.get(key)
                if rec is None:
                    rec = {
                        "name": name,
                        "position": row.get("position"),
                        "team": row.get("team"),
                        "age": row.get("age"),
                        "fantasy_positions": (
                            [row["position"]] if row.get("position") else []
                        ),
                        "ktc_player_id": int(ktc_id),
                        "ktc_slug": None,
                        "sleeper_id": sid,
                        "is_pick": False,
                        "1QB_Historical": {},
                        "SF_Historical": {},
                    }
                    records[key] = rec
                    counts["new_entrants"] += 1
                else:
                    # Backfill metadata we may have been missing.
                    rec.setdefault("ktc_player_id", int(ktc_id))
                    if not rec.get("sleeper_id") and sid:
                        rec["sleeper_id"] = sid
                    rec.setdefault("1QB_Historical", {})
                    rec.setdefault("SF_Historical", {})
            ktc_to_key[ktc_id] = key
        else:
            rec = records[key]
            rec.setdefault("1QB_Historical", {})
            rec.setdefault("SF_Historical", {})
            # Backfill ktc_player_id on records that were seeded CSV-only.
            rec.setdefault("ktc_player_id", int(ktc_id))

        # Stamp today's value(s) for whichever formats had the player.
        for fmt_key, parsed in scrapes.items():
            r = parsed.get(ktc_id)
            if r is None:
                continue
            rec[_HIST_KEY[fmt_key]][date_iso] = int(r["value"])

        seen_keys.add(key)
        counts["appended"] += 1

    # --- 4. Zero-fill records we have but didn't see today ---------------
    # Only zero-fill records that have a ktc_player_id (i.e. have appeared
    # on KTC at some point). CSV-only entries stay untouched -- their last
    # CSV date is their last word.
    for key, rec in records.items():
        if key in seen_keys:
            continue
        if rec.get("ktc_player_id") is None:
            continue
        rec.setdefault("1QB_Historical", {})[date_iso] = 0
        rec.setdefault("SF_Historical", {})[date_iso] = 0
        counts["zero_filled"] += 1

    # --- 5. Bookkeeping + write ------------------------------------------
    blob["n_records"] = len(records)
    blob["last_updated_utc"] = datetime.now(timezone.utc).isoformat()
    blob_upload(blob, blob_path)

    logger.info(
        "KTC daily append complete: date=%s scraped=%s "
        "new=%d appended=%d zero_filled=%d",
        date_iso, counts_scraped,
        counts["new_entrants"], counts["appended"], counts["zero_filled"],
    )
    return {
        "status": "ok",
        "date": date_iso,
        "scraped": counts_scraped,
        **counts,
        "n_records": len(records),
    }


__all__ = [
    "pick_key_from_name",
    "append_daily",
]
