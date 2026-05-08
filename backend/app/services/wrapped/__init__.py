"""League Wrapped (a.k.a. Fun Facts) — season recap pipeline.

Public surface:
    compute_wrapped(league_id, year) -> WrappedPayload

The payload is shaped as a flat dict of accolade categories. Each category
contains pre-computed, JSON-serializable values ready for the frontend to
render. See ``schedule_stats.py`` for the meaning of each schedule accolade.

Phasing:
* Phase 1 (current): schedule-only accolades. Uses the existing Sleeper
  matchups endpoint, no new scraper data needed.
* Phase 2 (next): roster / transaction / troll accolades. Needs
  ``owned_history_{year}.json``.
* Phase 3: draft + trade accolades. Needs ``player_season_scoring_{year}.json``
  + FantasyCalc values.
"""
from app.services.wrapped.pipeline import compute_wrapped, ALL_SUPPORTED_YEAR_HINT

__all__ = ["compute_wrapped", "ALL_SUPPORTED_YEAR_HINT"]
