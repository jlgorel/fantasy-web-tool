"""Cached loader for the historical KTC value blob.

The blob is ~33 MB (multi-year daily KTC values for ~700 players and
all relevant draft picks) and feeds the value-integral trade evaluator
used by the wrapped pipeline. We do NOT want to download it more than
once per process, so this module wraps :func:`app.services.blob_store.load_blob`
with an LRU cache and pre-flattens the per-format value series.

Two outputs are produced per format ("1qb" / "superflex"):

* ``flat[asset_id] = {date_iso: float}`` -- the shape the trade
  evaluator's :func:`make_blob_resolver` expects.
* ``meta[asset_id] = {...}`` -- record-level metadata (display name,
  sleeper_id, ``is_pick``, etc.) so callers can hydrate UI labels
  without re-parsing the blob.

In dev / tests the blob can be sourced from a local path via the
``KTC_HISTORICAL_BLOB_PATH`` env var, which bypasses Azure entirely
and points at a JSON file on disk. This matches the pattern already
in use for the FantasyCalc / Sleeper fixture blobs (see
:mod:`app.services.blob_store`).
"""
from __future__ import annotations

import json
import logging
import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

# Canonical blob path in the ``fantasyjsons`` container. Mirrors
# ``azure-functions/trade_eval/blob_layout.py::ktc_historical_blob()``
# and the upload target in ``tools/upload_historical_ktc.py``. The
# blob is NOT at the container root -- it lives under the trade_eval
# prefix tree alongside the per-day KTC snapshots.
DEFAULT_BLOB_NAME = "trade_eval/values/ktc/historical_KTC_rankings.json"

# Per-format history keys inside the canonical blob shape.
_FORMAT_HISTORY_KEYS: Dict[str, str] = {
    "1qb": "1QB_Historical",
    "superflex": "SF_Historical",
}


def _load_raw_blob() -> Dict[str, Any]:
    """Return the raw blob dict, sourced from the env override or Azure.

    Env var ``KTC_HISTORICAL_BLOB_PATH`` is honored first; that's what
    local dev + the in-repo fixture path use. Otherwise we fall back to
    the project's Azure-blob loader.
    """
    override = os.environ.get("KTC_HISTORICAL_BLOB_PATH")
    if override:
        path = Path(override)
        if not path.is_file():
            raise FileNotFoundError(
                f"KTC_HISTORICAL_BLOB_PATH={override} but file is missing."
            )
        logger.info("Loading KTC historical blob from override %s", path)
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    # Lazy import so unit tests that stub the loader don't need the azure SDK.
    from app.services.blob_store import load_blob
    logger.info("Loading KTC historical blob '%s' from Azure", DEFAULT_BLOB_NAME)
    return load_blob(DEFAULT_BLOB_NAME)


@lru_cache(maxsize=1)
def get_raw_blob() -> Dict[str, Any]:
    """Cache the blob in memory for the lifetime of the process.

    Returns the verbatim JSON dict (``{"records": {...}, ...}``). Callers
    that just want the flattened resolver-friendly view should use
    :func:`get_flat_blob` instead.
    """
    return _load_raw_blob()


@lru_cache(maxsize=2)  # one entry per format
def get_flat_blob(fmt: str = "1qb") -> Tuple[Dict[str, Dict[str, float]], Dict[str, Dict[str, Any]]]:
    """Return ``(flat, meta)`` for the requested format.

    ``flat`` is the ``{asset_id: {date: value}}`` shape the evaluator's
    blob resolver consumes. ``meta`` exposes the same keys with the
    record-level metadata stripped of the bulky history fields, so the
    UI can pull a player's display name + sleeper_id without re-parsing.

    Raises ``ValueError`` for unknown formats.
    """
    history_key = _FORMAT_HISTORY_KEYS.get(fmt)
    if history_key is None:
        raise ValueError(
            f"unknown KTC blob format: {fmt!r}; expected one of "
            f"{sorted(_FORMAT_HISTORY_KEYS)}"
        )
    blob = get_raw_blob()
    records = blob.get("records") or {}
    flat: Dict[str, Dict[str, float]] = {}
    meta: Dict[str, Dict[str, Any]] = {}
    for asset_id, rec in records.items():
        history = rec.get(history_key)
        if not history:
            continue
        flat[asset_id] = history
        # Strip the heavy fields; keep just labels + ids for hydration.
        meta[asset_id] = {
            k: v for k, v in rec.items()
            if k not in {"1QB_Historical", "SF_Historical"}
        }
    return flat, meta


def find_asset_id_by_sleeper_id(sleeper_id: str, fmt: str = "1qb") -> Optional[str]:
    """Look up the blob's asset_id (record key) for a given Sleeper id.

    The historical blob keys players by Sleeper id where available, so
    this is usually a direct membership check. Returns None when the
    Sleeper id is unknown -- the caller should fall through to a
    zero-value placeholder so a missing record doesn't break the trade.
    """
    flat, _ = get_flat_blob(fmt)
    return sleeper_id if sleeper_id in flat else None


def reset_cache() -> None:
    """Drop the in-memory cache. Used by tests."""
    get_raw_blob.cache_clear()
    get_flat_blob.cache_clear()


__all__ = [
    "DEFAULT_BLOB_NAME",
    "get_raw_blob",
    "get_flat_blob",
    "find_asset_id_by_sleeper_id",
    "reset_cache",
]
