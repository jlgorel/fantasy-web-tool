"""Guard the shared-module contracts.

Two pairs of paths must stay in sync; if either drifts the relevant
test below fails, telling you to re-run ``python tools/sync_shared.py``.

1. ``shared/fantasy_common.py`` -> ``<project>/_fantasy_common.py``
2. ``azure-functions/trade_eval/*.py`` ->
   ``backend/app/services/trade_eval/*.py`` (whole-package mirror)
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

TRADE_EVAL_SOURCE = REPO_ROOT / "azure-functions" / "trade_eval"
TRADE_EVAL_TARGET = REPO_ROOT / "backend" / "app" / "services" / "trade_eval"


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


def _list_py_files(root: Path) -> set[Path]:
    return {
        p.relative_to(root)
        for p in root.rglob("*.py")
        if "__pycache__" not in p.parts
    }


def test_trade_eval_mirror_matches_source():
    """The backend copy of trade_eval must match the azure-functions
    canonical copy byte-for-byte.

    Tests in both projects then run against the same code; you only have
    to write one set of unit tests.
    """
    assert TRADE_EVAL_SOURCE.is_dir(), (
        f"missing canonical source dir: {TRADE_EVAL_SOURCE}"
    )
    assert TRADE_EVAL_TARGET.is_dir(), (
        f"missing mirror dir: {TRADE_EVAL_TARGET}.\n"
        "Run: python tools/sync_shared.py"
    )
    src_files = _list_py_files(TRADE_EVAL_SOURCE)
    dst_files = _list_py_files(TRADE_EVAL_TARGET)
    only_src = src_files - dst_files
    only_dst = dst_files - src_files
    drift = []
    for rel in sorted(src_files & dst_files):
        if _digest(TRADE_EVAL_SOURCE / rel) != _digest(TRADE_EVAL_TARGET / rel):
            drift.append(str(rel))
    assert not only_src and not only_dst and not drift, (
        "azure-functions/trade_eval and backend/app/services/trade_eval "
        "drifted. Run: python tools/sync_shared.py\n"
        f"only_in_source: {sorted(map(str, only_src))}\n"
        f"only_in_target: {sorted(map(str, only_dst))}\n"
        f"contents_differ: {drift}"
    )
