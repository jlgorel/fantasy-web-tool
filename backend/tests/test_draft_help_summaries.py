"""Tests for draft_help.summaries orchestration + the /draft-help routes.

The summary functions take injectable fetchers, so these exercise the real
aggregation logic against in-memory fakes (no network / no Excel).
"""
from __future__ import annotations

import json

import pytest

from app.services.draft_help import summaries
from app.services.draft_help.draft_fetch import NormalizedDraft, NormalizedPick
from app.services.draft_help.rankings_source import config_key


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------
def _snake_draft(draft_id, league_id, season):
    # u1 takes player "1" (rank 5) at pick 1 -> a reach vs the rank-1 board slot;
    # u2 takes player "2" (rank 1) at pick 2 -> roughly on value.
    picks = [
        NormalizedPick(1, 1, 1, "1", name="Faller", position="RB", user_id="u1"),
        NormalizedPick(2, 1, 2, "2", name="Reacher", position="WR", user_id="u2"),
        NormalizedPick(13, 2, 1, "3", name="Late QB", position="QB", user_id="u1"),
    ]
    return NormalizedDraft(draft_id, league_id, season, "snake", 12, 16, "half_ppr", picks=picks)


def _auction_draft(draft_id, league_id, season):
    picks = [
        NormalizedPick(1, 1, None, "1", name="Star", position="RB", user_id="u1", amount=80),
        NormalizedPick(2, 1, None, "2", name="Scrub", position="WR", user_id="u1", amount=2),
    ]
    return NormalizedDraft(draft_id, league_id, season, "auction", 12, 16, "half_ppr", picks=picks)


def _league_obj(total_rosters=12, rec=0.5, superflex=False):
    rp = ["QB", "RB", "WR", "TE", "FLEX"]
    if superflex:
        rp.append("SUPER_FLEX")
    return {"total_rosters": total_rosters, "scoring_settings": {"rec": rec},
            "roster_positions": rp, "season": "2024"}


def _blob_loader(_name):
    cfg = {
        "teams": 12, "ppr": 0.5, "superflex": False, "budget": 200,
        "players": [
            {"player_id": "1", "name": "Faller", "pos": "RB", "overall_rank": 5, "vbd": 90, "auction": 40},
            {"player_id": "2", "name": "Reacher", "pos": "WR", "overall_rank": 1, "vbd": 120, "auction": 60},
            {"player_id": "3", "name": "Late QB", "pos": "QB", "overall_rank": 8, "vbd": 60, "auction": 10},
        ],
    }
    return {"year": "2024", "budget": 200, "configs": {config_key(12, 0.5, False): cfg}}


# ---------------------------------------------------------------------------
# league_habits
# ---------------------------------------------------------------------------
def test_league_habits_snake():
    payload = summaries.league_habits(
        "L1",
        seasons=2,
        season_chain=lambda lid: [{"league_id": "L1", "season": "2024"}],
        fetch_league=lambda lid: _league_obj(),
        fetch_users=lambda lid: {"u1": "Alice", "u2": "Bob"},
        load_drafts=lambda lid: [_snake_draft("d1", "L1", "2024")],
        blob_loader=_blob_loader,
    )
    assert payload["feature"] == "league_habits"
    assert set(payload["managers"]) == {"u1", "u2"}
    u1 = payload["managers"]["u1"]
    assert u1["username"] == "Alice"
    assert "snake" in u1
    assert u1["snake"]["reach"]["picks_evaluated"] == 2  # players 1 and 3
    # Reach/steal is now sized in VBD points vs the board slot.
    assert "avg_vbd_delta" in u1["snake"]["reach"]
    assert payload["league_wide"]["draft_type"] == "snake"


def test_league_habits_auction_market_section():
    payload = summaries.league_habits(
        "L1",
        season_chain=lambda lid: [{"league_id": "L1", "season": "2024"}],
        fetch_league=lambda lid: _league_obj(),
        fetch_users=lambda lid: {"u1": "Alice"},
        load_drafts=lambda lid: [_auction_draft("d1", "L1", "2024")],
        blob_loader=_blob_loader,
    )
    u1 = payload["managers"]["u1"]
    assert "auction" in u1
    assert u1["auction"]["avg_spend_by_position"]["RB"] == 80
    assert payload["league_wide"]["draft_type"] == "auction"


# ---------------------------------------------------------------------------
# user_habits
# ---------------------------------------------------------------------------
def test_user_habits_aggregates_and_dedupes():
    calls = {"n": 0}

    def fake_user_leagues(uid, year):
        # Same league shows up under multiple probed years; dedup by draft_id.
        return [{"league_id": "L1", "season": "2024"}]

    def fake_load_drafts(lid):
        calls["n"] += 1
        return [_snake_draft("d1", "L1", "2024")]

    payload = summaries.user_habits(
        "alice",
        seasons=2,
        resolve_user_id=lambda name: "u1",
        fetch_user_leagues=fake_user_leagues,
        fetch_league=lambda lid: _league_obj(),
        load_drafts=fake_load_drafts,
        blob_loader=_blob_loader,
    )
    assert payload["user_id"] == "u1"
    assert payload["leagues_scanned"] == 1  # deduped across probed years
    assert payload["summary"]["snake"]["reach"]["picks_evaluated"] == 2


def test_user_habits_unknown_user():
    payload = summaries.user_habits("ghost", resolve_user_id=lambda name: None)
    assert payload.get("error") == "user_not_found"


def test_user_habits_skips_dynasty_leagues():
    loaded = {"n": 0}

    def fake_load_drafts(lid):
        loaded["n"] += 1
        return [_snake_draft("d1", lid, "2024")]

    def fake_fetch_league(lid):
        league = _league_obj()
        league["settings"] = {"type": 2}  # dynasty
        return league

    payload = summaries.user_habits(
        "alice",
        seasons=1,
        resolve_user_id=lambda name: "u1",
        fetch_user_leagues=lambda uid, year: [{"league_id": "DYN", "season": "2024"}],
        fetch_league=fake_fetch_league,
        load_drafts=fake_load_drafts,
        blob_loader=_blob_loader,
    )
    assert payload["leagues_scanned"] == 0
    assert loaded["n"] == 0          # dynasty league is never crawled
    assert payload["summary"] == {}  # no habits gathered


def test_accumulate_skips_dynasty_draft():
    # Defense-in-depth: even if a dynasty draft slips past the league filter,
    # its picks are never accumulated (scoring_type carries "dynasty").
    accs: dict = {}
    dyn = NormalizedDraft(
        "d1", "L1", "2025", "linear", 12, 3, "dynasty_2qb",
        picks=[NormalizedPick(1, 1, 1, "1", name="Rook", position="WR", user_id="u1")],
    )
    assert dyn.is_dynasty is True
    summaries._accumulate(accs, dyn, {})
    assert accs == {}


# ---------------------------------------------------------------------------
# opponents_habits
# ---------------------------------------------------------------------------
def test_opponents_habits_excludes_own_league_and_respects_caps():
    seen_leagues = []

    def fake_user_leagues(uid, year):
        # Return the user's own league (excluded) + one OTHER league.
        return [{"league_id": "L1", "season": "2024"},
                {"league_id": f"OTHER_{uid}", "season": "2024"}]

    def fake_load_drafts(lid):
        seen_leagues.append(lid)
        return [_snake_draft(f"d_{lid}", lid, "2024")]

    payload = summaries.opponents_habits(
        "L1",
        seasons=1,
        max_leagues=5,
        fetch_users=lambda lid: {"u1": "Alice", "u2": "Bob"},
        fetch_user_leagues=fake_user_leagues,
        fetch_league=lambda lid: _league_obj(),
        load_drafts=fake_load_drafts,
        season_chain=lambda lid: [{"league_id": "L1", "season": "2024"}],
        blob_loader=_blob_loader,
    )
    assert payload["feature"] == "opponents_habits"
    assert "warning" in payload
    assert set(payload["opponents"]) == {"u1", "u2"}
    # Own league L1 must never be crawled.
    assert "L1" not in seen_leagues
    assert "OTHER_u1" in seen_leagues and "OTHER_u2" in seen_leagues


# ---------------------------------------------------------------------------
# Routes (monkeypatch the summary builders to avoid network)
# ---------------------------------------------------------------------------
def test_route_user_habits(client, monkeypatch):
    import app.routes as routes
    monkeypatch.setattr(
        routes.draft_help_summaries, "user_habits",
        lambda username, seasons: {"feature": "user_habits", "username": username, "seasons": seasons},
    )
    resp = client.get("/draft-help/user/alice/habits?seasons=2")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["username"] == "alice" and body["seasons"] == 2


def test_route_league_habits_caches(client, monkeypatch):
    import app.routes as routes
    calls = {"n": 0}

    def builder(league_id, seasons):
        calls["n"] += 1
        return {"feature": "league_habits", "league_id": league_id}

    monkeypatch.setattr(routes.draft_help_summaries, "league_habits", builder)
    r1 = client.get("/draft-help/league/L1/habits")
    r2 = client.get("/draft-help/league/L1/habits")
    assert r1.status_code == r2.status_code == 200
    # Second hit served from (fake) redis cache -> builder called once.
    assert calls["n"] == 1


def test_route_opponents_habits(client, monkeypatch):
    import app.routes as routes
    monkeypatch.setattr(
        routes.draft_help_summaries, "opponents_habits",
        lambda league_id, seasons, max_leagues: {
            "feature": "opponents_habits", "league_id": league_id,
            "caps": {"max_leagues_per_opponent": max_leagues, "seasons": seasons},
        },
    )
    resp = client.get("/draft-help/league/L9/opponents?seasons=2&max_leagues=3")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["caps"] == {"max_leagues_per_opponent": 3, "seasons": 2}


# ---------------------------------------------------------------------------
# rankings_config_players + sim/rankings routes (run against real fixtures)
# ---------------------------------------------------------------------------
def test_rankings_config_players_fake_loader():
    rows = summaries.rankings_config_players(2024, 12, 0.5, False, blob_loader=_blob_loader)
    assert len(rows) == 3
    assert {r["player_id"] for r in rows} == {"1", "2", "3"}


def test_rankings_config_players_merges_adp():
    def loader(name):
        if name.startswith("draft_adp_"):
            return {"configs": {config_key(12, 0.5, False): {
                "players": {"1": {"adp": 2.5, "stdev": 1.1}}}}}
        return _blob_loader(name)

    rows = summaries.rankings_config_players(2024, 12, 0.5, False, blob_loader=loader)
    by_id = {r["player_id"]: r for r in rows}
    assert by_id["1"]["adp"] == 2.5 and by_id["1"]["adp_stdev"] == 1.1
    assert "adp" not in by_id["2"]  # no FFC entry -> sim falls back to VBD order


def test_route_rankings_from_fixture(client):
    resp = client.get("/draft-help/rankings?year=2024&teams=12&ppr=0.5&sf=1")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["config"]["superflex"] is True
    assert len(body["players"]) > 100
    assert "overall_rank" in body["players"][0] and "fpts" in body["players"][0]


def test_route_sim_from_fixture(client):
    resp = client.post("/draft-help/sim", json={
        "year": "2024", "teams": 12, "rounds": 14, "my_slot": 1,
        "ppr": 0.5, "superflex": True,
        "drafted_ids": [], "my_roster_ids": [],
        "n_sims": 15, "top_k": 5, "seed": 1,
    })
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["recommendation"] is not None
    # top_k by ADP plus the best available at each startable position.
    assert len(body["candidates"]) >= 5
    assert body["recommendation"]["player_id"] in {c["player_id"] for c in body["candidates"]}


def test_route_sim_accepts_custom_slots(client):
    resp = client.post("/draft-help/sim", json={
        "year": "2024", "teams": 12, "rounds": 14, "my_slot": 1,
        "ppr": 0.5, "superflex": False,
        "slots": {"QB": 1, "RB": 2, "WR": 3, "TE": 1, "FLEX": 1},
        "drafted_ids": [], "my_roster_ids": [],
        "n_sims": 12, "top_k": 4, "seed": 1,
    })
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["candidates"]
    assert "likely_next" in body["candidates"][0]
