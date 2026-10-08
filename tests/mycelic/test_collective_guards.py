"""Repository guards for the collective layer. Later gates extend the lists at the top of this module.

* ImportGuardTests: the fabric core imports no model client and nothing from ``mycelic.collective`` (static AST
  check, including ``importlib.import_module``/``__import__`` string constants and names a package re-exports, each
  resolved to the module that defines it; plus fresh-interpreter checks: no core global is defined in a forbidden
  module, and the one known transitive load of a model-client module follows exactly its documented chain).
* StdlibOnlyTests: every collective module and CLI runs under ``python -S`` (no site-packages).
* NoModelNamesTests: no model-family name in collective code, docs or tests (the matcher holds digests only).
* DeterminismTests: no wall clock or unseeded randomness in the modules that must replay bit for bit.
* RunbookCommandTests: every command in ``docs/collective/RUNBOOK.md`` runs with ``--dry-run`` appended, with the
  network blocked, and creates nothing.
* DomainLiteralTests: the generic collective code holds no domain-pack literal of any built-in pack (G2; every
  built-in pack since B4).
* LoopCoverageTests: every generalised per-pack collection and table of the collective tests covers exactly the
  built-in packs (:data:`BUILTIN_PACKS`), and every loop over a literal list of built-in packs is allow-listed
  with a reason (B4).
* HqImportGuardTests: the HQ detection modules (``mycelic/collective/detect``) and, since G6, the pushdown modules
  (``mycelic/collective/pushdown``) import nothing site-side (the site verifier included), no harness and no model
  client (static check and a fresh interpreter) (G4, G6).
* ClockEntropyTests: every ``detect`` and ``pushdown`` module is on the determinism list and reads no clock or entropy
  (G4, G6).
* EvaluateImportGuardTests: no ``evaluate`` module and no openFDA replay imports an inference module or a model client
  (static check) (G5).
* FollowupImportGuardTests: no ``followup`` module imports a site-side module, the packet assembler, the world
  generator, a harness or a model client; only ``followup/drafts.py`` imports inference (``tasks`` and ``errors``);
  ``edge/packets.py`` imports no ``followup``, ``pushdown``, ``detect`` or inference module (static check and a fresh
  interpreter) (G7).
* ApprovalCallSiteTests: outside the allow-list (``followup/service.py``, the G0 runner's simulated owner, the demo
  console and ``tests/``) nothing under ``mycelic/`` or ``demo/`` calls ``approve``, ``edit`` or ``reject`` (G7).
* X5AttacksImportGuardTests: the X5 attacks (``experiments/x5_attacks.py``) are pure: no file, store, network or
  process module, nothing site-side, no harness, leakage scan, run file or world generator, and no model client;
  the pack type only under ``TYPE_CHECKING`` (static check and a fresh interpreter) (B3).

``forbidden_imports``, ``model_name_hits``, ``nondeterminism`` and ``domain_literal_hits`` are importable for
reviewers' probes.
"""
from __future__ import annotations

import ast
import codecs
import hashlib
import importlib
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[2]

# --------------------------------------------------------------------------------------------------- lists

CORE_FILES = tuple(ROOT / "mycelic" / f"{name}.py"
                   for name in ("service", "aggregation", "store", "transport", "lineage",
                                "verification", "integrity"))
FORBIDDEN_IMPORTS = ("mycelic.collective", "openai", "anthropic", "ollama", "llama_cpp", "vllm", "transformers",
                     "torch", "NeuralGraph.llm_backend", "NeuralGraph.chat_memory.llm")
# importing mycelic.service loads NeuralGraph.chat_memory (and with it chat_memory.llm) through mycelic.retrieval,
# whose ``from NeuralGraph.chat_memory.textutil import tokenize`` runs the package's __init__ (an integration note for
# the fabric, INTEGRATION.md); the fresh-interpreter check allows that one module to be loaded only along exactly
# this chain (KNOWN_CHAIN: module -> the only package or module that may import it), and checks that no core global
# comes from it
FORBIDDEN_LOADED = tuple(p for p in FORBIDDEN_IMPORTS if p != "NeuralGraph.chat_memory.llm")
KNOWN_CHAIN = {"NeuralGraph.chat_memory.llm": "NeuralGraph.chat_memory",
               "NeuralGraph.chat_memory": "mycelic.retrieval"}
STDLIB_ONLY_MODULES = (
    "mycelic.collective",
    "mycelic.collective.jsonio",
    "mycelic.collective.schemacheck",
    "mycelic.collective.stats",
    "mycelic.collective.inference",
    "mycelic.collective.inference.errors",
    "mycelic.collective.inference.routing",
    "mycelic.collective.inference.runtime",
    "mycelic.collective.inference.client",
    "mycelic.collective.inference.jsonparse",
    "mycelic.collective.inference.tasks",
    "mycelic.collective.inference.ledger",
    "mycelic.collective.inference.fake",
    "mycelic.collective.inference.fakeserver",
    "mycelic.collective.connectors",
    "mycelic.collective.connectors.openfda",
    "mycelic.collective.experiments",
    "mycelic.collective.experiments.common",
    "mycelic.collective.experiments.e3_latency",
    "mycelic.collective.experiments.n1_narratives",
    "mycelic.collective.packs",
    "mycelic.collective.packs.loader",
    "mycelic.collective.packs.canonical",
    "mycelic.collective.packs.connector",
    "mycelic.collective.packs.generator",
    "mycelic.collective.edge",
    "mycelic.collective.edge.extract",
    "mycelic.collective.experiments.e1_extract",
    "mycelic.collective.edge.weeks",
    "mycelic.collective.edge.records",
    "mycelic.collective.edge.egress",
    "mycelic.collective.edge.site",
    "mycelic.collective.leakage",
    "mycelic.collective.experiments.g0_canary",
    "mycelic.collective.detect",
    "mycelic.collective.detect.org",
    "mycelic.collective.detect.store",
    "mycelic.collective.detect.detectors",
    "mycelic.collective.detect.rules",
    "mycelic.collective.evaluate",
    "mycelic.collective.evaluate.plant",
    "mycelic.collective.evaluate.baselines",
    "mycelic.collective.evaluate.harness",
    "mycelic.collective.experiments.openfda_replay",
    "mycelic.collective.pushdown",
    "mycelic.collective.pushdown.questions",
    "mycelic.collective.pushdown.gate",
    "mycelic.collective.pushdown.orchestrator",
    "mycelic.collective.edge.verify",
    "mycelic.collective.experiments.e2_pushdown",
    "mycelic.collective.followup",
    "mycelic.collective.followup.policy",
    "mycelic.collective.followup.ledger",
    "mycelic.collective.followup.service",
    "mycelic.collective.followup.drafts",
    "mycelic.collective.followup.executors",
    "mycelic.collective.followup.outcome",
    "mycelic.collective.edge.packets",
    "mycelic.collective.experiments.e5_injection",
    "mycelic.collective.runfiles",
    "mycelic.collective.experiments.x5_attacks",
    "mycelic.collective.experiments.x5_inference",
)
CLI_MODULES = (
    ("mycelic.collective.experiments.e3_latency",),
    ("mycelic.collective.connectors.openfda", "fetch"),
    ("mycelic.collective.experiments.n1_narratives", "sample"),
    ("mycelic.collective.experiments.n1_narratives", "score"),
    ("mycelic.collective.experiments.e1_extract", "prepare"),
    ("mycelic.collective.experiments.e1_extract", "label-check"),
    ("mycelic.collective.experiments.e1_extract", "prereg"),
    ("mycelic.collective.experiments.e1_extract", "run"),
    ("mycelic.collective.experiments.e1_extract", "compare"),
    ("mycelic.collective.packs.loader", "check"),
    ("mycelic.collective.experiments.g0_canary",),
    ("mycelic.collective.evaluate.harness", "prereg"),
    ("mycelic.collective.evaluate.harness", "check-plant"),
    ("mycelic.collective.evaluate.harness", "run"),
    ("mycelic.collective.experiments.openfda_replay", "prereg"),
    ("mycelic.collective.experiments.openfda_replay", "signals"),
    ("mycelic.collective.experiments.openfda_replay", "score"),
    ("mycelic.collective.experiments.e2_pushdown", "run"),
    ("mycelic.collective.experiments.e5_injection",),
    ("mycelic.collective.followup.ledger", "verify"),
    ("mycelic.collective.experiments.x5_inference", "prereg"),
    ("mycelic.collective.experiments.x5_inference", "run"),
)
NAME_SCAN_ROOTS = ("mycelic/collective", "docs/collective", "demo/collective", "tests/mycelic/test_collective_*.py",
                   "runs/.gitignore")
NAME_SCAN_EXCLUDED = ("docs/collective/examples",)
DETERMINISTIC_MODULES = (
    "mycelic/collective/jsonio.py",
    "mycelic/collective/schemacheck.py",
    "mycelic/collective/stats.py",
    "mycelic/collective/inference/errors.py",
    "mycelic/collective/inference/routing.py",
    "mycelic/collective/inference/tasks.py",
    "mycelic/collective/inference/jsonparse.py",
    "mycelic/collective/inference/ledger.py",
    "mycelic/collective/inference/fake.py",
    "mycelic/collective/inference/runtime.py",
    "mycelic/collective/packs/loader.py",
    "mycelic/collective/packs/canonical.py",
    "mycelic/collective/packs/connector.py",
    "mycelic/collective/packs/generator.py",
    "mycelic/collective/edge/extract.py",
    "mycelic/collective/edge/weeks.py",
    "mycelic/collective/edge/records.py",
    "mycelic/collective/edge/egress.py",
    "mycelic/collective/edge/site.py",
    "mycelic/collective/leakage.py",
    "mycelic/collective/detect/__init__.py",
    "mycelic/collective/detect/org.py",
    "mycelic/collective/detect/store.py",
    "mycelic/collective/detect/detectors.py",
    "mycelic/collective/detect/rules.py",
    "mycelic/collective/evaluate/__init__.py",
    "mycelic/collective/evaluate/plant.py",
    "mycelic/collective/evaluate/baselines.py",
    "mycelic/collective/evaluate/harness.py",
    "mycelic/collective/experiments/openfda_replay.py",
    "mycelic/collective/pushdown/__init__.py",
    "mycelic/collective/pushdown/questions.py",
    "mycelic/collective/pushdown/orchestrator.py",
    "mycelic/collective/pushdown/gate.py",
    "mycelic/collective/edge/verify.py",
    "mycelic/collective/experiments/e2_pushdown.py",
    "mycelic/collective/followup/__init__.py",
    "mycelic/collective/followup/policy.py",
    "mycelic/collective/followup/ledger.py",
    "mycelic/collective/followup/service.py",
    "mycelic/collective/followup/drafts.py",
    "mycelic/collective/followup/executors.py",
    "mycelic/collective/followup/outcome.py",
    "mycelic/collective/edge/packets.py",
    "mycelic/collective/runfiles.py",
    "mycelic/collective/experiments/x5_attacks.py",
    "mycelic/collective/experiments/x5_inference.py",
    "demo/collective/scenario.py",
    "demo/collective/screen.py",
    "demo/collective/lint_numbers.py",
)
DETECT_DIR = ROOT / "mycelic" / "collective" / "detect"
PUSHDOWN_DIR = ROOT / "mycelic" / "collective" / "pushdown"
# what the HQ side (detect, and pushdown since G6) may never import: the site's raw-record modules, the extractor, the
# site verifier, the world generator, the harness and evaluation modules, any inference module (egress pulls in only
# inference.errors), and every model client
HQ_FORBIDDEN = ("mycelic.collective.edge.records", "mycelic.collective.edge.site", "mycelic.collective.edge.extract",
                "mycelic.collective.edge.verify",
                "mycelic.collective.packs.generator", "mycelic.collective.evaluate", "mycelic.collective.leakage",
                "mycelic.collective.experiments", "mycelic.collective.inference", "openai", "anthropic", "ollama",
                "llama_cpp", "vllm", "transformers", "torch")
HQ_ALLOWED_LOADED = ("mycelic.collective.inference", "mycelic.collective.inference.errors")
RUNBOOK = ROOT / "docs" / "collective" / "RUNBOOK.md"
RUNBOOK_PREFIXES = ("python -m mycelic.collective.", "python demo/collective/")
RUNBOOK_PLACEHOLDERS = {
    "routing-file": "{tmp}/missing/routing.json",
    "endpoint-name": "site-a-server",
    "run-id": "runbook-dry-run",
    "site-id": "a",
    "cache-dir": "{tmp}/missing/openfda-cache",
    "product-codes": "AAA,BBB,CCC",
    "date-from": "20240101",
    "date-to": "20240331",
    "seed": "1",
    "sample-file": "{tmp}/missing/sample.json",
    "labelled-sheet": "{tmp}/missing/sheet-labelled.csv",
    "out-file": "{tmp}/missing/narrative_gain.json",
    "server-note": "dry run",
    "powercap-path": "{tmp}/missing/energy_uj",
    "model-tag": "example-tag",
    "pack": "device_quality",
    "pack-dir": "{tmp}/missing/pack",
    "records-file": "{tmp}/missing/records.jsonl",
    "labels-file": "{tmp}/missing/labels.jsonl",
    "prereg-file": "{tmp}/missing/prereg.json",
    "partner-file": "{tmp}/missing/partner.jsonl",
    "reference-endpoint": "frontier-ref",
    "run-dirs": "{tmp}/missing/run-1",
    "seeds": "1,2",
    "tie-salt": "runbook",
    "detector-author": "detector-author",
    "plant-file": "{tmp}/missing/plant.json",
    "events-cache": "{tmp}/missing/events-cache",
    "recalls-cache": "{tmp}/missing/recalls-cache",
    "signals-dir": "{tmp}/missing/signals",
    "manufacturer": "ACME",
    "manufacturer-field": "device[].manufacturer_d_name",
    "partition-field": "event_location",
    "site-routing-dir": "{tmp}/missing/site-routing",
    "central-routing-file": "{tmp}/missing/central_routing.json",
    "context-tokens": "8192",
    "ledger-file": "{tmp}/missing/followups.sqlite3",
    "head-hash": "0" * 64,
    "entity-type": "supplier",
    "injected-id": "V9999",
    "page-file": "{tmp}/missing/page.html",
    "recorded-dir": "{tmp}/missing/recorded",
}
EVALUATE_DIR = ROOT / "mycelic" / "collective" / "evaluate"
EVALUATE_FILES = (*sorted(EVALUATE_DIR.glob("*.py")),
                  ROOT / "mycelic" / "collective" / "experiments" / "openfda_replay.py")
# what G5's evaluation may never import: any inference module (edge.site pulls in inference.ledger transitively, which
# this static check of the modules' own imports allows) and every model client
EVALUATE_FORBIDDEN = ("mycelic.collective.inference", "openai", "anthropic", "ollama", "llama_cpp", "vllm",
                      "transformers", "torch")

# --------------------------------------------------------------------------------------------------- import guard

_DYNAMIC_NAMES = {"__import__", "import_module"}


def _forbidden(name: str, prefixes: tuple[str, ...] = FORBIDDEN_IMPORTS) -> bool:
    return any(name == p or name.startswith(p + ".") for p in prefixes)


def _resolve(module: str, level: int, name: str | None) -> str | None:
    """Absolute name of ``from <dots><name>`` inside ``module``, whose package is all but its last part."""
    if level == 0:
        return name
    package = module.split(".")[:-1]
    if level - 1 > len(package):
        return None
    base = package[:len(package) - (level - 1)]
    return ".".join(base + ([name] if name else [])) or None


def _import_quietly(name: str) -> types.ModuleType | None:
    try:
        return importlib.import_module(name)
    except Exception:                    # noqa: BLE001 - a name the checker cannot import is left to the other rules
        return None


def _member(module_name: str, attr: str) -> object | None:
    """``module_name.attr``: an attribute of the imported module, else its submodule, else None."""
    mod = _import_quietly(module_name)
    if mod is None:
        return None
    if hasattr(mod, attr):
        return getattr(mod, attr)
    return _import_quietly(f"{module_name}.{attr}")


def defined_in(obj: object) -> str | None:
    """The module that defines ``obj``: a module's own name, else its ``__module__`` (None when it has none)."""
    if isinstance(obj, types.ModuleType):
        return obj.__name__
    name = getattr(obj, "__module__", None)
    return name if isinstance(name, str) else None


def _dotted(node: ast.AST) -> list[str] | None:
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if not isinstance(node, ast.Name):
        return None
    return [node.id, *reversed(parts)]


def forbidden_imports(source: str, module: str, prefixes: tuple[str, ...] = FORBIDDEN_IMPORTS) -> list[str]:
    """Every import of a module under ``prefixes`` in ``source`` (the text of ``module``), and every unverifiable
    one. A name taken from a package that is not itself under ``prefixes`` (``from pkg import name``, or ``import pkg
    as m`` then ``m.name``) is resolved by importing the package here and is a hit when the module that defines the
    object is under ``prefixes``: a package that re-exports a model client does not hide it."""
    tree = ast.parse(source)
    dynamic = set(_DYNAMIC_NAMES)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "importlib":
            dynamic.update(a.asname or a.name for a in node.names if a.name in _DYNAMIC_NAMES)
    hits: list[str] = []
    bound: dict[str, str] = {}              # a local name bound to a module (by an import) -> that module's name
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            hits += [f"line {node.lineno}: import {a.name}" for a in node.names if _forbidden(a.name, prefixes)]
            for a in node.names:
                if a.asname is not None:
                    bound[a.asname] = a.name
                else:
                    bound[a.name.split(".")[0]] = a.name.split(".")[0]
        elif isinstance(node, ast.ImportFrom):
            base = _resolve(module, node.level, node.module)
            if base is None:
                hits.append(f"line {node.lineno}: unresolvable relative import")
                continue
            found = False
            for name in [base] + [f"{base}.{a.name}" for a in node.names]:
                if _forbidden(name, prefixes):
                    hits.append(f"line {node.lineno}: from-import of {name}")
                    found = True
                    break
            if found or base == "__future__":
                continue
            for a in node.names:
                obj = _member(base, a.name) if a.name != "*" else None
                origin = defined_in(obj) if obj is not None else None
                if origin is not None and _forbidden(origin, prefixes):
                    hits.append(f"line {node.lineno}: from-import of {base}.{a.name}, defined in {origin}")
                elif isinstance(obj, types.ModuleType):
                    bound[a.asname or a.name] = obj.__name__
        elif isinstance(node, ast.Call):
            func = node.func
            is_dynamic = ((isinstance(func, ast.Name) and func.id in dynamic)
                          or (isinstance(func, ast.Attribute) and func.attr in _DYNAMIC_NAMES))
            if not is_dynamic:
                continue
            arg = node.args[0] if node.args else None
            if not (isinstance(arg, ast.Constant) and isinstance(arg.value, str)):
                hits.append(f"line {node.lineno}: dynamic import with a non-constant name (unverifiable)")
                continue
            name = arg.value
            if name.startswith("."):
                keywords = [k.value for k in node.keywords if k.arg == "package"]
                pkg = node.args[1] if len(node.args) > 1 else (keywords[0] if keywords else None)
                if not (isinstance(pkg, ast.Constant) and isinstance(pkg.value, str)):
                    hits.append(f"line {node.lineno}: relative dynamic import without a constant package")
                    continue
                level = len(name) - len(name.lstrip("."))
                name = _resolve(pkg.value + ".__init__", level, name.lstrip(".") or None) or ""
            if _forbidden(name, prefixes):
                hits.append(f"line {node.lineno}: dynamic import of {name}")
    for node in ast.walk(tree):
        chain = _dotted(node) if isinstance(node, ast.Attribute) else None
        if chain is None or chain[0] not in bound:
            continue
        current = bound[chain[0]]
        for attr in chain[1:]:
            obj = _member(current, attr)
            origin = defined_in(obj) if obj is not None else None
            if origin is not None and _forbidden(origin, prefixes):
                hits.append(f"line {node.lineno}: attribute {'.'.join(chain)}, defined in {origin}")
                break
            if not isinstance(obj, types.ModuleType):
                break
            current = obj.__name__
    return sorted(set(hits), key=hits.index)


POSITIVE_IMPORTS = (
    "import openai",
    "import openai.types.chat",
    "from openai import OpenAI",
    "import vllm as engine",
    "import llama_cpp",
    "import torch.nn",
    "from transformers import AutoModel",
    "from anthropic.types import Message",
    "import ollama",
    "def lazy():\n    import anthropic\n",
    "try:\n    import ollama\nexcept ImportError:\n    ollama = None\n",
    "from mycelic.collective.inference import client",
    "import mycelic.collective",
    "from . import collective",
    "from .collective import inference",
    "from .collective.inference.runtime import Runtime",
    "from NeuralGraph.chat_memory import llm",
    "from NeuralGraph.chat_memory.llm import LLMClient",
    "import NeuralGraph.llm_backend",
    "from NeuralGraph import llm_backend",
    "import importlib\nimportlib.import_module('vllm')",
    "import importlib\nimportlib.import_module('mycelic.collective.inference.client')",
    "from importlib import import_module\nimport_module('torch.nn')",
    "from importlib import import_module as load\nload('openai')",
    "import importlib as il\nil.import_module('anthropic')",
    "__import__('transformers')",
    "import importlib\nimportlib.import_module('.collective', 'mycelic')",
    "import importlib\nname = 'x'\nimportlib.import_module(name)",
    "__import__(''.join(['open', 'ai']))",
    # a package that re-exports a model client (regression: these passed the guard)
    "from NeuralGraph.chat_memory import BackendLLMClient",
    "from NeuralGraph.chat_memory import BackendLLMClient as _ModelClient",
    "import NeuralGraph.chat_memory as _cm\n_ModelClient = _cm.BackendLLMClient",
    "import NeuralGraph.chat_memory\nclient = NeuralGraph.chat_memory.BackendLLMClient",
    "from NeuralGraph import chat_memory\nclient = chat_memory.BackendLLMClient",
)
NEGATIVE_IMPORTS = (
    "import json",
    "from .store import MycelicStore",
    "from .models import Memory",
    "from NeuralGraph.chat_memory.textutil import tokenize",
    "from NeuralGraph.chat_memory import ChatMessage",
    "import NeuralGraph.chat_memory as _cm\nmessage = _cm.ChatMessage",
    "from NeuralGraph.research.coordination.core import LineageAnalyzer",
    "import openaiish",
    "import torchvisionlike",
    "from mycelic import collective_notes",
    "from .collectives import x",
    "import importlib\nimportlib.import_module('json')",
    "__import__('os')",
    "from typing import TYPE_CHECKING",
    "torch = 1",
    "def import_hook(module):\n    return module\n",
)


class ImportGuardTests(unittest.TestCase):
    def test_core_files_exist(self) -> None:
        for path in CORE_FILES:
            with self.subTest(path=path.name):
                self.assertTrue(path.is_file(), f"{path} is missing: if the fabric merge renamed or split it, "
                                                "update CORE_FILES so the guard keeps covering the core")

    def test_core_files_import_no_model_client(self) -> None:
        for path in CORE_FILES:
            with self.subTest(path=path.name):
                self.assertEqual(forbidden_imports(path.read_text(encoding="utf-8"), f"mycelic.{path.stem}"), [])

    def test_checker_flags_every_positive_snippet(self) -> None:
        for snippet in POSITIVE_IMPORTS:
            with self.subTest(snippet=snippet):
                self.assertTrue(forbidden_imports(snippet, "mycelic.service"))

    def test_checker_passes_every_negative_snippet(self) -> None:
        for snippet in NEGATIVE_IMPORTS:
            with self.subTest(snippet=snippet):
                self.assertEqual(forbidden_imports(snippet, "mycelic.service"), [])

    def test_checker_flags_an_injected_import_in_a_copy_of_a_core_file(self) -> None:
        for name in ("lineage", "service"):
            source = (ROOT / "mycelic" / f"{name}.py").read_text(encoding="utf-8")
            for line in ("import openai", "import importlib\nimportlib.import_module('vllm')",
                         "from NeuralGraph.chat_memory import BackendLLMClient as _ModelClient",
                         "import NeuralGraph.chat_memory as _cm\n_ModelClient = _cm.BackendLLMClient"):
                with self.subTest(core=name, line=line):
                    self.assertTrue(forbidden_imports(source + "\n" + line + "\n", f"mycelic.{name}"))

    def loaded(self, modules: tuple[str, ...]) -> list[str]:
        code = ("import json, sys\n" + "".join(f"import {m}\n" for m in modules)
                + "print(json.dumps(sorted(sys.modules)))\n")
        r = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, timeout=120,
                           env={**os.environ, "PYTHONPATH": str(ROOT)})
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout.strip().splitlines()[-1])

    def test_fresh_interpreter_loads_no_model_client_with_the_core(self) -> None:
        loaded = self.loaded(tuple(f"mycelic.{p.stem}" for p in CORE_FILES))
        self.assertIn("mycelic.service", loaded)
        self.assertEqual([m for m in loaded if _forbidden(m, FORBIDDEN_LOADED)], [])
        if any(m.startswith("NeuralGraph.chat_memory") for m in loaded):
            self.assertIn("mycelic.retrieval", loaded, "NeuralGraph.chat_memory loaded without mycelic.retrieval")

    def core_report(self, modules: tuple[str, ...], extra: str = "") -> dict[str, object]:
        """A fresh interpreter imports ``modules`` (after ``extra``) and reports, per module of KNOWN_CHAIN, the
        modules that imported it, and per module the modules that define its globals."""
        code = "\n".join([
            "import json, sys, types",
            "watch = " + json.dumps(sorted(KNOWN_CHAIN)),
            "importers = {}",
            "class Recorder:",
            "    def find_spec(self, name, path=None, target=None):",
            "        if name in watch:",
            "            frame = sys._getframe(1)",
            "            while frame is not None and frame.f_code.co_filename.startswith('<frozen importlib'):",
            "                frame = frame.f_back",
            "            importers.setdefault(name, set()).add(frame.f_globals.get('__name__') if frame else None)",
            "        return None",
            "sys.meta_path.insert(0, Recorder())",
            extra,
            *[f"import {m}" for m in modules],
            "origins = {}",
            "for m in " + json.dumps(list(modules)) + ":",
            "    values = vars(sys.modules[m]).values()",
            "    names = {v.__name__ if isinstance(v, types.ModuleType) else getattr(v, '__module__', None)",
            "             for v in values}",
            "    origins[m] = sorted(n for n in names if isinstance(n, str))",
            "print(json.dumps({'importers': {k: sorted(map(str, v)) for k, v in importers.items()},",
            "                  'origins': origins}))"])
        r = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, timeout=120,
                           env={**os.environ, "PYTHONPATH": str(ROOT)})
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout.strip().splitlines()[-1])

    def test_no_core_global_is_defined_in_a_model_client_module(self) -> None:
        # regression: a core file could take the model client a package re-exports and every check stayed green
        modules = tuple(f"mycelic.{p.stem}" for p in CORE_FILES)
        report = self.core_report(modules)
        for m in modules:
            with self.subTest(module=m):
                self.assertEqual([o for o in report["origins"][m] if _forbidden(o)], [])
        # the same check catches a re-exported client however it was imported (here added to a core module's
        # globals the way a one-line import in service.py would add it)
        injected = self.core_report(("mycelic.service",), "import mycelic.service as _s\n"
                                    "from NeuralGraph.chat_memory import BackendLLMClient as _M\n_s._ModelClient = _M")
        self.assertEqual([o for o in injected["origins"]["mycelic.service"] if _forbidden(o)],
                         ["NeuralGraph.chat_memory.llm"])

    def test_the_known_model_client_load_follows_exactly_its_documented_chain(self) -> None:
        # chat_memory.llm is loaded with the core only because mycelic.retrieval imports a chat_memory submodule,
        # which runs the package's __init__; any other importer of either module fails here
        report = self.core_report(tuple(f"mycelic.{p.stem}" for p in CORE_FILES))
        self.assertEqual(sorted(report["importers"]), sorted(KNOWN_CHAIN))
        for module, importers in report["importers"].items():
            allowed = KNOWN_CHAIN[module]
            with self.subTest(module=module):
                self.assertTrue(importers)
                self.assertEqual([i for i in importers if not (i == allowed or i.startswith(allowed + "."))], [])
        # a core file importing the package itself is outside the chain
        injected = self.core_report(("mycelic.lineage",), "import NeuralGraph.chat_memory")
        self.assertEqual(injected["importers"]["NeuralGraph.chat_memory"], ["__main__"])

    def test_core_without_service_loads_no_chat_memory(self) -> None:
        loaded = self.loaded(("mycelic.store", "mycelic.transport", "mycelic.lineage", "mycelic.aggregation",
                              "mycelic.verification", "mycelic.integrity"))
        self.assertEqual([m for m in loaded if m.startswith("NeuralGraph.chat_memory")], [])
        self.assertEqual([m for m in loaded if _forbidden(m)], [])


# --------------------------------------------------------------------------------------------------- stdlib only

DEMO_SCRIPTS = ("demo/collective/collective_demo.py", "demo/collective/lint_numbers.py")


def _run_without_site_packages(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, "-S", *args], cwd=ROOT, capture_output=True, text=True, timeout=60,
                          env={"PYTHONPATH": str(ROOT), "PATH": "/usr/bin:/bin"})


class StdlibOnlyTests(unittest.TestCase):
    def test_every_collective_module_imports_without_site_packages(self) -> None:
        self.assertEqual(len(STDLIB_ONLY_MODULES), 62)
        code = "import importlib\n" + "".join(f"importlib.import_module({m!r})\n" for m in STDLIB_ONLY_MODULES)
        r = _run_without_site_packages("-c", code)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_every_cli_answers_help_without_site_packages(self) -> None:
        for module, *sub in CLI_MODULES:
            with self.subTest(module=module, sub=sub):
                r = _run_without_site_packages("-m", module, *sub, "--help")
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertIn("usage:", r.stdout)

    def test_demo_scripts_answer_help_without_site_packages(self) -> None:
        for script in DEMO_SCRIPTS:
            with self.subTest(script=script):
                r = _run_without_site_packages(script, "--help")
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertIn("usage:", r.stdout)
        code = ("from demo.collective import scenario, screen\nscenario.load_scenario()\n"
                "screen.build_screen(None, mode='record', phase='preparing', run_id='x')\n")
        r = _run_without_site_packages("-c", code)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_site_packages_are_really_absent_under_dash_s(self) -> None:
        r = _run_without_site_packages("-c", "import numpy")
        self.assertNotEqual(r.returncode, 0, "numpy importable without site-packages; the checks above prove nothing")


# --------------------------------------------------------------------------------------------------- model names

# sha256 of each model-family word (computed outside the repository); the words themselves never appear here
FAMILY_DIGESTS = frozenset({
    "053ea4804ef1bb33d4a3d6fb024a614b6d257cebc2bc7cd915da9c9522f37ffc",
    "13b841d12f141d27607b5f228c62494790e7074f3e81b7e1ef7ecc87b7c3b529",
    "1c33f1f4e5c5bec3255747e748adb07b48832d5fe0dedef5239df15255f6b2dc",
    "28d71c9c16c2427c3234b60f9381d3ef860d222463f08367ddb9ed81e5576443",
    "3fb22a5597fb91ee4f9abbf30ea69d318be150e0fcf3ca1db8ca334b520d2894",
    "5a5110ebe1544b31e6d4207a419bc1deb148735f89ae984ff0a2ab809b51f1df",
    "5d72436256ada53828b51895a94bb8489e9f1ac4fe937a8024ef1594e7045ff6",
    "67f2d22514622d1be30c14ee9f3cb104503a159a33a656ca91a1953aa9616429",
    "6f7ac1823da81d2e52d1a1549ee69c85bbf8bb56d06682849e7c09da2785ce3b",
    "742c86876526552edd894cfb4059233d7708f9976773c1503f11758e65f5592d",
    "751a16d8cb41ea83c7b7c76d8deaa859cb1d10760163b5f184be6a79ed913c85",
    "7f754d3dbef2f9c8be86a085d505d9bf943e8a9d0c2d84c5015e92e97c53040d",
    "920510199770f4d65cb8aaa2cd12bdb2b8c37f5b3907c50a734a0e3409da2823",
    "a84571394b5e99fe70aae39ece25f844acbaf83479e27f39a30732e092b19677",
    "a93236bfd01d62d26119600f3a77a5847d7c206fb88da21e38333b8ad5ba2262",
    "ac7daf28fd6bfc7a5c3e4b83c7fc9fd51f92ddff10bdc848f99417eca6fafc7c",
    "add92b9cde2bdbf3daaf65a0db79e9b1a7fa428b71b4d6ce38c742eb6dca0c1c",
    "c857d09db23e6822e3600bc06ad8d58f92ed62bc8efd81c753f77048662cb97d",
    "c97504235ab15009f6c5f11ba890007a5f3fdd20c9e57b53ea74855033572ddf",
    "c9ad8f2cc1294afa0ef22fc2c019ff7243cdd272b2147ddca3f34c5036b05768",
    "e12ce8285efc67c6d93d3a122e2589ed95089bcbb775ba5634d94e2b8385db07",
    "e4f9c522e1c89280e9561b825f4f24fe32b51ff82d61e5c2b1dfd0321c35a90b",
    "fc5a1047f5919892fcdf8aa79ea5d6bb6531b5c176939ef0110906cb225941c1",
    "ff5c7e22fe1e88c4551bcdaf54fe3c45a78f565267a6573c1777e6903d281e91",
})
# step 1 of the matcher removes these literal strings: server names, and the git ref that the port headers must cite
# (a branch name, not a model; it is the only ref exempted)
SERVER_NAMES = ("llama-server", "llama.cpp", "llama_cpp", "ollama")
PROVENANCE_REFS = tuple(codecs.decode(r, "rot13") for r in ("bevtva/pynhqr/zlpryvp-vzcyrzragngvba-ie034c",))
_REMOVED = re.compile("|".join(re.escape(s) for s in PROVENANCE_REFS + SERVER_NAMES), re.IGNORECASE)
_SPLIT = re.compile(r"[^0-9A-Za-z]+")
_LEADING_LETTERS = re.compile(r"[A-Za-z]+")

# real model tags, rot13-encoded so the words never appear in plain text
POSITIVE_TAGS_ROT13 = ("Djra3-8O", "djra2.5:7o-vafgehpg", "Yynzn-3.2-3O-Vafgehpg", "trzzn3:4o", "Cuv-4-zvav",
                       "Zvfgeny-7O-Vafgehpg-i0.3", "tcg-bff-20o", "pynhqr-bchf-4-5", "QrrcFrrx-E1-Qvfgvyy",
                       "tenavgr3.3:8o", "FzbyYZ3-3O", "BYZb-2-7O", "snypba3:7o", "ZvavPCZ4-8O")
NEGATIVE_TAGS = ("llama-server", "llama.cpp", "llama_cpp", "ollama", "ollama pull <model-tag>", "openai_compat",
                 "graphite", "philosophy", "opusculum", "LLAMA.CPP", "Ollama serve", "model-tag", "fake-model",
                 f"Ported from {PROVENANCE_REFS[0]}@388aa30, models/router.py")


def model_name_hits(text: str) -> list[str]:
    """Tokens whose leading letters (lower-cased) hash to a model-family digest, after removing server names."""
    hits = []
    for token in _SPLIT.split(_REMOVED.sub(" ", text)):
        m = _LEADING_LETTERS.match(token)
        if m and hashlib.sha256(m.group().lower().encode("ascii")).hexdigest() in FAMILY_DIGESTS:
            hits.append(token)
    return hits


def name_scan_files() -> list[Path]:
    files: set[Path] = set()
    for pattern in NAME_SCAN_ROOTS:
        for path in ROOT.glob(pattern):
            candidates = [path] if path.is_file() else [p for p in path.rglob("*") if p.is_file()]
            for p in candidates:
                rel = p.relative_to(ROOT).as_posix()
                if "__pycache__" in p.parts or p.suffix == ".pyc":
                    continue
                if any(rel == ex or rel.startswith(ex + "/") for ex in NAME_SCAN_EXCLUDED):
                    continue
                files.add(p)
    return sorted(files)


class NoModelNamesTests(unittest.TestCase):
    def test_matcher_flags_every_real_tag(self) -> None:
        for encoded in POSITIVE_TAGS_ROT13:
            tag = codecs.decode(encoded, "rot13")
            with self.subTest(index=POSITIVE_TAGS_ROT13.index(encoded)):
                self.assertTrue(model_name_hits(tag))
                self.assertEqual(model_name_hits(encoded), [])

    def test_matcher_passes_server_names_and_lookalikes(self) -> None:
        for text in NEGATIVE_TAGS:
            with self.subTest(text=text):
                self.assertEqual(model_name_hits(text), [])

    def test_provenance_exemption_is_exact(self) -> None:
        ref = PROVENANCE_REFS[0]
        family = ref.split("/")[1]
        self.assertEqual(model_name_hits(ref + "@388aa30"), [])
        self.assertTrue(model_name_hits(ref.replace("vr034p", "other")))
        self.assertTrue(model_name_hits(f"origin/{family}/elsewhere"))

    def test_repository_scan_has_no_hits(self) -> None:
        files = name_scan_files()
        self.assertIn(ROOT / "runs" / ".gitignore", files)
        self.assertIn(Path(__file__).resolve(), files)
        self.assertTrue(any(p.suffix == ".md" for p in files))
        hits = {p.relative_to(ROOT).as_posix(): model_name_hits(p.read_bytes().decode("utf-8", "replace"))
                for p in files}
        self.assertEqual({k: v for k, v in hits.items() if v}, {})


# --------------------------------------------------------------------------------------------------- determinism

_DENIED = frozenset({
    "datetime.datetime.now", "datetime.datetime.utcnow", "datetime.datetime.today", "datetime.date.today",
    "time.time", "time.time_ns", "uuid.uuid1", "uuid.uuid4", "os.urandom", "random.SystemRandom",
    *(f"random.{fn}" for fn in ("random", "randint", "randrange", "choice", "choices", "shuffle", "sample", "uniform",
                                "gauss", "getrandbits", "seed")),
})


def nondeterminism(source: str) -> list[tuple[int, str]]:
    """Wall-clock and unseeded-randomness uses: (line, dotted name). ``random.Random(seed)`` and timers pass."""
    tree = ast.parse(source)
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                aliases[a.asname or a.name.split(".")[0]] = a.name if a.asname else a.name.split(".")[0]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            for a in node.names:
                aliases[a.asname or a.name] = f"{node.module}.{a.name}"

    def dotted(expr: ast.AST) -> str | None:
        parts = []
        while isinstance(expr, ast.Attribute):
            parts.append(expr.attr)
            expr = expr.value
        if isinstance(expr, ast.Name) and expr.id in aliases:
            return ".".join([aliases[expr.id], *reversed(parts)])
        return None

    hits: set[tuple[int, str]] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and dotted(node.func) == "random.Random" and not node.args and not node.keywords:
            hits.add((node.lineno, "random.Random()"))
        if isinstance(node, (ast.Attribute, ast.Name)) and isinstance(node.ctx, ast.Load):
            name = dotted(node)
            if name is not None and (name in _DENIED or name.startswith("secrets.")):
                hits.add((node.lineno, name))
    return sorted(hits)


POSITIVE_NONDETERMINISM = (
    "import datetime\ndatetime.datetime.now()",
    "from datetime import datetime\ndatetime.utcnow()",
    "from datetime import datetime as dt\ndt.today()",
    "from datetime import date\ndate.today()",
    "import time\ntime.time()",
    "import time as t\nt.time_ns()",
    "import time\nclock = time.time",
    "import uuid\nuuid.uuid4()",
    "from uuid import uuid1\nuuid1()",
    "import os\nos.urandom(8)",
    "import secrets\nsecrets.token_hex(4)",
    "from secrets import choice\nchoice('ab')",
    *(f"import random\nrandom.{fn}(x)"
      for fn in ("random", "randint", "randrange", "choice", "choices", "shuffle", "sample", "uniform", "gauss",
                 "getrandbits", "seed")),
    "from random import shuffle as mix\nmix(items)",
    "import random\nrandom.Random()",
    "import random as r\nr.Random()",
    "import random\nrandom.SystemRandom()",
    "from random import SystemRandom\nSystemRandom().random()",
)
NEGATIVE_NONDETERMINISM = (
    "import random\nrandom.Random(7)",
    "import random\nrng = random.Random('n1:1:AAA')\nrng.shuffle(items)\nrng.sample(items, 2)",
    "import time\ntime.perf_counter()",
    "import time\ntime.monotonic()",
    "import time\ntime.sleep(1)",
    "import time\ndef f(sleep=time.sleep):\n    return sleep",
    "from time import sleep\nsleep(0)",
    "import datetime\ndatetime.timezone.utc",
    "def time():\n    return 0\ntime()",
)


class DeterminismTests(unittest.TestCase):
    def test_denylist_flags_every_positive_snippet(self) -> None:
        for snippet in POSITIVE_NONDETERMINISM:
            with self.subTest(snippet=snippet):
                self.assertTrue(nondeterminism(snippet))

    def test_denylist_passes_every_negative_snippet(self) -> None:
        for snippet in NEGATIVE_NONDETERMINISM:
            with self.subTest(snippet=snippet):
                self.assertEqual(nondeterminism(snippet), [])

    def test_deterministic_modules_have_no_hits(self) -> None:
        for rel in DETERMINISTIC_MODULES:
            with self.subTest(module=rel):
                self.assertEqual(nondeterminism((ROOT / rel).read_text(encoding="utf-8")), [])


HQ_POSITIVE_IMPORTS = (
    "from ..edge import records",
    "from ..edge.site import EdgeSite",
    "from ..edge.extract import sense",
    "from ..edge.verify import SiteVerifier",
    "from ..edge import verify",
    "from ..packs.generator import generate",
    "from ..packs import generator",
    "from .. import leakage",
    "from ..evaluate import scorecard",
    "from ..experiments.common import UsageError",
    "import mycelic.collective.edge.site",
    "import mycelic.collective.edge.records as rec",
    "from ..inference.runtime import Runtime",
    "from ..inference import errors",
    "from mycelic.collective.inference.errors import KINDS",
    "import importlib\nimportlib.import_module('mycelic.collective.leakage')",
    "import importlib\nimportlib.import_module('..edge.site', 'mycelic.collective.detect')",
    "import openai",
    "def lazy():\n    import torch\n",
)
HQ_NEGATIVE_IMPORTS = (
    "from ..edge.egress import check_artifact",
    "from ..edge.weeks import next_week",
    "from ..edge import egress",
    "from ..packs.loader import FrozenPack",
    "from ..packs.connector import SITE_ID_RE",
    "from ...hierarchy import split_path",
    "from .. import stats",
    "from ..jsonio import canonical_bytes",
    "from .store import CollectiveStore",
    "import sqlite3",
)


class HqImportGuardTests(unittest.TestCase):
    def detect_files(self) -> list[Path]:
        files = sorted(DETECT_DIR.glob("*.py"))
        self.assertEqual([p.name for p in files], ["__init__.py", "detectors.py", "org.py", "rules.py", "store.py"])
        return files

    def pushdown_files(self) -> list[Path]:
        files = sorted(PUSHDOWN_DIR.glob("*.py"))
        self.assertEqual([p.name for p in files], ["__init__.py", "gate.py", "orchestrator.py", "questions.py"])
        return files

    def test_no_detect_module_imports_a_forbidden_module(self) -> None:
        for path in self.detect_files():
            with self.subTest(module=path.name):
                module = f"mycelic.collective.detect.{path.stem}"
                self.assertEqual(forbidden_imports(path.read_text(encoding="utf-8"), module, HQ_FORBIDDEN), [])

    def test_no_pushdown_module_imports_a_forbidden_module(self) -> None:
        self.assertIn("mycelic.collective.edge.verify", HQ_FORBIDDEN)
        for path in self.pushdown_files():
            with self.subTest(module=path.name):
                module = f"mycelic.collective.pushdown.{path.stem}"
                self.assertEqual(forbidden_imports(path.read_text(encoding="utf-8"), module, HQ_FORBIDDEN), [])

    def test_checker_flags_every_positive_snippet(self) -> None:
        for snippet in HQ_POSITIVE_IMPORTS:
            with self.subTest(snippet=snippet):
                self.assertTrue(forbidden_imports(snippet, "mycelic.collective.detect.detectors", HQ_FORBIDDEN))

    def test_checker_passes_every_negative_snippet(self) -> None:
        for snippet in HQ_NEGATIVE_IMPORTS:
            with self.subTest(snippet=snippet):
                self.assertEqual(forbidden_imports(snippet, "mycelic.collective.detect.detectors", HQ_FORBIDDEN), [])

    def test_checker_flags_an_injected_import_in_a_copy_of_detectors(self) -> None:
        source = (DETECT_DIR / "detectors.py").read_text(encoding="utf-8")
        for line in ("from ..edge.records import RecordStore", "from ..inference.runtime import Runtime"):
            with self.subTest(line=line):
                self.assertTrue(forbidden_imports(source + "\n" + line + "\n", "mycelic.collective.detect.detectors",
                                                  HQ_FORBIDDEN))

    def test_fresh_interpreter_loads_nothing_forbidden_with_the_detect_modules(self) -> None:
        modules = tuple(f"mycelic.collective.detect.{p.stem}" for p in self.detect_files() if p.stem != "__init__")
        modules += tuple(f"mycelic.collective.pushdown.{p.stem}" for p in self.pushdown_files() if p.stem != "__init__")
        code = ("import json, sys\n" + "".join(f"import {m}\n" for m in modules)
                + "print(json.dumps(sorted(sys.modules)))\n")
        r = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, timeout=120,
                           env={**os.environ, "PYTHONPATH": str(ROOT)})
        self.assertEqual(r.returncode, 0, r.stderr)
        loaded = json.loads(r.stdout.strip().splitlines()[-1])
        self.assertIn("mycelic.collective.detect.detectors", loaded)
        self.assertEqual([m for m in loaded if _forbidden(m, HQ_FORBIDDEN) and m not in HQ_ALLOWED_LOADED], [])
        self.assertEqual([m for m in loaded if m.startswith("mycelic.collective.inference")], list(HQ_ALLOWED_LOADED))
        self.assertEqual([m for m in loaded if _forbidden(m, FORBIDDEN_LOADED) and not m.startswith("mycelic.")], [])


X5_ATTACKS = ROOT / "mycelic" / "collective" / "experiments" / "x5_attacks.py"
X5_ATTACKS_MODULE = "mycelic.collective.experiments.x5_attacks"
# what the X5 attacks may never import (B3): no file, store, network or process module; nothing site-side, no
# pushdown, follow-up or detection code, no leakage scan, run file, harness or world generator, neither the X5
# orchestrator nor G0, and no model client. The pack type may be named only under TYPE_CHECKING.
X5_ATTACKS_FORBIDDEN = ("sqlite3", "os", "pathlib", "io", "shutil", "tempfile", "subprocess", "socket",
                        "mycelic.collective.edge", "mycelic.collective.pushdown", "mycelic.collective.followup",
                        "mycelic.collective.detect", "mycelic.collective.inference", "mycelic.collective.leakage",
                        "mycelic.collective.runfiles", "mycelic.collective.evaluate",
                        "mycelic.collective.packs.generator", "mycelic.collective.experiments.x5_inference",
                        "mycelic.collective.experiments.g0_canary", "openai", "anthropic", "ollama", "llama_cpp",
                        "vllm", "transformers", "torch")
X5_ATTACKS_ALLOWED_LOADED = ("mycelic", "mycelic.version", "mycelic.collective", "mycelic.collective.stats",
                             "mycelic.collective.jsonio", "mycelic.collective.experiments", X5_ATTACKS_MODULE)
X5_POSITIVE_IMPORTS = (
    "import sqlite3",
    "import os",
    "from pathlib import Path",
    "import io",
    "from ..edge.site import build_cells",
    "from ..edge import egress",
    "from ..pushdown.questions import build_question",
    "from ..followup.ledger import FollowupLedger",
    "from ..detect.store import HqReader",
    "from ..inference import fake",
    "from .. import leakage",
    "from .. import runfiles",
    "from ..evaluate.baselines import r_mf_cells",
    "from ..packs.generator import world_digest",
    "from ..packs import generator",
    "from . import x5_inference",
    "from .x5_inference import lexical_rows",
    "from .g0_canary import make_world",
    "import mycelic.collective.edge.records as rec",
    "import openai",
)
X5_NEGATIVE_IMPORTS = (
    "from .. import stats",
    "from ..stats import wilson",
    "from ..jsonio import canonical_dumps",
    "import math",
    "import random",
    "from dataclasses import dataclass",
    "from typing import TYPE_CHECKING",
)


def type_checking_only(source: str, prefix: str) -> list[str]:
    """Every import of a module under ``prefix`` that is not inside an ``if TYPE_CHECKING:`` block."""
    tree = ast.parse(source)
    guarded: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.If) and isinstance(node.test, ast.Name) and node.test.id == "TYPE_CHECKING":
            guarded.update(id(n) for child in node.body for n in ast.walk(child))
    hits = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            base = _resolve(X5_ATTACKS_MODULE, node.level, node.module) or ""
            names = [base] + [f"{base}.{a.name}" for a in node.names]
        else:
            continue
        if any(_forbidden(n, (prefix,)) for n in names) and id(node) not in guarded:
            hits.append(f"line {node.lineno}")
    return hits


class X5AttacksImportGuardTests(unittest.TestCase):
    def test_x5_attacks_imports_nothing_forbidden(self) -> None:
        self.assertEqual(forbidden_imports(X5_ATTACKS.read_text(encoding="utf-8"), X5_ATTACKS_MODULE,
                                           X5_ATTACKS_FORBIDDEN), [])

    def test_checker_flags_every_positive_snippet(self) -> None:
        for snippet in X5_POSITIVE_IMPORTS:
            with self.subTest(snippet=snippet):
                self.assertTrue(forbidden_imports(snippet, X5_ATTACKS_MODULE, X5_ATTACKS_FORBIDDEN))

    def test_checker_passes_every_negative_snippet(self) -> None:
        for snippet in X5_NEGATIVE_IMPORTS:
            with self.subTest(snippet=snippet):
                self.assertEqual(forbidden_imports(snippet, X5_ATTACKS_MODULE, X5_ATTACKS_FORBIDDEN), [])

    def test_checker_flags_an_injected_import_in_a_copy_of_x5_attacks(self) -> None:
        source = X5_ATTACKS.read_text(encoding="utf-8")
        for line in ("import sqlite3", "from ..edge.site import build_cells", "from .x5_inference import WorldData"):
            with self.subTest(line=line):
                self.assertTrue(forbidden_imports(source + "\n" + line + "\n", X5_ATTACKS_MODULE,
                                                  X5_ATTACKS_FORBIDDEN))

    def test_the_pack_type_only_under_type_checking(self) -> None:
        self.assertEqual(type_checking_only(X5_ATTACKS.read_text(encoding="utf-8"), "mycelic.collective.packs"), [])
        guarded = "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    from ..packs.loader import FrozenPack\n"
        self.assertEqual(type_checking_only(guarded, "mycelic.collective.packs"), [])
        self.assertEqual(type_checking_only("from ..packs.loader import FrozenPack\n", "mycelic.collective.packs"),
                         ["line 1"])

    def test_fresh_interpreter_loads_only_the_allowed_modules(self) -> None:
        code = f"import json, sys\nimport {X5_ATTACKS_MODULE}\nprint(json.dumps(sorted(sys.modules)))\n"
        r = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, timeout=120,
                           env={**os.environ, "PYTHONPATH": str(ROOT)})
        self.assertEqual(r.returncode, 0, r.stderr)
        loaded = json.loads(r.stdout.strip().splitlines()[-1])
        self.assertIn(X5_ATTACKS_MODULE, loaded)
        self.assertEqual([m for m in loaded if (m == "mycelic" or m.startswith("mycelic."))
                          and m not in X5_ATTACKS_ALLOWED_LOADED], [])
        self.assertNotIn("sqlite3", loaded)
        self.assertEqual([m for m in loaded if _forbidden(m, FORBIDDEN_LOADED) and not m.startswith("mycelic.")], [])


EVALUATE_POSITIVE_IMPORTS = (
    "from ..inference.runtime import Runtime",
    "from ..inference import client",
    "from .. import inference",
    "import mycelic.collective.inference.fake",
    "from mycelic.collective.inference.errors import KINDS",
    "import importlib\nimportlib.import_module('mycelic.collective.inference.client')",
    "import openai",
    "from anthropic import Anthropic",
    "def lazy():\n    import transformers\n",
    "import torch.nn",
    "import ollama",
    "from llama_cpp import server",
    "import vllm",
)
EVALUATE_NEGATIVE_IMPORTS = (
    "from ..edge.site import EdgeSite",
    "from ..detect.detectors import detect",
    "from ..packs.connector import field_values",
    "from ..connectors.openfda import load_cache",
    "from .. import stats",
    "from .baselines import CHANNELS",
    "import openaiish",
    "from ..inferences import x",
)


class EvaluateImportGuardTests(unittest.TestCase):
    def test_the_evaluation_files_exist(self) -> None:
        self.assertEqual([p.name for p in EVALUATE_FILES],
                         ["__init__.py", "baselines.py", "harness.py", "plant.py", "openfda_replay.py"])

    def test_no_evaluation_module_imports_an_inference_module_or_a_model_client(self) -> None:
        for path in EVALUATE_FILES:
            module = ".".join(path.relative_to(ROOT).with_suffix("").parts)
            with self.subTest(module=module):
                self.assertEqual(forbidden_imports(path.read_text(encoding="utf-8"), module, EVALUATE_FORBIDDEN), [])

    def test_checker_flags_every_positive_snippet(self) -> None:
        for snippet in EVALUATE_POSITIVE_IMPORTS:
            with self.subTest(snippet=snippet):
                self.assertTrue(forbidden_imports(snippet, "mycelic.collective.evaluate.harness", EVALUATE_FORBIDDEN))

    def test_checker_passes_every_negative_snippet(self) -> None:
        for snippet in EVALUATE_NEGATIVE_IMPORTS:
            with self.subTest(snippet=snippet):
                self.assertEqual(forbidden_imports(snippet, "mycelic.collective.evaluate.harness",
                                                   EVALUATE_FORBIDDEN), [])

    def test_checker_flags_an_injected_import_in_a_copy_of_the_harness(self) -> None:
        source = (EVALUATE_DIR / "harness.py").read_text(encoding="utf-8")
        for line in ("from ..inference.runtime import Runtime", "import openai"):
            with self.subTest(line=line):
                self.assertTrue(forbidden_imports(source + "\n" + line + "\n", "mycelic.collective.evaluate.harness",
                                                  EVALUATE_FORBIDDEN))


class ClockEntropyTests(unittest.TestCase):
    def test_every_detect_module_is_on_the_determinism_list_with_no_hits(self) -> None:
        files = sorted(DETECT_DIR.glob("*.py"))
        self.assertEqual(len(files), 5)
        for path in files:
            rel = path.relative_to(ROOT).as_posix()
            with self.subTest(module=rel):
                self.assertIn(rel, DETERMINISTIC_MODULES)
                self.assertEqual(nondeterminism(path.read_text(encoding="utf-8")), [])

    def test_every_pushdown_module_is_on_the_determinism_list_with_no_hits(self) -> None:
        files = sorted(PUSHDOWN_DIR.glob("*.py"))
        self.assertEqual(len(files), 4)
        for path in [*files, ROOT / "mycelic" / "collective" / "edge" / "verify.py",
                     ROOT / "mycelic" / "collective" / "experiments" / "e2_pushdown.py"]:
            rel = path.relative_to(ROOT).as_posix()
            with self.subTest(module=rel):
                self.assertIn(rel, DETERMINISTIC_MODULES)
                self.assertEqual(nondeterminism(path.read_text(encoding="utf-8")), [])

    def test_an_injected_clock_or_entropy_call_in_a_copy_of_detectors_is_flagged(self) -> None:
        source = (DETECT_DIR / "detectors.py").read_text(encoding="utf-8")
        self.assertEqual(nondeterminism(source), [])
        for snippet in ("import time\nstamp = time.time()\n", "import datetime\nday = datetime.date.today()\n",
                        "import random\nsalt = random.random()\n", "import os\nnoise = os.urandom(4)\n"):
            with self.subTest(snippet=snippet):
                self.assertTrue(nondeterminism(source + "\n" + snippet))

    def test_every_followup_module_is_on_the_determinism_list_with_no_hits(self) -> None:
        files = sorted(FOLLOWUP_DIR.glob("*.py"))
        self.assertEqual(len(files), 7)
        for path in [*files, ROOT / "mycelic" / "collective" / "edge" / "packets.py"]:
            rel = path.relative_to(ROOT).as_posix()
            with self.subTest(module=rel):
                self.assertIn(rel, DETERMINISTIC_MODULES)
                self.assertEqual(nondeterminism(path.read_text(encoding="utf-8")), [])


# --------------------------------------------------------------------------------------------------- follow-up (G7)

FOLLOWUP_DIR = ROOT / "mycelic" / "collective" / "followup"
FOLLOWUP_FILES = ("__init__.py", "drafts.py", "executors.py", "ledger.py", "outcome.py", "policy.py", "service.py")
# what no follow-up module may import: the site's raw-record modules, the site verifier and packet assembler, the world
# generator, the evaluation and leakage modules, the harnesses and every model client; inference only in drafts.py
FOLLOWUP_FORBIDDEN = ("mycelic.collective.edge.records", "mycelic.collective.edge.site",
                      "mycelic.collective.edge.extract", "mycelic.collective.edge.verify",
                      "mycelic.collective.edge.packets", "mycelic.collective.packs.generator",
                      "mycelic.collective.evaluate", "mycelic.collective.leakage", "mycelic.collective.experiments",
                      "openai", "anthropic", "ollama", "llama_cpp", "vllm", "transformers", "torch")
INFERENCE_PREFIX = ("mycelic.collective.inference",)
DRAFTS_INFERENCE = ("mycelic.collective.inference.errors", "mycelic.collective.inference.tasks")
# importing the follow-up modules may load these inference modules: errors (through edge.egress), tasks and the
# routing module tasks reads its task-name pattern from (through drafts)
FOLLOWUP_ALLOWED_LOADED = ("mycelic.collective.inference", "mycelic.collective.inference.errors",
                           "mycelic.collective.inference.routing", "mycelic.collective.inference.tasks")
PACKETS_FORBIDDEN = ("mycelic.collective.followup", "mycelic.collective.pushdown", "mycelic.collective.detect",
                     "mycelic.collective.inference")


def runtime_imports(source: str, module: str) -> set[str]:
    """The absolute module names a module imports when it runs (its ``if TYPE_CHECKING:`` blocks excluded)."""
    tree = ast.parse(source)
    skipped: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.If) and isinstance(node.test, ast.Name) and node.test.id == "TYPE_CHECKING":
            skipped.update(id(n) for child in node.body for n in ast.walk(child))
    names: set[str] = set()
    for node in ast.walk(tree):
        if id(node) in skipped:
            continue
        if isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = _resolve(module, node.level, node.module)
            if base is not None:
                names.add(base)
    return names


class FollowupImportGuardTests(unittest.TestCase):
    def files(self) -> list[Path]:
        files = sorted(FOLLOWUP_DIR.glob("*.py"))
        self.assertEqual([p.name for p in files], list(FOLLOWUP_FILES))
        return files

    def test_no_followup_module_imports_a_forbidden_module(self) -> None:
        for path in self.files():
            with self.subTest(module=path.name):
                module = f"mycelic.collective.followup.{path.stem}"
                self.assertEqual(forbidden_imports(path.read_text(encoding="utf-8"), module, FOLLOWUP_FORBIDDEN), [])

    def test_only_drafts_imports_inference_and_only_tasks_and_errors_at_runtime(self) -> None:
        for path in self.files():
            module = f"mycelic.collective.followup.{path.stem}"
            source = path.read_text(encoding="utf-8")
            with self.subTest(module=path.name):
                inference = sorted(n for n in runtime_imports(source, module) if _forbidden(n, INFERENCE_PREFIX))
                if path.name == "drafts.py":
                    self.assertEqual(inference, list(DRAFTS_INFERENCE))
                    self.assertIn("mycelic.collective.inference.runtime",
                                  {n for n in (_resolve(module, node.level, node.module)
                                               for node in ast.walk(ast.parse(source))
                                               if isinstance(node, ast.ImportFrom)) if n})
                else:
                    self.assertEqual(inference, [])
                    self.assertEqual(forbidden_imports(source, module, INFERENCE_PREFIX), [])

    def test_the_packet_assembler_imports_no_followup_pushdown_detect_or_inference_module(self) -> None:
        path = ROOT / "mycelic" / "collective" / "edge" / "packets.py"
        source = path.read_text(encoding="utf-8")
        self.assertEqual(forbidden_imports(source, "mycelic.collective.edge.packets", PACKETS_FORBIDDEN), [])
        for line in ("from ..followup.policy import BUILT_AHEAD_LABEL", "from ..detect.store import HqReader",
                     "from ..pushdown import gate", "from ..inference.runtime import Runtime"):
            with self.subTest(line=line):
                self.assertTrue(forbidden_imports(source + "\n" + line + "\n", "mycelic.collective.edge.packets",
                                                  PACKETS_FORBIDDEN))

    def test_an_injected_import_in_a_copy_of_the_service_is_flagged(self) -> None:
        source = (FOLLOWUP_DIR / "service.py").read_text(encoding="utf-8")
        module = "mycelic.collective.followup.service"
        self.assertEqual(forbidden_imports(source, module, FOLLOWUP_FORBIDDEN), [])
        for line in ("from ..edge.records import RecordStore", "from ..edge.packets import PacketAssembler",
                     "from ..edge import site", "from ..packs.generator import generate", "from .. import leakage",
                     "from ..experiments.common import fail", "import openai"):
            with self.subTest(line=line):
                self.assertTrue(forbidden_imports(source + "\n" + line + "\n", module, FOLLOWUP_FORBIDDEN))
        self.assertTrue(forbidden_imports(source + "\nfrom ..inference.runtime import Runtime\n", module,
                                          INFERENCE_PREFIX))

    def loaded(self, modules: list[str]) -> list[str]:
        code = ("import json, sys\n" + "".join(f"import {m}\n" for m in modules)
                + "print(json.dumps(sorted(sys.modules)))\n")
        r = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, timeout=120,
                           env={**os.environ, "PYTHONPATH": str(ROOT)})
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout.strip().splitlines()[-1])

    def test_a_fresh_interpreter_loads_nothing_forbidden_with_the_followup_modules(self) -> None:
        loaded = self.loaded([f"mycelic.collective.followup.{p.stem}" for p in self.files() if p.stem != "__init__"])
        self.assertIn("mycelic.collective.followup.service", loaded)
        self.assertEqual([m for m in loaded if _forbidden(m, FOLLOWUP_FORBIDDEN)], [])
        self.assertEqual([m for m in loaded if _forbidden(m, INFERENCE_PREFIX)], list(FOLLOWUP_ALLOWED_LOADED))
        self.assertEqual([m for m in loaded if _forbidden(m, FORBIDDEN_LOADED) and not m.startswith("mycelic.")], [])

    def test_a_fresh_interpreter_loads_no_followup_pushdown_or_detect_module_with_the_packet_assembler(self) -> None:
        loaded = self.loaded(["mycelic.collective.edge.packets"])
        self.assertIn("mycelic.collective.edge.packets", loaded)
        self.assertEqual([m for m in loaded if _forbidden(m, PACKETS_FORBIDDEN) and m not in HQ_ALLOWED_LOADED], [])


APPROVAL_NAMES = ("approve", "edit", "reject")
# the only places that may call approve, edit or reject (D10): the service itself, the G0 runner's simulated owner
# (stamped simulated_approvals), the demo console a later gate adds, and the tests
APPROVAL_ALLOWED = ("mycelic/collective/followup/service.py", "mycelic/collective/experiments/g0_canary.py",
                    "demo/collective/collective_demo.py")
APPROVAL_SCAN_ROOTS = ("mycelic", "demo")


def approval_calls(source: str) -> list[str]:
    """Every call of ``approve``, ``edit`` or ``reject``: by name, as an attribute, or through ``getattr`` with that
    name as a constant."""
    hits = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name) and func.id in APPROVAL_NAMES:
            hits.append(f"line {node.lineno}: call of {func.id}")
        elif isinstance(func, ast.Attribute) and func.attr in APPROVAL_NAMES:
            hits.append(f"line {node.lineno}: call of .{func.attr}")
        elif (isinstance(func, ast.Name) and func.id == "getattr" and len(node.args) >= 2
              and isinstance(node.args[1], ast.Constant) and node.args[1].value in APPROVAL_NAMES):
            hits.append(f"line {node.lineno}: getattr of {node.args[1].value}")
    return hits


POSITIVE_APPROVAL_CALLS = (
    "service.approve(key, 1, principal=p, as_of=a)",
    "svc.edit(key, 1, {'title': 'x'}, principal=p, as_of=a)",
    "svc.reject(key, principal=p, as_of=a)",
    "approve(key)",
    "getattr(svc, 'approve')(key, 1)",
    "decide = getattr(svc, 'reject')",
    "self.followups.service.approve(k, v)",
    "[s.edit(k, 1, {}) for s in services]",
)
NEGATIVE_APPROVAL_CALLS = (
    "def approve(self, key):\n    return key\n",
    "svc.approved",
    "x = 'approve'",
    "svc.approve_all()",
    "reject_reason = 1",
    "editor.open()",
    "getattr(svc, 'state')(key)",
    "fn = svc.approve",
)


def approval_scan_files() -> list[Path]:
    out = []
    for root in APPROVAL_SCAN_ROOTS:
        for path in sorted((ROOT / root).rglob("*.py")):
            if "__pycache__" not in path.parts:
                out.append(path)
    return out


class ApprovalCallSiteTests(unittest.TestCase):
    def test_checker_flags_every_positive_snippet(self) -> None:
        for snippet in POSITIVE_APPROVAL_CALLS:
            with self.subTest(snippet=snippet):
                self.assertTrue(approval_calls(snippet))

    def test_checker_passes_every_negative_snippet(self) -> None:
        for snippet in NEGATIVE_APPROVAL_CALLS:
            with self.subTest(snippet=snippet):
                self.assertEqual(approval_calls(snippet), [])

    def test_only_the_allow_list_calls_approve_edit_or_reject(self) -> None:
        files = approval_scan_files()
        self.assertIn(ROOT / "mycelic" / "collective" / "followup" / "service.py", files)
        self.assertIn(ROOT / "mycelic" / "service.py", files)
        hits = {p.relative_to(ROOT).as_posix(): approval_calls(p.read_text(encoding="utf-8")) for p in files}
        self.assertEqual({k: v for k, v in hits.items() if v and k not in APPROVAL_ALLOWED}, {})
        self.assertTrue(hits["mycelic/collective/experiments/g0_canary.py"])      # the checker sees the real call
        self.assertTrue(hits["demo/collective/collective_demo.py"])               # and the demo console's (G8)

    def test_an_injected_call_in_a_copy_of_detectors_is_flagged(self) -> None:
        source = (DETECT_DIR / "detectors.py").read_text(encoding="utf-8")
        self.assertEqual(approval_calls(source), [])
        for line in ("service.approve(key, 1, principal=p, as_of=a)", "getattr(service, 'edit')(key, 1, {})",
                     "reject(key)"):
            with self.subTest(line=line):
                self.assertTrue(approval_calls(source + "\n" + line + "\n"))


# --------------------------------------------------------------------------------------------------- demo imports

DEMO_DIR = ROOT / "demo" / "collective"
# what the demo's screen builder and number lint may never import: the engine, and every module that runs the loop
# (they read run files only, so a lint or a replayed screen can never compute a number of its own)
DEMO_FORBIDDEN = ("demo.collective.collective_demo", "collective_demo", "mycelic.collective.inference",
                  "mycelic.collective.edge", "mycelic.collective.detect", "mycelic.collective.pushdown",
                  "mycelic.collective.followup", "openai", "anthropic", "ollama", "llama_cpp", "vllm", "transformers",
                  "torch")
DEMO_READERS = ("lint_numbers.py", "screen.py")
DEMO_POSITIVE_IMPORTS = (
    "from demo.collective import collective_demo",
    "from demo.collective.collective_demo import DemoEngine",
    "import collective_demo",
    "import demo.collective.collective_demo as engine",
    "from mycelic.collective.inference.runtime import Runtime",
    "from mycelic.collective.edge.egress import verdict_buckets",
    "from mycelic.collective.detect import detectors",
    "from mycelic.collective.pushdown.gate import STATUS_RANK",
    "from mycelic.collective.followup.service import replay",
    "import importlib\nimportlib.import_module('mycelic.collective.edge.site')",
)
DEMO_NEGATIVE_IMPORTS = (
    "from demo.collective import screen as scr",
    "from mycelic.collective import runfiles",
    "from mycelic.collective.jsonio import strict_load",
    "from mycelic.collective import schemacheck",
    "import html.parser",
)


class DemoImportGuardTests(unittest.TestCase):
    def test_the_demo_readers_import_no_engine_or_loop_module(self) -> None:
        for name in DEMO_READERS:
            with self.subTest(module=name):
                source = (DEMO_DIR / name).read_text(encoding="utf-8")
                self.assertEqual(forbidden_imports(source, f"demo.collective.{Path(name).stem}", DEMO_FORBIDDEN), [])

    def test_checker_flags_every_positive_snippet(self) -> None:
        for snippet in DEMO_POSITIVE_IMPORTS:
            with self.subTest(snippet=snippet):
                self.assertTrue(forbidden_imports(snippet, "demo.collective.lint_numbers", DEMO_FORBIDDEN))

    def test_checker_passes_every_negative_snippet(self) -> None:
        for snippet in DEMO_NEGATIVE_IMPORTS:
            with self.subTest(snippet=snippet):
                self.assertEqual(forbidden_imports(snippet, "demo.collective.lint_numbers", DEMO_FORBIDDEN), [])

    def test_an_injected_import_in_a_copy_of_the_lint_is_flagged(self) -> None:
        source = (DEMO_DIR / "lint_numbers.py").read_text(encoding="utf-8")
        self.assertEqual(forbidden_imports(source, "demo.collective.lint_numbers", DEMO_FORBIDDEN), [])
        for line in ("from demo.collective import collective_demo", "from mycelic.collective.detect import detectors",
                     "import mycelic.collective.inference.runtime"):
            with self.subTest(line=line):
                self.assertTrue(forbidden_imports(source + "\n" + line + "\n", "demo.collective.lint_numbers",
                                                  DEMO_FORBIDDEN))

    def test_a_fresh_interpreter_loads_nothing_forbidden_with_the_demo_readers(self) -> None:
        code = ("import json, sys\nimport demo.collective.screen, demo.collective.lint_numbers\n"
                "print(json.dumps(sorted(sys.modules)))\n")
        r = subprocess.run([sys.executable, "-S", "-c", code], cwd=ROOT, capture_output=True, text=True, timeout=60,
                           env={"PYTHONPATH": str(ROOT), "PATH": "/usr/bin:/bin"})
        self.assertEqual(r.returncode, 0, r.stderr)
        loaded = json.loads(r.stdout)
        self.assertIn("demo.collective.lint_numbers", loaded)
        self.assertEqual([m for m in loaded if _forbidden(m, DEMO_FORBIDDEN)], [])


# --------------------------------------------------------------------------------------------------- runbook

BOOTSTRAP = """
import runpy, socket, sys

def _blocked(*args, **kwargs):
    raise OSError("network disabled for the runbook dry-run test")

socket.socket.connect = _blocked
socket.socket.connect_ex = _blocked
socket.create_connection = _blocked
socket.getaddrinfo = _blocked
kind, target, *argv = sys.argv[1:]
sys.argv = [target, *argv]
if kind == "-m":
    runpy.run_module(target, run_name="__main__", alter_sys=True)
else:
    runpy.run_path(target, run_name="__main__")
"""
_PLACEHOLDER = re.compile(r"<([a-z][a-z-]*)>")
_SKIP_DIRS = {".git", "__pycache__", ".pytest_cache"}


def runbook_commands() -> list[str]:
    return [line.strip() for line in RUNBOOK.read_text(encoding="utf-8").splitlines()
            if line.strip().startswith(RUNBOOK_PREFIXES)]


def path_snapshot(root: Path) -> set[str]:
    out = set()
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        rel = os.path.relpath(dirpath, root)
        out.add(rel + "/")
        out.update(os.path.join(rel, f) for f in filenames)
    return out


class RunbookCommandTests(unittest.TestCase):
    def test_commands_are_single_lines_with_known_placeholders(self) -> None:
        commands = runbook_commands()
        self.assertTrue(commands)
        for line in RUNBOOK.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith(RUNBOOK_PREFIXES):
                self.assertFalse(line.rstrip().endswith("\\"), line)
        for command in commands:
            for token in shlex.split(command):
                for name in _PLACEHOLDER.findall(token):
                    self.assertIn(name, RUNBOOK_PLACEHOLDERS, command)

    def test_commands_cover_the_week1_clis(self) -> None:
        joined = "\n".join(runbook_commands())
        for needle in ("mycelic.collective.experiments.e3_latency", "mycelic.collective.connectors.openfda fetch",
                       "mycelic.collective.experiments.n1_narratives sample",
                       "mycelic.collective.experiments.n1_narratives score"):
            self.assertIn(needle, joined)

    def test_commands_cover_the_g2_clis(self) -> None:
        joined = "\n".join(runbook_commands())
        for sub in ("prepare", "label-check", "prereg", "run", "compare"):
            self.assertIn(f"mycelic.collective.experiments.e1_extract {sub} ", joined)
        self.assertIn("mycelic.collective.packs.loader check", joined)

    def test_commands_cover_the_g3_cli(self) -> None:
        joined = "\n".join(runbook_commands())
        self.assertIn("mycelic.collective.experiments.g0_canary ", joined)

    def test_commands_cover_the_g5_clis(self) -> None:
        joined = "\n".join(runbook_commands())
        for sub in ("prereg", "check-plant", "run"):
            self.assertIn(f"mycelic.collective.evaluate.harness {sub} ", joined)
        for sub in ("prereg", "signals", "score"):
            self.assertIn(f"mycelic.collective.experiments.openfda_replay {sub} ", joined)
        self.assertIn("mycelic.collective.connectors.openfda fetch --dataset recall ", joined)

    def test_commands_cover_the_g6_clis(self) -> None:
        commands = [c for c in runbook_commands() if "mycelic.collective.experiments.e2_pushdown run " in c]
        self.assertTrue(commands)
        for command in commands:
            self.assertIn("--allow-external-raw synthetic --data-label synthetic", command)
        self.assertTrue(any("--site-routing <site-routing-dir>" in c and "--central-routing <central-routing-file>"
                            in c for c in commands))
        self.assertTrue(any("--site-routing" not in c and "--central-routing" not in c for c in commands))

    def test_commands_cover_the_g7_clis(self) -> None:
        joined = "\n".join(runbook_commands())
        self.assertIn("python -m mycelic.collective.followup.ledger verify --ledger <ledger-file> --expected-head "
                      "<head-hash>", joined)
        self.assertIn("python -m mycelic.collective.experiments.e5_injection --pack <pack> --records 1000 --seed "
                      "<seed> --entity-type <entity-type> --injected-id <injected-id> --out runs/e5/<run-id>", joined)

    def test_commands_cover_the_g8_clis(self) -> None:
        commands = runbook_commands()
        demo = [c for c in commands if c.startswith("python demo/collective/collective_demo.py ")]
        self.assertIn("python demo/collective/collective_demo.py --record runs/collective/<run-id>", demo)
        self.assertTrue(any("--record runs/collective/<run-id>" in c and "--routing <routing-file>" in c
                            for c in demo))
        for needle in ("--serve", "--replay <recorded-dir>", "--export <page-file>"):
            self.assertTrue(any(needle in c for c in demo), needle)
        self.assertTrue(any(c.startswith("python demo/collective/lint_numbers.py ") for c in commands))

    def test_commands_cover_the_b3_clis(self) -> None:
        commands = runbook_commands()
        self.assertIn("python -m mycelic.collective.experiments.x5_inference prereg --packs "
                      "device_quality,claims_integrity --n 1000 --run-id <run-id> --runs-dir runs", commands)
        self.assertIn("python -m mycelic.collective.experiments.x5_inference run --prereg <prereg-file> --run-id "
                      "<run-id> --runs-dir runs --work-dir runs/x5-work/<run-id>", commands)

    def test_every_command_dry_runs_offline_and_creates_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            values = {k: v.format(tmp=tmp) for k, v in RUNBOOK_PLACEHOLDERS.items()}
            before = path_snapshot(ROOT), path_snapshot(Path(tmp))
            for command in runbook_commands():
                tokens = [_PLACEHOLDER.sub(lambda m: values[m.group(1)], t) for t in shlex.split(command)]
                self.assertEqual(tokens[0], "python")
                if tokens[1] == "-m":
                    kind, target, args = "-m", tokens[2], tokens[3:]
                else:
                    kind, target, args = "path", tokens[1], tokens[2:]
                with self.subTest(command=command):
                    r = subprocess.run([sys.executable, "-c", BOOTSTRAP, kind, target, *args, "--dry-run"], cwd=ROOT,
                                       capture_output=True, text=True, timeout=60,
                                       env={"PYTHONDONTWRITEBYTECODE": "1", "TMPDIR": tmp, "PYTHONPATH": str(ROOT),
                                            "PATH": "/usr/bin:/bin"})
                    self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
                    self.assertTrue(r.stdout.startswith("dry-run: "), r.stdout)
            self.assertEqual((path_snapshot(ROOT), path_snapshot(Path(tmp))), before)


# --------------------------------------------------------------------------------------------------- domain literals

PACK_DATA = ROOT / "mycelic" / "collective" / "packs" / "data"
# every built-in pack (B4): the generic test loops of the collective tests iterate this, not a literal list
BUILTIN_PACKS = tuple(sorted(p.name for p in PACK_DATA.iterdir() if (p / "pack.json").is_file()))
DOMAIN_SCAN_EXCLUDED = ("mycelic/collective/packs/data",)
OPENFDA_LITERAL_SCOPE = ("mycelic/collective/packs/*.py", "mycelic/collective/edge/*.py",
                         "mycelic/collective/experiments/e1_extract.py")
# "text" is the openFDA narrative field name and also the model payload key the G2 brief fixes ({language, text});
# it is the only exemption, and test_openfda_check_is_exact proves every other segment is still flagged
OPENFDA_SEGMENT_EXEMPT = ("text",)
ILLUSTRATIVE_CODE_MARK = "ILL-"


def pack_terms_of(pack: str) -> set[str]:
    """Type, predicate, code, rule, template, follow-up type and role ids of one built-in pack."""
    d = PACK_DATA / pack
    vocabulary = json.loads((d / "vocabulary.json").read_text(encoding="utf-8"))
    followups = json.loads((d / "followups.json").read_text(encoding="utf-8"))
    terms = set(vocabulary["entity_types"]) | set(vocabulary["predicates"])
    terms |= set(json.loads((d / "codes.json").read_text(encoding="utf-8")))
    terms |= set(json.loads((d / "rules.json").read_text(encoding="utf-8"))["rules"])
    terms |= set(json.loads((d / "questions.json").read_text(encoding="utf-8"))["templates"])
    terms |= set(followups["types"]) | set(followups["roles"])
    return terms


def pack_terms() -> set[str]:
    """The terms of every built-in pack (:data:`BUILTIN_PACKS`)."""
    terms: set[str] = set()
    for pack in BUILTIN_PACKS:
        terms |= pack_terms_of(pack)
    return terms


def openfda_segments() -> set[str]:
    mapping = json.loads((PACK_DATA / "device_quality" / "mapping_openfda.json").read_text(encoding="utf-8"))
    paths = [mapping["record_ref"], mapping["received_date"]["path"], *(c["path"] for c in mapping["codes"]),
             *(p for paths in mapping["entities"].values() for p in paths), *(n["path"] for n in mapping["narrative"])]
    names = {seg.removesuffix("[]") for path in paths for seg in path.split(".")}
    names |= {n["where"]["field"] for n in mapping["narrative"] if n["where"]}
    return names


def _docstring_nodes(tree: ast.AST) -> set[int]:
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) \
                    and isinstance(first.value.value, str):
                out.add(id(first.value))
    return out


def domain_literal_hits(source: str, terms: set[str] | frozenset[str]) -> list[str]:
    """Identifiers and whole string constants (f-string parts included, docstrings excluded) equal to a pack term,
    and string constants containing the illustrative code mark."""
    tree = ast.parse(source)
    docstrings = _docstring_nodes(tree)
    hits = []
    for node in ast.walk(tree):
        names: list[str] = []
        if isinstance(node, ast.Name):
            names = [node.id]
        elif isinstance(node, ast.arg):
            names = [node.arg]
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names = [node.name]
        elif isinstance(node, ast.keyword) and node.arg is not None:
            names = [node.arg]
        elif isinstance(node, ast.alias):
            names = [*node.name.split("."), *([node.asname] if node.asname else [])]
        elif isinstance(node, ast.Attribute):
            names = [node.attr]
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings:
            if node.value in terms:
                hits.append(f"line {node.lineno}: string {node.value!r}")
            if ILLUSTRATIVE_CODE_MARK in node.value:
                hits.append(f"line {node.lineno}: string containing {ILLUSTRATIVE_CODE_MARK!r}")
        hits += [f"line {getattr(node, 'lineno', 0)}: name {n!r}" for n in names if n in terms]
    return hits


def string_constants(source: str) -> set[str]:
    tree = ast.parse(source)
    docstrings = _docstring_nodes(tree)
    return {n.value for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docstrings}


def generic_code_files() -> list[Path]:
    out = []
    for path in sorted((ROOT / "mycelic" / "collective").rglob("*.py")):
        rel = path.relative_to(ROOT).as_posix()
        if any(rel.startswith(ex + "/") for ex in DOMAIN_SCAN_EXCLUDED) or path.name.startswith("test_"):
            continue
        out.append(path)
    out += sorted((ROOT / "demo" / "collective").glob("*.py"))       # the demo (G8): every domain value from data
    return out


class DomainLiteralTests(unittest.TestCase):
    """No pack term (type, predicate, code, rule, template, follow-up type or role id of any built-in pack)
    appears in generic code as an identifier (name, argument, def or class name, keyword argument, import alias,
    attribute) or as a whole string constant (f-string constant parts included; docstrings excluded), and no
    string constant there contains 'ILL-'. This check does not catch a term inside a longer literal (a substring)
    or a value built at runtime: it keeps domain literals out of the code's structure, it cannot prove the code
    never handles domain text."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.terms = pack_terms()

    def positive_snippets(self) -> list[str]:
        out = []
        for term in sorted(t for t in self.terms if t.isidentifier())[:12]:
            out += [f"{term} = 'x'", f"def {term}():\n    pass\n", f"f({term}=1)", f"def g({term}):\n    pass\n",
                    f"class {term}:\n    pass\n", f"obj.{term}", f"y = '{term}'", f"y = f'{term}{{x}}'",
                    f"import os as {term}", f"from os import {term}"]
        for term in sorted(t for t in self.terms if not t.isidentifier())[:4]:
            out += [f"y = '{term}'", f"y = f'{term}{{x}}'"]
        out.append("code = 'ILL-0001'")
        return out

    def test_terms_cover_every_builtin_pack(self) -> None:
        # B4: was test_terms_cover_both_packs; each built-in pack's own terms are now checked to be in the scan
        self.assertGreaterEqual(len(BUILTIN_PACKS), 3)
        for pack in BUILTIN_PACKS:
            with self.subTest(pack=pack):
                own = pack_terms_of(pack)
                self.assertTrue(own)
                self.assertLessEqual(own, self.terms)
        self.assertGreater(len(self.terms), 40)
        self.assertTrue(any(t.startswith(ILLUSTRATIVE_CODE_MARK) for t in self.terms))

    def test_checker_flags_every_positive_snippet(self) -> None:
        for snippet in self.positive_snippets():
            with self.subTest(snippet=snippet):
                self.assertTrue(domain_literal_hits(snippet, self.terms))

    def test_checker_passes_every_negative_snippet(self) -> None:
        term = sorted(t for t in self.terms if t.isidentifier())[0]
        half = len(term) // 2
        negatives = (f'def f():\n    """{term}"""\n    return 1\n', f'"""{term}"""\nx = 1\n',
                     f'class C:\n    """{term}"""\n', f"x = '{term}_rate'", f"x = '{term[:half]}' + '{term[half:]}'",
                     "x = 'ILL'")
        for snippet in negatives:
            with self.subTest(snippet=snippet):
                self.assertEqual(domain_literal_hits(snippet, self.terms), [])

    def test_checker_flags_an_injected_literal_in_a_copy_of_extract(self) -> None:
        source = (ROOT / "mycelic" / "collective" / "edge" / "extract.py").read_text(encoding="utf-8")
        predicate = sorted(json.loads((PACK_DATA / "device_quality" / "vocabulary.json").read_text())["predicates"])[0]
        injected = source + f"\n\ndef probe(t):\n    if t == {predicate!r}:\n        return 1\n"
        self.assertEqual(domain_literal_hits(source, self.terms), [])
        self.assertTrue(domain_literal_hits(injected, self.terms))

    def test_generic_code_has_no_domain_literal(self) -> None:
        files = generic_code_files()
        self.assertIn(ROOT / "mycelic" / "collective" / "edge" / "extract.py", files)
        for name in ("collective_demo.py", "scenario.py", "screen.py", "lint_numbers.py"):
            self.assertIn(ROOT / "demo" / "collective" / name, files)
        self.assertIn(ROOT / "mycelic" / "collective" / "runfiles.py", files)
        hits = {p.relative_to(ROOT).as_posix(): domain_literal_hits(p.read_text(encoding="utf-8"), self.terms)
                for p in files}
        self.assertEqual({k: v for k, v in hits.items() if v}, {})

    def test_no_openfda_field_name_in_pack_extract_or_e1_code(self) -> None:
        segments = openfda_segments() - set(OPENFDA_SEGMENT_EXEMPT)
        self.assertIn("mdr_report_key", segments)
        files = sorted({p for pattern in OPENFDA_LITERAL_SCOPE for p in ROOT.glob(pattern)})
        self.assertGreaterEqual(len(files), 7)
        hits = {p.relative_to(ROOT).as_posix(): sorted(string_constants(p.read_text(encoding="utf-8")) & segments)
                for p in files}
        self.assertEqual({k: v for k, v in hits.items() if v}, {})

    def test_openfda_check_is_exact(self) -> None:
        segments = openfda_segments()
        self.assertEqual(set(OPENFDA_SEGMENT_EXEMPT) - segments, set())
        for name in sorted(segments - set(OPENFDA_SEGMENT_EXEMPT)):
            with self.subTest(name=name):
                self.assertIn(name, string_constants(f"x = {name!r}"))
                self.assertNotIn(name, string_constants(f'def f():\n    """{name}"""\n'))


# --------------------------------------------------------------------------------------------------- loop coverage

LOOP_SCAN_FILES = tuple(f"test_collective_{name}.py" for name in (
    "packs", "extract", "e1", "edge", "leakage", "pushdown", "detect", "followup", "evaluate", "guards"))
# not scanned and not generalised: their frozen scope names the two packs it was sealed for
LOOP_SCAN_EXCLUDED = {"test_collective_x1_sealed.py": "B2/B3 sealed scope names its two packs",
                      "test_collective_x5.py": "B2/B3 sealed scope names its two packs"}
# every remaining loop over a literal list of built-in packs, with why it stays, by file and the qualified name of
# its enclosing function
PACK_SPECIFIC_LOOPS = {
    ("test_collective_packs.py", "TemplateLoaderTests.test_only_the_config_hash_differs_from_g5"):
        "the G5 and B1 hash history of the two original packs",
    ("test_collective_pushdown.py", "PackPushdownConfigTests.test_only_config_hash_changed_against_g5"):
        "the G5 to B1 hash history of the two original packs",
    ("test_collective_followup.py", "PackFollowupTests.test_only_config_hash_changed_against_g6"):
        "the G6 to B1 hash history of the two original packs",
    ("test_collective_followup.py", "PackFollowupTests.test_the_new_args_shapes_and_the_unchanged_settings"):
        "pins the G6 follow-up settings of the two original packs",
    ("test_collective_followup.py",
     "PackFollowupTests.test_scar_is_proposable_only_on_supplier_keys_and_every_other_type_on_any_key"):
        "the device pack's supplier-keyed draft; test_collective_x3 checks the vendor draft",
    ("test_collective_evaluate.py", "PlantSpecTests.test_the_loader_accepts_plant_files_and_hashes_none_of_them"):
        "the R2, G7 and B1 hash history of the two original packs",
    ("test_collective_detect.py", "StoreTests.test_store_info_mismatch"):
        "store mismatch cases: the claims pack stands for another pack id",
    ("test_collective_leakage.py", "G0RunnerTests.setUpClass"):
        "G0RunnerTests' single-pack option cases (master data off, the pack default, lexical mode); the "
        "master-data-on run of every built-in pack is ON_RUNS",
}
# (test module, attribute path) of every generalised per-pack collection or table: its pack ids are its values (a
# tuple of ids or of packs) or its keys (a mapping)
GENERALISED = (
    ("test_collective_packs", "PACKS"), ("test_collective_packs", "EGRESS_EXPECTED"),
    ("test_collective_extract", "PACKS"), ("test_collective_e1", "PACKS"), ("test_collective_e1", "WORLDS"),
    ("test_collective_edge", "PACK_IDS"), ("test_collective_edge", "PACKS"), ("test_collective_edge", "KEY"),
    ("test_collective_leakage", "PACK_IDS"), ("test_collective_leakage", "PACKS"),
    ("test_collective_leakage", "ON_RUNS"),
    ("test_collective_pushdown", "PACKS"), ("test_collective_detect", "PACKS"), ("test_collective_detect", "VOCAB"),
    ("test_collective_followup", "TemplateDraftTests.PACKS"), ("test_collective_followup", "E5_INJECTIONS"),
    ("test_collective_evaluate", "SMOKE_FIXTURES"), ("test_collective_detect", "DETECTOR_DEFAULTS"),
)


def _pack_ids(value: Any) -> set[str]:
    if isinstance(value, Mapping):
        return set(value)
    return {v if isinstance(v, str) else v.id for v in value}


def _constant_pack_ids(node: ast.AST) -> set[str]:
    return {n.value for n in ast.walk(node) if isinstance(n, ast.Constant) and n.value in BUILTIN_PACKS}


def _pack_aliases(tree: ast.AST) -> dict[str, set[str]]:
    """Module-level names bound to a value that names built-in pack ids (``DQ = load_pack('device_quality')``), with
    the ids each one names."""
    out: dict[str, set[str]] = {}
    for node in getattr(tree, "body", ()):
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and _constant_pack_ids(node.value):
                out.setdefault(target.id, set()).update(_constant_pack_ids(node.value))
            elif isinstance(target, ast.Tuple) and isinstance(node.value, ast.Tuple):
                for t, v in zip(target.elts, node.value.elts):
                    if isinstance(t, ast.Name) and _constant_pack_ids(v):
                        out.setdefault(t.id, set()).update(_constant_pack_ids(v))
    return out


def literal_pack_loops(source: str) -> list[tuple[str, int]]:
    """``(qualified name of the enclosing function, such as ``Class.method``, or '<module>', line)`` of every for loop
    or comprehension whose iterable is a literal tuple, list or set naming two or more distinct built-in packs anywhere
    inside its elements: as ids, or as module-level names bound to one (a table row ``("dq-on", "device_quality")`` or
    a call ``args("claims_integrity", ...)`` counts as well as a bare id)."""
    tree = ast.parse(source)
    aliases = _pack_aliases(tree)

    def pack_ids(node: ast.AST) -> set[str]:
        named = {pid for n in ast.walk(node) if isinstance(n, ast.Name) for pid in aliases.get(n.id, ())}
        return _constant_pack_ids(node) | named

    found: list[tuple[str, int]] = []

    def visit(node: ast.AST, function: str) -> None:
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            function = node.name if function == "<module>" else f"{function}.{node.name}"
        iters = []
        if isinstance(node, (ast.For, ast.AsyncFor)):
            iters.append(node.iter)
        elif isinstance(node, ast.comprehension):
            iters.append(node.iter)
        for it in iters:
            if isinstance(it, (ast.Tuple, ast.List, ast.Set)) and len(set().union(*map(pack_ids, it.elts))) >= 2:
                found.append((function, it.lineno))
        for child in ast.iter_child_nodes(node):
            visit(child, function)

    visit(tree, "<module>")
    return found


class LoopCoverageTests(unittest.TestCase):
    """B4: the generic checks of the collective tests run on every built-in pack. Each generalised collection or
    table covers exactly :data:`BUILTIN_PACKS`, and a loop over a literal list of built-in packs is allowed only where
    :data:`PACK_SPECIFIC_LOOPS` says why it stays pack-specific."""

    def test_generalised_collections_and_tables_cover_every_builtin_pack(self) -> None:
        for module_name, attribute in GENERALISED:
            with self.subTest(module=module_name, attribute=attribute):
                value: Any = importlib.import_module(f"tests.mycelic.{module_name}")
                for part in attribute.split("."):
                    value = getattr(value, part)
                self.assertEqual(_pack_ids(value), set(BUILTIN_PACKS))

    def test_every_literal_pack_loop_is_allow_listed_with_a_reason(self) -> None:
        here = Path(__file__).resolve().parent
        self.assertEqual(set(LOOP_SCAN_FILES) & set(LOOP_SCAN_EXCLUDED), set())
        for name in (*LOOP_SCAN_FILES, *LOOP_SCAN_EXCLUDED):
            self.assertTrue((here / name).is_file(), name)
        found = {(name, function) for name in LOOP_SCAN_FILES
                 for function, _ in literal_pack_loops((here / name).read_text(encoding="utf-8"))}
        self.assertEqual(found - set(PACK_SPECIFIC_LOOPS), set())        # every literal loop is allow-listed
        self.assertEqual(set(PACK_SPECIFIC_LOOPS) - found, set())        # and no allow-list entry is stale
        self.assertTrue(all(reason for reason in PACK_SPECIFIC_LOOPS.values()))

    def test_the_scan_flags_literal_loops_and_passes_generalised_ones(self) -> None:
        a, b = BUILTIN_PACKS[:2]
        flagged = (f"for p in ({a!r}, {b!r}):\n    pass\n", f"x = [p for p in [{a!r}, {b!r}]]\n",
                   f"A = load({a!r})\nB = load({b!r})\ndef f():\n    for p in (A, B):\n        pass\n",
                   f"A, B = P[{a!r}], P[{b!r}]\nfor p, q in ((A, 1), (B, 2)):\n    pass\n",
                   # the pack is not a row's first element, or sits inside a call or behind an alias in the row
                   f"for name, pid in (('a-on', {a!r}), ('b-on', {b!r})):\n    pass\n",
                   f"for name, argv in (('a', f({a!r}, 1)), ('b', f({b!r}, records=3))):\n    pass\n",
                   f"A = load({a!r})\ndef f():\n    for c in (('x', A.f), ('y', g({b!r}))):\n        pass\n")
        for source in flagged:
            with self.subTest(source=source):
                self.assertTrue(literal_pack_loops(source))
        passed = ("for p in BUILTIN_PACKS:\n    pass\n", f"for p in ({a!r},):\n    pass\n",
                  f"x = {{{a!r}: 1, {b!r}: 2}}\nfor p in x:\n    pass\n", f"y = ({a!r}, {b!r})\n",
                  # cases of one pack, however many
                  f"for argv in (f({a!r}, 1), f({a!r}, 2), ('c', f({a!r}, 3))):\n    pass\n",
                  f"A = load({a!r})\nfor t in (A.f, A.g, ('x', {a!r})):\n    pass\n")
        for source in passed:
            with self.subTest(source=source):
                self.assertEqual(literal_pack_loops(source), [])
        self.assertEqual(literal_pack_loops(f"def g():\n    for p in ({a!r}, {b!r}):\n        pass\n"),
                         [("g", 2)])
        self.assertEqual(literal_pack_loops(f"class C:\n    def g(self):\n        x = [p for p in ({a!r}, {b!r})]\n"),
                         [("C.g", 3)])


if __name__ == "__main__":
    unittest.main()
