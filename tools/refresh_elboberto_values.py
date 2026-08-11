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
    if args.passing_td != 4 and args.upload:
        print(
            "FATAL: the current production config key represents the 4-point passing-TD "
            "profile. A 6-point profile needs the planned schema migration.",
            file=sys.stderr,
        )
        return 2

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

        candidate = builder.build_year(
            str(args.year),
            source_path.parent,
            resolver,
            configs,
            args.budget,
            args.max_overall,
            args.visible,
            source_file=source_path,
            source_metadata=source_metadata,
            passing_td=args.passing_td,
            bench_size=args.bench_size,
        )
        if candidate is None:
            return 1

    required = draft_values.expected_config_keys()
    errors = draft_values.validate_rankings_blob(
        candidate,
        expected_year=args.year,
        required_keys=required,
        min_players_per_config=100,
    )
    if errors:
        print("REJECTED: generated rankings did not pass validation:", file=sys.stderr)
        for error in errors[:20]:
            print(f"  - {error}", file=sys.stderr)
        return 1

    args.out_dir.mkdir(parents=True, exist_ok=True)
    output = args.out_dir / f"draft_rankings_{args.year}.json"
    output.write_text(json.dumps(candidate, separators=(",", ":"), allow_nan=False), encoding="utf-8")
    print(f"Validated {len(candidate['configs'])} configs; wrote {output}.")

    if args.upload:
        assert upload_blob is not None and load_blob is not None
        errors = draft_values.publish_rankings_candidate(
            candidate,
            year=args.year,
            upload=upload_blob,
            load=load_blob,
            required_keys=required,
            min_players_per_config=100,
        )
        if errors:
            print("REJECTED before upload: " + "; ".join(errors[:10]), file=sys.stderr)
            return 1
        print(f"Published guarded ElBoberto values for {args.year}.")
    else:
        print("Dry publish: add --upload to snapshot and publish the validated blob.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())