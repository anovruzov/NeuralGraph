"""Latency test L001, run 1 stopped at the plan job's guard (``lab.l1guard``): one refused value of MSHA's file was
found as a whole word in ``plan.json``, a file ``lab.plan`` writes from the request, the model manifest and the commit
before the MSHA file is read. This probe asks, with counts only, where such coincidences fall, before any rule change:

* the refused values of D002's split (c1 to c5), counted by column and by length; never a value;
* ``plan.json`` as run 1 wrote it (regenerated at run 1's commit from the same request): hits by path class, with
  indexes and model keys collapsed (``units[].run_id``, ``models.*.gguf.file``), and the refused columns that hold the
  values hit; never the value or the token;
* the files of an earlier lab run whose units ran small models on CPU (server logs, ``run.json``, ledgers, reports):
  files and hits by file class and by column;
* the lab's code and docs: files and hits by class and by column.

Then a second pass, after K14 (``CHOICE-L001.md``): the guard rebuilt as the lab now builds it, with the values that
``PLAN``'s own text holds left out of its value sets (``lab.l1guard.read_plan``, the same reading, with the sha256 of
``PLAN``'s bytes: the probe's own regeneration, which nothing else writes, so it is the sha256 ``lab.plan`` gives), and
the same counts again, each line prefixed ``after K14``: the counts left out (by set, and the refused values by column
and length), the refused values kept, ``plan.json`` whole and by path class, the earlier run's files and the lab's
code and docs.

With ``--run1-artifact``, each pass also counts the files of run 1's plan artifact (``lab-plan-38090725021-1``) by
class: the file the first guard withheld, the files it scanned, and the plan summary's two files, which no guard
scanned (the second guard of run 1's plan job read the withheld ``plan.json`` as a plan without L1 units and left the
directory alone before the upload).

The guard itself (``lab.l1guard.Guard``) does the matching, so a hit here is a hit there. Prints fixed lines with counts
and writes the same counts as JSON (the second pass under ``after_k14``). Nothing else: no value, token, file name,
index or line of any scanned file.

    python tools/l1/guard_probe.py --raw DIR --plan PLAN --artifacts DIR --out FILE [--run1-artifact DIR]
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import re
import sys
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from lab import l1guard as G  # noqa: E402
from lab.l1path import ARM, COMPANY, FETCH, SETTINGS, load_script  # noqa: E402
from mycelic.collective.onboard import draft as D  # noqa: E402
from mycelic.collective.onboard import score as S  # noqa: E402
from mycelic.collective.onboard.exports import read_export  # noqa: E402
from mycelic.collective.packs.canonical import folded  # noqa: E402

INDEX = re.compile(r"\[\d+\]")


def length_bucket(n: int) -> str:
    return str(n) if n < 8 else "8+"


class Split:
    """D002's split of the file (c1 to c5), read once, and for each refused value the refused columns it came from."""

    def __init__(self, raw: Path) -> None:
        settings_path = ROOT / SETTINGS
        self.settings, _ = S.load_settings(settings_path)
        roles = S.arm_roles(self.settings, ARM)
        refusal = self.settings["params"]["refusal"]
        fetch = load_script(FETCH)
        with tempfile.TemporaryDirectory(prefix="l1-probe-") as tmp:
            with contextlib.redirect_stdout(io.StringIO()):
                result = fetch.split(settings_path, raw, Path(tmp))
            companies = result["companies"]
            self.exports = [read_export(Path(tmp) / companies[label]["file"])
                            for label in sorted(companies, key=lambda c: int(c[1:]))]
            self.own = read_export(Path(tmp) / companies[COMPANY]["file"])
        self.columns: dict[str, set[str]] = {}
        for export in self.exports:
            for name in roles.forbidden:
                if not export.has(name):
                    continue
                for r in range(len(export)):
                    for v in D._cells(export.value(r, name)):        # noqa: SLF001 (the guard's own reading)
                        f = folded(v)
                        if (len(f) >= refusal["forbidden_inside_min_chars"] and any(ch.isalpha() for ch in f)) \
                                or len(f) >= refusal["reference_inside_min_chars"]:
                            self.columns.setdefault(f, set()).add(name)


def by_column_and_length(columns: dict[str, set[str]]) -> dict[str, dict[str, int]]:
    out: dict[str, Counter] = {}
    for value, cols in columns.items():
        for col in cols:
            out.setdefault(col, Counter())[length_bucket(len(value))] += 1
    return {c: dict(sorted(n.items())) for c, n in sorted(out.items())}


class Probe:
    """The guard (run 1's without a plan's text; K14's with it) plus, for each refused value the guard keeps, the
    refused columns it came from."""

    def __init__(self, split: Split, plan_text: str | None = None, plan_state: str = G.PLAN_UNREAD) -> None:
        self.guard = G.Guard(split.settings, split.exports, split.own, plan_text, plan_state)
        kept = {v for vs in self.guard.refused.by_run.values() for v in vs} | set(self.guard.refused.general)
        assert kept <= set(split.columns) and len(split.columns) - len(kept) == \
            self.guard.left_out["refused_value"], "the probe's values differ from the guard's"
        self.columns = {v: cols for v, cols in split.columns.items() if v in kept}
        self.left_out = {v: cols for v, cols in split.columns.items() if v not in kept}

    def summary(self) -> dict[str, Any]:
        return {"refused_values": len(self.columns), "operators": self.guard.counts["operators"],
                "by_column_and_length": by_column_and_length(self.columns)}

    def scan(self, text: str) -> dict[str, Any]:
        """The guard's hits on ``text``, with the refused columns of the refused values found (no value)."""
        hits = self.guard.hits(text)
        cols: Counter = Counter()
        for value in self.guard.refused.found_all(folded(text)):
            for col in self.columns.get(value, ()):
                cols[col] += 1
        return {"hits": hits, "refused_columns": dict(sorted(cols.items()))}


def leaves(doc: Any, path: str = "") -> list[tuple[str, str]]:
    """Every key and leaf of a JSON document as (path class, text); indexes and the keys under ``models`` collapsed."""
    out: list[tuple[str, str]] = []
    if isinstance(doc, dict):
        for k, v in doc.items():
            sub = f"{path}.*" if path.endswith("models") else f"{path}.{k}"
            out.append((f"{path}.(key)", str(k)))
            out.extend(leaves(v, sub))
    elif isinstance(doc, list):
        for v in doc:
            out.extend(leaves(v, f"{path}[]"))
    else:
        out.append((path or ".", json.dumps(doc) if not isinstance(doc, str) else doc))
    return out


def add(total: dict[str, Any], found: dict[str, Any]) -> None:
    total["files"] += 1
    if any(found["hits"].values()):
        total["files_with_hits"] += 1
    for k, n in found["hits"].items():
        total["hits"][k] = total["hits"].get(k, 0) + n
    for c, n in found["refused_columns"].items():
        total["refused_columns"][c] = total["refused_columns"].get(c, 0) + n


def empty() -> dict[str, Any]:
    return {"files": 0, "files_with_hits": 0, "hits": {}, "refused_columns": {}}


def artifact_class(rel: str) -> str:
    name = rel.rsplit("/", 1)[-1]
    if "/server/" in f"/{rel}" and name.endswith(".log"):
        return "server log"
    if name.endswith(".log"):
        return "other log"
    if name == "run.json":
        return "run.json"
    if name.endswith(".jsonl"):
        return "jsonl (ledgers, replies)"
    if name.startswith("report"):
        return "report"
    if name.startswith("summary"):
        return "summary"
    if name.endswith(".json"):
        return "other json"
    return "other"


def scan_tree(probe: Probe, root: Path, classify) -> dict[str, Any]:
    groups: dict[str, dict[str, Any]] = {}
    for path in sorted(p for p in root.rglob("*") if p.is_file() and not p.is_symlink()):
        rel = path.relative_to(root).as_posix()
        cls = classify(rel)
        if cls is None:
            continue
        text = path.read_bytes().decode("utf-8", errors="replace")
        add(groups.setdefault(cls, empty()), probe.scan(text))
    return dict(sorted(groups.items()))


def code_class(rel: str) -> str | None:
    if rel.startswith("lab/") and rel.endswith(".py"):
        return "lab code"
    if rel.startswith("docs/lab/") and rel.endswith(".md"):
        return "lab docs"
    if rel.startswith("docs/collective/L001/") and rel.endswith(".md"):
        return "L001 docs"
    return None


def run1_artifact(probe: Probe, root: Path, prefix: str) -> dict[str, Any]:
    """Run 1's plan artifact by class (each file's hits under ``probe``): the file the first guard withheld (a stub,
    which the guard never scans again), the plan summary's two files, which no guard scanned, and the rest, which the
    first guard scanned. Counts only."""
    groups: dict[str, dict[str, Any]] = {}
    for path in sorted(p for p in root.rglob("*") if p.is_file() and not p.is_symlink()):
        data = path.read_bytes()
        rel = path.relative_to(root).as_posix()
        if G.is_stub(data):
            cls = "withheld by the first guard"
        elif rel in ("summary.md", "summary.sources.json"):
            cls = "plan summary, never scanned"
        else:
            cls = "scanned by the first guard"
        found = probe.scan(data.decode("utf-8", errors="replace")) if not G.is_stub(data) else {
            "hits": dict.fromkeys(G.KINDS, 0), "refused_columns": {}}
        add(groups.setdefault(cls, empty()), found)
    out = dict(sorted(groups.items()))
    for name, g in out.items():
        print(line(f"{prefix}run 1 plan artifact", name, g), flush=True)
    return out


def line(prefix: str, name: str, g: dict[str, Any]) -> str:
    hits = " ".join(f"{k} {g['hits'].get(k, 0)}" for k in G.KINDS)
    cols = ", ".join(f"{c} {n}" for c, n in g["refused_columns"].items()) or "none"
    return f"l1 probe: {prefix} [{name}] files {g['files']} with hits {g['files_with_hits']} {hits}; " \
           f"refused columns: {cols}"


def values_lines(prefix: str, values: dict[str, Any]) -> None:
    print(f"l1 probe: {prefix}refused values {values['refused_values']} of {values['operators']} operators",
          flush=True)
    for col, lengths in values["by_column_and_length"].items():
        print(f"l1 probe: {prefix}values of {col} by length: "
              + ", ".join(f"{k} chars {n}" for k, n in lengths.items()), flush=True)


def scans(probe: Probe, plan_text: str, artifacts: Path, prefix: str) -> dict[str, Any]:
    """``plan.json`` whole and by path class, the earlier run's files and the lab's code and docs, each line led by
    ``prefix``; the counts."""
    out: dict[str, Any] = {}
    whole = probe.scan(plan_text)
    out["plan_whole"] = whole
    print(f"l1 probe: {prefix}plan.json whole: " + " ".join(f"{k} {whole['hits'][k]}" for k in G.KINDS)
          + "; refused columns: " + (", ".join(f"{c} {n}" for c, n in whole["refused_columns"].items()) or "none"),
          flush=True)
    classes: dict[str, dict[str, Any]] = {}
    for path, text in leaves(json.loads(plan_text)):
        found = probe.scan(text)
        if any(found["hits"].values()):
            add(classes.setdefault(INDEX.sub("[]", path), empty()), found)
    out["plan_classes"] = classes
    if not classes:
        print(f"l1 probe: {prefix}plan.json: no leaf holds a hit on its own", flush=True)
    for name, g in sorted(classes.items()):
        print(line(f"{prefix}plan.json path", name, g), flush=True)

    art = scan_tree(probe, artifacts, artifact_class)
    out["artifacts"] = art
    for name, g in art.items():
        print(line(f"{prefix}earlier run", name, g), flush=True)

    code = scan_tree(probe, ROOT, code_class)
    out["code"] = code
    for name, g in code.items():
        print(line(f"{prefix}repository", name, g), flush=True)
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--raw", required=True)
    p.add_argument("--plan", required=True)
    p.add_argument("--artifacts", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--run1-artifact", help="run 1's plan artifact (lab-plan-38090725021-1), unpacked")
    a = p.parse_args(argv)
    raw = Path(a.raw)
    if not (raw / "Accidents.zip").is_file():
        with contextlib.redirect_stdout(io.StringIO()):
            load_script(FETCH).download(raw)
    split = Split(raw)
    probe = Probe(split)
    out: dict[str, Any] = {"values": probe.summary()}
    values_lines("", out["values"])
    plan_bytes = Path(a.plan).read_bytes()
    plan_text = plan_bytes.decode("utf-8")
    out.update(scans(probe, plan_text, Path(a.artifacts), ""))
    if a.run1_artifact:
        out["run1_plan_artifact"] = run1_artifact(probe, Path(a.run1_artifact), "")

    # K14: the guard as the lab now builds it, with the values the plan's own text holds left out (the guard's reading,
    # with the sha256 of the plan as lab.plan wrote it: the probe's own regeneration, which nothing else writes)
    prefix = "after K14: "
    k14_text, state = G.read_plan(Path(a.plan), hashlib.sha256(plan_bytes).hexdigest())
    after = Probe(split, k14_text, state)
    left = after.guard.left_out
    if not after.guard.plan_read:
        print(f"l1 probe: {prefix}nothing left out: the plan is {after.guard.plan_state}", flush=True)
    print(f"l1 probe: {prefix}left out: " + " ".join(f"{k} {n}" for k, n in left.items()), flush=True)
    by_col = by_column_and_length(after.left_out)
    print(f"l1 probe: {prefix}refused values left out by column and length: "
          + ("; ".join(f"{c} " + ", ".join(f"{k} chars {n}" for k, n in lengths.items())
                       for c, lengths in by_col.items()) or "none"), flush=True)
    k14: dict[str, Any] = {"plan_read": after.guard.plan_read, "plan_state": after.guard.plan_state,
                           "left_out": dict(left), "left_out_by_column_and_length": by_col, "values": after.summary()}
    values_lines(prefix, k14["values"])
    k14.update(scans(after, plan_text, Path(a.artifacts), prefix))
    if a.run1_artifact:
        k14["run1_plan_artifact"] = run1_artifact(after, Path(a.run1_artifact), prefix)
    out["after_k14"] = k14

    Path(a.out).write_text(json.dumps(out, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print("l1 probe: done", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
