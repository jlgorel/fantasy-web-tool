"""Reusable FantasyFootballCalculator ADP ingestion.

This module is intentionally pure apart from the injected ``fetch_json``
callable.  It is used by the Azure Functions timer in production, the local
backfill CLI, and offline unit tests.

Output remains backward-compatible with ``draft_adp_{year}.json`` while adding
source-quality metadata (sample size, high/low range, and whether standard
deviation is observed or modeled).
"""
from __future__ import annotations

import math
import re
import unicodedata
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Iterable, Mapping, Optional, Tuple

FFC_URL = (
    "https://fantasyfootballcalculator.com/api/v1/adp/{fmt}"
    "?teams={teams}&year={year}&position=all"
)
FALLBACK_TEAMS = 12
SKIP_POSITIONS = {"PK", "DEF", "K", "DST", "D/ST"}

FetchJson = Callable[[str], Optional[Mapping[str, Any]]]


def normalize_player_name(value: Any) -> str:
    """Normalize a player name for conservative exact matching."""
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.lower().replace("’", "'")
    text = re.sub(r"[^a-z0-9]+", " ", text).strip()
    tokens = [
        token for token in text.split()
        if token not in {"jr", "sr", "ii", "iii", "iv", "v"}
    ]
    return " ".join(tokens)


def ffc_format(ppr: float, superflex: bool) -> str:
    if superflex:
        return "2qb"
    if float(ppr) >= 1.0:
        return "ppr"
    if float(ppr) <= 0.0:
        return "standard"
    return "half-ppr"


def ffc_url(fmt: str, teams: int, year: str) -> str:
    return FFC_URL.format(fmt=fmt, teams=int(teams), year=str(year))


def _finite_float(value: Any) -> Optional[float]:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def ffc_name_map(data: Mapping[str, Any]) -> Dict[Any, Dict[str, Any]]:
    """Build exact ``(normalized name, position) -> ADP row`` mappings.

    A unique name-only key is also emitted as a fallback for benign position
    label differences. Ambiguous duplicate names deliberately lose that
    fallback rather than risk joining ADP to the wrong player.
    """
    out: Dict[Any, Dict[str, Any]] = {}
    name_hits: Dict[str, list[Dict[str, Any]]] = {}
    for raw in data.get("players", []) or []:
        if not isinstance(raw, Mapping):
            continue
        pos = str(raw.get("position") or "").upper()
        name = normalize_player_name(raw.get("name"))
        adp = _finite_float(raw.get("adp"))
        if not name or pos in SKIP_POSITIONS or adp is None or adp <= 0:
            continue
        stdev = _finite_float(raw.get("stdev"))
        entry: Dict[str, Any] = {
            "adp": round(adp, 2),
            "stdev": round(max(stdev or 0.0, 0.0), 2),
            "stdev_source": "observed" if stdev is not None else "missing",
        }
        for source_key, output_key in (
            ("times_drafted", "times_drafted"),
            ("high", "high"),
            ("low", "low"),
        ):
            numeric = _finite_float(raw.get(source_key))
            if numeric is not None:
                entry[output_key] = int(numeric) if numeric.is_integer() else numeric
        out.setdefault((name, pos), entry)
        name_hits.setdefault(name, []).append(entry)

    for name, entries in name_hits.items():
        if len(entries) == 1:
            out[(name, None)] = entries[0]
    return out


def _sleeper_player_index(
    players: Mapping[str, Any],
) -> Tuple[Dict[Tuple[str, str], str], Dict[str, Optional[str]]]:
    by_name_pos: Dict[Tuple[str, str], str] = {}
    by_name_candidates: Dict[str, list[str]] = {}
    for pid, raw in players.items():
        if not isinstance(raw, Mapping) or not raw.get("full_name"):
            continue
        name = normalize_player_name(raw.get("full_name"))
        if not name:
            continue
        by_name_candidates.setdefault(name, []).append(str(pid))
        for pos in raw.get("fantasy_positions") or []:
            by_name_pos.setdefault((name, str(pos).upper()), str(pid))
    by_name = {
        name: ids[0] if len(set(ids)) == 1 else None
        for name, ids in by_name_candidates.items()
    }
    return by_name_pos, by_name


def _player_pool_configs(
    team_sizes: Iterable[int],
    ppr_values: Iterable[float],
) -> Iterable[Tuple[str, int, float, bool]]:
    for teams in team_sizes:
        for ppr in ppr_values:
            for superflex in (False, True):
                ppr_label = str(int(ppr)) if float(ppr).is_integer() else str(ppr)
                key = f"{int(teams)}|{ppr_label}|{'sf' if superflex else '1qb'}"
                yield key, int(teams), float(ppr), superflex


def _valid_ffc_payload(data: Optional[Mapping[str, Any]]) -> bool:
    return bool(
        isinstance(data, Mapping)
        and data.get("status") == "Success"
        and isinstance(data.get("players"), list)
        and data.get("players")
    )


def build_adp_blob(
    year: str,
    rankings_blob: Mapping[str, Any],
    *,
    fetch_json: FetchJson,
    generated_at: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Build a complete ADP blob for every ranking configuration.

    The requested team-size feed is preferred. If FFC has no data for that
    size, its 12-team feed is used and ``source_teams`` records the fallback.
    """
    generated_at = generated_at or datetime.now(timezone.utc)
    cache: Dict[Tuple[str, int], Optional[Mapping[str, Any]]] = {}
    configs_out: Dict[str, Any] = {}

    def fetch(fmt: str, teams: int) -> Optional[Mapping[str, Any]]:
        key = (fmt, teams)
        if key not in cache:
            payload = fetch_json(ffc_url(fmt, teams, str(year)))
            cache[key] = payload if _valid_ffc_payload(payload) else None
        return cache[key]

    for cfg_key, cfg in (rankings_blob.get("configs") or {}).items():
        if not isinstance(cfg, Mapping):
            continue
        teams = int(cfg.get("teams") or FALLBACK_TEAMS)
        ppr = float(cfg.get("ppr") or 0.0)
        superflex = bool(cfg.get("superflex"))
        fmt = ffc_format(ppr, superflex)

        source_teams = teams
        data = fetch(fmt, teams)
        if data is None and teams != FALLBACK_TEAMS:
            source_teams = FALLBACK_TEAMS
            data = fetch(fmt, FALLBACK_TEAMS)

        name_map = ffc_name_map(data or {})
        players_out: Dict[str, Dict[str, Any]] = {}
        ranking_players = cfg.get("players") or []
        for player in ranking_players:
            if not isinstance(player, Mapping) or not player.get("player_id"):
                continue
            name = normalize_player_name(player.get("name"))
            pos = str(player.get("pos") or "").upper()
            hit = name_map.get((name, pos)) or name_map.get((name, None))
            if hit:
                players_out[str(player["player_id"])] = dict(hit)

        meta = (data or {}).get("meta") or {}
        configs_out[str(cfg_key)] = {
            "teams": teams,
            "ppr": ppr,
            "superflex": superflex,
            "format": fmt,
            "source_teams": source_teams,
            "total_drafts": meta.get("total_drafts"),
            "source_start_date": meta.get("start_date"),
            "source_end_date": meta.get("end_date"),
            "matched": len(players_out),
            "total": len(ranking_players),
            "players": players_out,
        }

    return {
        "schema_version": int(rankings_blob.get("schema_version") or 1),
        "year": str(year),
        "source": "fantasyfootballcalculator",
        "generated_at_utc": generated_at.astimezone(timezone.utc).isoformat(),
        "configs": configs_out,
    }


def build_adp_blob_from_players(
    year: str,
    players_blob: Mapping[str, Any],
    *,
    fetch_json: FetchJson,
    team_sizes: Iterable[int] = (8, 10, 12, 14),
    ppr_values: Iterable[float] = (0.0, 0.5, 1.0),
    generated_at: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Build an ADP-only current player pool before a value sheet exists.

    FFC provides the draftable-player universe; ``players.json`` resolves its
    names to canonical Sleeper IDs. Identity fields are stored beside ADP so
    the backend can display a board and accept an uploaded value sheet without
    pretending old projections are current.
    """
    generated_at = generated_at or datetime.now(timezone.utc)
    by_name_pos, by_name = _sleeper_player_index(players_blob)
    cache: Dict[Tuple[str, int], Optional[Mapping[str, Any]]] = {}
    configs_out: Dict[str, Any] = {}

    def fetch(fmt: str, teams: int) -> Optional[Mapping[str, Any]]:
        key = (fmt, teams)
        if key not in cache:
            payload = fetch_json(ffc_url(fmt, teams, str(year)))
            cache[key] = payload if _valid_ffc_payload(payload) else None
        return cache[key]

    for cfg_key, teams, ppr, superflex in _player_pool_configs(
        team_sizes, ppr_values,
    ):
        fmt = ffc_format(ppr, superflex)
        source_teams = teams
        data = fetch(fmt, teams)
        if data is None and teams != FALLBACK_TEAMS:
            source_teams = FALLBACK_TEAMS
            data = fetch(fmt, FALLBACK_TEAMS)

        players_out: Dict[str, Dict[str, Any]] = {}
        eligible = 0
        for raw in (data or {}).get("players", []) or []:
            if not isinstance(raw, Mapping):
                continue
            pos = str(raw.get("position") or "").upper()
            name_key = normalize_player_name(raw.get("name"))
            adp = _finite_float(raw.get("adp"))
            if not name_key or pos in SKIP_POSITIONS or adp is None or adp <= 0:
                continue
            eligible += 1
            pid = by_name_pos.get((name_key, pos)) or by_name.get(name_key)
            if not pid:
                continue
            sleeper = players_blob.get(pid) or {}
            stdev = _finite_float(raw.get("stdev"))
            entry: Dict[str, Any] = {
                "name": sleeper.get("full_name") or raw.get("name") or pid,
                "pos": pos,
                "team": raw.get("team"),
                "adp": round(adp, 2),
                "stdev": round(max(stdev or 0.0, 0.0), 2),
                "stdev_source": "observed" if stdev is not None else "missing",
            }
            for source_key in ("times_drafted", "high", "low"):
                numeric = _finite_float(raw.get(source_key))
                if numeric is not None:
                    entry[source_key] = int(numeric) if numeric.is_integer() else numeric
            players_out[pid] = entry

        meta = (data or {}).get("meta") or {}
        configs_out[cfg_key] = {
            "teams": teams,
            "ppr": ppr,
            "superflex": superflex,
            "format": fmt,
            "source_teams": source_teams,
            "total_drafts": meta.get("total_drafts"),
            "source_start_date": meta.get("start_date"),
            "source_end_date": meta.get("end_date"),
            "matched": len(players_out),
            "total": eligible,
            "players": players_out,
            "adp_only": True,
        }

    return {
        "schema_version": 1,
        "year": str(year),
        "source": "fantasyfootballcalculator",
        "generated_at_utc": generated_at.astimezone(timezone.utc).isoformat(),
        "adp_only": True,
        "configs": configs_out,
    }


def validate_adp_blob(
    blob: Mapping[str, Any],
    *,
    min_config_coverage: float = 0.35,
    min_overall_coverage: float = 0.45,
) -> list[str]:
    """Return validation errors; an empty list means safe to publish.

    Coverage gates reflect the actual FFC feed: a 300-player values sheet
    commonly matches 160-180 draftable skill players. Requiring 80% would reject
    healthy data because FFC intentionally stops well before deep rankings do.
    """
    errors: list[str] = []
    configs = blob.get("configs")
    if not isinstance(configs, Mapping) or not configs:
        return ["no ADP configurations were built"]

    matched_total = 0
    player_total = 0
    for key, cfg in configs.items():
        if not isinstance(cfg, Mapping):
            errors.append(f"{key}: invalid config payload")
            continue
        matched = int(cfg.get("matched") or 0)
        total = int(cfg.get("total") or 0)
        players = cfg.get("players")
        matched_total += matched
        player_total += total
        if total <= 0:
            errors.append(f"{key}: empty rankings universe")
            continue
        if matched != len(players or {}):
            errors.append(f"{key}: matched count does not equal player rows")
        coverage = matched / total
        if coverage < min_config_coverage:
            errors.append(f"{key}: coverage {coverage:.1%} below {min_config_coverage:.0%}")
        for pid, row in (players or {}).items():
            adp = _finite_float((row or {}).get("adp"))
            stdev = _finite_float((row or {}).get("stdev"))
            if adp is None or adp <= 0:
                errors.append(f"{key}/{pid}: invalid ADP")
            if stdev is None or stdev < 0:
                errors.append(f"{key}/{pid}: invalid standard deviation")

    overall = matched_total / player_total if player_total else 0.0
    if overall < min_overall_coverage:
        errors.append(
            f"overall coverage {overall:.1%} below {min_overall_coverage:.0%}"
        )
    return errors


def is_draft_season(now: Optional[datetime] = None) -> bool:
    """True during the redraft-data collection window (July-September)."""
    now = now or datetime.now(timezone.utc)
    return now.month in (7, 8, 9)


__all__ = [
    "build_adp_blob",
    "build_adp_blob_from_players",
    "ffc_format",
    "ffc_name_map",
    "ffc_url",
    "is_draft_season",
    "normalize_player_name",
    "validate_adp_blob",
]
