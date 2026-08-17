"""Pure safety tests for the weekly local DraftSheets Excel publisher."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
TOOL = REPO / "tools" / "refresh_draftsheets_values.py"


@pytest.fixture(scope="module")
def refresh_module():
    spec = importlib.util.spec_from_file_location("draftsheets_local_refresh_test", TOOL)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
        yield module
    finally:
        sys.modules.pop(spec.name, None)


def test_excel_values_are_rendered_like_google_display_values(refresh_module):
    row = [None] * 28
    row[1] = 1.0       # tier
    row[4] = 245.23    # points
    row[5] = 134.35    # value
    row[6] = 0.92237   # positional scarcity
    row[7] = 1.0       # ECR
    rendered = refresh_module._draftsheet_display_values([row])[0]
    assert rendered[1] == "1"
    assert rendered[4] == "245"
    assert rendered[5] == "134"
    assert rendered[6] == "92%"
    assert rendered[7] == "1"


def test_public_parity_rejects_recalculation_drift(refresh_module):
    profile_id = "qb1-rb2-wr2-te1-flex1-bn5-ptd4"
    key = "12|0.5|1qb"
    expected_players = [
        {"player_id": str(index), "vbd": float(100 - index), "pos": "RB"}
        for index in range(30)
    ]
    actual_players = [dict(row) for row in expected_players]
    public = {
        "profile": {"id": profile_id},
        "configs": {key: {"players": expected_players}},
    }
    generated = {
        profile_id: {"configs": {key: {"players": actual_players}}},
    }
    refresh_module._validate_public_parity(generated, public)
    actual_players[0]["vbd"] += 1
    with pytest.raises(ValueError, match="does not match public DraftSheets"):
        refresh_module._validate_public_parity(generated, public)
