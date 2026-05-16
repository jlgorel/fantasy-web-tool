"""One-shot historical Sleeper backfill.

Populates the Azure blobs the Wrapped pipeline and trade-eval pipeline
read for past seasons:

  * ``player_season_scoring_{year}.json`` (legacy slim blob)
  * ``owned_history_{year}.json`` (per-week ownership %)
  * ``trade_eval/scoring/{year}.json`` (per-player season summary)
  * ``trade_eval/scoring/raw/{year}/{week}.json`` (full raw stats per week)
  * ``trade_eval/scoring/_index.json``

Idempotent: ``--skip-existing`` (default true) checks blob presence before
re-fetching. Set ``--dry-run`` to print the planned blob writes without
hitting Sleeper or Azure.

Usage::

    # Default: backfill 2017..(current_fantasy_year - 1)
    python tools/bootstrap_historical_sleeper.py

    # Specific years
    python tools/bootstrap_historical_sleeper.py --years 2022 2023 2024

    # Force-overwrite an existing year
    python tools/bootstrap_historical_sleeper.py --years 2024 --no-skip-existing

    # Legacy blob only (no trade_eval/ writes)
    python tools/bootstrap_historical_sleeper.py --mode legacy
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "azure-functions"))

import requests  # noqa: E402

from trade_eval import blob_layout  # noqa: E402
from trade_eval.legacy_season_scoring import (  # noqa: E402
    build_legacy_season_scoring_blob,
)
from trade_eval.ownership_history import build_ownership_history  # noqa: E402
from trade_eval.sleeper_scoring import bootstrap_history  # noqa: E402

LEGACY_BLOB = "player_season_scoring_{year}.json"
OWNERSHIP_BLOB = "owned_history_{year}.json"

DEFAULT_START_YEAR = 2017


# ---------------------------------------------------------------------------
# HTTP + Azure helpers (kept inline; this is a tool, not packaged code).
# ---------------------------------------------------------------------------
RETRYABLE_STATUS = {429, 500, 502, 503, 504}


def http_get_json(url: str, *, timeout: int = 30, max_retries: int = 3) -> Any:
    last_exc: Optional[Exception] = None
    for attempt in range(max_retries + 1):
        try:
            resp = requests.get(url, timeout=timeout)
        except requests.RequestException as exc:
            last_exc = exc
            if attempt < max_retries:
                time.sleep(0.5 * (2 ** attempt))
                continue
            raise
        if resp.status_code in RETRYABLE_STATUS and attempt < max_retries:
            time.sleep(0.5 * (2 ** attempt))
            continue
        resp.raise_for_status()
        return resp.json()
    if last_exc:
        raise last_exc
    raise RuntimeError(f"unreachable: {url}")


def _make_azure_clients(
    connection_string: str, container: str
):
    from azure.storage.blob import BlobServiceClient

    svc = BlobServiceClient.from_connection_string(connection_string)
    container_client = svc.get_container_client(container)
    return svc, container_client


def make_blob_io(
    connection_string: str,
    container: str,
    *,
    dry_run: bool,
) -> Tuple[Callable[[Any, str], None], Callable[[str], Optional[Any]], Callable[[str], bool]]:
    """Return (upload, load, exists) callables.

    In ``dry_run`` mode, ``upload`` only logs the intended blob name and
    payload size; ``load`` always returns None; ``exists`` always returns
    False so the run actually plans full work.
    """
    if dry_run:
        def upload(data: Any, blob_name: str) -> None:
            size = len(json.dumps(data))
            logging.info("[dry-run] would upload %s (%d bytes)", blob_name, size)

        def load(_blob_name: str) -> Optional[Any]:
            return None

        def exists(_blob_name: str) -> bool:
            return False

        return upload, load, exists

    svc, container_client = _make_azure_clients(connection_string, container)

    def upload(data: Any, blob_name: str) -> None:
        blob_client = container_client.get_blob_client(blob_name)
        blob_client.upload_blob(json.dumps(data), overwrite=True)
        logging.info("uploaded %s", blob_name)

    def load(blob_name: str) -> Optional[Any]:
        try:
            blob_client = container_client.get_blob_client(blob_name)
            return json.loads(blob_client.download_blob().readall())
        except Exception:
            return None

    def exists(blob_name: str) -> bool:
        try:
            blob_client = container_client.get_blob_client(blob_name)
            return blob_client.exists()
        except Exception:
            return False

    return upload, load, exists


# ---------------------------------------------------------------------------
# Per-year orchestration
# ---------------------------------------------------------------------------
def fetch_players_meta() -> Dict[str, Dict[str, Any]]:
    """One-shot current /players/nfl snapshot used to attach full_name +
    fantasy_positions to historical scoring entries.

    Players who retired before this snapshot will simply lack a name /
    positions, and Wrapped accolades degrade gracefully on missing meta.
    """
    logging.info("fetching current /players/nfl meta snapshot...")
    data = http_get_json("https://api.sleeper.app/v1/players/nfl", timeout=60)
    slim: Dict[str, Dict[str, Any]] = {}
    for pid, pdata in data.items():
        if not isinstance(pdata, dict):
            continue
        slim[pid] = {
            "full_name": pdata.get("full_name"),
            "fantasy_positions": pdata.get("fantasy_positions") or [],
        }
    logging.info("loaded meta for %d players", len(slim))
    return slim


def backfill_legacy_year(
    year: int,
    players_meta: Dict[str, Dict[str, Any]],
    *,
    upload: Callable[[Any, str], None],
    exists: Callable[[str], bool],
    skip_existing: bool,
) -> bool:
    blob_name = LEGACY_BLOB.format(year=year)
    if skip_existing and exists(blob_name):
        logging.info("[%s] legacy blob already present, skipping (%s)", year, blob_name)
        return False
    logging.info("[%s] building legacy scoring blob...", year)
    blob = build_legacy_season_scoring_blob(
        year, players_meta, http_get_json=http_get_json
    )
    upload(blob, blob_name)
    return True


def backfill_ownership_year(
    year: int,
    *,
    upload: Callable[[Any, str], None],
    exists: Callable[[str], bool],
    skip_existing: bool,
) -> bool:
    blob_name = OWNERSHIP_BLOB.format(year=year)
    if skip_existing and exists(blob_name):
        logging.info("[%s] ownership blob already present, skipping (%s)", year, blob_name)
        return False
    logging.info("[%s] building ownership history...", year)
    blob = build_ownership_history(year, http_get_json=http_get_json)
    if not blob:
        logging.warning("[%s] ownership backfill produced empty blob -- skipping write", year)
        return False
    upload(blob, blob_name)
    return True


def backfill_trade_eval_year(
    year: int,
    *,
    upload: Callable[[Any, str], None],
    load: Callable[[str], Optional[Any]],
    exists: Callable[[str], bool],
    skip_existing: bool,
) -> bool:
    summary_blob = blob_layout.scoring_summary_blob(year)
    if skip_existing and exists(summary_blob):
        logging.info(
            "[%s] trade_eval scoring summary already present, skipping (%s)",
            year, summary_blob,
        )
        return False
    logging.info("[%s] bootstrapping trade_eval scoring (writes raw + summary)...", year)
    result = bootstrap_history(
        [year], http_get_json=http_get_json, blob_upload=upload, blob_load=load
    )
    return bool(result.get(year))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _default_years() -> List[int]:
    from _fantasy_common import get_current_fantasy_year  # noqa: WPS433

    end = int(get_current_fantasy_year())
    return list(range(DEFAULT_START_YEAR, end))


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument(
        "--years",
        type=int,
        nargs="+",
        default=None,
        help=f"Seasons to backfill (default: {DEFAULT_START_YEAR}..current-1).",
    )
    p.add_argument(
        "--mode",
        choices=("legacy", "trade_eval", "ownership", "both", "all"),
        default="all",
        help="Which blob families to write. 'all'/'both' writes legacy + ownership + trade_eval.",
    )
    p.add_argument(
        "--skip-existing",
        dest="skip_existing",
        action="store_true",
        default=True,
        help="Skip a (year, family) combo if its blob already exists (default).",
    )
    p.add_argument(
        "--no-skip-existing",
        dest="skip_existing",
        action="store_false",
        help="Overwrite existing blobs.",
    )
    p.add_argument("--dry-run", action="store_true", help="Plan only; no HTTP / Azure writes.")
    p.add_argument(
        "--container",
        default="fantasyjsons",
        help="Azure blob container (default: fantasyjsons).",
    )
    p.add_argument(
        "--connection-string",
        default=None,
        help="Azure storage connection string. Defaults to env AZURE_STORAGE_CONNECTION_STRING.",
    )
    p.add_argument("-v", "--verbose", action="store_true")
    return p.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    years = args.years or _default_years()
    logging.info("Backfilling years: %s (mode=%s, skip_existing=%s, dry_run=%s)",
                 years, args.mode, args.skip_existing, args.dry_run)

    if not args.dry_run:
        conn = args.connection_string or os.environ.get("AZURE_STORAGE_CONNECTION_STRING")
        if not conn:
            print(
                "ERROR: --connection-string not provided and "
                "AZURE_STORAGE_CONNECTION_STRING is unset. Use --dry-run to plan only.",
                file=sys.stderr,
            )
            return 2
    else:
        conn = ""

    upload, load, exists = make_blob_io(conn, args.container, dry_run=args.dry_run)

    want_legacy = args.mode in ("legacy", "both", "all")
    want_ownership = args.mode in ("ownership", "both", "all")
    want_trade_eval = args.mode in ("trade_eval", "both", "all")

    # Only fetch the meta snapshot if we're writing the legacy blob (only
    # consumer of full_name / fantasy_positions).
    players_meta: Dict[str, Dict[str, Any]] = (
        fetch_players_meta() if want_legacy and not args.dry_run else {}
    )

    summary: Dict[int, Dict[str, bool]] = {}
    for year in years:
        per_year: Dict[str, bool] = {}
        try:
            if want_legacy:
                per_year["legacy"] = backfill_legacy_year(
                    year, players_meta, upload=upload, exists=exists,
                    skip_existing=args.skip_existing,
                )
            if want_ownership:
                per_year["ownership"] = backfill_ownership_year(
                    year, upload=upload, exists=exists,
                    skip_existing=args.skip_existing,
                )
            if want_trade_eval:
                per_year["trade_eval"] = backfill_trade_eval_year(
                    year, upload=upload, load=load, exists=exists,
                    skip_existing=args.skip_existing,
                )
        except Exception:
            logging.exception("Year %s failed; continuing", year)
            per_year["error"] = True
        summary[year] = per_year

    print()
    print("=" * 60)
    print("Backfill summary:")
    for year, families in summary.items():
        flags = ", ".join(f"{k}={'OK' if v else 'skip'}" for k, v in families.items())
        print(f"  {year}: {flags}")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
