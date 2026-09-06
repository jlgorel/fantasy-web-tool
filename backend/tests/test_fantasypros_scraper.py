"""Offline tests for authenticated FantasyPros parsing and quality guards."""
from __future__ import annotations

import base64
import importlib
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
AZURE_FN_DIR = REPO_ROOT / "azure-functions"


@pytest.fixture
def fp_scraper():
    saved_path = list(sys.path)
    saved = sys.modules.pop("fantasypros_scraper", None)
    sys.path.insert(0, str(AZURE_FN_DIR))
    try:
        yield importlib.import_module("fantasypros_scraper")
    finally:
        sys.path[:] = saved_path
        sys.modules.pop("fantasypros_scraper", None)
        if saved is not None:
            sys.modules["fantasypros_scraper"] = saved


def _identity(value: str) -> str:
    return value


def test_storage_state_loads_from_base64(fp_scraper, monkeypatch):
    state = {"cookies": [{"name": "sessionid", "value": "secret"}], "origins": []}
    encoded = base64.b64encode(json.dumps(state).encode()).decode()
    monkeypatch.setenv("FANTASYPROS_STORAGE_STATE_B64", encoded)
    monkeypatch.delenv("FANTASYPROS_STORAGE_STATE_PATH", raising=False)

    assert fp_scraper.storage_state_from_environment() == state


def test_storage_state_rejects_invalid_shape(fp_scraper, monkeypatch):
    encoded = base64.b64encode(json.dumps({"cookies": "bad"}).encode()).decode()
    monkeypatch.setenv("FANTASYPROS_STORAGE_STATE_B64", encoded)

    with pytest.raises(ValueError, match="unexpected shape"):
        fp_scraper.storage_state_from_environment()


def test_parse_projection_tables(fp_scraper):
    flex_html = """
    <table><thead><tr></tr><tr>
      <th><small></small></th><th><small>POS</small></th>
      <th><small>ATT</small></th><th><small>YDS</small></th>
      <th><small>TDS</small></th><th><small>REC</small></th>
      <th><small>YDS</small></th><th><small>TDS</small></th>
      <th><small>FL</small></th><th><small>FPTS</small></th>
    </tr></thead><tbody>
      <tr class="mpb-player-1"><td class="player-name">Sample Receiver</td><td>WR</td>
        <td class="center">2</td><td class="center">12</td>
        <td class="center">1</td><td class="center">6</td>
        <td class="center">75</td><td class="center">0.5</td>
        <td class="center">0</td><td class="center">18</td>
      </tr>
    </tbody></table>
    """
    qb_html = """
    <table><thead><tr></tr><tr>
      <th><small></small></th><th><small>ATT</small></th>
      <th><small>CMP</small></th><th><small>YDS</small></th>
      <th><small>TDS</small></th><th><small>INTS</small></th>
      <th><small>ATT</small></th><th><small>YDS</small></th>
      <th><small>TDS</small></th><th><small>FL</small></th>
      <th><small>FPTS</small></th>
    </tr></thead><tbody>
      <tr class="mpb-player-2"><td class="player-name">Sample Quarterback</td>
        <td class="center">35</td><td class="center">24</td>
        <td class="center">280</td><td class="center">2</td>
        <td class="center">1</td><td class="center">4</td>
        <td class="center">25</td><td class="center">0.2</td>
        <td class="center">0</td><td class="center">22</td>
      </tr>
    </tbody></table>
    """

    result = fp_scraper.parse_projection_html(
        flex_html, qb_html, normalize_name=_identity
    )

    assert result["samplereceiver"]["Receptions"] == 6.0
    assert result["samplereceiver"]["Receiving Yards"] == 75.0
    assert result["samplereceiver"]["Rushing Yards"] == 12.0
    assert result["samplequarterback"]["Passing Yards"] == 280.0
    assert result["samplequarterback"]["Passing Touchdowns"] == 2.0
    assert result["samplequarterback"]["Interceptions"] == 1.0


def test_parse_rankings_table(fp_scraper):
    html = """
    <table><tr class="player-row">
      <td class="sticky-cell sticky-cell-one">7</td>
      <td><div class="player-cell player-cell__td">
        <a class="player-cell-name" fp-player-name="Sample Player">S. Player</a>
        <span class="player-cell-team">(ABC)</span>
      </div></td>
      <td class="matchup-star-cell"><div class="template-stars-star">
        <span class="sr-only">4 stars</span>
      </div></td>
    </tr></table>
    """

    result = fp_scraper.parse_rankings_html(html, normalize_name=_identity)

    assert result["Sample Player"] == {
        "overall_rank": 7,
        "abbreviated_name": "S. Player",
        "Team Name": "ABC",
        "Opponent Rating": 4,
    }


def test_quality_guard_accepts_full_and_rejects_truncated(fp_scraper):
    backup = {str(index): {} for index in range(fp_scraper.MIN_BACKUP_PLAYERS)}
    rankings = {str(index): {} for index in range(fp_scraper.MIN_RANKED_PLAYERS)}
    fp_scraper.validate_candidates(backup, rankings)

    with pytest.raises(ValueError, match="candidate rejected"):
        fp_scraper.validate_candidates({"one": {}}, {"one": {}})
