"""Fetch, recalculate, validate, and optionally publish DraftSheets values.

Without ``--all-profiles`` this publishes the one configuration exposed by the
public Google Sheet. The weekly ``--all-profiles`` mode downloads the current
XLSX, drives desktop Excel through the curated 24-profile/576-config grid,
checks the public configuration for calculation parity, then publishes every
validated profile with registries last. It never recreates DraftSheets'
projection/VOR formulas.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import requests

REPO = Path(__file__).resolve().parent.parent
AZURE_DIR = REPO / "azure-functions"
DEFAULT_OUT_DIR = REPO / "tests" / "fixtures" / "blobs"
DEFAULT_PLAYERS = DEFAULT_OUT_DIR / "players.json"
DEFAULT_SETTINGS = AZURE_DIR / "local.settings.json"
DEFAULT_CONTAINER = "fantasyjsons"
SCORING_RANGE = "A1:P23"
DRAFTSHEET_RANGE = "A1:AH300"


def _module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


draft_values = _module("draftsheets_draft_values", AZURE_DIR / "draft_values.py")
draftsheets = _module("draftsheets_values", AZURE_DIR / "draftsheets_values.py")


def _connection_string(explicit: Optional[str], settings_path: Path) -> Optional[str]:
    if explicit:
        return explicit
    if os.environ.get("AZURE_STORAGE_CONNECTION_STRING"):
        return os.environ["AZURE_STORAGE_CONNECTION_STRING"]
    try:
        settings = json.loads(settings_path.read_text(encoding="utf-8-sig"))
        return (settings.get("Values") or {}).get("AZURE_STORAGE_CONNECTION_STRING")
    except Exception:
        return None


def _blob_io(connection: str, container_name: str):
    from azure.storage.blob import BlobServiceClient

    container = BlobServiceClient.from_connection_string(connection).get_container_client(
        container_name
    )

    def load(name: str) -> Optional[Any]:
        try:
            return json.loads(
                container.get_blob_client(name).download_blob(
                    connection_timeout=30, read_timeout=60,
                ).readall()
            )
        except Exception:
            return None

    def upload(data: Any, name: str) -> None:
        raw = json.dumps(data, separators=(",", ":"), allow_nan=False)
        last_error: Optional[Exception] = None
        for attempt in range(1, 4):
            try:
                container.get_blob_client(name).upload_blob(
                    raw,
                    overwrite=True,
                    connection_timeout=30,
                    read_timeout=90,
                    max_concurrency=2,
                )
                print(f"  uploaded {name} ({len(raw):,} bytes)")
                return
            except Exception as exc:
                last_error = exc
                print(f"  upload retry {attempt}/3 for {name}: {exc}", file=sys.stderr)
                if attempt < 3:
                    time.sleep(attempt)
        assert last_error is not None
        raise last_error

    return load, upload


def _fetch_text(url: str) -> str:
    response = requests.get(
        url, timeout=60,
        headers={"User-Agent": "fantasy-web-tool/1.0 (DraftSheets public export)"},
    )
    response.raise_for_status()
    if "csv" not in str(response.headers.get("Content-Type") or "").lower():
        raise ValueError(f"DraftSheets returned non-CSV content for {url}")
    return response.content.decode("utf-8-sig", errors="strict")


def _download_workbook(destination: Path) -> tuple[Path, dict[str, Any]]:
    response = requests.get(
        draftsheets.XLSX_EXPORT_URL,
        timeout=120,
        headers={"User-Agent": "fantasy-web-tool/1.0 (DraftSheets public export)"},
    )
    response.raise_for_status()
    content = response.content
    if len(content) < 100_000 or not content.startswith(b"PK"):
        raise ValueError(
            f"DraftSheets download is not a plausible XLSX workbook ({len(content)} bytes)"
        )
    path = destination / f"DraftSheets_{datetime.now().year}.xlsx"
    path.write_bytes(content)
    return path, {
        "source_file": path.name,
        "source_content_sha256": hashlib.sha256(content).hexdigest(),
        "source_content_bytes": len(content),
        "source_last_modified": response.headers.get("Last-Modified"),
        "retrieved_at_utc": datetime.now(timezone.utc).isoformat(),
    }


def _set_excel_config(scoring, config: dict[str, Any]) -> None:
    starters = config["starters"]
    scoring.range("I4:P4").value = [[
        int(config["teams"]),
        int(starters["QB"]),
        int(starters["RB"]),
        int(starters["WR"]),
        int(starters["TE"]),
        int(starters["FLEX"]),
        int(config["bench_size"]),
        1 if config["superflex"] else 0,
    ]]
    scoring.range("B8").value = int(config["passing_td"])
    scoring.range("B9").value = int(config.get("interceptions", -1))
    scoring.range("B16:B18").value = [
        [float(config["ppr"])],
        [float(config["ppr"])],
        [float(config["ppr"])],
    ]


def _draftsheet_display_values(rows: Any) -> list[list[Any]]:
    """Render provider columns like Google ``getDisplayValues``/GViz CSV."""
    if not isinstance(rows, list):
        raise ValueError("DraftSheet Excel range is not a row array")
    integer_columns = {1, 4, 5, 7, 11, 14, 15, 17, 21, 24, 25, 27}
    percentage_columns = {6, 16, 26}
    rendered: list[list[Any]] = []
    for raw_row in rows:
        row = list(raw_row) if isinstance(raw_row, list) else [raw_row]
        for column in integer_columns:
            if column < len(row) and isinstance(row[column], (int, float)):
                row[column] = f"{float(row[column]):.0f}"
        for column in percentage_columns:
            if column < len(row) and isinstance(row[column], (int, float)):
                row[column] = f"{float(row[column]):.0%}"
        rendered.append(row)
    return rendered


def _build_excel_profiles(
    year: Any,
    workbook_path: Path,
    players: dict[str, Any],
    *,
    visible: bool,
    source_metadata: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    import xlwings as xw

    resolver = draftsheets.NameResolver(players)
    profiles = draftsheets.common_profiles()
    expected_keys = set(draft_values.expected_config_keys())
    results: dict[str, dict[str, Any]] = {}
    app = xw.App(visible=visible, add_book=False)
    app.display_alerts = False
    app.screen_updating = False
    try:
        workbook = app.books.open(str(workbook_path), update_links=False)
        scoring = workbook.sheets["Scoring"]
        draftsheet = workbook.sheets["DraftSheet"]
        total = len(profiles) * 24
        completed = 0
        for profile in profiles:
            bridge_results = []
            for config in draftsheets.bridge_configurations(profile):
                _set_excel_config(scoring, config)
                app.calculate()
                bridge_results.append({
                    "scoring_values": scoring.range(SCORING_RANGE).value,
                    "draftsheet_values": _draftsheet_display_values(
                        draftsheet.range(DRAFTSHEET_RANGE).value
                    ),
                })
                completed += 1
                print(
                    f"    [{completed:>3}/{total}] "
                    f"{config['teams']}t {config['ppr']:>3} PPR "
                    f"{'SF' if config['superflex'] else '1QB'} "
                    f"WR{profile['starters']['WR']} FLEX{profile['starters']['FLEX']} "
                    f"BN{profile['bench_size']} PTD{profile['passing_td']}"
                )
            candidate = draftsheets.build_draftsheets_profile_from_bridge(
                year,
                players,
                {"ok": True, "results": bridge_results},
                resolver_factory=lambda _players: resolver,
            )
            candidate.update(source_metadata)
            candidate["provider"] = draftsheets.PROVIDER_ID
            candidate["source"] = draftsheets.PROVIDER_NAME
            candidate["source_url"] = draftsheets.SOURCE_URL
            candidate["attribution"] = draftsheets.PROVIDER_NAME
            profile_id = candidate["profile"]["id"]
            errors = draft_values.validate_rankings_blob(
                candidate,
                expected_year=year,
                required_keys=expected_keys,
                min_players_per_config=100,
            )
            if errors:
                raise ValueError(
                    f"Rejected DraftSheets {profile_id}: {'; '.join(errors[:20])}"
                )
            results[profile_id] = candidate
        workbook.close()
    finally:
        app.quit()
    return results


def _validate_public_parity(
    generated: dict[str, dict[str, Any]],
    public_candidate: dict[str, Any],
    *,
    tolerance: float = 0.05,
) -> None:
    profile_id = public_candidate["profile"]["id"]
    generated_profile = generated.get(profile_id)
    if not generated_profile:
        raise ValueError(f"Generated grid is missing public profile {profile_id}")
    key = next(iter(public_candidate["configs"]))
    expected_rows = public_candidate["configs"][key]["players"]
    actual_rows = generated_profile["configs"][key]["players"]
    expected = {
        str(row["player_id"]): (float(row["vbd"]), str(row["pos"]))
        for row in expected_rows
    }
    actual = {
        str(row["player_id"]): (float(row["vbd"]), str(row["pos"]))
        for row in actual_rows
    }
    differences = []
    position_differences = []
    compared = 0
    for player_id, (expected_value, expected_position) in expected.items():
        if player_id not in actual:
            continue
        compared += 1
        actual_value, actual_position = actual[player_id]
        difference = abs(actual_value - expected_value)
        if difference > tolerance:
            differences.append((player_id, expected_value, actual_value))
        if actual_position != expected_position:
            position_differences.append(
                (player_id, expected_position, actual_position)
            )
    required_overlap = max(25, int(len(expected) * 0.9))
    if compared < required_overlap:
        raise ValueError(
            f"Only {compared}/{len(expected)} players overlapped the public parity board"
        )
    if position_differences:
        sample = ", ".join(
            f"{pid}: public {expected_pos}, Excel {actual_pos}"
            for pid, expected_pos, actual_pos in position_differences[:5]
        )
        raise ValueError(f"Local Excel positions do not match public DraftSheets: {sample}")
    if differences:
        sample = ", ".join(
            f"{pid}: public {expected:.2f}, Excel {actual_value:.2f}"
            for pid, expected, actual_value in differences[:5]
        )
        raise ValueError(f"Local Excel does not match public DraftSheets: {sample}")
    print(f"Public parity passed for {compared}/{len(expected)} players in {key}.")


def _profile_registry_name(year: Any) -> str:
    return f"draft_value_profiles_{year}_{draftsheets.PROVIDER_ID}.json"


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--year", default=str(datetime.now().year))
    parser.add_argument("--players", type=Path, default=DEFAULT_PLAYERS)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument(
        "--all-profiles", action="store_true",
        help="Use desktop Excel to generate the curated 24-profile/576-config grid.",
    )
    parser.add_argument(
        "--workbook", type=Path,
        help="Use a local DraftSheets XLSX instead of downloading the current public workbook.",
    )
    parser.add_argument("--visible", action="store_true", help="Show Excel while recalculating.")
    parser.add_argument("--upload", action="store_true")
    parser.add_argument("--container", default=DEFAULT_CONTAINER)
    parser.add_argument("--connection-string")
    parser.add_argument("--local-settings", type=Path, default=DEFAULT_SETTINGS)
    return parser.parse_args(argv)


def main(argv=None) -> int:
    started_at = time.monotonic()
    args = parse_args(argv)
    connection = _connection_string(args.connection_string, args.local_settings)
    if args.upload and not connection:
        print("FATAL: Azure connection string is unavailable.", file=sys.stderr)
        return 2
    load_blob = upload_blob = None
    if connection:
        load_blob, upload_blob = _blob_io(connection, args.container)

    if args.upload and load_blob is not None:
        players = load_blob("players.json") or {}
        print(f"Using production players.json ({len(players):,} identities).")
    else:
        players = json.loads(args.players.read_text(encoding="utf-8"))
        print(f"Using local players.json ({len(players):,} identities).")
    if not players:
        print("FATAL: players.json is unavailable.", file=sys.stderr)
        return 2

    scoring_csv = _fetch_text(draftsheets.SCORING_CSV_URL)
    draftsheet_csv = _fetch_text(draftsheets.DRAFTSHEET_CSV_URL)
    public_candidate = draftsheets.build_draftsheets_blob(
        args.year, players, scoring_csv, draftsheet_csv,
        resolver_factory=draftsheets.NameResolver,
    )

    if args.all_profiles:
        with tempfile.TemporaryDirectory(prefix="draftsheets_values_") as temp:
            if args.workbook:
                workbook_path = args.workbook.resolve()
                if not workbook_path.exists():
                    print(f"FATAL: workbook not found: {workbook_path}", file=sys.stderr)
                    return 2
                content = workbook_path.read_bytes()
                source_metadata = {
                    "source_file": workbook_path.name,
                    "source_content_sha256": hashlib.sha256(content).hexdigest(),
                    "source_content_bytes": len(content),
                    "retrieved_at_utc": datetime.now(timezone.utc).isoformat(),
                }
                print(f"Using local workbook {workbook_path.name}.")
            else:
                print("Downloading the current public DraftSheets workbook.")
                workbook_path, source_metadata = _download_workbook(Path(temp))
                print(
                    f"Downloaded {workbook_path.name} "
                    f"({source_metadata['source_content_bytes']:,} bytes)."
                )
            candidates = _build_excel_profiles(
                args.year,
                workbook_path,
                players,
                visible=args.visible,
                source_metadata=source_metadata,
            )
        _validate_public_parity(candidates, public_candidate)
    else:
        profile_id = public_candidate["profile"]["id"]
        errors = draft_values.validate_rankings_blob(
            public_candidate,
            expected_year=args.year,
            required_keys=list(public_candidate["configs"]),
            min_players_per_config=100,
        )
        if errors:
            print("REJECTED DraftSheets: " + "; ".join(errors[:20]), file=sys.stderr)
            return 1
        candidates = {profile_id: public_candidate}

    profile_registry_name = _profile_registry_name(args.year)
    existing_profile_registry = load_blob(profile_registry_name) if load_blob else None
    profile_entries = (
        {} if args.all_profiles
        else dict((existing_profile_registry or {}).get("profiles") or {})
    )
    blob_names: dict[str, str] = {}
    for profile_id, candidate in candidates.items():
        blob_name = draft_values.provider_profile_rankings_blob_name(
            args.year, draftsheets.PROVIDER_ID, profile_id,
        )
        blob_names[profile_id] = blob_name
        config_keys = list(candidate["configs"])
        profile_entries[profile_id] = {
            "id": profile_id,
            "blob_name": blob_name,
            "profile": candidate["profile"],
            "config_count": len(config_keys),
            "supported_config_keys": config_keys,
            "generated_at_utc": candidate["generated_at_utc"],
        }

    public_profile_id = public_candidate["profile"]["id"]
    default_profile_id = (
        public_profile_id if public_profile_id in profile_entries
        else (existing_profile_registry or {}).get("default_profile_id")
    )
    if default_profile_id not in profile_entries:
        default_profile_id = next(iter(profile_entries))
    latest_candidate = candidates.get(public_profile_id) or next(iter(candidates.values()))
    profile_registry = {
        "schema_version": 1,
        "year": str(args.year),
        "provider": draftsheets.PROVIDER_ID,
        "default_profile_id": default_profile_id,
        "generated_at_utc": latest_candidate["generated_at_utc"],
        "profiles": profile_entries,
    }
    registry_errors = draft_values.validate_profile_registry(
        profile_registry, expected_year=args.year,
    )
    if registry_errors:
        print("REJECTED profile registry: " + "; ".join(registry_errors), file=sys.stderr)
        return 1

    provider_registry_name = draft_values.value_providers_registry_blob_name(args.year)
    existing_provider_registry = load_blob(provider_registry_name) if load_blob else None
    providers = dict((existing_provider_registry or {}).get("providers") or {})
    providers[draftsheets.PROVIDER_ID] = {
        "id": draftsheets.PROVIDER_ID,
        "name": draftsheets.PROVIDER_NAME,
        "attribution": draftsheets.PROVIDER_NAME,
        "source_url": draftsheets.SOURCE_URL,
        "source_version": latest_candidate.get("source_version"),
        "generated_at_utc": latest_candidate["generated_at_utc"],
        "profile_registry_blob_name": profile_registry_name,
        "profile_count": len(profile_entries),
    }
    provider_registry = {
        "schema_version": 1,
        "year": str(args.year),
        "default_provider_id": (
            (existing_provider_registry or {}).get("default_provider_id")
            if (existing_provider_registry or {}).get("default_provider_id") in providers
            else draftsheets.PROVIDER_ID
        ),
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "providers": providers,
    }
    provider_errors = draft_values.validate_provider_registry(
        provider_registry, expected_year=args.year,
    )
    if provider_errors:
        print("REJECTED provider registry: " + "; ".join(provider_errors), file=sys.stderr)
        return 1

    args.out_dir.mkdir(parents=True, exist_ok=True)
    for profile_id, candidate in candidates.items():
        output = args.out_dir / blob_names[profile_id]
        output.write_text(
            json.dumps(candidate, separators=(",", ":"), allow_nan=False),
            encoding="utf-8",
        )
        print(f"Validated; wrote {output}.")
    for name, data in (
        (profile_registry_name, profile_registry),
        (provider_registry_name, provider_registry),
    ):
        (args.out_dir / name).write_text(
            json.dumps(data, separators=(",", ":"), allow_nan=False),
            encoding="utf-8",
        )
        print(f"Validated; wrote {args.out_dir / name}.")

    if args.upload:
        assert load_blob is not None and upload_blob is not None
        for profile_id, candidate in candidates.items():
            draft_values.publish_json_with_snapshot(
                candidate, blob_names[profile_id], upload=upload_blob, load=load_blob,
            )
        # Registries go last so partial runs cannot become discoverable.
        draft_values.publish_json_with_snapshot(
            profile_registry, profile_registry_name,
            upload=upload_blob, load=load_blob,
        )
        draft_values.publish_json_with_snapshot(
            provider_registry, provider_registry_name,
            upload=upload_blob, load=load_blob,
        )
        print(
            f"Published {len(candidates)} DraftSheets profile(s), "
            f"{sum(len(candidate['configs']) for candidate in candidates.values())} configs."
        )
    else:
        print(
            f"Dry publish complete for {len(candidates)} profile(s); "
            "add --upload to publish."
        )
    elapsed_minutes = (time.monotonic() - started_at) / 60
    print(f"DraftSheets refresh completed in {elapsed_minutes:.1f} minutes.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
