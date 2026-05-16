"""Tests for the pick-handoff splicing + Sleeper trade loader.

Like the other ``test_trade_eval_*`` modules, we mutate ``sys.path`` to
import the package from ``azure-functions/`` since it lives outside the
backend package proper.

All tests are 100% offline -- they read the snapshot fixture saved at
``tests/fixtures/sleeper_league/1312205344964898816_chain/data.json``
and the historical KTC blob at
``tests/fixtures/blobs/historical_KTC_rankings.json``.
"""
from __future__ import annotations

import importlib
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
AZURE_FN_DIR = REPO_ROOT / "azure-functions"
FIXTURE_CHAIN = REPO_ROOT / "tests" / "fixtures" / "sleeper_league" / \
    "1312205344964898816_chain" / "data.json"
FIXTURE_KTC = REPO_ROOT / "tests" / "fixtures" / "blobs" / \
    "historical_KTC_rankings.json"


# ---------------------------------------------------------------------------
# Module loader (mirrors the other trade_eval test files)
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def te():
    saved_path = list(sys.path)
    targets = (
        "config", "_fantasy_common", "trade_eval",
        "trade_eval.active_window",
        "trade_eval.value_integral",
        "trade_eval.trade_evaluator",
        "trade_eval.pick_handoff",
        "trade_eval.sleeper_trade_loader",
        "trade_eval.sleeper_trade_adapter",
    )
    saved_mods = {name: sys.modules.get(name) for name in targets}
    for name in targets:
        sys.modules.pop(name, None)
    sys.path.insert(0, str(AZURE_FN_DIR))
    try:
        yield {
            "vi": importlib.import_module("trade_eval.value_integral"),
            "ev": importlib.import_module("trade_eval.trade_evaluator"),
            "ph": importlib.import_module("trade_eval.pick_handoff"),
            "stl": importlib.import_module("trade_eval.sleeper_trade_loader"),
            "sta": importlib.import_module("trade_eval.sleeper_trade_adapter"),
        }
    finally:
        sys.path[:] = saved_path
        for name, mod in saved_mods.items():
            if mod is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = mod


# ---------------------------------------------------------------------------
# Fixture loaders
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def chain(te):
    SeasonContext = te["stl"].SeasonContext
    raw = json.loads(FIXTURE_CHAIN.read_text(encoding="utf-8"))
    return [SeasonContext(**season) for season in raw["chain"]]


@pytest.fixture(scope="module")
def chain_by_season(chain):
    return {ctx.season: ctx for ctx in chain}


@pytest.fixture(scope="module")
def ktc_blob():
    if not FIXTURE_KTC.exists():
        pytest.skip(
            "historical KTC blob not present -- run "
            "tools/build_historical_ktc_json.py to build it"
        )
    return json.loads(FIXTURE_KTC.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def flat_blob(te, ktc_blob):
    return te["ph"].flatten_value_blob(ktc_blob, fmt="1qb")


@pytest.fixture(scope="module")
def base_resolver(te, flat_blob):
    return te["ev"].make_blob_resolver(flat_blob, max_stale_days=30)


# ---------------------------------------------------------------------------
# Pick-key encode/decode
# ---------------------------------------------------------------------------
class TestPickKey:
    def test_round_trip(self, te):
        key = te["ph"].encode_pick_key("2024", 2, 7)
        assert te["ph"].parse_pick_key(key) == ("2024", 2, 7)

    def test_parse_none(self, te):
        assert te["ph"].parse_pick_key(None) is None

    def test_parse_non_pick_string(self, te):
        # Plain sleeper player id shouldn't be misread as a pick key.
        assert te["ph"].parse_pick_key("4035") is None

    def test_pick_blob_id_known_slot(self, te):
        f = te["ph"].pick_blob_id
        assert f("2025", 1, 2) == "pick:2025_early_1st"
        assert f("2025", 1, 5) == "pick:2025_mid_1st"
        assert f("2025", 1, 9) == "pick:2025_late_1st"

    def test_pick_blob_id_unknown_slot(self, te):
        assert te["ph"].pick_blob_id("2027", 2, None) == "pick:2027_mid_2nd"


# ---------------------------------------------------------------------------
# splice_series unit tests
# ---------------------------------------------------------------------------
class TestSpliceSeries:
    def test_basic_handoff(self, te):
        VS = te["vi"].ValueSeries
        pre = VS.from_mapping({
            "2024-01-01": 5000.0,
            "2024-04-01": 7000.0,
            "2024-05-01": 7200.0,
        })
        post = VS.from_mapping({
            "2024-04-26": 9000.0,
            "2024-06-01": 9500.0,
        })
        spliced = te["ph"].splice_series(pre, post, date(2024, 4, 25))
        # Before cutoff -> pick value.
        assert spliced.value_on(date(2024, 4, 1)) == 7000.0
        assert spliced.value_on(date(2024, 4, 24)) == 7000.0
        # On/after cutoff -> drafted-player value.
        assert spliced.value_on(date(2024, 4, 25)) == 9000.0
        assert spliced.value_on(date(2024, 4, 26)) == 9000.0
        assert spliced.value_on(date(2024, 6, 15)) == 9500.0

    def test_post_has_no_data_yet_uses_first_known(self, te):
        VS = te["vi"].ValueSeries
        pre = VS.from_mapping({"2024-04-01": 7000.0})
        post = VS.from_mapping({"2024-04-27": 8800.0})
        spliced = te["ph"].splice_series(pre, post, date(2024, 4, 25))
        assert spliced.value_on(date(2024, 4, 25)) == 8800.0
        assert spliced.value_on(date(2024, 4, 26)) == 8800.0
        assert spliced.value_on(date(2024, 4, 27)) == 8800.0

    def test_post_empty_uses_last_pre_value(self, te):
        VS = te["vi"].ValueSeries
        pre = VS.from_mapping({"2024-04-01": 7000.0})
        post = VS.from_mapping({})
        spliced = te["ph"].splice_series(pre, post, date(2024, 4, 25))
        assert spliced.value_on(date(2024, 4, 25)) == 7000.0
        assert spliced.value_on(date(2024, 6, 1)) == 7000.0

    def test_cutoff_before_any_pre_data(self, te):
        VS = te["vi"].ValueSeries
        pre = VS.from_mapping({"2024-06-01": 100.0})
        post = VS.from_mapping({"2024-04-01": 200.0, "2024-05-01": 220.0})
        spliced = te["ph"].splice_series(pre, post, date(2024, 4, 25))
        # Cutoff before any pre entries: pre side is empty -> initial value.
        assert spliced.value_on(date(2024, 4, 1)) == 0.0
        # Post forward-fills from cutoff onward.
        assert spliced.value_on(date(2024, 4, 25)) == 200.0
        assert spliced.value_on(date(2024, 5, 15)) == 220.0


# ---------------------------------------------------------------------------
# Flat blob helper
# ---------------------------------------------------------------------------
class TestFlattenBlob:
    def test_flatten_players_and_picks(self, ktc_blob, flat_blob):
        # Picks survive the flattening and carry full date->value series.
        assert any(k.startswith("pick:") for k in flat_blob.keys())
        assert "pick:2026_early_1st" in flat_blob

        # Some non-pick record key from the new blob's records map.
        non_pick_keys = [
            k for k in ktc_blob["records"]
            if not ktc_blob["records"][k].get("is_pick")
        ]
        assert non_pick_keys, "blob should have at least one non-pick record"
        sample_pid = non_pick_keys[0]
        assert sample_pid in flat_blob

        # Pick payload is a flat date->value dict after flattening.
        sample_pick = flat_blob["pick:2026_early_1st"]
        assert isinstance(sample_pick, dict)
        for k, v in list(sample_pick.items())[:5]:
            assert isinstance(k, str) and isinstance(v, (int, float))

    def test_flatten_legacy_nested_shape_still_supported(self, te):
        """Ensure the helper still handles hand-crafted ``players``/``picks``
        blobs (used by other tests)."""
        legacy = {
            "format": "1qb",
            "players": {
                "100": {"name": "Test", "values": {"2024-01-01": 5000.0}},
            },
            "picks": {
                "pick:2024_mid_1st": {"label": "x", "values": {"2024-01-01": 4000.0}},
            },
        }
        flat = te["ph"].flatten_value_blob(legacy)
        assert flat["100"] == {"2024-01-01": 5000.0}
        assert flat["pick:2024_mid_1st"] == {"2024-01-01": 4000.0}

    def test_flatten_unknown_fmt_raises(self, te):
        with pytest.raises(ValueError):
            te["ph"].flatten_value_blob({"records": {}}, fmt="bogus")


# ---------------------------------------------------------------------------
# Normalized trade loading from saved fixture
# ---------------------------------------------------------------------------
class TestNormalizeAllTrades:
    def test_total_count_matches_probe(self, te, chain):
        normalized = te["stl"].normalize_all_trades(chain)
        # Probe counted 101 raw completed trades. 4 of those are FAAB-only
        # or one-sided "gift" trades where the loser receives nothing we
        # model (we don't track waiver_budget yet) -- the normalizer
        # correctly drops them rather than emit a 1-sided trade.
        assert len(normalized) == 97

    def test_every_trade_has_at_least_two_sides_with_assets(self, te, chain):
        for nt in te["stl"].normalize_all_trades(chain):
            assert len(nt.sides) >= 2
            for side in nt.sides:
                assert side.received_player_ids or side.received_picks

    def test_trade_dates_are_utc_datetimes(self, te, chain):
        for nt in te["stl"].normalize_all_trades(chain):
            assert isinstance(nt.trade_date, datetime)
            assert nt.trade_date.tzinfo is not None

    def test_picks_carry_season_round_original_owner(self, te, chain):
        any_pick = False
        for nt in te["stl"].normalize_all_trades(chain):
            for side in nt.sides:
                for p in side.received_picks:
                    any_pick = True
                    assert p.season.isdigit() and len(p.season) == 4
                    # 1-7 for rookie drafts; the 2022 startup auction
                    # logged trades against >7 "rounds" so just sanity-
                    # check positivity.
                    assert p.round >= 1
                    assert isinstance(p.original_roster_id, int)
        assert any_pick


class TestNormalizeTradeEdgeCases:
    def test_no_roster_ids_returns_none(self, te):
        assert te["stl"].normalize_trade(
            {"adds": {"1": 1}, "roster_ids": []},
            season="2024", league_id="x") is None

    def test_player_only_trade(self, te):
        raw = {
            "transaction_id": "abc",
            "roster_ids": [1, 2],
            "adds": {"100": 2, "200": 1},
            "drops": {"100": 1, "200": 2},
            "draft_picks": [],
            "status_updated": 1_700_000_000_000,
            "leg": 3,
        }
        nt = te["stl"].normalize_trade(raw, season="2024", league_id="L")
        assert nt is not None
        rid_to_side = {s.roster_id: s for s in nt.sides}
        assert rid_to_side[1].received_player_ids == ["200"]
        assert rid_to_side[2].received_player_ids == ["100"]


# ---------------------------------------------------------------------------
# Pick handoff table from the fixture
# ---------------------------------------------------------------------------
class TestPickHandoffTable:
    def test_table_built_only_for_realized_drafts(self, te, chain):
        table = te["stl"].build_pick_to_player(chain)
        # 2026 draft hasn't happened yet.
        assert not any(season == "2026" for (season, _, _) in table.keys())
        # 2023-2025 rookie drafts are 3 rounds * 10 teams = 30 picks/year.
        for yr in ("2023", "2024", "2025"):
            entries = [k for k in table if k[0] == yr]
            assert len(entries) == 30, f"{yr}: expected 30, got {len(entries)}"

    def test_each_entry_has_drafted_player(self, te, chain):
        table = te["stl"].build_pick_to_player(chain)
        for key, info in table.items():
            assert info["player_id"]
            assert info["draft_date"] is not None


# ---------------------------------------------------------------------------
# Pick-aware resolver: splice vs. raw pick
# ---------------------------------------------------------------------------
class TestPickAwareResolver:
    def test_unused_pick_falls_through(self, te, chain, base_resolver):
        table = te["stl"].build_pick_to_player(chain)
        wrapped = te["ph"].make_pick_aware_resolver(base_resolver, table)
        TA = te["ev"].TradeAsset
        asset = TA(
            asset_id="pick:2026_mid_1st",
            label="2026 R1 mid",
            sleeper_id=te["ph"].encode_pick_key("2026", 1, 5),
            is_pick=True,
        )
        wrapped_series = wrapped(asset)
        raw_series = base_resolver(asset)
        assert list(wrapped_series.sorted_dates) == list(raw_series.sorted_dates)

    def test_used_pick_gets_spliced(self, te, chain, chain_by_season,
                                     base_resolver, flat_blob):
        table = te["stl"].build_pick_to_player(chain)
        # Pick any 2024 entry whose drafted player has a KTC series.
        target_key = None
        target_info = None
        for key, info in table.items():
            if key[0] == "2024" and info["player_id"] in flat_blob:
                target_key = key
                target_info = info
                break
        assert target_key is not None

        season, round_, orig_rid = target_key
        ctx_2024 = chain_by_season["2024"]
        slot_to_roster = ctx_2024.draft["slot_to_roster_id"]
        slot = next(int(s) for s, r in slot_to_roster.items()
                    if int(r) == orig_rid)
        TA = te["ev"].TradeAsset
        asset = TA(
            asset_id=te["ph"].pick_blob_id(season, round_, slot),
            label=f"{season} R{round_}",
            sleeper_id=te["ph"].encode_pick_key(season, round_, orig_rid),
            is_pick=True,
        )
        wrapped = te["ph"].make_pick_aware_resolver(base_resolver, table)
        spliced = wrapped(asset)
        raw_pick = base_resolver(asset)

        draft_dt = target_info["draft_date"]
        if isinstance(draft_dt, str):
            draft_dt = datetime.fromisoformat(draft_dt.replace("Z", "+00:00"))
        cutoff = draft_dt.astimezone(timezone.utc).date() \
            if isinstance(draft_dt, datetime) else draft_dt

        # Post-draft: spliced should track the drafted player, not the pick.
        post_player = base_resolver(TA(
            asset_id=target_info["player_id"],
            sleeper_id=target_info["player_id"],
        ))
        for offset in (30, 90, 180):
            check = date.fromordinal(cutoff.toordinal() + offset)
            assert spliced.value_on(check) == post_player.value_on(check)

        # Pre-draft: spliced == raw pick line.
        pre_day = date.fromordinal(cutoff.toordinal() - 30)
        assert spliced.value_on(pre_day) == raw_pick.value_on(pre_day)


# ---------------------------------------------------------------------------
# End-to-end: build a Trade and evaluate it
# ---------------------------------------------------------------------------
class TestEvaluateRealTrade:
    def test_evaluate_first_trade_with_picks(
        self, te, chain, chain_by_season, base_resolver
    ):
        roster_labels = te["sta"].merged_roster_labels(chain)
        table = te["stl"].build_pick_to_player(chain)
        resolver = te["ph"].make_pick_aware_resolver(base_resolver, table)

        target = None
        for nt in te["stl"].normalize_all_trades(chain):
            if any(side.received_picks for side in nt.sides):
                target = nt
                break
        assert target is not None

        trade = te["sta"].build_trade(
            target,
            chain_by_season=chain_by_season,
            roster_labels=roster_labels,
            evaluation_end=date(2025, 11, 1),
        )
        result = te["ev"].evaluate_trade(trade, value_resolver=resolver)
        assert len(result.sides) == len(trade.sides)
        # Margins sum to ~zero in a 2-side trade.
        if len(result.sides) == 2:
            ms = list(result.margins.values())
            assert abs(ms[0] + ms[1]) < 1e-6

    def test_evaluate_trade_with_known_players_produces_positive_scores(
        self, te, chain, chain_by_season, base_resolver, flat_blob
    ):
        """End-to-end with a trade where both sides exchanged players
        whose Sleeper ids are in our historical KTC blob. Verifies the
        full pipeline (loader -> adapter -> evaluator) yields a non-zero
        score on at least one side."""
        roster_labels = te["sta"].merged_roster_labels(chain)
        table = te["stl"].build_pick_to_player(chain)
        resolver = te["ph"].make_pick_aware_resolver(base_resolver, table)

        target = None
        for nt in te["stl"].normalize_all_trades(chain):
            # Pick a 2023-or-earlier trade so the players are likely
            # already in the historical CSV ingest.
            if nt.season not in ("2023", "2024"):
                continue
            all_pids = [pid for s in nt.sides for pid in s.received_player_ids]
            if not all_pids:
                continue
            if all(pid in flat_blob for pid in all_pids):
                target = nt
                break
        assert target is not None, "no fully-covered trade found"

        trade = te["sta"].build_trade(
            target,
            chain_by_season=chain_by_season,
            roster_labels=roster_labels,
            evaluation_end=date(2025, 11, 1),
        )
        result = te["ev"].evaluate_trade(trade, value_resolver=resolver)
        assert any(s.total_score > 0 for s in result.sides)
        if len(result.sides) == 2:
            ms = list(result.margins.values())
            assert abs(ms[0] + ms[1]) < 1e-6


# ---------------------------------------------------------------------------
# Forward chain walk
# ---------------------------------------------------------------------------
class TestFindHeadLeagueId:
    def test_walks_forward_until_no_match(self, te):
        # Synthetic 3-season chain: 2023 -> 2024 -> 2025. Caller passes
        # the 2023 league_id; we should advance to 2025.
        leagues = {
            "L23": {"league_id": "L23", "season": 2023},
            "L24": {"league_id": "L24", "season": 2024, "previous_league_id": "L23"},
            "L25": {"league_id": "L25", "season": 2025, "previous_league_id": "L24"},
        }

        def fake_http(url):
            if url.endswith("/league/L23"):
                return leagues["L23"]
            if url.endswith("/league/L23/rosters"):
                return [{"roster_id": 1, "owner_id": "U1"}]
            if "/user/U1/leagues/nfl/2024" in url:
                return [leagues["L24"]]
            if "/user/U1/leagues/nfl/2025" in url:
                return [leagues["L25"]]
            if "/user/U1/leagues/nfl/" in url:
                return []
            return None

        head = te["stl"].find_head_league_id("L23", http=fake_http)
        assert head == "L25"

    def test_returns_starting_when_no_future_league(self, te):
        def fake_http(url):
            if url.endswith("/league/L"):
                return {"league_id": "L", "season": 2025}
            if url.endswith("/league/L/rosters"):
                return [{"roster_id": 1, "owner_id": "U1"}]
            return []  # no future leagues for user

        assert te["stl"].find_head_league_id("L", http=fake_http) == "L"

    def test_returns_starting_when_no_owner_id(self, te):
        def fake_http(url):
            if url.endswith("/league/L"):
                return {"league_id": "L", "season": 2024}
            if url.endswith("/league/L/rosters"):
                return [{"roster_id": 1, "owner_id": None}]
            return []

        assert te["stl"].find_head_league_id("L", http=fake_http) == "L"
