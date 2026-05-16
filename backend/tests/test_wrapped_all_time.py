"""Tests for the all-time Wrapped aggregator (TODO #5).

We don't exercise the live Sleeper chain or pipeline here — both are
already covered elsewhere. These tests verify that, given a synthetic
list of per-year payloads, ``_aggregate`` computes the right cross-year
winners, and that ``build_all_time_payload`` calls
``get_league_season_chain`` + ``compute_wrapped`` correctly.
"""
from __future__ import annotations

from typing import Any, Dict

import pytest

from app.services.wrapped import all_time


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------
def _payload(
    *,
    user_id_to_username: Dict[str, str],
    luckiest: tuple | None = None,        # (username, count)
    unluckiest: tuple | None = None,
    luck_by_user: Dict[str, Dict[str, int]] | None = None,
    eff_by_user: Dict[str, float] | None = None,
    troll: Dict[str, Dict[str, Any]] | None = None,  # username -> entry
    trades_by_user: Dict[str, Dict[str, Any]] | None = None,
) -> Dict[str, Any]:
    """Build a synthetic per-year payload.

    ``luck_by_user`` is the new shape (per-user raw counts the
    aggregator sums across seasons). When omitted, the helper derives a
    sensible default from ``luckiest`` / ``unluckiest`` so the existing
    tests still exercise the new code path.
    """
    if luck_by_user is None:
        derived: Dict[str, Dict[str, int]] = {}
        if luckiest:
            derived.setdefault(luckiest[0], {})["lucky_wins"] = int(luckiest[1])
        if unluckiest:
            derived.setdefault(unluckiest[0], {})["unlucky_losses"] = int(unluckiest[1])
        luck_by_user = derived
    return {
        "meta": {"user_id_to_username": dict(user_id_to_username)},
        "schedule": {
            "luck": {
                "luckiest": (
                    {"username": luckiest[0], "count": luckiest[1]}
                    if luckiest else {"username": None, "count": 0}
                ),
                "unluckiest": (
                    {"username": unluckiest[0], "count": unluckiest[1]}
                    if unluckiest else {"username": None, "count": 0}
                ),
                "by_user": {
                    user: {
                        "lucky_wins": int(stats.get("lucky_wins", 0)),
                        "unlucky_losses": int(stats.get("unlucky_losses", 0)),
                    }
                    for user, stats in luck_by_user.items()
                },
            },
            "manager_efficiency": {
                "by_user": dict(eff_by_user or {}),
                "most_efficient": None,
                "least_efficient": None,
            },
        },
        "roster_moves": {"troll": dict(troll or {})},
        "trades": {"by_user": dict(trades_by_user or {})},
    }


def _entry(year: str, lid: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    return {"year": year, "league_id": lid, "payload": payload}


# ---------------------------------------------------------------------------
# _aggregate
# ---------------------------------------------------------------------------
def test_single_year_chain_collapses_to_that_year():
    payload = _payload(
        user_id_to_username={"u1": "alice", "u2": "bob"},
        luckiest=("alice", 3),
        unluckiest=("bob", 2),
        eff_by_user={"alice": 80.0, "bob": 70.0},
        troll={
            "alice": {"player_id": "p1", "name": "WR1", "troll_value": 4.5,
                      "num_start": 5, "num_bench": 2,
                      "start_avg": 8.0, "bench_avg": 12.5},
            "bob": None,
        },
        trades_by_user={
            "alice": {"num_trades": 2, "net_ktc_per_season": 10.0},
            "bob": {"num_trades": 1, "net_ktc_per_season": -10.0},
        },
    )
    result = all_time._aggregate([_entry("2025", "L1", payload)])

    assert result["luckiest"] == {
        "username": "alice", "user_id": "u1",
        "lucky_wins": 3, "seasons": 1,
    }
    assert result["unluckiest"] == {
        "username": "bob", "user_id": "u2",
        "unlucky_losses": 2, "seasons": 1,
    }
    assert result["worst_start_sit"]["username"] == "alice"
    assert result["worst_start_sit"]["user_id"] == "u1"
    assert result["worst_start_sit"]["total_troll_value"] == 4.5
    assert result["worst_start_sit"]["years_counted"] == 1

    assert result["most_efficient"]["username"] == "alice"
    assert result["most_efficient"]["avg_efficiency_pct"] == 80.0
    assert result["least_efficient"]["username"] == "bob"
    assert result["least_efficient"]["avg_efficiency_pct"] == 70.0

    assert result["most_active_trader"]["username"] == "alice"
    assert result["most_active_trader"]["total_trades"] == 2
    assert result["biggest_net_gainer"]["username"] == "alice"
    assert result["biggest_net_gainer"]["net_ktc_per_season"] == 10.0
    assert result["biggest_net_loser"]["username"] == "bob"
    assert result["biggest_net_loser"]["net_ktc_per_season"] == -10.0


def test_multi_year_aggregation_sums_correctly():
    # alice trolls more total but bob trolls in more years; alice wins on
    # total magnitude. Trade counts: bob 4 total, alice 3.
    y2025 = _payload(
        user_id_to_username={"u1": "alice", "u2": "bob"},
        luckiest=("alice", 4),
        unluckiest=("bob", 1),
        eff_by_user={"alice": 90.0, "bob": 60.0},
        troll={"alice": {"troll_value": 5.0, "player_id": "p", "name": "p",
                         "num_start": 5, "num_bench": 2,
                         "start_avg": 1, "bench_avg": 1}},
        trades_by_user={
            "alice": {"num_trades": 1, "net_ktc_per_season": 5.0},
            "bob": {"num_trades": 2, "net_ktc_per_season": -3.0},
        },
    )
    y2024 = _payload(
        user_id_to_username={"u1": "alice", "u2": "bob"},
        luckiest=("bob", 2),
        unluckiest=("bob", 3),
        eff_by_user={"alice": 70.0, "bob": 80.0},
        troll={
            "alice": {"troll_value": 4.0, "player_id": "p", "name": "p",
                      "num_start": 5, "num_bench": 2,
                      "start_avg": 1, "bench_avg": 1},
            "bob":   {"troll_value": 2.0, "player_id": "p", "name": "p",
                      "num_start": 5, "num_bench": 2,
                      "start_avg": 1, "bench_avg": 1},
        },
        trades_by_user={
            "alice": {"num_trades": 2, "net_ktc_per_season": 1.0},
            "bob": {"num_trades": 2, "net_ktc_per_season": 4.0},
        },
    )
    result = all_time._aggregate([_entry("2025", "L2", y2025),
                                  _entry("2024", "L1", y2024)])

    # Luck: alice 4 lucky wins (1 season), bob 2 (1 season) -> alice
    assert result["luckiest"]["username"] == "alice"
    assert result["luckiest"]["lucky_wins"] == 4
    assert result["luckiest"]["seasons"] == 1
    # Bob: 1 + 3 = 4 unlucky losses across 2 seasons
    assert result["unluckiest"]["username"] == "bob"
    assert result["unluckiest"]["unlucky_losses"] == 4
    assert result["unluckiest"]["seasons"] == 2

    # worst_start_sit: alice 5+4=9, bob 2 → alice
    assert result["worst_start_sit"]["username"] == "alice"
    assert result["worst_start_sit"]["total_troll_value"] == 9.0
    assert result["worst_start_sit"]["years_counted"] == 2

    # avg efficiency: alice (90+70)/2=80, bob (60+80)/2=70 → alice most, bob least
    assert result["most_efficient"]["username"] == "alice"
    assert result["most_efficient"]["avg_efficiency_pct"] == 80.0
    assert result["least_efficient"]["username"] == "bob"
    assert result["least_efficient"]["avg_efficiency_pct"] == 70.0

    # most_active_trader: alice 1+2=3, bob 2+2=4 → bob
    assert result["most_active_trader"]["username"] == "bob"
    assert result["most_active_trader"]["total_trades"] == 4

    # net: alice 5+1=6, bob -3+4=1 → alice gainer, no loser (no negative net)
    assert result["biggest_net_gainer"]["username"] == "alice"
    assert result["biggest_net_gainer"]["net_ktc_per_season"] == 6.0
    assert result["biggest_net_loser"] is None


def test_user_id_buckets_across_display_name_change():
    # Alice rebrands to 'alicia' between seasons; the aggregator should
    # treat them as one user (same user_id u1) and render the most recent
    # display_name (which is the newest-first-iterated one — 'alicia' in
    # 2025).
    y2025 = _payload(  # newest
        user_id_to_username={"u1": "alicia", "u2": "bob"},
        luckiest=("alicia", 1),
        eff_by_user={"alicia": 75.0, "bob": 65.0},
        troll={"alicia": {"troll_value": 3.0, "player_id": "p", "name": "p",
                          "num_start": 5, "num_bench": 2,
                          "start_avg": 1, "bench_avg": 1}},
        trades_by_user={"alicia": {"num_trades": 2, "net_ktc_per_season": 5.0}},
    )
    y2024 = _payload(  # older
        user_id_to_username={"u1": "alice", "u2": "bob"},
        luckiest=("alice", 1),
        eff_by_user={"alice": 85.0, "bob": 55.0},
        troll={"alice": {"troll_value": 2.0, "player_id": "p", "name": "p",
                         "num_start": 5, "num_bench": 2,
                         "start_avg": 1, "bench_avg": 1}},
        trades_by_user={"alice": {"num_trades": 1, "net_ktc_per_season": 3.0}},
    )
    result = all_time._aggregate([_entry("2025", "L2", y2025),
                                  _entry("2024", "L1", y2024)])

    # Two seasons merged under user_id u1, rendered with newest display_name
    assert result["luckiest"]["user_id"] == "u1"
    assert result["luckiest"]["username"] == "alicia"
    assert result["luckiest"]["lucky_wins"] == 2
    assert result["luckiest"]["seasons"] == 2

    # Troll values summed across the rename
    assert result["worst_start_sit"]["user_id"] == "u1"
    assert result["worst_start_sit"]["username"] == "alicia"
    assert result["worst_start_sit"]["total_troll_value"] == 5.0
    assert result["worst_start_sit"]["years_counted"] == 2

    # Efficiency averaged across the rename
    assert result["most_efficient"]["user_id"] == "u1"
    assert result["most_efficient"]["avg_efficiency_pct"] == 80.0
    assert result["most_efficient"]["years_counted"] == 2


def test_legacy_payload_without_user_id_map_falls_back_to_display_name():
    payload = {
        "meta": {},  # no user_id_to_username (pre-Phase-4 cache)
        "schedule": {
            "luck": {
                "luckiest": {"username": "alice", "count": 2},
                "unluckiest": {"username": None, "count": 0},
            },
            "manager_efficiency": {"by_user": {"alice": 75.0}},
        },
        "roster_moves": {"troll": {}},
        "trades": {"by_user": {}},
    }
    result = all_time._aggregate([_entry("2025", "L1", payload)])
    assert result["luckiest"]["username"] == "alice"
    assert result["luckiest"]["user_id"] is None  # fallback bucket
    assert result["most_efficient"]["username"] == "alice"
    assert result["most_efficient"]["user_id"] is None


def test_empty_aggregates_when_chain_empty(monkeypatch):
    monkeypatch.setattr(all_time, "get_league_season_chain", lambda _lid: [])
    out = all_time.build_all_time_payload("nonexistent")
    assert out["mode"] == "all_time"
    assert out["years"] == []
    assert out["all_time"]["luckiest"] is None
    assert out["all_time"]["worst_start_sit"] is None


def test_build_all_time_skips_failing_year(monkeypatch):
    monkeypatch.setattr(
        all_time, "get_league_season_chain",
        lambda _lid: [
            {"season": "2025", "league_id": "L2"},
            {"season": "2024", "league_id": "L1"},
        ],
    )
    good_payload = _payload(
        user_id_to_username={"u1": "alice"},
        luckiest=("alice", 2),
    )

    def fake_compute(lid, year):
        if lid == "L1":
            raise RuntimeError("simulated upstream failure")
        return good_payload

    monkeypatch.setattr(all_time, "compute_wrapped", fake_compute)

    out = all_time.build_all_time_payload("L2")
    assert out["mode"] == "all_time"
    # Only the good year survives
    assert [y["league_id"] for y in out["years"]] == ["L2"]
    assert out["all_time"]["luckiest"]["username"] == "alice"


def test_build_all_time_walks_chain_in_order(monkeypatch):
    monkeypatch.setattr(
        all_time, "get_league_season_chain",
        lambda _lid: [
            {"season": "2025", "league_id": "Lnew"},
            {"season": "2024", "league_id": "Lold"},
        ],
    )
    calls = []

    def fake_compute(lid, year):
        calls.append((lid, year))
        return _payload(user_id_to_username={"u1": "alice"})

    monkeypatch.setattr(all_time, "compute_wrapped", fake_compute)

    out = all_time.build_all_time_payload("Lnew")
    assert calls == [("Lnew", "2025"), ("Lold", "2024")]
    assert [y["year"] for y in out["years"]] == ["2025", "2024"]
