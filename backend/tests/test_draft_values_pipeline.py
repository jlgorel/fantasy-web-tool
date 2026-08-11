"""Offline tests for guarded external finished-value ingestion."""
from __future__ import annotations

import importlib
import math
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
AZURE_DIR = REPO / "azure-functions"


@pytest.fixture(scope="module")
def values_module():
    saved_path = list(sys.path)
    saved = sys.modules.pop("draft_values", None)
    sys.path.insert(0, str(AZURE_DIR))
    try:
        yield importlib.import_module("draft_values")
    finally:
        sys.path[:] = saved_path
        sys.modules.pop("draft_values", None)
        if saved is not None:
            sys.modules["draft_values"] = saved


def test_discovers_current_dropbox_workbook_and_forces_download(values_module):
    html = """
    <a href="https://www.dropbox.com/scl/fi/abc/
    2026_FantasyFootball_0.4_elboberto.xlsm?rlkey=secret&amp;dl=0">
    Spreadsheet Download Link</a>
    """.replace("\n", "")
    url = values_module.discover_elboberto_workbook_url(html)
    assert "2026_FantasyFootball_0.4_elboberto.xlsm" in url
    assert "rlkey=secret" in url
    assert "dl=1" in url
    assert values_module.elboberto_version_from_url(url) == "0.4"


def _candidate(values_module, *, required_keys=None, players_per_config=3):
    required_keys = required_keys or ["12|0.5|1qb"]
    configs = {}
    for key in required_keys:
        teams, ppr, format_name = key.split("|")
        configs[key] = {
            "teams": int(teams),
            "ppr": float(ppr),
            "superflex": format_name == "sf",
            "players": [
                {
                    "player_id": str(index),
                    "name": f"Player {index}",
                    "pos": "RB",
                    "vbd": float(100 - index),
                    "overall_rank": index,
                }
                for index in range(1, players_per_config + 1)
            ],
        }
    return {
        "schema_version": 1,
        "year": "2026",
        "provider": "elboberto",
        "generated_at_utc": "2026-08-10T00:00:00+00:00",
        "profile": {
            "id": "qb1-rb2-wr2-te1-flex1-bn6-ptd4",
            "starters": {"QB": 1, "RB": 2, "WR": 2, "TE": 1, "FLEX": 1},
            "bench_size": 6,
            "passing_td": 4,
        },
        "configs": configs,
    }


def test_validates_exact_configs_finite_values_and_duplicates(values_module):
    required = ["12|0.5|1qb"]
    candidate = _candidate(values_module, required_keys=required)
    assert values_module.validate_rankings_blob(
        candidate,
        expected_year=2026,
        required_keys=required,
        min_players_per_config=3,
    ) == []

    candidate["configs"][required[0]]["players"][1]["player_id"] = "1"
    candidate["configs"][required[0]]["players"][2]["vbd"] = math.nan
    errors = values_module.validate_rankings_blob(
        candidate,
        expected_year=2026,
        required_keys=required,
        min_players_per_config=3,
    )
    assert any("duplicate player_id" in error for error in errors)
    assert any("finite provider values" in error for error in errors)


def test_rejects_partial_candidate_without_publishing(values_module):
    uploads = []
    candidate = _candidate(values_module)
    errors = values_module.publish_rankings_candidate(
        candidate,
        year=2026,
        upload=lambda data, name: uploads.append((name, data)),
        load=lambda _name: None,
        required_keys=["12|0.5|1qb", "12|0.5|sf"],
        min_players_per_config=3,
    )
    assert errors
    assert uploads == []


def test_snapshots_prior_healthy_blob_before_publish(values_module):
    required = ["12|0.5|1qb"]
    previous = {"year": "2026", "configs": {"old": {}}}
    candidate = _candidate(values_module, required_keys=required)
    uploads = []

    errors = values_module.publish_rankings_candidate(
        candidate,
        year=2026,
        upload=lambda data, name: uploads.append((name, data)),
        load=lambda name: previous if name == "draft_rankings_2026.json" else None,
        required_keys=required,
        min_players_per_config=3,
    )
    assert errors == []
    assert [name for name, _data in uploads] == [
        "draft_rankings_2026_prev.json",
        "draft_rankings_2026.json",
    ]
    assert uploads[0][1] is previous
    assert uploads[1][1] is candidate


def test_validates_profile_registry(values_module):
    profile_id = "qb1-rb2-wr2-te1-flex1-bn6-ptd4"
    registry = {
        "schema_version": 1,
        "year": "2026",
        "default_profile_id": profile_id,
        "profiles": {
            profile_id: {
                "id": profile_id,
                "blob_name": f"draft_rankings_2026_elboberto_{profile_id}.json",
                "profile": _candidate(values_module)["profile"],
                "config_count": 24,
            },
        },
    }
    assert values_module.validate_profile_registry(registry, expected_year=2026) == []
    registry["default_profile_id"] = "missing"
    assert any(
        "default_profile_id" in error
        for error in values_module.validate_profile_registry(registry, expected_year=2026)
    )


def test_snapshots_profile_blob_before_publish(values_module):
    uploads = []
    result = values_module.publish_json_with_snapshot(
        {"new": True},
        "draft_value_profiles_2026.json",
        upload=lambda data, name: uploads.append((name, data)),
        load=lambda _name: {"old": True},
    )
    assert result is None
    assert [name for name, _data in uploads] == [
        "draft_value_profiles_2026_prev.json",
        "draft_value_profiles_2026.json",
    ]