"""Player Detail Page backend.

Aggregates everything we know about a single Sleeper player ID into one
JSON payload for the frontend's ``/player/<pid>`` page:

* **meta** — name + fantasy positions (from ``players.json``).
* **scoring** — per-year season totals + weekly breakdowns from
  ``player_season_scoring_{year}.json``. Years without data are omitted.
* **ownership** — per-year per-week ``{owned, started}`` percentages from
  ``owned_history_{year}.json``. Years without data are omitted.
* **available_years** — sorted list of years that contributed data, so the
  frontend can render a year-tab control without a second probe.

Year discovery is bounded — we try the current fantasy year and a few years
back rather than listing the blob container, so this works identically in
production (Azure) and fixture mode.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from app.services.blob_store import load_blob, try_load_blob
from app.services.season import get_current_fantasy_year

logger = logging.getLogger(__name__)


# How many seasons back from "current" to probe. 4 covers the typical
# dynasty / keeper history horizon without spamming blob fetches.
_HISTORY_DEPTH = 4


def _candidate_years() -> List[str]:
    """Years to probe, newest first."""
    try:
        current = int(get_current_fantasy_year())
    except Exception:
        # Off-season / mis-configured clock — fall back to a sensible window.
        current = 2024
    return [str(y) for y in range(current, current - _HISTORY_DEPTH, -1)]


def _extract_player_meta(
    pid: str,
    players_blob: Dict[str, Any],
    scoring_by_year: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    """Pick the best metadata we have for this player.

    ``players.json`` is the source of truth, but if the catalog snapshot is
    behind (e.g. a player just signed mid-season) we fall back to whatever
    name/position the most recent scoring blob carries.
    """
    base = players_blob.get(pid) or {}
    full_name = base.get("full_name")
    fantasy_positions = list(base.get("fantasy_positions") or [])

    if not full_name or not fantasy_positions:
        # Newest year first — _candidate_years() is already sorted desc, and
        # scoring_by_year preserves insertion order from that probe.
        for year_blob in scoring_by_year.values():
            entry = year_blob.get(pid) or {}
            if not full_name and entry.get("full_name"):
                full_name = entry["full_name"]
            if not fantasy_positions and entry.get("fantasy_positions"):
                fantasy_positions = list(entry["fantasy_positions"])
            if full_name and fantasy_positions:
                break

    return {
        "player_id": pid,
        "full_name": full_name,
        "fantasy_positions": fantasy_positions,
    }


def _build_scoring_section(
    pid: str, scoring_by_year: Dict[str, Dict[str, Any]]
) -> Dict[str, Dict[str, Any]]:
    """Project just this player's slice out of each year's scoring blob."""
    out: Dict[str, Dict[str, Any]] = {}
    for year, blob in scoring_by_year.items():
        entry = blob.get(pid)
        if not entry:
            continue
        weekly = entry.get("scoring_data_weekly") or {}
        season = entry.get("scoring_data_season") or {}
        # Skip years where the player exists in the catalog but has no
        # actual production — keeps the response lean and the frontend's
        # year-tab from showing empty seasons.
        if not weekly and not season:
            continue
        out[year] = {
            "weekly": weekly,
            "season": season,
        }
    return out


def _build_ownership_section(
    pid: str, ownership_by_year: Dict[str, Dict[str, Any]]
) -> Dict[str, Dict[str, Dict[str, float]]]:
    """Project just this player's slice out of each year's ownership blob."""
    out: Dict[str, Dict[str, Dict[str, float]]] = {}
    for year, blob in ownership_by_year.items():
        entry = blob.get(pid)
        if not entry:
            continue
        # Stored as { week_str: {"owned": float, "started": float} }.
        # Sort weeks numerically so the frontend can render a clean
        # left-to-right line chart without re-sorting.
        try:
            sorted_weeks = sorted(entry.items(), key=lambda kv: int(kv[0]))
        except (TypeError, ValueError):
            sorted_weeks = list(entry.items())
        out[year] = {wk: dict(info) for wk, info in sorted_weeks}
    return out


def get_player_detail(player_id: str) -> Optional[Dict[str, Any]]:
    """Build the Player Detail Page payload for one Sleeper PID.

    Returns ``None`` if the player isn't in the live ``players.json`` catalog
    AND has no presence in any scoring/ownership history blob — i.e. we have
    nothing useful to render.
    """
    pid = str(player_id)

    players_blob = load_blob("players.json") or {}

    scoring_by_year: Dict[str, Dict[str, Any]] = {}
    ownership_by_year: Dict[str, Dict[str, Any]] = {}
    for year in _candidate_years():
        scoring = try_load_blob(f"player_season_scoring_{year}.json")
        if scoring is not None:
            scoring_by_year[year] = scoring
        ownership = try_load_blob(f"owned_history_{year}.json")
        if ownership is not None:
            ownership_by_year[year] = ownership

    in_catalog = pid in players_blob
    in_scoring = any(pid in b for b in scoring_by_year.values())
    in_ownership = any(pid in b for b in ownership_by_year.values())

    if not (in_catalog or in_scoring or in_ownership):
        return None

    meta = _extract_player_meta(pid, players_blob, scoring_by_year)
    scoring = _build_scoring_section(pid, scoring_by_year)
    ownership = _build_ownership_section(pid, ownership_by_year)

    available_years = sorted(set(scoring.keys()) | set(ownership.keys()))

    return {
        "meta": meta,
        "scoring": scoring,
        "ownership": ownership,
        "available_years": available_years,
    }
