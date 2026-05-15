"""FantasyCalc daily value snapshot scraper.

We snapshot the FantasyCalc ``/values/current`` API for each format we
care about (1QB and Superflex, dynasty, half-PPR) once a day so that, going
forward, we have our own trustworthy time series of FantasyCalc values to
feed the trade-evaluator's value integral.

Storage: ``trade_eval/values/fantasycalc/{format_key}/{YYYY-MM-DD}.json``.
The blob is the FantasyCalc payload reshaped to a dict keyed by
``sleeperId``::

    {
      "<sleeperId>": {
        "name": "Justin Jefferson",
        "position": "WR",
        "team": "MIN",
        "age": 26,
        "value": 9999,
        "overallRank": 1,
        "positionRank": 1,
        "trend30Day": 12
      },
      ...
    }

Same-day re-runs overwrite -- the snapshot is by *date*, not by run.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from . import blob_layout

logger = logging.getLogger(__name__)


def values_url(num_qbs: int, *, is_dynasty: bool = True, ppr: float = 0.5,
               num_teams: int = 12) -> str:
    """Build a FantasyCalc ``/values/current`` URL."""
    return (
        "https://api.fantasycalc.com/values/current"
        f"?isDynasty={'true' if is_dynasty else 'false'}"
        f"&numQbs={num_qbs}"
        f"&numTeams={num_teams}"
        f"&ppr={ppr}"
    )


# ---------------------------------------------------------------------------
# Pure parsing
# ---------------------------------------------------------------------------
# Fields from the FantasyCalc ``player`` object we want to keep verbatim.
_PLAYER_KEEP_KEYS = (
    "name",
    "position",
    "maybeTeam",  # FantasyCalc field for team abbrev
    "maybeAge",
    "sleeperId",
    "mflId",
    "fleaflickerId",
    "espnId",
    "yahooId",
)

# Top-level row fields we keep alongside the player data.
_ROW_KEEP_KEYS = (
    "value",
    "overallRank",
    "positionRank",
    "trend30Day",
    "redraftValue",
    "combinedValue",
    "starter",
    "maybeMovingStandardDeviation",
    "maybeOwner",
)


def parse_payload(payload: Any) -> Dict[str, Dict[str, Any]]:
    """Reshape a FantasyCalc ``/values/current`` list-payload into a
    sleeperId-keyed dict.

    Players without a sleeperId are skipped (FantasyCalc occasionally
    surfaces a few non-Sleeper-mapped rookies; we don't need them for
    trade evaluation since the rest of our system is keyed on sleeperId).
    """
    if not isinstance(payload, list):
        logger.warning("FantasyCalc payload not a list: %s", type(payload))
        return {}

    out: Dict[str, Dict[str, Any]] = {}
    for row in payload:
        if not isinstance(row, dict):
            continue
        player = row.get("player") or {}
        sid = player.get("sleeperId")
        if not sid:
            continue
        record: Dict[str, Any] = {}
        for k in _PLAYER_KEEP_KEYS:
            if k in player:
                record[k] = player[k]
        for k in _ROW_KEEP_KEYS:
            if k in row:
                record[k] = row[k]
        out[str(sid)] = record
    return out


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------
HttpGetJson = Callable[[str], Any]
BlobUpload = Callable[[Dict[str, Any], str], None]
BlobLoad = Callable[[str], Optional[Dict[str, Any]]]


def snapshot_format(
    format_key: str,
    num_qbs: int,
    *,
    http_get_json: HttpGetJson,
    blob_upload: BlobUpload,
    date_iso: Optional[str] = None,
) -> Dict[str, Dict[str, Any]]:
    """Snapshot one (format, date) and upload it to blob storage.

    Returns the parsed dict for the caller / logging.
    """
    date_iso = date_iso or datetime.now(timezone.utc).date().isoformat()
    url = values_url(num_qbs)
    payload = http_get_json(url)
    parsed = parse_payload(payload)
    if not parsed:
        logger.warning(
            "FantasyCalc returned 0 usable rows for %s (%s) -- not overwriting blob.",
            format_key, url,
        )
        return parsed
    blob_upload(parsed, blob_layout.fantasycalc_snapshot_blob(format_key, date_iso))
    logger.info(
        "FantasyCalc snapshot uploaded: %s players for format=%s date=%s",
        len(parsed), format_key, date_iso,
    )
    return parsed


def snapshot_all(
    *,
    http_get_json: HttpGetJson,
    blob_upload: BlobUpload,
    blob_load: BlobLoad,
    date_iso: Optional[str] = None,
) -> Dict[str, int]:
    """Snapshot every format in :data:`blob_layout.FANTASYCALC_FORMATS`.

    Returns a ``{format_key: row_count}`` map. Updates the index blob.
    """
    date_iso = date_iso or datetime.now(timezone.utc).date().isoformat()
    counts: Dict[str, int] = {}
    for format_key, num_qbs in blob_layout.FANTASYCALC_FORMATS:
        try:
            parsed = snapshot_format(
                format_key, num_qbs,
                http_get_json=http_get_json,
                blob_upload=blob_upload,
                date_iso=date_iso,
            )
            counts[format_key] = len(parsed)
        except Exception:
            logger.exception("FantasyCalc snapshot failed for format=%s", format_key)
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
    index = blob_load(blob_layout.fantasycalc_index_blob()) or {"snapshots": {}}
    snapshots: Dict[str, Any] = index.setdefault("snapshots", {})
    formats_today: Dict[str, Any] = snapshots.setdefault(date_iso, {})
    for format_key, n in counts.items():
        formats_today[format_key] = n
    index["last_updated_utc"] = datetime.now(timezone.utc).isoformat()
    blob_upload(index, blob_layout.fantasycalc_index_blob())


__all__ = [
    "values_url",
    "parse_payload",
    "snapshot_format",
    "snapshot_all",
]
