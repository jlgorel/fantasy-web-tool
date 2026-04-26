"""Helpers for resolving the current fantasy season."""
from __future__ import annotations

from datetime import datetime


def get_current_fantasy_year() -> str:
    """Return the fantasy season year as a string.

    Anything Jan-Jul still belongs to the previous season (post-season /
    offseason for the year that already kicked off).
    """
    now = datetime.now()
    year = int(now.strftime("%Y"))
    if now.month <= 7:
        year -= 1
    return str(year)
