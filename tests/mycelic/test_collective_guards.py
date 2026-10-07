"""Repository guards for the collective layer. Later gates extend the lists at the top of this module.

* ImportGuardTests: the fabric core imports no model client and nothing from ``mycelic.collective`` (static AST
  check, including ``importlib.import_module``/``__import__`` string constants, plus fresh-interpreter checks).
* StdlibOnlyTests: every collective module and CLI runs under ``python -S`` (no site-packages).
* NoModelNamesTests: no model-family name in collective code, docs or tests (the matcher holds digests only).
* DeterminismTests: no wall clock or unseeded randomness in the modules that must replay bit for bit.
* RunbookCommandTests: every command in ``docs/collective/RUNBOOK.md`` runs with ``--dry-run`` appended, with the
  network blocked, and creates nothing.
* DomainLiteralTests: the generic collective code holds no domain-pack literal (G2).
* HqImportGuardTests: the HQ detection modules (``mycelic/collective/detect``) import nothing site-side, no harness and
  no model client (static check and a fresh interpreter) (G4).
* ClockEntropyTests: every ``detect`` module is on the determinism list and reads no clock or entropy (G4).

``forbidden_imports``, ``model_name_hits``, ``nondeterminism`` and ``domain_literal_hits`` are importable for
reviewers' probes.
"""
from __future__ import annotations

import ast
import codecs
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# --------------------------------------------------------------------------------------------------- lists

CORE_FILES = tuple(ROOT / "mycelic" / f"{name}.py"
                   for name in ("service", "aggregation", "store", "transport", "lineage"))
FORBIDDEN_IMPORTS = ("mycelic.collective", "openai", "anthropic", "ollama", "llama_cpp", "vllm", "transformers",
                     "torch", "NeuralGraph.llm_backend", "NeuralGraph.chat_memory.llm")
# importing mycelic.service loads NeuralGraph.chat_memory (and with it chat_memory.llm) through mycelic.retrieval;
# the fresh-interpreter check therefore omits chat_memory.llm and checks that chain separately
FORBIDDEN_LOADED = tuple(p for p in FORBIDDEN_IMPORTS if p != "NeuralGraph.chat_memory.llm")
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
)
DETECT_DIR = ROOT / "mycelic" / "collective" / "detect"
# what the HQ side may never import: the site's raw-record modules, the extractor, the world generator, the harness
# and evaluation modules, any inference module (egress pulls in only inference.errors), and every model client
HQ_FORBIDDEN = ("mycelic.collective.edge.records", "mycelic.collective.edge.site", "mycelic.collective.edge.extract",
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
}

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


def forbidden_imports(source: str, module: str, prefixes: tuple[str, ...] = FORBIDDEN_IMPORTS) -> list[str]:
    """Every import of a module under ``prefixes`` in ``source`` (the text of ``module``), and every unverifiable
    one."""
    tree = ast.parse(source)
    dynamic = set(_DYNAMIC_NAMES)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "importlib":
            dynamic.update(a.asname or a.name for a in node.names if a.name in _DYNAMIC_NAMES)
    hits: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            hits += [f"line {node.lineno}: import {a.name}" for a in node.names if _forbidden(a.name, prefixes)]
        elif isinstance(node, ast.ImportFrom):
            base = _resolve(module, node.level, node.module)
            if base is None:
                hits.append(f"line {node.lineno}: unresolvable relative import")
                continue
            for name in [base] + [f"{base}.{a.name}" for a in node.names]:
                if _forbidden(name, prefixes):
                    hits.append(f"line {node.lineno}: from-import of {name}")
                    break
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
    return hits


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
)
NEGATIVE_IMPORTS = (
    "import json",
    "from .store import MycelicStore",
    "from .models import Memory",
    "from NeuralGraph.chat_memory.textutil import tokenize",
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
        source = (ROOT / "mycelic" / "lineage.py").read_text(encoding="utf-8")
        for line in ("import openai", "import importlib\nimportlib.import_module('vllm')"):
            with self.subTest(line=line):
                self.assertTrue(forbidden_imports(source + "\n" + line + "\n", "mycelic.lineage"))

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

    def test_core_without_service_loads_no_chat_memory(self) -> None:
        loaded = self.loaded(("mycelic.store", "mycelic.transport", "mycelic.lineage", "mycelic.aggregation"))
        self.assertEqual([m for m in loaded if m.startswith("NeuralGraph.chat_memory")], [])
        self.assertEqual([m for m in loaded if _forbidden(m)], [])


# --------------------------------------------------------------------------------------------------- stdlib only

def _run_without_site_packages(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, "-S", *args], cwd=ROOT, capture_output=True, text=True, timeout=60,
                          env={"PYTHONPATH": str(ROOT), "PATH": "/usr/bin:/bin"})


class StdlibOnlyTests(unittest.TestCase):
    def test_every_collective_module_imports_without_site_packages(self) -> None:
        self.assertEqual(len(STDLIB_ONLY_MODULES), 39)
        code = "import importlib\n" + "".join(f"importlib.import_module({m!r})\n" for m in STDLIB_ONLY_MODULES)
        r = _run_without_site_packages("-c", code)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_every_cli_answers_help_without_site_packages(self) -> None:
        for module, *sub in CLI_MODULES:
            with self.subTest(module=module, sub=sub):
                r = _run_without_site_packages("-m", module, *sub, "--help")
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertIn("usage:", r.stdout)

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

    def test_no_detect_module_imports_a_forbidden_module(self) -> None:
        for path in self.detect_files():
            with self.subTest(module=path.name):
                module = f"mycelic.collective.detect.{path.stem}"
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


class ClockEntropyTests(unittest.TestCase):
    def test_every_detect_module_is_on_the_determinism_list_with_no_hits(self) -> None:
        files = sorted(DETECT_DIR.glob("*.py"))
        self.assertEqual(len(files), 5)
        for path in files:
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
DOMAIN_SCAN_EXCLUDED = ("mycelic/collective/packs/data",)
OPENFDA_LITERAL_SCOPE = ("mycelic/collective/packs/*.py", "mycelic/collective/edge/*.py",
                         "mycelic/collective/experiments/e1_extract.py")
# "text" is the openFDA narrative field name and also the model payload key the G2 brief fixes ({language, text});
# it is the only exemption, and test_openfda_check_is_exact proves every other segment is still flagged
OPENFDA_SEGMENT_EXEMPT = ("text",)
ILLUSTRATIVE_CODE_MARK = "ILL-"


def pack_terms() -> set[str]:
    """Type, predicate, code, rule, template, follow-up type and role ids of both built-in packs."""
    terms: set[str] = set()
    for pack in ("device_quality", "claims_integrity"):
        d = PACK_DATA / pack
        vocabulary = json.loads((d / "vocabulary.json").read_text(encoding="utf-8"))
        followups = json.loads((d / "followups.json").read_text(encoding="utf-8"))
        terms |= set(vocabulary["entity_types"]) | set(vocabulary["predicates"])
        terms |= set(json.loads((d / "codes.json").read_text(encoding="utf-8")))
        terms |= set(json.loads((d / "rules.json").read_text(encoding="utf-8"))["rules"])
        terms |= set(json.loads((d / "questions.json").read_text(encoding="utf-8"))["templates"])
        terms |= set(followups["types"]) | set(followups["roles"])
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
    return out


class DomainLiteralTests(unittest.TestCase):
    """No pack term (type, predicate, code, rule, template, follow-up type or role id of either built-in pack)
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

    def test_terms_cover_both_packs(self) -> None:
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


if __name__ == "__main__":
    unittest.main()
