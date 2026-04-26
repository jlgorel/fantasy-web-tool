"""Standalone overall-rankings reader (powers the new /overall-ranks endpoint)."""
from __future__ import annotations

from typing import Any

from app.services.blob_store import load_blob


def get_overall_rankings() -> Any:
    """Return the precomputed standard player rankings blob."""
    return load_blob("standard_player_rankings.json")
