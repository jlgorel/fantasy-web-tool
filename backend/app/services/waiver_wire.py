"""League-agnostic waiver-wire cheat sheet.

Returns the top low-owned players per position ranked by Vegas projected
points for a given scoring variant. Unlike ``form_top_free_agents_parallel``
this requires no roster — it's meant for the rankings page where the user
hasn't necessarily loaded a Sleeper username.

Data sources (all already produced by the scraper):
- ``standard_player_rankings.json`` — has VEGAS / BOOM / BUST / P10 / P90
  precomputed for every supported scoring variant.
- ``owned.json`` — Sleeper league-wide ownership percentages
  (``{pid: {"owned": float, "started": float}}``).
"""
from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any, Dict, List

from app.services.blob_store import load_blob

logger = logging.getLogger(__name__)

_DEFAULT_VARIANT = "halfppr_4ptpass"
_VALID_VARIANTS = {
    "std_4ptpass",
    "halfppr_4ptpass",
    "fullppr_4ptpass",
    "std_6ptpass",
    "halfppr_6ptpass",
    "fullppr_6ptpass",
}
_FA_POSITIONS = ("QB", "RB", "WR", "TE")
_DEFAULT_MAX_OWNED_PCT = 50.0
_DEFAULT_TOP_N = 15


def get_waiver_wire(
    variant: str = _DEFAULT_VARIANT,
    max_owned_pct: float = _DEFAULT_MAX_OWNED_PCT,
    top_n: int = _DEFAULT_TOP_N,
) -> Dict[str, Any]:
    """Build the waiver-wire payload.

    Returns a dict shaped as::

        {
          "variant": "halfppr_4ptpass",
          "max_owned_pct": 50.0,
          "by_position": {
            "QB": [ {ranking_row + OWNED_PCT}, ... ],
            "RB": [...], "WR": [...], "TE": [...]
          }
        }

    ``standard_player_rankings.json`` is already sorted by VEGAS descending so
    we can stream the first ``top_n`` low-owned rows per position without an
    explicit re-sort.
    """
    if variant not in _VALID_VARIANTS:
        logger.info("Unknown waiver variant %r; falling back to %s", variant, _DEFAULT_VARIANT)
        variant = _DEFAULT_VARIANT

    rankings = load_blob("standard_player_rankings.json") or {}
    owned = load_blob("owned.json") or {}

    rows: List[Dict[str, Any]] = rankings.get(variant) or []
    by_pos: Dict[str, List[Dict[str, Any]]] = defaultdict(list)

    for row in rows:
        pos = row.get("POS")
        if pos not in _FA_POSITIONS:
            continue
        if len(by_pos[pos]) >= top_n:
            # Already filled this position bucket. Skip without short-
            # circuiting the loop because other positions may still need fills.
            if all(len(by_pos[p]) >= top_n for p in _FA_POSITIONS):
                break
            continue

        pid = row.get("PID")
        if not pid:
            continue
        owned_entry = owned.get(pid) or {}
        owned_pct = owned_entry.get("owned")
        # If we have no ownership signal at all, treat as ~0% — better to
        # surface a possibly-relevant deep cut than silently drop them.
        if owned_pct is None:
            owned_pct = 0.0
        if owned_pct >= max_owned_pct:
            continue

        # Drop rows that have no Vegas projection at all — they'd just be
        # noise and are usually injured/inactive players.
        vegas = row.get("VEGAS")
        if not isinstance(vegas, (int, float)) or vegas <= 0:
            continue

        enriched = dict(row)
        enriched["OWNED_PCT"] = round(float(owned_pct), 1)
        started_pct = owned_entry.get("started")
        if started_pct is not None:
            enriched["STARTED_PCT"] = round(float(started_pct), 1)
        by_pos[pos].append(enriched)

    return {
        "variant": variant,
        "max_owned_pct": max_owned_pct,
        "top_n": top_n,
        "by_position": {pos: by_pos.get(pos, []) for pos in _FA_POSITIONS},
    }
