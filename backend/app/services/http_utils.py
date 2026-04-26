"""Tiny HTTP helpers shared by the Sleeper / Fleaflicker clients.

All outbound HTTP calls go through here so we get consistent timeouts,
retries with exponential backoff, and a single place to flip behavior.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Optional

import requests

logger = logging.getLogger(__name__)

# Defaults chosen to stay well under typical platform request timeouts (30s on
# Azure Functions consumption plan, ~60s on most container PaaS) while still
# giving slow upstreams (Fleaflicker, Sleeper) some breathing room.
DEFAULT_TIMEOUT_SECONDS = 10
DEFAULT_MAX_RETRIES = 2  # so total attempts == 3
RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


def fetch_json(
    url: str,
    *,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    max_retries: int = DEFAULT_MAX_RETRIES,
    backoff_seconds: float = 0.5,
) -> Optional[Any]:
    """GET a URL and return parsed JSON, or None on failure.

    Retries transient errors (timeout / connection error / 5xx / 429) with
    exponential backoff. Always returns None instead of raising so callers
    can keep their existing "if data is None: skip" patterns.
    """
    last_err: Optional[str] = None
    for attempt in range(max_retries + 1):
        try:
            resp = requests.get(url, timeout=timeout)
        except requests.RequestException as e:
            last_err = f"{type(e).__name__}: {e}"
            if attempt < max_retries:
                _sleep_backoff(backoff_seconds, attempt)
                continue
            logger.error("Error fetching %s: %s", url, last_err)
            return None

        if resp.status_code == 200:
            try:
                return resp.json()
            except ValueError as e:
                logger.error("Invalid JSON from %s: %s", url, e)
                return None

        if resp.status_code in RETRYABLE_STATUS_CODES and attempt < max_retries:
            last_err = f"HTTP {resp.status_code}"
            _sleep_backoff(backoff_seconds, attempt)
            continue

        logger.error("Error fetching %s: HTTP %s", url, resp.status_code)
        return None

    logger.error("Error fetching %s after retries: %s", url, last_err)
    return None


def _sleep_backoff(base: float, attempt: int) -> None:
    """Exponential backoff: base, 2*base, 4*base, ..."""
    time.sleep(base * (2 ** attempt))
