"""Honesty rules enforced statically (DESIGN.md §1, MODULE_SPEC.md).

* Only world.py, evaluate.py, attacks.py, failures.py and routing.py (and experiments/) may
  import `Effect` / `GroundTruth` from world or touch `.effects`, `.p_true`, `.true_labels`.
* No module or experiment hard-codes effect ids ("GLO0012", "CRO0003", "LOC0040", ...).
* The record view handed to systems carries no truth fields.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "mycelic_bench"
ALLOWED_TRUTH_MODULES = {"world.py", "evaluate.py", "attacks.py", "failures.py", "routing.py"}
FORBIDDEN_NAMES = {"Effect", "GroundTruth"}
FORBIDDEN_ATTRS = {"effects", "p_true", "true_labels", "effect_active_mask", "effect_matches"}
EFFECT_ID_RE = re.compile(r"\b(?:GLO|CRO|LOC|TMP|CON|DEC|BAS)\d{3,}\b")


def python_files(*dirs: Path) -> list[Path]:
    out: list[Path] = []
    for d in dirs:
        if d.exists():
            out.extend(p for p in d.rglob("*.py") if "__pycache__" not in p.parts)
    return sorted(out)


def test_source_tree_present() -> None:
    files = python_files(SRC)
    assert {p.name for p in files} >= {"world.py", "evaluate.py", "hierarchy.py", "hypothesis.py", "agents.py", "runner.py"}


@pytest.mark.parametrize("path", python_files(SRC), ids=lambda p: p.name)
def test_no_ground_truth_access_outside_allowed_modules(path: Path) -> None:
    if path.name in ALLOWED_TRUTH_MODULES:
        pytest.skip("allowed to read ground truth")
    tree = ast.parse(path.read_text(), filename=str(path))
    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            mod = (node.module or "").split(".")[-1]
            if mod == "world":
                names = {a.name for a in node.names}
                if names & FORBIDDEN_NAMES:
                    violations.append(f"{path.name}:{node.lineno} imports {sorted(names & FORBIDDEN_NAMES)} from world")
                if "*" in names:
                    violations.append(f"{path.name}:{node.lineno} star-imports world")
        elif isinstance(node, ast.Attribute):
            if node.attr in FORBIDDEN_ATTRS:
                violations.append(f"{path.name}:{node.lineno} accesses .{node.attr}")
            if node.attr in FORBIDDEN_NAMES and isinstance(node.value, ast.Name) and node.value.id == "world":
                violations.append(f"{path.name}:{node.lineno} accesses world.{node.attr}")
        elif isinstance(node, ast.Subscript):
            # getattr-style / dict-style evasion: obj["p_true"], obj["effects"]
            sl = node.slice
            if isinstance(sl, ast.Constant) and isinstance(sl.value, str) and sl.value in FORBIDDEN_ATTRS:
                violations.append(f"{path.name}:{node.lineno} subscripts [{sl.value!r}]")
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "getattr":
            if len(node.args) >= 2 and isinstance(node.args[1], ast.Constant) and node.args[1].value in FORBIDDEN_ATTRS:
                violations.append(f"{path.name}:{node.lineno} getattr({node.args[1].value!r})")
    assert not violations, "\n".join(violations)


@pytest.mark.parametrize(
    "path",
    [p for p in python_files(SRC, ROOT / "experiments", ROOT / "scripts", ROOT / "data" / "generators")
     if p.name not in ("world.py", "evaluate.py")],
    ids=lambda p: str(p.relative_to(ROOT)),
)
def test_no_hard_coded_effect_ids(path: Path) -> None:
    tree = ast.parse(path.read_text(), filename=str(path))
    hits = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and EFFECT_ID_RE.search(node.value):
            hits.append(f"{path.relative_to(ROOT)}:{node.lineno} {node.value!r}")
    assert not hits, "hard-coded effect ids:\n" + "\n".join(hits)


def test_experiments_do_not_hand_truth_to_systems() -> None:
    """experiments/ may read ground truth for evaluation, but must never pass `world.effects`,
    `world.p_true` or `world.true_labels` into run_system / Hierarchy / a baseline call."""
    hits = []
    for path in python_files(ROOT / "experiments"):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fname = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
            if fname not in ("run_system", "run_hierarchy", "run_baseline", "Hierarchy"):
                continue
            for arg in list(node.args) + [k.value for k in node.keywords]:
                for sub in ast.walk(arg):
                    if isinstance(sub, ast.Attribute) and sub.attr in FORBIDDEN_ATTRS:
                        hits.append(f"{path.relative_to(ROOT)}:{node.lineno} passes .{sub.attr} to {fname}")
    assert not hits, "\n".join(hits)


def test_record_view_exposes_no_truth() -> None:
    src = (SRC / "world.py").read_text()
    tree = ast.parse(src)
    cls = next(n for n in ast.walk(tree) if isinstance(n, ast.ClassDef) and n.name == "RecordView")
    assigned = set()
    for node in ast.walk(cls):
        if isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Store) and isinstance(node.value, ast.Name) and node.value.id == "self":
            assigned.add(node.attr)
    assert not (assigned & {"effects", "p_true", "true_labels", "canary", "is_copy", "origin_worker"}), assigned


def test_every_system_uses_the_shared_search() -> None:
    """Systems must call hypothesis.search / rate_test rather than a private statistical method."""
    for name in ("hierarchy.py", "baselines.py"):
        path = SRC / name
        if not path.exists():
            continue
        src = path.read_text()
        assert re.search(r"from \.hypothesis import .*\b(search|rate_test)\b", src), f"{name} does not import the shared search"
        assert "scipy.stats" not in src, f"{name} runs its own statistics instead of hypothesis.search"
