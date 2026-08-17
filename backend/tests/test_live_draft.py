"""Pure and route tests for the read-only Sleeper live draft lobby."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app import routes
from app.services.draft_help import live_draft

FIXTURE = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / \
    "sleeper_live_draft_1392134959602356224.json"


@pytest.fixture
def supplied():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_supplied_paused_mock_builds_expected_state(supplied):
    state = live_draft.build_live_draft_state(
        supplied["draft"], supplied["picks"], supplied["traded_picks"],
        selected_slot=2,
    )
    assert state["draft_id"] == "1392134959602356224"
    assert state["status"] == "paused"
    assert state["current_pick"] == 8
    assert state["on_clock_slot"] == 8
    assert state["user_slot"] == 2
    assert state["my_roster_ids"] == ["9221"]
    assert state["my_upcoming_picks"][0] == 23
    assert state["picks_until_user"] == 15
    assert state["poll_interval_ms"] == 5000
    assert state["config"] == {
        "teams": 12,
        "rounds": 15,
        "bench_size": 6,
        "ppr": 0.5,
        "superflex": False,
        "slots": {
            "QB": 1, "RB": 2, "WR": 2, "TE": 1,
            "FLEX": 1, "REC_FLEX": 1,
        },
    }


def test_username_auto_resolves_slot(supplied):
    state = live_draft.build_live_draft_state(
        supplied["draft"], supplied["picks"], [],
        user_id=supplied["user"]["user_id"],
    )
    assert state["user_slot"] == 2
    assert state["needs_slot"] is False
    assert state["my_roster_ids"] == ["9221"]


def test_missing_identity_requests_manual_slot(supplied):
    state = live_draft.build_live_draft_state(
        supplied["draft"], supplied["picks"], [],
    )
    assert state["needs_slot"] is True
    assert state["user_slot"] is None
    assert state["available_slots"] == list(range(1, 13))


def test_traded_pick_changes_explicit_future_schedule(supplied):
    # User roster 2 sends its R2 to roster 3, and receives roster 3's R2.
    trades = [
        {"round": 2, "roster_id": 2, "owner_id": 3},
        {"round": 2, "roster_id": 3, "owner_id": 2},
    ]
    state = live_draft.build_live_draft_state(
        supplied["draft"], supplied["picks"], trades, selected_slot=2,
    )
    assert 23 not in state["my_upcoming_picks"]
    assert state["my_upcoming_picks"][0] == 22
    assert state["picks_until_user"] == 14


def test_traded_current_pick_marks_user_on_clock(supplied):
    state = live_draft.build_live_draft_state(
        supplied["draft"], supplied["picks"],
        [{"round": 1, "roster_id": 8, "owner_id": 2}],
        selected_slot=2,
    )
    assert state["current_pick"] == 8
    assert state["on_clock_slot"] == 8
    assert state["is_user_pick"] is True
    assert state["picks_until_user"] == 0

    picks = supplied["picks"] + [{
        "pick_no": 8, "round": 1, "draft_slot": 8,
        "player_id": "traded-player", "metadata": {"position": "WR"},
    }]
    after_pick = live_draft.build_live_draft_state(
        supplied["draft"], picks,
        [{"round": 1, "roster_id": 8, "owner_id": 2}],
        selected_slot=2,
    )
    assert "traded-player" in after_pick["my_roster_ids"]


def test_first_missing_pick_ignores_future_keeper(supplied):
    picks = supplied["picks"] + [{
        "pick_no": 30, "round": 3, "draft_slot": 6,
        "player_id": "keeper", "is_keeper": True, "metadata": {},
    }]
    state = live_draft.build_live_draft_state(
        supplied["draft"], picks, [], selected_slot=2,
    )
    assert state["current_pick"] == 8


def test_unsupported_draft_types_are_rejected(supplied):
    detail = dict(supplied["draft"])
    detail["type"] = "auction"
    with pytest.raises(live_draft.LiveDraftError, match="snake"):
        live_draft.build_live_draft_state(detail, [], [], selected_slot=1)


def test_choose_league_draft_prefers_paused_over_predraft(supplied):
    predraft = dict(supplied["draft"], draft_id="pre", status="pre_draft", created=2)
    paused = dict(supplied["draft"], draft_id="paused", status="paused", created=1)
    selected = live_draft.choose_league_draft([predraft, paused])
    assert selected["draft_id"] == "paused"


def test_direct_route_and_conditional_poll(client, supplied, monkeypatch):
    pick_calls = []
    traded_calls = []
    monkeypatch.setattr(routes.draft_help_fetch, "fetch_draft_detail", lambda _id: supplied["draft"])
    monkeypatch.setattr(
        routes.draft_help_fetch, "fetch_draft_picks",
        lambda _id: pick_calls.append(_id) or supplied["picks"],
    )
    monkeypatch.setattr(
        routes.draft_help_fetch, "fetch_draft_traded_picks",
        lambda _id: traded_calls.append(_id) or [],
    )
    monkeypatch.setattr(
        routes.draft_help_fetch, "resolve_user_id",
        lambda username: supplied["user"]["user_id"] if username == "jlgorel" else None,
    )

    full = client.get(
        "/draft-help/live/draft/1392134959602356224?username=jlgorel"
    )
    assert full.status_code == 200
    assert full.get_json()["user_slot"] == 2
    unchanged = client.get(
        "/draft-help/live/draft/1392134959602356224"
        "?known_last_picked=1786273359868&known_status=paused"
    )
    assert unchanged.status_code == 200
    assert unchanged.get_json()["changed"] is False
    assert len(pick_calls) == 1
    assert len(traded_calls) == 1

    predraft = dict(supplied["draft"], status="pre_draft", last_picked=None)
    monkeypatch.setattr(routes.draft_help_fetch, "fetch_draft_detail", lambda _id: predraft)
    empty_unchanged = client.get(
        "/draft-help/live/draft/1392134959602356224"
        "?known_last_picked=null&known_status=pre_draft"
    )
    assert empty_unchanged.status_code == 200
    assert empty_unchanged.get_json()["changed"] is False
    assert len(pick_calls) == 1


def test_league_route_finds_active_draft(client, supplied, monkeypatch):
    monkeypatch.setattr(
        routes.draft_help_fetch, "fetch_league",
        lambda _id: {"settings": {"type": 0}},
    )
    monkeypatch.setattr(
        routes.draft_help_fetch, "fetch_league_drafts",
        lambda _id: [supplied["draft"]],
    )
    monkeypatch.setattr(routes.draft_help_fetch, "fetch_draft_detail", lambda _id: supplied["draft"])
    monkeypatch.setattr(routes.draft_help_fetch, "fetch_draft_picks", lambda _id: supplied["picks"])
    monkeypatch.setattr(routes.draft_help_fetch, "fetch_draft_traded_picks", lambda _id: [])
    monkeypatch.setattr(routes.draft_help_fetch, "resolve_user_id", lambda _name: supplied["user"]["user_id"])

    response = client.get(
        "/draft-help/live/league/1383556954865016832?username=jlgorel"
    )
    assert response.status_code == 200
    assert response.get_json()["draft_id"] == supplied["draft"]["draft_id"]


def test_direct_route_treats_missing_upstream_response_as_transient(client, monkeypatch):
    monkeypatch.setattr(routes.draft_help_fetch, "fetch_draft_detail", lambda _id: None)
    response = client.get("/draft-help/live/draft/temporary-failure")
    assert response.status_code == 503
    assert "retry" in response.get_json()["detail"].lower()
