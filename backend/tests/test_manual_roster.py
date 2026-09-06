from __future__ import annotations

import pytest

from app.services.manual_roster import (
    ManualRosterValidationError,
    normalize_manual_roster,
)


def _payload():
    return {
        "name": "Family League",
        "scoring": {"ppr": 0.5, "passing_td_points": 4},
        "lineup_limits": {
            "QB": 1, "RB": 2, "WR": 2, "TE": 1, "REC_FLEX": 0,
            "FLEX": 1, "SUPER_FLEX": 0, "DEF": 1, "K": 1, "BN": 6,
        },
        "players": [
            {"player_id": "4984", "slot": "QB"},
            {"player_id": "9509", "slot": "RB"},
            {"player_id": "4034", "slot": "RB"},
            {"player_id": "7564", "slot": "WR"},
            {"player_id": "6794", "slot": "WR"},
            {"player_id": "1466", "slot": "TE"},
            {"player_id": "9493", "slot": "BN"},
        ],
    }


def test_normalizes_manual_payload_to_shared_roster_contract():
    roster = normalize_manual_roster(_payload())
    assert roster["league"] == "Family League"
    assert roster["positions"] == ["QB", "RB", "RB", "WR", "WR", "TE", "BN"]
    assert roster["starters"] == ["4984", "9509", "4034", "7564", "6794", "1466"]
    assert roster["settings"]["rec"] == 0.5
    assert roster["settings"]["pass_td"] == 4


def test_normalizes_shuffled_players_to_standard_slot_order_with_wt_flex():
    payload = _payload()
    payload["lineup_limits"]["REC_FLEX"] = 1
    payload["players"] = [
        {"player_id": "9493", "slot": "BN"},
        {"player_id": "7564", "slot": "REC_FLEX"},
        {"player_id": "9509", "slot": "RB"},
        {"player_id": "4984", "slot": "QB"},
    ]
    roster = normalize_manual_roster(payload)
    assert roster["positions"] == ["QB", "RB", "REC_FLEX", "BN"]
    assert roster["starters"] == ["4984", "9509", "7564"]


@pytest.mark.parametrize(
    "mutation, message",
    [
        (lambda body: body.update(name=""), "name is required"),
        (lambda body: body.update(scoring={"ppr": 0.25, "passing_td_points": 4}), "ppr"),
        (lambda body: body["players"].append({"player_id": "4984", "slot": "BN"}), "unique"),
        (lambda body: body["players"].__setitem__(0, {"player_id": "missing", "slot": "QB"}), "Unknown player_id"),
        (lambda body: body["players"].__setitem__(0, {"player_id": "4984", "slot": "WR"}), "not eligible"),
        (lambda body: body["players"].__setitem__(1, {"player_id": "4046", "slot": "QB"}), "Too many"),
    ],
)
def test_rejects_invalid_manual_payloads(mutation, message):
    body = _payload()
    mutation(body)
    with pytest.raises(ManualRosterValidationError, match=message):
        normalize_manual_roster(body)


def test_manual_player_catalog_route_returns_search_fields(client):
    response = client.get("/manual/players")
    assert response.status_code == 200
    players = response.get_json()["players"]
    assert len(players) >= 400
    josh_allen = next(player for player in players if player["player_id"] == "4984")
    assert josh_allen["name"] == "Josh Allen"
    assert josh_allen["position"] == "QB"
    assert {"player_id", "name", "position", "team"} <= josh_allen.keys()
    evan_mcpherson = next(player for player in players if player["player_id"] == "7839")
    assert evan_mcpherson["position"] == "K"
    buffalo = next(player for player in players if player["player_id"] == "BUF")
    assert buffalo == {
        "player_id": "BUF", "name": "Buffalo Bills", "position": "DEF", "team": "BUF"
    }


def test_manual_lineup_uses_optimizer_without_claiming_free_agents(client):
    response = client.post(
        "/manual/lineup",
        json=_payload(),
        headers={"X-User-UUID": "manual-test-user"},
    )
    assert response.status_code == 200, response.get_data(as_text=True)
    body = response.get_json()
    assert body["boris_optimized"]
    assert body["vegas_optimized"]
    assert body["your_lineup"]
    assert body["suggested_starts"] == body["boris_optimized"]
    assert body["free_agent_model"] == "not_available"
    assert body["free_agent_recs"] == {}


def test_manual_lineup_never_models_free_agents(client):
    payload = _payload()
    # Older exports may still contain this removed field. It is ignored.
    payload["unavailable_player_ids"] = ["5850", "9221", "8138"]
    response = client.post("/manual/lineup", json=payload)
    assert response.status_code == 200, response.get_data(as_text=True)
    body = response.get_json()
    assert body["free_agent_model"] == "not_available"
    assert body["free_agent_recs"] == {}


def test_manual_lineup_returns_clean_400(client):
    response = client.post("/manual/lineup", json={})
    assert response.status_code == 400
    assert response.get_json()["error"]