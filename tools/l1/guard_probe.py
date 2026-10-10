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

The guard itself (``lab.l1guard.Guard``) does the matching, so a hit here is a hit there. Prints fixed lines with counts
and writes the same counts as JSON. Nothing else: no value, token, file name, index or line of any scanned file.

    python tools/l1/guard_probe.py --raw DIR --plan PLAN --artifacts DIR --out FILE
"""
from __future__ import annotations

import argparse
import contextlib
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


class Probe:
    """The guard of run 1 plus, for each refused value the guard keeps, the refused columns it came from."""

    def __init__(self, raw: Path) -> None:
        settings_path = ROOT / SETTINGS
        settings, _ = S.load_settings(settings_path)
        roles = S.arm_roles(settings, ARM)
        refusal = settings["params"]["refusal"]
        fetch = load_script(FETCH)
        with tempfile.TemporaryDirectory(prefix="l1-probe-") as tmp:
            with contextlib.redirect_stdout(io.StringIO()):
                result = fetch.split(settings_path, raw, Path(tmp))
            companies = result["companies"]
            exports = [read_export(Path(tmp) / companies[label]["file"])
                       for label in sorted(companies, key=lambda c: int(c[1:]))]
            own = read_export(Path(tmp) / companies[COMPANY]["file"])
        self.guard = G.Guard(settings, exports, own)
        self.columns: dict[str, set[str]] = {}
        for export in exports:
            for name in roles.forbidden:
                if not export.has(name):
                    continue
                for r in range(len(export)):
                    for v in D._cells(export.value(r, name)):        # noqa: SLF001 (the guard's own reading)
                        f = folded(v)
                        if (len(f) >= refusal["forbidden_inside_min_chars"] and any(ch.isalpha() for ch in f)) \
                                or len(f) >= refusal["reference_inside_min_chars"]:
                            self.columns.setdefault(f, set()).add(name)
        assert set(self.columns) == {v for vs in self.guard.refused.by_run.values() for v in vs} | set(
            self.guard.refused.general), "the probe's values differ from the guard's"

    def summary(self) -> dict[str, Any]:
        by_column: dict[str, Counter] = {}
        for value, cols in self.columns.items():
            for col in cols:
                by_column.setdefault(col, Counter())[length_bucket(len(value))] += 1
        return {"refused_values": len(self.columns), "operators": self.guard.counts["operators"],
                "by_column_and_length": {c: dict(sorted(n.items())) for c, n in sorted(by_column.items())}}

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


def line(prefix: str, name: str, g: dict[str, Any]) -> str:
    hits = " ".join(f"{k} {g['hits'].get(k, 0)}" for k in G.KINDS)
    cols = ", ".join(f"{c} {n}" for c, n in g["refused_columns"].items()) or "none"
    return f"l1 probe: {prefix} [{name}] files {g['files']} with hits {g['files_with_hits']} {hits}; " \
           f"refused columns: {cols}"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--raw", required=True)
    p.add_argument("--plan", required=True)
    p.add_argument("--artifacts", required=True)
    p.add_argument("--out", required=True)
    a = p.parse_args(argv)
    raw = Path(a.raw)
    if not (raw / "Accidents.zip").is_file():
        with contextlib.redirect_stdout(io.StringIO()):
            load_script(FETCH).download(raw)
    probe = Probe(raw)
    out: dict[str, Any] = {"values": probe.summary()}
    print(f"l1 probe: refused values {out['values']['refused_values']} of {out['values']['operators']} operators",
          flush=True)
    for col, lengths in out["values"]["by_column_and_length"].items():
        print(f"l1 probe: values of {col} by length: "
              + ", ".join(f"{k} chars {n}" for k, n in lengths.items()), flush=True)

    plan_text = Path(a.plan).read_text(encoding="utf-8")
    whole = probe.scan(plan_text)
    out["plan_whole"] = whole
    print("l1 probe: plan.json whole: " + " ".join(f"{k} {whole['hits'][k]}" for k in G.KINDS)
          + "; refused columns: " + (", ".join(f"{c} {n}" for c, n in whole["refused_columns"].items()) or "none"),
          flush=True)
    classes: dict[str, dict[str, Any]] = {}
    for path, text in leaves(json.loads(plan_text)):
        found = probe.scan(text)
        if any(found["hits"].values()):
            add(classes.setdefault(INDEX.sub("[]", path), empty()), found)
    out["plan_classes"] = classes
    if not classes:
        print("l1 probe: plan.json: no leaf holds a hit on its own", flush=True)
    for name, g in sorted(classes.items()):
        print(line("plan.json path", name, g), flush=True)

    art = scan_tree(probe, Path(a.artifacts), artifact_class)
    out["artifacts"] = art
    for name, g in art.items():
        print(line("earlier run", name, g), flush=True)

    code = scan_tree(probe, ROOT, code_class)
    out["code"] = code
    for name, g in code.items():
        print(line("repository", name, g), flush=True)

    Path(a.out).write_text(json.dumps(out, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print("l1 probe: done", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
