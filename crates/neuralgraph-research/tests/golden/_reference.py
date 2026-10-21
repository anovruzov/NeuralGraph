"""Shared setup for the research golden-fixture generators (``gen_*.py`` next to this file).

The reference is the Python research code as it stood at ``REFERENCE_COMMIT`` (the last commit with Python in the
repository). It is extracted from git into a temporary directory, so the generators work on any checkout even
though the repository no longer contains that code. Each ``gen_<area>.py`` imports this module, exercises the
deterministic pieces of its area and writes ``<area>.json`` next to itself; the Rust tests replay the same
inputs and assert equality.

Run any generator from the repository root, e.g.:

    python crates/neuralgraph-research/tests/golden/gen_foundation.py

Needs Python 3.11 with aiohttp, numpy and rank-bm25, and git. ``NG_PYTHON_REFERENCE=/path/to/checkout`` uses an
existing tree that contains ``NeuralGraph/`` and ``research/`` instead of extracting one.

Determinism helpers:
- ``freeze_now(modules, now)`` replaces ``datetime`` in the given modules with a subclass whose ``now()`` returns
  ``now`` (aware UTC when a tz is passed, the same wall time naive otherwise), the way the Rust tests pin
  ``clock::set_fixed`` and ``pytime::set_fixed_local_offset(Some(0))``.
- ``ordered_ids(modules)`` makes ``uuid.uuid4`` deterministic for modules that mint ids (shared counter).
- ``dump(name, obj)`` writes the fixture with sorted keys off (insertion order is part of the contract).
"""
from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tarfile
import tempfile
from datetime import datetime as _datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
# origin/main after Phase A (the Rust port of the product): the last commit containing the Python research code
REFERENCE_COMMIT = "eba2f3707f5b3b9c62104afffce4230c7608d8b8"


def _reference_root() -> Path:
    override = os.environ.get("NG_PYTHON_REFERENCE")
    if override:
        return Path(override)
    dest = Path(tempfile.mkdtemp(prefix="ng-research-reference-"))
    tar_bytes = subprocess.run(
        ["git", "-C", str(ROOT), "archive", "--format=tar", REFERENCE_COMMIT, "NeuralGraph", "research/benchmarks"],
        check=True, capture_output=True).stdout
    with tarfile.open(fileobj=io.BytesIO(tar_bytes)) as tar:
        tar.extractall(dest, filter="data")
    return dest


REF = _reference_root()
sys.path.insert(0, str(REF))
sys.path.insert(0, str(REF / "research" / "benchmarks"))
DATASETS = ROOT / "research" / "datasets"
FIXED_NOW = _datetime(2026, 9, 30, 8, 0, 0, tzinfo=timezone.utc)


def frozen_datetime_class(now: _datetime):
    """A ``datetime`` subclass whose ``now()`` is pinned (naive callers get the UTC wall time)."""

    class FrozenDateTime(_datetime):
        @classmethod
        def now(cls, tz=None):
            if tz is None:
                return cls(now.year, now.month, now.day, now.hour, now.minute, now.second, now.microsecond)
            return cls.fromtimestamp(now.timestamp(), tz)

        @classmethod
        def utcnow(cls):
            return cls(now.year, now.month, now.day, now.hour, now.minute, now.second, now.microsecond)

    return FrozenDateTime


def freeze_now(modules, now: _datetime = FIXED_NOW) -> None:
    cls = frozen_datetime_class(now)
    for m in modules:
        if hasattr(m, "datetime"):
            m.datetime = cls


class _OrderedUuid:
    """Stand-in for the ``uuid`` module: ``uuid4()`` counts ``00000000-0000-4000-0000-000000000001``... (the
    Rust side's ``data_types::set_deterministic_uuids(Some(0))``). One counter for every patched module."""

    def __init__(self) -> None:
        import uuid as _uuid
        self._uuid = _uuid
        self.n = 0

    def reset(self, start: int = 0) -> None:
        self.n = start

    def uuid4(self):
        self.n += 1
        return self._uuid.UUID(int=(0x4000 << 64) | self.n)

    def __getattr__(self, name):
        return getattr(self._uuid, name)


UUIDS = _OrderedUuid()


def ordered_ids(modules) -> None:
    """Make ``uuid.uuid4()`` deterministic in the given modules (shared counter ``UUIDS``; ``UUIDS.reset()``)."""
    for m in modules:
        if hasattr(m, "uuid"):
            m.uuid = UUIDS


def f32_bits(values) -> list[int]:
    import numpy as np
    return np.asarray(values, dtype=np.float32).view(np.uint32).tolist()


def f64_bits(values) -> list[int]:
    import numpy as np
    return [int(x) for x in np.asarray(values, dtype=np.float64).view(np.uint64).tolist()]


def dump(name: str, obj) -> None:
    path = HERE / name
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"wrote {path.relative_to(ROOT)} ({path.stat().st_size // 1024} KiB)")
