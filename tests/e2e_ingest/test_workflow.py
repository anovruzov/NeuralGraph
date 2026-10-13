"""S8: the CI workflow read as data (criterion 6, section 11, M2, M10, M11, M12, M14) and the settings test of
section 6: the harness's constants against the brief's own tables, parsed from ``docs/collective/E2E-INGEST.md``.

``c6_ci`` is "asserted by the workflow itself" (section 9): this file is that assertion. PyYAML reads the ``on:`` key
as the boolean True, so the triggers are ``wf.get("on", wf.get(True))``. The deployment branch's name is read from
``mycelic.yml`` (the brief's section 11 names it there, never literally), never written here.
"""
from __future__ import annotations

import inspect
import re
import shlex
import subprocess
import sys
import unittest
from pathlib import Path
from typing import Any

import yaml

from research.e2e_ingest import drills
from research.e2e_ingest import emergence_world as ew
from research.e2e_ingest import harness as H
from research.e2e_ingest import measure as ms
from research.e2e_ingest import report as rp
from research.e2e_ingest import suites as su

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "e2e-ingest.yml"
MYCELIC_WORKFLOW = ROOT / ".github" / "workflows" / "mycelic.yml"
BRIEF = ROOT / "docs" / "collective" / "E2E-INGEST.md"
TEXT = WORKFLOW.read_text(encoding="utf-8")
WF = yaml.safe_load(TEXT)
JOBS = WF["jobs"]
BRIEF_TEXT = BRIEF.read_text(encoding="utf-8")
ON = WF.get("on", WF.get(True))
SUITE_JOBS = ("suites-core", "suites-lab", "suites-e2e")
E2E_JOBS = ("e2e", "measure")
CLI = "python -m research.e2e_ingest"
#: ``pytest --collect-only tests/e2e_ingest`` at this build (M2): a literal, never derived from ``suites.py``, so
#: lowering ``E2E_INGEST_FLOOR`` fails here; a later build raises it together with the floor when tests are added.
E2E_INGEST_TESTS_AT_THIS_BUILD = 151


def section(start: str, end: str) -> str:
    """The brief's text between two headings (or markers)."""
    i = BRIEF_TEXT.index(start)
    j = BRIEF_TEXT.index(end, i + len(start))
    return BRIEF_TEXT[i:j]


def backticked(text: str) -> list[str]:
    return re.findall(r"`([^`]+)`", text)


def number(text: str) -> int:
    return int(re.search(r"\d[\d,]*", text).group().replace(",", ""))


def steps(job: str) -> list[dict[str, Any]]:
    return JOBS[job]["steps"]


def runs(job: str) -> list[str]:
    return [str(s["run"]) for s in steps(job) if "run" in s]


def uses(step: dict[str, Any]) -> str:
    return str(step.get("uses", "")).split("@")[0]


def step_named(job: str, prefix: str) -> dict[str, Any]:
    return next(s for s in steps(job) if str(s.get("name", "")).startswith(prefix))


# =================================================================================================== the workflow

class TriggerTests(unittest.TestCase):
    """Section 11: on push to the deployment branch (named by ``mycelic.yml``, never by the brief) with the brief's
    paths, and on workflow_dispatch."""

    def test_push_to_the_deployment_branch_with_the_briefs_paths_and_dispatch(self) -> None:
        s11 = section("## 11. The CI workflow", "## 12.")
        self.assertIn("on `push` to the deployment branch", s11)
        mycelic = yaml.safe_load(MYCELIC_WORKFLOW.read_text(encoding="utf-8"))
        mon = mycelic.get("on", mycelic.get(True))
        branches = [b for b in mon["push"]["branches"] if b != "main"]
        self.assertEqual(len(branches), 1)
        self.assertEqual(ON["push"]["branches"], branches)
        m = re.search(r"with paths\n?(.*?)and on `workflow_dispatch`", s11, re.S)
        self.assertIsNotNone(m)
        paths = backticked(m.group(1))
        self.assertEqual(ON["push"]["paths"], paths)
        self.assertEqual(len(paths), 8)
        self.assertIn("workflow_dispatch", ON)
        self.assertNotIn("pull_request", ON)
        self.assertEqual(WF["permissions"], {"contents": "read"})

    def test_the_file_is_in_its_own_paths_filter(self) -> None:
        self.assertIn(".github/workflows/e2e-ingest.yml", ON["push"]["paths"])
        self.assertIn(".github/workflows/e2e-ingest.yml", rp.DIRTY_PATHS)


class JobTests(unittest.TestCase):
    """Section 11's table (amended 2026-10-13): five jobs, the three suite jobs first, the end-to-end jobs needing
    all three, the bounds."""

    def test_five_jobs_in_the_briefs_order_with_its_needs_and_bounds(self) -> None:
        self.assertEqual(list(JOBS), [*SUITE_JOBS, *E2E_JOBS])
        self.assertEqual(len(JOBS), 5)
        s11 = section("## 11. The CI workflow", "## 12.")
        rows = {}
        for line in s11.splitlines():
            if line.startswith("| `"):
                cells = [c.strip() for c in line.strip("|").split("|")]
                rows[backticked(cells[0])[0]] = cells
        self.assertEqual(sorted(rows), sorted(JOBS))
        for job, cells in rows.items():
            with self.subTest(job=job):
                needs = backticked(cells[1])
                self.assertEqual(JOBS[job].get("needs", []), needs)
                self.assertEqual(JOBS[job]["timeout-minutes"], number(cells[2]))
        self.assertEqual({j: JOBS[j]["timeout-minutes"] for j in JOBS},
                         {"suites-core": rp.SUITES_TIMEOUT_MINUTES, "suites-lab": rp.SUITES_TIMEOUT_MINUTES,
                          "suites-e2e": rp.SUITES_TIMEOUT_MINUTES, "e2e": rp.E2E_TIMEOUT_MINUTES,
                          "measure": rp.MEASURE_TIMEOUT_MINUTES})
        for job in E2E_JOBS:
            self.assertEqual(JOBS[job]["needs"], list(SUITE_JOBS))
        self.assertEqual(sorted(su.NEEDS_JOBS.values()), sorted(SUITE_JOBS))
        self.assertEqual(sorted(su.NEEDS_ENV), sorted(SUITE_JOBS))

    def test_every_job_runs_on_ubuntu_with_python_3_11_and_the_hashed_requirements(self) -> None:
        for job in JOBS:
            with self.subTest(job=job):
                self.assertEqual(JOBS[job]["runs-on"], "ubuntu-latest")
                self.assertEqual(uses(steps(job)[0]), "actions/checkout")
                python = steps(job)[1]
                self.assertEqual(uses(python), "actions/setup-python")
                self.assertEqual(python["with"]["python-version"], "3.11")
                self.assertTrue(any(r.startswith("pip install --require-hashes -r requirements.txt")
                                    for r in runs(job)), runs(job))

    def test_the_nats_install_step_is_the_one_mycelic_yml_runs_with_its_version(self) -> None:
        """c6: the install step pinned to mycelic.yml's, NATS_VERSION v2.10.29 (mycelic.yml:16)."""
        mycelic = yaml.safe_load(MYCELIC_WORKFLOW.read_text(encoding="utf-8"))
        reference = next(s for s in mycelic["jobs"]["tests"]["steps"] if s.get("name") == "install nats-server")
        self.assertEqual(WF["env"]["NATS_VERSION"], mycelic["jobs"]["tests"]["env"]["NATS_VERSION"])
        self.assertEqual(WF["env"]["NATS_VERSION"].lstrip("v"), rp.CI_BROKER_VERSION)
        for job in JOBS:
            with self.subTest(job=job):
                step = step_named(job, "install nats-server")
                self.assertEqual(step["run"], reference["run"])

    def test_m11_every_job_names_the_broker_binary_and_requires_it(self) -> None:
        self.assertEqual(WF["env"]["MYCELIC_NATS_SERVER_BIN"], "/usr/local/bin/nats-server")
        self.assertEqual(str(WF["env"]["E2E_REQUIRE_NATS"]), "1")
        install = step_named("suites-core", "install nats-server")["run"]
        self.assertIn("/usr/local/bin/", install)


class SuiteJobTests(unittest.TestCase):
    """M2: every one of the seven suites runs through the suites CLI in its job, each step kept even when an earlier
    one is red, the rows uploaded as e2e-suites-core, e2e-suites-lab and e2e-suites-e2e, and the job's own
    verification last."""

    def suite_steps(self, job: str) -> list[dict[str, Any]]:
        return [s for s in steps(job) if "run" in s and f"{CLI} suites --out suites --suite " in s["run"]]

    def test_each_suite_is_one_step_of_its_job_with_the_cli(self) -> None:
        found: dict[str, tuple[str, dict[str, Any]]] = {}
        for job in SUITE_JOBS:
            for step in self.suite_steps(job):
                name = step["run"].split("--suite ", 1)[1].strip()
                self.assertNotIn(name, found)
                found[name] = (job, step)
        self.assertEqual(sorted(found), sorted(s.name for s in su.SUITES))
        for suite in su.SUITES:
            job, step = found[suite.name]
            with self.subTest(suite=suite.name):
                self.assertEqual(job, su.NEEDS_JOBS[suite.group])
                self.assertEqual(step["run"], f"{CLI} suites --out suites --suite {suite.name}")
                self.assertEqual(step["name"], suite.name)

    def test_the_steps_after_the_first_run_even_when_an_earlier_suite_is_red(self) -> None:
        for job in SUITE_JOBS:
            suite_steps = self.suite_steps(job)
            # suites-e2e runs the one suite of its group (section 11, amended); the other two run several
            self.assertEqual(len(suite_steps), len([s for s in su.SUITES if su.NEEDS_JOBS[s.group] == job]))
            self.assertGreaterEqual(len(suite_steps), 2 if job != "suites-e2e" else 1)
            self.assertNotIn("if", suite_steps[0])
            for step in suite_steps[1:]:
                with self.subTest(job=job, step=step["name"]):
                    self.assertEqual(step["if"], "${{ !cancelled() }}")

    def test_the_rows_are_uploaded_always_and_verified_last(self) -> None:
        for job in SUITE_JOBS:
            with self.subTest(job=job):
                upload = next(s for s in steps(job) if uses(s) == "actions/upload-artifact")
                self.assertEqual(upload["if"], "always()")
                self.assertEqual(upload["with"]["name"], f"e2e-{job}")
                self.assertEqual(upload["with"]["path"], "suites/")
                last = steps(job)[-1]
                group = job.split("-", 1)[1]
                self.assertEqual(last["run"], f"{CLI} suites --out suites --verify --group {group}")
                self.assertEqual(last["if"], "${{ !cancelled() }}")

    def test_the_cli_runs_each_suite_with_pytest_and_junit_as_m2_says(self) -> None:
        for suite in su.SUITES:
            with self.subTest(suite=suite.name):
                command = suite.command("suites")
                self.assertEqual(command[1:6], ["-m", "pytest", suite.name, "-q", "-p"])
                self.assertEqual(command[6], "no:warnings")
                self.assertEqual(command[-1], f"--junitxml=suites/{suite.slug}.xml")
        self.assertEqual([s.name for s in su.SUITES if s.group == "core"],
                         ["NeuralGraph/tests", "tests/mycelic", "tests/onboard", "tests/routing_spike"])
        self.assertEqual([s.name for s in su.SUITES if s.group == "e2e"], ["tests/e2e_ingest"])
        self.assertEqual([s.name for s in su.SUITES if s.group == "lab"],
                         ["tests/lab", "research/mycelic/test_mycelic.py"])
        self.assertEqual(su.GROUPS, ("core", "lab", "e2e"))


class EndToEndJobTests(unittest.TestCase):
    """The e2e and measure jobs: the suites' evidence downloaded, the needs results exported, the brief's commands,
    the report printed between markers with its sha256 in the step summary (M12), the artifacts uploaded always."""

    def test_both_jobs_download_the_evidence_and_export_the_needs_results(self) -> None:
        for job in E2E_JOBS:
            with self.subTest(job=job):
                download = next(s for s in steps(job) if uses(s) == "actions/download-artifact")
                self.assertEqual(download["with"], {"pattern": "e2e-suites-*", "path": "suites",
                                                    "merge-multiple": True})
                env = JOBS[job]["env"]
                for needs_job, name in su.NEEDS_ENV.items():
                    self.assertEqual(env[name], f"${{{{ needs.{needs_job}.result }}}}")

    def test_the_commands_are_the_briefs(self) -> None:
        e2e = next(r for r in runs("e2e") if r.startswith(f"{CLI} run "))
        self.assertTrue(e2e.startswith(f"{CLI} run --size 200 --mode process --out e2e-out --suites suites"), e2e)
        measure = next(r for r in runs("measure") if r.startswith(f"{CLI} measure "))
        self.assertTrue(measure.startswith(f"{CLI} measure --sizes 200,600,2000 --out measure-out --suites suites"),
                        measure)
        self.assertEqual(ms.parse_sizes(shlex.split(measure)[shlex.split(measure).index("--sizes") + 1]),
                         rp.MEASURE_SIZES)
        for command in (e2e, measure):
            self.assertIn("--run-id", command)
            self.assertNotIn("E2E_SUITES_SHA", command)        # M2 dropped it
        self.assertNotIn("E2E_SUITES_SHA", TEXT)

    def test_the_report_is_printed_between_markers_and_digested(self) -> None:
        for job, report in (("e2e", f"e2e-out/{rp.REPORT_NAME}"), ("measure", f"measure-out/{rp.MEASURE_REPORT_NAME}")):
            with self.subTest(job=job):
                step = step_named(job, "the report, printed between markers")
                self.assertEqual(step["if"], "always()")
                run = step["run"]
                self.assertIn(f"f={report}\n", run)
                self.assertIn('sha=$(sha256sum "$f" | cut -d\' \' -f1)', run)
                self.assertIn('echo "=== E2E-INGEST $f BEGIN lines=$n sha256=$sha ==="', run)
                self.assertIn('echo "=== E2E-INGEST $f END ==="', run)
                self.assertIn('>> "$GITHUB_STEP_SUMMARY"', run)
                self.assertIn('test -f "$f" || { echo "no report at $f"; exit 1; }', run)
                marker = re.search(r'=== E2E-INGEST \$f BEGIN lines=\$n sha256=\$sha ===', run)
                self.assertIsNotNone(marker)

    def test_the_artifacts_are_the_briefs_names_uploaded_always(self) -> None:
        for job, name, report in (("e2e", "e2e-ingest-report.json", f"e2e-out/{rp.REPORT_NAME}"),
                                  ("measure", "e2e-ingest-measure.json", f"measure-out/{rp.MEASURE_REPORT_NAME}")):
            with self.subTest(job=job):
                upload = next(s for s in steps(job) if uses(s) == "actions/upload-artifact")
                self.assertEqual(upload["if"], "always()")
                self.assertEqual(upload["with"]["name"], name)
                self.assertEqual(upload["with"]["path"].splitlines()[0], report)
                self.assertIn("suites/*.json", upload["with"]["path"])
                self.assertEqual(steps(job)[-1], upload)

    def test_no_run_block_holds_an_expression_but_the_run_id(self) -> None:
        for job in JOBS:
            for run in runs(job):
                with self.subTest(job=job, run=run[:40]):
                    expressions = re.findall(r"\$\{\{[^}]*\}\}", run)
                    self.assertTrue(all(e == "${{ github.run_id }}" for e in expressions), expressions)


# =================================================================================================== the settings

class SettingsTests(unittest.TestCase):
    """Section 6's settings test: the harness constants against the brief's tables, parsed from the brief."""

    def test_section_3_1_world_table(self) -> None:
        s31 = section("### 3.1 The world", "### 3.2")
        rows = {}
        for line in s31.splitlines():
            if line.startswith("| ") and re.match(r"\| [\d,]+ \|", line):
                cells = [c.strip() for c in line.strip("|").split("|")]
                rows[number(cells[0])] = cells
        self.assertEqual(sorted(rows), sorted(ew.WORLD_TABLE))
        for size, cells in rows.items():
            table = ew.WORLD_TABLE[size]
            with self.subTest(size=size):
                self.assertEqual((table["users"], table["sites"], table["teams"], table["records"]),
                                 tuple(number(c) for c in cells[1:5]))
                self.assertEqual((table["patterns_real"], table["gold_discoverable"], table["gold_rare"],
                                  table["decoys"]), tuple(number(c) for c in cells[6:10]))
                self.assertEqual(table["entities"], 150)
        self.assertEqual(rp.MEASURE_SIZES, tuple(sorted(rows)))

    def test_section_3_5_and_3_6_settings(self) -> None:
        s35 = section("### 3.5", "### 3.6")
        self.assertEqual(ew.TOP_N, number(re.search(r"`top_n = (\d+)`", s35).group(1)))
        self.assertEqual(ew.TIE_SALT, re.search(r"tie salt `([^`]+)`", s35).group(1))
        self.assertEqual(ew.M, number(re.search(r"Budget m = (\d+)", s35).group(1)))
        self.assertEqual(rp.FIXED_QUERIES, number(re.search(r"The (\d+) fixed queries per site", s35).group(1)))
        s36 = section("### 3.6", "## 4.")
        self.assertEqual(drills.MAX_ACTIVE_MEMORIES_PER_ORG,
                         number(re.search(r"MYCELIC_MAX_ACTIVE_MEMORIES_PER_ORG=(\d+)", s36).group(1)))
        self.assertEqual(float(drills.REPLAY_MAX_REJECT_RATIO),
                         float(re.search(r"MYCELIC_REPLAY_MAX_REJECT_RATIO=([\d.]+)", BRIEF_TEXT).group(1)))
        m5 = section("**M5.", "**M6.")
        self.assertIn(drills.REPLAY_MAX_REJECT_RATIO, m5)

    def test_section_2_3_floors(self) -> None:
        s23 = section("### 2.3 Floors raised", "## 3.")
        self.assertEqual(rp.FLOOR_CANDIDATES, number(re.search(r"candidates asked ≥ (\d+)", s23).group(1)))
        self.assertEqual(rp.FLOOR_VERDICT_EVENTS, number(re.search(r"verdict events ≥ (\d+)", s23).group(1)))
        self.assertEqual(rp.FLOOR_CONCLUSION_EVENTS, number(re.search(r"conclusion events ≥ (\d+)", s23).group(1)))
        self.assertEqual(rp.FLOOR_PUBLISHES, number(re.search(r"N ≥ (\d+) \(one POST", s23).group(1)))
        self.assertEqual(rp.FIXED_QUERIES, number(re.search(r"exactly (\d+) per site", s23).group(1)))
        self.assertEqual(rp.FORGERIES, 3)

    def test_the_oracle_and_the_forgeries(self) -> None:
        s35 = section("### 3.5", "### 3.6")
        m = re.search(r"at least (\d+) of the pattern's chain facet records in the question window, from at least "
                      r"(\d+)\s+distinct users and (\d+)\s+distinct events", s35)
        self.assertEqual((H.ORACLE_MIN_RECORDS, H.ORACLE_MIN_USERS, H.ORACLE_MIN_EVENTS),
                         tuple(int(g) for g in m.groups()))
        self.assertEqual(H.FRESHNESS_DAYS, number(re.search(r"newest within (\d+) days", s35).group(1)))
        m8 = section("**M8.", "**M9.")
        self.assertEqual(list(H.FORGED_IDS), [f"mem_forged_e2e_{i}" for i in (1, 2, 3)])
        for forged in H.FORGED_IDS:
            self.assertIn(forged, m8)
        self.assertIn("mem_forged_e2e_{1,2,3}", m8)

    def test_m21_no_candidate_window(self) -> None:
        self.assertIn('"eval_window": None', inspect.getsource(H.E2ERun.collect_routing))
        self.assertIn("routing.eval_window: null", section("**M21.", "**M22."))

    def test_m13_the_spike_code_hash_and_file_count(self) -> None:
        m13 = section("**M13.", "**M14.")
        self.assertIn(rp.SPIKE_CODE_HASH, m13)
        self.assertEqual(rp.SPIKE_CODE_FILES, number(re.search(r"`(\d+)` files", m13).group(1)))

    def test_m10_the_fourteen_dirty_paths(self) -> None:
        m10 = section("**M10.", "**M11.")
        listed = [t for t in backticked(m10[m10.index("Now c10's paths are"):m10.index("`code_dirty` is evaluated")])
                  if ":" not in t]                      # the line references beside ``deploy/mycelic``
        self.assertEqual(sorted(listed), sorted(rp.DIRTY_PATHS))
        self.assertEqual(len(rp.DIRTY_PATHS), 14)

    def test_m2_the_suite_floors(self) -> None:
        m2 = section("**M2.", "**M3.")
        floors = {}
        for line in m2.splitlines():
            if line.startswith("| `"):
                cells = [c.strip() for c in line.strip("|").split("|")]
                floors[backticked(cells[0])[0]] = number(cells[1])
        self.assertEqual(sorted(floors), sorted(s.name for s in su.SUITES))
        for suite in su.SUITES:
            with self.subTest(suite=suite.name):
                if suite.name == "tests/e2e_ingest":
                    self.assertGreaterEqual(suite.floor, floors[suite.name])      # ≥ 1: this build's count
                    self.assertEqual(suite.floor, su.E2E_INGEST_FLOOR)
                else:
                    self.assertEqual(suite.floor, floors[suite.name])
        self.assertEqual([s.zero_skip_scope for s in su.SUITES if s.zero_skip_scope is not None],
                         [su.JETSTREAM_CLASSNAME, ""])

    def test_the_e2e_ingest_floor_is_this_suites_collected_count_and_may_only_rise(self) -> None:
        """M2: ``tests/e2e_ingest``'s count at the build's merge is written here as a literal
        (:data:`E2E_INGEST_TESTS_AT_THIS_BUILD`); the floor in ``suites.py`` may only rise from it, and the suite
        collected here must reach the floor."""
        proc = subprocess.run([sys.executable, "-m", "pytest", "tests/e2e_ingest", "--collect-only", "-q",
                               "-p", "no:cacheprovider"], cwd=str(ROOT), capture_output=True, text=True, timeout=600)
        self.assertEqual(proc.returncode, 0, proc.stdout[-2000:] + proc.stderr[-2000:])
        m = re.search(r"(\d+) tests? collected", proc.stdout)
        self.assertIsNotNone(m, proc.stdout[-500:])
        collected = int(m.group(1))
        self.assertGreaterEqual(su.E2E_INGEST_FLOOR, E2E_INGEST_TESTS_AT_THIS_BUILD)
        self.assertGreaterEqual(collected, su.E2E_INGEST_FLOOR)
        self.assertEqual(collected, E2E_INGEST_TESTS_AT_THIS_BUILD)      # raise the literal with the floor

    def test_m14_the_measure_phases_and_their_budgets(self) -> None:
        m14 = section("**M14.", "**M15.")
        rows = {}
        for line in m14.splitlines():
            if line.startswith("| ") and not line.startswith("| Phase") and not line.startswith("|---"):
                cells = [c.strip() for c in line.strip("|").split("|")]
                rows[cells[0]] = cells
        budgets = {}
        for phase, cells in rows.items():
            if phase.startswith("install"):
                budgets["install"] = number(cells[1])
            elif phase.startswith("size "):
                size = number(phase)
                budgets[f"size-{size}"] = number(cells[1])
                candidates = number(re.search(r"\((\d+) candidates\)", phase).group(1))
                self.assertEqual(rp.MEASURE_CANDIDATES[size], candidates)
            elif phase.startswith("rate sweep"):
                budgets["rate-sweep"] = number(cells[1])
            elif phase.startswith("Tesseract at"):
                budgets["tesseract-nodes"] = number(cells[1])
                counts = tuple(int(n.replace(",", "")) for n in re.findall(r"[\d,]+", phase))
                self.assertEqual(counts, rp.MEASURE_NODE_COUNTS)
            elif phase == "total":
                total, bound = (number(c) for c in cells[1].split(" of "))
                self.assertEqual(total, sum(v for k, v in budgets.items()))
                self.assertEqual(bound, rp.MEASURE_TIMEOUT_MINUTES)
        self.assertEqual(budgets, rp.MEASURE_PHASE_BUDGET_MINUTES)
        self.assertEqual(sum(rp.MEASURE_PHASE_BUDGET_MINUTES.values()), 42)
        self.assertEqual(ms.phase_plan(rp.MEASURE_SIZES),
                         ["size-200", "rate-sweep", "tesseract-nodes", "size-600", "size-2000"])

    def test_section_10_the_sweep_and_the_node_counts(self) -> None:
        s10 = section("## 10. Measurement", "## 11.")
        m = re.search(r"Rate steps ([\d, ]+) events/s, each for (\d+) s \(≥ (\d+) events\)", s10)
        self.assertEqual(tuple(int(x) for x in m.group(1).replace(" ", "").split(",")), rp.MEASURE_RATE_STEPS)
        self.assertEqual(float(m.group(2)), rp.MEASURE_STEP_SECONDS)
        self.assertEqual(int(m.group(3)), rp.MEASURE_STEP_MIN_EVENTS)
        self.assertEqual(float(re.search(r"exceeds ([\d.]+) s or (\d+) events/s is reached",
                                         section("### 2.3", "## 3.")).group(1)), rp.MEASURE_APPLY_P95_BOUND)
        counts = re.search(r"Tesseract at ([\d,]+), ([\d,]+) and ([\d,]+) nodes", s10)
        self.assertEqual(tuple(number(c) for c in counts.groups()), rp.MEASURE_NODE_COUNTS)
        self.assertIn("the first N records of the 2,000 world", s10)
        self.assertEqual(ms.GRAPH_WORLD, 2000)

    def test_the_measure_jobs_artifacts_and_the_report_names(self) -> None:
        s5 = section("## 5. The JSON report", "## 6.")
        for name in (rp.REPORT_NAME, rp.MEASURE_REPORT_NAME):
            self.assertIn(f"`{name}`", s5)
        self.assertEqual(ms.ALLOCATION, "back-loaded")
        self.assertIn('`"back-loaded"`', section("**M24.", "**M25."))

    def test_the_amendments_of_the_build_review_are_in_the_brief_and_in_the_code(self) -> None:
        """The two amendments of 2026-10-13: section 11's fifth job (``suites-e2e``) and M14's budgets (Tesseract
        8, size 2,000 12, the total still 42 of 45)."""
        self.assertIn("### Amendment 2026-10-13, after the build review (section 11: a fifth job)", BRIEF_TEXT)
        self.assertIn("### Amendment 2026-10-13, after the build review (M14: two budgets moved", BRIEF_TEXT)
        self.assertEqual((rp.MEASURE_PHASE_BUDGET_MINUTES["tesseract-nodes"],
                          rp.MEASURE_PHASE_BUDGET_MINUTES["size-2000"]), (8, 12))
        self.assertIn("`suites-e2e`", section("## 11. The CI workflow", "## 12."))
        self.assertIn("suites-e2e", JOBS)


if __name__ == "__main__":
    unittest.main()
