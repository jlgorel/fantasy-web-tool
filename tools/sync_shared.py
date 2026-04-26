"""Sync the canonical shared module into each deployable project.

Run this any time you edit ``shared/fantasy_common.py``::

    python tools/sync_shared.py

A test (``backend/tests/test_shared_in_sync.py``) asserts the three copies
stay byte-identical, so CI will fail if you forget.
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SOURCE = REPO_ROOT / "shared" / "fantasy_common.py"
TARGETS = [
    REPO_ROOT / "backend" / "app" / "_fantasy_common.py",
    REPO_ROOT / "azure-functions" / "_fantasy_common.py",
]


def main() -> int:
    if not SOURCE.exists():
        print(f"ERROR: source missing: {SOURCE}", file=sys.stderr)
        return 1
    for target in TARGETS:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(SOURCE, target)
        print(f"synced {SOURCE.name} -> {target.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
