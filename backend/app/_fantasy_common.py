"""Canonical source for values shared between the Flask backend and the
Azure-functions scraper.

This module is the single source of truth. It gets copied into each project
as ``_fantasy_common.py`` by ``tools/sync_shared.py``. Edit *this* file, then
run that script. A test asserts the three copies stay byte-identical.

Keep this module dependency-free (stdlib only) so it imports the same way
in either runtime.
"""
from __future__ import annotations

from datetime import datetime
from typing import Dict, Optional


# ---------------------------------------------------------------------------
# NFL team name lookups (used to map Sleeper team-pid <-> Fleaflicker DST name)
# ---------------------------------------------------------------------------
nfl_teams: Dict[str, str] = {
    "NE": "New England Patriots",
    "NYG": "New York Giants",
    "NYJ": "New York Jets",
    "PHI": "Philadelphia Eagles",
    "WAS": "Washington Commanders",
    "DAL": "Dallas Cowboys",
    "BUF": "Buffalo Bills",
    "MIA": "Miami Dolphins",
    "PIT": "Pittsburgh Steelers",
    "CIN": "Cincinnati Bengals",
    "CLE": "Cleveland Browns",
    "BAL": "Baltimore Ravens",
    "TEN": "Tennessee Titans",
    "JAX": "Jacksonville Jaguars",
    "IND": "Indianapolis Colts",
    "HOU": "Houston Texans",
    "KC": "Kansas City Chiefs",
    "LAC": "Los Angeles Chargers",
    "LV": "Las Vegas Raiders",
    "SEA": "Seattle Seahawks",
    "SF": "San Francisco 49ers",
    "LA": "Los Angeles Rams",
    "ARI": "Arizona Cardinals",
    "CHI": "Chicago Bears",
    "DET": "Detroit Lions",
    "GB": "Green Bay Packers",
    "MIN": "Minnesota Vikings",
    "NO": "New Orleans Saints",
    "ATL": "Atlanta Falcons",
    "CAR": "Carolina Panthers",
    "TB": "Tampa Bay Buccaneers",
    "DEN": "Denver Broncos",
    # Historical alias - Chargers were SD before relocating to LA. Kept for
    # back-compat with older projection blobs.
    "SD": "San Diego Chargers",
}

nfl_teams_reverse_lookup: Dict[str, str] = {v: k for k, v in nfl_teams.items()}


# ---------------------------------------------------------------------------
# League-scoring -> stat multiplier map
# ---------------------------------------------------------------------------
def get_stat_point_multipliers(settings: Dict[str, float]) -> Dict[str, float]:
    """Return a stat-key -> point-multiplier dict derived from a Sleeper-style
    league scoring settings object.

    The keys here must match the stat-name keys used in the projection blobs
    (see ``Config.prop_name_to_stat_name_map`` in the scraper).

    Notes:
    - ``Anytime Touchdown`` uses ``rush_td``. Sleeper rec_td and rush_td are
      almost always equal in practice; if they differ we'd need anytime-TDs
      split by source, which the projection blobs don't currently provide.
    - ``Receiving Touchdown`` / ``Rushing Touchdown`` are exposed for any
      future code that wants to score receiving vs rushing TDs separately.
      No current projection blob emits these keys, so they're harmless today
      but available going forward.
    """
    return {
        "Interceptions": settings["pass_int"],
        "Receiving Touchdown": settings["rec_td"],
        "Rushing Touchdown": settings["rush_td"],
        "Anytime Touchdown": settings["rush_td"],
        "Passing Yards": settings["pass_yd"],
        "Passing TDs": settings["pass_td"],
        "Passing Touchdowns": settings["pass_td"],
        "Rushing Yards": settings["rush_yd"],
        "Receiving Yards": settings["rec_yd"],
        "Receptions": settings["rec"],
        "TE Receptions": (
            settings["rec"] + settings["bonus_rec_te"]
            if "bonus_rec_te" in settings
            else settings["rec"]
        ),
    }


__all__ = [
    "nfl_teams",
    "nfl_teams_reverse_lookup",
    "get_stat_point_multipliers",
    "get_current_fantasy_year",
    "is_in_fantasy_season",
    "get_current_nfl_week",
    "NFL_SEASON_START_MONTH",
    "NFL_SEASON_START_DAY",
]


# ---------------------------------------------------------------------------
# Season / week helpers
# ---------------------------------------------------------------------------
# Approximate NFL Week 1 kickoff (first Thursday of September). Used for
# week-of-season math; off by a few days at most, capped 1-18.
NFL_SEASON_START_MONTH = 9
NFL_SEASON_START_DAY = 4
# Months that count as "in fantasy season" for scrape gating: regular season
# Sep-Dec, fantasy + NFL playoffs in Jan, Super Bowl in early Feb. Aug is
# preseason and is intentionally skipped.
_IN_SEASON_MONTHS = {1, 2, 9, 10, 11, 12}


def get_current_fantasy_year(now: Optional[datetime] = None) -> int:
    """Return the fantasy season year as an int.

    Anything Jan-Jul still belongs to the previous season (post-season /
    offseason for the year that already kicked off).
    """
    now = now or datetime.now()
    return 2026
    #return now.year - 1 if now.month <= 7 else now.year


def is_in_fantasy_season(now: Optional[datetime] = None) -> bool:
    """True when scrapers should be running. Aug returns False (preseason)."""
    now = now or datetime.now()
    return now.month in _IN_SEASON_MONTHS


def get_current_nfl_week(now: Optional[datetime] = None) -> int:
    """Return the current NFL week (1-18, capped)."""
    now = now or datetime.now()
    season_year = get_current_fantasy_year(now)
    season_start = datetime(season_year, NFL_SEASON_START_MONTH, NFL_SEASON_START_DAY)
    week = ((now - season_start).days // 7) + 1
    return max(1, min(week, 18))
