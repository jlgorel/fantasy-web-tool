"""Per-season ownership-history backfill.

Builds the ``owned_history_{year}.json`` blob the Wrapped pipeline reads
to compute ``early_pickup`` / ``late_drop`` accolades. The shape mirrors
the in-season weekly write in ``function_app.get_sleeper_owned_for_week``::

    { "<player_id>": { "<week>": {"owned": <pct>, "started": <pct>, ...} } }

Pure (HTTP injected). Drives the one-shot historical backfill in
``tools/bootstrap_historical_sleeper.py``.
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Dict, Iterable, Optional

from .sleeper_scoring import regular_season_weeks

HttpGetJson = Callable[[str], Any]
OwnershipBlob = Dict[str, Dict[str, Dict[str, Any]]]


def ownership_url(year: int, week: int) -> str:
    return f"https://api.sleeper.com/players/nfl/research/regular/{year}/{week}"


def build_ownership_history(
    year: int,
    *,
    http_get_json: HttpGetJson,
    weeks: Optional[Iterable[int]] = None,
    max_workers: int = 8,
) -> OwnershipBlob:
    """Fetch ownership for every regular-season week and return the
    ``{pid: {week_str: {owned, started, ...}}}`` blob.

    Silently skips weeks where the endpoint returns a non-dict (early
    pre-season weeks of a future season, etc.) so a single bad week
    doesn't tank the whole backfill.
    """
    week_iter = list(weeks) if weeks is not None else list(regular_season_weeks(year))

    def _one(week: int):
        try:
            payload = http_get_json(ownership_url(year, week))
        except Exception:
            logging.exception("Ownership fetch failed for %s/%s", year, week)
            return week, None
        if not isinstance(payload, dict):
            logging.info("Ownership payload not a dict for %s/%s", year, week)
            return week, None
        return week, payload

    out: OwnershipBlob = {}
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        for week, payload in pool.map(_one, week_iter):
            if not payload:
                continue
            week_str = str(week)
            for pid, owned_info in payload.items():
                out.setdefault(str(pid), {})[week_str] = owned_info
    return out


__all__ = [
    "ownership_url",
    "build_ownership_history",
]
