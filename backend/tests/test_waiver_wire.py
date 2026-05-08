"""Tests for app.services.waiver_wire.get_waiver_wire."""
from __future__ import annotations

import pytest

from app.services import waiver_wire


class TestWaiverWire:
    def test_default_variant_returns_payload(self):
        payload = waiver_wire.get_waiver_wire()
        assert payload["variant"] == "halfppr_4ptpass"
        assert "by_position" in payload
        # Always exposes the four FA-relevant positions, even if some are empty.
        assert set(payload["by_position"].keys()) == {"QB", "RB", "WR", "TE"}

    def test_unknown_variant_falls_back(self):
        payload = waiver_wire.get_waiver_wire(variant="not_a_thing")
        assert payload["variant"] == "halfppr_4ptpass"

    def test_top_n_caps_each_position(self):
        payload = waiver_wire.get_waiver_wire(top_n=3, max_owned_pct=100.0)
        for pos, rows in payload["by_position"].items():
            assert len(rows) <= 3, f"{pos} bucket exceeded top_n cap"

    def test_max_owned_filters(self):
        # With max_owned=0 we should get nothing (every player owned >= 0%).
        # The default-zero behavior in waiver_wire treats missing ownership
        # as 0, so they'd survive — but `>=` filter skips them. Verify.
        payload_zero = waiver_wire.get_waiver_wire(max_owned_pct=0.0, top_n=50)
        all_rows_zero = [r for rows in payload_zero["by_position"].values() for r in rows]
        for row in all_rows_zero:
            # Anyone surviving must have ownership strictly less than 0 — and
            # since negative ownership doesn't exist, the bucket must be empty
            # of any owned player. We assert no row above 0 sneaks through.
            assert row["OWNED_PCT"] < 0.0 or row["OWNED_PCT"] == 0.0

    def test_high_threshold_returns_more(self):
        few = waiver_wire.get_waiver_wire(max_owned_pct=10.0, top_n=10)
        many = waiver_wire.get_waiver_wire(max_owned_pct=99.0, top_n=10)

        few_total = sum(len(v) for v in few["by_position"].values())
        many_total = sum(len(v) for v in many["by_position"].values())
        # Loosening the ownership ceiling can only equal or grow the result.
        assert many_total >= few_total

    def test_rows_have_owned_pct_and_skip_zero_vegas(self):
        payload = waiver_wire.get_waiver_wire(max_owned_pct=99.0, top_n=20)
        for pos, rows in payload["by_position"].items():
            for r in rows:
                assert "OWNED_PCT" in r
                # zero-VEGAS rows are dropped to filter out injured/inactive noise
                assert isinstance(r.get("VEGAS"), (int, float))
                assert r["VEGAS"] > 0
