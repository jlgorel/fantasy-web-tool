"""Tests for draft_help.rankings_source (pure parsing + read-side repo).

These also cover the extraction logic used by
``tools/build_draft_rankings.py`` (which is Excel-bound and not directly
testable in CI).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.services.draft_help import rankings_source as rs
from app.services.draft_help.rankings_source import (
    NameResolver,
    RankingPlayer,
    RankingsConfig,
    RankingsRepository,
    assign_overall_ranks,
    config_key,
    normalize_player_name,
    parse_config_key,
    parse_position_sheet,
    rankings_blob_name,
)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
FIXTURE_BLOBS = REPO_ROOT / "tests" / "fixtures" / "blobs"


# ---------------------------------------------------------------------------
# normalize_player_name
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "raw,expected",
    [
        ("D.J. Moore", "dj moore"),
        ("DJ Moore", "dj moore"),
        ("De'Von Achane", "devon achane"),
        ("Amon-Ra St. Brown", "amon ra st brown"),
        ("Marvin Harrison Jr.", "marvin harrison"),
        ("Kenneth Walker III", "kenneth walker"),
        ("  Patrick   Mahomes  ", "patrick mahomes"),
    ],
)
def test_normalize_player_name(raw, expected):
    assert normalize_player_name(raw) == expected


def test_normalize_applies_aliases():
    # Spreadsheet spellings that differ from Sleeper canonical names.
    assert normalize_player_name("Gabriel Davis") == "gabe davis"
    assert normalize_player_name("Mitch Trubisky") == "mitchell trubisky"
    assert normalize_player_name("Chigoziem Okonkwo") == "chig okonkwo"
    assert normalize_player_name("Bam Knight") == "zonovan knight"
    assert normalize_player_name("Hollywood Brown") == "marquise brown"
    assert normalize_player_name("Kenny Gainwell") == "kenneth gainwell"


def test_normalize_handles_none_and_empty():
    assert normalize_player_name(None) == ""
    assert normalize_player_name("") == ""


# ---------------------------------------------------------------------------
# NameResolver
# ---------------------------------------------------------------------------
@pytest.fixture
def resolver():
    players = {
        "1": {"full_name": "Josh Allen", "fantasy_positions": ["QB"]},
        "2": {"full_name": "Josh Allen", "fantasy_positions": ["LB"]},
        "3": {"full_name": "Christian McCaffrey", "fantasy_positions": ["RB"]},
        "4": {"full_name": "DJ Moore", "fantasy_positions": ["WR"]},
        "5": {"full_name": "Duplicate Player", "fantasy_positions": None},
        "6": {"full_name": "Player Invalid", "fantasy_positions": ["RB"]},
        "KC": {"fantasy_positions": ["DEF"]},  # no full_name
    }
    return NameResolver(players)


def test_resolver_prefers_position_match(resolver):
    # Two Josh Allens; the QB should win when position is supplied.
    assert resolver.resolve("Josh Allen", "QB") == "1"
    assert resolver.resolve("Josh Allen", "LB") == "2"


def test_resolver_unique_name_fallback(resolver):
    assert resolver.resolve("Christian McCaffrey") == "3"
    assert resolver.resolve("D.J. Moore", "WR") == "4"


def test_resolver_ambiguous_name_without_position_returns_none(resolver):
    # Two Josh Allens and no position -> ambiguous.
    assert resolver.resolve("Josh Allen") is None


def test_resolver_skips_placeholders_and_missing(resolver):
    assert resolver.resolve("Duplicate Player") is None
    assert resolver.resolve("Player Invalid", "RB") is None
    assert resolver.resolve("Unknown Guy", "WR") is None


# ---------------------------------------------------------------------------
# config_key
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "teams,ppr,sf,key",
    [
        (12, 0.5, True, "12|0.5|sf"),
        (12, 0.0, False, "12|0|1qb"),
        (8, 1.0, True, "8|1|sf"),
        (14, 1.0, False, "14|1|1qb"),
    ],
)
def test_config_key_roundtrip(teams, ppr, sf, key):
    assert config_key(teams, ppr, sf) == key
    assert parse_config_key(key) == (teams, ppr, sf)


def test_rankings_blob_name():
    assert rankings_blob_name(2024) == "draft_rankings_2024.json"
    assert rankings_blob_name("2025") == "draft_rankings_2025.json"


# ---------------------------------------------------------------------------
# parse_position_sheet
# ---------------------------------------------------------------------------
def _row(name, pos, bye=None, team=None, fpts=None, dollar=None, vbd=None, tier=None):
    """Build a 27-wide raw sheet row aligned to the COL_* indices."""
    row = [None] * 27
    row[rs.COL_PLAYER] = name
    row[rs.COL_POS] = pos
    row[rs.COL_BYE] = bye
    row[rs.COL_TEAM] = team
    row[rs.COL_FPTS] = fpts
    row[rs.COL_DOLLAR] = dollar
    row[rs.COL_VBD] = vbd
    row[rs.COL_TIER] = tier
    return row


def test_parse_position_sheet_basic_and_ranks(resolver):
    rows = [
        _row("Josh Allen", "QB", bye=12, team="BUF", fpts=360.5, dollar=46.2, vbd=144.7, tier="QB1"),
        _row("DJ Moore", "WR", bye=7, team="CHI", fpts=210.0, dollar=20.0, vbd=80.0, tier="WR2"),
    ]
    players, unmatched = parse_position_sheet("QB", rows, resolver)
    assert unmatched == []
    assert [p.player_id for p in players] == ["1", "4"]
    assert players[0].pos_rank == 1 and players[1].pos_rank == 2
    assert players[0].team == "BUF" and players[0].bye == 12
    assert players[0].auction == pytest.approx(46.2)
    assert players[0].tier == "QB1"


def test_parse_position_sheet_stops_at_blank(resolver):
    rows = [
        _row("Josh Allen", "QB", vbd=144.7),
        _row(None, None),  # blank player -> stop
        _row("Christian McCaffrey", "RB", vbd=180.0),  # should NOT be read
    ]
    players, _ = parse_position_sheet("QB", rows, resolver)
    assert [p.player_id for p in players] == ["1"]


def test_parse_position_sheet_collects_unmatched(resolver):
    rows = [
        _row("Nobody Here", "WR", vbd=50.0),
        _row("Christian McCaffrey", "RB", vbd=180.0),
    ]
    players, unmatched = parse_position_sheet("WR", rows, resolver)
    assert [p.player_id for p in players] == ["3"]
    assert unmatched == ["Nobody Here"]


# ---------------------------------------------------------------------------
# assign_overall_ranks
# ---------------------------------------------------------------------------
def test_assign_overall_ranks_by_vbd_desc():
    players = [
        RankingPlayer(player_id="a", name="A", pos="WR", vbd=50.0, pos_rank=1),
        RankingPlayer(player_id="b", name="B", pos="RB", vbd=120.0, pos_rank=1),
        RankingPlayer(player_id="c", name="C", pos="QB", vbd=None, pos_rank=1),
        RankingPlayer(player_id="d", name="D", pos="TE", vbd=90.0, pos_rank=1),
    ]
    ranked = assign_overall_ranks(players)
    assert [p.player_id for p in ranked] == ["b", "d", "a", "c"]
    assert [p.overall_rank for p in ranked] == [1, 2, 3, 4]
    # None-VBD player sorts last.
    assert ranked[-1].player_id == "c"


# ---------------------------------------------------------------------------
# RankingsConfig (de)serialization
# ---------------------------------------------------------------------------
def test_rankings_config_roundtrip():
    cfg = RankingsConfig(
        teams=12,
        ppr=0.5,
        superflex=True,
        budget=200,
        players=[RankingPlayer(player_id="1", name="Josh Allen", pos="QB", vbd=144.7, overall_rank=1)],
    )
    again = RankingsConfig.from_dict(cfg.to_dict())
    assert again.key == "12|0.5|sf"
    assert again.players[0].player_id == "1"
    assert again.by_player_id()["1"].vbd == pytest.approx(144.7)


# ---------------------------------------------------------------------------
# RankingsRepository
# ---------------------------------------------------------------------------
def _synthetic_blob():
    def cfg(teams, ppr, sf):
        return {
            "teams": teams,
            "ppr": ppr,
            "superflex": sf,
            "budget": 200,
            "players": [
                {"player_id": "1", "name": "Josh Allen", "pos": "QB", "vbd": 144.7,
                 "auction": 46.2, "overall_rank": 1, "pos_rank": 1, "tier": "QB1"},
            ],
        }
    return {
        "schema_version": 1,
        "year": "2024",
        "budget": 200,
        "configs": {
            config_key(12, 0.5, True): cfg(12, 0.5, True),
            config_key(12, 0.5, False): cfg(12, 0.5, False),
            config_key(10, 0.0, True): cfg(10, 0.0, True),
        },
    }


def test_repository_exact_lookup():
    repo = RankingsRepository(_synthetic_blob())
    assert repo.has_config(12, 0.5, True)
    cfg = repo.get_config(12, 0.5, True)
    assert cfg is not None and cfg.key == "12|0.5|sf"


def test_repository_fallback_prefers_matching_superflex_then_nearest_team():
    repo = RankingsRepository(_synthetic_blob())
    # 14-team 0.5 SF not present -> nearest by superflex match + team distance
    # should land on 12|0.5|sf (same sf, closest team) over 10|0|sf.
    cfg = repo.get_config(14, 0.5, True)
    assert cfg is not None and cfg.key == "12|0.5|sf"


def test_repository_fallback_can_be_disabled():
    repo = RankingsRepository(_synthetic_blob())
    assert repo.get_config(14, 0.5, True, fallback=False) is None


def test_repository_empty_returns_none():
    repo = RankingsRepository({"year": "2099", "configs": {}})
    assert repo.get_config(12, 0.5, True) is None


# ---------------------------------------------------------------------------
# Generated fixture integrity (guards the ingestion output shape)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("year", ["2022", "2023", "2024", "2025", "2026"])
def test_generated_fixture_is_wellformed(year):
    path = FIXTURE_BLOBS / rankings_blob_name(year)
    if not path.exists():
        pytest.skip(f"{path.name} not generated locally")
    blob = json.loads(path.read_text(encoding="utf-8"))
    assert blob["year"] == year
    repo = RankingsRepository(blob)
    # All 24 configs present.
    assert len(repo.config_keys) == 24
    cfg = repo.get_config(12, 0.5, True)
    assert cfg is not None
    assert len(cfg.players) > 100
    # overall_rank starts at 1 and the top player has the highest VBD.
    top = min(cfg.players, key=lambda p: p.overall_rank or 9999)
    assert top.overall_rank == 1
    assert top.vbd == max(p.vbd for p in cfg.players if p.vbd is not None)
    # Auction values present for premium players.
    assert any(p.auction for p in cfg.players[:20])
    if year == "2026":
        assert blob.get("provider") == "elboberto"
        assert blob.get("source_version")
        assert blob.get("unmatched_names") == {}
        one_qb = repo.get_config(12, 0.5, False, fallback=False)
        assert one_qb is not None
        by_name = {player.name: player for player in one_qb.players}
        # These are the workbook's AvgVBD cells (column Z), not an inverted
        # overall-rank sequence. The large gaps are intentional and important
        # to the recommender's cross-position utility scale.
        assert by_name["Jahmyr Gibbs"].vbd == pytest.approx(211.58)
        assert by_name["Bijan Robinson"].vbd == pytest.approx(206.96)
        assert by_name["Christian McCaffrey"].vbd == pytest.approx(170.12)
        assert by_name["Derrick Henry"].vbd == pytest.approx(138.56)
