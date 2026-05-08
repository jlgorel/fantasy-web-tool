"""Tests for ``app.services.wrapped.fantasy_calc``."""
from __future__ import annotations

from typing import Any, Dict

import pytest

import app.services.wrapped.fantasy_calc as fc


@pytest.fixture(autouse=True)
def _clear_cache():
    fc.clear_cache()
    yield
    fc.clear_cache()


def _payload():
    return [
        {"player": {"sleeperId": "P1", "name": "QB One"}, "value": 9000},
        {"player": {"sleeperId": "P2", "name": "RB Two"}, "value": 8500.5},
        {"player": {"sleeperId": None, "name": "Bad"}, "value": 100},  # skipped
        {"player": {"sleeperId": "P3"}, "value": None},                 # skipped
    ]


class TestGetPlayerValues:
    def test_parses_sleeper_id_and_value(self, monkeypatch):
        captured: Dict[str, str] = {}

        def fake(url: str, **_kw: Any) -> Any:
            captured["url"] = url
            return _payload()

        monkeypatch.setattr(fc, "fetch_json", fake)

        vals = fc.get_player_values(False, "1", "half_ppr")
        assert vals == {"P1": 9000.0, "P2": 8500.5}
        assert "isDynasty=false" in captured["url"]
        assert "numQbs=1" in captured["url"]
        assert "ppr=0.5" in captured["url"]

    def test_dynasty_2qb_ppr_query_string(self, monkeypatch):
        captured: Dict[str, str] = {}

        def fake(url: str, **_kw: Any) -> Any:
            captured["url"] = url
            return []

        monkeypatch.setattr(fc, "fetch_json", fake)
        fc.get_player_values(True, "2", "ppr")
        assert "isDynasty=true" in captured["url"]
        assert "numQbs=2" in captured["url"]
        assert "ppr=1" in captured["url"]

    def test_caches_per_param_combo(self, monkeypatch):
        call_count = {"n": 0}

        def fake(url: str, **_kw: Any) -> Any:
            call_count["n"] += 1
            return _payload()

        monkeypatch.setattr(fc, "fetch_json", fake)
        fc.get_player_values(False, "1", "half_ppr")
        fc.get_player_values(False, "1", "half_ppr")
        assert call_count["n"] == 1
        # Different combo -> new fetch
        fc.get_player_values(True, "1", "half_ppr")
        assert call_count["n"] == 2

    def test_network_error_returns_empty_and_caches(self, monkeypatch):
        call_count = {"n": 0}

        def fake(url: str, **_kw: Any) -> Any:
            call_count["n"] += 1
            raise RuntimeError("nope")

        monkeypatch.setattr(fc, "fetch_json", fake)
        assert fc.get_player_values(False, "1", "ppr") == {}
        # Cached negative result — no second call.
        assert fc.get_player_values(False, "1", "ppr") == {}
        assert call_count["n"] == 1

    def test_unexpected_payload_type_returns_empty(self, monkeypatch):
        monkeypatch.setattr(fc, "fetch_json", lambda url, **_kw: {"oops": True})
        assert fc.get_player_values(False, "1", "ppr") == {}

    def test_std_scoring_maps_to_ppr_zero(self, monkeypatch):
        captured: Dict[str, str] = {}

        def fake(url: str, **_kw: Any) -> Any:
            captured["url"] = url
            return []

        monkeypatch.setattr(fc, "fetch_json", fake)
        fc.get_player_values(False, "1", "std")
        assert "ppr=0" in captured["url"]
