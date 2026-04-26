"""Guard the shared/fantasy_common.py contract.

Both deployable projects (backend and azure-functions) need the same NFL team
table and scoring-multiplier function. The canonical source lives at
``shared/fantasy_common.py`` and is copied into each project as
``_fantasy_common.py`` by ``tools/sync_shared.py``.

This test fails if any of the three copies drift, prompting a re-sync.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SOURCE = REPO_ROOT / "shared" / "fantasy_common.py"
COPIES = [
    REPO_ROOT / "backend" / "app" / "_fantasy_common.py",
    REPO_ROOT / "azure-functions" / "_fantasy_common.py",
]


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_shared_module_copies_match_source():
    assert SOURCE.exists(), f"missing canonical source: {SOURCE}"
    expected = _digest(SOURCE)
    drift = []
    for copy in COPIES:
        if not copy.exists() or _digest(copy) != expected:
            drift.append(str(copy.relative_to(REPO_ROOT)))
    assert not drift, (
        "fantasy_common.py copies are out of sync with shared/fantasy_common.py.\n"
        "Run: python tools/sync_shared.py\n"
        f"Drift: {drift}"
    )
