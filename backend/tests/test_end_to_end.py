"""End-to-end tests stitching together the *real* pieces.

Two flows are exercised:

**Flow A — Scraper → backend → frontend (rankings)**
1. Run the DraftKings scraper's ``form_player_projections_dict`` against the
   saved DK prop JSONs in ``tests/fixtures/scraper/`` (with the real
   ``players.json`` fixture so name lookups land), producing the same shape
   the scraper writes to ``hand_calculated_projections.json``.
2. Feed that into ``form_all_projections_and_points_dict`` to produce the
   ``standard_player_rankings.json`` shape.
3. Round-trip both through ``json.dumps`` → ``json.loads`` (i.e. assert
   they're JSON-serializable, no ``np.float64`` or other non-portable types
   that would 500 the Flask response).
4. Swap them into the backend's blob loader and hit ``/overall-ranks``,
   ``/waiver-wire`` and ``/risers-fallers`` — assert each returns a 200 and
   the response shape the frontend expects.

**Flow B — Sleeper API → /load-sleeper-info → /load-cached-starts →
/load-league-data**
* ``sleeper_client.fetch_json`` is monkeypatched to return the captured
  Sleeper fixtures, so ``cache_sleeper_user_info`` runs with real player IDs
  but no network. The cached lineup bundle is then served back through the
  routes the frontend actually consumes.

The point of these tests is *not* to assert specific stat values — those
shift every offseason. We're asserting that the parser → backend → JSON
contract holds, with no exceptions or non-serializable values along the way.
"""
from __future__ import annotations

import copy
import importlib
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRAPER_FIX = REPO_ROOT / "tests" / "fixtures" / "scraper"
BLOB_FIX = REPO_ROOT / "tests" / "fixtures" / "blobs"
SLEEPER_FIX = REPO_ROOT / "tests" / "fixtures" / "api" / "sleeper"
AZURE_FN_DIR = REPO_ROOT / "azure-functions"


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
# Module-scoped fixtures (heavy work runs once per test session).
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def scraper_outputs():
    """Run the *real* DraftKings scraper end-to-end against the saved fixture
    JSONs and return ``(projections_dict, rankings_dict)``.

    The scraper module imports a top-level ``config`` (from
    ``azure-functions/config.py``) that collides with the backend's
    ``backend/config.py``. We do all the scraping inside this fixture, then
    *immediately* restore ``sys.modules`` so the rest of the test module sees
    the real backend ``config`` again. The function is module-scoped so the
    ~20s scrape only runs once per session.
    """
    saved_path = list(sys.path)
    saved_mods = {
        name: sys.modules.get(name)
        for name in ("config", "_fantasy_common", "draftkings_help")
    }
    for name in ("config", "_fantasy_common", "draftkings_help"):
        sys.modules.pop(name, None)
    sys.path.insert(0, str(AZURE_FN_DIR))
    try:
        dk = importlib.import_module("draftkings_help")

        # Load fixture inputs.
        saved_dk_props = {
            stat: json.loads((SCRAPER_FIX / fname).read_text(encoding="utf-8"))
            for fname, stat in PROP_FILE_TO_STAT.items()
        }
        players_blob_data = json.loads(
            (BLOB_FIX / "players.json").read_text(encoding="utf-8")
        )
        backup_proj_data = json.loads(
            (BLOB_FIX / "backup_fantasypros_projections.json").read_text(encoding="utf-8")
        )

        # Avoid the headless browser scrape; serve saved DK JSONs instead.
        dk.get_draftkings_data = lambda: saved_dk_props

        # Cut Monte Carlo iterations for speed (still plenty for shape checks).
        original_sim = dk.run_player_sim
        dk.run_player_sim = lambda stats, n_sims=10000: original_sim(stats, n_sims=500)

        # Stage 1: produce the hand_calculated_projections.json shape.
        def stage1_load(blob_name, *_a, **_kw):
            if blob_name == "players.json":
                return players_blob_data
            raise AssertionError(f"unexpected blob during stage1: {blob_name}")

        dk.load_json_from_azure_storage = stage1_load
        projections = dk.form_player_projections_dict()

        # Stage 2: produce the standard_player_rankings.json shape, fed by
        # the freshly-built projections dict.
        def stage2_load(blob_name, *_a, **_kw):
            if blob_name == "hand_calculated_projections.json":
                return projections
            if blob_name == "backup_fantasypros_projections.json":
                return backup_proj_data
            if blob_name == "players.json":
                return players_blob_data
            raise AssertionError(f"unexpected blob during stage2: {blob_name}")

        dk.load_json_from_azure_storage = stage2_load
        rankings = dk.form_all_projections_and_points_dict()
    finally:
        # Restore sys.modules + sys.path so the backend's `from config import
        # Config` resolves to backend/config.py for the rest of the module.
        sys.path[:] = saved_path
        for name, mod in saved_mods.items():
            if mod is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = mod

    return projections, rankings


# ---------------------------------------------------------------------------
# Flow A: scraper output is JSON-clean and the backend can serve it
# ---------------------------------------------------------------------------
class TestScraperToBackendRankings:
    def test_projections_json_serializable(self, scraper_outputs):
        """The dict the scraper writes to Azure must be vanilla JSON — no
        ``np.float64`` or other types Flask's ``jsonify`` would choke on."""
        projections, _ = scraper_outputs
        round_tripped = json.loads(json.dumps(projections))
        assert isinstance(round_tripped, dict)
        # Find at least one player that got a Simulations block.
        simmed = [
            v for v in round_tripped.values()
            if isinstance(v, dict) and isinstance(v.get("Simulations"), dict)
            and "error" not in v["Simulations"]
        ]
        assert simmed, "no players had a successful Monte Carlo simulation"

    def test_rankings_json_serializable_and_correct_shape(self, scraper_outputs):
        _, rankings = scraper_outputs
        rt = json.loads(json.dumps(rankings))
        # Six scoring variants are produced (3 PPR levels × 2 pass-TD values).
        expected = {
            "std_4ptpass", "halfppr_4ptpass", "fullppr_4ptpass",
            "std_6ptpass", "halfppr_6ptpass", "fullppr_6ptpass",
        }
        assert set(rt.keys()) == expected
        for variant, rows in rt.items():
            assert isinstance(rows, list)
            assert rows, f"{variant} is empty"
            # Every row needs the keys the frontend's PlayerTable consumes.
            for row in rows[:5]:
                assert {"PID", "NAME", "POS", "VEGAS", "PROJ"} <= row.keys()
            # Sorted descending by VEGAS (capped to top 500).
            vs = [r["VEGAS"] for r in rows]
            assert vs == sorted(vs, reverse=True)
            assert len(rows) <= 500

    def test_backend_serves_scraper_rankings(
        self, monkeypatch, scraper_outputs, client
    ):
        """Wire the freshly-built rankings into blob_store, then hit
        ``/overall-ranks`` and assert the frontend gets a clean payload."""
        _, rankings = scraper_outputs

        from app.services import blob_store

        real_load_fixture = blob_store._load_fixture

        def fake_load_fixture(blob_name: str):
            if blob_name == "standard_player_rankings.json":
                return rankings
            return real_load_fixture(blob_name)

        monkeypatch.setattr(blob_store, "_load_fixture", fake_load_fixture)

        resp = client.get("/overall-ranks")
        assert resp.status_code == 200
        body = resp.get_json()
        assert "overall_rankings" in body
        for variant_rows in body["overall_rankings"].values():
            assert isinstance(variant_rows, list)
            # Round-trip survived the Flask json encoder.
            for row in variant_rows[:3]:
                assert isinstance(row.get("VEGAS"), (int, float))

    def test_backend_serves_waiver_wire_with_scraper_rankings(
        self, monkeypatch, scraper_outputs, client
    ):
        """Same swap, different endpoint — the waiver-wire route reads the
        same rankings blob the scraper produces."""
        _, rankings = scraper_outputs

        from app.services import blob_store

        real_load_fixture = blob_store._load_fixture

        def fake_load_fixture(blob_name: str):
            if blob_name == "standard_player_rankings.json":
                return rankings
            return real_load_fixture(blob_name)

        monkeypatch.setattr(blob_store, "_load_fixture", fake_load_fixture)

        resp = client.get("/waiver-wire?top_n=5")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["variant"] == "halfppr_4ptpass"
        assert "by_position" in body
        for pos, rows in body["by_position"].items():
            assert isinstance(rows, list)
            assert len(rows) <= 5

    def test_backend_serves_risers_fallers_with_scraper_rankings(
        self, monkeypatch, scraper_outputs, client
    ):
        _, rankings = scraper_outputs

        from app.services import blob_store

        real_load_fixture = blob_store._load_fixture

        def fake_load_fixture(blob_name: str):
            if blob_name == "standard_player_rankings.json":
                return rankings
            return real_load_fixture(blob_name)

        monkeypatch.setattr(blob_store, "_load_fixture", fake_load_fixture)

        resp = client.get("/risers-fallers?top_n=5")
        assert resp.status_code == 200
        body = resp.get_json()
        assert {"risers", "fallers"} <= body.keys()


# ---------------------------------------------------------------------------
# Flow B: Sleeper API capture → /load-sleeper-info → cached routes
# ---------------------------------------------------------------------------
def _load_sleeper(name: str) -> Any:
    return json.loads((SLEEPER_FIX / name).read_text(encoding="utf-8"))


def _available_league_ids() -> List[str]:
    return sorted(
        p.name[len("league_"):-len(".json")]
        for p in SLEEPER_FIX.glob("league_LEAGUE_*.json")
    )


@pytest.fixture
def patch_sleeper_api(monkeypatch):
    """Make ``sleeper_client.fetch_json`` read captured fixtures."""
    available = set(_available_league_ids())

    def _fake(url: str, **_kw: Any) -> Any:
        if re.fullmatch(r"https://api\.sleeper\.app/v1/user/[^/]+", url):
            return copy.deepcopy(_load_sleeper("user.json"))
        if re.fullmatch(r"https://api\.sleeper\.app/v1/user/[^/]+/leagues/nfl/\d+", url):
            return [
                lg for lg in _load_sleeper("leagues.json")
                if lg["league_id"] in available
            ]
        m = re.fullmatch(r"https://api\.sleeper\.app/v1/league/(LEAGUE_\d+)", url)
        if m:
            return copy.deepcopy(_load_sleeper(f"league_{m.group(1)}.json"))
        m = re.fullmatch(r"https://api\.sleeper\.app/v1/league/(LEAGUE_\d+)/rosters", url)
        if m:
            return copy.deepcopy(_load_sleeper(f"rosters_{m.group(1)}.json"))
        raise AssertionError(f"unexpected sleeper url {url!r}")

    import app.services.sleeper_client as sc

    monkeypatch.setattr(sc, "fetch_json", _fake)
    monkeypatch.setattr(sc, "get_current_fantasy_year", lambda: "2026")


class TestSleeperLoadFlow:
    def test_load_sleeper_info_cycles_to_cached_routes(
        self, client, fake_redis, patch_sleeper_api
    ):
        """POST /load-sleeper-info with a Sleeper username (fed by captured
        API fixtures), then verify the same data is reachable through the
        cached routes the frontend hits next."""
        uuid_header = {"X-User-UUID": "e2e-test-user"}

        # --- 1) Trigger the full cache flow.
        resp = client.post(
            "/load-sleeper-info",
            json={"name": "jlgorel", "website": "Sleeper"},
            headers=uuid_header,
        )
        assert resp.status_code == 200, resp.get_data(as_text=True)
        body = resp.get_json()
        assert body["message"] == "Data cached successfully"
        league_names = body["league_names"]
        assert isinstance(league_names, dict)
        assert league_names, "no leagues came back from cache_sleeper_user_info"

        # The lineup-bundle shape is the new contract: each league entry
        # is a dict with boris_optimized + vegas_optimized + your_lineup.
        for lg, bundle in league_names.items():
            assert {"boris_optimized", "vegas_optimized", "your_lineup"} <= bundle.keys()
            assert isinstance(bundle["boris_optimized"], list)
            assert isinstance(bundle["vegas_optimized"], list)

        # Free agents map league name → {position: rows}.
        fa = body["free_agents"]
        assert set(fa.keys()) == set(league_names.keys())

        # --- 2) /load-cached-starts should now find the redis entry.
        resp2 = client.get("/load-cached-starts", headers=uuid_header)
        assert resp2.status_code == 200
        cached_names = resp2.get_json()["league_names"]
        assert sorted(cached_names) == sorted(league_names.keys())

        # --- 3) /load-league-data for any one of those leagues returns the
        # full bundle — exactly what the LeagueTabs frontend component reads.
        a_league = cached_names[0]
        resp3 = client.get(
            f"/load-league-data?league={a_league}",
            headers=uuid_header,
        )
        assert resp3.status_code == 200, resp3.get_data(as_text=True)
        bundle = resp3.get_json()
        assert "boris_optimized" in bundle
        assert "vegas_optimized" in bundle
        # Legacy alias is still emitted for older client builds.
        assert bundle["boris_optimized"] == bundle["suggested_starts"]
        assert "free_agent_recs" in bundle

    def test_load_sleeper_info_response_is_json_serializable(
        self, client, fake_redis, patch_sleeper_api
    ):
        """Whatever cache_sleeper_user_info returns has to round-trip
        through json.dumps — Flask already did it once on the way out, but
        this catches np.* values that slipped past via a single str() cast."""
        resp = client.post(
            "/load-sleeper-info",
            json={"name": "jlgorel", "website": "Sleeper"},
            headers={"X-User-UUID": "e2e-test-user-2"},
        )
        assert resp.status_code == 200
        # Flask gave us bytes; round-trip through json.loads/dumps to confirm
        # the *values* are simple types.
        parsed = resp.get_json()
        json.dumps(parsed)  # would raise on non-serializable

    def test_invalid_username_payload_400s_cleanly(self, client):
        resp = client.post("/load-sleeper-info", json={}, headers={"X-User-UUID": "x"})
        assert resp.status_code == 400
        assert resp.get_json().get("error")
