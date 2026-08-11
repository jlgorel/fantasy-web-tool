"""Offline FantasyPros DraftWizard ADP parser/builder tests."""
from __future__ import annotations

import importlib
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
AZURE_DIR = REPO / "azure-functions"

ONE_QB_HTML = """
<table id="adpTable"><thead><tr>
<th>Position</th><th>Overall</th><th>Player</th><th>Team (Bye)</th>
<th>Avg Pick</th><th>High</th><th>Low</th><th>Std Dev</th><th>% Drafted</th>
</tr></thead><tbody>
<tr class="PosRB"><td>RB1</td><td>1</td><td class="playerName">Jahmyr Gibbs</td>
<td>DET (6)</td><td>1.01</td><td>1.01</td><td>1.09</td><td>0.84</td><td>100%</td></tr>
<tr class="PosWR"><td>WR1</td><td>2</td><td class="playerName">Ja'Marr Chase</td>
<td>CIN (6)</td><td>1.03</td><td>1.01</td><td>1.10</td><td>1.06</td><td>100%</td></tr>
</tbody></table>
"""

TWO_QB_HTML = """
<table id="adpTable"><tbody>
<tr><td>QB1</td><td>1</td><td>Josh Allen</td><td>BUF (7)</td>
<td>1.05</td><td>1.01</td><td>3.05</td><td>6.25</td><td>100%</td></tr>
<tr><td>RB1</td><td>2</td><td>Jahmyr Gibbs</td><td>DET (6)</td>
<td>2.00</td><td>1.01</td><td>2.05</td><td>2.63</td><td>100%</td></tr>
</tbody></table>
"""


@pytest.fixture(scope="module")
def modules():
    saved_path = list(sys.path)
    saved = {name: sys.modules.get(name) for name in ("draft_adp", "fantasypros_adp")}
    for name in saved:
        sys.modules.pop(name, None)
    sys.path.insert(0, str(AZURE_DIR))
    try:
        yield importlib.import_module("fantasypros_adp"), importlib.import_module("draft_adp")
    finally:
        sys.path[:] = saved_path
        for name, module in saved.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module


def test_format_urls_are_scoring_specific(modules):
    fp, _ = modules
    assert fp.fantasypros_format(0, False) == "1qb-std"
    assert fp.fantasypros_format(0.5, False) == "1qb-half"
    assert fp.fantasypros_format(1, False) == "1qb-ppr"
    assert fp.fantasypros_format(0.5, True) == "2qb-half"
    assert fp.fantasypros_url("1qb-half", 12).endswith("/1qb-half-12-teams")


def test_parse_one_qb_round_slot_adp(modules):
    fp, _ = modules
    rows = fp.parse_fantasypros_html(ONE_QB_HTML, teams=12, superflex=False)
    assert rows[0] == {
        "name": "Jahmyr Gibbs", "pos": "RB", "team": "DET",
        "adp": 1.0, "stdev": 0.84, "stdev_source": "observed",
        "high": 1.0, "low": 9.0, "drafted_pct": 100.0,
    }


def test_parse_two_qb_round_slot_notation(modules):
    fp, _ = modules
    rows = fp.parse_fantasypros_html(TWO_QB_HTML, teams=12, superflex=True)
    assert rows[0]["adp"] == 5.0
    assert rows[0]["high"] == 1.0
    assert rows[0]["low"] == 29.0
    # DraftWizard uses 2.00 for the 12th overall pick.
    assert rows[1]["adp"] == 12.0


def test_build_matches_sleeper_ids_and_validates(modules):
    fp, adp = modules
    players = {
        "9221": {"full_name": "Jahmyr Gibbs", "fantasy_positions": ["RB"]},
        "7564": {"full_name": "Ja'Marr Chase", "fantasy_positions": ["WR"]},
        "4984": {"full_name": "Josh Allen", "fantasy_positions": ["QB"]},
    }

    def fetch(url):
        return TWO_QB_HTML if "2qb-" in url else ONE_QB_HTML

    blob = fp.build_fantasypros_adp_blob(
        "2026", players, fetch_text=fetch,
        team_sizes=(12,), ppr_values=(0.5,),
        generated_at=datetime(2026, 8, 9, tzinfo=timezone.utc),
    )
    assert blob["source"] == "fantasypros_draftwizard"
    assert blob["configs"]["12|0.5|1qb"]["players"]["9221"]["adp"] == 1.0
    assert blob["configs"]["12|0.5|sf"]["players"]["4984"]["adp"] == 5.0
    assert adp.validate_adp_blob(
        blob, min_config_coverage=0.5, min_overall_coverage=0.5,
    ) == []


def test_scoring_formats_use_distinct_urls_and_values(modules):
    fp, _ = modules
    players = {
        "9221": {"full_name": "Jahmyr Gibbs", "fantasy_positions": ["RB"]},
        "7564": {"full_name": "Ja'Marr Chase", "fantasy_positions": ["WR"]},
    }
    requested = []

    def fetch(url):
        requested.append(url)
        if "1qb-ppr" in url:
            return ONE_QB_HTML.replace("<td>1.01</td>", "<td>1.02</td>", 1)
        return ONE_QB_HTML

    blob = fp.build_fantasypros_adp_blob(
        "2026", players, fetch_text=fetch,
        team_sizes=(12,), ppr_values=(0.5, 1.0),
    )
    half = blob["configs"]["12|0.5|1qb"]["players"]["9221"]["adp"]
    ppr = blob["configs"]["12|1|1qb"]["players"]["9221"]["adp"]
    assert half == 1.0 and ppr == 2.0
    assert any("/1qb-half-12-teams" in url for url in requested)
    assert any("/1qb-ppr-12-teams" in url for url in requested)
