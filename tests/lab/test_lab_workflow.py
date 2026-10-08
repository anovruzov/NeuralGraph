"""The cloud lab workflow, read as data: triggers, permissions, pinned actions, run blocks that hold no expression and
only lab commands, the token in one step, the openFDA key and the two hosted secrets each in exactly the plan step's
presence flag and the run step's environment (the run step's only for the shards that need them), the preregistration
step, job and step conditions, artifact names, the job clock and the cache paths. Every lab command in it must parse
with that module's own argument parser.

PyYAML reads the ``on:`` key as the boolean True, so the triggers are ``wf.get("on", wf.get(True))``. PyYAML is used
here only; the lab itself never imports it.
"""
from __future__ import annotations

import importlib
import re
import shlex
import unittest
from typing import Any, Iterator

import yaml

from lab import discover
from tests.lab.helpers import ROOT
from tests.mycelic.test_collective_guards import model_name_hits

WORKFLOW = ROOT / ".github" / "workflows" / "mycelic-lab.yml"
TEXT = WORKFLOW.read_text(encoding="utf-8")
WF = yaml.safe_load(TEXT)
JOBS = WF["jobs"]
USES_LINE_RE = re.compile(r"\s*(- )?uses: ([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(/[A-Za-z0-9_.-]+)*)@([0-9a-f]{40}) "
                          r"# v[0-9]+\.[0-9]+\.[0-9]+")
ACTIONS = {"actions/checkout", "actions/setup-python", "actions/cache/restore", "actions/cache/save",
           "actions/upload-artifact", "actions/download-artifact"}
SELF_TEST = "python -m unittest tests.lab.test_lab_request tests.lab.test_lab_plan"
STAMP = 'echo "LAB_JOB_START=$(date +%s)" >> "$GITHUB_ENV"'
LAB_COMMAND_RE = re.compile(r"python -m lab\.[a-z_]+ .+")
CANCELLED = "!cancelled()"
PLAN_ARTIFACT = "lab-plan-${{ github.run_id }}-${{ github.run_attempt }}"
CACHE_ROOT = "${{ runner.temp }}/lab-cache/"


def steps(job: str) -> list[dict[str, Any]]:
    return JOBS[job]["steps"]


def all_steps() -> Iterator[tuple[str, int, dict[str, Any]]]:
    for name, job in JOBS.items():
        for i, step in enumerate(job["steps"]):
            yield name, i, step


def step(job: str, step_id: str) -> dict[str, Any]:
    return next(s for s in steps(job) if s.get("id") == step_id)


def uses(step_: dict[str, Any]) -> str:
    return step_.get("uses", "").split("@")[0]


def runs() -> list[tuple[str, dict[str, Any]]]:
    return [(name, s) for name, _, s in all_steps() if "run" in s]


def scalars(node: Any, path: tuple[Any, ...] = ()) -> Iterator[tuple[tuple[Any, ...], str]]:
    if isinstance(node, dict):
        for key, value in node.items():
            yield from scalars(value, (*path, key))
    elif isinstance(node, list):
        for i, value in enumerate(node):
            yield from scalars(value, (*path, i))
    elif isinstance(node, str):
        yield path, node


def keys(node: Any) -> Iterator[Any]:
    if isinstance(node, dict):
        for key, value in node.items():
            yield key
            yield from keys(value)
    elif isinstance(node, list):
        for value in node:
            yield from keys(value)


class WorkflowTests(unittest.TestCase):
    def test_triggers(self) -> None:
        on = WF.get("on", WF.get(True))
        self.assertEqual(set(on), {"push", "workflow_dispatch"})
        self.assertEqual(on["push"], {"branches-ignore": ["main"], "paths": ["lab/requests/*.json"]})
        self.assertEqual(discover.REQUEST_GLOB, ":(glob)" + on["push"]["paths"][0])
        self.assertEqual(discover.WORKFLOW_FILE, WORKFLOW.name)
        request = on["workflow_dispatch"]["inputs"]["request"]
        self.assertIs(request["required"], True)
        self.assertEqual(request["type"], "string")
        self.assertEqual(set(on["workflow_dispatch"]["inputs"]), {"request"})

    def test_permissions_concurrency_defaults(self) -> None:
        self.assertEqual(WF["permissions"], {"contents": "read"})
        for name, job in JOBS.items():
            self.assertNotIn("permissions", job, name)
        self.assertNotIn("models: read", TEXT)
        self.assertIsNone(re.search(r"^\s*models\s*:", TEXT, re.M))
        self.assertNotIn("models", set(keys(WF)))
        self.assertNotIn("concurrency", set(keys(WF)))
        self.assertEqual(WF["defaults"], {"run": {"shell": "bash"}})

    def test_jobs_and_runners(self) -> None:
        self.assertEqual(set(JOBS), {"plan", "provision", "run", "aggregate"})
        for name, job in JOBS.items():
            self.assertEqual(job["runs-on"], "ubuntu-24.04", name)

    def test_actions_pinned_and_checkout_credentials(self) -> None:
        shas: dict[str, set[str]] = {}
        lines = [line for line in TEXT.splitlines() if "uses:" in line]
        self.assertTrue(lines)
        for line in lines:
            m = USES_LINE_RE.fullmatch(line)
            self.assertIsNotNone(m, line)
            shas.setdefault(m.group(2), set()).add(m.group(4))
        self.assertEqual(set(shas), ACTIONS)
        self.assertTrue(all(len(v) == 1 for v in shas.values()), shas)
        parsed = [s for _, _, s in all_steps() if "uses" in s]
        self.assertEqual(len(parsed), len(lines))
        checkouts = [s for s in parsed if uses(s) == "actions/checkout"]
        self.assertEqual(len(checkouts), len(JOBS))
        for s in checkouts:
            self.assertIs(s["with"]["persist-credentials"], False)

    def test_run_blocks_are_whitelisted_and_expression_free(self) -> None:
        commands = runs()
        self.assertTrue(commands)
        for job, s in commands:
            run = s["run"]
            with self.subTest(job=job, run=run[:60]):
                self.assertNotIn("${{", run)
                self.assertNotIn("\n", run)
                for word in ("pip", "apt", "curl", "wget"):
                    self.assertNotIn(word, run)
                self.assertTrue(run == SELF_TEST or run == STAMP or LAB_COMMAND_RE.fullmatch(run), run)
        self.assertEqual([s["run"] for _, s in commands if not s["run"].startswith("python -m lab.")],
                         [SELF_TEST, STAMP])

    def test_secrets_and_token_only_in_provision_server_step(self) -> None:
        holders = []
        for path, value in scalars(WF):
            if "secrets." in value:
                self.assertEqual((path[0], path[2], path[4]), ("jobs", "steps", "env"), path)
                holders.append((path[1], steps(path[1])[path[3]].get("id"), path[5], value))
        self.assertEqual(holders, [
            ("plan", "plan", "LAB_HAS_OPENFDA_KEY", "${{ secrets.MYCELIC_LAB_OPENFDA_API_KEY != '' }}"),
            ("plan", "plan", "LAB_HAS_HOSTED",
             "${{ secrets.MYCELIC_LAB_HOSTED_API_KEY != '' && secrets.MYCELIC_LAB_HOSTED_BASE_URL != '' }}"),
            ("run", "run", "MYCELIC_LAB_OPENFDA_API_KEY",
             "${{ matrix.openfda && secrets.MYCELIC_LAB_OPENFDA_API_KEY || '' }}"),
            ("run", "run", "MYCELIC_LAB_HOSTED_API_KEY",
             "${{ matrix.hosted && secrets.MYCELIC_LAB_HOSTED_API_KEY || '' }}"),
            ("run", "run", "MYCELIC_LAB_HOSTED_BASE_URL",
             "${{ matrix.hosted && secrets.MYCELIC_LAB_HOSTED_BASE_URL || '' }}")])
        self.assertEqual(TEXT.count("secrets."), 6)
        self.assertEqual(set(re.findall(r"secrets\.([A-Z_]+)", TEXT)),
                         {"MYCELIC_LAB_OPENFDA_API_KEY", "MYCELIC_LAB_HOSTED_API_KEY", "MYCELIC_LAB_HOSTED_BASE_URL"})
        self.assertEqual(sorted(re.findall(r"secrets\.([A-Z_]+)", TEXT)),
                         ["MYCELIC_LAB_HOSTED_API_KEY"] * 2 + ["MYCELIC_LAB_HOSTED_BASE_URL"] * 2
                         + ["MYCELIC_LAB_OPENFDA_API_KEY"] * 2)
        self.assertEqual(TEXT.count("LAB_HAS_OPENFDA_KEY"), 1)
        self.assertEqual(TEXT.count("LAB_HAS_HOSTED"), 1)
        for name in ("MYCELIC_LAB_OPENFDA_API_KEY:", "MYCELIC_LAB_HOSTED_API_KEY:", "MYCELIC_LAB_HOSTED_BASE_URL:"):
            self.assertEqual(TEXT.count(name), 1, name)
        self.assertEqual(set(step("plan", "plan")["env"]), {"LAB_HAS_OPENFDA_KEY", "LAB_HAS_HOSTED"})
        self.assertEqual(set(step("run", "run")["env"]), {"LAB_DEADLINE", "MYCELIC_LAB_OPENFDA_API_KEY",
                                                          "MYCELIC_LAB_HOSTED_API_KEY", "MYCELIC_LAB_HOSTED_BASE_URL"})
        self.assertNotIn("--openfda-base-url", TEXT)
        self.assertEqual(TEXT.count("github.token"), 1)
        self.assertEqual(TEXT.count("GH_TOKEN"), 1)
        self.assertEqual(step("provision", "server")["env"]["GH_TOKEN"], "${{ github.token }}")
        holders = [(job, i) for job, i, s in all_steps() if "GH_TOKEN" in s.get("env", {})]
        self.assertEqual(holders, [("provision", steps("provision").index(step("provision", "server")))])

    def test_no_provider_override_and_no_model_names(self) -> None:
        for _, s in runs():
            self.assertNotIn("--provider", s["run"])
        self.assertEqual(model_name_hits(TEXT), [])

    def test_job_conditions_and_needs(self) -> None:
        self.assertEqual(JOBS["provision"]["if"], "needs.plan.outputs.has_provision == 'true'")
        self.assertEqual(JOBS["provision"]["needs"], "plan")
        for name in ("run", "aggregate"):
            self.assertEqual(JOBS[name]["if"], "${{ !cancelled() && needs.plan.result == 'success' && "
                                               "needs.plan.outputs.has_units == 'true' }}")
        self.assertEqual(JOBS["run"]["needs"], ["plan", "provision"])
        self.assertEqual(JOBS["aggregate"]["needs"], ["plan", "provision", "run"])
        for name, output in (("provision", "provision_matrix"), ("run", "matrix")):
            strategy = JOBS[name]["strategy"]
            self.assertIs(strategy["fail-fast"], False)
            self.assertEqual(strategy["matrix"], f"${{{{ fromJSON(needs.plan.outputs.{output}) }}}}")
        self.assertEqual(JOBS["run"]["strategy"]["max-parallel"], "${{ fromJSON(needs.plan.outputs.max_parallel) }}")
        save = next(s for s in steps("provision") if uses(s) == "actions/cache/save")
        for ref in ("steps.server.outputs.verified", "steps.server.outputs.save", "steps.gguf.outputs.verified",
                    "steps.gguf.outputs.save"):
            self.assertIn(f"{ref} == 'true'", save["if"])
        self.assertEqual(step("provision", "server")["if"], "matrix.target == 'server'")
        self.assertEqual(step("provision", "gguf")["if"], "matrix.target == 'gguf'")

    def test_step_conditions_cancelled_and_continue_on_error(self) -> None:
        checked = 0
        for job, _, s in all_steps():
            is_summary = "run" in s and s["run"].startswith("python -m lab.summary ")
            if uses(s) == "actions/upload-artifact" or s.get("id") == "seal" or is_summary:
                self.assertIn(CANCELLED, s.get("if", ""), (job, s))
                checked += 1
            if uses(s) == "actions/cache/restore":
                self.assertIs(s.get("continue-on-error"), True, (job, s))
        self.assertEqual(checked, 4 + 1 + 3)

    def test_artifact_names_and_plan_artifact_output(self) -> None:
        self.assertEqual(JOBS["plan"]["outputs"]["plan_artifact"], PLAN_ARTIFACT)
        uploads = {job: [s for s in steps(job) if uses(s) == "actions/upload-artifact"] for job in JOBS}
        self.assertTrue(all(len(v) == 1 for v in uploads.values()), uploads)
        self.assertEqual(uploads["plan"][0]["with"]["name"], PLAN_ARTIFACT)
        for job, (upload,) in uploads.items():
            self.assertEqual(upload["with"]["if-no-files-found"], "error", job)
        prov = uploads["provision"][0]["with"]["name"]
        for part in ("github.run_id", "github.run_attempt", "matrix.entry"):
            self.assertIn(part, prov)
        run = uploads["run"][0]["with"]
        for part in ("github.run_id", "github.run_attempt", "matrix.shard"):
            self.assertIn(part, run["name"])
        self.assertIs(run["include-hidden-files"], True)
        report = uploads["aggregate"][0]["with"]["name"]
        for part in ("needs.plan.outputs.result_class", "github.run_id", "github.run_attempt"):
            self.assertIn(part, report)
        plan_downloads = [s for _, _, s in all_steps() if uses(s) == "actions/download-artifact"
                          and "name" in s["with"]]
        self.assertEqual(len(plan_downloads), 3)
        for s in plan_downloads:
            self.assertEqual(s["with"]["name"], "${{ needs.plan.outputs.plan_artifact }}")
        patterns = [s["with"]["pattern"] for _, _, s in all_steps() if uses(s) == "actions/download-artifact"
                    and "pattern" in s["with"]]
        self.assertEqual(sorted(patterns), ["lab-prov-${{ github.run_id }}-*", "lab-prov-${{ github.run_id }}-*",
                                            "lab-run-${{ github.run_id }}-*"])

    def test_clock_and_timeouts(self) -> None:
        self.assertEqual(steps("run")[0].get("run"), STAMP)
        self.assertEqual(JOBS["run"]["timeout-minutes"], "${{ matrix.timeout_minutes }}")
        run = step("run", "run")
        self.assertEqual(run["timeout-minutes"], "${{ fromJSON(steps.prepare.outputs.shard_minutes) }}")
        self.assertEqual(run["if"], "steps.prepare.outcome == 'success'")
        self.assertEqual(run["env"]["LAB_DEADLINE"], "${{ steps.prepare.outputs.deadline_epoch }}")
        self.assertIn('--deadline-epoch "$LAB_DEADLINE"', run["run"])
        prepare = step("run", "prepare")
        self.assertIsInstance(prepare["timeout-minutes"], int)
        self.assertEqual(prepare["env"]["LAB_JOB_TIMEOUT"], "${{ matrix.timeout_minutes }}")
        self.assertIn('--job-start-epoch "$LAB_JOB_START" --job-timeout-minutes "$LAB_JOB_TIMEOUT"', prepare["run"])
        for name, job in JOBS.items():
            self.assertIn("timeout-minutes", job, name)
        self.assertLessEqual(JOBS["plan"]["timeout-minutes"], 30)
        self.assertLessEqual(JOBS["aggregate"]["timeout-minutes"], 30)
        self.assertLessEqual(JOBS["provision"]["timeout-minutes"], 60)
        for s in (step("provision", "server"), step("provision", "gguf")):
            self.assertLess(s["timeout-minutes"], JOBS["provision"]["timeout-minutes"])

    def test_cache_paths_identical(self) -> None:
        restore = step("provision", "restore")
        save = next(s for s in steps("provision") if uses(s) == "actions/cache/save")
        self.assertEqual(restore["with"]["path"], save["with"]["path"])
        self.assertEqual(restore["with"]["path"], CACHE_ROOT + "${{ matrix.cache_path }}")
        self.assertEqual(restore["with"]["key"], "${{ matrix.cache_key }}")
        self.assertEqual(save["with"]["key"], "${{ steps.server.outputs.save_key }}${{ steps.gguf.outputs.save_key }}")
        run_restores = [s for s in steps("run") if uses(s) == "actions/cache/restore"]
        self.assertEqual(len(run_restores), 2)
        for s, target in zip(run_restores, ("server", "model")):
            self.assertEqual(s["if"], f"steps.keys.outputs.{target}_key != ''")
            self.assertTrue(s["with"]["path"].startswith(CACHE_ROOT), s)
            self.assertEqual(s["with"]["path"], CACHE_ROOT + f"${{{{ steps.keys.outputs.{target}_path }}}}")
            self.assertEqual(s["with"]["key"], f"${{{{ steps.keys.outputs.{target}_key }}}}")
            self.assertEqual(s["with"]["restore-keys"], f"${{{{ steps.keys.outputs.{target}_prefix }}}}")
        self.assertIn('--cache-root "$RUNNER_TEMP/lab-cache"', step("run", "prepare")["run"])

    def test_seal_receives_every_prior_step_outcome(self) -> None:
        run_steps = steps("run")
        seal = step("run", "seal")
        before = [s["id"] for s in run_steps[:run_steps.index(seal)] if "id" in s]
        self.assertEqual(before, ["get-plan", "get-prov", "keys", "prepare", "run"])
        pairs = re.findall(r'--step-outcome "([a-z-]+)=\$([A-Z_]+)"', seal["run"])
        self.assertEqual([name for name, _ in pairs], before)
        for name, var in pairs:
            self.assertEqual(seal["env"][var], f"${{{{ steps.{name}.outcome }}}}")
        self.assertIn('--plan "$RUNNER_TEMP/lab-plan/plan.json"', seal["run"])

    def test_every_lab_command_parses(self) -> None:
        commands = [s["run"] for _, s in runs() if s["run"].startswith("python -m lab.")]
        modules = set()
        for command in commands:
            args = [re.sub(r"\$([A-Z_]+)", lambda m: "push" if m.group(1) == "GITHUB_EVENT_NAME" else "1", a)
                    for a in shlex.split(command)]
            self.assertEqual(args[:2], ["python", "-m"])
            module = importlib.import_module(args[2])
            modules.add(args[2] + (" " + args[3] if not args[3].startswith("-") else ""))
            with self.subTest(command=command[:70]):
                try:
                    module._parser().parse_args(args[3:])
                except SystemExit as exc:
                    self.fail(f"{command} does not parse ({exc})")
        self.assertEqual(modules, {"lab.plan", "lab.prereg", "lab.provision server", "lab.provision gguf",
                                   "lab.shard cache-keys", "lab.shard prepare", "lab.shard run", "lab.shard seal",
                                   "lab.summary plan", "lab.summary shard", "lab.summary report", "lab.aggregate"})

    def test_prereg_step_before_any_shard(self) -> None:
        plan_steps = steps("plan")
        prereg = step("plan", "prereg")
        self.assertEqual(prereg, {"id": "prereg", "if": "steps.plan.outputs.has_units == 'true'",
                                  "timeout-minutes": 20,
                                  "run": 'python -m lab.prereg --plan "$RUNNER_TEMP/lab-plan/plan.json"'})
        order = [s.get("id") or s.get("uses", s.get("run", ""))[:30] for s in plan_steps]
        at = plan_steps.index(prereg)
        self.assertEqual(plan_steps[at - 1].get("id"), "plan")
        later = plan_steps[at + 1:]
        self.assertTrue(any("lab.summary plan" in s.get("run", "") for s in later), order)
        self.assertTrue(any(uses(s) == "actions/upload-artifact" for s in later), order)
        self.assertEqual(JOBS["plan"]["timeout-minutes"], 30)
        self.assertEqual(step("plan", "plan")["env"], {
            "LAB_HAS_OPENFDA_KEY": "${{ secrets.MYCELIC_LAB_OPENFDA_API_KEY != '' }}",
            "LAB_HAS_HOSTED": "${{ secrets.MYCELIC_LAB_HOSTED_API_KEY != '' && secrets.MYCELIC_LAB_HOSTED_BASE_URL "
                              "!= '' }}"})
        self.assertEqual(JOBS["run"]["needs"], ["plan", "provision"])
        upload = next(s for s in plan_steps if uses(s) == "actions/upload-artifact")
        self.assertEqual(upload["with"]["path"], "${{ runner.temp }}/lab-plan")


if __name__ == "__main__":
    unittest.main()
