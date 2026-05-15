"""Blob path conventions for the trade-evaluator pipeline.

Centralizing the names here means the scrapers, the calibration job, and the
backend reader all agree without each holding its own copy of the string.

Layout (all within the existing ``fantasyjsons`` container)::

    trade_eval/
      scoring/
        {season}.json                       # per-player season summary
        raw/{season}/{week}.json            # full raw stats per (season, week)
        _index.json                         # which seasons/weeks are present
      values/
        fantasycalc/{format}/{date}.json    # daily snapshot per format
        fantasycalc/_index.json
        ktc/{format}/{date}.json            # weekly snapshot per format
        ktc/_index.json

Dates are ISO ``YYYY-MM-DD``. ``format`` is one of the constants in
:data:`FANTASYCALC_FORMATS` / :data:`KTC_FORMATS`.
"""
from __future__ import annotations

from typing import Final, Tuple

# ---------------------------------------------------------------------------
# Top-level prefix
# ---------------------------------------------------------------------------
TRADE_EVAL_PREFIX: Final[str] = "trade_eval"


# ---------------------------------------------------------------------------
# Sleeper historical scoring
# ---------------------------------------------------------------------------
def scoring_summary_blob(season: int) -> str:
    """Per-season per-player scoring summary blob.

    Shape::

        {
          "<player_id>": {
            "position": "WR",
            "team": "GB",
            "weekly_pts": {                # half-PPR / PPR / standard
              "1": {"half_ppr": 12.4, "ppr": 14.1, "std": 10.2},
              ...
            },
            "weekly_rank_half_ppr": {"1": 8, ...},
            "games_played": 14,
            "total_pts": {"half_ppr": ..., "ppr": ..., "std": ...},
            "ppg": {"half_ppr": ..., "ppr": ..., "std": ...}
          }
        }
    """
    return f"{TRADE_EVAL_PREFIX}/scoring/{season}.json"


def scoring_raw_blob(season: int, week: int) -> str:
    """Per-(season, week) full raw-stats blob, keyed by player_id.

    Shape: ``{ "<player_id>": <full sleeper stats dict>, ... }``.

    We keep this verbatim so any league's custom scoring (TE premium,
    6pt pass TDs, IDP, etc.) can be re-derived later without re-scraping.
    """
    return f"{TRADE_EVAL_PREFIX}/scoring/raw/{season}/{week}.json"


def scoring_index_blob() -> str:
    """Index blob describing what scoring data is currently stored.

    Shape::

        {
          "seasons": {
            "2020": {"weeks_present": [1, 2, ...], "last_updated_utc": "..."},
            ...
          },
          "scoring_format": "half_ppr",  # canonical baseline
          "last_updated_utc": "..."
        }
    """
    return f"{TRADE_EVAL_PREFIX}/scoring/_index.json"


# ---------------------------------------------------------------------------
# FantasyCalc value snapshots
# ---------------------------------------------------------------------------
# Format identifiers we'll snapshot. Tuple of (key, num_qbs) so the URL is
# easy to derive. ``key`` is what we use in blob paths.
FANTASYCALC_FORMATS: Final[Tuple[Tuple[str, int], ...]] = (
    ("1qb", 1),
    ("superflex", 2),
)


def fantasycalc_snapshot_blob(format_key: str, date_iso: str) -> str:
    return f"{TRADE_EVAL_PREFIX}/values/fantasycalc/{format_key}/{date_iso}.json"


def fantasycalc_index_blob() -> str:
    return f"{TRADE_EVAL_PREFIX}/values/fantasycalc/_index.json"


# ---------------------------------------------------------------------------
# KTC value snapshots
# ---------------------------------------------------------------------------
# KTC dynasty rankings page query param ``format`` value:
#   1 = 1QB, 2 = Superflex.
KTC_FORMATS: Final[Tuple[Tuple[str, int], ...]] = (
    ("1qb", 1),
    ("superflex", 2),
)


def ktc_snapshot_blob(format_key: str, date_iso: str) -> str:
    return f"{TRADE_EVAL_PREFIX}/values/ktc/{format_key}/{date_iso}.json"


def ktc_index_blob() -> str:
    return f"{TRADE_EVAL_PREFIX}/values/ktc/_index.json"


def ktc_historical_blob() -> str:
    """Rolling per-player KTC value history (1QB + Superflex, daily).

    Single canonical blob, built one-shot from CSV + per-slug scrape (see
    ``tools/build_historical_ktc_json.py``), then appended to each day by
    :mod:`trade_eval.ktc_top500_daily`.

    Shape::

        {
          "n_records": ..., "last_updated_utc": "...",
          "records": {
            "<sleeper_id | pick:YYYY_tier_round | ktc:N>": {
              "name" | "label": "...",
              "position": "WR", "team": "MIN", "age": 26,
              "fantasy_positions": ["WR"],
              "ktc_player_id": 547, "ktc_slug": "justin-jefferson-547",
              "sleeper_id": "6794", "is_pick": false,
              "1QB_Historical": {"YYYY-MM-DD": value, ...},
              "SF_Historical":  {"YYYY-MM-DD": value, ...}
            }, ...
          }
        }
    """
    return f"{TRADE_EVAL_PREFIX}/values/ktc/historical_KTC_rankings.json"


__all__ = [
    "TRADE_EVAL_PREFIX",
    "scoring_summary_blob",
    "scoring_raw_blob",
    "scoring_index_blob",
    "FANTASYCALC_FORMATS",
    "fantasycalc_snapshot_blob",
    "fantasycalc_index_blob",
    "KTC_FORMATS",
    "ktc_snapshot_blob",
    "ktc_index_blob",
    "ktc_historical_blob",
]
