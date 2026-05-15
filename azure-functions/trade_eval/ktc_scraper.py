"""KeepTradeCut (KTC) weekly value snapshot scraper.

KTC has no public API. Their dynasty rankings page embeds the entire
ranking in a ``<script>var playersArray = [...]</script>`` block, which is
parseable JSON. We fetch the page with Playwright (matching the existing
``boris_chen`` / ``fantasypros`` scrapers in :mod:`function_app`), extract
the array, normalize the fields we care about, and snapshot to blob.

Storage: ``trade_eval/values/ktc/{format_key}/{YYYY-MM-DD}.json``::

    {
      "<ktc_player_id>": {
        "name": "Justin Jefferson",
        "position": "WR",
        "team": "MIN",
        "age": 26,
        "value": 9999,                 # 1QB or SF value, depending on format
        "overall_rank": 1,
        "position_rank": 1,
        "is_pick": false,
        "sleeper_id": "6794"            # when KTC provides one (often null)
      },
      ...
    }

Picks (e.g. "2024 Mid 1st") are scraped too -- they have ``is_pick=True``
and use a synthetic key. They're useful for valuing pick-side trades but
the integral evaluator hands them off to the drafted player on draft day.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from . import blob_layout

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# URL builder
# ---------------------------------------------------------------------------
KTC_BASE_URL = "https://keeptradecut.com/dynasty-rankings"


def page_url(format_id: int) -> str:
    """KTC dynasty rankings URL.

    ``format_id``: 1 = 1QB, 2 = Superflex (matches the on-page query param).

    The ``filters`` param requests every position plus rookie draft picks
    so a single fetch yields the whole ranking.
    """
    return f"{KTC_BASE_URL}?format={format_id}&filters=QB|WR|RB|TE|RDP&p=0"


# ---------------------------------------------------------------------------
# Pure parsing
# ---------------------------------------------------------------------------
# Matches `var playersArray = [ ... ];` even if minified or whitespace-padded.
# We capture the bracketed array body. KTC's content is JSON-compatible (keys
# are quoted) so json.loads handles it directly.
_PLAYERS_ARRAY_RE = re.compile(
    r"var\s+playersArray\s*=\s*(\[.*?\])\s*;",
    re.DOTALL,
)


def extract_players_array(html: str) -> List[Dict[str, Any]]:
    """Pull ``playersArray`` out of a KTC dynasty-rankings page HTML.

    Raises ``ValueError`` if the array can't be located or parsed -- that's
    a strong signal KTC's page structure has changed and we should not
    silently overwrite snapshots with empty data.
    """
    m = _PLAYERS_ARRAY_RE.search(html)
    if not m:
        raise ValueError("KTC playersArray not found in page HTML")
    raw = m.group(1)
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        # Last-resort: KTC has historically used valid JSON; if that ever
        # changes we'd want to know rather than silently discarding.
        raise ValueError(f"KTC playersArray failed to parse as JSON: {e}") from e


def _player_value_for_format(player: Dict[str, Any], format_id: int) -> Optional[int]:
    """Pull the value/rank for the requested format from a KTC row.

    KTC's row shape (representative):
        {
          "playerName": "Justin Jefferson", "position": "WR", "team": "MIN",
          "age": 26.1, "playerID": 547,
          "oneQBValues":   {"value": 9999, "rank": 1, "positionalRank": 1, ...},
          "superflexValues": {"value": 9876, "rank": 3, "positionalRank": 1, ...}
        }
    """
    key = "oneQBValues" if format_id == 1 else "superflexValues"
    block = player.get(key) or {}
    val = block.get("value")
    return int(val) if val is not None else None


def _player_rank_for_format(player: Dict[str, Any], format_id: int) -> Dict[str, Optional[int]]:
    key = "oneQBValues" if format_id == 1 else "superflexValues"
    block = player.get(key) or {}
    return {
        "overall_rank": block.get("rank"),
        "position_rank": block.get("positionalRank"),
    }


def normalize_player(raw: Dict[str, Any], format_id: int) -> Optional[Dict[str, Any]]:
    """Convert one KTC player row to our snapshot shape.

    Returns ``None`` for rows that lack a value in the requested format
    (e.g., a 1QB-only row when scraping superflex, or a stub row).
    """
    pid = raw.get("playerID")
    if pid is None:
        return None
    value = _player_value_for_format(raw, format_id)
    if value is None:
        return None
    ranks = _player_rank_for_format(raw, format_id)
    name = raw.get("playerName")
    position = raw.get("position")
    is_pick = position == "RDP" or (isinstance(name, str) and ("Pick" in name or " 1st" in name or " 2nd" in name or " 3rd" in name or " 4th" in name))

    record: Dict[str, Any] = {
        "name": name,
        "position": position,
        "team": raw.get("team"),
        "age": raw.get("age"),
        "value": value,
        "overall_rank": ranks["overall_rank"],
        "position_rank": ranks["position_rank"],
        "is_pick": bool(is_pick),
    }
    # KTC sometimes exposes a sleeperPlayerID; preserve if present.
    sleeper = raw.get("sleeperPlayerID") or raw.get("sleeperId")
    if sleeper:
        record["sleeper_id"] = str(sleeper)
    return record


def parse_html(html: str, format_id: int) -> Dict[str, Dict[str, Any]]:
    """Full parse: HTML -> ``{ktc_player_id: normalized}`` dict."""
    raw_players = extract_players_array(html)
    out: Dict[str, Dict[str, Any]] = {}
    for raw in raw_players:
        if not isinstance(raw, dict):
            continue
        record = normalize_player(raw, format_id)
        if record is None:
            continue
        out[str(raw["playerID"])] = record
    return out


# ---------------------------------------------------------------------------
# Driver (HTTP / Playwright + blob IO injected for testability)
# ---------------------------------------------------------------------------
PageFetcher = Callable[[str], str]                    # url -> rendered HTML
BlobUpload = Callable[[Dict[str, Any], str], None]
BlobLoad = Callable[[str], Optional[Dict[str, Any]]]


def snapshot_format(
    format_key: str,
    format_id: int,
    *,
    fetch_page: PageFetcher,
    blob_upload: BlobUpload,
    date_iso: Optional[str] = None,
    min_acceptable_rows: int = 100,
) -> Dict[str, Dict[str, Any]]:
    """Scrape and snapshot a single KTC format.

    ``min_acceptable_rows`` is a sanity guard: if KTC returns way fewer
    rows than expected we treat it as a failed scrape rather than
    overwriting a healthy snapshot with garbage.
    """
    date_iso = date_iso or datetime.now(timezone.utc).date().isoformat()
    url = page_url(format_id)
    html = fetch_page(url)
    parsed = parse_html(html, format_id)
    if len(parsed) < min_acceptable_rows:
        logger.warning(
            "KTC scrape returned only %d rows for %s -- below %d threshold, "
            "skipping blob upload.",
            len(parsed), format_key, min_acceptable_rows,
        )
        return parsed
    blob_upload(parsed, blob_layout.ktc_snapshot_blob(format_key, date_iso))
    logger.info("KTC snapshot uploaded: %d rows for format=%s date=%s",
                len(parsed), format_key, date_iso)
    return parsed


def snapshot_all(
    *,
    fetch_page: PageFetcher,
    blob_upload: BlobUpload,
    blob_load: BlobLoad,
    date_iso: Optional[str] = None,
) -> Dict[str, int]:
    """Snapshot every format in :data:`blob_layout.KTC_FORMATS`."""
    date_iso = date_iso or datetime.now(timezone.utc).date().isoformat()
    counts: Dict[str, int] = {}
    for format_key, format_id in blob_layout.KTC_FORMATS:
        try:
            parsed = snapshot_format(
                format_key, format_id,
                fetch_page=fetch_page,
                blob_upload=blob_upload,
                date_iso=date_iso,
            )
            counts[format_key] = len(parsed)
        except Exception:
            logger.exception("KTC snapshot failed for format=%s", format_key)
            counts[format_key] = 0

    _bump_index(blob_load, blob_upload, date_iso=date_iso, counts=counts)
    return counts


def _bump_index(
    blob_load: BlobLoad,
    blob_upload: BlobUpload,
    *,
    date_iso: str,
    counts: Dict[str, int],
) -> None:
    index = blob_load(blob_layout.ktc_index_blob()) or {"snapshots": {}}
    snapshots: Dict[str, Any] = index.setdefault("snapshots", {})
    formats_today: Dict[str, Any] = snapshots.setdefault(date_iso, {})
    for format_key, n in counts.items():
        formats_today[format_key] = n
    index["last_updated_utc"] = datetime.now(timezone.utc).isoformat()
    blob_upload(index, blob_layout.ktc_index_blob())


__all__ = [
    "page_url",
    "extract_players_array",
    "normalize_player",
    "parse_html",
    "snapshot_format",
    "snapshot_all",
]
