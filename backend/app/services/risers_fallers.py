"""Top risers and fallers: diff current vs previous standard rankings.

Compares ``standard_player_rankings.json`` against the previous-run snapshot
``standard_player_rankings_prev.json`` (written by the scraper before
overwriting the canonical blob — see ``form_standard_player_rankings`` in
the Azure function).

Returns the top N positive and negative VEGAS-point deltas per scoring
variant, with enough context (POS, current/prev points, abs delta) for the
frontend to render a leaderboard.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

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
_DEFAULT_TOP_N = 10
# Players need at least this many projected points (in the *higher* of the
# two snapshots) to make the leaderboard. Filters out scrub-level noise where
# a 0.3 -> 0.6 jump shows as "+100%".
_MIN_VEGAS_FLOOR = 5.0


def _index_by_pid(rows: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        pid = r.get("PID")
        if pid:
            out[pid] = r
    return out


def get_risers_fallers(
    variant: str = _DEFAULT_VARIANT,
    top_n: int = _DEFAULT_TOP_N,
) -> Dict[str, Any]:
    if variant not in _VALID_VARIANTS:
        logger.info("Unknown risers variant %r; falling back to %s", variant, _DEFAULT_VARIANT)
        variant = _DEFAULT_VARIANT

    current = load_blob("standard_player_rankings.json") or {}
    try:
        prev = load_blob("standard_player_rankings_prev.json") or {}
    except FileNotFoundError:
        prev = {}
    except Exception as e:
        # Azure path will raise on missing blob; treat as no-history.
        logger.info("No prev rankings snapshot available: %s", e)
        prev = {}

    cur_rows: List[Dict[str, Any]] = current.get(variant) or []
    prev_rows: List[Dict[str, Any]] = prev.get(variant) or []

    if not prev_rows:
        return {
            "variant": variant,
            "available": False,
            "message": (
                "No previous-run snapshot exists yet. The scraper writes one before "
                "each overwrite, so this will populate after the next refresh."
            ),
            "risers": [],
            "fallers": [],
        }

    prev_index = _index_by_pid(prev_rows)
    deltas: List[Dict[str, Any]] = []

    for row in cur_rows:
        pid = row.get("PID")
        if not pid:
            continue
        prev_row = prev_index.get(pid)
        if prev_row is None:
            # Brand new player — skip rather than treat as huge riser.
            continue
        cur_v = row.get("VEGAS")
        prev_v = prev_row.get("VEGAS")
        if not isinstance(cur_v, (int, float)) or not isinstance(prev_v, (int, float)):
            continue
        if max(cur_v, prev_v) < _MIN_VEGAS_FLOOR:
            continue
        delta = round(float(cur_v) - float(prev_v), 2)
        if delta == 0:
            continue
        pct = None
        if prev_v != 0:
            pct = round(((cur_v - prev_v) / abs(prev_v)) * 100.0, 1)
        deltas.append({
            "PID": pid,
            "NAME": row.get("NAME"),
            "POS": row.get("POS"),
            "VEGAS": round(float(cur_v), 2),
            "PREV_VEGAS": round(float(prev_v), 2),
            "DELTA": delta,
            "DELTA_PCT": pct,
            "P10": row.get("P10"),
            "P90": row.get("P90"),
            "BOOM": row.get("BOOM"),
            "BUST": row.get("BUST"),
        })

    risers = sorted(deltas, key=lambda d: d["DELTA"], reverse=True)[:top_n]
    fallers = sorted(deltas, key=lambda d: d["DELTA"])[:top_n]

    return {
        "variant": variant,
        "available": True,
        "risers": risers,
        "fallers": fallers,
    }
