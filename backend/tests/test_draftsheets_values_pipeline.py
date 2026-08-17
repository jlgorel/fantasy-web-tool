"""Offline tests for the public DraftSheets finished-Value adapter."""
from __future__ import annotations

import importlib
import csv
import io
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
AZURE_DIR = REPO / "azure-functions"

SCORING_CSV = """LEAGUE SETTINGS & SCORING INPUTS Points:,,,,,,,,LEAGUE & ROSTER SETTINGS #TEAMS:,QB:,RB:,WR:,TE:,FLEX:,BENCH:,SUPERFLEX:
PASSING,,,,,,,,12,1,2,2,1,1,5,0
PassTDs,4
RB PPR,0.5
WR PPR,0.5
TE PPR,0.5
"""

DRAFTSHEET_CSV = """,DRAFTSHEET - Updated: 2026-08-11 QUARTERBACK TIER,NAME,TM/BYE,PTS,VALUE,PS,ECR,DRAFT,,,RUNNING BACK TIER,NAME,TM/BYE,PTS,VALUE,PS,ECR,DRAFT,,,WIDE RECEIVER TIER,NAME,TM/BYE,PTS,VALUE,PS,ECR,DRAFT
,1,Josh Allen,BUF/7,303,67,76%,28,,,,1,Jahmyr Gibbs,DET/6,245,134,92%,1,,,,1,Puka Nacua,LAR/11,237,140,92%,4

,,NAME,TM/BYE,,,,DRAFT
,2,Trey McBride,ARI/14,170,64,83%,24
"""


@pytest.fixture(scope="module")
def modules():
    saved_path = list(sys.path)
    saved = {
        name: sys.modules.get(name)
        for name in ("draftsheets_values", "draft_adp", "draft_values")
    }
    sys.path.insert(0, str(AZURE_DIR))
    try:
        adapter = importlib.import_module("draftsheets_values")
        validator = importlib.import_module("draft_values")
        yield adapter, validator
    finally:
        sys.path[:] = saved_path
        for name, module in saved.items():
            sys.modules.pop(name, None)
            if module is not None:
                sys.modules[name] = module


class Resolver:
    IDS = {
        ("Josh Allen", "QB"): "4984",
        ("Jahmyr Gibbs", "RB"): "9221",
        ("Puka Nacua", "WR"): "9493",
        ("Trey McBride", "TE"): "8130",
    }

    def __init__(self, _players):
        pass

    def resolve(self, name, position=None):
        return self.IDS.get((str(name), str(position)))


def test_parses_exact_scoring_profile(modules):
    adapter, _ = modules
    scoring = adapter.parse_scoring_csv(SCORING_CSV)
    assert scoring["teams"] == 12
    assert scoring["ppr"] == 0.5
    assert scoring["profile_id"] == "qb1-rb2-wr2-te1-flex1-bn5-ptd4"
    assert scoring["superflex"] is False


def test_parses_side_by_side_position_blocks(modules):
    adapter, _ = modules
    players, unmatched, updated = adapter.parse_draftsheet_csv(
        DRAFTSHEET_CSV, Resolver({}),
    )
    assert unmatched == []
    assert updated == "2026-08-11"
    assert {player["pos"] for player in players} == {"QB", "RB", "WR", "TE"}
    assert players[0]["name"] == "Puka Nacua"
    assert players[0]["vbd"] == 140
    assert players[0]["overall_rank"] == 1
    assert next(player for player in players if player["name"] == "Trey McBride")["provider_ps"] == pytest.approx(0.83)


def test_builds_one_exact_finished_value_config(modules):
    adapter, validator = modules
    blob = adapter.build_draftsheets_blob(
        "2026", {}, SCORING_CSV, DRAFTSHEET_CSV,
        resolver_factory=Resolver,
    )
    assert blob["provider"] == "draftsheets"
    assert list(blob["configs"]) == ["12|0.5|1qb"]
    assert blob["profile"]["id"] == "qb1-rb2-wr2-te1-flex1-bn5-ptd4"
    assert validator.validate_rankings_blob(
        blob,
        expected_year=2026,
        required_keys=["12|0.5|1qb"],
        min_players_per_config=4,
    ) == []


def test_combines_google_bridge_results_into_one_profile(modules):
    adapter, _ = modules
    scoring_rows = [
        ["", "LEAGUE SETTINGS & SCORING INPUTS"],
        ["INPUT YOUR LEAGUE SCORING HERE"],
        *list(csv.reader(io.StringIO(SCORING_CSV))),
    ]
    draft_rows = [
        [""], ["DRAFTSHEET"], ["Passing/scoring subtitle"],
        *list(csv.reader(io.StringIO(DRAFTSHEET_CSV))),
    ]
    second_scoring = [list(row) for row in scoring_rows]
    second_scoring[3][8] = "10"
    payload = {
        "ok": True,
        "source_last_updated_utc": "2026-08-11T12:00:00.000Z",
        "results": [
            {"scoring_values": scoring_rows, "draftsheet_values": draft_rows},
            {"scoring_values": second_scoring, "draftsheet_values": draft_rows},
        ],
    }
    blob = adapter.build_draftsheets_profile_from_bridge(
        "2026", {}, payload, resolver_factory=Resolver,
    )
    assert set(blob["configs"]) == {"12|0.5|1qb", "10|0.5|1qb"}
    assert blob["profile"]["id"] == "qb1-rb2-wr2-te1-flex1-bn5-ptd4"
    assert blob["source_version"] == "2026-08-11T12:00:00.000Z"


def test_common_bridge_grid_is_complete_and_unique(modules):
    adapter, _ = modules
    profiles = adapter.common_profiles()
    profile_ids = {
        adapter._profile_id(
            profile["starters"], profile["bench_size"], profile["passing_td"]
        )
        for profile in profiles
    }
    assert len(profiles) == len(profile_ids) == 24
    configs = adapter.bridge_configurations(profiles[0])
    assert len(configs) == 24
    assert {(config["teams"], config["ppr"], config["superflex"]) for config in configs} == {
        (teams, ppr, superflex)
        for teams in (8, 10, 12, 14)
        for ppr in (0.0, 0.5, 1.0)
        for superflex in (False, True)
    }
