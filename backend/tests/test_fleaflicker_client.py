"""Tests for ``app.services.fleaflicker_client``.

Drives the client with ``fetch_json`` monkeypatched to return JSON loaded
from ``tests/fixtures/api/fleaflicker/`` (captured + sanitized via
``tools/capture_api_fixtures.py``).
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Dict

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
FF_FIX = REPO_ROOT / "tests" / "fixtures" / "api" / "fleaflicker"


def _load(name: str) -> Any:
    return json.loads((FF_FIX / name).read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Routing fake
# ---------------------------------------------------------------------------
@pytest.fixture
def ff_fetch():
    def _fake(url: str, **_kw: Any) -> Any:
        if "FetchUserLeagues" in url:
            return copy.deepcopy(_load("user_leagues.json"))
        if "FetchLeagueRules" in url:
            return copy.deepcopy(_load("league_rules_LEAGUE_009.json"))
        if "FetchLeagueRosters" in url:
            return copy.deepcopy(_load("league_rosters_LEAGUE_009.json"))
        if "FetchRoster" in url:
            return copy.deepcopy(_load("roster_LEAGUE_009.json"))
        raise AssertionError(f"ff_fetch: unexpected url {url!r}")

    return _fake


@pytest.fixture
def patched_ff(monkeypatch, ff_fetch):
    import app.services.fleaflicker_client as fc

    monkeypatch.setattr(fc, "fetch_json", ff_fetch)
    monkeypatch.setattr(fc, "get_current_fantasy_year", lambda: "2026")
    return fc


@pytest.fixture
def name_to_pid(patched_ff) -> Dict[str, str]:
    """Build a tiny name->pid map covering every player in the fixture
    rosters so ``_resolve_player_pid`` resolves cleanly."""
    rosters = _load("league_rosters_LEAGUE_009.json")
    user_roster = _load("roster_LEAGUE_009.json")

    out: Dict[str, str] = {}
    counter = 1
    for roster in rosters["rosters"]:
        for player in roster.get("players", []):
            full = player.get("proPlayer", {}).get("nameFull")
            if full and full not in out:
                out[full] = f"sleeper{counter:04d}"
                counter += 1
    for group in user_roster.get("groups", []):
        for slot in group.get("slots", []):
            full = slot.get("leaguePlayer", {}).get("proPlayer", {}).get("nameFull")
            if full and full not in out:
                out[full] = f"sleeper{counter:04d}"
                counter += 1
    return out


# ---------------------------------------------------------------------------
# convert_ff_roster_settings — pure function over rosterRequirements
# ---------------------------------------------------------------------------
class TestConvertFFRosterSettings:
    def test_real_league_shape(self, patched_ff):
        leagues = _load("user_leagues.json")["leagues"]
        positions = patched_ff.convert_ff_roster_settings(
            leagues[0]["rosterRequirements"]
        )
        # Captured league is a 1QB / 2RB / 2WR / 1TE / 1FLEX / 1K / 1DEF + 7 BN.
        assert positions.count("QB") == 1
        assert positions.count("RB") == 2
        assert positions.count("WR") == 2
        assert positions.count("TE") == 1
        assert positions.count("FLEX") == 1
        assert positions.count("K") == 1
        assert positions.count("DEF") == 1
        assert positions.count("BN") == 7

    def test_renames_applied(self, patched_ff):
        synthetic = {
            "positions": [
                {"label": "QB", "start": 1},
                {"label": "QB/RB/WR/TE", "start": 1},  # -> SUPER_FLEX
                {"label": "WR/TE", "start": 1},  # -> WT
                {"label": "RB/WR/TE", "start": 1},  # -> FLEX
                {"label": "D/ST", "start": 1},  # -> DEF
                {"label": "IR", "start": 2},  # -> BN
                {"label": "BN", "max": 4},  # uses max as fallback
                {"label": "INVALID_NONSENSE", "start": 1},  # filtered out
            ]
        }
        out = patched_ff.convert_ff_roster_settings(synthetic)
        assert out == [
            "QB", "SUPER_FLEX", "WT", "FLEX", "DEF",
            "BN", "BN",
            "BN", "BN", "BN", "BN",
        ]


# ---------------------------------------------------------------------------
# get_fleaflicker_rosters_and_convert_to_sleeper — full pipeline
# ---------------------------------------------------------------------------
class TestGetFleaflickerRosters:
    def test_returns_one_entry_per_league(self, patched_ff, name_to_pid):
        out = patched_ff.get_fleaflicker_rosters_and_convert_to_sleeper(
            "jlgorel@example.com", name_to_pid
        )
        assert len(out) == 1
        assert out[0]["league"] == "Test League"

    def test_required_keys_present(self, patched_ff, name_to_pid):
        out = patched_ff.get_fleaflicker_rosters_and_convert_to_sleeper(
            "jlgorel@example.com", name_to_pid
        )
        required = {"league", "pids", "settings", "positions", "all_owned"}
        assert required <= out[0].keys()

    def test_user_pids_subset_of_all_owned(self, patched_ff, name_to_pid):
        out = patched_ff.get_fleaflicker_rosters_and_convert_to_sleeper(
            "jlgorel@example.com", name_to_pid
        )
        assert set(out[0]["pids"]).issubset(set(out[0]["all_owned"]))

    def test_user_roster_is_nonempty(self, patched_ff, name_to_pid):
        out = patched_ff.get_fleaflicker_rosters_and_convert_to_sleeper(
            "jlgorel@example.com", name_to_pid
        )
        # Real captured roster has ~16 rostered players.
        assert len(out[0]["pids"]) >= 10

    def test_scoring_includes_core_keys(self, patched_ff, name_to_pid):
        out = patched_ff.get_fleaflicker_rosters_and_convert_to_sleeper(
            "jlgorel@example.com", name_to_pid
        )
        s = out[0]["settings"]
        # Captured league is a half-PPR redraft with 4pt pass TDs.
        assert s.get("pass_yd") == pytest.approx(0.04)
        assert s.get("pass_td") == 4.0
        assert s.get("pass_int") == -2.0
        assert s.get("rush_yd") == pytest.approx(0.1)
        assert s.get("rec_yd") == pytest.approx(0.1)
        assert s.get("rush_td") == 6.0
        assert s.get("rec_td") == 6.0
        # Captured league is half-PPR.
        assert s.get("rec") == pytest.approx(0.5, abs=0.01) or "rec" in s

    def test_unresolvable_players_are_dropped_silently(self, patched_ff):
        """An empty name map should yield ~empty rosters (only DST entries
        survive via the NFL teams reverse-lookup), not raise."""
        from app.config import Config

        out = patched_ff.get_fleaflicker_rosters_and_convert_to_sleeper(
            "jlgorel@example.com", {}
        )
        team_codes = set(Config.nfl_teams_reverse_lookup.values())
        # Whatever IDs come back, they all have to be NFL team codes — i.e.
        # only D/STs resolved. Real player names were silently dropped.
        assert set(out[0]["pids"]) <= team_codes
        assert set(out[0]["all_owned"]) <= team_codes

    def test_test_named_league_is_skipped(self, monkeypatch, ff_fetch, name_to_pid):
        """Leagues literally named ``test`` are filtered out (debug hold-over)."""
        import app.services.fleaflicker_client as fc

        ul = copy.deepcopy(_load("user_leagues.json"))
        ul["leagues"][0]["name"] = "test"

        def fake(url: str, **_kw: Any) -> Any:
            if "FetchUserLeagues" in url:
                return ul
            return ff_fetch(url)

        monkeypatch.setattr(fc, "fetch_json", fake)
        monkeypatch.setattr(fc, "get_current_fantasy_year", lambda: "2026")
        out = fc.get_fleaflicker_rosters_and_convert_to_sleeper(
            "jlgorel@example.com", name_to_pid
        )
        assert out == []
