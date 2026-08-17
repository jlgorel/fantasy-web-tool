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


def value_profile_id(starters: Dict[str, Any], bench_size: int, passing_td: int) -> str:
    return "-".join([
        f"qb{int(starters.get('QB') or 0)}",
        f"rb{int(starters.get('RB') or 0)}",
        f"wr{int(starters.get('WR') or 0)}",
        f"te{int(starters.get('TE') or 0)}",
        f"flex{int(starters.get('FLEX') or 0)}",
        f"bn{int(bench_size)}",
        f"ptd{int(passing_td)}",
    ])


def profile_rankings_blob_name(year: Any, profile_id: str) -> str:
    return provider_profile_rankings_blob_name(
        year, ELBOBERTO_PROVIDER, profile_id,
    )


def provider_profile_rankings_blob_name(
    year: Any, provider_id: str, profile_id: str,
) -> str:
    provider = str(provider_id).strip().lower()
    profile = str(profile_id).strip().lower()
    allowed = "abcdefghijklmnopqrstuvwxyz0123456789-"
    if not provider or any(ch not in allowed for ch in provider):
        raise ValueError(f"Invalid value provider id: {provider_id!r}")
    if not profile or any(ch not in allowed for ch in profile):
        raise ValueError(f"Invalid value profile id: {profile_id!r}")
    return f"draft_rankings_{year}_{provider}_{profile}.json"


def profile_registry_blob_name(year: Any) -> str:
    return f"draft_value_profiles_{year}.json"


def value_providers_registry_blob_name(year: Any) -> str:
    return f"draft_value_providers_{year}.json"


def value_provider_status_blob_name(year: Any, provider_id: str) -> str:
    provider = str(provider_id).strip().lower()
    allowed = "abcdefghijklmnopqrstuvwxyz0123456789-"
    if not provider or any(ch not in allowed for ch in provider):
        raise ValueError(f"Invalid value provider id: {provider_id!r}")
    return f"draft_value_provider_status_{year}_{provider}.json"


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
    profile = blob.get("profile")
    if not isinstance(profile, dict):
        errors.append("profile metadata is missing")
    else:
        starters = profile.get("starters")
        try:
            expected_profile_id = value_profile_id(
                starters if isinstance(starters, dict) else {},
                int(profile.get("bench_size")),
                int(profile.get("passing_td")),
            )
        except (TypeError, ValueError):
            expected_profile_id = ""
        if not expected_profile_id or profile.get("id") not in (None, expected_profile_id):
            errors.append("profile id/settings are invalid")

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


def validate_profile_registry(registry: Any, *, expected_year: Any) -> List[str]:
    if not isinstance(registry, dict):
        return ["profile registry is not an object"]
    errors: List[str] = []
    if int(registry.get("schema_version") or 0) != 1:
        errors.append("unsupported profile registry schema_version")
    if str(registry.get("year")) != str(expected_year):
        errors.append("profile registry year mismatch")
    profiles = registry.get("profiles")
    if not isinstance(profiles, dict) or not profiles:
        errors.append("profile registry has no profiles")
        return errors
    default_id = registry.get("default_profile_id")
    if default_id not in profiles:
        errors.append("default_profile_id is missing from profiles")
    for profile_id, entry in profiles.items():
        if not isinstance(entry, dict):
            errors.append(f"{profile_id}: registry entry is not an object")
            continue
        settings = entry.get("profile")
        if not isinstance(settings, dict):
            errors.append(f"{profile_id}: profile settings are missing")
            continue
        expected_id = value_profile_id(
            settings.get("starters") or {},
            settings.get("bench_size") or 0,
            settings.get("passing_td") or 0,
        )
        if profile_id != expected_id:
            errors.append(f"{profile_id}: id does not match settings")
        if not str(entry.get("blob_name") or "").endswith(".json"):
            errors.append(f"{profile_id}: blob_name is invalid")
    return errors


def validate_provider_registry(registry: Any, *, expected_year: Any) -> List[str]:
    if not isinstance(registry, dict):
        return ["provider registry is not an object"]
    errors: List[str] = []
    if int(registry.get("schema_version") or 0) != SCHEMA_VERSION:
        errors.append("unsupported provider registry schema_version")
    if str(registry.get("year")) != str(expected_year):
        errors.append("provider registry year mismatch")
    providers = registry.get("providers")
    if not isinstance(providers, dict) or not providers:
        return errors + ["provider registry has no providers"]
    default_id = str(registry.get("default_provider_id") or "")
    if default_id not in providers:
        errors.append("default_provider_id is missing from providers")
    for provider_id, entry in providers.items():
        if not isinstance(entry, dict):
            errors.append(f"{provider_id}: provider entry is not an object")
            continue
        if str(entry.get("id") or "") != str(provider_id):
            errors.append(f"{provider_id}: id mismatch")
        registry_name = str(entry.get("profile_registry_blob_name") or "")
        if not registry_name.endswith(".json"):
            errors.append(f"{provider_id}: profile registry blob is invalid")
        try:
            provider_profile_rankings_blob_name(expected_year, provider_id, "profile")
        except ValueError as exc:
            errors.append(str(exc))
    return errors


def publish_json_with_snapshot(
    candidate: Dict[str, Any], blob_name: str, *,
    upload: Callable[[Any, str], None],
    load: Callable[[str], Optional[Any]],
) -> None:
    existing = load(blob_name)
    if isinstance(existing, dict):
        previous_name = blob_name[:-5] + "_prev.json" if blob_name.endswith(".json") else blob_name + "_prev"
        upload(existing, previous_name)
    upload(candidate, blob_name)


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