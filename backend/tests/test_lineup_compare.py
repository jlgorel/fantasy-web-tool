"""Tests for app.services.lineup_compare.

Two layers:
- ``annotate_lineup_deltas``: pure data transform — pair promoted vs demoted
  starters and stamp DELTA fields. Tested with synthetic rows.
- ``build_your_lineup``: integration smoke test against fixture blobs to
  guarantee shape parity with optimizer output.
"""
from __future__ import annotations

import pytest

from app.services.lineup_compare import _vegas_float, annotate_lineup_deltas, build_your_lineup
from app.services.boris_chen import prepare_boris_chen_tier_dict
from app.services.player_data import prepare_pid_to_name_dict


# ---------------------------------------------------------------------------
# _vegas_float coercion
# ---------------------------------------------------------------------------
class TestVegasFloat:
    def test_numeric_passthrough(self):
        assert _vegas_float({"VEGAS": 12.5}) == 12.5

    def test_string_pure_number(self):
        assert _vegas_float({"VEGAS": "12.34"}) == 12.34

    def test_string_with_old_projection_warning(self):
        # _format_starter_entry produces strings like
        # "12.34\t Old projection, no lines available, confirm uninjured"
        assert _vegas_float({"VEGAS": "12.34\t Old projection..."}) == 12.34

    def test_na_string(self):
        # DEF / K rows
        assert _vegas_float({"VEGAS": "N/A"}) == 0.0

    def test_missing_field(self):
        assert _vegas_float({}) == 0.0


# ---------------------------------------------------------------------------
# annotate_lineup_deltas
# ---------------------------------------------------------------------------
class TestAnnotateLineupDeltas:
    def test_pure_intra_starter_shuffle_no_annotations(self, make_row):
        # Same set of starters, just slot positions different — should NOT be
        # flagged. Otherwise WR1<->WR2 swaps would noise up the UI.
        your = [
            make_row("RB", "CMC", reallife="RB", vegas=20),
            make_row("FLEX", "Saquon", reallife="RB", vegas=18),
        ]
        opt = [
            make_row("RB", "Saquon", reallife="RB", vegas=18),
            make_row("FLEX", "CMC", reallife="RB", vegas=20),
        ]
        annotate_lineup_deltas(opt, your)
        for r in opt:
            assert "DELTA_VS_YOUR_LINEUP" not in r
            assert "DELTA_VS_PLAYER" not in r

    def test_bench_promoted_starter_gets_delta(self, make_row):
        your = [
            make_row("RB", "Pollard", reallife="RB", vegas=8),
            make_row("WR", "Shakir", reallife="WR", vegas=6),
        ]
        opt = [
            make_row("RB", "CMC", reallife="RB", vegas=20),  # promoted
            make_row("WR", "Shakir", reallife="WR", vegas=6),
        ]
        annotate_lineup_deltas(opt, your)
        cmc = next(r for r in opt if r["NAME"] == "CMC")
        assert cmc["DELTA_VS_PLAYER"] == "Pollard"
        assert cmc["DELTA_VS_YOUR_LINEUP"] == pytest.approx(12.0)
        # Unchanged starter is NOT annotated
        shakir = next(r for r in opt if r["NAME"] == "Shakir")
        assert "DELTA_VS_YOUR_LINEUP" not in shakir

    def test_pairing_prefers_same_position(self, make_row):
        # Promoted RB should pair with demoted RB even when a higher-VEGAS WR
        # was also demoted (eligible by the Flex rules but not strict-pos).
        your = [
            make_row("RB", "Pollard", reallife="RB", vegas=8),
            make_row("WR", "Shakir", reallife="WR", vegas=14),
        ]
        opt = [
            make_row("RB", "CMC", reallife="RB", vegas=20),
            make_row("WR", "Jefferson", reallife="WR", vegas=22),
        ]
        annotate_lineup_deltas(opt, your)
        cmc = next(r for r in opt if r["NAME"] == "CMC")
        # CMC should pair with Pollard (same-pos eligible) — not Shakir.
        assert cmc["DELTA_VS_PLAYER"] == "Pollard"

    def test_highest_vegas_promoted_picks_first(self, make_row):
        # Two promoted players, two demoted. The bigger swap (Jefferson) gets
        # paired with the bigger demoted (Shakir) for the most informative
        # delta.
        your = [
            make_row("WR", "Shakir", reallife="WR", vegas=14),
            make_row("WR", "Wandale", reallife="WR", vegas=4),
        ]
        opt = [
            make_row("WR", "Jefferson", reallife="WR", vegas=22),
            make_row("WR", "Hollywood", reallife="WR", vegas=10),
        ]
        annotate_lineup_deltas(opt, your)
        jj = next(r for r in opt if r["NAME"] == "Jefferson")
        hw = next(r for r in opt if r["NAME"] == "Hollywood")
        # Higher-VEGAS promoted (Jefferson) chose Shakir (highest demoted).
        assert jj["DELTA_VS_PLAYER"] == "Shakir"
        assert hw["DELTA_VS_PLAYER"] == "Wandale"

    def test_no_promotions_no_changes(self, make_row):
        your = [make_row("RB", "CMC", reallife="RB", vegas=20)]
        opt = [make_row("RB", "CMC", reallife="RB", vegas=20)]
        annotate_lineup_deltas(opt, your)
        assert "DELTA_VS_YOUR_LINEUP" not in opt[0]


# ---------------------------------------------------------------------------
# build_your_lineup integration
# ---------------------------------------------------------------------------
class TestBuildYourLineup:
    def test_returns_none_when_no_starters_field(self, half_ppr_settings):
        rosters = [{
            "league": "noStarters",
            "pids": [],
            "settings": half_ppr_settings,
            "positions": ["QB", "RB", "BN"],
            "all_owned": [],
            # Note: no 'starters' key at all (Fleaflicker shape)
        }]
        out = build_your_lineup(rosters, name_to_pid={}, boris_chen_tiers={})
        assert out["noStarters"] is None

    def test_builds_rows_for_real_pids(self, half_ppr_settings):
        # Use the real fixture players catalog and pick a known PID.
        pid_to_player, name_to_pid = prepare_pid_to_name_dict()
        boris = prepare_boris_chen_tier_dict()

        # Pick a few well-known players that exist in the snapshot.
        target_names = ["Patrick Mahomes", "Christian McCaffrey", "Justin Jefferson"]
        pids = [name_to_pid[n] for n in target_names if n in name_to_pid]
        assert len(pids) >= 2, "fixture should still contain Mahomes / CMC / JJ"

        rosters = [{
            "league": "L",
            "pids": pids,
            "settings": half_ppr_settings,
            "positions": ["QB", "RB", "WR"][: len(pids)],
            "all_owned": pids,
            "starters": pids,
        }]
        out = build_your_lineup(rosters, name_to_pid, boris)
        rows = out["L"]
        assert rows is not None
        # Every starter row carries the optimizer-shape keys (POS, NAME, etc).
        starters = [r for r in rows if r["POS"] != "BN"]
        assert len(starters) == len(pids)
        for row in starters:
            assert "POS" in row and "NAME" in row
            assert "VEGAS" in row  # numeric or "N/A"
