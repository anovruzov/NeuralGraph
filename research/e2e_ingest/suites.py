"""Criterion 10's gate with evidence (``docs/collective/E2E-INGEST.md`` M2, M11): the seven unit suites run with
junit output, one JSON row per suite, and the reader the harness uses to embed them in a report.

    python -m research.e2e_ingest suites --out DIR [--group core|lab] [--suite NAME ...] [--verify]

Each suite runs as ``python -m pytest <suite> -q -p no:warnings [-p no:cacheprovider] --junitxml=DIR/<slug>.xml``
(``python -m unittest research.mycelic.test_mycelic`` becomes ``python -m pytest research/mycelic/test_mycelic.py``,
which collects the same 30 tests). After each, ``DIR/<slug>.json`` holds ``{suite, slug, group, sha, tests,
failures, errors, skipped, skipped_in_scope, duration_s, junit_sha256, ...}`` where ``sha`` is ``GITHUB_SHA`` in CI
and the commit of the tree otherwise, and ``junit_sha256`` binds the row to the junit file beside it. Every selected
suite runs even when an earlier one is red, so the evidence is whole; the exit code is 1 when any suite failed,
errored or left no junit file. ``--verify`` only reads a directory and prints what the harness would embed.

The floors are the ``pytest --collect-only`` counts the brief fixed at ``e3052c0`` (M2's table) and, for
``tests/e2e_ingest``, the count at this build (:data:`E2E_INGEST_FLOOR`), which may only rise. ``skipped`` is bounded
at 0 for ``tests/e2e_ingest`` (the whole suite) and for ``tests/mycelic/test_jetstream.py`` (the testcases whose junit
``classname`` starts with ``tests.mycelic.test_jetstream``), as M11 asks; the other suites' skips are reported.

The harness (``run --suites DIR``, ``measure --suites DIR``) calls :func:`evidence_block`, which re-hashes every
junit file (``junit_bound``) and reads the ``needs`` results the workflow exports (``E2E_NEEDS_SUITES_CORE``,
``E2E_NEEDS_SUITES_LAB``), and criterion 10's check (``report.c10_suites_green``) asserts over the rows. Nothing
here imports the fabric, the sites or the simulator.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from mycelic.collective.experiments.common import code_commit, utc_clock, write_json_atomic
from mycelic.collective.jsonio import sha256_hex

__all__ = ["E2E_INGEST_FLOOR", "EVIDENCE", "GROUPS", "NEEDS_ENV", "NEEDS_JOBS", "ROOT", "SUITES", "Suite",
           "by_name", "evidence_block", "junit_summary", "main", "needs_results", "read_evidence", "run_suite"]

ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = "junit"
GROUPS = ("core", "lab")
#: The suite jobs of section 11 and the environment names the workflow exports their ``needs`` results under.
NEEDS_JOBS = {"core": "suites-core", "lab": "suites-lab"}
NEEDS_ENV = {"suites-core": "E2E_NEEDS_SUITES_CORE", "suites-lab": "E2E_NEEDS_SUITES_LAB"}
#: ``tests/e2e_ingest`` at this build's merge (M2: written into the settings test, may only rise).
E2E_INGEST_FLOOR = 151
JETSTREAM_CLASSNAME = "tests.mycelic.test_jetstream"
PYTEST_FLAGS = ("-q", "-p", "no:warnings")


@dataclass(frozen=True)
class Suite:
    """One of the seven suites: its path, the slug its files carry, its job (``group``), its floor (M2's table),
    the extra pytest plugin flags the regular CI gives it, and the scope whose skips must be 0 (M11): ``None`` for
    no bound, ``""`` for the whole suite, else a junit ``classname`` prefix."""

    name: str
    slug: str
    group: str
    floor: int
    plugins: tuple[str, ...] = ()
    zero_skip_scope: str | None = None

    def command(self, out: str | Path, python: str = sys.executable) -> list[str]:
        return [python, "-m", "pytest", self.name, *PYTEST_FLAGS, *self.plugins,
                f"--junitxml={Path(out) / (self.slug + '.xml')}"]


SUITES: tuple[Suite, ...] = (
    Suite("NeuralGraph/tests", "neuralgraph-tests", "core", 265),
    Suite("tests/mycelic", "tests-mycelic", "core", 1711, zero_skip_scope=JETSTREAM_CLASSNAME),
    Suite("tests/onboard", "tests-onboard", "core", 287),
    Suite("tests/routing_spike", "tests-routing_spike", "core", 61, ("-p", "no:cacheprovider")),
    Suite("tests/e2e_ingest", "tests-e2e_ingest", "core", E2E_INGEST_FLOOR, ("-p", "no:cacheprovider"),
          zero_skip_scope=""),
    Suite("tests/lab", "tests-lab", "lab", 725, ("-p", "no:cacheprovider")),
    Suite("research/mycelic/test_mycelic.py", "research-mycelic-test_mycelic", "lab", 30),
)
_BY_NAME = {s.name: s for s in SUITES}
_BY_SLUG = {s.slug: s for s in SUITES}
assert len(_BY_NAME) == len(SUITES) == len(_BY_SLUG)


def by_name(name: str) -> Suite:
    try:
        return _BY_NAME[name]
    except KeyError:
        raise ValueError(f"unknown suite {name!r}; the seven are {', '.join(_BY_NAME)}") from None


# --------------------------------------------------------------------------------------------------- the junit file

def junit_summary(path: str | Path, zero_skip_scope: str | None = None) -> dict[str, Any]:
    """The counts of a pytest junit file: ``tests``, ``failures``, ``errors``, ``skipped`` and ``duration_s`` summed
    over its ``testsuite`` elements, and ``skipped_in_scope``, the testcases with a ``skipped`` child whose
    ``classname`` starts with ``zero_skip_scope`` (``""`` is every testcase; ``None`` gives null)."""
    root = ET.parse(str(path)).getroot()
    suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
    out = {"tests": 0, "failures": 0, "errors": 0, "skipped": 0, "duration_s": 0.0}
    for suite in suites:
        for key in ("tests", "failures", "errors", "skipped"):
            out[key] += int(suite.get(key) or 0)
        out["duration_s"] += float(suite.get("time") or 0.0)
    in_scope: int | None = None
    if zero_skip_scope is not None:
        in_scope = 0
        for case in root.iter("testcase"):
            if str(case.get("classname") or "").startswith(zero_skip_scope) and case.find("skipped") is not None:
                in_scope += 1
    out["skipped_in_scope"] = in_scope
    out["testcases"] = sum(1 for _ in root.iter("testcase"))
    return out


def _sha() -> str:
    return os.environ.get("GITHUB_SHA") or code_commit()


def run_suite(suite: Suite, out: str | Path, *, sha: str | None = None, python: str = sys.executable,
              cwd: str | Path = ROOT) -> dict[str, Any]:
    """Run one suite, write ``<slug>.log`` and ``<slug>.json`` beside its junit file, and return the row."""
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    command = suite.command(out, python)
    xml = out / f"{suite.slug}.xml"
    log = out / f"{suite.slug}.log"
    if xml.exists():
        xml.unlink()
    started = utc_clock()
    t0 = time.perf_counter()
    with open(log, "wb") as fh:
        proc = subprocess.run(command, cwd=str(cwd), stdout=fh, stderr=subprocess.STDOUT)
    seconds = time.perf_counter() - t0
    row: dict[str, Any] = {"suite": suite.name, "slug": suite.slug, "group": suite.group, "floor": suite.floor,
                           "zero_skip_scope": suite.zero_skip_scope, "sha": sha or _sha(), "command": command,
                           "exit_code": proc.returncode, "started_at": started, "finished_at": utc_clock(),
                           "wall_seconds": seconds, "python": sys.version.split()[0], "junit_file": xml.name,
                           "log_file": log.name, "tests": 0, "failures": 0, "errors": 0, "skipped": 0,
                           "skipped_in_scope": None, "duration_s": 0.0, "junit_sha256": None,
                           "junit_present": xml.exists()}
    if xml.exists():
        row.update(junit_summary(xml, suite.zero_skip_scope))
        row["junit_sha256"] = sha256_hex(xml.read_bytes())
    else:
        # pytest left no junit file (a crash before collection, an interpreter error): the row says so and counts
        # as one error, never as a green suite
        row["errors"] = 1
    write_json_atomic(out / f"{suite.slug}.json", row)
    return row


def green(row: Mapping[str, Any]) -> bool:
    """A junit file, at least one test, no failure, no error, exit 0 (a suite that collected nothing is not green)."""
    return (row.get("junit_present") is True and int(row.get("tests") or 0) > 0 and row.get("failures") == 0
            and row.get("errors") == 0 and row.get("exit_code") == 0)


# --------------------------------------------------------------------------------------------------- reading

def read_evidence(out: str | Path | None, group: str | None = None) -> dict[str, Any]:
    """Every suite's row from ``DIR`` (one job's with ``group``), each re-bound to its junit file (``junit_bound``:
    the file is beside it and hashes to the row's ``junit_sha256``); ``evidence`` is ``"junit"`` only when every
    expected row is present and bound, and ``problems`` names what is not."""
    rows: list[dict[str, Any]] = []
    problems: list[str] = []
    expected = [s for s in SUITES if group is None or s.group == group]
    if out is None:
        return {"rows": rows, "evidence": None, "problems": ["no suites directory"], "dir": None}
    out = Path(out)
    for suite in expected:
        path = out / f"{suite.slug}.json"
        if not path.is_file():
            problems.append(f"{suite.name}: no {path.name}")
            continue
        try:
            row = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            problems.append(f"{suite.name}: unreadable {path.name} ({exc.__class__.__name__})")
            continue
        if not isinstance(row, dict) or row.get("suite") != suite.name:
            problems.append(f"{suite.name}: {path.name} is not its row")
            continue
        xml = out / str(row.get("junit_file") or f"{suite.slug}.xml")
        bound = xml.is_file() and row.get("junit_sha256") is not None and sha256_hex(xml.read_bytes()) == \
            row.get("junit_sha256")
        if not bound:
            problems.append(f"{suite.name}: junit file missing or not the one the row hashed")
        rows.append({**row, "junit_bound": bool(bound)})
    evidence = EVIDENCE if len(rows) == len(expected) and not problems else None
    return {"rows": rows, "evidence": evidence, "problems": problems, "dir": str(out)}


def needs_results() -> dict[str, str | None] | None:
    """The suite jobs' ``needs`` results as the workflow exports them, or None outside a job that exports them."""
    found = {job: os.environ.get(env) for job, env in NEEDS_ENV.items()}
    return found if any(v is not None for v in found.values()) else None


def evidence_block(out: str | Path | None) -> dict[str, Any]:
    """The ``run.suites*`` fields of a report: the rows, ``suites_evidence`` (``"junit"`` or null), the directory,
    the problems and the ``needs`` results."""
    found = read_evidence(out)
    return {"suites": found["rows"], "suites_evidence": found["evidence"], "suites_dir": found["dir"],
            "suites_problems": found["problems"], "needs_results": needs_results()}


# --------------------------------------------------------------------------------------------------- the CLI

def summary_line(row: Mapping[str, Any]) -> str:
    return (f"  {row['suite']}: tests={row.get('tests')} failures={row.get('failures')} errors={row.get('errors')} "
            f"skipped={row.get('skipped')} skipped_in_scope={row.get('skipped_in_scope')} "
            f"exit={row.get('exit_code')} seconds={float(row.get('wall_seconds') or 0):.0f} "
            f"junit={'yes' if row.get('junit_present') else 'NO'}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m research.e2e_ingest suites",
                                     description="Run the seven unit suites with junit evidence (M2).")
    parser.add_argument("--out", required=True, help="where the junit files and the rows go")
    parser.add_argument("--group", choices=GROUPS, default=None, help="only this job's suites (section 11)")
    parser.add_argument("--suite", action="append", default=None, help="only this suite (repeatable)")
    parser.add_argument("--sha", default=None, help="the commit the rows name (default: GITHUB_SHA or the tree's)")
    parser.add_argument("--verify", action="store_true",
                        help="read DIR (one job's suites with --group) and print what a report would embed")
    args = parser.parse_args(list(argv) if argv is not None else None)
    out = Path(args.out)
    if args.verify:
        found = read_evidence(out, args.group)
        for row in found["rows"]:
            print(summary_line(row))
        for problem in found["problems"]:
            print(f"  problem: {problem}")
        print(f"suites: evidence={found['evidence']} rows={len(found['rows'])} problems={len(found['problems'])}")
        return 0 if found["evidence"] == EVIDENCE and all(green(r) for r in found["rows"]) else 1
    selected = list(SUITES)
    if args.group is not None:
        selected = [s for s in selected if s.group == args.group]
    if args.suite:
        try:
            wanted = [by_name(n) for n in args.suite]
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        selected = [s for s in selected if s in wanted]
    if not selected:
        print("error: no suite selected", file=sys.stderr)
        return 2
    rows = []
    for suite in selected:
        print(f"suites: running {suite.name} ({' '.join(suite.command(out)[1:])})", flush=True)
        row = run_suite(suite, out, sha=args.sha)
        rows.append(row)
        print(summary_line(row), flush=True)
    red = [r["suite"] for r in rows if not green(r)]
    named = (": " + ", ".join(red)) if red else ""
    print(f"suites: {len(rows)} run, {len(rows) - len(red)} green, {len(red)} red{named}")
    return 1 if red else 0


if __name__ == "__main__":
    sys.exit(main())
