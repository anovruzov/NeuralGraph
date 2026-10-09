"""Repository guards for the lab.

* no model-family name in lab code, docs, tests, test stubs or the lab workflows (model names belong only in the
  manifest, the request and template files, kept results and ``docs/lab/MODELS.md``);
* no lab Python reads kept results (``lab/results/``);
* no server program, release tag or asset literal in lab Python (they come from the manifest), no hub host and no
  concrete release download URL, and the GitHub API host exactly once (the provision step's constant);
* lab Python never passes the harnesses' dirty-tree override, and every harness argv is ``--flag=value`` only;
* every fixed sentence in ``lab/notes.py`` is free of ASCII digits;
* lab Python imports only the standard library, ``mycelic`` and ``lab`` (no YAML, no HTTP client, nothing else).
"""
from __future__ import annotations

import ast
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path

from lab import notes
from lab.units import build_argv
from tests.lab.helpers import MANIFEST_TEST, ROOT, make_plan, plumbing_min, sim_block
from tests.mycelic.test_collective_guards import model_name_hits

LAB = ROOT / "lab"
DOCS = ROOT / "docs" / "lab"
WORKFLOW = ROOT / ".github" / "workflows" / "mycelic-lab.yml"
REAGGREGATE_WORKFLOW = ROOT / ".github" / "workflows" / "lab-reaggregate.yml"
NAME_SCAN_EXCLUDED = ("lab/models.json", "lab/models.lock.json", "docs/lab/MODELS.md")
DIRTY_OVERRIDE = "--allow-" + "dirty"


def lab_python() -> list[Path]:
    return sorted(p for p in LAB.rglob("*.py") if "__pycache__" not in p.parts)


def name_scan_excluded(rel: str) -> bool:
    """The manifest, the lock, request and template files, results and the models page: the places model names may
    appear."""
    parts = rel.split("/")
    in_json_dir = len(parts) == 3 and parts[0] == "lab" and parts[1] in ("requests", "templates")
    return (rel in NAME_SCAN_EXCLUDED or (in_json_dir and rel.endswith(".json"))
            or rel.startswith("lab/results/"))


def name_scan_files() -> list[Path]:
    """Every file of ``lab/``, ``tests/lab/`` and ``docs/lab/`` and the lab workflows, less the exclusions."""
    files = []
    for base in (LAB, ROOT / "tests" / "lab", DOCS):
        for path in sorted(base.rglob("*")):
            if not path.is_file() or "__pycache__" in path.parts or path.suffix == ".pyc":
                continue
            if not name_scan_excluded(path.relative_to(ROOT).as_posix()):
                files.append(path)
    return [*files, WORKFLOW, REAGGREGATE_WORKFLOW]


class LabGuardTests(unittest.TestCase):
    def test_exclusions(self) -> None:
        for rel in ("lab/models.json", "lab/models.lock.json", "lab/requests/x.json", "lab/templates/y.json",
                    "lab/results/a/b.md", "docs/lab/MODELS.md"):
            self.assertTrue(name_scan_excluded(rel), rel)
        for rel in ("lab/units.py", "lab/requests/sub/x.json", "lab/README.md", "tests/lab/data/manifest-test.json",
                    "docs/lab/README.md", "docs/lab/REFERENCE.md", "docs/lab/INTEGRATION.md", "lab/requests/README.md",
                    ".github/workflows/mycelic-lab.yml", ".github/workflows/lab-reaggregate.yml"):
            self.assertFalse(name_scan_excluded(rel), rel)

    def test_scan_scope(self) -> None:
        files = name_scan_files()
        for rel in ("docs/lab/README.md", "docs/lab/REFERENCE.md", "docs/lab/INTEGRATION.md", "lab/requests/README.md",
                    ".github/workflows/mycelic-lab.yml", ".github/workflows/lab-reaggregate.yml"):
            self.assertIn(ROOT / rel, files, rel)
        self.assertNotIn(ROOT / "docs" / "lab" / "MODELS.md", files)
        self.assertTrue((ROOT / "docs" / "lab" / "MODELS.md").is_file())

    def test_no_lab_python_reads_results(self) -> None:
        for path in lab_python():
            with self.subTest(path=path.name):
                self.assertNotIn("lab/results", path.read_text(encoding="utf-8"))

    def test_no_model_names(self) -> None:
        files = name_scan_files()
        self.assertTrue(files)
        self.assertIn(LAB / "units.py", files)
        self.assertIn(MANIFEST_TEST, files)
        for path in files:
            with self.subTest(path=path.relative_to(ROOT).as_posix()):
                self.assertEqual(model_name_hits(path.read_text(encoding="utf-8")), [])

    def test_no_server_literals_in_lab_python(self) -> None:
        literals = []
        for manifest in (MANIFEST_TEST, LAB / "models.json"):
            server = json.loads(manifest.read_text(encoding="utf-8"))["server"]
            if server is not None:
                literals += [server["program"], server["tag"], server["asset"]]
        self.assertTrue(literals)
        for path in lab_python():
            text = path.read_text(encoding="utf-8")
            for literal in literals:
                with self.subTest(path=path.name, literal=literal):
                    self.assertNotIn(literal, text)

    def test_no_download_urls_in_lab_python(self) -> None:
        release_url = re.compile(r"https://github\.com/[A-Za-z0-9._-]+/[A-Za-z0-9._-]+/releases/download/")
        hub_host = re.compile(r"huggingface\.co|(?<![A-Za-z0-9_.])hf\.co(?![A-Za-z0-9_])")
        placeholder = "https://github.com/<owner>/<repo>/releases/download/"
        self.assertIsNone(release_url.search(placeholder))
        self.assertIsNotNone(release_url.search("x https://github.com/a-b/c.d/releases/download/t/f"))
        self.assertIsNotNone(hub_host.search("https://hf.co/x"))
        self.assertIsNone(hub_host.search("self.config"))
        for path in lab_python():
            text = path.read_text(encoding="utf-8")
            with self.subTest(path=path.name):
                self.assertIsNone(release_url.search(text))
                self.assertIsNone(hub_host.search(text))

    def test_api_host_constant_once(self) -> None:
        host = "api." + "github.com"
        hits = [(path.name, path.read_text(encoding="utf-8").count(host)) for path in lab_python()]
        self.assertEqual([(name, n) for name, n in hits if n], [("provision.py", 1)])
        self.assertIn(f'API_HOST = "{host}"', (LAB / "provision.py").read_text(encoding="utf-8"))
        built = [(path.name, path.read_text(encoding="utf-8").count('"Authorization"')) for path in lab_python()]
        self.assertEqual([(name, n) for name, n in built if n], [("provision.py", 1)])

    def test_stubs_in_name_scan(self) -> None:
        files = name_scan_files()
        stubs = sorted(p.name for p in files if p.parent == ROOT / "tests" / "lab" / "stubs")
        self.assertEqual(stubs, ["__init__.py", "fake_server_stub.py", "hosted_stub.py", "http_stub.py",
                                 "openfda_stub.py"])

    def test_no_dirty_override(self) -> None:
        for path in lab_python():
            self.assertNotIn(DIRTY_OVERRIDE, path.read_text(encoding="utf-8"), path.name)

    def test_build_argv_is_flag_equals_value_only(self) -> None:
        request = plumbing_min()
        request["experiments"]["sim"] = sim_block(models=["fake-a"])
        with tempfile.TemporaryDirectory(prefix="lab-guard-") as tmp:
            plan, _ = make_plan(Path(tmp), request)
        self.assertEqual({u["experiment"] for u in plan["units"]}, {"e3", "g0", "sim"})
        modules = {"mycelic.collective.experiments.e3_latency", "mycelic.collective.experiments.g0_canary", "lab.sim"}
        for unit in plan["units"]:
            budget = 870 if unit["experiment"] == "sim" else None
            argv = build_argv(unit, Path("/out"), Path("/out/routing/x.json"), budget_s=budget)
            self.assertEqual(argv[:2], [sys.executable, "-m"])
            self.assertIn(argv[2], modules)
            for arg in argv[3:]:
                self.assertRegex(arg, r"\A--[a-z][a-z0-9-]*=.*\Z")
            self.assertNotIn(DIRTY_OVERRIDE, " ".join(argv))

    def test_notes_hold_no_digits(self) -> None:
        values = []
        for name in dir(notes):
            value = getattr(notes, name)
            if name.isupper() and isinstance(value, str):
                values.append((name, value))
            elif name.isupper() and isinstance(value, dict):
                values += [(f"{name}.{k}", v) for k, v in value.items()]
        self.assertGreater(len(values), 20)
        for name, value in values:
            with self.subTest(name=name):
                self.assertIsNone(re.search(r"[0-9]", value))

    def test_imports_are_stdlib_mycelic_or_lab(self) -> None:
        allowed = set(sys.stdlib_module_names) | {"__future__", "mycelic", "lab"}
        for path in lab_python():
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    names = [] if node.level else [node.module or ""]
                else:
                    continue
                for name in names:
                    with self.subTest(path=path.name, module=name):
                        self.assertIn(name.split(".")[0], allowed)


if __name__ == "__main__":
    unittest.main()
