"""The agent-side pieces run on the standard library alone.

Agents, the SDK, the smoke test and the demos talk to a deployed Mycelic over HTTP; they must not need the server's
dependencies (aiohttp, nats-py, prometheus-client).  ``python -S`` disables site-packages, so these imports fail if
anything on the path reaches for a third-party package.
"""
from __future__ import annotations

import re
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
    "demo.live.scenario",
    "demo.live.mycelic_live",
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
        for script in ("tests/smoke/mycelic_smoke.py", "demo/mycelic_strategic_demo.py", "demo/mycelic_demo.py",
                       "demo/live/mycelic_live.py"):
            with self.subTest(script=script):
                r = _run_without_site_packages(script, "--help")
                self.assertEqual(r.returncode, 0, r.stderr)

    def test_version_is_single_sourced(self) -> None:
        import mycelic
        from mycelic import service, version
        self.assertEqual(mycelic.__version__, version.VERSION)
        self.assertIs(service.VERSION, version.VERSION)


def _locked(path: Path) -> dict[str, tuple[str, list[str]]]:
    """``{name: (version, hashes)}`` of a requirements file in pip-compile's ``--generate-hashes`` layout."""
    out: dict[str, tuple[str, list[str]]] = {}
    name = None
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith(("#", "-r ")):
            continue
        if line.startswith("--hash=sha256:"):
            assert name is not None, line
            out[name][1].append(line.split(":", 1)[1].rstrip(" \\"))
            continue
        requirement = line.rstrip(" \\")
        assert "==" in requirement, f"{path.name}: {requirement} is not pinned to one version"
        name, version = requirement.split("==")
        out[name] = (version, [])
    return out


class PinnedDependencyTests(unittest.TestCase):
    """The image runs what the tests ran: the runtime set is pinned by version and hash (requirements-lock.txt), the
    image installs it on a base pinned by digest, CI installs it too, and CI runs the broker version that ships."""

    def test_the_runtime_set_is_pinned_with_hashes_and_is_what_the_tests_run(self) -> None:
        from importlib import metadata

        from packaging.requirements import Requirement
        from packaging.utils import canonicalize_name

        lock = _locked(ROOT / "requirements-lock.txt")
        for name, (version, hashes) in lock.items():
            with self.subTest(package=name):
                self.assertTrue(hashes and all(re.fullmatch(r"[0-9a-f]{64}", h) for h in hashes), hashes)
                self.assertEqual(metadata.version(name), version, "the environment that runs the tests is the locked one")
        for line in (ROOT / "requirements-runtime.txt").read_text().splitlines():
            if line.strip() and not line.startswith("#"):
                req = Requirement(line)
                with self.subTest(requirement=line):
                    self.assertIn(canonicalize_name(req.name), lock)
                    self.assertIn(lock[canonicalize_name(req.name)][0], req.specifier)
        # every dependency of a locked package (for this interpreter) is locked as well
        for name in lock:
            for r in metadata.requires(name) or []:
                req = Requirement(r)
                if req.marker is None or req.marker.evaluate({"extra": ""}):
                    with self.subTest(package=name, dependency=req.name):
                        self.assertIn(canonicalize_name(req.name), lock)
        tests = _locked(ROOT / "requirements.txt")
        self.assertIn("-r requirements-lock.txt", (ROOT / "requirements.txt").read_text().splitlines())
        self.assertTrue({"pytest", "pytest-asyncio", "pyyaml"} <= set(tests))

    def test_the_image_installs_the_lock_on_a_base_pinned_by_digest(self) -> None:
        dockerfile = (ROOT / "deploy" / "mycelic" / "Dockerfile").read_text()
        [base] = [line for line in dockerfile.splitlines() if line.startswith("FROM ")]
        self.assertRegex(base, r"^FROM python:3\.11-slim@sha256:[0-9a-f]{64}$")
        self.assertIn("COPY requirements-lock.txt /app/requirements-lock.txt", dockerfile)
        self.assertIn("pip install --no-cache-dir --require-hashes -r /app/requirements-lock.txt", dockerfile)

    def test_ci_installs_the_lock_and_runs_the_broker_that_ships(self) -> None:
        import yaml

        workflow = yaml.safe_load((ROOT / ".github" / "workflows" / "mycelic.yml").read_text())
        steps = [step.get("run", "") for step in workflow["jobs"]["tests"]["steps"]]
        self.assertTrue(any(run.startswith("pip install --require-hashes -r requirements.txt") for run in steps), steps)
        version = workflow["jobs"]["tests"]["env"]["NATS_VERSION"].lstrip("v")
        compose = (ROOT / "deploy" / "mycelic" / "docker-compose.yml").read_text()
        nats = (ROOT / "deploy" / "mycelic" / "k8s" / "nats-statefulset.yaml").read_text()
        self.assertIn(f"${{NATS_IMAGE:-nats:{version}-alpine}}", compose)
        self.assertIn(f"image: nats:{version}-alpine", nats)


if __name__ == "__main__":
    unittest.main()
