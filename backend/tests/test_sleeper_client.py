"""Tests for ``app.services.sleeper_client``.

We monkeypatch ``fetch_json`` so the client thinks it's hitting the live
Sleeper API but really pulls JSON from ``tests/fixtures/api/sleeper/``
(captured + sanitized by ``tools/capture_api_fixtures.py``).
"""
from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Any, Dict, List

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SLEEPER_FIX = REPO_ROOT / "tests" / "fixtures" / "api" / "sleeper"


# ---------------------------------------------------------------------------
# Fixture loaders
# ---------------------------------------------------------------------------
def _load(name: str) -> Any:
    return json.loads((SLEEPER_FIX / name).read_text(encoding="utf-8"))


def _available_league_ids() -> List[str]:
    return sorted(
        p.name[len("league_"):-len(".json")]
        for p in SLEEPER_FIX.glob("league_LEAGUE_*.json")
    )


@pytest.fixture
def sleeper_fetch():
    """Returns a fake ``fetch_json(url, **kw)`` routed by URL pattern."""
    available = set(_available_league_ids())  # e.g. {"LEAGUE_001", ...}

    def _fake(url: str, **_kw: Any) -> Any:
        # /v1/user/<username>
        if re.fullmatch(r"https://api\.sleeper\.app/v1/user/[^/]+", url):
            return copy.deepcopy(_load("user.json"))
        # /v1/user/<id>/leagues/nfl/<year>
        if re.fullmatch(r"https://api\.sleeper\.app/v1/user/[^/]+/leagues/nfl/\d+", url):
            leagues = _load("leagues.json")
            # Hide leagues we didn't capture detail files for so the test
            # mock never has to invent fake league/roster payloads.
            return [lg for lg in leagues if lg["league_id"] in available]
        m = re.fullmatch(
            r"https://api\.sleeper\.app/v1/league/(LEAGUE_\d+)", url
        )
        if m:
            return copy.deepcopy(_load(f"league_{m.group(1)}.json"))
        m = re.fullmatch(
            r"https://api\.sleeper\.app/v1/league/(LEAGUE_\d+)/rosters", url
        )
        if m:
            return copy.deepcopy(_load(f"rosters_{m.group(1)}.json"))
        raise AssertionError(f"sleeper_fetch: unexpected url {url!r}")

    return _fake


@pytest.fixture
def patched_sleeper(monkeypatch, sleeper_fetch):
    """Apply the fake fetcher + pin the season year to '2026'."""
    import app.services.sleeper_client as sc

    monkeypatch.setattr(sc, "fetch_json", sleeper_fetch)
    monkeypatch.setattr(sc, "get_current_fantasy_year", lambda: "2026")
    return sc


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
class TestGetSleeperRostersForUser:
    def test_returns_one_entry_per_captured_league(self, patched_sleeper):
        rosters = patched_sleeper.get_sleeper_rosters_for_user("jlgorel")
        assert isinstance(rosters, list)
        assert len(rosters) == len(_available_league_ids())

    def test_each_roster_has_required_keys(self, patched_sleeper):
        rosters = patched_sleeper.get_sleeper_rosters_for_user("jlgorel")
        required = {"league", "pids", "settings", "positions", "all_owned", "starters"}
        for r in rosters:
            assert required <= r.keys(), f"missing: {required - r.keys()}"

    def test_all_owned_is_superset_of_user_pids(self, patched_sleeper):
        rosters = patched_sleeper.get_sleeper_rosters_for_user("jlgorel")
        for r in rosters:
            assert set(r["pids"]).issubset(set(r["all_owned"])), (
                f"league {r['league']}: user pids leaked outside all_owned"
            )

    def test_starters_drop_empty_slots(self, patched_sleeper):
        """Sleeper uses '0' / None for unfilled slots; the client strips them."""
        rosters = patched_sleeper.get_sleeper_rosters_for_user("jlgorel")
        for r in rosters:
            assert all(s and s != "0" for s in r["starters"])

    def test_starters_are_subset_of_user_pids(self, patched_sleeper):
        rosters = patched_sleeper.get_sleeper_rosters_for_user("jlgorel")
        for r in rosters:
            # starter pids must come from your roster
            assert set(r["starters"]).issubset(set(r["pids"])), (
                f"league {r['league']}: starter pid not on roster"
            )

    def test_positions_match_real_league_shape(self, patched_sleeper):
        """At least one roster slot should be a recognizable starter position."""
        rosters = patched_sleeper.get_sleeper_rosters_for_user("jlgorel")
        skill = {"QB", "RB", "WR", "TE", "FLEX", "SUPER_FLEX", "REC_FLEX"}
        for r in rosters:
            assert skill & set(r["positions"]), r["league"]


class TestSleeperEdgeCases:
    """Synthetic scenarios that exercise branches the captured fixtures don't."""

    def _patch(self, monkeypatch, fake):
        import app.services.sleeper_client as sc

        monkeypatch.setattr(sc, "fetch_json", fake)
        monkeypatch.setattr(sc, "get_current_fantasy_year", lambda: "2026")
        return sc

    def test_idp_league_is_skipped(self, monkeypatch):
        """A league with IDP_FLEX / DB / LB / DL is dropped silently."""
        league_detail = copy.deepcopy(_load("league_LEAGUE_001.json"))
        league_detail["roster_positions"] = ["QB", "RB", "WR", "IDP_FLEX", "BN"]

        rosters_blob = _load("rosters_LEAGUE_001.json")

        def fake(url: str, **_kw: Any) -> Any:
            if url.endswith("/v1/user/jlgorel"):
                return _load("user.json")
            if "/leagues/nfl/" in url:
                # Return only one synthetic IDP league.
                return [{
                    "name": "IDP League",
                    "league_id": "LEAGUE_IDP",
                    "status": "in_season",
                }]
            if url.endswith("/league/LEAGUE_IDP"):
                return league_detail
            if url.endswith("/league/LEAGUE_IDP/rosters"):
                return rosters_blob
            raise AssertionError(url)

        sc = self._patch(monkeypatch, fake)
        assert sc.get_sleeper_rosters_for_user("jlgorel") == []

    def test_league_with_no_user_roster_is_skipped(self, monkeypatch):
        """If the user owns no team in a league it must not crash — skip it."""
        league_detail = copy.deepcopy(_load("league_LEAGUE_001.json"))
        # Strip every roster ownership so `next(...)` returns None.
        rosters_blob = copy.deepcopy(_load("rosters_LEAGUE_001.json"))
        for r in rosters_blob:
            r["owner_id"] = "USER_OTHER"

        def fake(url: str, **_kw: Any) -> Any:
            if url.endswith("/v1/user/jlgorel"):
                return _load("user.json")
            if "/leagues/nfl/" in url:
                return [{
                    "name": "Orphan League",
                    "league_id": "LEAGUE_ORPHAN",
                    "status": "in_season",
                }]
            if url.endswith("/league/LEAGUE_ORPHAN"):
                return league_detail
            if url.endswith("/league/LEAGUE_ORPHAN/rosters"):
                return rosters_blob
            raise AssertionError(url)

        sc = self._patch(monkeypatch, fake)
        assert sc.get_sleeper_rosters_for_user("jlgorel") == []

    def test_skips_non_active_league_status(self, monkeypatch):
        """Drafted/complete leagues outside pre_draft/in_season/post_season are filtered."""
        def fake(url: str, **_kw: Any) -> Any:
            if url.endswith("/v1/user/jlgorel"):
                return _load("user.json")
            if "/leagues/nfl/" in url:
                return [{
                    "name": "Old League",
                    "league_id": "LEAGUE_OLD",
                    "status": "complete",  # not in the allowed set
                }]
            raise AssertionError(f"should not have been called: {url}")

        sc = self._patch(monkeypatch, fake)
        assert sc.get_sleeper_rosters_for_user("jlgorel") == []
