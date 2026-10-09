"""The founder documents against the code, and the request templates against the validator and the planner.

The documents are ``docs/lab/*.md`` and ``lab/requests/README.md``. Every lab or collective command in their fenced
blocks must parse with its module's own parser; every dispatch line must be the one ``lab.discover`` prints; every
secret they name must be one the workflow reads; every quoted label must equal its ``lab.notes`` constant; the
reference must list every request, manifest, lock, provenance and status key, every status, class, column and summary
heading; the guide must keep its sections, sentences, first-run order and a troubleshooting row for every message a
founder can meet. A rename in the code turns this file red. Standard library only: no YAML, no Markdown parser.
"""
from __future__ import annotations

import contextlib
import importlib
import io
import json
import re
import shlex
import shutil
import tempfile
import unittest
from pathlib import Path
from typing import Any

from lab import discover, manifest, notes, request, shard, units
from lab.manifest import load_manifest
from lab.plan import build_plan
from lab.request import HOSTED_CENTRAL_CONTEXT, RequestError, load_request
from tests.lab.helpers import (LAB_MANIFEST, PLUMBING_DRYRUN_ARGS, ROOT, TEMPLATES, GitWorld, run_plan, run_prereg,
                               write_json)

DOCS = ROOT / "docs" / "lab"
README = DOCS / "README.md"
REFERENCE = DOCS / "REFERENCE.md"
MODELS = DOCS / "MODELS.md"
INTEGRATION = DOCS / "INTEGRATION.md"
REQUESTS_README = ROOT / "lab" / "requests" / "README.md"
WORKFLOW = ROOT / ".github" / "workflows" / "mycelic-lab.yml"
DOC_FILES = (README, REFERENCE, MODELS, INTEGRATION, REQUESTS_README)

README_HEADINGS = ("What the lab is", "What a run costs", "Everything is public", "Request a run",
                   "The first runs, in order", "Cancel or re-run", "Read the results", "Keep results",
                   "Optional secrets", "What the lab does not do", "Troubleshooting")
REFERENCE_HEADINGS = ("Request format", "Model manifest and lock", "Ids", "Shard root", "Provenance and status files",
                      "Statuses", "Classes", "Exit codes", "Labels", "Columns", "Summary sections",
                      "Artifacts and caches")
PUBLIC_SENTENCE = ("Everything a run writes is public: anyone can read this public repository's run logs, step "
                   "summaries and artifacts.")
DISPATCH_SENTENCE = "Pushes to main never run a request; on main a request runs only by dispatch."
UNVERIFIED_SENTENCE = ("The pinned server release asset and the model file names in lab/models.json are unverified "
                       "until the check run downloads and verifies them.")
FIRST_RUNS = ("plumbing-001", "check-001", "smoke-001", "lab/models.lock.json", "main-001")
SIZING_SENTENCE = ("Larger models are slower on the same runner, and one `minutes` value serves every model of a "
                   "block, so size each block by its slowest model")

SINGLE_LABELS = ("PLUMBING_BANNER", "PLUMBING_HOSTED_BANNER", "PLUMBING_CHECK_LINE", "PLUMBING_HOSTED_LINE",
                 "NO_MEASUREMENT_LINE", "X1_LABEL", "OPENFDA_LABEL", "OPENFDA_PUBLIC_FLAG", "SHEETS_LABEL",
                 "G0_BELOW_PROTOCOL", "G0_MODEL_PATH", "SIZING_NOTE", "E2_SIZING_NOTE", "E1_ENDPOINT_EXCLUDED",
                 "E1_VERDICTS_WITHHELD", "E1_HOSTED_LABEL", "HOSTED_COST_NOTE", "SIM_WORLD_SAME", "SIM_WORLD_DIFFERS",
                 "WORLD_DIGEST_SAME", "WORLD_DIGEST_DIFFERS", "CPU_MODELS_DIFFER", "NOT_PINNED", "LOCK_UNCHANGED",
                 "LOCK_NEW", "LOCK_CONFLICT_NOTE", "LOCK_NOT_COMPUTED", "BY_CONSTRUCTION_LABEL",
                 "BY_CONSTRUCTION_NOTE", "OPENFDA_SAW_RECALLS", "OPENFDA_WARNED", "OPENFDA_FALSE_ALARM_SCOPE",
                 "E1_SCORES_NOTE", "E1_SCORES_ONLY", "E1_SCORES_REFUSED", "E1_SCORES_UNPINNED", "REAGGREGATION_LINE")
DICT_LABELS = ("NOTES", "SIM_NOTES", "SIM_CHANNEL_LABELS", "SIM_LIFT_LABELS", "SIM_MEASUREMENT_REASONS", "E1_LABELS",
               "E2_LABELS", "CLASS_REASONS")
REQUIRED_LABELS = (*SINGLE_LABELS, *(f"{d}.{k}" for d in DICT_LABELS for k in getattr(notes, d)))
README_LABELS = ("E1_LABELS.generator_text", "E2_LABELS.synthetic", "X1_LABEL", "OPENFDA_LABEL", "SHEETS_LABEL",
                 "SIM_NOTES.synthetic_internal", "SIM_NOTES.lexical_exact", "NOTES.runner_hardware",
                 "NOTES.text_only_scan", "PLUMBING_BANNER", "BY_CONSTRUCTION_NOTE", "OPENFDA_FALSE_ALARM_SCOPE",
                 "OPENFDA_SAW_RECALLS", "OPENFDA_WARNED")

TROUBLESHOOTING = (
    # push discovery
    "SEVERAL_REQUESTS", "MERGE_SEVERAL", "NO_REQUEST_CHANGE", "NO_BASE", "CHECKOUT_MISMATCH", "BAD_REQUEST_NAME",
    "DEFAULT_BRANCH", "DELETE_ONLY", "PLAN_CHANGED",
    # provisioning
    "RELEASE_TAG_MISSING", "ASSET_MISSING", "ASSET_NOT_UPLOADED", "DIGEST_MISMATCH", "LOCK_MISMATCH", "HUB_REFUSED",
    "HUB_REDIRECTED", "HUB_UNAVAILABLE", "HUB_COMMIT_DIFFERS", "REPO_GATED", "LICENSE_DIFFERS", "FILE_MISSING",
    "FILE_TOO_LARGE", "HUB_DIFFERS_FROM_LOCK", "NOT_ENOUGH_DISK", "NOT_ENOUGH_MEMORY", "DOWNLOAD_DEADLINE",
    "LOCK_CONFLICT", "PROVISION_FAILED_SERVER", "PROVISION_FAILED_MODEL", "VERSION_FAILED",
    # the model server
    "SERVER_START_FAILED", "SERVER_NOT_HEALTHY", "SERVER_SETTINGS", "ALIAS_MISMATCH", "OOM_HINT", "MEMORY_AFTER_LOAD",
    "WARMUP_LENGTH", "WARMUP_REASONING", "CONTEXT_TOO_SMALL", "SERVER_UNAVAILABLE", "NOT_PREPARED",
    # units
    "BUDGET_EXHAUSTED", "LOW_PARTICIPATION", "NO_MODEL_CALLS", "E3_FAILURES", "TIMED_OUT", "SIM_PROJECTED",
    "SIM_LOW_PARTICIPATION", "E2_ABORTED", "PREREG_MISSING", "G0_MODEL_PATH",
    # openFDA
    "OPENFDA_UNREACHABLE", "OPENFDA_RATE_LIMITED", "OPENFDA_FETCH_REFUSED",
    # the hosted provider
    "HOSTED_SECRETS_MISSING", "HOSTED_SECRET_NOT_SET", "HOSTED_SECRET_BLANK", "HOSTED_KEY_NOT_TOKEN",
    "HOSTED_BASE_URL_INVALID", "HOSTED_PREFLIGHT_FAILED", "HOSTED_UNAVAILABLE", "HOSTED_LENGTH", "HOSTED_MAX_CALLS",
    "E1_WITHOUT_HOSTED", "E2_CENTRAL_HOSTED_SKIPPED",
    # the report
    "NO_ARTIFACT", "UNSEALED", "ALTERED", "OTHER_PLAN", "AMBIGUOUS_ARTIFACTS", "NO_PLAN", "NO_REPORT",
    "E1_NO_REFERENCE", "E1_COMPARE_FAILED")

LABEL_LINE_RE = re.compile(r"- `([A-Z][A-Z0-9_]*(?:\.[A-Za-z0-9_-]+)?)`: (.*)")
PLACEHOLDER_RE = re.compile(r"<[^<>]*>")
COMMAND_PREFIXES = ("python -m lab.", "python -m mycelic.collective.")
ARTIFACT_PREFIXES = ("lab-plan-", "lab-prov-", "lab-run-", "lab-report-")
TEMPLATE_NAMES = ["check.json", "hosted-comparison.json", "main.json", "openfda-replay.json", "smoke.json"]


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def fenced_lines(text: str) -> list[str]:
    """Every line inside a fenced block, with ``\\`` continuations joined."""
    lines, inside, pending = [], False, ""
    for line in text.splitlines():
        if line.strip().startswith("```"):
            inside = not inside
            continue
        if not inside:
            continue
        stripped = line.strip()
        if stripped.endswith("\\"):
            pending += stripped[:-1] + " "
            continue
        lines.append(pending + stripped)
        pending = ""
    return lines


def substitute(line: str) -> str:
    line = line.replace("<branch>", "lab").replace("<name>", "main-001")
    return PLACEHOLDER_RE.sub("x", line)


def commands(text: str) -> list[str]:
    return [line for line in fenced_lines(text) if line.startswith(COMMAND_PREFIXES)]


def h2_sections(text: str) -> dict[str, str]:
    """``{heading: body}`` of every ``## `` section, in order."""
    out: dict[str, str] = {}
    name = None
    for line in text.splitlines():
        if line.startswith("## "):
            name = line[3:].strip()
            out[name] = ""
        elif name is not None:
            out[name] += line + "\n"
    return out


def code_spans(text: str) -> set[str]:
    return set(re.findall(r"`([^`\n]+)`", text))


def table_rows(text: str) -> list[list[str]]:
    """The cells of every table row that is not a header separator."""
    rows = []
    for line in text.splitlines():
        if line.startswith("|") and not re.fullmatch(r"\|( ?-+ ?\|)+", line.replace(" --- ", "---")):
            cells = [c.strip() for c in line.strip().strip("|").split(" | ")]
            if not all(set(c) <= {"-", " "} for c in cells):
                rows.append(cells)
    return rows


def label_value(name: str) -> Any:
    head, _, key = name.partition(".")
    value = getattr(notes, head, None)
    if key:
        return value.get(key) if isinstance(value, dict) else None
    return value if isinstance(value, str) else None


# --------------------------------------------------------------------------------------------------- commands

class DocsCommandsTests(unittest.TestCase):
    def test_every_command_parses(self) -> None:
        seen = 0
        for path in DOC_FILES:
            for line in commands(_text(path)):
                argv = shlex.split(substitute(line))
                with self.subTest(doc=path.name, command=line):
                    self.assertEqual(argv[:2], ["python", "-m"])
                    module = importlib.import_module(argv[2])
                    with contextlib.redirect_stderr(io.StringIO()) as err:
                        try:
                            module._parser().parse_args(argv[3:])
                        except SystemExit:
                            self.fail(f"{argv[2]} refused {argv[3:]}: {err.getvalue()}")
                seen += 1
        self.assertGreaterEqual(seen, 4)

    def test_the_guide_has_the_dry_run_plan_and_merge_lock_commands(self) -> None:
        argvs = [shlex.split(substitute(line)) for line in commands(_text(README))]
        modules = [(a[2], a[3] if len(a) > 3 else None) for a in argvs]
        self.assertIn(("lab.dryrun", "--request"), modules)
        self.assertIn(("lab.plan", "--request"), modules)
        self.assertIn(("lab.provision", "merge-lock"), modules)
        (dry,) = [a for a in argvs if a[2] == "lab.dryrun"]
        self.assertEqual(dry[:-1], ["python", "-m", *PLUMBING_DRYRUN_ARGS])
        self.assertEqual(len(dry), len(PLUMBING_DRYRUN_ARGS) + 3)
        self.assertIn("python -m lab.plan --request lab/requests/<name>.json --manifest lab/models.json --out "
                      "<empty dir>", commands(_text(REQUESTS_README)))

    def test_dispatch_lines_are_the_discover_command(self) -> None:
        expected = discover.dispatch_command("lab", "lab/requests/main-001.json")
        found = 0
        for path in DOC_FILES:
            for line in _text(path).splitlines():
                if "gh workflow run" not in line:
                    continue
                with self.subTest(doc=path.name, line=line):
                    self.assertEqual(substitute(line.strip()), expected)
                found += path == README
        self.assertGreaterEqual(found, 1)


# --------------------------------------------------------------------------------------------------- secrets

class SecretNamesTests(unittest.TestCase):
    def test_every_named_secret_is_a_workflow_secret(self) -> None:
        workflow = _text(WORKFLOW)
        names = {n for path in DOC_FILES for n in re.findall(r"MYCELIC_LAB_[A-Z0-9_]+", _text(path))}
        self.assertEqual(names, {"MYCELIC_LAB_HOSTED_BASE_URL", "MYCELIC_LAB_HOSTED_API_KEY",
                                 "MYCELIC_LAB_OPENFDA_API_KEY"})
        for name in names:
            self.assertIn(f"secrets.{name}", workflow, name)

    def test_every_workflow_secret_is_in_the_guide(self) -> None:
        secrets = set(re.findall(r"secrets\.(\w+)", _text(WORKFLOW)))
        self.assertTrue(secrets)
        readme = _text(README)
        for name in secrets:
            self.assertIn(name, readme, name)


# --------------------------------------------------------------------------------------------------- labels

class LabelQuoteTests(unittest.TestCase):
    def labels(self, path: Path) -> dict[str, str]:
        out = {}
        for line in _text(path).splitlines():
            m = LABEL_LINE_RE.fullmatch(line)
            if m is not None:
                out[m.group(1)] = m.group(2)
        return out

    def test_every_label_line_equals_its_constant(self) -> None:
        total = 0
        for path in DOC_FILES:
            for name, text in self.labels(path).items():
                with self.subTest(doc=path.name, label=name):
                    self.assertEqual(text, label_value(name))
                total += 1
        self.assertGreater(total, len(REQUIRED_LABELS))

    def test_the_reference_quotes_every_required_label(self) -> None:
        quoted = self.labels(REFERENCE)
        self.assertEqual(sorted(set(REQUIRED_LABELS) - set(quoted)), [])
        self.assertGreater(len(REQUIRED_LABELS), 70)

    def test_the_guide_quotes_each_experiment_label(self) -> None:
        quoted = self.labels(README)
        self.assertEqual(sorted(set(README_LABELS) - set(quoted)), [])
        for name in ("PLUMBING_CHECK_LINE", "PLUMBING_HOSTED_LINE", "NO_MEASUREMENT_LINE", "PLUMBING_HOSTED_BANNER"):
            self.assertIn(name, quoted)


# --------------------------------------------------------------------------------------------------- reference

class ReferenceCompletenessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.text = _text(REFERENCE)
        cls.sections = h2_sections(cls.text)

    def spans(self, heading: str) -> set[str]:
        return code_spans(self.sections[heading])

    def test_section_order(self) -> None:
        self.assertEqual(tuple(self.sections), REFERENCE_HEADINGS)

    def test_request_keys(self) -> None:
        keys = [*request._TOP_KEYS, "hosted", "max_calls", *request._E1_KEYS, *request._LABELS_KEYS,
                *request._E2_KEYS, *request._X1_KEYS, *request._E3_KEYS, *request._G0_KEYS, *request._SIM_KEYS,
                *request._OPENFDA_KEYS, *request._SHEET_KEYS]
        spans = self.spans("Request format")
        self.assertEqual(sorted({k for k in keys if k not in spans}), [])

    def test_manifest_and_lock_keys(self) -> None:
        keys = [*manifest._TOP_KEYS, *manifest._SERVER_KEYS, *manifest._FAKE_KEYS, *manifest._GGUF_KEYS,
                *manifest._GGUF_REF_KEYS, *manifest._HOSTED_KEYS, *manifest._PRICE_KEYS, *manifest._LOCK_KEYS,
                *manifest._LOCK_SERVER_KEYS, *manifest._LOCK_MODEL_KEYS]
        spans = self.spans("Model manifest and lock")
        self.assertIn("context_tokens", keys)
        self.assertEqual(sorted({k for k in keys if k not in spans}), [])
        for form in ("lab-server-<tag>-<sha16>", "lab-gguf-<key>-<sha16>"):
            self.assertIn(form, spans)

    def test_provenance_and_status_keys(self) -> None:
        spans = self.spans("Provenance and status files")
        self.assertEqual(sorted(k for k in (*shard.PROVENANCE_KEYS, *shard.STATUS_KEYS) if k not in spans), [])

    def test_statuses_and_classes(self) -> None:
        statuses = self.spans("Statuses")
        for status in (*units.STATUSES, "not_run", "excluded", "sealed", "unsealed", "altered", "other_plan",
                       "ambiguous", "no_artifact"):
            self.assertIn(status, statuses)
        classes = self.spans("Classes")
        for cls in (*units.DISPLAY_CLASSES, "lock", "hf-api", "github-api", "first-use"):
            self.assertIn(cls, classes)
        self.assertEqual(set(units.MODEL_VERIFIED_BY) | set(units.SERVER_VERIFIED_BY),
                         {"lock", "hf-api", "github-api", "first-use"})

    def test_exit_codes(self) -> None:
        codes = [row[0] for row in table_rows(self.sections["Exit codes"])]
        self.assertEqual(codes, ["code", "0", "1", "2", "3"])

    def test_every_column(self) -> None:
        rows = {row[0]: row for row in table_rows(self.sections["Columns"]) if len(row) == 3}
        self.assertEqual(rows.pop("column"), ["column", "means", "does not mean"])
        values = list(dict.fromkeys(notes.COLUMNS.values()))
        self.assertEqual(sorted(set(values) - set(rows)), [])
        self.assertEqual(sorted(set(rows) - set(values)), [])
        for value in values:
            with self.subTest(column=value):
                self.assertTrue(rows[value][1] and rows[value][2])

    def test_every_summary_section(self) -> None:
        rows = {row[0] for row in table_rows(self.sections["Summary sections"])}
        for heading in set(notes.HEADINGS.values()):
            self.assertIn(heading, rows)

    def test_artifacts(self) -> None:
        artifacts = self.sections["Artifacts and caches"]
        workflow = _text(WORKFLOW)
        for prefix in ARTIFACT_PREFIXES:
            self.assertIn(f"`{prefix}", artifacts, prefix)
            self.assertIn(f"name: {prefix}", workflow, prefix)


# --------------------------------------------------------------------------------------------------- the guide

class ReadmeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.text = _text(README)
        cls.sections = h2_sections(cls.text)

    def test_section_order(self) -> None:
        self.assertEqual(tuple(self.sections), README_HEADINGS)

    def test_required_sentences(self) -> None:
        self.assertIn(PUBLIC_SENTENCE, self.sections["Everything is public"])
        self.assertIn(DISPATCH_SENTENCE, self.sections["Request a run"])
        self.assertIn(UNVERIFIED_SENTENCE, self.text)
        self.assertIn(UNVERIFIED_SENTENCE, _text(MODELS))
        self.assertIn("lab/results/<request name>/", self.sections["Keep results"])

    def test_first_runs_in_order(self) -> None:
        body = self.sections["The first runs, in order"]
        positions = [body.find(name) for name in FIRST_RUNS]
        self.assertNotIn(-1, positions)
        self.assertEqual(positions, sorted(positions))

    def test_sizing_is_per_model(self) -> None:
        """Step 5 sizes each block by its slowest model, from each model's own smoke numbers, and its G0 and E1
        estimates count every call those units make (G0's judge calls too)."""
        body = self.sections["The first runs, in order"]
        step = " ".join(body[body.index("5. Set the minutes"):body.index("6. main-001")].split())
        self.assertIn(SIZING_SENTENCE, step)
        for needed in ("`extract_claims`", "`judge_record`", "`labels.n`", "`experiments.sim.minutes`",
                       "`experiments.e1.minutes`", "the largest", "capacity",
                       f"`{notes.SIM_PROJECTED}`", f"`{notes.E1_NO_REFERENCE}`"):
            with self.subTest(needed=needed):
                self.assertIn(needed, step)
        self.assertIn("Simulation sizing for the next request", step)
        self.assertEqual(notes.HEADINGS["sizing"], "Simulation sizing for the next request")
        self.assertIn("Model call latency from the ledgers", step)
        self.assertEqual(notes.HEADINGS["latency"], "Model call latency from the ledgers")
        self.assertIn("needs the largest suggestion among its models", notes.SIZING_NOTE)

    def test_a_troubleshooting_row_for_every_message(self) -> None:
        rows = {row[0]: row for row in table_rows(self.sections["Troubleshooting"])}
        self.assertEqual(rows.pop("message"), ["message", "what to do"])
        self.assertEqual(len(TROUBLESHOOTING), len(set(TROUBLESHOOTING)))
        for name in TROUBLESHOOTING:
            value = getattr(notes, name)
            with self.subTest(name=name):
                self.assertIsInstance(value, str)
                self.assertIn(f"`{value}`", rows)
                self.assertEqual(len(rows[f"`{value}`"]), 2)
                self.assertTrue(rows[f"`{value}`"][1])

    def test_the_hosted_entry(self) -> None:
        secrets = self.sections["Optional secrets"]
        self.assertIn('"big-hosted"', secrets)
        self.assertIn('"context_tokens"', secrets)
        self.assertEqual(sorted(code_spans(secrets) & {"MYCELIC_LAB_HOSTED_BASE_URL", "MYCELIC_LAB_HOSTED_API_KEY",
                                                        "MYCELIC_LAB_OPENFDA_API_KEY"}),
                         ["MYCELIC_LAB_HOSTED_API_KEY", "MYCELIC_LAB_HOSTED_BASE_URL", "MYCELIC_LAB_OPENFDA_API_KEY"])


# --------------------------------------------------------------------------------------------------- research/mycelic

RESEARCH = ROOT / "research" / "mycelic"
MODEL_CLIENT_RE = re.compile(r"^\s*(?:import|from)\s+(?:urllib|http|requests|httpx|aiohttp|openai|socket)\b|base_url",
                             re.M)


class ResearchSimulatorTests(unittest.TestCase):
    """INTEGRATION.md section 6 and the guide's line on ``research/mycelic``, against the package: its benchmark calls
    no model, and its live measurements score real models' answer files, which the documents must not deny."""

    def test_no_module_of_the_package_calls_a_model(self) -> None:
        modules = sorted(RESEARCH.glob("*.py"))
        self.assertTrue(modules)
        for path in modules:
            with self.subTest(module=path.name):
                self.assertIsNone(MODEL_CLIENT_RE.search(_text(path)))

    def test_the_live_measurements_are_described(self) -> None:
        section = h2_sections(_text(INTEGRATION))["6. The research simulator"]
        self.assertIn("Its live measurements are measurements of real models.", section)
        self.assertNotIn("cannot use a model server", section)
        for name in ("models.py", "live_tasks.py", "score_live.py", "live_rank.py"):
            self.assertIn(f"`{name}`", section)
            self.assertTrue((RESEARCH / name).is_file(), name)
        self.assertIn("`live_rank_answers_<name>.json`", section)
        answers = sorted((RESEARCH / "artifacts").glob("live_rank_answers_*.json"))
        self.assertTrue(answers)
        for path in answers:
            self.assertLessEqual({"ranking", "real"}, set(json.loads(_text(path))), path.name)
        for name in ("live_rank_results.json", "live_rank_results_rich.json"):
            self.assertIn(f"`{name}`", section)
            self.assertTrue((RESEARCH / "artifacts" / name).is_file(), name)
        self.assertIn("blind candidate-discrimination measurement on real models", _text(RESEARCH / "README.md"))
        not_done = h2_sections(_text(README))["What the lab does not do"]
        for name in ("`research/mycelic`", "`live_rank.py`", "real models' answers"):
            self.assertIn(name, not_done)


# --------------------------------------------------------------------------------------------------- templates

def _checkout() -> Path:
    """A temporary checkout: a root with ``lab/requests/`` and a copy of the manifest and lock."""
    root = Path(tempfile.mkdtemp(prefix="lab-docs-"))
    (root / "lab" / "requests").mkdir(parents=True)
    shutil.copy(LAB_MANIFEST, root / "lab" / "models.json")
    shutil.copy(ROOT / "lab" / "models.lock.json", root / "lab" / "models.lock.json")
    return root


def _copy(root: Path, template: str, name: str | None = None) -> Path:
    stem = template.removesuffix(".json")
    target = root / "lab" / "requests" / (name or f"{stem}-001.json")
    shutil.copy(TEMPLATES / template, target)
    return target


class TemplateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = _checkout()
        self.addCleanup(shutil.rmtree, self.root, True)
        self.manifest = load_manifest(LAB_MANIFEST)

    def hosted_manifest(self, entry: dict[str, Any] | None) -> Any:
        doc = json.loads(LAB_MANIFEST.read_text(encoding="utf-8"))
        doc["models"].pop("big-hosted", None)       # the founder may have added it (Optional secrets)
        if entry is not None:
            doc["models"]["big-hosted"] = entry
        write_json(self.root / "lab" / "models.json", doc)
        return load_manifest(self.root / "lab" / "models.json")

    def test_the_templates_dir(self) -> None:
        self.assertEqual(sorted(p.name for p in TEMPLATES.iterdir()), TEMPLATE_NAMES)

    def test_purposes_name_their_copy_target(self) -> None:
        for name in TEMPLATE_NAMES:
            purpose = json.loads((TEMPLATES / name).read_text(encoding="utf-8"))["purpose"]
            with self.subTest(template=name):
                self.assertIn(f"lab/requests/{name.removesuffix('.json')}-001.json", purpose)
                self.assertNotIn("<", purpose)
                self.assertLessEqual(len(purpose), 500)

    def test_check_smoke_and_main_validate_where_copied(self) -> None:
        for name in ("check.json", "smoke.json", "main.json"):
            with self.subTest(template=name):
                loaded = load_request(_copy(self.root, name), self.manifest, strict_location=True, root=self.root)
                self.assertEqual(loaded.name, f"{name.removesuffix('.json')}-001")

    def test_the_smoke_run_sizes_every_model_the_main_run_sizes(self) -> None:
        """The main request's sim, G0 and E1 minutes are set from each model's own smoke numbers: every gguf model of
        those blocks has a sim unit in the smoke run (its sizing row), the smoke's G0 counts G0's calls, and the main
        G0 keeps the smoke's records and seed, for which those counts hold."""
        smoke = json.loads((TEMPLATES / "smoke.json").read_text(encoding="utf-8"))
        main = json.loads((TEMPLATES / "main.json").read_text(encoding="utf-8"))

        def block_models(doc: dict[str, Any], name: str) -> set[str]:
            block = doc["experiments"].get(name)
            if block is None:
                return set()
            return {k for k in block.get("models", doc["models"]) if self.manifest.models[k]["kind"] == "gguf"}

        sized = block_models(main, "sim") | block_models(main, "g0") | block_models(main, "e1")
        self.assertEqual(sized, {"a-0p5b", "a-1p5b", "a-4b", "b-2b"})
        self.assertLessEqual(sized, block_models(smoke, "sim"))
        self.assertTrue(block_models(smoke, "g0"))
        self.assertEqual({k: smoke["experiments"]["g0"][k] for k in ("records", "seed", "pack")},
                         {k: main["experiments"]["g0"][k] for k in ("records", "seed", "pack")})
        self.assertEqual({k: smoke["experiments"]["sim"][k] for k in ("plant", "weeks", "top_n", "seeds")},
                         {k: main["experiments"]["sim"][k] for k in ("plant", "weeks", "top_n", "seeds")})
        self.assertIn("largest", main["purpose"])

    def test_a_bad_copy_name_is_refused(self) -> None:
        with self.assertRaises(RequestError) as caught:
            load_request(_copy(self.root, "main.json", "Main_001.json"), self.manifest, strict_location=True,
                         root=self.root)
        self.assertEqual((caught.exception.path, caught.exception.problem), ("request", "bad request name"))
        # copied without renaming, the template's own name is a valid request name and runs
        load_request(_copy(self.root, "main.json", "main.json"), self.manifest, strict_location=True, root=self.root)

    def test_hosted_comparison(self) -> None:
        path = _copy(self.root, "hosted-comparison.json")
        entry = {"kind": "hosted", "model": "lab-hosted-test", "context_tokens": 32768}
        loaded = load_request(path, self.hosted_manifest(entry), strict_location=True, root=self.root)
        self.assertEqual(loaded.data["experiments"]["e2"]["central"], "big-hosted")
        with_secrets = build_plan(loaded, self.hosted_manifest(entry), "unknown", has_hosted=True)
        self.assertEqual(len(with_secrets["shards"]), 4)
        self.assertLessEqual(with_secrets["hosted"]["big-hosted"]["bound"],
                             loaded.data["hosted"]["big-hosted"]["max_calls"])
        without = build_plan(loaded, self.hosted_manifest(entry), "unknown", has_hosted=False)
        self.assertEqual((len(without["units"]), len(without["skipped"]), without["shards"]), (0, 10, []))
        with self.assertRaises(RequestError) as caught:
            load_request(path, self.hosted_manifest({"kind": "hosted", "model": "lab-hosted-test"}),
                         strict_location=True, root=self.root)
        self.assertEqual((caught.exception.path, caught.exception.problem),
                         ("$.experiments.e2.central", HOSTED_CENTRAL_CONTEXT))
        with self.assertRaises(RequestError) as caught:
            load_request(path, self.hosted_manifest(None), strict_location=True, root=self.root)
        self.assertEqual(caught.exception.path, "$.models[2]")

    def test_openfda_replay(self) -> None:
        path = _copy(self.root, "openfda-replay.json")
        with self.assertRaises(RequestError) as caught:
            load_request(path, self.manifest, strict_location=True, root=self.root)
        self.assertEqual(caught.exception.problem, notes.PLACEHOLDER)
        doc = json.loads(path.read_text(encoding="utf-8"))
        block = doc["experiments"]["openfda"]
        block.update(product_codes=["AAA", "BBB", "CCC"], date_from="20240101", date_to="20241229",
                     manufacturers=["ACME Devices"], partition_field="event_location", saw_recall_outcomes="no")
        self.assertNotRegex(json.dumps(doc), r"<[^<>]*>")
        path.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
        loaded = load_request(path, self.manifest, strict_location=True, root=self.root)
        plan = build_plan(loaded, self.manifest, "unknown")
        self.assertEqual([(s["kind"], s["units"]) for s in plan["shards"]], [("none", ["openfda"])])
        self.assertEqual(plan["provision"], [])


class MainPlanTests(unittest.TestCase):
    """The main template, copied, planned by the CLI, summarised and preregistered (slow: once)."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.root = _checkout()
        path = _copy(cls.root, "main.json")
        cls.out = cls.root / "plan"
        cls.code, cls.stdout, cls.stderr = run_plan(["--request", str(path), "--manifest", str(LAB_MANIFEST),
                                                     "--out", str(cls.out)])
        cls.plan = json.loads((cls.out / "plan.json").read_text(encoding="utf-8")) if cls.code == 0 else None
        cls.prereg = run_prereg(cls.out / "plan.json") if cls.code == 0 else None

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.root, True)

    def setUp(self) -> None:
        self.assertEqual(self.code, 0, self.stderr)

    def test_plan(self) -> None:
        plan = self.plan
        self.assertLessEqual(plan["max_parallel"], 16)
        self.assertTrue(1 <= len(plan["shards"]) <= 20, len(plan["shards"]))
        self.assertEqual(plan["skipped"], [])
        sims = [u for u in plan["units"] if u["experiment"] == "sim"]
        self.assertTrue(sims)
        self.assertEqual({u["params"]["plant"] for u in sims}, {"sim_small"})
        self.assertEqual(plan["result_class"], "real")

    def test_summary_sources_every_planned_minutes(self) -> None:
        from lab import summary
        md, sources = summary.render_plan(self.out)
        for i, shard_ in enumerate(self.plan["shards"]):
            with self.subTest(shard=shard_["shard"]):
                self.assertIn({"text": str(shard_["planned_minutes"]), "file": "plan.json",
                               "pointer": f"/shards/{i}/planned_minutes", "style": "int"}, sources)

    def test_prereg(self) -> None:
        code, out, err = self.prereg
        self.assertEqual(code, 0, err)
        self.assertIn("prereg: e1 yes x1 yes e2 yes", out.splitlines())


class RequestsReadmeTests(unittest.TestCase):
    """``lab/requests/README.md`` triggers nothing: it is outside the workflow's ``lab/requests/*.json`` filter, and
    the plan's discovery applies the same rule."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="lab-docs-git-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.world = GitWorld(self.tmp)
        self.request = (ROOT / "lab" / "requests" / "plumbing-001.json").read_text(encoding="utf-8")
        self.readme = _text(REQUESTS_README)

    def test_readme_with_a_request_runs_the_request(self) -> None:
        w = self.world
        w.git("checkout", "-q", "-b", "lab")
        after = w.commit({"lab/requests/README.md": self.readme, "lab/requests/plumbing-001.json": self.request})
        w.push("lab")
        found = discover.discover(w.push_event(before="0" * 40, after=after, branch="lab"), "push", w.clone(after))
        self.assertEqual((found.outcome, found.request, found.exit_code), ("run", "lab/requests/plumbing-001.json", 0))

    def test_a_readme_only_push_changes_no_request(self) -> None:
        w = self.world
        w.git("checkout", "-q", "-b", "lab")
        before = w.commit({"lab/requests/README.md": self.readme, "lab/requests/plumbing-001.json": self.request})
        w.push("lab")
        after = w.commit({"lab/requests/README.md": self.readme + "\nOne more line.\n"})
        w.push("lab")
        found = discover.discover(w.push_event(before=before, after=after, branch="lab"), "push", w.clone(after))
        self.assertEqual((found.outcome, found.exit_code), ("error", 2))
        self.assertEqual(found.errors[0], f"event: {notes.NO_REQUEST_CHANGE}")


if __name__ == "__main__":
    unittest.main()
