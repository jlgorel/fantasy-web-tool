"""FantasyCalc redraft/dynasty player values.

Used by the trade-accolades pipeline to put a single number on each side of
a trade. We hit the public ``/values/current`` endpoint, which returns
positional rankings + a numeric ``value`` (FantasyCalc's proprietary
trade-value scale, anchored so the #1 overall is ~10000).

The endpoint is keyed on ``(is_dynasty, num_qbs, ppr)`` — three params that
match what we've already inferred from the league context — so we cache one
``sleeper_id -> value`` map per parameter combo.
"""
from __future__ import annotations

import logging
from typing import Dict, Tuple

from app.services.http_utils import fetch_json

logger = logging.getLogger(__name__)


# Map our internal ``skill_score_key`` to FantasyCalc's ``ppr`` query value.
_SCORING_TO_PPR = {
    "std": 0,
    "half_ppr": 0.5,
    "ppr": 1,
}


# Process-local cache. Wrapped payloads are also Redis-cached upstream so
# this rarely re-hits the wire even across requests within a worker, but
# we still want to avoid duplicate calls when one league touches the
# function several times in a single pipeline run.
_VALUE_CACHE: Dict[Tuple[bool, str, float], Dict[str, float]] = {}


def _build_url(is_dynasty: bool, num_qbs: str, ppr: float) -> str:
    return (
        "https://api.fantasycalc.com/values/current"
        f"?isDynasty={'true' if is_dynasty else 'false'}"
        f"&numQbs={num_qbs}"
        f"&ppr={ppr}"
    )


def get_player_values(
    is_dynasty: bool,
    num_qbs: str,
    skill_score_key: str,
) -> Dict[str, float]:
    """Return ``{sleeper_player_id: trade_value}`` for the given league shape.

    Returns an empty dict (never raises) on network failure or unexpected
    payload shape — the trade pipeline treats missing values as 0 and
    callers can decide whether to surface "no FantasyCalc data" inline.
    """
    ppr = _SCORING_TO_PPR.get(skill_score_key, 0.5)
    cache_key = (bool(is_dynasty), str(num_qbs), float(ppr))
    if cache_key in _VALUE_CACHE:
        return _VALUE_CACHE[cache_key]

    url = _build_url(is_dynasty, num_qbs, ppr)
    try:
        payload = fetch_json(url)
    except Exception as e:
        logger.warning("FantasyCalc fetch failed (%s): %s", url, e)
        _VALUE_CACHE[cache_key] = {}
        return {}

    if not isinstance(payload, list):
        logger.warning("FantasyCalc returned unexpected payload type: %s", type(payload))
        _VALUE_CACHE[cache_key] = {}
        return {}

    values: Dict[str, float] = {}
    for row in payload:
        # Shape: {"player": {"sleeperId": "1234", "name": "...", ...}, "value": 8200, ...}
        player = (row or {}).get("player") or {}
        sid = player.get("sleeperId")
        val = row.get("value")
        if not sid or val is None:
            continue
        try:
            values[str(sid)] = float(val)
        except (TypeError, ValueError):
            continue

    _VALUE_CACHE[cache_key] = values
    logger.info(
        "FantasyCalc loaded %d player values (dynasty=%s, qbs=%s, ppr=%s)",
        len(values), is_dynasty, num_qbs, ppr,
    )
    return values


def clear_cache() -> None:
    """Test hook — wipe the process-local value cache."""
    _VALUE_CACHE.clear()
