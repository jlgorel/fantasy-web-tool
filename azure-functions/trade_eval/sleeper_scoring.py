"""Sleeper historical & in-season scoring scraper.

Pulls per-week raw stats from ``https://api.sleeper.com/stats/nfl/{season}/{week}``,
preserves the full raw stats object per player (so any league's custom
scoring can be re-derived later), and computes a per-season summary blob
keyed by player_id with weekly + season-aggregate PPG in std / half-PPR /
PPR.

The pure aggregation logic in :func:`aggregate_week`,
:func:`merge_week_into_summary`, and :func:`finalize_summary` is
self-contained and IO-free for easy unit testing. The
``backfill_*`` / ``update_current_week`` driver functions take HTTP and
blob IO as injected callables to keep them testable as well.
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from . import blob_layout

# Type aliases for clarity.
SleeperRow = Dict[str, Any]
RawWeekBlob = Dict[str, SleeperRow]   # {player_id: full row}
SeasonSummary = Dict[str, Dict[str, Any]]  # {player_id: per-player summary}

# Scoring keys we materialize alongside the raw stats. These all come from
# the row's ``stats`` sub-dict.
PTS_KEYS: Tuple[str, ...] = ("pts_std", "pts_half_ppr", "pts_ppr")
RANK_KEY_HALF_PPR: str = "pos_rank_half_ppr"
GAMES_PLAYED_KEY: str = "gp"

# Default canonical baseline used by the index blob & calibration.
CANONICAL_SCORING_FORMAT: str = "half_ppr"


# ---------------------------------------------------------------------------
# Season / week helpers
# ---------------------------------------------------------------------------
def regular_season_weeks(season: int) -> range:
    """Return the iterable of regular-season weeks for a given season.

    NFL expanded the regular season to 18 games starting in 2021. Earlier
    seasons capped at 17.
    """
    return range(1, 19) if season >= 2021 else range(1, 18)


def stats_url(season: int, week: int) -> str:
    return (
        f"https://api.sleeper.com/stats/nfl/{season}/{week}"
        f"?season_type=regular"
    )


# ---------------------------------------------------------------------------
# Pure aggregation: takes already-fetched rows, returns blobs
# ---------------------------------------------------------------------------
def aggregate_week(rows: Iterable[SleeperRow]) -> RawWeekBlob:
    """Index a week's worth of Sleeper stats rows by ``player_id``.

    Skips rows missing a ``player_id``. The full row (including ``stats``,
    ``player`` meta, ``team``, ``opponent``, ``game_id``) is preserved so
    custom scoring can be re-derived. If duplicate ids appear within a
    single payload (defensive — shouldn't happen) the last one wins.
    """
    out: RawWeekBlob = {}
    for row in rows:
        pid = row.get("player_id")
        if not pid:
            continue
        out[str(pid)] = row
    return out


def _empty_summary_bucket() -> Dict[str, Any]:
    return {
        "position": None,
        "team": None,
        "weekly_pts": {},          # week_str -> {std, half_ppr, ppr}
        "weekly_rank_half_ppr": {},
        "games_played": 0,
        "total_pts": {"std": 0.0, "half_ppr": 0.0, "ppr": 0.0},
        "ppg": {"std": 0.0, "half_ppr": 0.0, "ppr": 0.0},
    }


def _safe_float(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def merge_week_into_summary(
    summary: SeasonSummary,
    week: int,
    raw_week: RawWeekBlob,
) -> SeasonSummary:
    """Merge one week's raw rows into a season summary in place.

    Idempotent on (season, week): re-merging the same week overwrites the
    week's slot rather than double-counting.
    """
    week_str = str(week)
    for pid, row in raw_week.items():
        bucket = summary.setdefault(pid, _empty_summary_bucket())
        stats = row.get("stats") or {}
        player_meta = row.get("player") or {}

        # If we previously merged this week, subtract its contribution from
        # the running totals before re-adding the latest values, so re-runs
        # are idempotent.
        prev = bucket["weekly_pts"].get(week_str)
        if prev is not None:
            for k in ("std", "half_ppr", "ppr"):
                bucket["total_pts"][k] -= prev.get(k, 0.0)
            if prev.get("_played"):
                bucket["games_played"] = max(0, bucket["games_played"] - 1)

        weekly = {
            "std": _safe_float(stats.get("pts_std")),
            "half_ppr": _safe_float(stats.get("pts_half_ppr")),
            "ppr": _safe_float(stats.get("pts_ppr")),
        }
        played = _safe_float(stats.get(GAMES_PLAYED_KEY)) >= 1.0
        # Stash the played flag inside the weekly slot so future re-merges
        # can reverse it correctly. Stripped before persistence.
        weekly["_played"] = played
        bucket["weekly_pts"][week_str] = weekly
        for k in ("std", "half_ppr", "ppr"):
            bucket["total_pts"][k] += weekly[k]
        if played:
            bucket["games_played"] += 1

        rank = stats.get(RANK_KEY_HALF_PPR)
        if rank is not None:
            try:
                bucket["weekly_rank_half_ppr"][week_str] = int(rank)
            except (TypeError, ValueError):
                pass

        if not bucket["position"]:
            bucket["position"] = player_meta.get("position")
        if row.get("team"):
            bucket["team"] = row["team"]
    return summary


def finalize_summary(summary: SeasonSummary) -> SeasonSummary:
    """Compute PPG and strip internal scratch keys.

    Should be called once after all weeks have been merged in. Returns the
    same dict for convenience.
    """
    for bucket in summary.values():
        gp = bucket["games_played"] or 0
        for k in ("std", "half_ppr", "ppr"):
            total = bucket["total_pts"][k]
            bucket["ppg"][k] = (total / gp) if gp else 0.0
        # Drop the played scratch flag we stashed during merge.
        for week_slot in bucket["weekly_pts"].values():
            week_slot.pop("_played", None)
    return summary


# ---------------------------------------------------------------------------
# Drivers (HTTP + blob IO injected for testability)
# ---------------------------------------------------------------------------
HttpGetJson = Callable[[str], Any]                   # url -> parsed JSON
BlobUpload = Callable[[Dict[str, Any], str], None]    # (data, blob_name)
BlobLoad = Callable[[str], Optional[Dict[str, Any]]]  # blob_name -> data | None


def fetch_week(http_get_json: HttpGetJson, season: int, week: int) -> List[SleeperRow]:
    """Fetch a single (season, week). Returns ``[]`` on a non-list payload
    (occasional 404 / empty response) so callers can keep going."""
    payload = http_get_json(stats_url(season, week))
    if isinstance(payload, list):
        return payload
    logging.info("Sleeper week stats returned non-list for %s/%s: %r", season, week, type(payload))
    return []


def build_season(
    season: int,
    *,
    http_get_json: HttpGetJson,
    weeks: Optional[Iterable[int]] = None,
    max_workers: int = 8,
) -> Tuple[SeasonSummary, Dict[int, RawWeekBlob]]:
    """Fetch every regular-season week for ``season`` and return both the
    per-week raw blobs and the finalized season summary.

    No blob IO -- the caller decides what to persist. ``weeks`` lets
    in-season callers fetch a single week.
    """
    week_iter = list(weeks) if weeks is not None else list(regular_season_weeks(season))
    raw_by_week: Dict[int, RawWeekBlob] = {}

    def _one(week: int) -> Tuple[int, RawWeekBlob]:
        rows = fetch_week(http_get_json, season, week)
        return week, aggregate_week(rows)

    # Sleeper tolerates parallel calls fine; the bottleneck is the JSON
    # decode of the ~1-2 MB payloads.
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        for week, raw in pool.map(_one, week_iter):
            raw_by_week[week] = raw

    summary: SeasonSummary = {}
    for week in sorted(raw_by_week):
        merge_week_into_summary(summary, week, raw_by_week[week])
    finalize_summary(summary)
    return summary, raw_by_week


def update_current_week(
    season: int,
    week: int,
    *,
    http_get_json: HttpGetJson,
    blob_load: BlobLoad,
    blob_upload: BlobUpload,
) -> bool:
    """Fetch one week and merge it into the existing season summary blob.

    Idempotent: re-running the same (season, week) overwrites that week's
    contribution rather than double-counting. Used by the in-season weekly
    timer; safe to call repeatedly.

    Returns True if anything was actually updated.
    """
    raw_rows = fetch_week(http_get_json, season, week)
    raw_week = aggregate_week(raw_rows)
    if not raw_week:
        logging.info("No rows returned for %s/%s -- skipping update.", season, week)
        return False

    blob_upload(raw_week, blob_layout.scoring_raw_blob(season, week))

    summary = blob_load(blob_layout.scoring_summary_blob(season)) or {}
    merge_week_into_summary(summary, week, raw_week)
    finalize_summary(summary)
    blob_upload(summary, blob_layout.scoring_summary_blob(season))

    _bump_index(blob_load, blob_upload, season=season, week=week)
    return True


def bootstrap_history(
    seasons: Iterable[int],
    *,
    http_get_json: HttpGetJson,
    blob_upload: BlobUpload,
    blob_load: BlobLoad,
    max_workers: int = 8,
) -> Dict[int, int]:
    """One-shot historical seeding for past seasons.

    Writes:
      * ``trade_eval/scoring/raw/{season}/{week}.json`` for each (season, week)
      * ``trade_eval/scoring/{season}.json`` per-player season summary
      * ``trade_eval/scoring/_index.json`` describing what's present

    Returns a ``{season: weeks_loaded}`` map. Continues past a single
    season's failure so a flaky week doesn't sink the whole bootstrap.
    """
    out: Dict[int, int] = {}
    for season in seasons:
        try:
            summary, raw_by_week = build_season(
                season, http_get_json=http_get_json, max_workers=max_workers
            )
            for week, raw in raw_by_week.items():
                blob_upload(raw, blob_layout.scoring_raw_blob(season, week))
            blob_upload(summary, blob_layout.scoring_summary_blob(season))
            out[season] = len(raw_by_week)
            for week in sorted(raw_by_week):
                _bump_index(blob_load, blob_upload, season=season, week=week)
            logging.info("Bootstrapped Sleeper scoring for %s (%d weeks).", season, len(raw_by_week))
        except Exception:
            logging.exception("Failed to bootstrap season %s", season)
    return out


def _bump_index(
    blob_load: BlobLoad,
    blob_upload: BlobUpload,
    *,
    season: int,
    week: int,
) -> None:
    """Update the scoring index blob with the latest (season, week)."""
    index = blob_load(blob_layout.scoring_index_blob()) or {
        "seasons": {},
        "scoring_format": CANONICAL_SCORING_FORMAT,
    }
    seasons = index.setdefault("seasons", {})
    season_entry = seasons.setdefault(str(season), {"weeks_present": []})
    weeks = set(season_entry.get("weeks_present") or [])
    weeks.add(int(week))
    season_entry["weeks_present"] = sorted(weeks)
    season_entry["last_updated_utc"] = datetime.now(timezone.utc).isoformat()
    index["last_updated_utc"] = season_entry["last_updated_utc"]
    blob_upload(index, blob_layout.scoring_index_blob())


__all__ = [
    "regular_season_weeks",
    "stats_url",
    "aggregate_week",
    "merge_week_into_summary",
    "finalize_summary",
    "fetch_week",
    "build_season",
    "update_current_week",
    "bootstrap_history",
    "CANONICAL_SCORING_FORMAT",
]
