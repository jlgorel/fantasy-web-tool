"""Azure blob storage loader for fantasy data JSON blobs.

Centralizes the only place we talk to Azure Blob Storage so the rest of the
service layer can stay dependency-free and easier to mock in tests.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from azure.storage.blob import BlobServiceClient

from app.config import Config

logger = logging.getLogger(__name__)


# When USE_FIXTURE_BLOBS=1 we bypass Azure entirely and read JSON blobs from
# tests/fixtures/blobs/. This lets the whole backend run offline against a
# frozen snapshot of real production data — important during the off-season
# when Vegas isn't posting any lines and the live blobs are empty.
_REPO_ROOT = Path(__file__).resolve().parents[3]
_FIXTURE_DIR = _REPO_ROOT / "tests" / "fixtures" / "blobs"


def _fixture_enabled() -> bool:
    return os.environ.get("USE_FIXTURE_BLOBS", "").lower() in ("1", "true", "yes")


def _load_fixture(blob_name: str) -> Any:
    path = _FIXTURE_DIR / blob_name
    if not path.exists():
        raise FileNotFoundError(
            f"USE_FIXTURE_BLOBS is set but fixture {path} is missing. "
            f"Drop a snapshot of {blob_name} into tests/fixtures/blobs/."
        )
    logger.info("Loading FIXTURE blob %s from %s", blob_name, path)
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if blob_name.lower() == "players.json":
        try:
            normalize_players_positions(data)
        except Exception as e:  # pragma: no cover
            logger.warning("normalize_players_positions failed on fixture: %s", e)
    return data


def load_json_from_azure_storage(blob_name: str, container_name: str, connection_string: str) -> Any:
    """Download a JSON blob from Azure storage and return the parsed object.

    If the blob is the players catalog we run normalization (Travis Hunter, etc.)
    so every downstream consumer sees the same shape.
    """
    if _fixture_enabled():
        return _load_fixture(blob_name)

    logger.info("Loading blob %s from container %s", blob_name, container_name)
    blob_service_client = BlobServiceClient.from_connection_string(connection_string)
    blob_client = blob_service_client.get_blob_client(container=container_name, blob=blob_name)
    blob_data = blob_client.download_blob()
    data = json.loads(blob_data.readall())

    if blob_name.lower() == "players.json":
        try:
            normalize_players_positions(data)
        except Exception as e:  # pragma: no cover - defensive
            logger.warning("normalize_players_positions failed: %s", e)

    return data


def load_blob(blob_name: str) -> Any:
    """Convenience wrapper that uses the default container + connection string."""
    if _fixture_enabled():
        return _load_fixture(blob_name)
    return load_json_from_azure_storage(
        blob_name, Config.containername, Config.azure_storage_connection_string
    )


def try_load_blob(blob_name: str) -> Any:
    """Like ``load_blob`` but returns ``None`` if the blob is missing.

    Used by callers that want to iterate a range of optional per-year blobs
    (e.g. ``player_season_scoring_{year}.json``, ``owned_history_{year}.json``)
    without raising for years we don't have data for yet.
    """
    try:
        return load_blob(blob_name)
    except FileNotFoundError:
        return None
    except Exception as e:
        # Azure raises a variety of exception types depending on auth /
        # network state. We treat any of them as "blob unavailable" so a
        # transient hiccup doesn't 500 the whole request.
        logger.info("try_load_blob: %s unavailable (%s)", blob_name, e)
        return None


def normalize_players_positions(players_dict: dict) -> dict:
    """Mutate the Sleeper players dict in-place so override players (e.g. Travis
    Hunter) are listed at their fantasy-relevant position first.

    players_dict is expected to be the players.json structure:
        { pid: {"full_name": "...", "fantasy_positions": [...]} }
    """
    overrides = {
        "travis hunter": "WR",
    }

    for _pid, pdata in players_dict.items():
        full_name = (pdata.get("full_name") or "").strip()
        if not full_name:
            continue
        key = full_name.lower()
        if key in overrides:
            forced_pos = overrides[key]
            positions = pdata.get("fantasy_positions") or []
            positions = [p for p in positions if p != forced_pos]
            positions.insert(0, forced_pos)
            pdata["fantasy_positions"] = positions
    return players_dict
