"""Download, generate, validate, and optionally publish ElBoberto values.

The provider owns the VBD calculation.  This tool only sets the supported
league inputs in the public workbook, asks desktop Excel to recalculate it,
normalizes the finished ``AvgVBD`` column to Sleeper IDs, and applies the same
snapshot-before-overwrite safety policy as the Azure ADP refresh.

Examples (PowerShell)::

    & ".venv/Scripts/python.exe" tools/refresh_elboberto_values.py
    & ".venv/Scripts/python.exe" tools/refresh_elboberto_values.py --upload
    & ".venv/Scripts/python.exe" tools/refresh_elboberto_values.py `
        --workbook tests/fixtures/drafthelp/2026_FantasyFootball_0.4_elboberto.xlsm
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple
from urllib.parse import unquote, urlsplit

import requests

REPO = Path(__file__).resolve().parent.parent
TOOLS = REPO / "tools"
AZURE_DIR = REPO / "azure-functions"
DEFAULT_OUT_DIR = REPO / "tests" / "fixtures" / "blobs"
DEFAULT_LOCAL_SETTINGS = AZURE_DIR / "local.settings.json"
DEFAULT_CONTAINER = "fantasyjsons"
DEFAULT_STARTERS = {"QB": 1, "RB": 2, "WR": 2, "TE": 1, "FLEX": 1}


def common_profiles():
    """Curated centralized grid: WR2/3 × FLEX1/2 × BN5/6/7 × pass-TD4/6."""
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

sys.path.insert(0, str(TOOLS))
import build_draft_rankings as builder  # noqa: E402


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


draft_values = _load_module("external_draft_values", AZURE_DIR / "draft_values.py")


def _http_get(url: str, *, timeout: int = 60) -> requests.Response:
    response = requests.get(
        url,
        timeout=timeout,
        headers={
            "User-Agent": (
                "fantasy-web-tool/1.0 "
                "(personal fantasy draft values refresh; contact: local user)"
            )
        },
    )
    response.raise_for_status()
    return response


def _filename_from_source_url(url: str, year: str) -> str:
    name = Path(unquote(urlsplit(url).path)).name
    if name.lower().endswith(".xlsm"):
        return name
    return f"{year}Rankings.xlsm"


def download_current_workbook(
    year: str,
    destination: Path,
    *,
    post_url: str = draft_values.ELBOBERTO_POST_URL,
    http_get: Callable[..., requests.Response] = _http_get,
) -> Tuple[Path, Dict[str, Any]]:
    post = http_get(post_url, timeout=30)
    workbook_url = draft_values.discover_elboberto_workbook_url(post.text)
    response = http_get(workbook_url, timeout=120)
    content = response.content
    if len(content) < 100_000 or not content.startswith(b"PK"):
        raise ValueError(
            f"ElBoberto download is not a plausible Excel workbook ({len(content)} bytes)"
        )

    filename = _filename_from_source_url(workbook_url, year)
    path = destination / filename
    path.write_bytes(content)
    retrieved_at = datetime.now(timezone.utc).isoformat()
    metadata = {
        "provider": draft_values.ELBOBERTO_PROVIDER,
        "source": "ElBoberto Custom Auction Value Generator",
        "source_url": workbook_url,
        "source_post_url": post_url,
        "source_version": draft_values.elboberto_version_from_url(workbook_url),
        "source_content_sha256": hashlib.sha256(content).hexdigest(),
        "source_content_bytes": len(content),
        "source_last_modified": response.headers.get("Last-Modified"),
        "retrieved_at_utc": retrieved_at,
        "attribution": "ElBoberto Custom Auction Value Generator",
    }
    return path, metadata


def _connection_string(
    explicit: Optional[str], local_settings: Path,
) -> Optional[str]:
    if explicit:
        return explicit
    from_env = os.environ.get("AZURE_STORAGE_CONNECTION_STRING")
    if from_env:
        return from_env
    try:
        settings = json.loads(local_settings.read_text(encoding="utf-8-sig"))
        value = (settings.get("Values") or {}).get("AZURE_STORAGE_CONNECTION_STRING")
        return str(value) if value else None
    except Exception:
        return None


def make_blob_io(
    connection_string: str, container: str,
) -> Tuple[Callable[[Any, str], None], Callable[[str], Optional[Any]]]:
    from azure.storage.blob import BlobServiceClient

    service = BlobServiceClient.from_connection_string(connection_string)
    container_client = service.get_container_client(container)

    def upload(data: Any, blob_name: str) -> None:
        raw = json.dumps(data, separators=(",", ":"), allow_nan=False)
        container_client.get_blob_client(blob_name).upload_blob(raw, overwrite=True)
        print(f"  uploaded {blob_name} ({len(raw):,} bytes)")

    def load(blob_name: str) -> Optional[Any]:
        try:
            raw = container_client.get_blob_client(blob_name).download_blob().readall()
            return json.loads(raw)
        except Exception:
            return None

    return upload, load


def _load_players(
    players_path: Path, load_blob: Optional[Callable[[str], Optional[Any]]],
) -> Dict[str, Dict[str, Any]]:
    if load_blob is not None:
        current = load_blob("players.json")
        if isinstance(current, dict) and current:
            print(f"Using production players.json ({len(current):,} identities).")
            return current
    data = json.loads(players_path.read_text(encoding="utf-8"))
    print(f"Using local players.json ({len(data):,} identities).")
    return data


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--year", default=str(datetime.now().year))
    parser.add_argument("--workbook", type=Path,
                        help="Use a local workbook instead of discovering/downloading the current one.")
    parser.add_argument("--post-url", default=draft_values.ELBOBERTO_POST_URL)
    parser.add_argument("--players", type=Path, default=builder.DEFAULT_PLAYERS)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--passing-td", type=int, choices=(4, 6), default=4)
    parser.add_argument("--bench-size", type=int, default=6)
    parser.add_argument("--wr-starters", type=int, choices=(2, 3), default=2)
    parser.add_argument("--flex-starters", type=int, choices=(1, 2), default=1)
    parser.add_argument("--all-profiles", action="store_true",
                        help="Generate the curated 24-profile registry in one Excel session.")
    parser.add_argument("--budget", type=int, default=builder.DEFAULT_AUCTION_BUDGET)
    parser.add_argument("--max-overall", type=int, default=300)
    parser.add_argument("--visible", action="store_true")
    parser.add_argument("--upload", action="store_true",
                        help="Snapshot and publish draft_rankings_{year}.json to Azure.")
    parser.add_argument("--container", default=DEFAULT_CONTAINER)
    parser.add_argument("--connection-string", default=None)
    parser.add_argument("--local-settings", type=Path, default=DEFAULT_LOCAL_SETTINGS)
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)

    connection = _connection_string(args.connection_string, args.local_settings)
    upload_blob = None
    load_blob = None
    if connection:
        upload_blob, load_blob = make_blob_io(connection, args.container)
    if args.upload and not connection:
        print(
            "FATAL: Azure connection string is unavailable in --connection-string, "
            "AZURE_STORAGE_CONNECTION_STRING, or local.settings.json.",
            file=sys.stderr,
        )
        return 2

    players = _load_players(args.players, load_blob if args.upload else None)
    resolver = builder.NameResolver(players)
    configs = [
        {"teams": teams, "ppr": ppr, "superflex": superflex}
        for teams in builder.SUPPORTED_TEAM_SIZES
        for ppr in builder.SUPPORTED_PPR
        for superflex in builder.SUPPORTED_SUPERFLEX
    ]

    with tempfile.TemporaryDirectory(prefix="elboberto_values_") as tmp:
        temp_dir = Path(tmp)
        if args.workbook:
            source_path = args.workbook.resolve()
            if not source_path.exists():
                print(f"FATAL: workbook not found: {source_path}", file=sys.stderr)
                return 2
            source_metadata = {
                "provider": draft_values.ELBOBERTO_PROVIDER,
                "source": "ElBoberto Custom Auction Value Generator",
                "source_url": None,
                "source_post_url": args.post_url,
                "source_version": draft_values.elboberto_version_from_url(source_path.name),
                "source_content_sha256": hashlib.sha256(source_path.read_bytes()).hexdigest(),
                "source_content_bytes": source_path.stat().st_size,
                "retrieved_at_utc": datetime.now(timezone.utc).isoformat(),
                "attribution": "ElBoberto Custom Auction Value Generator",
            }
            print(f"Using local workbook {source_path.name}.")
        else:
            print(f"Discovering current ElBoberto workbook from {args.post_url}")
            source_path, source_metadata = download_current_workbook(
                str(args.year), temp_dir, post_url=args.post_url,
            )
            print(
                f"Downloaded {source_path.name} "
                f"({source_metadata['source_content_bytes']:,} bytes, "
                f"version {source_metadata.get('source_version') or 'unknown'})."
            )

        profiles = common_profiles() if args.all_profiles else [{
            "starters": {
                "QB": 1, "RB": 2, "WR": args.wr_starters,
                "TE": 1, "FLEX": args.flex_starters,
            },
            "bench_size": args.bench_size,
            "passing_td": args.passing_td,
        }]
        candidates = builder.build_profiles(
            str(args.year), source_path, resolver, configs, profiles,
            budget=args.budget, max_overall=args.max_overall,
            visible=args.visible, source_metadata=source_metadata,
        )

    required = draft_values.expected_config_keys()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    registry_entries: Dict[str, Any] = {}
    standard_default_id = draft_values.value_profile_id(DEFAULT_STARTERS, 6, 4)
    for profile_id, candidate in candidates.items():
        errors = draft_values.validate_rankings_blob(
            candidate, expected_year=args.year, required_keys=required,
            min_players_per_config=100,
        )
        if errors:
            print(f"REJECTED {profile_id}: " + "; ".join(errors[:20]), file=sys.stderr)
            return 1
        blob_name = draft_values.profile_rankings_blob_name(args.year, profile_id)
        output = args.out_dir / blob_name
        output.write_text(
            json.dumps(candidate, separators=(",", ":"), allow_nan=False),
            encoding="utf-8",
        )
        registry_entries[profile_id] = {
            "id": profile_id,
            "blob_name": blob_name,
            "profile": candidate["profile"],
            "config_count": len(candidate["configs"]),
            "generated_at_utc": candidate["generated_at_utc"],
        }
        print(f"Validated {profile_id}; wrote {output}.")

    default_profile_id = (
        standard_default_id if standard_default_id in registry_entries
        else next(iter(registry_entries))
    )
    registry = {
        "schema_version": 1,
        "year": str(args.year),
        "provider": draft_values.ELBOBERTO_PROVIDER,
        "default_profile_id": default_profile_id,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "profiles": registry_entries,
    }
    registry_errors = draft_values.validate_profile_registry(
        registry, expected_year=args.year,
    )
    if registry_errors:
        print("REJECTED registry: " + "; ".join(registry_errors), file=sys.stderr)
        return 1
    registry_path = args.out_dir / draft_values.profile_registry_blob_name(args.year)
    registry_path.write_text(json.dumps(registry, separators=(",", ":")), encoding="utf-8")
    if standard_default_id in candidates:
        legacy_path = args.out_dir / f"draft_rankings_{args.year}.json"
        legacy_path.write_text(
            json.dumps(
                candidates[standard_default_id],
                separators=(",", ":"),
                allow_nan=False,
            ),
            encoding="utf-8",
        )
        print(f"Wrote legacy default {legacy_path}.")

    provider_entry = {
        "id": draft_values.ELBOBERTO_PROVIDER,
        "name": "ElBoberto Custom Auction Value Generator",
        "attribution": source_metadata.get("attribution"),
        "source_url": source_metadata.get("source_url"),
        "source_version": source_metadata.get("source_version"),
        "generated_at_utc": registry["generated_at_utc"],
        "profile_registry_blob_name": draft_values.profile_registry_blob_name(args.year),
        "profile_count": len(registry_entries),
    }
    provider_registry = {
        "schema_version": 1,
        "year": str(args.year),
        "default_provider_id": draft_values.ELBOBERTO_PROVIDER,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "providers": {draft_values.ELBOBERTO_PROVIDER: provider_entry},
    }
    provider_errors = draft_values.validate_provider_registry(
        provider_registry, expected_year=args.year,
    )
    if provider_errors:
        print("REJECTED provider registry: " + "; ".join(provider_errors), file=sys.stderr)
        return 1
    provider_registry_path = (
        args.out_dir / draft_values.value_providers_registry_blob_name(args.year)
    )
    provider_registry_path.write_text(
        json.dumps(provider_registry, separators=(",", ":")), encoding="utf-8",
    )

    if args.upload:
        assert upload_blob is not None and load_blob is not None
        for profile_id, candidate in candidates.items():
            blob_name = registry_entries[profile_id]["blob_name"]
            draft_values.publish_json_with_snapshot(
                candidate, blob_name, upload=upload_blob, load=load_blob,
            )
        if standard_default_id in candidates:
            errors = draft_values.publish_rankings_candidate(
                candidates[standard_default_id], year=args.year,
                upload=upload_blob, load=load_blob, required_keys=required,
                min_players_per_config=100,
            )
            if errors:
                print("REJECTED default publish: " + "; ".join(errors[:10]), file=sys.stderr)
                return 1
        # The profile registry follows its blobs; the provider registry below
        # is the final discoverability boundary.
        draft_values.publish_json_with_snapshot(
            registry, draft_values.profile_registry_blob_name(args.year),
            upload=upload_blob, load=load_blob,
        )
        existing_providers = load_blob(
            draft_values.value_providers_registry_blob_name(args.year)
        )
        if isinstance(existing_providers, dict):
            merged = dict(existing_providers.get("providers") or {})
            merged[draft_values.ELBOBERTO_PROVIDER] = provider_entry
            provider_registry["providers"] = merged
            if provider_registry.get("default_provider_id") not in merged:
                provider_registry["default_provider_id"] = draft_values.ELBOBERTO_PROVIDER
        provider_errors = draft_values.validate_provider_registry(
            provider_registry, expected_year=args.year,
        )
        if provider_errors:
            print(
                "REJECTED provider registry publish: " + "; ".join(provider_errors),
                file=sys.stderr,
            )
            return 1
        # Provider registry is the final discoverability boundary. It cannot
        # reference this provider until both profile blobs and profile registry exist.
        draft_values.publish_json_with_snapshot(
            provider_registry,
            draft_values.value_providers_registry_blob_name(args.year),
            upload=upload_blob, load=load_blob,
        )
        print(f"Published {len(candidates)} ElBoberto profiles for {args.year}.")
    else:
        print(f"Dry publish: validated {len(candidates)} profile(s); add --upload to publish.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())