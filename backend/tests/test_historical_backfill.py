"""Tests for the legacy season-scoring builder + ownership backfill in
``azure-functions/trade_eval/``.

Both modules are pure (HTTP injected), so we just feed canned payloads
through a stub and verify the output shape matches what the Flask
backend's Wrapped pipeline reads from blob storage.
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path
from typing import Any, Dict, List

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
AZURE_FN_DIR = REPO_ROOT / "azure-functions"


@pytest.fixture(scope="module")
def backfill_modules():
    saved_path = list(sys.path)
    saved_mods = {
        name: sys.modules.get(name)
        for name in (
            "config", "_fantasy_common", "trade_eval",
            "trade_eval.legacy_season_scoring",
            "trade_eval.ownership_history",
            "trade_eval.sleeper_scoring",
        )
    }
    for name in list(saved_mods):
        sys.modules.pop(name, None)
    sys.path.insert(0, str(AZURE_FN_DIR))
    try:
        legacy = importlib.import_module("trade_eval.legacy_season_scoring")
        ownership = importlib.import_module("trade_eval.ownership_history")
        yield {"legacy": legacy, "ownership": ownership}
    finally:
        sys.path[:] = saved_path
        for name, mod in saved_mods.items():
            if mod is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = mod


# ---------------------------------------------------------------------------
# legacy_season_scoring
# ---------------------------------------------------------------------------
def _fake_season_row(pid: str, pts: float, rank: int, *, pass_td: int = 0) -> Dict[str, Any]:
    return {
        "player_id": pid,
        "stats": {
            "pts_half_ppr": pts,
            "pts_ppr": pts + 1,
            "pts_std": pts - 1,
            "pos_rank_half_ppr": rank,
            "pos_rank_ppr": rank,
            "pos_rank_std": rank,
            "rec": 5,
            "pass_td": pass_td,
        },
    }


def _fake_week_row(pid: str, pts: float, *, pass_td: int = 0) -> Dict[str, Any]:
    return {
        "player_id": pid,
        "stats": {
            "pts_half_ppr": pts,
            "pts_ppr": pts + 0.5,
            "pts_std": pts - 0.5,
            "rec": 1,
            "pass_td": pass_td,
        },
    }


class FakeHttp:
    def __init__(self, routes: Dict[str, Any]):
        self.routes = routes
        self.calls: List[str] = []

    def __call__(self, url: str) -> Any:
        self.calls.append(url)
        # Match by substring so tests don't need to know full URL formatting.
        for key, payload in self.routes.items():
            if key in url:
                return payload
        return []


class TestLegacySeasonScoring:
    def test_builder_emits_expected_shape(self, backfill_modules):
        legacy = backfill_modules["legacy"]

        # Tiny route table -- one player per position, one week of data.
        routes = {
            # Season-level (one row per position is plenty).
            "stats/nfl/2023?season_type=regular&position=QB":  [_fake_season_row("qb1", 300, 1, pass_td=30)],
            "stats/nfl/2023?season_type=regular&position=RB":  [_fake_season_row("rb1", 250, 1)],
            "stats/nfl/2023?season_type=regular&position=WR":  [_fake_season_row("wr1", 220, 1)],
            "stats/nfl/2023?season_type=regular&position=TE":  [_fake_season_row("te1", 180, 1)],
            "stats/nfl/2023?season_type=regular&position=DEF": [_fake_season_row("def1", 120, 1)],
            "stats/nfl/2023?season_type=regular&position=K":   [_fake_season_row("k1", 140, 1)],
            # Weekly: only week 1 has rows; the rest return [].
            "stats/nfl/2023/1?season_type=regular&position=QB":  [_fake_week_row("qb1", 25, pass_td=3)],
            "stats/nfl/2023/1?season_type=regular&position=RB":  [_fake_week_row("rb1", 18)],
            "stats/nfl/2023/1?season_type=regular&position=WR":  [_fake_week_row("wr1", 14)],
            "stats/nfl/2023/1?season_type=regular&position=TE":  [_fake_week_row("te1", 10)],
            "stats/nfl/2023/1?season_type=regular&position=DEF": [_fake_week_row("def1", 8)],
            "stats/nfl/2023/1?season_type=regular&position=K":   [_fake_week_row("k1", 9)],
        }
        http = FakeHttp(routes)
        players_meta = {
            "qb1": {"full_name": "Test QB",  "fantasy_positions": ["QB"]},
            "rb1": {"full_name": "Test RB",  "fantasy_positions": ["RB"]},
            "wr1": {"full_name": "Test WR",  "fantasy_positions": ["WR"]},
            "te1": {"full_name": "Test TE",  "fantasy_positions": ["TE"]},
            "def1": {"full_name": "Test DEF", "fantasy_positions": ["DEF"]},
            "k1":  {"full_name": "Test K",   "fantasy_positions": ["K"]},
        }

        blob = legacy.build_legacy_season_scoring_blob(
            2023, players_meta, http_get_json=http
        )

        # All six players present, no internal scratch keys leaked.
        assert set(blob.keys()) == {"qb1", "rb1", "wr1", "te1", "def1", "k1"}
        for pid, entry in blob.items():
            assert set(entry.keys()) == {
                "full_name", "fantasy_positions",
                "scoring_data_weekly", "scoring_data_season",
            }
            assert entry["full_name"] is not None
            assert isinstance(entry["fantasy_positions"], list)

            season = entry["scoring_data_season"]
            for key in (
                "half_ppr_rank", "ppr_rank", "std_rank",
                "half_ppr_points", "ppr_points", "std_points",
                "receptions",
            ):
                assert key in season, f"{pid} missing season key {key}"

            weekly = entry["scoring_data_weekly"]
            # Week 1 should have data.
            assert 1 in weekly
            wk = weekly[1]
            for key in ("half_ppr", "ppr", "std", "receptions", "pass_td"):
                assert key in wk, f"{pid} week 1 missing {key}"

        # 6pt passing TD rank added only to QBs.
        assert "6pt_pass_td_rank" in blob["qb1"]["scoring_data_season"]
        assert "6pt_pass_td_points" in blob["qb1"]["scoring_data_season"]
        # std_points (299) + pass_td (30) * 2 = 359
        assert blob["qb1"]["scoring_data_season"]["6pt_pass_td_points"] == 359
        assert blob["qb1"]["scoring_data_season"]["6pt_pass_td_rank"] == 0
        assert "6pt_pass_td_rank" not in blob["rb1"]["scoring_data_season"]

        # Sanity: at least 7 (1 per pos season) + 78 (13 weeks x 6 pos) URLs
        # were hit. Don't pin exact because thread ordering is non-det.
        assert len(http.calls) >= 6 + (13 * 6)

    def test_missing_meta_player_still_included(self, backfill_modules):
        """Players who scored historically but aren't in the current
        /players/nfl snapshot should appear with full_name=None and
        fantasy_positions=[] -- accolades degrade gracefully."""
        legacy = backfill_modules["legacy"]
        routes = {
            "stats/nfl/2018?season_type=regular&position=QB": [_fake_season_row("ghost", 200, 1, pass_td=20)],
            "stats/nfl/2018?season_type=regular&position=RB": [],
            "stats/nfl/2018?season_type=regular&position=WR": [],
            "stats/nfl/2018?season_type=regular&position=TE": [],
            "stats/nfl/2018?season_type=regular&position=DEF": [],
            "stats/nfl/2018?season_type=regular&position=K": [],
        }
        http = FakeHttp(routes)
        blob = legacy.build_legacy_season_scoring_blob(2018, {}, http_get_json=http)
        assert "ghost" in blob
        assert blob["ghost"]["full_name"] is None
        assert blob["ghost"]["fantasy_positions"] == []
        # No 6pt rank since meta says it's not a QB.
        assert "6pt_pass_td_rank" not in blob["ghost"]["scoring_data_season"]


# ---------------------------------------------------------------------------
# ownership_history
# ---------------------------------------------------------------------------
class TestOwnershipHistory:
    def test_aggregates_weeks_into_pid_keyed_blob(self, backfill_modules):
        ownership = backfill_modules["ownership"]
        # Three weeks of synthetic ownership data; player p2 only shows
        # up in week 2 (mirrors a late-season add).
        routes_by_week = {
            1: {"p1": {"owned": 90.0, "started": 80.0}},
            2: {
                "p1": {"owned": 92.0, "started": 81.0},
                "p2": {"owned": 12.0, "started": 5.0},
            },
            3: {"p1": {"owned": 95.0, "started": 85.0}, "p2": {"owned": 25.0, "started": 9.0}},
        }

        def http(url: str) -> Any:
            # URL ends in .../{year}/{week}
            week = int(url.rstrip("/").rsplit("/", 1)[-1])
            return routes_by_week.get(week, {})

        blob = ownership.build_ownership_history(
            2023, http_get_json=http, weeks=[1, 2, 3]
        )

        # Shape: {pid: {week_str: {owned, started, ...}}}
        assert set(blob.keys()) == {"p1", "p2"}
        assert set(blob["p1"].keys()) == {"1", "2", "3"}
        assert set(blob["p2"].keys()) == {"2", "3"}
        assert blob["p1"]["1"]["owned"] == 90.0
        assert blob["p2"]["2"]["started"] == 5.0

    def test_skips_bad_week_payloads(self, backfill_modules):
        ownership = backfill_modules["ownership"]

        def http(url: str) -> Any:
            week = int(url.rstrip("/").rsplit("/", 1)[-1])
            if week == 5:
                return None  # bad payload -> should be skipped
            if week == 6:
                raise RuntimeError("boom")  # exception -> swallowed
            return {"p1": {"owned": 50.0, "started": 25.0}}

        blob = ownership.build_ownership_history(
            2023, http_get_json=http, weeks=[4, 5, 6, 7]
        )
        # p1 only appears in the two good weeks (4 and 7).
        assert set(blob["p1"].keys()) == {"4", "7"}
