"""Tests for app.services.risers_fallers.get_risers_fallers."""
from __future__ import annotations

from app.services import risers_fallers


class TestRisersFallers:
    def test_default_variant_runs(self):
        payload = risers_fallers.get_risers_fallers()
        # Either we have history (available=True) or the friendly no-snapshot
        # message — never a crash.
        assert payload["variant"] == "halfppr_4ptpass"
        assert isinstance(payload.get("risers"), list)
        assert isinstance(payload.get("fallers"), list)

    def test_unknown_variant_falls_back(self):
        payload = risers_fallers.get_risers_fallers(variant="bogus")
        assert payload["variant"] == "halfppr_4ptpass"

    def test_risers_sorted_descending_by_delta(self):
        payload = risers_fallers.get_risers_fallers(top_n=10)
        if not payload.get("available"):
            return  # no history yet — nothing to assert
        risers = payload["risers"]
        deltas = [r["DELTA"] for r in risers]
        assert deltas == sorted(deltas, reverse=True)
        # All risers have positive delta
        assert all(d > 0 for d in deltas)

    def test_fallers_sorted_ascending_by_delta(self):
        payload = risers_fallers.get_risers_fallers(top_n=10)
        if not payload.get("available"):
            return
        fallers = payload["fallers"]
        deltas = [r["DELTA"] for r in fallers]
        assert deltas == sorted(deltas)
        assert all(d < 0 for d in deltas)

    def test_top_n_caps(self):
        payload = risers_fallers.get_risers_fallers(top_n=3)
        if not payload.get("available"):
            return
        assert len(payload["risers"]) <= 3
        assert len(payload["fallers"]) <= 3

    def test_rows_have_required_keys(self):
        payload = risers_fallers.get_risers_fallers(top_n=5)
        if not payload.get("available"):
            return
        for row in payload["risers"] + payload["fallers"]:
            for key in ("PID", "NAME", "VEGAS", "PREV_VEGAS", "DELTA"):
                assert key in row, f"missing {key} in mover row"
