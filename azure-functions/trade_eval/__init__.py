"""Trade-evaluator data pipeline.

This package holds the *separate* scraping / data-prep pipeline that powers
the dynasty trade lookback evaluator. It is intentionally isolated from the
in-season DraftKings / Vegas / FantasyPros pipeline in ``function_app.py`` so
that:

  * It can run year-round (dynasty values move all summer; scoring updates
    only during the season).
  * It does not get gated by ``is_in_fantasy_season`` for the value /
    snapshot legs.
  * Its blobs all live under a ``trade_eval/`` prefix (see
    :mod:`trade_eval.blob_layout`) and are easy to identify, regenerate, or
    archive without touching the rest of the app.

Modules:

  * :mod:`trade_eval.blob_layout` -- single source of truth for blob paths
    and partition conventions.
  * :mod:`trade_eval.sleeper_scoring` -- historical + in-season Sleeper
    weekly stats / PPG. League-agnostic (half-PPR canonical, raw stats
    preserved for custom-scoring derivations).
  * :mod:`trade_eval.fantasycalc_values` -- daily FantasyCalc dynasty value
    snapshots (1QB and Superflex).
  * :mod:`trade_eval.ktc_scraper` -- weekly KTC dynasty value snapshots
    (1QB and Superflex), used to extend our historical KTC excel forward
    in time and to build the KTC -> FantasyCalc rank-based calibration.

All side-effecting modules accept their HTTP and blob IO as injected
callables so the parsing / aggregation logic is unit-testable without
network or Azure access.
"""
