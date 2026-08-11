"""Offline tests for the production FFC ADP builder."""
from __future__ import annotations

import importlib.util
from datetime import datetime, timezone
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = REPO_ROOT / "azure-functions" / "draft_adp.py"


@pytest.fixture(scope="module")
def adp():
    spec = importlib.util.spec_from_file_location("draft_adp_test", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _rankings():
    players = [
        {"player_id": "1", "name": "Jahmyr Gibbs", "pos": "RB"},
        {"player_id": "2", "name": "Ja'Marr Chase Jr.", "pos": "WR"},
        {"player_id": "3", "name": "No ADP Player", "pos": "TE"},
    ]
    return {
        "schema_version": 1,
        "year": "2026",
        "configs": {
            "12|0.5|1qb": {
                "teams": 12,
                "ppr": 0.5,
                "superflex": False,
                "players": players,
            }
        },
    }


def _ffc_payload():
    return {
        "status": "Success",
        "meta": {
            "total_drafts": 1889,
            "start_date": "2026-08-01",
            "end_date": "2026-08-08",
        },
        "players": [
            {
                "name": "Jahmyr Gibbs", "position": "RB", "adp": 1.4,
                "stdev": 0.6, "times_drafted": 274, "high": 1, "low": 4,
            },
            {
                "name": "Ja'Marr Chase", "position": "WR", "adp": 4.0,
                "stdev": 0.9, "times_drafted": 266, "high": 1, "low": 7,
            },
        ],
    }


def test_format_mapping(adp):
    assert adp.ffc_format(0, False) == "standard"
    assert adp.ffc_format(0.5, False) == "half-ppr"
    assert adp.ffc_format(1, False) == "ppr"
    assert adp.ffc_format(0, True) == "2qb"
    assert adp.ffc_format(1, True) == "2qb"


def test_name_map_keeps_quality_metadata(adp):
    name_map = adp.ffc_name_map(_ffc_payload())
    row = name_map[("jahmyr gibbs", "RB")]
    assert row == {
        "adp": 1.4,
        "stdev": 0.6,
        "stdev_source": "observed",
        "times_drafted": 274,
        "high": 1,
        "low": 4,
    }


def test_build_blob_matches_ids_and_source_metadata(adp):
    urls = []

    def fetch(url):
        urls.append(url)
        return _ffc_payload()

    blob = adp.build_adp_blob(
        "2026",
        _rankings(),
        fetch_json=fetch,
        generated_at=datetime(2026, 8, 8, tzinfo=timezone.utc),
    )
    cfg = blob["configs"]["12|0.5|1qb"]
    assert cfg["format"] == "half-ppr"
    assert cfg["total_drafts"] == 1889
    assert cfg["matched"] == 2
    assert cfg["players"]["1"]["adp"] == 1.4
    # Suffix-normalized exact-name join.
    assert cfg["players"]["2"]["adp"] == 4.0
    assert blob["generated_at_utc"].startswith("2026-08-08")
    assert len(urls) == 1


def test_missing_team_feed_falls_back_to_12(adp):
    rankings = _rankings()
    cfg = rankings["configs"].pop("12|0.5|1qb")
    cfg["teams"] = 10
    rankings["configs"]["10|0.5|1qb"] = cfg

    def fetch(url):
        return None if "teams=10" in url else _ffc_payload()

    blob = adp.build_adp_blob("2026", rankings, fetch_json=fetch)
    cfg_out = blob["configs"]["10|0.5|1qb"]
    assert cfg_out["source_teams"] == 12
    assert cfg_out["matched"] == 2


def test_build_adp_only_pool_from_sleeper_players(adp):
    players = {
        "1": {"full_name": "Jahmyr Gibbs", "fantasy_positions": ["RB"]},
        "2": {"full_name": "Ja'Marr Chase", "fantasy_positions": ["WR"]},
    }
    blob = adp.build_adp_blob_from_players(
        "2026", players,
        fetch_json=lambda _url: _ffc_payload(),
        team_sizes=(12,), ppr_values=(0.5,),
        generated_at=datetime(2026, 8, 8, tzinfo=timezone.utc),
    )
    assert blob["adp_only"] is True
    one_qb = blob["configs"]["12|0.5|1qb"]
    assert one_qb["matched"] == 2
    assert one_qb["players"]["1"]["name"] == "Jahmyr Gibbs"
    assert one_qb["players"]["2"]["pos"] == "WR"
    assert adp.validate_adp_blob(
        blob, min_config_coverage=0.5, min_overall_coverage=0.5,
    ) == []


def test_validation_rejects_partial_or_invalid_update(adp):
    healthy = adp.build_adp_blob(
        "2026", _rankings(), fetch_json=lambda _url: _ffc_payload(),
    )
    assert adp.validate_adp_blob(
        healthy, min_config_coverage=0.5, min_overall_coverage=0.5,
    ) == []

    broken = adp.build_adp_blob(
        "2026", _rankings(), fetch_json=lambda _url: None,
    )
    errors = adp.validate_adp_blob(broken)
    assert any("coverage" in error for error in errors)


def test_draft_season_window(adp):
    assert adp.is_draft_season(datetime(2026, 8, 8, tzinfo=timezone.utc))
    assert not adp.is_draft_season(datetime(2026, 1, 8, tzinfo=timezone.utc))
