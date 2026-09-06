"""Validation and optimization support for browser-local My Teams rosters."""
from __future__ import annotations

from typing import Any, Dict, List, Tuple

from app.config import Config
from app.services.blob_store import load_blob
from app.services.sleeper_service import build_lineup_recommendations


ALLOWED_POSITIONS = {"QB", "RB", "WR", "TE", "K", "DEF"}
ALLOWED_SLOTS = ALLOWED_POSITIONS | {"REC_FLEX", "FLEX", "SUPER_FLEX", "BN"}
SLOT_ORDER = ("QB", "RB", "WR", "TE", "REC_FLEX", "FLEX", "SUPER_FLEX", "DEF", "K", "BN")
DEFAULT_LINEUP_LIMITS = {
    "QB": 1, "RB": 2, "WR": 2, "TE": 1, "REC_FLEX": 0,
    "FLEX": 1, "SUPER_FLEX": 0, "DEF": 1, "K": 1, "BN": 6,
}
SLOT_ELIGIBILITY = {
    "QB": {"QB"},
    "RB": {"RB"},
    "WR": {"WR"},
    "TE": {"TE"},
    "K": {"K"},
    "DEF": {"DEF"},
    "REC_FLEX": {"WR", "TE"},
    "FLEX": {"RB", "WR", "TE"},
    "SUPER_FLEX": {"QB", "RB", "WR", "TE"},
    "BN": ALLOWED_POSITIONS,
}


class ManualRosterValidationError(ValueError):
    pass


def get_manual_player_catalog(limit: int = 750) -> List[Dict[str, Any]]:
    """Return current ranking players plus every NFL team defense."""
    rankings = load_blob("standard_player_rankings.json")
    rows = rankings.get("halfppr_4ptpass") or next(iter(rankings.values()), [])
    players = load_blob("players.json")
    fantasypros = load_blob("fantasypros_data.json")
    ownership = load_blob("owned.json")

    selected: List[Dict[str, Any]] = []
    seen = set()
    for row in rows:
        pid = str(row.get("PID") or "").strip()
        name = str(row.get("NAME") or "").strip()
        pos = str(row.get("POS") or "").strip().upper()
        if pid in Config.nfl_teams:
            name = Config.nfl_teams[pid]
            pos = "DEF"
        if not pid or not name or pos not in ALLOWED_POSITIONS or pid in seen:
            continue
        vegas = row.get("VEGAS")
        ownership_row = ownership.get(pid) or {}
        currently_rostered = any(
            isinstance(ownership_row.get(key), (int, float)) and ownership_row[key] > 0
            for key in ("owned", "started")
        )
        if (
            len(selected) >= limit
            and not currently_rostered
            and not (isinstance(vegas, (int, float)) and vegas > 0)
        ):
            continue
        pdata = players.get(pid) or {}
        team = pid if pid in Config.nfl_teams else (
            pdata.get("team") or (fantasypros.get(name) or {}).get("Team Name")
        )
        selected.append({"player_id": pid, "name": name, "position": pos, "team": team})
        seen.add(pid)

    for pid, name in Config.nfl_teams.items():
        if pid not in seen:
            selected.append({"player_id": pid, "name": name, "position": "DEF", "team": pid})
    return selected


def _scoring_settings(scoring: Any) -> Dict[str, float]:
    if not isinstance(scoring, dict):
        raise ManualRosterValidationError("scoring must be an object")
    ppr = scoring.get("ppr")
    pass_td = scoring.get("passing_td_points")
    if ppr not in (0, 0.5, 1):
        raise ManualRosterValidationError("ppr must be 0, 0.5, or 1")
    if pass_td not in (4, 6):
        raise ManualRosterValidationError("passing_td_points must be 4 or 6")
    return {
        "pass_int": -2,
        "rec_td": 6,
        "rush_td": 6,
        "pass_yd": 0.04,
        "pass_td": float(pass_td),
        "rush_yd": 0.1,
        "rec_yd": 0.1,
        "rec": float(ppr),
    }


def _lineup_limits(value: Any) -> Dict[str, int]:
    if value is None:
        return dict(DEFAULT_LINEUP_LIMITS)
    if not isinstance(value, dict):
        raise ManualRosterValidationError("lineup_limits must be an object")
    limits: Dict[str, int] = {}
    for slot in SLOT_ORDER:
        raw = value.get(slot, DEFAULT_LINEUP_LIMITS[slot])
        if isinstance(raw, bool) or not isinstance(raw, int):
            raise ManualRosterValidationError(f"lineup_limits.{slot} must be an integer")
        maximum = 30 if slot == "BN" else 10
        if raw < 0 or raw > maximum:
            raise ManualRosterValidationError(
                f"lineup_limits.{slot} must be between 0 and {maximum}"
            )
        limits[slot] = raw
    if sum(limits[slot] for slot in SLOT_ORDER if slot != "BN") < 1:
        raise ManualRosterValidationError("At least one starter slot is required")
    return limits


def normalize_manual_roster(payload: Any) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        raise ManualRosterValidationError("Request body must be an object")
    name = str(payload.get("name") or "").strip()
    if not name or len(name) > 100:
        raise ManualRosterValidationError("name is required and must be at most 100 characters")

    entries = payload.get("players")
    if not isinstance(entries, list) or not entries or len(entries) > 50:
        raise ManualRosterValidationError("players must contain between 1 and 50 entries")

    lineup_limits = _lineup_limits(payload.get("lineup_limits"))
    player_data = load_blob("players.json")
    seen = set()
    starters: List[Tuple[str, str]] = []
    bench: List[str] = []
    slot_counts = {slot: 0 for slot in SLOT_ORDER}
    for entry in entries:
        if not isinstance(entry, dict):
            raise ManualRosterValidationError("Every player entry must be an object")
        pid = str(entry.get("player_id") or "").strip()
        slot = str(entry.get("slot") or "").strip().upper()
        if not pid or pid in seen:
            raise ManualRosterValidationError("Player IDs must be present and unique")
        if slot not in ALLOWED_SLOTS:
            raise ManualRosterValidationError(f"Unsupported lineup slot: {slot or 'missing'}")
        pdata = player_data.get(pid)
        if not isinstance(pdata, dict):
            raise ManualRosterValidationError(f"Unknown player_id: {pid}")
        positions = [str(pos).upper() for pos in (pdata.get("fantasy_positions") or [])]
        if pid in Config.nfl_teams:
            positions = ["DEF"]
        eligible = set(positions) & ALLOWED_POSITIONS
        if not eligible:
            raise ManualRosterValidationError(f"Player {pid} has no supported fantasy position")
        if not eligible.intersection(SLOT_ELIGIBILITY[slot]):
            raise ManualRosterValidationError(f"Player {pid} is not eligible for {slot}")
        slot_counts[slot] += 1
        if slot_counts[slot] > lineup_limits[slot]:
            raise ManualRosterValidationError(f"Too many players assigned to {slot}")
        seen.add(pid)
        if slot == "BN":
            bench.append(pid)
        else:
            starters.append((pid, slot))

    if not starters:
        raise ManualRosterValidationError("At least one player must be assigned to a starter slot")

    starters.sort(key=lambda item: SLOT_ORDER.index(item[1]))
    starter_ids = [pid for pid, _slot in starters]
    roster = {
        "league": name,
        "pids": starter_ids + bench,
        "settings": _scoring_settings(payload.get("scoring")),
        "positions": [slot for _pid, slot in starters] + (["BN"] * len(bench)),
        "all_owned": starter_ids + bench,
        "starters": starter_ids,
    }
    return roster


def evaluate_manual_roster(payload: Any) -> Dict[str, Any]:
    roster = normalize_manual_roster(payload)
    lineups, _free_agents = build_lineup_recommendations([roster], include_free_agents=False)
    league_name = roster["league"]
    bundle = lineups[league_name]
    return {
        "suggested_starts": bundle["boris_optimized"],
        "boris_optimized": bundle["boris_optimized"],
        "vegas_optimized": bundle["vegas_optimized"],
        "your_lineup": bundle["your_lineup"],
        "free_agent_recs": {},
        "free_agent_model": "not_available",
    }