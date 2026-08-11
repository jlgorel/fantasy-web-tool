"""Pure helpers for guarded external draft-value ingestion.

This module deliberately does not calculate VBD/VORP.  It validates and
publishes values already calculated by an external provider, and contains the
small amount of source discovery needed to find ElBoberto's current workbook.
Network, Excel, and Azure access are injected by callers so the safety rules
remain offline-testable.
"""
from __future__ import annotations

import html as _html
import math
import re
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from bs4 import BeautifulSoup

SCHEMA_VERSION = 1
SUPPORTED_TEAM_SIZES = (8, 10, 12, 14)
SUPPORTED_PPR = (0.0, 0.5, 1.0)
SUPPORTED_SUPERFLEX = (False, True)

ELBOBERTO_PROVIDER = "elboberto"
ELBOBERTO_POST_URL = (
    "https://old.reddit.com/r/fantasyfootball/comments/1uttmpp/"
    "elbobertos_custom_auction_value_generator_2026/"
)


def _ppr_label(ppr: float) -> str:
    value = float(ppr)
    return str(int(value)) if value.is_integer() else str(value)


def config_key(teams: int, ppr: float, superflex: bool) -> str:
    return f"{int(teams)}|{_ppr_label(ppr)}|{'sf' if superflex else '1qb'}"


def expected_config_keys(
    team_sizes: Sequence[int] = SUPPORTED_TEAM_SIZES,
    ppr_values: Sequence[float] = SUPPORTED_PPR,
    superflex_values: Sequence[bool] = SUPPORTED_SUPERFLEX,
) -> List[str]:
    return [
        config_key(teams, ppr, superflex)
        for teams in team_sizes
        for ppr in ppr_values
        for superflex in superflex_values
    ]


def discover_elboberto_workbook_url(post_html: str) -> str:
    """Return the current public Dropbox ``.xlsm`` link from the Reddit post."""
    soup = BeautifulSoup(post_html or "", "html.parser")
    candidates: List[str] = []
    for anchor in soup.find_all("a", href=True):
        href = re.sub(
            r"\s+", "", _html.unescape(str(anchor.get("href") or ""))
        )
        lower = href.lower()
        if "dropbox.com/" in lower and ".xlsm" in lower:
            candidates.append(href)
    if not candidates:
        # Keep discovery resilient to a stripped-down fixture that contains a
        # bare URL rather than an anchor.
        candidates = re.findall(
            r"https?://[^\s\"'<>]*dropbox\.com/[^\s\"'<>]*\.xlsm[^\s\"'<>]*",
            _html.unescape(post_html or ""),
            flags=re.IGNORECASE,
        )
    if not candidates:
        raise ValueError("No public ElBoberto .xlsm Dropbox link found")

    parts = urlsplit(candidates[0])
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query["dl"] = "1"
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), ""))


def elboberto_version_from_url(url: str) -> Optional[str]:
    match = re.search(
        r"(?:^|/)\d{4}_FantasyFootball_([0-9]+(?:\.[0-9]+)*)_elboberto\.xlsm",
        urlsplit(url).path,
        flags=re.IGNORECASE,
    )
    return match.group(1) if match else None


def validate_rankings_blob(
    blob: Any,
    *,
    expected_year: Optional[Any] = None,
    required_keys: Optional[Iterable[str]] = None,
    min_players_per_config: int = 100,
) -> List[str]:
    """Validate a provider-produced canonical rankings candidate.

    Validation checks identity, exact configuration coverage, finite finished
    values, duplicate IDs, and rank integrity.  It never repairs or derives a
    missing provider value.
    """
    if not isinstance(blob, dict):
        return ["candidate is not an object"]

    errors: List[str] = []
    try:
        schema_version = int(blob.get("schema_version") or 0)
    except (TypeError, ValueError):
        schema_version = 0
    if schema_version != SCHEMA_VERSION:
        errors.append("unsupported schema_version")
    if expected_year is not None and str(blob.get("year")) != str(expected_year):
        errors.append(
            f"year mismatch: expected {expected_year}, got {blob.get('year')}"
        )
    provider = str(blob.get("provider") or "").strip()
    if not provider:
        errors.append("provider metadata is missing")
    if not str(blob.get("generated_at_utc") or "").strip():
        errors.append("generated_at_utc is missing")

    configs = blob.get("configs")
    if not isinstance(configs, dict) or not configs:
        errors.append("configs are missing")
        return errors

    required = list(required_keys) if required_keys is not None else expected_config_keys()
    missing = [key for key in required if key not in configs]
    if missing:
        errors.append(f"missing configs: {', '.join(missing[:10])}")

    for key in required:
        cfg = configs.get(key)
        if not isinstance(cfg, dict):
            continue
        players = cfg.get("players")
        if not isinstance(players, list):
            errors.append(f"{key}: players is not a list")
            continue
        if len(players) < min_players_per_config:
            errors.append(
                f"{key}: only {len(players)} players; need {min_players_per_config}"
            )

        seen_ids = set()
        seen_ranks = set()
        usable = 0
        for index, row in enumerate(players):
            if not isinstance(row, dict):
                errors.append(f"{key}: player row {index + 1} is not an object")
                continue
            player_id = str(row.get("player_id") or "").strip()
            if not player_id:
                errors.append(f"{key}: player row {index + 1} has no player_id")
                continue
            if player_id in seen_ids:
                errors.append(f"{key}: duplicate player_id {player_id}")
            seen_ids.add(player_id)

            value = row.get("vbd")
            if isinstance(value, (int, float)) and math.isfinite(float(value)):
                usable += 1
            rank = row.get("overall_rank")
            if not isinstance(rank, int) or rank < 1:
                errors.append(f"{key}: {player_id} has invalid overall_rank")
            elif rank in seen_ranks:
                errors.append(f"{key}: duplicate overall_rank {rank}")
            else:
                seen_ranks.add(rank)
        if usable < min_players_per_config:
            errors.append(
                f"{key}: only {usable} finite provider values; "
                f"need {min_players_per_config}"
            )

    return errors


def publish_rankings_candidate(
    candidate: Dict[str, Any],
    *,
    year: Any,
    upload: Callable[[Any, str], None],
    load: Callable[[str], Optional[Any]],
    required_keys: Optional[Iterable[str]] = None,
    min_players_per_config: int = 100,
) -> List[str]:
    """Validate, snapshot the prior healthy blob, and publish ``candidate``."""
    errors = validate_rankings_blob(
        candidate,
        expected_year=year,
        required_keys=required_keys,
        min_players_per_config=min_players_per_config,
    )
    if errors:
        return errors

    blob_name = f"draft_rankings_{year}.json"
    previous_name = f"draft_rankings_{year}_prev.json"
    existing = load(blob_name)
    if isinstance(existing, dict) and existing.get("configs"):
        upload(existing, previous_name)
    upload(candidate, blob_name)
    return []