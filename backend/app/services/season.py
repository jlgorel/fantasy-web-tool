"""Helpers for resolving the current fantasy season.

Delegates to the canonical ``shared/fantasy_common.py`` (synced to
``app/_fantasy_common.py``) so the backend and the azure-functions scraper
agree on how the calendar maps to season year / week.
"""
from __future__ import annotations

from app._fantasy_common import (
    get_current_fantasy_year as _shared_year,
    get_current_nfl_week as _shared_week,
    is_in_fantasy_season as _shared_in_season,
)


def get_current_fantasy_year() -> str:
    """Return the fantasy season year as a string (legacy backend signature)."""
    return str(_shared_year())


def get_current_nfl_week() -> int:
    """Return the current NFL week (1-18, capped)."""
    return _shared_week()


def is_in_fantasy_season() -> bool:
    """True when the season window covers the current calendar date."""
    return _shared_in_season()
