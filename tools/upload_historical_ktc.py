"""One-shot uploader for the historical KTC rankings blob.

Pushes the locally-built ``tests/fixtures/blobs/historical_KTC_rankings.json``
(produced by ``tools/build_historical_ktc_json.py``) up to Azure Blob
Storage at the path the trade evaluator reads from
(:func:`trade_eval.blob_layout.ktc_historical_blob`).

This is intentionally NOT a pytest target -- it talks to live Azure. Run
it manually once, then the daily appender (``trade_eval/ktc_top500_daily.py``)
takes over.

Usage::

    $env:AZURE_STORAGE_CONNECTION_STRING = "<conn>"
    python tools/upload_historical_ktc.py
    # ...or with explicit paths / a dry-run preview:
    python tools/upload_historical_ktc.py --dry-run
    python tools/upload_historical_ktc.py --source path/to/file.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DEFAULT_SOURCE = REPO / "tests" / "fixtures" / "blobs" / "historical_KTC_rankings.json"
# Mirrors azure-functions/trade_eval/blob_layout.py::ktc_historical_blob().
DEFAULT_BLOB_PATH = "trade_eval/values/ktc/historical_KTC_rankings.json"


def _load_container_name() -> str:
    """Pull the container name out of the same place function_app does.

    azure-functions/config.py defines ``Config.container_name``. We can't
    import it from ``tools/`` cleanly because of sys.path conflicts with
    backend/, so we just hard-code the same default and let an env var
    override it.
    """
    return os.environ.get("AZURE_BLOB_CONTAINER", "fantasyjsons")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--source", type=Path, default=DEFAULT_SOURCE,
                    help=f"Local JSON file (default: {DEFAULT_SOURCE})")
    ap.add_argument("--blob-path", default=DEFAULT_BLOB_PATH,
                    help=f"Destination blob path (default: {DEFAULT_BLOB_PATH})")
    ap.add_argument("--container", default=_load_container_name(),
                    help="Azure container name (env: AZURE_BLOB_CONTAINER, "
                         "default: fantasyjsons)")
    ap.add_argument("--dry-run", action="store_true",
                    help="Print upload plan + sanity stats and exit.")
    ap.add_argument("--allow-overwrite", action="store_true",
                    help="Required to overwrite an existing blob. "
                         "Without this flag we abort if the destination exists.")
    args = ap.parse_args()

    if not args.source.exists():
        print(f"FATAL: source not found: {args.source}", file=sys.stderr)
        return 2

    size = args.source.stat().st_size
    print(f"Source: {args.source}")
    print(f"  size: {size/1024/1024:,.1f} MB ({size:,} bytes)")

    # Quick sanity: parse the JSON and print key stats so we never push
    # a corrupted file blindly. Re-loading 33 MB is a fraction of a second.
    blob_data = json.loads(args.source.read_text(encoding="utf-8"))
    n_records = blob_data.get("n_records") or len(blob_data.get("records", {}))
    n_players = blob_data.get("n_players", "?")
    n_picks = blob_data.get("n_picks", "?")
    print(f"  records:  {n_records}")
    print(f"  players:  {n_players}")
    print(f"  picks:    {n_picks}")

    print(f"Destination: {args.container}/{args.blob_path}")

    if args.dry_run:
        print("\n[dry-run] not uploading.")
        return 0

    conn = os.environ.get("AZURE_STORAGE_CONNECTION_STRING")
    if not conn:
        print("FATAL: AZURE_STORAGE_CONNECTION_STRING not set.", file=sys.stderr)
        return 2

    # Import azure SDK lazily so --dry-run works without it installed.
    try:
        from azure.storage.blob import BlobServiceClient  # type: ignore
    except ImportError:
        print("FATAL: azure-storage-blob not installed.\n"
              "       pip install azure-storage-blob", file=sys.stderr)
        return 2

    svc = BlobServiceClient.from_connection_string(conn)
    client = svc.get_blob_client(container=args.container, blob=args.blob_path)

    if client.exists():
        if not args.allow_overwrite:
            print(
                f"\nABORT: blob already exists at "
                f"{args.container}/{args.blob_path}.\n"
                f"       Re-run with --allow-overwrite to replace it.",
                file=sys.stderr,
            )
            return 3
        print("  (destination exists; --allow-overwrite set, will replace)")

    # Stream the raw bytes; no need to re-serialize through json.dumps.
    raw = args.source.read_bytes()
    print(f"\nUploading {len(raw):,} bytes...")
    client.upload_blob(raw, overwrite=True)
    print("Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
