"""Azure blob storage loader for fantasy data JSON blobs.

Centralizes the only place we talk to Azure Blob Storage so the rest of the
service layer can stay dependency-free and easier to mock in tests.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from azure.storage.blob import BlobServiceClient

from app.config import Config

logger = logging.getLogger(__name__)


def load_json_from_azure_storage(blob_name: str, container_name: str, connection_string: str) -> Any:
    """Download a JSON blob from Azure storage and return the parsed object.

    If the blob is the players catalog we run normalization (Travis Hunter, etc.)
    so every downstream consumer sees the same shape.
    """
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
    return load_json_from_azure_storage(
        blob_name, Config.containername, Config.azure_storage_connection_string
    )


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
