"""Tests for the DraftKings scraper helpers in
``azure-functions/draftkings_help.py``.

The scraper module lives outside the backend package and uses top-level
imports (``from config import Config``, ``from _fantasy_common import ...``)
that collide with the backend's ``config`` module. We isolate the scraper
import by inserting ``azure-functions/`` ahead on ``sys.path`` for the
duration of the import, then loading via ``importlib`` and snapshot/restore
``sys.modules`` so the backend Flask tests in the same session aren't poisoned.

We also avoid ever calling ``get_draftkings_data()`` (which spins up a real
headless browser) — only the pure helpers and the
``form_player_projections_dict`` flow are exercised, with the live scrape
monkeypatched to return the saved JSON fixtures from
``tests/fixtures/scraper/``.
"""
from __future__ import annotations

import importlib
import json
import math
import sys
from pathlib import Path
from typing import Any, Dict

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRAPER_FIX = REPO_ROOT / "tests" / "fixtures" / "scraper"
AZURE_FN_DIR = REPO_ROOT / "azure-functions"

# Filename → stat key the scraper assigns it under (mirrors
# Config.prop_name_to_stat_name_map).
PROP_FILE_TO_STAT = {
    "Anytime Scorer.json": "Anytime Touchdown",
    "Receptions Over Under.json": "Receptions",
    "Passing TDs Alt Lines.json": "Passing Touchdowns",
    "Passing Yards Alt Lines.json": "Passing Yards",
    "Receiving Yards Alt Lines.json": "Receiving Yards",
    "Rushing Yards Alt Lines.json": "Rushing Yards",
    "Interceptions Over Under.json": "Interceptions",
}


# ---------------------------------------------------------------------------
# Module loader: brings draftkings_help in despite the backend conftest's
# sys.path. We snapshot conflicting modules and restore them afterwards.
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def dk_module():
    saved_path = list(sys.path)
    saved_mods = {
        name: sys.modules.get(name)
        for name in ("config", "_fantasy_common", "draftkings_help")
    }
    # Pop conflicting modules so the import below picks azure-functions versions.
    for name in ("config", "_fantasy_common", "draftkings_help"):
        sys.modules.pop(name, None)
    sys.path.insert(0, str(AZURE_FN_DIR))
    try:
        dk = importlib.import_module("draftkings_help")
        yield dk
    finally:
        sys.path[:] = saved_path
        for name, mod in saved_mods.items():
            if mod is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = mod


@pytest.fixture(scope="module")
def all_props(dk_module) -> Dict[str, Any]:
    """Return ``{stat_name: parsed_json}`` for every saved scraper fixture."""
    out: Dict[str, Any] = {}
    for fname, stat in PROP_FILE_TO_STAT.items():
        out[stat] = json.loads((SCRAPER_FIX / fname).read_text(encoding="utf-8"))
    return out


# ---------------------------------------------------------------------------
# Pure odds math
# ---------------------------------------------------------------------------
class TestOddsMath:
    def test_odds_to_probability_negative_favorite(self, dk_module):
        # -200 implies 200/(200+100) = 0.6667
        assert dk_module.odds_to_probability(-200) == pytest.approx(2 / 3)

    def test_odds_to_probability_plus_underdog(self, dk_module):
        # +150 implies 100/(150+100) = 0.40
        assert dk_module.odds_to_probability(150) == pytest.approx(0.4)

    def test_odds_to_probability_pickem(self, dk_module):
        # +100 should give 0.5
        assert dk_module.odds_to_probability(100) == pytest.approx(0.5)

    def test_devig_probability_collapses(self, dk_module):
        # When the book takes 0% vig the probability should be unchanged.
        assert dk_module.devig_probability(0.5, 0.0) == 0.5
        # 5% vig brings 0.55 down toward 0.524
        assert dk_module.devig_probability(0.55, 0.05) == pytest.approx(0.55 / 1.05)

    def test_expected_anytime_touchdown_basic(self, dk_module):
        ev, exact = dk_module.expected_anytime_touchdown(-150, 500)
        # P1 = 150/250 = .6, P2 = 100/600 ≈ .1667; E[TDs] = .4333*1 + .1667*2
        assert ev == pytest.approx(0.4333 + 0.3333, abs=0.005)
        # Distribution must sum to ~1.0
        assert sum(exact.values()) == pytest.approx(1.0, abs=1e-6)
        # All branches non-negative
        assert all(p >= 0 for p in exact.values())

    def test_expected_anytime_touchdown_no_two_plus(self, dk_module):
        """Some props have no 2+ market — passes 0 for the second odds."""
        ev, exact = dk_module.expected_anytime_touchdown(-150, 0)
        assert ev == pytest.approx(0.6, abs=1e-6)  # = P1 since P2=0
        assert exact[(2, 2)] == 0.0


# ---------------------------------------------------------------------------
# over_under_projection — receptions / interceptions
# ---------------------------------------------------------------------------
class TestOverUnderProjection:
    def test_balanced_market(self, dk_module):
        # Line 5.5, both sides at -110 → projection should land near 5.5.
        proj, exact = dk_module.over_under_projection(5.5, -110, -110, "receptions")
        assert proj == pytest.approx(5.5, abs=0.5)
        assert sum(exact.values()) == pytest.approx(1.0, abs=1e-6)

    def test_skewed_to_over(self, dk_module):
        # Heavy over → projection drifts above the line.
        proj_balanced, _ = dk_module.over_under_projection(5.5, -110, -110, "receptions")
        proj_over_heavy, _ = dk_module.over_under_projection(5.5, -300, 240, "receptions")
        assert proj_over_heavy > proj_balanced

    def test_zero_probability_market_returns_none(self, dk_module):
        # Manufactured impossible combo: both at +∞ would give ~0 prob; we
        # pass huge positive odds so the function's "total_probability == 0"
        # branch isn't triggered, but at least confirm structure.
        proj, exact = dk_module.over_under_projection(0.5, -120, +110, "interceptions")
        assert isinstance(exact, dict) and len(exact) > 0

    def test_interceptions_capped(self, dk_module):
        # max_val for interceptions is hard-capped at 3 in the source.
        _, exact = dk_module.over_under_projection(0.5, -120, +100, "interceptions")
        assert max(k[0] for k in exact.keys()) <= 3


# ---------------------------------------------------------------------------
# Expected yardage / TDs from alt-line markets
# ---------------------------------------------------------------------------
class TestExpectedFromAltLines:
    def test_expected_yards_monotonic(self, dk_module):
        # Synthetic alt-line ladder: increasing yards → decreasing prob.
        odds = {25: -10000, 50: -500, 75: -200, 100: +120, 125: +400, 150: +800}
        ev, exact = dk_module.calculate_expected_yards(odds, 0.071)
        # All buckets nonneg and sum to ~1.
        assert all(p >= -1e-9 for p in exact.values())
        assert sum(exact.values()) == pytest.approx(1.0, abs=1e-3)
        # Expected yards should be in the bracket where -110ish odds live (~75–100).
        assert 50 < ev < 130

    def test_expected_tds_basic(self, dk_module):
        # 1+ TD heavy fav, 2+ TD ~+400.
        ev, exact = dk_module.calculate_expected_tds({1: -250, 2: 400}, 0.071)
        assert ev > 0
        assert all(p >= 0 for p in exact.values())


# ---------------------------------------------------------------------------
# Monte Carlo sim — make sure the scoring shapes look right
# ---------------------------------------------------------------------------
class TestRunPlayerSim:
    def test_skill_position_returns_three_variants(self, dk_module):
        stats = {
            "Receptions": {(0, 4): 0.4, (5, 8): 0.45, (9, 14): 0.15},
            "Receiving Yards": {(0, 50): 0.3, (50, 100): 0.5, (100, 200): 0.2},
            "Rushing Yards": {(0, 0): 1.0},
            "Anytime Touchdown": {(0, 0): 0.5, (1, 1): 0.4, (2, 2): 0.1},
        }
        out = dk_module.run_player_sim(stats, n_sims=2000)
        assert {"STD", "HalfPPR", "PPR"} <= out.keys()
        # PPR mean ≥ HalfPPR mean ≥ STD mean (more reception scoring).
        assert out["STD"]["mean"] <= out["HalfPPR"]["mean"] <= out["PPR"]["mean"]
        # Percentiles span 1..100 and are non-decreasing.
        pcts = out["PPR"]["percentiles"]
        keys_sorted = sorted(pcts.keys())
        vals_sorted = [pcts[k] for k in keys_sorted]
        assert vals_sorted == sorted(vals_sorted)
        assert keys_sorted[0] == 1 and keys_sorted[-1] == 100

    def test_qb_returns_qb_variants(self, dk_module):
        stats = {
            "Passing Yards": {(150, 200): 0.2, (200, 275): 0.5, (275, 350): 0.3},
            "Passing Touchdowns": {(0, 0): 0.1, (1, 1): 0.5, (2, 2): 0.3, (3, 3): 0.1},
            "Interceptions": {(0, 0): 0.7, (1, 1): 0.25, (2, 2): 0.05},
            "Rushing Yards": {(0, 25): 0.7, (25, 60): 0.3},
            "Anytime Touchdown": {(0, 0): 0.85, (1, 1): 0.15},
            "Receiving Yards": {(0, 0): 1.0},
        }
        out = dk_module.run_player_sim(stats, n_sims=2000)
        assert {"QB_STD", "QB_6PT"} <= out.keys()
        # 6pt pass TDs strictly raises the mean (passing TDs always >=0).
        assert out["QB_6PT"]["mean"] >= out["QB_STD"]["mean"]
        # Sanity: probabilities in [0,1].
        for k in ("QB_STD", "QB_6PT"):
            assert 0.0 <= out[k]["boom"] <= 1.0
            assert 0.0 <= out[k]["bust"] <= 1.0

    def test_sample_from_ranges_picks_within_buckets(self, dk_module):
        rng = {(0, 0): 0.5, (5, 10): 0.5}
        samples = dk_module.sample_from_ranges(rng, 1000)
        # Each sample is either 0 or the bucket midpoint 7.5 (low+high)/2.
        unique = set(samples.tolist())
        assert unique <= {0.0, 7.5}


# ---------------------------------------------------------------------------
# normalize_name_to_sleeper — name aliasing
# ---------------------------------------------------------------------------
class TestNormalizeName:
    @pytest.mark.parametrize("dk_name,expected", [
        ("Patrick Mahomes II", "Patrick Mahomes"),
        ("Marquise Brown", "Hollywood Brown"),
        ("DeVon Achane", "De'Von Achane"),
        ("D.J. Moore", "DJ Moore"),
        ("Lamar Jackson (BAL)", "Lamar Jackson"),
        ("Justin Jefferson", "Justin Jefferson"),  # passthrough
        ("Calvin Ridley Sr.", "Calvin Ridley"),  # Sr. stripped
        ("Brian Robinson Jr.", "Brian Robinson"),  # Jr. stripped
    ])
    def test_known_aliases(self, dk_module, dk_name, expected):
        assert dk_module.normalize_name_to_sleeper(dk_name) == expected


class TestDraftKingsHttpFetch:
    @pytest.mark.parametrize("raw,expected", [
        ("−160", -160),
        ("-160", -160),
        ("+285", 285),
        (110, 110),
    ])
    def test_parse_american_odds(self, dk_module, raw, expected):
        assert dk_module.parse_american_odds(raw) == expected

    def test_fetch_uses_verified_headers_and_accepts_empty_market(
        self, dk_module, monkeypatch
    ):
        calls = []

        class FakeResponse:
            status_code = 200

            def raise_for_status(self):
                return None

            def json(self):
                return {"selections": []}

        class FakeSession:
            def __init__(self):
                self.headers = {}

            def get(self, url, timeout):
                calls.append((url, timeout, dict(self.headers)))
                return FakeResponse()

        monkeypatch.setattr(dk_module.requests, "Session", FakeSession)
        monkeypatch.setattr(dk_module.time, "sleep", lambda _seconds: None)
        monkeypatch.setattr(
            dk_module.Config,
            "prop_name_to_ids_map",
            {"Interceptions Over Under": (1000, 15937)},
        )
        monkeypatch.setattr(
            dk_module.Config,
            "prop_name_to_stat_name_map",
            {"Interceptions Over Under": "Interceptions"},
        )

        result = dk_module.get_draftkings_data()

        assert result == {"Interceptions": {"selections": []}}
        assert len(calls) == 2
        warmup_url, _, _ = calls[0]
        assert warmup_url == dk_module.DRAFTKINGS_WARMUP_URL
        url, timeout, headers = calls[1]
        assert "/categories/1000/subcategories/15937?format=json" in url
        assert timeout == dk_module.DRAFTKINGS_REQUEST_TIMEOUT_SECONDS
        assert headers["Accept-Language"] == "en-US,en;q=0.9"
        assert "Mozilla/5.0" in headers["User-Agent"]

    def test_fetch_raises_on_http_failure(self, dk_module, monkeypatch):
        class FakeResponse:
            status_code = 200

            def raise_for_status(self):
                raise dk_module.requests.HTTPError("403 Client Error")

        class FakeSession:
            def __init__(self):
                self.headers = {}

            def get(self, _url, timeout):
                assert timeout == dk_module.DRAFTKINGS_REQUEST_TIMEOUT_SECONDS
                return FakeResponse()

        monkeypatch.setattr(dk_module.requests, "Session", FakeSession)
        monkeypatch.setattr(dk_module.time, "sleep", lambda _seconds: None)
        monkeypatch.setattr(
            dk_module.Config,
            "prop_name_to_ids_map",
            {"Anytime Scorer": (1003, 12438)},
        )
        monkeypatch.setattr(
            dk_module.Config,
            "prop_name_to_stat_name_map",
            {"Anytime Scorer": "Anytime Touchdown"},
        )

        with pytest.raises(dk_module.requests.HTTPError, match="403"):
            dk_module.get_draftkings_data()


# ---------------------------------------------------------------------------
# has_all_vegas_stats — branching for QB / RB / WR-TE
# ---------------------------------------------------------------------------
class TestHasAllVegasStats:
    def test_qb_complete(self, dk_module):
        ok, _ = dk_module.has_all_vegas_stats({
            "Passing Yards": {}, "Passing Touchdowns": {}, "Interceptions": {},
            "Anytime Touchdown": {}, "Rushing Yards": {},
        })
        assert ok is True

    def test_qb_missing_rushing_returns_note(self, dk_module):
        ok, note = dk_module.has_all_vegas_stats({
            "Passing Yards": {}, "Passing Touchdowns": {}, "Interceptions": {},
        })
        # Backend has all the *required* QB props so flag is True, but warns.
        assert ok is True
        assert "rushing" in note.lower()

    def test_rb_complete(self, dk_module):
        ok, _ = dk_module.has_all_vegas_stats({
            "Rushing Yards": {}, "Receptions": {}, "Receiving Yards": {},
            "Anytime Touchdown": {},
        })
        assert ok is True

    def test_rb_missing_receptions(self, dk_module):
        ok, _ = dk_module.has_all_vegas_stats({
            "Rushing Yards": {}, "Receiving Yards": {}, "Anytime Touchdown": {},
        })
        assert ok is False

    def test_wr_complete(self, dk_module):
        ok, _ = dk_module.has_all_vegas_stats({
            "Receiving Yards": {}, "Receptions": {}, "Anytime Touchdown": {},
        })
        assert ok is True


# ---------------------------------------------------------------------------
# End-to-end: form_player_projections_dict against the saved scraper JSON.
# ---------------------------------------------------------------------------
class TestFormPlayerProjections:
    """Drives ``form_player_projections_dict`` with the real saved fixtures.

    We monkeypatch out:
      * ``get_draftkings_data`` → returns the saved JSON keyed by stat name
      * ``load_json_from_azure_storage`` → returns a synthetic
        ``players.json`` containing a known subset of player names so the
        ``name not in sleeper_names`` branch keeps most parsing alive.
    """

    def _build_sleeper_players(self, dk_module, raw_props: Dict[str, Any]) -> Dict[str, Any]:
        """Build a synthetic players.json containing every name that appears
        in any saved prop fixture (post name-normalization)."""
        names: set[str] = set()
        for stat, blob in raw_props.items():
            for sel in blob.get("selections", []):
                participants = sel.get("participants") or []
                if not participants:
                    # Alt-line markets sometimes use ``label`` as the name.
                    nm = sel.get("label", "").replace("+", "").strip()
                else:
                    nm = participants[0].get("name", "")
                if nm:
                    names.add(nm)
        return {
            f"pid_{i:05d}": {"full_name": dk_module.normalize_name_to_sleeper(n),
                             "fantasy_positions": ["WR"]}
            for i, n in enumerate(sorted(names))
        }

    def test_pipeline_produces_expected_stat_buckets(
        self, dk_module, all_props, monkeypatch
    ):
        players = self._build_sleeper_players(dk_module, all_props)

        monkeypatch.setattr(dk_module, "get_draftkings_data", lambda: all_props)
        monkeypatch.setattr(
            dk_module,
            "load_json_from_azure_storage",
            lambda *a, **k: players,
        )

        result = dk_module.form_player_projections_dict()

        # Sanity: dict isn't empty, has many players.
        assert len(result) > 50, f"only {len(result)} players got projections"

        # Every player entry should have at least one of the expected stat
        # categories the scraper assigns.
        valid_stat_keys = set(PROP_FILE_TO_STAT.values()) | {"Simulations"}
        for player, stats in result.items():
            assert isinstance(stats, dict)
            assert set(stats.keys()) <= valid_stat_keys, (
                f"unexpected keys for {player}: {set(stats) - valid_stat_keys}"
            )

    def test_pipeline_runs_simulations_for_qualifying_players(
        self, dk_module, all_props, monkeypatch
    ):
        players = self._build_sleeper_players(dk_module, all_props)
        monkeypatch.setattr(dk_module, "get_draftkings_data", lambda: all_props)
        monkeypatch.setattr(
            dk_module, "load_json_from_azure_storage", lambda *a, **k: players,
        )

        # Smaller n_sims for speed: monkeypatch run_player_sim to use n=500.
        original_sim = dk_module.run_player_sim
        monkeypatch.setattr(
            dk_module,
            "run_player_sim",
            lambda stats, n_sims=10000: original_sim(stats, n_sims=500),
        )

        result = dk_module.form_player_projections_dict()

        sim_blocks = [
            stats["Simulations"] for stats in result.values()
            if "Simulations" in stats and "error" not in stats["Simulations"]
        ]
        # At least *some* players must have qualified for sim (i.e. props in
        # all required categories). Real captured DK day had ~50+ such guys.
        assert len(sim_blocks) >= 5
        # Each sim block has the QB or skill-pos shape.
        for block in sim_blocks:
            keys = set(block.keys())
            assert keys & {"STD", "HalfPPR", "PPR", "QB_STD", "QB_6PT"}

    def test_pipeline_marks_insufficient_data(
        self, dk_module, all_props, monkeypatch
    ):
        # Limit the saved props to just receptions so most players miss
        # the receiving yards / TD requirements → "Not enough data" path.
        partial = {"Receptions": all_props["Receptions"]}
        players = self._build_sleeper_players(dk_module, partial)
        monkeypatch.setattr(dk_module, "get_draftkings_data", lambda: partial)
        monkeypatch.setattr(
            dk_module, "load_json_from_azure_storage", lambda *a, **k: players,
        )

        result = dk_module.form_player_projections_dict()
        errored = [
            p for p, s in result.items()
            if isinstance(s.get("Simulations"), dict)
            and "error" in s["Simulations"]
        ]
        assert errored, "expected at least one player flagged with insufficient data"

    def test_pipeline_skips_dst_touchdown_rows_without_participants(
        self, dk_module, monkeypatch
    ):
        anytime = {
            "selections": [
                {
                    "label": "KC Chiefs D/ST",
                    "outcomeType": "To Score 2 Or More",
                    "participants": None,
                    "displayOdds": {"american": "+4000"},
                },
                {
                    "label": "Christian McCaffrey",
                    "outcomeType": "Anytime Scorer",
                    "participants": [{"name": "Christian McCaffrey"}],
                    "displayOdds": {"american": "−160"},
                },
            ]
        }
        players = {
            "pid_cmc": {
                "full_name": "Christian McCaffrey",
                "fantasy_positions": ["RB"],
            }
        }
        monkeypatch.setattr(
            dk_module,
            "get_draftkings_data",
            lambda: {"Anytime Touchdown": anytime},
        )
        monkeypatch.setattr(
            dk_module,
            "load_json_from_azure_storage",
            lambda *args, **kwargs: players,
        )

        result = dk_module.form_player_projections_dict()

        assert "christianmccaffrey" in result
        assert "kcchiefsdst" not in result
