"""FantasyPros DraftWizard ADP ingestion for current redraft boards.

DraftWizard publishes format/team-size-specific mock-draft ADP with an observed
standard deviation.  This module parses the server-rendered free table and
matches it to canonical Sleeper player IDs.  Network access is injected so the
parser/builder remain fixture-testable.
"""
from __future__ import annotations

import math
import re
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Iterable, Mapping, Optional, Tuple

from bs4 import BeautifulSoup

from draft_adp import normalize_player_name

BASE_URL = (
    "https://draftwizard.fantasypros.com/football/adp/mock-drafts/overall/"
    "{format}-{teams}-teams"
)
SKIP_POSITIONS = {"K", "PK", "DST", "DEF", "D/ST"}
FetchText = Callable[[str], Optional[str]]


def fantasypros_format(ppr: float, superflex: bool) -> str:
    if float(ppr) >= 1.0:
        scoring = "ppr"
    elif float(ppr) <= 0.0:
        scoring = "std"
    else:
        scoring = "half"
    return f"{'2qb' if superflex else '1qb'}-{scoring}"


def fantasypros_url(fmt: str, teams: int) -> str:
    return BASE_URL.format(format=fmt, teams=int(teams))


def _finite_float(value: Any) -> Optional[float]:
    try:
        result = float(str(value).replace("%", "").strip())
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _round_pick_to_overall(value: Any, teams: int) -> Optional[float]:
    """Convert DraftWizard's 2QB ``round.pick`` display to overall pick.

    DraftWizard uses ``2.00`` for the 12th pick, ``2.01`` for pick 13, etc.
    The fractional part is a slot label, not a decimal fraction.
    """
    text = str(value or "").strip()
    match = re.fullmatch(r"(\d+)\.(\d{2})", text)
    if not match:
        return _finite_float(text)
    round_no = int(match.group(1))
    slot = int(match.group(2))
    if round_no < 1:
        return None
    if slot == 0:
        return float(max(1, (round_no - 1) * teams))
    if slot > teams:
        return None
    return float((round_no - 1) * teams + slot)


def parse_fantasypros_html(
    html: str,
    *,
    teams: int,
    superflex: bool,
) -> list[Dict[str, Any]]:
    """Parse the ``#adpTable`` into normalized player ADP rows."""
    soup = BeautifulSoup(html or "", "html.parser")
    table = soup.select_one("#adpTable")
    if table is None:
        return []
    out: list[Dict[str, Any]] = []
    for tr in table.select("tbody tr"):
        cells = [td.get_text(" ", strip=True) for td in tr.find_all("td")]
        if len(cells) < 9:
            continue
        pos_match = re.match(r"([A-Za-z/]+)", cells[0])
        pos = pos_match.group(1).upper() if pos_match else ""
        if not pos or pos in SKIP_POSITIONS:
            continue
        name = cells[2].strip()
        if not name:
            continue
        team = (cells[3].split(" ", 1)[0] or "").strip() or None
        # Both explicit 1QB and 2QB routes encode overall picks as
        # round/slot strings (e.g. 1.12, 2.00, 2.01).
        parse_pick = lambda raw: _round_pick_to_overall(raw, teams)
        adp = parse_pick(cells[4])
        high = parse_pick(cells[5])
        low = parse_pick(cells[6])
        stdev = _finite_float(cells[7])
        drafted_pct = _finite_float(cells[8])
        if adp is None or adp <= 0 or stdev is None or stdev < 0:
            continue
        out.append({
            "name": name,
            "pos": pos,
            "team": team,
            "adp": round(adp, 2),
            "stdev": round(stdev, 2),
            "stdev_source": "observed",
            "high": round(high, 2) if high is not None else None,
            "low": round(low, 2) if low is not None else None,
            "drafted_pct": round(drafted_pct, 2) if drafted_pct is not None else None,
        })
    return out


def _sleeper_index(
    players: Mapping[str, Any],
) -> Tuple[Dict[Tuple[str, str], str], Dict[str, Optional[str]]]:
    by_name_pos: Dict[Tuple[str, str], str] = {}
    candidates: Dict[str, list[str]] = {}
    for pid, raw in players.items():
        if not isinstance(raw, Mapping) or not raw.get("full_name"):
            continue
        name = normalize_player_name(raw.get("full_name"))
        if not name:
            continue
        candidates.setdefault(name, []).append(str(pid))
        for pos in raw.get("fantasy_positions") or []:
            by_name_pos.setdefault((name, str(pos).upper()), str(pid))
    by_name = {
        name: ids[0] if len(set(ids)) == 1 else None
        for name, ids in candidates.items()
    }
    return by_name_pos, by_name


def build_fantasypros_adp_blob(
    year: str,
    players_blob: Mapping[str, Any],
    *,
    fetch_text: FetchText,
    team_sizes: Iterable[int] = (8, 10, 12, 14),
    ppr_values: Iterable[float] = (0.0, 0.5, 1.0),
    generated_at: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Build every team-size/scoring/1QB-or-2QB configuration."""
    generated_at = generated_at or datetime.now(timezone.utc)
    by_name_pos, by_name = _sleeper_index(players_blob)
    configs: Dict[str, Any] = {}

    for teams in team_sizes:
        for ppr in ppr_values:
            ppr_label = str(int(ppr)) if float(ppr).is_integer() else str(ppr)
            for superflex in (False, True):
                fmt = fantasypros_format(ppr, superflex)
                url = fantasypros_url(fmt, teams)
                rows = parse_fantasypros_html(
                    fetch_text(url) or "",
                    teams=int(teams),
                    superflex=superflex,
                )
                matched: Dict[str, Dict[str, Any]] = {}
                for row in rows:
                    key = normalize_player_name(row["name"])
                    pid = by_name_pos.get((key, row["pos"])) or by_name.get(key)
                    if not pid:
                        continue
                    sleeper = players_blob.get(pid) or {}
                    matched[pid] = {
                        **row,
                        "name": sleeper.get("full_name") or row["name"],
                    }
                config_key = (
                    f"{int(teams)}|{ppr_label}|{'sf' if superflex else '1qb'}"
                )
                configs[config_key] = {
                    "teams": int(teams),
                    "ppr": float(ppr),
                    "superflex": superflex,
                    "format": fmt,
                    "source_url": url,
                    "source_window": "FantasyPros mock drafts over the past day",
                    "total_drafts": None,
                    "matched": len(matched),
                    "total": len(rows),
                    "players": matched,
                    "adp_only": True,
                }

    return {
        "schema_version": 1,
        "year": str(year),
        "source": "fantasypros_draftwizard",
        "generated_at_utc": generated_at.astimezone(timezone.utc).isoformat(),
        "adp_only": True,
        "configs": configs,
    }


__all__ = [
    "build_fantasypros_adp_blob",
    "fantasypros_format",
    "fantasypros_url",
    "parse_fantasypros_html",
]
