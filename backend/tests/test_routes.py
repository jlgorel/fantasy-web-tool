"""Smoke tests for the Flask routes.

These exercise the request → service → fixture-blob path end-to-end without
hitting external services. ``/load-sleeper-info`` and friends are skipped
because they would require live HTTP calls to Sleeper/Fleaflicker.
"""
from __future__ import annotations

import json


class TestOverallRanks:
    def test_returns_all_variants(self, client):
        resp = client.get("/overall-ranks")
        assert resp.status_code == 200
        body = resp.get_json()
        assert "overall_rankings" in body
        # Six scoring variants are produced by the scraper.
        variants = set(body["overall_rankings"].keys())
        assert {"std_4ptpass", "halfppr_4ptpass", "fullppr_4ptpass"}.issubset(variants)

    def test_rows_are_sorted_by_vegas_desc(self, client):
        body = client.get("/overall-ranks").get_json()
        rows = body["overall_rankings"]["halfppr_4ptpass"]
        vegases = [r.get("VEGAS") for r in rows if isinstance(r.get("VEGAS"), (int, float))]
        # Scraper writes them sorted; assert non-increasing.
        assert vegases == sorted(vegases, reverse=True)


class TestWaiverWireRoute:
    def test_default(self, client):
        resp = client.get("/waiver-wire")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["variant"] == "halfppr_4ptpass"
        assert "by_position" in body

    def test_query_params_respected(self, client):
        resp = client.get("/waiver-wire?variant=fullppr_4ptpass&max_owned=25&top_n=4")
        body = resp.get_json()
        assert body["variant"] == "fullppr_4ptpass"
        assert body["max_owned_pct"] == 25.0
        assert body["top_n"] == 4
        for rows in body["by_position"].values():
            assert len(rows) <= 4

    def test_invalid_query_params_default(self, client):
        # Garbage values fall back to defaults rather than 500.
        resp = client.get("/waiver-wire?max_owned=garbage&top_n=alsogarbage")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["max_owned_pct"] == 50.0
        assert body["top_n"] == 15


class TestRisersFallersRoute:
    def test_default(self, client):
        resp = client.get("/risers-fallers")
        assert resp.status_code == 200
        body = resp.get_json()
        assert "risers" in body
        assert "fallers" in body


class TestLoadLastRunInfo:
    def test_reads_fixture_runinfo(self, client):
        resp = client.get("/load-last-run-info")
        assert resp.status_code == 200
        # runinfo.json fixture is a JSON object.
        assert isinstance(resp.get_json(), dict)


class TestCachedRoutes:
    """The cached endpoints rely on Redis. Without a prior /load-sleeper-info
    the cache is empty and they should 404 cleanly (not 500)."""

    def test_load_cached_starts_404_without_cache(self, client):
        resp = client.get("/load-cached-starts", headers={"X-User-UUID": "no-such-user"})
        assert resp.status_code == 404

    def test_load_league_data_requires_league(self, client):
        resp = client.get("/load-league-data", headers={"X-User-UUID": "x"})
        assert resp.status_code == 400

    def test_load_league_data_404_without_cache(self, client):
        resp = client.get(
            "/load-league-data?league=Whatever",
            headers={"X-User-UUID": "no-such-user"},
        )
        assert resp.status_code == 404

    def test_load_league_data_success_with_seeded_cache(self, app, client, fake_redis):
        """Seed the redis stub with the new lineup-bundle shape and assert
        /load-league-data returns boris_optimized + vegas_optimized + your_lineup."""
        uuid = "test-user"
        league_name = "Test League"

        seeded_lineups = {
            league_name: {
                "boris_optimized": [{"POS": "QB", "NAME": "Mahomes"}],
                "vegas_optimized": [{"POS": "QB", "NAME": "Mahomes"}],
                "your_lineup": [{"POS": "QB", "NAME": "Mahomes"}],
            }
        }
        seeded_fa = {league_name: {"QB": []}}

        fake_redis.set(f"boris_data_{uuid}", json.dumps(seeded_lineups))
        fake_redis.set(f"free_agents_{uuid}", json.dumps(seeded_fa))

        resp = client.get(
            f"/load-league-data?league={league_name}",
            headers={"X-User-UUID": uuid},
        )
        assert resp.status_code == 200
        body = resp.get_json()
        # New explicit fields present
        assert body["boris_optimized"][0]["NAME"] == "Mahomes"
        assert body["vegas_optimized"][0]["NAME"] == "Mahomes"
        assert body["your_lineup"][0]["NAME"] == "Mahomes"
        # Legacy alias still wired up
        assert body["suggested_starts"][0]["NAME"] == "Mahomes"
        assert body["free_agent_recs"] == {"QB": []}

    def test_load_cached_starts_with_seeded_cache(self, client, fake_redis):
        uuid = "test-user-2"
        fake_redis.set(
            f"boris_data_{uuid}",
            json.dumps({"League A": {}, "League B": {}}),
        )
        resp = client.get("/load-cached-starts", headers={"X-User-UUID": uuid})
        assert resp.status_code == 200
        names = resp.get_json()["league_names"]
        assert set(names) == {"League A", "League B"}
