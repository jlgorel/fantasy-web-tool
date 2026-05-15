"""Tests for the trade-evaluator scraper modules in
``azure-functions/trade_eval/``.

These modules live outside the backend package and (like
``test_scraper_draftkings.py``) we have to swap ``sys.path`` to import them
without colliding with the backend's ``config`` module.

All HTTP and blob IO is injected, so these tests stay pure: no live network,
no Azure, no Playwright.
"""
from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
AZURE_FN_DIR = REPO_ROOT / "azure-functions"


# ---------------------------------------------------------------------------
# Module loader (mirrors test_scraper_draftkings.py pattern)
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def trade_eval_modules():
    saved_path = list(sys.path)
    saved_mods = {
        name: sys.modules.get(name)
        for name in (
            "config", "_fantasy_common", "trade_eval",
            "trade_eval.blob_layout",
            "trade_eval.sleeper_scoring",
            "trade_eval.fantasycalc_values",
            "trade_eval.ktc_scraper",
            "trade_eval.ktc_top500_daily",
        )
    }
    for name in list(saved_mods):
        sys.modules.pop(name, None)
    sys.path.insert(0, str(AZURE_FN_DIR))
    try:
        blob_layout = importlib.import_module("trade_eval.blob_layout")
        sleeper_scoring = importlib.import_module("trade_eval.sleeper_scoring")
        fantasycalc_values = importlib.import_module("trade_eval.fantasycalc_values")
        ktc_scraper = importlib.import_module("trade_eval.ktc_scraper")
        ktc_top500_daily = importlib.import_module("trade_eval.ktc_top500_daily")
        yield {
            "blob_layout": blob_layout,
            "sleeper_scoring": sleeper_scoring,
            "fantasycalc_values": fantasycalc_values,
            "ktc_scraper": ktc_scraper,
            "ktc_top500_daily": ktc_top500_daily,
        }
    finally:
        sys.path[:] = saved_path
        for name, mod in saved_mods.items():
            if mod is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = mod


# ---------------------------------------------------------------------------
# Tiny in-memory blob backend for the driver tests
# ---------------------------------------------------------------------------
class FakeBlobStore:
    def __init__(self) -> None:
        self.store: Dict[str, Any] = {}

    def upload(self, data: Any, blob_name: str) -> None:
        # Round-trip through JSON to mimic real serialization quirks.
        self.store[blob_name] = json.loads(json.dumps(data))

    def load(self, blob_name: str) -> Any:
        v = self.store.get(blob_name)
        # Return a deep copy so callers can mutate freely.
        return json.loads(json.dumps(v)) if v is not None else None


# ===========================================================================
# blob_layout
# ===========================================================================
class TestBlobLayout:
    def test_paths_are_unique_and_well_formed(self, trade_eval_modules):
        bl = trade_eval_modules["blob_layout"]
        paths = {
            bl.scoring_summary_blob(2024),
            bl.scoring_raw_blob(2024, 5),
            bl.scoring_index_blob(),
            bl.fantasycalc_snapshot_blob("1qb", "2025-09-01"),
            bl.fantasycalc_index_blob(),
            bl.ktc_snapshot_blob("superflex", "2025-09-01"),
            bl.ktc_index_blob(),
        }
        # All distinct.
        assert len(paths) == 7
        # All under the trade_eval prefix so they're easy to identify in
        # the container.
        assert all(p.startswith(bl.TRADE_EVAL_PREFIX + "/") for p in paths)

    def test_format_constants(self, trade_eval_modules):
        bl = trade_eval_modules["blob_layout"]
        assert ("1qb", 1) in bl.FANTASYCALC_FORMATS
        assert ("superflex", 2) in bl.FANTASYCALC_FORMATS
        assert ("1qb", 1) in bl.KTC_FORMATS
        assert ("superflex", 2) in bl.KTC_FORMATS


# ===========================================================================
# sleeper_scoring -- pure aggregation
# ===========================================================================
def _row(pid: str, *, half=0.0, ppr=0.0, std=0.0, gp=1.0, rank=None,
         position="WR", team="GB") -> Dict[str, Any]:
    """Build a minimal Sleeper stats row matching the real shape."""
    stats: Dict[str, Any] = {
        "pts_half_ppr": half,
        "pts_ppr": ppr,
        "pts_std": std,
        "gp": gp,
    }
    if rank is not None:
        stats["pos_rank_half_ppr"] = rank
    return {
        "player_id": pid,
        "stats": stats,
        "player": {"position": position},
        "team": team,
    }


class TestSleeperAggregation:
    def test_aggregate_week_indexes_by_pid(self, trade_eval_modules):
        ss = trade_eval_modules["sleeper_scoring"]
        rows = [_row("100", half=12.4), _row("200", half=8.1)]
        out = ss.aggregate_week(rows)
        assert set(out.keys()) == {"100", "200"}
        assert out["100"]["stats"]["pts_half_ppr"] == 12.4

    def test_aggregate_week_skips_rows_missing_pid(self, trade_eval_modules):
        ss = trade_eval_modules["sleeper_scoring"]
        rows = [{"stats": {"pts_half_ppr": 5.0}}, _row("100", half=12.4)]
        out = ss.aggregate_week(rows)
        assert list(out.keys()) == ["100"]

    def test_merge_and_finalize_basic_ppg(self, trade_eval_modules):
        ss = trade_eval_modules["sleeper_scoring"]
        summary: Dict[str, Any] = {}
        ss.merge_week_into_summary(summary, 1, ss.aggregate_week([
            _row("100", half=10.0, ppr=12.0, std=8.0, rank=15),
        ]))
        ss.merge_week_into_summary(summary, 2, ss.aggregate_week([
            _row("100", half=20.0, ppr=22.0, std=18.0, rank=5),
        ]))
        ss.finalize_summary(summary)
        b = summary["100"]
        assert b["games_played"] == 2
        assert b["total_pts"]["half_ppr"] == pytest.approx(30.0)
        assert b["ppg"]["half_ppr"] == pytest.approx(15.0)
        assert b["ppg"]["ppr"] == pytest.approx(17.0)
        assert b["ppg"]["std"] == pytest.approx(13.0)
        assert b["weekly_rank_half_ppr"] == {"1": 15, "2": 5}
        # Internal scratch flag must be stripped.
        assert "_played" not in b["weekly_pts"]["1"]

    def test_merge_is_idempotent_on_replay(self, trade_eval_modules):
        """Re-merging the same week must not double-count."""
        ss = trade_eval_modules["sleeper_scoring"]
        summary: Dict[str, Any] = {}
        week = ss.aggregate_week([_row("100", half=10.0, gp=1.0)])
        ss.merge_week_into_summary(summary, 1, week)
        ss.merge_week_into_summary(summary, 1, week)
        ss.finalize_summary(summary)
        b = summary["100"]
        assert b["games_played"] == 1
        assert b["total_pts"]["half_ppr"] == pytest.approx(10.0)

    def test_merge_replays_with_updated_value_overwrites(self, trade_eval_modules):
        """If a stat-correction comes through, latest value wins."""
        ss = trade_eval_modules["sleeper_scoring"]
        summary: Dict[str, Any] = {}
        ss.merge_week_into_summary(summary, 1, ss.aggregate_week([
            _row("100", half=10.0, gp=1.0),
        ]))
        ss.merge_week_into_summary(summary, 1, ss.aggregate_week([
            _row("100", half=14.0, gp=1.0),
        ]))
        ss.finalize_summary(summary)
        assert summary["100"]["total_pts"]["half_ppr"] == pytest.approx(14.0)
        assert summary["100"]["games_played"] == 1

    def test_did_not_play_does_not_count_as_game(self, trade_eval_modules):
        ss = trade_eval_modules["sleeper_scoring"]
        summary: Dict[str, Any] = {}
        ss.merge_week_into_summary(summary, 1, ss.aggregate_week([
            _row("100", half=0.0, gp=0.0),  # bye / inactive
        ]))
        ss.merge_week_into_summary(summary, 2, ss.aggregate_week([
            _row("100", half=10.0, gp=1.0),
        ]))
        ss.finalize_summary(summary)
        b = summary["100"]
        assert b["games_played"] == 1
        assert b["ppg"]["half_ppr"] == pytest.approx(10.0)

    def test_position_team_metadata_captured(self, trade_eval_modules):
        ss = trade_eval_modules["sleeper_scoring"]
        summary: Dict[str, Any] = {}
        ss.merge_week_into_summary(summary, 1, ss.aggregate_week([
            _row("100", half=5.0, position="RB", team="GB"),
        ]))
        # Mid-season trade: latest team wins.
        ss.merge_week_into_summary(summary, 5, ss.aggregate_week([
            _row("100", half=5.0, position="RB", team="LAR"),
        ]))
        b = summary["100"]
        assert b["position"] == "RB"
        assert b["team"] == "LAR"

    def test_regular_season_weeks_18_or_17(self, trade_eval_modules):
        ss = trade_eval_modules["sleeper_scoring"]
        assert list(ss.regular_season_weeks(2024)) == list(range(1, 19))
        assert list(ss.regular_season_weeks(2020)) == list(range(1, 18))

    def test_safe_float_handles_garbage(self, trade_eval_modules):
        ss = trade_eval_modules["sleeper_scoring"]
        # Through the public path: stats with non-numeric values must not crash.
        summary: Dict[str, Any] = {}
        rows = [{
            "player_id": "100",
            "stats": {"pts_half_ppr": "junk", "gp": None},
            "player": {"position": "WR"},
        }]
        ss.merge_week_into_summary(summary, 1, ss.aggregate_week(rows))
        ss.finalize_summary(summary)
        assert summary["100"]["total_pts"]["half_ppr"] == 0.0
        assert summary["100"]["games_played"] == 0


class TestSleeperDrivers:
    def test_build_season_writes_per_week_and_summary(self, trade_eval_modules):
        ss = trade_eval_modules["sleeper_scoring"]
        # Fake HTTP that returns one row per week.
        def http(url: str):
            # Pull week from the URL.
            week = int(url.rsplit("/", 1)[-1].split("?")[0])
            return [_row("100", half=10.0 * week)]

        summary, raw_by_week = ss.build_season(
            2024, http_get_json=http, weeks=[1, 2, 3], max_workers=2,
        )
        assert set(raw_by_week.keys()) == {1, 2, 3}
        assert summary["100"]["games_played"] == 3
        assert summary["100"]["total_pts"]["half_ppr"] == pytest.approx(60.0)

    def test_update_current_week_uploads_and_merges(self, trade_eval_modules):
        ss = trade_eval_modules["sleeper_scoring"]
        bl = trade_eval_modules["blob_layout"]
        store = FakeBlobStore()

        # Pre-existing summary so we can verify a merge (not overwrite).
        store.store[bl.scoring_summary_blob(2024)] = {
            "100": {
                "position": "WR", "team": "GB",
                "weekly_pts": {"1": {"std": 5.0, "half_ppr": 6.0, "ppr": 7.0}},
                "weekly_rank_half_ppr": {"1": 30},
                "games_played": 1,
                "total_pts": {"std": 5.0, "half_ppr": 6.0, "ppr": 7.0},
                "ppg": {"std": 5.0, "half_ppr": 6.0, "ppr": 7.0},
            }
        }

        def http(url: str):
            return [_row("100", half=10.0, ppr=11.0, std=9.0, rank=10)]

        updated = ss.update_current_week(
            2024, 2,
            http_get_json=http,
            blob_load=store.load, blob_upload=store.upload,
        )
        assert updated is True

        # Per-week raw blob written.
        assert bl.scoring_raw_blob(2024, 2) in store.store
        # Summary merged, week 1 untouched.
        merged = store.store[bl.scoring_summary_blob(2024)]
        assert merged["100"]["games_played"] == 2
        assert merged["100"]["total_pts"]["half_ppr"] == pytest.approx(16.0)
        assert "1" in merged["100"]["weekly_pts"]
        assert "2" in merged["100"]["weekly_pts"]
        # Index updated.
        idx = store.store[bl.scoring_index_blob()]
        assert 2 in idx["seasons"]["2024"]["weeks_present"]

    def test_update_current_week_returns_false_on_empty(self, trade_eval_modules):
        ss = trade_eval_modules["sleeper_scoring"]
        store = FakeBlobStore()
        updated = ss.update_current_week(
            2024, 5,
            http_get_json=lambda url: [],
            blob_load=store.load, blob_upload=store.upload,
        )
        assert updated is False
        assert store.store == {}  # nothing written


# ===========================================================================
# fantasycalc_values
# ===========================================================================
class TestFantasyCalcParse:
    def test_parses_sleeper_keyed_rows(self, trade_eval_modules):
        fc = trade_eval_modules["fantasycalc_values"]
        payload = [
            {
                "player": {
                    "name": "Justin Jefferson", "position": "WR",
                    "maybeTeam": "MIN", "sleeperId": "6794", "maybeAge": 26,
                },
                "value": 9999, "overallRank": 1, "positionRank": 1,
                "trend30Day": 5,
            },
            {
                # missing sleeperId -> skipped
                "player": {"name": "Some Rookie", "position": "RB"},
                "value": 4500,
            },
        ]
        out = fc.parse_payload(payload)
        assert set(out.keys()) == {"6794"}
        rec = out["6794"]
        assert rec["name"] == "Justin Jefferson"
        assert rec["value"] == 9999
        assert rec["overallRank"] == 1
        assert rec["maybeTeam"] == "MIN"

    def test_parses_returns_empty_on_bad_shape(self, trade_eval_modules):
        fc = trade_eval_modules["fantasycalc_values"]
        assert fc.parse_payload({"not": "a list"}) == {}
        assert fc.parse_payload(None) == {}

    def test_url_includes_format_params(self, trade_eval_modules):
        fc = trade_eval_modules["fantasycalc_values"]
        url = fc.values_url(2)
        assert "isDynasty=true" in url
        assert "numQbs=2" in url
        assert "ppr=0.5" in url


class TestFantasyCalcDriver:
    def test_snapshot_all_writes_per_format_and_index(self, trade_eval_modules):
        fc = trade_eval_modules["fantasycalc_values"]
        bl = trade_eval_modules["blob_layout"]
        store = FakeBlobStore()

        sample_payload = [{
            "player": {"name": "X", "position": "WR", "sleeperId": "1"},
            "value": 1000,
        }]
        counts = fc.snapshot_all(
            http_get_json=lambda url: sample_payload,
            blob_upload=store.upload, blob_load=store.load,
            date_iso="2025-09-01",
        )
        assert counts == {"1qb": 1, "superflex": 1}
        assert bl.fantasycalc_snapshot_blob("1qb", "2025-09-01") in store.store
        assert bl.fantasycalc_snapshot_blob("superflex", "2025-09-01") in store.store
        idx = store.store[bl.fantasycalc_index_blob()]
        assert idx["snapshots"]["2025-09-01"] == {"1qb": 1, "superflex": 1}

    def test_snapshot_format_skips_blob_on_empty(self, trade_eval_modules):
        fc = trade_eval_modules["fantasycalc_values"]
        bl = trade_eval_modules["blob_layout"]
        store = FakeBlobStore()
        # Pre-populate so we can verify it isn't clobbered.
        store.store[bl.fantasycalc_snapshot_blob("1qb", "2025-09-01")] = {"existing": True}
        result = fc.snapshot_format(
            "1qb", 1,
            http_get_json=lambda url: [],
            blob_upload=store.upload,
            date_iso="2025-09-01",
        )
        assert result == {}
        assert store.store[bl.fantasycalc_snapshot_blob("1qb", "2025-09-01")] == {"existing": True}


# ===========================================================================
# ktc_scraper
# ===========================================================================
KTC_SAMPLE_PAGE = """
<html><head></head><body>
<script>
var someUnrelatedThing = {};
var playersArray = [
  {
    "playerName": "Justin Jefferson",
    "position": "WR",
    "team": "MIN",
    "age": 26.1,
    "playerID": 547,
    "sleeperPlayerID": "6794",
    "oneQBValues":   {"value": 9999, "rank": 1, "positionalRank": 1},
    "superflexValues": {"value": 9876, "rank": 3, "positionalRank": 1}
  },
  {
    "playerName": "Patrick Mahomes",
    "position": "QB",
    "team": "KC",
    "age": 30.0,
    "playerID": 100,
    "oneQBValues":   {"value": 8000, "rank": 8, "positionalRank": 1},
    "superflexValues": {"value": 11500, "rank": 1, "positionalRank": 1}
  },
  {
    "playerName": "2025 Mid 1st",
    "position": "RDP",
    "team": null,
    "age": null,
    "playerID": 9001,
    "oneQBValues":   {"value": 4500, "rank": 80, "positionalRank": 5},
    "superflexValues": {"value": 5200, "rank": 70, "positionalRank": 5}
  }
];
var trailing = 1;
</script>
</body></html>
"""


class TestKtcParse:
    def test_extract_players_array(self, trade_eval_modules):
        ktc = trade_eval_modules["ktc_scraper"]
        arr = ktc.extract_players_array(KTC_SAMPLE_PAGE)
        assert isinstance(arr, list) and len(arr) == 3
        assert arr[0]["playerName"] == "Justin Jefferson"

    def test_extract_raises_when_not_found(self, trade_eval_modules):
        ktc = trade_eval_modules["ktc_scraper"]
        with pytest.raises(ValueError):
            ktc.extract_players_array("<html>no array here</html>")

    def test_parse_html_1qb(self, trade_eval_modules):
        ktc = trade_eval_modules["ktc_scraper"]
        out = ktc.parse_html(KTC_SAMPLE_PAGE, format_id=1)
        assert set(out.keys()) == {"547", "100", "9001"}
        jj = out["547"]
        assert jj["value"] == 9999
        assert jj["overall_rank"] == 1
        assert jj["position_rank"] == 1
        assert jj["sleeper_id"] == "6794"
        assert jj["is_pick"] is False
        # Mahomes 1QB value is 8000, not the SF 11500.
        assert out["100"]["value"] == 8000

    def test_parse_html_superflex(self, trade_eval_modules):
        ktc = trade_eval_modules["ktc_scraper"]
        out = ktc.parse_html(KTC_SAMPLE_PAGE, format_id=2)
        assert out["100"]["value"] == 11500
        assert out["547"]["value"] == 9876

    def test_picks_flagged(self, trade_eval_modules):
        ktc = trade_eval_modules["ktc_scraper"]
        out = ktc.parse_html(KTC_SAMPLE_PAGE, format_id=1)
        assert out["9001"]["is_pick"] is True
        assert out["547"]["is_pick"] is False


class TestKtcDriver:
    def test_snapshot_format_uploads_when_above_threshold(self, trade_eval_modules):
        ktc = trade_eval_modules["ktc_scraper"]
        bl = trade_eval_modules["blob_layout"]
        store = FakeBlobStore()
        out = ktc.snapshot_format(
            "1qb", 1,
            fetch_page=lambda url: KTC_SAMPLE_PAGE,
            blob_upload=store.upload,
            date_iso="2025-09-01",
            min_acceptable_rows=1,  # tiny fixture
        )
        assert len(out) == 3
        assert bl.ktc_snapshot_blob("1qb", "2025-09-01") in store.store

    def test_snapshot_format_skips_blob_when_below_threshold(self, trade_eval_modules):
        """Sanity guard: tiny scrape result must not clobber a real snapshot."""
        ktc = trade_eval_modules["ktc_scraper"]
        bl = trade_eval_modules["blob_layout"]
        store = FakeBlobStore()
        store.store[bl.ktc_snapshot_blob("1qb", "2025-09-01")] = {"existing": True}
        out = ktc.snapshot_format(
            "1qb", 1,
            fetch_page=lambda url: KTC_SAMPLE_PAGE,
            blob_upload=store.upload,
            date_iso="2025-09-01",
            min_acceptable_rows=100,  # fixture has 3, should fail guard
        )
        assert len(out) == 3  # parsed successfully...
        # ...but blob was NOT overwritten:
        assert store.store[bl.ktc_snapshot_blob("1qb", "2025-09-01")] == {"existing": True}

    def test_snapshot_all_writes_index(self, trade_eval_modules):
        ktc = trade_eval_modules["ktc_scraper"]
        bl = trade_eval_modules["blob_layout"]
        store = FakeBlobStore()
        counts = ktc.snapshot_all(
            fetch_page=lambda url: KTC_SAMPLE_PAGE,
            blob_upload=store.upload, blob_load=store.load,
            date_iso="2025-09-01",
        )
        # Both formats parse the fixture (3 rows each). Default threshold
        # (100) blocks the snapshot blob upload, but the index is still
        # written and counts reflect the parsed-row count.
        assert counts == {"1qb": 3, "superflex": 3}
        # Snapshot blobs themselves were NOT uploaded (below threshold)...
        assert bl.ktc_snapshot_blob("1qb", "2025-09-01") not in store.store
        assert bl.ktc_snapshot_blob("superflex", "2025-09-01") not in store.store
        # ...but the index was, recording what we attempted.
        idx = store.store[bl.ktc_index_blob()]
        assert idx["snapshots"]["2025-09-01"] == {"1qb": 3, "superflex": 3}


# ===========================================================================
# ktc_top500_daily -- rolling appender to historical_KTC_rankings.json
# ===========================================================================
class TestKtcPickKeyFromName:
    @pytest.mark.parametrize("name, expected", [
        ("2026 Mid 1st",   "pick:2026_mid_1st"),
        ("2027 Early 2nd", "pick:2027_early_2nd"),
        ("2028 Late 4th",  "pick:2028_late_4th"),
        ("  2026  EARLY  1ST  ", "pick:2026_early_1st"),
    ])
    def test_parses_canonical_form(self, trade_eval_modules, name, expected):
        mod = trade_eval_modules["ktc_top500_daily"]
        assert mod.pick_key_from_name(name) == expected

    @pytest.mark.parametrize("name", [
        "", "Justin Jefferson", "2026 1st", "Mid 1st", "2026 Mid 5th",
    ])
    def test_returns_none_on_non_pick(self, trade_eval_modules, name):
        mod = trade_eval_modules["ktc_top500_daily"]
        assert mod.pick_key_from_name(name) is None


class TestKtcAppendDaily:
    """Exercises ``ktc_top500_daily.append_daily`` end-to-end with a fake
    blob store + injected page HTML."""

    def _seeded_store(self, trade_eval_modules) -> "FakeBlobStore":
        """Pre-populate the historical blob with one known player, one known
        pick, and one CSV-only retired vet."""
        bl = trade_eval_modules["blob_layout"]
        store = FakeBlobStore()
        store.store[bl.ktc_historical_blob()] = {
            "n_records": 3,
            "records": {
                # Known player with prior history; matches KTC_SAMPLE_PAGE.
                "6794": {
                    "name": "Justin Jefferson", "position": "WR", "team": "MIN",
                    "ktc_player_id": 547, "ktc_slug": "justin-jefferson-547",
                    "sleeper_id": "6794", "is_pick": False,
                    "fantasy_positions": ["WR"],
                    "1QB_Historical": {"2025-08-31": 9000},
                    "SF_Historical":  {"2025-08-31": 8800},
                },
                # Known pick matching the fixture's "2025 Mid 1st".
                "pick:2025_mid_1st": {
                    "label": "2025 Mid 1st",
                    "ktc_player_id": 9001, "ktc_slug": "2025-mid-1st-9001",
                    "is_pick": True,
                    "1QB_Historical": {"2025-08-31": 4400},
                    "SF_Historical":  {"2025-08-31": 5100},
                },
                # CSV-only retired vet: NO ktc_player_id -> should be left
                # alone (not zero-filled) when absent from today's scrape.
                "232": {
                    "name": "Frank Gore", "position": "RB", "team": None,
                    "ktc_player_id": None, "ktc_slug": None,
                    "sleeper_id": "232", "is_pick": False,
                    "fantasy_positions": ["RB"],
                    "1QB_Historical": {"2022-01-01": 500},
                    "SF_Historical":  {"2022-01-01": 450},
                },
            },
        }
        return store

    def test_appends_today_for_known_player_and_pick(self, trade_eval_modules):
        mod = trade_eval_modules["ktc_top500_daily"]
        bl = trade_eval_modules["blob_layout"]
        store = self._seeded_store(trade_eval_modules)

        result = mod.append_daily(
            fetch_page=lambda url: KTC_SAMPLE_PAGE,
            blob_upload=store.upload,
            blob_load=store.load,
            date_iso="2026-05-14",
            min_acceptable_rows=1,  # tiny fixture
        )

        assert result["status"] == "ok"
        assert result["date"] == "2026-05-14"
        assert result["scraped"] == {"1qb": 3, "superflex": 3}

        blob = store.store[bl.ktc_historical_blob()]
        jj = blob["records"]["6794"]
        assert jj["1QB_Historical"]["2026-05-14"] == 9999
        assert jj["SF_Historical"]["2026-05-14"] == 9876
        # Prior day untouched.
        assert jj["1QB_Historical"]["2025-08-31"] == 9000

        pick = blob["records"]["pick:2025_mid_1st"]
        assert pick["1QB_Historical"]["2026-05-14"] == 4500
        assert pick["SF_Historical"]["2026-05-14"] == 5200

    def test_creates_new_entrant_with_resolver(self, trade_eval_modules):
        """Mahomes (sleeperPlayerID not in fixture) should be added as a new
        entrant with the resolver-supplied sleeper_id."""
        mod = trade_eval_modules["ktc_top500_daily"]
        bl = trade_eval_modules["blob_layout"]
        store = self._seeded_store(trade_eval_modules)

        resolver_calls: List[str] = []

        def resolver(name: str):
            resolver_calls.append(name)
            return "4046" if name == "Patrick Mahomes" else None

        result = mod.append_daily(
            fetch_page=lambda url: KTC_SAMPLE_PAGE,
            blob_upload=store.upload,
            blob_load=store.load,
            name_resolver=resolver,
            date_iso="2026-05-14",
            min_acceptable_rows=1,
        )

        assert result["new_entrants"] == 1
        assert "Patrick Mahomes" in resolver_calls

        blob = store.store[bl.ktc_historical_blob()]
        mahomes = blob["records"]["4046"]
        assert mahomes["name"] == "Patrick Mahomes"
        assert mahomes["ktc_player_id"] == 100
        assert mahomes["sleeper_id"] == "4046"
        assert mahomes["1QB_Historical"] == {"2026-05-14": 8000}
        assert mahomes["SF_Historical"] == {"2026-05-14": 11500}

    def test_new_entrant_without_resolver_falls_back_to_ktc_key(self, trade_eval_modules):
        mod = trade_eval_modules["ktc_top500_daily"]
        bl = trade_eval_modules["blob_layout"]
        store = self._seeded_store(trade_eval_modules)

        mod.append_daily(
            fetch_page=lambda url: KTC_SAMPLE_PAGE,
            blob_upload=store.upload,
            blob_load=store.load,
            date_iso="2026-05-14",
            min_acceptable_rows=1,
        )

        blob = store.store[bl.ktc_historical_blob()]
        # Mahomes had no sleeperPlayerID on the fixture and no resolver given,
        # so he keys off the ktc id.
        assert "ktc:100" in blob["records"]
        assert blob["records"]["ktc:100"]["sleeper_id"] is None

    def test_zero_fills_known_record_absent_from_scrape(self, trade_eval_modules):
        """If a previously-scraped record is missing today, zero-fill the
        date so the time-series stays dense."""
        mod = trade_eval_modules["ktc_top500_daily"]
        bl = trade_eval_modules["blob_layout"]
        store = FakeBlobStore()
        store.store[bl.ktc_historical_blob()] = {
            "records": {
                # Player WITH ktc_player_id but NOT in KTC_SAMPLE_PAGE
                # (he dropped out of top-500).
                "9999": {
                    "name": "Dropped Vet", "position": "RB",
                    "ktc_player_id": 12345, "sleeper_id": "9999",
                    "is_pick": False,
                    "1QB_Historical": {"2025-08-31": 5000},
                    "SF_Historical":  {"2025-08-31": 4900},
                },
                # CSV-only entry (no ktc_player_id) -- must NOT be zero-filled.
                "232": {
                    "name": "Frank Gore", "ktc_player_id": None,
                    "sleeper_id": "232", "is_pick": False,
                    "1QB_Historical": {"2022-01-01": 500},
                    "SF_Historical":  {"2022-01-01": 450},
                },
            },
        }

        result = mod.append_daily(
            fetch_page=lambda url: KTC_SAMPLE_PAGE,
            blob_upload=store.upload,
            blob_load=store.load,
            date_iso="2026-05-14",
            min_acceptable_rows=1,
        )

        blob = store.store[bl.ktc_historical_blob()]
        assert result["zero_filled"] == 1

        dropped = blob["records"]["9999"]
        assert dropped["1QB_Historical"]["2026-05-14"] == 0
        assert dropped["SF_Historical"]["2026-05-14"] == 0

        # CSV-only vet untouched.
        gore = blob["records"]["232"]
        assert "2026-05-14" not in gore["1QB_Historical"]
        assert "2026-05-14" not in gore["SF_Historical"]

    def test_low_row_count_aborts_without_writing(self, trade_eval_modules):
        """Sanity guard: a too-small scrape must not corrupt the blob."""
        mod = trade_eval_modules["ktc_top500_daily"]
        bl = trade_eval_modules["blob_layout"]
        store = self._seeded_store(trade_eval_modules)
        # Capture the pristine blob state for comparison.
        before = store.load(bl.ktc_historical_blob())

        result = mod.append_daily(
            fetch_page=lambda url: KTC_SAMPLE_PAGE,
            blob_upload=store.upload,
            blob_load=store.load,
            date_iso="2026-05-14",
            min_acceptable_rows=400,  # fixture only has 3 rows
        )

        assert result["status"] == "skipped_low_rows"
        # Blob unchanged.
        assert store.load(bl.ktc_historical_blob()) == before

    def test_empty_blob_bootstraps_from_scrape(self, trade_eval_modules):
        """If the rolling blob doesn't exist yet, ``append_daily`` should
        bootstrap a fresh one from today's scrape alone."""
        mod = trade_eval_modules["ktc_top500_daily"]
        bl = trade_eval_modules["blob_layout"]
        store = FakeBlobStore()  # empty

        result = mod.append_daily(
            fetch_page=lambda url: KTC_SAMPLE_PAGE,
            blob_upload=store.upload,
            blob_load=store.load,
            date_iso="2026-05-14",
            min_acceptable_rows=1,
        )

        assert result["status"] == "ok"
        assert result["new_entrants"] == 3
        assert result["zero_filled"] == 0
        blob = store.store[bl.ktc_historical_blob()]
        assert blob["n_records"] == 3
        # Jefferson keyed by his sleeperPlayerID from the fixture.
        assert "6794" in blob["records"]
        assert blob["records"]["6794"]["1QB_Historical"] == {"2026-05-14": 9999}
        # Pick keyed canonically.
        assert "pick:2025_mid_1st" in blob["records"]

    def test_repeat_append_is_idempotent_for_same_date(self, trade_eval_modules):
        """Running twice for the same date should produce identical state
        (today's value just gets re-stamped)."""
        mod = trade_eval_modules["ktc_top500_daily"]
        bl = trade_eval_modules["blob_layout"]
        store = self._seeded_store(trade_eval_modules)

        mod.append_daily(
            fetch_page=lambda url: KTC_SAMPLE_PAGE,
            blob_upload=store.upload, blob_load=store.load,
            date_iso="2026-05-14", min_acceptable_rows=1,
        )
        after_first = json.loads(json.dumps(store.store[bl.ktc_historical_blob()]))
        mod.append_daily(
            fetch_page=lambda url: KTC_SAMPLE_PAGE,
            blob_upload=store.upload, blob_load=store.load,
            date_iso="2026-05-14", min_acceptable_rows=1,
        )
        after_second = store.store[bl.ktc_historical_blob()]

        # Last-updated timestamp will differ; everything else identical.
        after_first.pop("last_updated_utc", None)
        after_second_copy = {k: v for k, v in after_second.items() if k != "last_updated_utc"}
        assert after_second_copy == after_first

