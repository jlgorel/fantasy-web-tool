"""Tests for the local Task Scheduler DraftKings refresh runner."""
from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
RUNNER_PATH = REPO_ROOT / "tools" / "refresh_draftkings_local.py"


@pytest.fixture
def runner():
    spec = importlib.util.spec_from_file_location("refresh_draftkings_local_test", RUNNER_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
        yield module
    finally:
        sys.modules.pop(spec.name, None)


def test_load_local_settings(runner, tmp_path, monkeypatch):
    settings = tmp_path / "local.settings.json"
    settings.write_text(json.dumps({
        "Values": {"AZURE_STORAGE_CONNECTION_STRING": "UseDevelopmentStorage=true"}
    }))
    monkeypatch.delenv("AZURE_STORAGE_CONNECTION_STRING", raising=False)

    runner.load_local_settings(settings)

    assert os.environ["AZURE_STORAGE_CONNECTION_STRING"] == "UseDevelopmentStorage=true"


def test_exclusive_lock_rejects_overlap(runner, tmp_path):
    lock = tmp_path / "refresh.lock"
    with runner.exclusive_refresh_lock(lock):
        with pytest.raises(runner.RefreshAlreadyRunning):
            with runner.exclusive_refresh_lock(lock):
                pass
    assert not lock.exists()


def test_exclusive_lock_recovers_stale_file(runner, tmp_path):
    lock = tmp_path / "refresh.lock"
    lock.write_text("stale")
    os.utime(lock, (0, 0))

    with runner.exclusive_refresh_lock(lock, stale_seconds=1):
        assert lock.exists()
    assert not lock.exists()


class FakeApp:
    def __init__(self, *, in_season=True, failures=0):
        self.in_season = in_season
        self.failures = failures
        self.fetch_calls = 0
        self.uploads = []

    def is_in_fantasy_season(self):
        return self.in_season

    def getDraftkingsProjections(self):
        self.fetch_calls += 1
        if self.fetch_calls <= self.failures:
            raise RuntimeError("temporary failure")
        return {
            "complete": {"Simulations": {"PPR": {}}},
            "partial": {"Simulations": {"error": "Not enough data"}},
        }

    def form_standard_player_rankings(self):
        return {"halfppr_4ptpass": [], "ppr_4ptpass": []}

    def capture_vegas_history(self):
        return None

    def get_current_fantasy_year(self):
        return 2026

    def get_current_nfl_week(self):
        return 1

    def upload_to_azure_blob(self, data, blob_name, _description):
        self.uploads.append((blob_name, data))


def test_run_refresh_publishes_counts_and_status(runner):
    app = FakeApp()

    result = runner.run_refresh(app)

    assert result["status"] == "success"
    assert result["projection_players"] == 2
    assert result["players_with_simulations"] == 1
    assert result["ranking_variants"] == 2
    assert app.uploads[-1][0] == runner.STATUS_BLOB


def test_run_refresh_skips_offseason(runner):
    app = FakeApp(in_season=False)

    result = runner.run_refresh(app)

    assert result["status"] == "skipped_offseason"
    assert app.fetch_calls == 0


def test_retry_succeeds_after_transient_failure(runner, monkeypatch):
    app = FakeApp(failures=2)
    monkeypatch.setattr(runner.time, "sleep", lambda _seconds: None)

    result = runner.run_with_retries(app, attempts=3)

    assert result["status"] == "success"
    assert app.fetch_calls == 3
