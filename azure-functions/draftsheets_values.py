"""Pure DraftSheets adapter for published finished cross-position Value rows.

DraftSheets owns the projection/VOR methodology. This module only reads the
public Scoring and DraftSheet CSV exports, verifies their exact league profile,
conservatively matches names to canonical player IDs, and preserves the
provider's finished ``VALUE`` field without recalculating it.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import re
from datetime import datetime, timezone
from typing import Any, Dict, Mapping, Optional, Protocol

PROVIDER_ID = "draftsheets"
PROVIDER_NAME = "DraftSheets"
SHEET_ID = "1De-LEk2Moq8vQpgKBw6XKL0xOGTxQqvL"
SOURCE_URL = f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/edit"
XLSX_EXPORT_URL = (
    f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/export?format=xlsx"
)
SCORING_CSV_URL = (
    f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/gviz/tq"
    "?tqx=out:csv&sheet=Scoring"
)
DRAFTSHEET_CSV_URL = (
    f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/gviz/tq"
    "?tqx=out:csv&sheet=DraftSheet"
)
_NAME_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}
_NAME_ALIASES = {
    "bam knight": "zonovan knight",
    "hollywood brown": "marquise brown",
    "kenny gainwell": "kenneth gainwell",
}


class PlayerResolver(Protocol):
    def resolve(self, name: Any, position: Any = None) -> Optional[str]: ...


def normalize_player_name(name: Any) -> str:
    text = str(name or "").strip().lower()
    for character in ".'`,":
        text = text.replace(character, "")
    tokens = [
        token for token in text.replace("-", " ").split()
        if token and token not in _NAME_SUFFIXES
    ]
    normalized = " ".join(tokens)
    return _NAME_ALIASES.get(normalized, normalized)


class NameResolver:
    """Conservative exact name+position resolver for a Sleeper players blob."""

    def __init__(self, players: Mapping[str, Any]):
        self._by_name_position: Dict[tuple[str, str], str] = {}
        self._by_name: Dict[str, list[str]] = {}
        for player_id, raw in players.items():
            if not isinstance(raw, Mapping) or not raw.get("full_name"):
                continue
            name = normalize_player_name(raw.get("full_name"))
            self._by_name.setdefault(name, []).append(str(player_id))
            for position in raw.get("fantasy_positions") or []:
                self._by_name_position.setdefault(
                    (name, str(position).upper()), str(player_id),
                )

    def resolve(self, name: Any, position: Any = None) -> Optional[str]:
        normalized = normalize_player_name(name)
        if position:
            exact = self._by_name_position.get(
                (normalized, str(position).upper())
            )
            if exact:
                return exact
        candidates = list(dict.fromkeys(self._by_name.get(normalized) or []))
        return candidates[0] if len(candidates) == 1 else None


def common_profiles() -> list[Dict[str, Any]]:
    """Curated WR/FLEX/bench/pass-TD grid shared with ElBoberto D3."""
    return [
        {
            "starters": {"QB": 1, "RB": 2, "WR": wr, "TE": 1, "FLEX": flex},
            "bench_size": bench,
            "passing_td": passing_td,
        }
        for wr in (2, 3)
        for flex in (1, 2)
        for bench in (5, 6, 7)
        for passing_td in (4, 6)
    ]


def bridge_configurations(profile: Mapping[str, Any]) -> list[Dict[str, Any]]:
    return [
        {
            **dict(profile),
            "teams": teams,
            "ppr": ppr,
            "superflex": superflex,
            "interceptions": -1,
        }
        for teams in (8, 10, 12, 14)
        for ppr in (0.0, 0.5, 1.0)
        for superflex in (False, True)
    ]


def _rows(text: str) -> list[list[str]]:
    return list(csv.reader(io.StringIO(text or "")))


def _values_csv(values: Any) -> str:
    if not isinstance(values, list):
        raise ValueError("DraftSheets bridge values are not a row array")
    stream = io.StringIO(newline="")
    writer = csv.writer(stream)
    for row in values:
        if not isinstance(row, list):
            raise ValueError("DraftSheets bridge row is invalid")
        writer.writerow(row)
    return stream.getvalue()


def _finite_float(value: Any) -> Optional[float]:
    try:
        result = float(str(value).replace("$", "").replace(",", "").strip())
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _int(value: Any) -> int:
    parsed = _finite_float(value)
    if parsed is None:
        raise ValueError(f"Expected integer setting, got {value!r}")
    return int(parsed)


def _profile_id(starters: Mapping[str, int], bench_size: int, passing_td: int) -> str:
    return "-".join([
        f"qb{int(starters.get('QB') or 0)}",
        f"rb{int(starters.get('RB') or 0)}",
        f"wr{int(starters.get('WR') or 0)}",
        f"te{int(starters.get('TE') or 0)}",
        f"flex{int(starters.get('FLEX') or 0)}",
        f"bn{int(bench_size)}",
        f"ptd{int(passing_td)}",
    ])


def parse_scoring_csv(text: str) -> Dict[str, Any]:
    rows = _rows(text)
    if len(rows) < 2:
        raise ValueError("DraftSheets Scoring export is empty")
    try:
        header_index = next(
            index for index, row in enumerate(rows[:-1])
            if any(str(cell).strip().upper().endswith("#TEAMS:") for cell in row)
        )
    except StopIteration as exc:
        raise ValueError("DraftSheets roster header was not found") from exc
    header = rows[header_index]
    values = rows[header_index + 1]
    roster_labels = {
        "#TEAMS:": "teams", "QB:": "QB", "RB:": "RB", "WR:": "WR",
        "TE:": "TE", "FLEX:": "FLEX", "BENCH:": "bench_size",
        "SUPERFLEX:": "superflex",
    }
    roster: Dict[str, int] = {}
    for index, raw_header in enumerate(header):
        normalized = str(raw_header).strip().upper()
        for label, target in sorted(
            roster_labels.items(), key=lambda item: len(item[0]), reverse=True,
        ):
            if normalized.endswith(label):
                roster[target] = _int(values[index] if index < len(values) else None)
                break
    missing = [key for key in roster_labels.values() if key not in roster]
    if missing:
        raise ValueError(f"DraftSheets roster settings missing: {', '.join(missing)}")

    settings = {
        str(row[0]).strip(): str(row[1]).strip()
        for row in rows
        if len(row) > 1 and str(row[0]).strip() and str(row[1]).strip()
    }
    passing_td = _int(settings.get("PassTDs"))
    ppr_values = [
        _finite_float(settings.get("RB PPR")),
        _finite_float(settings.get("WR PPR")),
        _finite_float(settings.get("TE PPR")),
    ]
    if any(value is None for value in ppr_values) or len(set(ppr_values)) != 1:
        raise ValueError("DraftSheets position-specific PPR is unsupported")
    starters = {
        "QB": roster["QB"], "RB": roster["RB"], "WR": roster["WR"],
        "TE": roster["TE"], "FLEX": roster["FLEX"],
    }
    profile_id = _profile_id(starters, roster["bench_size"], passing_td)
    return {
        "teams": roster["teams"],
        "ppr": float(ppr_values[0]),
        "superflex": bool(roster["superflex"]),
        "passing_td": passing_td,
        "bench_size": roster["bench_size"],
        "starters": starters,
        "profile_id": profile_id,
        "settings": settings,
    }


def _team_bye(value: str) -> tuple[Optional[str], Optional[int]]:
    text = str(value or "").strip()
    if not text:
        return None, None
    team, separator, bye_text = text.partition("/")
    bye = _finite_float(bye_text) if separator else None
    return team.strip() or None, int(bye) if bye is not None else None


def _parse_block(
    rows: list[list[str]], *, position: str, start: int, stop: int,
    base: int, resolver: PlayerResolver,
) -> tuple[list[Dict[str, Any]], list[str]]:
    players: list[Dict[str, Any]] = []
    unmatched: list[str] = []
    position_rank = 0
    for row in rows[start:stop]:
        name = str(row[base + 1]).strip() if len(row) > base + 1 else ""
        if not name or name.upper() == "NAME":
            continue
        value = _finite_float(row[base + 4] if len(row) > base + 4 else None)
        points = _finite_float(row[base + 3] if len(row) > base + 3 else None)
        if value is None:
            continue
        position_rank += 1
        player_id = resolver.resolve(name, position)
        if not player_id:
            unmatched.append(f"{position}:{name}")
            continue
        team, bye = _team_bye(row[base + 2] if len(row) > base + 2 else "")
        scarcity_text = str(row[base + 5]).strip() if len(row) > base + 5 else ""
        scarcity = _finite_float(scarcity_text.replace("%", ""))
        ecr = _finite_float(row[base + 6] if len(row) > base + 6 else None)
        players.append({
            "player_id": str(player_id),
            "name": name,
            "source_name": name,
            "pos": position,
            "team": team,
            "source_team_bye": row[base + 2] if len(row) > base + 2 else None,
            "bye": bye,
            "fpts": points,
            "provider_points": points,
            "provider_ps": scarcity / 100.0 if scarcity is not None else None,
            "provider_ecr": ecr,
            "auction": None,
            "vbd": value,
            "tier": str(row[base]).strip() or None,
            "pos_rank": position_rank,
            "overall_rank": None,
        })
    return players, unmatched


def parse_draftsheet_csv(
    text: str, resolver: PlayerResolver,
) -> tuple[list[Dict[str, Any]], list[str], Optional[str]]:
    rows = _rows(text)
    if not rows:
        raise ValueError("DraftSheets DraftSheet export is empty")
    title = " ".join(
        " ".join(row) for row in rows[:5]
    )
    updated_match = re.search(r"Updated:\s*(\d{4}-\d{2}-\d{2})", title)
    updated_date = updated_match.group(1) if updated_match else None
    name_headers = [
        index for index, row in enumerate(rows)
        if len(row) > 2 and str(row[2]).strip().upper() == "NAME"
    ]
    if not name_headers:
        raise ValueError("DraftSheets TE block header was not found")
    # GViz compacts the first QB header into its title row, while a full Excel/
    # Apps Script range retains both the upper QB header and lower TE header.
    te_header = name_headers[-1]

    specs = [
        ("QB", 1, te_header, 1),
        ("RB", 1, len(rows), 11),
        ("WR", 1, len(rows), 21),
        ("TE", te_header + 1, len(rows), 1),
    ]
    players: list[Dict[str, Any]] = []
    unmatched: list[str] = []
    seen_ids: set[str] = set()
    for position, start, stop, base in specs:
        parsed, missing = _parse_block(
            rows, position=position, start=start, stop=stop,
            base=base, resolver=resolver,
        )
        for player in parsed:
            if player["player_id"] in seen_ids:
                raise ValueError(f"Duplicate DraftSheets player: {player['player_id']}")
            seen_ids.add(player["player_id"])
            players.append(player)
        unmatched.extend(missing)
    players.sort(key=lambda row: (-float(row["vbd"]), row["pos"], row["pos_rank"]))
    for rank, player in enumerate(players, start=1):
        player["overall_rank"] = rank
    return players, unmatched, updated_date


def build_draftsheets_blob(
    year: Any,
    players_blob: Mapping[str, Any],
    scoring_csv: str,
    draftsheet_csv: str,
    *,
    resolver_factory: Any,
    retrieved_at: Optional[datetime] = None,
) -> Dict[str, Any]:
    retrieved_at = retrieved_at or datetime.now(timezone.utc)
    scoring = parse_scoring_csv(scoring_csv)
    resolver = resolver_factory(dict(players_blob))
    players, unmatched, updated_date = parse_draftsheet_csv(draftsheet_csv, resolver)
    config_key = (
        f"{scoring['teams']}|{int(scoring['ppr']) if scoring['ppr'].is_integer() else scoring['ppr']}|"
        f"{'sf' if scoring['superflex'] else '1qb'}"
    )
    source_hash = hashlib.sha256(
        (scoring_csv + "\n---DRAFTSHEET---\n" + draftsheet_csv).encode("utf-8")
    ).hexdigest()
    return {
        "schema_version": 1,
        "year": str(year),
        "provider": PROVIDER_ID,
        "source": PROVIDER_NAME,
        "source_url": SOURCE_URL,
        "source_version": updated_date,
        "source_content_sha256": source_hash,
        "generated_at_utc": retrieved_at.astimezone(timezone.utc).isoformat(),
        "retrieved_at_utc": retrieved_at.astimezone(timezone.utc).isoformat(),
        "attribution": PROVIDER_NAME,
        "profile": {
            "id": scoring["profile_id"],
            "passing_td": scoring["passing_td"],
            "bench_size": scoring["bench_size"],
            "starters": scoring["starters"],
            "superflex_mode": "superflex" if scoring["superflex"] else "1qb",
        },
        "configs": {
            config_key: {
                "teams": scoring["teams"],
                "ppr": scoring["ppr"],
                "superflex": scoring["superflex"],
                "budget": 200,
                "players": players,
                "matched": len(players),
                "unmatched": len(unmatched),
                "total": len(players) + len(unmatched),
            },
        },
        "unmatched_names": unmatched,
    }


def build_draftsheets_profile_from_bridge(
    year: Any,
    players_blob: Mapping[str, Any],
    bridge_payload: Mapping[str, Any],
    *,
    resolver_factory: Any = NameResolver,
    retrieved_at: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Combine one bridge batch into an exact profile with many configs."""
    if bridge_payload.get("ok") is not True:
        raise ValueError(
            f"DraftSheets bridge failed: {bridge_payload.get('error') or 'unknown error'}"
        )
    results = bridge_payload.get("results")
    if not isinstance(results, list) or not results:
        raise ValueError("DraftSheets bridge returned no configurations")
    retrieved_at = retrieved_at or datetime.now(timezone.utc)
    combined: Optional[Dict[str, Any]] = None
    expected_profile_id: Optional[str] = None
    unmatched: list[str] = []
    for result in results:
        if not isinstance(result, Mapping):
            raise ValueError("DraftSheets bridge result is invalid")
        candidate = build_draftsheets_blob(
            year,
            players_blob,
            _values_csv(result.get("scoring_values")),
            _values_csv(result.get("draftsheet_values")),
            resolver_factory=resolver_factory,
            retrieved_at=retrieved_at,
        )
        candidate_configs = dict(candidate["configs"])
        profile_id = candidate["profile"]["id"]
        if expected_profile_id is None:
            expected_profile_id = profile_id
            combined = {**candidate, "configs": {}}
            combined["unmatched_names"] = []
        elif profile_id != expected_profile_id:
            raise ValueError("DraftSheets bridge batch crossed profile boundaries")
        assert combined is not None
        for key, config in candidate_configs.items():
            if key in combined["configs"]:
                raise ValueError(f"Duplicate DraftSheets bridge configuration: {key}")
            combined["configs"][key] = config
        unmatched.extend(candidate.get("unmatched_names") or [])
    assert combined is not None
    combined["unmatched_names"] = sorted(set(unmatched))
    combined["generated_at_utc"] = retrieved_at.astimezone(timezone.utc).isoformat()
    combined["retrieved_at_utc"] = retrieved_at.astimezone(timezone.utc).isoformat()
    source_updated = bridge_payload.get("source_last_updated_utc")
    if source_updated:
        combined["source_version"] = str(source_updated)
        combined["source_last_modified"] = str(source_updated)
    combined["source_content_sha256"] = hashlib.sha256(
        json.dumps(bridge_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return combined
