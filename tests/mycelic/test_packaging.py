"""The agent-side pieces run on the standard library alone.

Agents, the SDK, the smoke test and the demos talk to a deployed Mycelic over HTTP; they must not need the server's
dependencies (aiohttp, nats-py, prometheus-client).  ``python -S`` disables site-packages, so these imports fail if
anything on the path reaches for a third-party package.
"""
from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

STDLIB_ONLY_MODULES = (
    "mycelic",
    "mycelic.version",
    "mycelic.sdk",
    "mycelic.sdk.agent",
    "mycelic.harness",
    "tests.smoke.scenario",
    "tests.smoke.scenario_strategic",
    "tests.smoke.mycelic_smoke",
)


def _run_without_site_packages(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, "-S", *args], cwd=ROOT, capture_output=True, text=True, timeout=60,
                          env={"PYTHONPATH": str(ROOT), "PATH": "/usr/bin:/bin"})


class StdlibOnlyTests(unittest.TestCase):
    def test_agent_side_modules_import_without_site_packages(self) -> None:
        code = "import importlib\n" + "".join(f"importlib.import_module({m!r})\n" for m in STDLIB_ONLY_MODULES)
        r = _run_without_site_packages("-c", code)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_server_stack_is_really_absent_under_dash_s(self) -> None:
        # guards the test above: if -S stopped hiding site-packages it would pass vacuously
        r = _run_without_site_packages("-c", "import aiohttp")
        self.assertNotEqual(r.returncode, 0, "aiohttp importable without site-packages; the check above proves nothing")

    def test_smoke_and_demo_entry_points_start_without_site_packages(self) -> None:
        for script in ("tests/smoke/mycelic_smoke.py", "demo/mycelic_strategic_demo.py", "demo/mycelic_demo.py"):
            with self.subTest(script=script):
                r = _run_without_site_packages(script, "--help")
                self.assertEqual(r.returncode, 0, r.stderr)

    def test_version_is_single_sourced(self) -> None:
        import mycelic
        from mycelic import service, version
        self.assertEqual(mycelic.__version__, version.VERSION)
        self.assertIs(service.VERSION, version.VERSION)


if __name__ == "__main__":
    unittest.main()
