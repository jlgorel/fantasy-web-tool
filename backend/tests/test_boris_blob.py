"""Tests for app.services.boris_chen and app.services.blob_store."""
from __future__ import annotations

import json

import pytest

from app.services.blob_store import (
    _fixture_enabled,
    _load_fixture,
    normalize_players_positions,
)
from app.services.boris_chen import (
    get_tier_page_names_from_league_settings,
    prepare_boris_chen_tier_dict,
)


class TestFixtureFlag:
    def test_fixture_enabled_true_when_set(self):
        # conftest.py forces USE_FIXTURE_BLOBS=1 for the whole session.
        assert _fixture_enabled() is True

    def test_load_fixture_returns_parsed_json(self):
        data = _load_fixture("runinfo.json")
        assert isinstance(data, dict)

    def test_load_fixture_missing_raises(self):
        with pytest.raises(FileNotFoundError):
            _load_fixture("definitely_not_a_blob.json")


class TestNormalizePlayers:
    def test_travis_hunter_forced_to_wr(self):
        players = {
            "999999": {
                "full_name": "Travis Hunter",
                "fantasy_positions": ["CB", "WR"],
            },
            "111111": {
                "full_name": "Some Other Guy",
                "fantasy_positions": ["WR"],
            },
        }
        normalize_players_positions(players)
        assert players["999999"]["fantasy_positions"][0] == "WR"
        assert players["111111"]["fantasy_positions"] == ["WR"]

    def test_handles_missing_full_name(self):
        # No-op, must not raise.
        normalize_players_positions({"x": {"fantasy_positions": ["WR"]}})


class TestTierPagePrefixes:
    @pytest.mark.parametrize(
        "rec,expected",
        [
            (0.0, ""),
            (0.5, "0.5 PPR "),
            (1.0, "PPR "),
        ],
    )
    def test_rb_wr_flex_prefix(self, rec, expected):
        prefix, _ = get_tier_page_names_from_league_settings({"rec": rec})
        assert prefix == expected

    def test_te_premium_rounds_to_full_ppr(self):
        # 0.5 base + 0.6 TE bonus = 1.1 effective — bucket as PPR for TE.
        _, te_prefix = get_tier_page_names_from_league_settings(
            {"rec": 0.5, "bonus_rec_te": 0.6}
        )
        assert te_prefix == "PPR "

    def test_te_premium_rounds_down_to_half_ppr(self):
        # 0.5 + 0.0 = 0.5 → halfppr bucket
        _, te_prefix = get_tier_page_names_from_league_settings(
            {"rec": 0.5, "bonus_rec_te": 0.0}
        )
        assert te_prefix == "0.5 PPR "


class TestBorisChenTierDict:
    def test_returns_dict_of_dicts(self):
        d = prepare_boris_chen_tier_dict()
        assert isinstance(d, dict)
        # Should contain at least a few real player names from the fixture.
        # If empty, the fixture is broken.
        assert len(d) > 0
        # Tier values are stringified ints.
        sample_player = next(iter(d))
        sample_tiers = d[sample_player]
        assert all(isinstance(t, str) for t in sample_tiers.values())
