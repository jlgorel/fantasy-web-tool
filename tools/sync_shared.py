"""Sync the canonical shared modules into each deployable project.

Run this any time you edit ``shared/fantasy_common.py`` or anything
under ``azure-functions/trade_eval/``::

    python tools/sync_shared.py

Two contracts are enforced (by tests in ``backend/tests``):

1. ``shared/fantasy_common.py`` -> ``_fantasy_common.py`` in each project.
2. ``azure-functions/trade_eval/`` -> ``backend/app/services/trade_eval/``.

The trade-eval package lives under ``azure-functions/`` for historical
reasons (it was written for the Azure function first). The backend
wrapped pipeline now consumes it too, so we mirror the whole package
into the backend tree instead of doing import-path hacks.
"""
from __future__ import annotations

import filecmp
import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
FANTASY_COMMON_SOURCE = REPO_ROOT / "shared" / "fantasy_common.py"
FANTASY_COMMON_TARGETS = [
    REPO_ROOT / "backend" / "app" / "_fantasy_common.py",
    REPO_ROOT / "azure-functions" / "_fantasy_common.py",
]

TRADE_EVAL_SOURCE = REPO_ROOT / "azure-functions" / "trade_eval"
TRADE_EVAL_TARGET = REPO_ROOT / "backend" / "app" / "services" / "trade_eval"


def _sync_file(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)
    print(f"synced {source.name} -> {target.relative_to(REPO_ROOT)}")


def _sync_package(source: Path, target: Path) -> None:
    """Mirror a package directory: copy every ``*.py`` and prune any
    file in the target that no longer exists in the source.

    Only ``*.py`` files are copied; ``__pycache__`` directories and other
    junk are ignored. The target directory is created if missing.
    """
    target.mkdir(parents=True, exist_ok=True)
    # Walk source, copy py files.
    source_files: set[Path] = set()
    for src_path in source.rglob("*.py"):
        if "__pycache__" in src_path.parts:
            continue
        rel = src_path.relative_to(source)
        dst_path = target / rel
        source_files.add(rel)
        dst_path.parent.mkdir(parents=True, exist_ok=True)
        if not dst_path.exists() or not filecmp.cmp(src_path, dst_path, shallow=False):
            shutil.copyfile(src_path, dst_path)
            print(f"synced {rel}  -> {dst_path.relative_to(REPO_ROOT)}")
    # Prune target-only py files (do NOT touch __pycache__).
    for dst_path in target.rglob("*.py"):
        if "__pycache__" in dst_path.parts:
            continue
        rel = dst_path.relative_to(target)
        if rel not in source_files:
            dst_path.unlink()
            print(f"removed {dst_path.relative_to(REPO_ROOT)} (gone from source)")


def main() -> int:
    if not FANTASY_COMMON_SOURCE.exists():
        print(f"ERROR: missing canonical source: {FANTASY_COMMON_SOURCE}",
              file=sys.stderr)
        return 1
    for target in FANTASY_COMMON_TARGETS:
        _sync_file(FANTASY_COMMON_SOURCE, target)

    if not TRADE_EVAL_SOURCE.is_dir():
        print(f"ERROR: missing trade_eval source dir: {TRADE_EVAL_SOURCE}",
              file=sys.stderr)
        return 1
    _sync_package(TRADE_EVAL_SOURCE, TRADE_EVAL_TARGET)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
