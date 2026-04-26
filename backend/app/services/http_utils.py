"""Tiny HTTP helpers shared by the Sleeper / Fleaflicker clients."""
from __future__ import annotations

import logging
from typing import Any, Optional

import requests

logger = logging.getLogger(__name__)


def fetch_json(url: str) -> Optional[Any]:
    """GET a URL and return parsed JSON. Logs and returns None on non-200."""
    resp = requests.get(url)
    if resp.status_code == 200:
        return resp.json()
    logger.error("Error fetching %s: %s", url, resp.status_code)
    return None
