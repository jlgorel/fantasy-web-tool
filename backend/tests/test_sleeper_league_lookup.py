"""Tests for ``app.services.sleeper_league_lookup``."""
from __future__ import annotations

from typing import Any, Dict, List

import pytest

import app.services.sleeper_league_lookup as sll


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _make_router(routes: Dict[str, Any]):
    """Return a fake fetch_json that maps url -> precomputed value (or raises)."""

    def _fake(url: str, **_kw: Any) -> Any:
        if url not in routes:
            raise AssertionError(f"unexpected url: {url}")
        val = routes[url]
        if isinstance(val, Exception):
            raise val
        return val

    return _fake


# ---------------------------------------------------------------------------
# get_user_leagues
# ---------------------------------------------------------------------------
class TestGetUserLeagues:
    def test_returns_projected_league_rows(self, monkeypatch):
        routes = {
            "https://api.sleeper.app/v1/user/jlgorel": {"user_id": "U1", "username": "jlgorel"},
            "https://api.sleeper.app/v1/user/U1/leagues/nfl/2025": [
                {
                    "league_id": "L_2025_A",
                    "name": "Best League",
                    "season": "2025",
                    "previous_league_id": "L_2024_A",
                    "total_rosters": 12,
                    "status": "in_season",
                    "extra_field": "ignored",
                },
                {
                    "league_id": "L_2025_B",
                    "name": "Other League",
                    "season": "2025",
                    "previous_league_id": None,
                    "total_rosters": 10,
                    "status": "complete",
                },
            ],
        }
        monkeypatch.setattr(sll, "fetch_json", _make_router(routes))

        leagues = sll.get_user_leagues("jlgorel", "2025")

        assert len(leagues) == 2
        assert leagues[0] == {
            "league_id": "L_2025_A",
            "name": "Best League",
            "season": "2025",
            "previous_league_id": "L_2024_A",
            "total_rosters": 12,
            "status": "in_season",
            "dynasty": False,
        }
        assert "extra_field" not in leagues[0]

    def test_exclude_dynasty_filters_type_2_leagues(self, monkeypatch):
        routes = {
            "https://api.sleeper.app/v1/user/jlgorel": {"user_id": "U1"},
            "https://api.sleeper.app/v1/user/U1/leagues/nfl/2025": [
                {"league_id": "REDRAFT", "name": "Redraft", "season": "2025",
                 "settings": {"type": 0}},
                {"league_id": "KEEPER", "name": "Keeper", "season": "2025",
                 "settings": {"type": 1}},
                {"league_id": "DYNASTY", "name": "Dynasty", "season": "2025",
                 "settings": {"type": 2}},
            ],
        }
        monkeypatch.setattr(sll, "fetch_json", _make_router(routes))

        # Default: all leagues returned, each flagged with its dynasty status.
        all_leagues = sll.get_user_leagues("jlgorel", "2025")
        assert {lg["league_id"]: lg["dynasty"] for lg in all_leagues} == {
            "REDRAFT": False, "KEEPER": False, "DYNASTY": True,
        }
        # exclude_dynasty drops only the dynasty league.
        kept = sll.get_user_leagues("jlgorel", "2025", exclude_dynasty=True)
        assert [lg["league_id"] for lg in kept] == ["REDRAFT", "KEEPER"]

    def test_unknown_username_returns_empty(self, monkeypatch):
        routes = {"https://api.sleeper.app/v1/user/ghost": None}
        monkeypatch.setattr(sll, "fetch_json", _make_router(routes))
        assert sll.get_user_leagues("ghost", "2025") == []

    def test_user_with_no_leagues_returns_empty(self, monkeypatch):
        routes = {
            "https://api.sleeper.app/v1/user/loner": {"user_id": "U2"},
            "https://api.sleeper.app/v1/user/U2/leagues/nfl/2025": [],
        }
        monkeypatch.setattr(sll, "fetch_json", _make_router(routes))
        assert sll.get_user_leagues("loner", "2025") == []

    def test_empty_username_returns_empty_without_fetch(self, monkeypatch):
        # If fetch_json gets called the router AssertionError will fire.
        monkeypatch.setattr(sll, "fetch_json", _make_router({}))
        assert sll.get_user_leagues("", "2025") == []

    def test_skips_leagues_without_id(self, monkeypatch):
        routes = {
            "https://api.sleeper.app/v1/user/u": {"user_id": "U3"},
            "https://api.sleeper.app/v1/user/U3/leagues/nfl/2025": [
                {"league_id": None, "name": "broken"},
                {"league_id": "L_OK", "name": "ok", "season": "2025"},
            ],
        }
        monkeypatch.setattr(sll, "fetch_json", _make_router(routes))
        leagues = sll.get_user_leagues("u", "2025")
        assert [lg["league_id"] for lg in leagues] == ["L_OK"]


# ---------------------------------------------------------------------------
# resolve_league_for_year
# ---------------------------------------------------------------------------
class TestResolveLeagueForYear:
    def test_returns_input_when_year_matches(self, monkeypatch):
        routes = {
            "https://api.sleeper.app/v1/league/L_2025": {
                "league_id": "L_2025",
                "season": "2025",
                "previous_league_id": "L_2024",
            }
        }
        monkeypatch.setattr(sll, "fetch_json", _make_router(routes))
        assert sll.resolve_league_for_year("L_2025", "2025") == "L_2025"

    def test_walks_chain_back_multiple_hops(self, monkeypatch):
        routes = {
            "https://api.sleeper.app/v1/league/L_2025": {
                "season": "2025", "previous_league_id": "L_2024",
            },
            "https://api.sleeper.app/v1/league/L_2024": {
                "season": "2024", "previous_league_id": "L_2023",
            },
            "https://api.sleeper.app/v1/league/L_2023": {
                "season": "2023", "previous_league_id": None,
            },
        }
        monkeypatch.setattr(sll, "fetch_json", _make_router(routes))
        assert sll.resolve_league_for_year("L_2025", "2023") == "L_2023"

    def test_returns_none_when_chain_too_short(self, monkeypatch):
        routes = {
            "https://api.sleeper.app/v1/league/L_2025": {
                "season": "2025", "previous_league_id": "L_2024",
            },
            "https://api.sleeper.app/v1/league/L_2024": {
                "season": "2024", "previous_league_id": None,
            },
        }
        monkeypatch.setattr(sll, "fetch_json", _make_router(routes))
        assert sll.resolve_league_for_year("L_2025", "2020") is None

    def test_returns_none_for_zero_previous_pointer(self, monkeypatch):
        # Sleeper sometimes serializes the end-of-chain as the string "0".
        routes = {
            "https://api.sleeper.app/v1/league/L_2025": {
                "season": "2025", "previous_league_id": "0",
            }
        }
        monkeypatch.setattr(sll, "fetch_json", _make_router(routes))
        assert sll.resolve_league_for_year("L_2025", "2024") is None

    def test_returns_none_when_league_not_found(self, monkeypatch):
        routes = {"https://api.sleeper.app/v1/league/L_BOGUS": None}
        monkeypatch.setattr(sll, "fetch_json", _make_router(routes))
        assert sll.resolve_league_for_year("L_BOGUS", "2024") is None

    def test_bounded_depth_against_cyclic_chain(self, monkeypatch):
        # Self-referential chain — should bail out instead of infinite-looping.
        call_count = {"n": 0}

        def _fake(url: str, **_kw: Any) -> Any:
            call_count["n"] += 1
            return {"season": "2025", "previous_league_id": "L_LOOP"}

        monkeypatch.setattr(sll, "fetch_json", _fake)
        assert sll.resolve_league_for_year("L_LOOP", "1999") is None
        # Should hit the depth cap and stop.
        assert call_count["n"] <= sll._MAX_PREVIOUS_HOPS

    def test_empty_inputs_return_none(self, monkeypatch):
        monkeypatch.setattr(sll, "fetch_json", _make_router({}))
        assert sll.resolve_league_for_year("", "2024") is None
        assert sll.resolve_league_for_year("L", "") is None


# ---------------------------------------------------------------------------
# get_league_season_chain
# ---------------------------------------------------------------------------
class TestGetLeagueSeasonChain:
    def test_returns_full_chain_newest_first(self, monkeypatch):
        routes = {
            "https://api.sleeper.app/v1/league/L_2026": {
                "season": "2026", "previous_league_id": "L_2025",
            },
            "https://api.sleeper.app/v1/league/L_2025": {
                "season": "2025", "previous_league_id": "L_2024",
            },
            "https://api.sleeper.app/v1/league/L_2024": {
                "season": "2024", "previous_league_id": None,
            },
        }
        monkeypatch.setattr(sll, "fetch_json", _make_router(routes))
        chain = sll.get_league_season_chain("L_2026")
        assert chain == [
            {"season": "2026", "league_id": "L_2026"},
            {"season": "2025", "league_id": "L_2025"},
            {"season": "2024", "league_id": "L_2024"},
        ]

    def test_single_season_league(self, monkeypatch):
        routes = {
            "https://api.sleeper.app/v1/league/L_NEW": {
                "season": "2026", "previous_league_id": None,
            },
        }
        monkeypatch.setattr(sll, "fetch_json", _make_router(routes))
        assert sll.get_league_season_chain("L_NEW") == [
            {"season": "2026", "league_id": "L_NEW"},
        ]

    def test_empty_input(self, monkeypatch):
        monkeypatch.setattr(sll, "fetch_json", _make_router({}))
        assert sll.get_league_season_chain("") == []

    def test_bounded_against_cycles(self, monkeypatch):
        # Self-referential previous pointer should bail out via the seen-set.
        routes = {
            "https://api.sleeper.app/v1/league/L_LOOP": {
                "season": "2026", "previous_league_id": "L_LOOP",
            },
        }
        monkeypatch.setattr(sll, "fetch_json", _make_router(routes))
        chain = sll.get_league_season_chain("L_LOOP")
        # Should record the season once and stop, not loop forever.
        assert chain == [{"season": "2026", "league_id": "L_LOOP"}]
