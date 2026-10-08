"""The end-to-end verification scenario (``python -m mycelic scenario``) as an automated test.

It starts the API on a random port, two separate holder processes with their own stores and keys, the discovery
worker, the fake model and the SQLite transport, and runs checks a–l (every role, a second tenant, copied evidence, a
stale fact, a contradiction, a source revision, a worker crash, loop controls, progress with no client). About ten
seconds. Set ``MYCELIC_SKIP_SCENARIO=1`` to skip it in a constrained environment.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from mycelic.seed.scenario import run_scenario

pytestmark = pytest.mark.skipif(os.environ.get("MYCELIC_SKIP_SCENARIO") == "1", reason="MYCELIC_SKIP_SCENARIO=1")

EXPECTED = [chr(c) for c in range(ord("a"), ord("l") + 1)]


async def test_verification_scenario_passes_every_check(tmp_path: Path):
    report = await run_scenario(data_dir=str(tmp_path / "scenario"), timeout=150.0)
    checks = {c["name"].split(".")[0]: c for c in report["checks"]}
    failed = {k: c["evidence"][:300] for k, c in checks.items() if not c["passed"]}
    assert not failed, failed
    assert sorted(checks) == EXPECTED, sorted(checks)
    assert report["passed"] is True
    # the run stayed healthy: no job died and nothing was left leased
    assert report["jobs"].get("dead", 0) == 0 and report["jobs"].get("leased", 0) == 0
