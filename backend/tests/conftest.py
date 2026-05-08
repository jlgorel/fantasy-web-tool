"""Pytest fixtures for the backend test suite.

All tests run against the fixture blobs checked into
``<repo>/tests/fixtures/blobs/`` (gitignored) — never live Azure. We force
``USE_FIXTURE_BLOBS=1`` *before* any backend module imports so that
``Config`` and ``blob_store`` see the right environment.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List

import pytest

# ---------------------------------------------------------------------------
# Environment: must be set BEFORE any `from app...` import below.
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
BACKEND_DIR = REPO_ROOT / "backend"
FIXTURE_DIR = REPO_ROOT / "tests" / "fixtures" / "blobs"

os.environ["USE_FIXTURE_BLOBS"] = "1"
# Skip the local.settings.json auto-load in app/__init__.py.
os.environ.setdefault("AZURE_FUNCTIONS_ENVIRONMENT", "Production")
# Required by app/__init__.py (CORS + Redis). Values don't matter — Redis is
# replaced with an in-memory stub in the `app` fixture.
os.environ.setdefault("FRONTEND_URL", "http://localhost:3000")
os.environ.setdefault("AZURE_REDIS_CONNECTIONSTRING", "redis://localhost:6379/0")
os.environ.setdefault("AZURE_STORAGE_CONNECTION_STRING", "fixture-mode")

# Make `from app...` and `from config import Config` importable.
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


# ---------------------------------------------------------------------------
# In-memory Redis stub
# ---------------------------------------------------------------------------
class FakeRedis:
    """Tiny stand-in for redis.Redis covering only the methods the routes use.

    The real client is created in ``create_app()`` via ``redis.from_url``; we
    swap it out post-construction with this stub so tests don't need a live
    Redis server (or fakeredis as an extra dep).
    """

    def __init__(self) -> None:
        self._store: Dict[str, bytes] = {}
        self.timeout: float = 5.0

    def set(self, key: str, value: Any, ex: int | None = None) -> bool:
        if isinstance(value, str):
            value = value.encode("utf-8")
        self._store[key] = value
        return True

    def get(self, key: str) -> bytes | None:
        return self._store.get(key)

    def delete(self, key: str) -> int:
        return 1 if self._store.pop(key, None) is not None else 0

    def flushall(self) -> None:
        self._store.clear()


# ---------------------------------------------------------------------------
# Flask app + test client
# ---------------------------------------------------------------------------
@pytest.fixture()
def fake_redis() -> FakeRedis:
    return FakeRedis()


@pytest.fixture()
def app(fake_redis: FakeRedis):
    """Build the Flask app and swap in the fake redis client."""
    from app import create_app  # imported here so env vars above are set

    application = create_app()
    application.redis_client = fake_redis  # type: ignore[attr-defined]
    application.config["TESTING"] = True
    return application


@pytest.fixture()
def client(app):
    return app.test_client()


# ---------------------------------------------------------------------------
# Fixture-blob helpers
# ---------------------------------------------------------------------------
@pytest.fixture(scope="session")
def fixture_dir() -> Path:
    return FIXTURE_DIR


@pytest.fixture(scope="session")
def players_blob() -> Dict[str, Any]:
    with (FIXTURE_DIR / "players.json").open("r", encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture(scope="session")
def rankings_blob() -> Dict[str, List[Dict[str, Any]]]:
    with (FIXTURE_DIR / "standard_player_rankings.json").open("r", encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# League-settings builder
# ---------------------------------------------------------------------------
@pytest.fixture()
def half_ppr_settings() -> Dict[str, float]:
    """A typical 0.5-PPR / 4pt-pass scoring settings dict."""
    return {
        "pass_int": -2.0,
        "pass_2pt": 2.0,
        "rec_td": 6.0,
        "rush_td": 6.0,
        "rec_2pt": 2.0,
        "rec": 0.5,
        "int": 2.0,
        "fum_lost": -2.0,
        "rush_2pt": 2.0,
        "pass_yd": 0.04,
        "pass_td": 4.0,
        "rush_yd": 0.1,
        "rec_yd": 0.1,
    }


@pytest.fixture()
def std_settings(half_ppr_settings) -> Dict[str, float]:
    s = dict(half_ppr_settings)
    s["rec"] = 0.0
    return s


@pytest.fixture()
def full_ppr_6pt_settings(half_ppr_settings) -> Dict[str, float]:
    s = dict(half_ppr_settings)
    s["rec"] = 1.0
    s["pass_td"] = 6.0
    return s


# ---------------------------------------------------------------------------
# Lineup-row builder used across the lineup_compare / optimizer tests
# ---------------------------------------------------------------------------
def _row(pos: str, name: str, *, pid: str | None = None, vegas: float | str = 0.0,
         reallife: str | None = None, team_name: str | None = None) -> Dict[str, Any]:
    return {
        "POS": pos,
        "NAME": name,
        "PID": pid or name.replace(" ", "").lower(),
        "REALLIFE_POS": reallife or (pos if pos != "FLEX" else "RB"),
        "VEGAS": vegas if isinstance(vegas, str) else str(vegas),
        **({"TEAM_NAME": team_name} if team_name else {}),
    }


@pytest.fixture()
def make_row():
    """Factory used by lineup-shape tests to build minimal player rows."""
    return _row
