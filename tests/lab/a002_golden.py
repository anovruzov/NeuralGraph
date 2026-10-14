"""A002's golden outputs (``docs/collective/A003/CHOICE-A003.md``, "The build plan", step (0), and C30): the j3
template dry run's plan summary, ``report.json`` and ``report.md``, recorded on the build's base commit and pinned byte
for byte after each build, as D003's build recorded D002's (``tests/onboard/test_d002_reproducible.py``).

    python -m tests.lab.a002_golden record --out tests/lab/data/a002_golden

runs the steps the rule names, ``python -m tests.lab.j3_data --out DIR`` then ``python -m lab.dryrun --request
lab/templates/accuracy-002.json --out OUT --j3-data DIR`` (in subprocesses, from the repository), and writes the three
outputs after :func:`normalise_json` and :func:`normalise_md`; ``tests/lab/test_lab_jt.py`` (``A002GoldenTests``)
runs the same steps on the code under test and compares the bytes. The files under ``tests/lab/data/a002_golden/``
were recorded on the deployment branch's head ``ed96530`` (its lab code is ``5a8f980``'s, the commit the rule names),
before any A003 code existed.

What is pinned is every byte of the three outputs except the values below, each replaced by a fixed token. Nothing
else is excluded, and the test fails on any other difference.

* **The synthetic archive's sha256** (``complaint_file_sha256`` in ``report.json``; the ``complaint file sha``
  printed by both summaries; ``<archive>``). ``tests.lab.j2_data.archive`` writes a zip whose one entry carries the
  time it was written (``zipfile.ZipFile.writestr`` stamps ``time.localtime()``), so the archive's bytes, and only
  they, differ from one generation of the test data to the next; the records inside are the same, and so are the
  labels', the questions' and the chain file's hashes, which stay pinned.
* **The values that name the code** (``<commit>``, ``<plan>``, ``<prereg>``): the plan's ``git_sha`` and the summaries'
  ``Commit:`` lines; the plan's sha256 (``report.json``'s ``plan.sha256``, the report's ``Plan:``), since the plan
  holds ``git_sha``; and the ``prereg sha`` of ``prereg/j3/prereg.json`` printed by the plan summary, which holds
  ``code_commit`` and ``code_dirty`` and the labels record with the archive's sha256. They differ at every commit by
  construction, as D003's pin left out ``code_commit``. A checkout without git metadata (a ``git archive`` copy)
  prints ``unknown`` for the commit in the summaries' ``Commit:`` lines, and the same token replaces it.
* **What the machine and the clock give** (``<host>``, ``<time>``): every ``wall_s``, every latency figure
  (``p50_ms``, ``p95_ms``, ``latency_ms_p50``, ``latency_ms_p95``, ``latency_ms_by_task``), every ``host`` block and
  ``cpu_model``; in the markdown, every table cell under a header ``wall seconds``, ``CPU``, ``median ms`` or
  ``ninety-fifth percentile ms``, and the ``- CPU models:`` line. They are measurements of the sandbox, not of the
  code: two dry runs on one commit differ in them.

One substitution is not an exclusion: the plan summary's models table prints each model's repository and file name
from ``lab/models.json``, which hold a model-family name that no file under ``tests/lab/`` may hold
(``tests/lab/test_lab_guards.py``, ``test_no_model_names``). :func:`normalise_md` writes each manifest value in a
code span as ``<repository of KEY>`` or ``<file of KEY>``, KEY the manifest's model key: an exact substitution keyed by
the manifest, so the cells stay pinned to the manifest's values.

``report.json`` is written as canonical JSON (``jsonio.canonical_dumps``); :func:`normalise_json` checks that and
writes the normalised document back the same way, so the comparison is byte for byte on canonical bytes.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from mycelic.collective.jsonio import canonical_dumps

ROOT = Path(__file__).resolve().parents[2]
GOLDEN = ROOT / "tests" / "lab" / "data" / "a002_golden"
TEMPLATE = "lab/templates/accuracy-002.json"
MANIFEST = ROOT / "lab" / "models.json"
OUTPUTS = {"summary.md": "plan/summary.md", "report.json": "report/report.json", "report.md": "report/report.md"}
RECORDED_AT = "ed96530"
ARCHIVE, COMMIT, PLAN, PREREG, HOST, TIME = "<archive>", "<commit>", "<plan>", "<prereg>", "<host>", "<time>"
JSON_TIME_KEYS = frozenset({"wall_s", "p50_ms", "p95_ms", "latency_ms_p50", "latency_ms_p95", "latency_ms_by_task"})
JSON_HOST_KEYS = frozenset({"host", "cpu_model"})
MD_COLUMNS = {"wall seconds": TIME, "CPU": HOST, "median ms": TIME, "ninety-fifth percentile ms": TIME}
MD_LINES = (
    (re.compile(r"^(- Commit: `)(?:[0-9a-f]{40}|unknown)(`)$"), rf"\g<1>{COMMIT}\g<2>"),
    (re.compile(r"^(- Plan: `)[0-9a-f]{12}(`; Commit: `)(?:[0-9a-f]{40}|unknown)(`; )"),
     rf"\g<1>{PLAN}\g<2>{COMMIT}\g<3>"),
    (re.compile(r"^- CPU models: .*$"), f"- CPU models: {HOST}"),
)
MD_SPANS = (
    (re.compile(r"(prereg sha `)[0-9a-f]{12}(`)"), rf"\g<1>{PREREG}\g<2>"),
    (re.compile(r"(complaint file sha `)[0-9a-f]{12}(`)"), rf"\g<1>{ARCHIVE}\g<2>"),
)


class GoldenError(AssertionError):
    pass


def _walk(node: Any) -> Any:
    if isinstance(node, dict):
        out = {}
        for key, value in node.items():
            if key == "complaint_file_sha256":
                out[key] = ARCHIVE
            elif key == "git_sha":
                out[key] = COMMIT
            elif key in JSON_TIME_KEYS:
                out[key] = TIME
            elif key in JSON_HOST_KEYS:
                out[key] = HOST
            else:
                out[key] = _walk(value)
        return out
    if isinstance(node, list):
        return [_walk(item) for item in node]
    return node


def normalise_json(data: bytes) -> bytes:
    """``report.json`` with the values of the module docstring replaced; refuses a file that is not canonical."""
    doc = json.loads(data)
    if (canonical_dumps(doc) + "\n").encode("utf-8") != data:
        raise GoldenError("report.json is not canonical JSON")
    out = _walk(doc)
    if isinstance(out.get("plan"), dict) and "sha256" in out["plan"]:
        out["plan"]["sha256"] = PLAN
    return (canonical_dumps(out) + "\n").encode("utf-8")


def _cells(line: str) -> list[str] | None:
    if not (line.startswith("| ") and line.endswith(" |")):
        return None
    return line[2:-2].split(" | ")


def model_names(manifest: Path = MANIFEST) -> list[tuple[str, str]]:
    """``(code span, token)`` for every gguf model's repository and file name in the manifest."""
    doc = json.loads(manifest.read_text(encoding="utf-8"))
    pairs = []
    for key, entry in sorted(doc["models"].items()):
        gguf = entry.get("gguf") if isinstance(entry, dict) else None
        if isinstance(gguf, dict):
            pairs += [(f"`{gguf['repo']}`", f"`<repository of {key}>`"), (f"`{gguf['file']}`", f"`<file of {key}>`")]
    return pairs


def normalise_md(text: str) -> str:
    """A summary with the values of the module docstring replaced: the named lines and spans, every cell of a
    table column whose header is one of :data:`MD_COLUMNS`, and the manifest's repository and file names."""
    for span, token in model_names():
        text = text.replace(span, token)
    out: list[str] = []
    header: list[str] | None = None
    for line in text.split("\n"):
        cells = _cells(line)
        if cells is None:
            header = None
            for pattern, repl in MD_LINES:
                line = pattern.sub(repl, line)
            for pattern, repl in MD_SPANS:
                line = pattern.sub(repl, line)
            out.append(line)
            continue
        if header is None:
            header = cells
        elif set(cells) != {"---"} and len(cells) == len(header):
            cells = [MD_COLUMNS.get(name, cell) for name, cell in zip(header, cells)]
            line = "| " + " | ".join(cells) + " |"
        out.append(line)
    return "\n".join(out)


def normalise(name: str, data: bytes) -> bytes:
    if name.endswith(".json"):
        return normalise_json(data)
    return normalise_md(data.decode("utf-8")).encode("utf-8")


def dry_run(work: Path) -> tuple[subprocess.CompletedProcess[str], Path]:
    """The rule's two commands in ``work``: the j3 test data, then the template's dry run; the dry run's process and
    its ``--out`` directory."""
    data, out = work / "j3-data", work / "dry"
    made = subprocess.run([sys.executable, "-m", "tests.lab.j3_data", "--out", str(data)], cwd=ROOT,
                          capture_output=True, text=True, timeout=600, stdin=subprocess.DEVNULL)
    if made.returncode != 0:
        raise GoldenError(f"tests.lab.j3_data failed: {made.stderr[-2000:]}")
    proc = subprocess.run([sys.executable, "-m", "lab.dryrun", "--request", TEMPLATE, "--out", str(out),
                           "--j3-data", str(data)], cwd=ROOT, capture_output=True, text=True, timeout=1800,
                          stdin=subprocess.DEVNULL)
    return proc, out


def outputs(out: Path) -> dict[str, bytes]:
    """The three outputs of a dry run's ``--out`` directory, normalised, by golden file name."""
    return {name: normalise(name, (out / rel).read_bytes()) for name, rel in OUTPUTS.items()}


def record(golden: Path) -> dict[str, bytes]:
    with tempfile.TemporaryDirectory(prefix="a002-golden-") as tmp:
        proc, out = dry_run(Path(tmp))
        if proc.returncode != 0:
            raise GoldenError(f"the dry run exited {proc.returncode}: {proc.stdout[-2000:]} {proc.stderr[-2000:]}")
        found = outputs(out)
    golden.mkdir(parents=True, exist_ok=True)
    for name, data in found.items():
        (golden / name).write_bytes(data)
    return found


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m tests.lab.a002_golden", description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="command", required=True)
    rec = sub.add_parser("record", help="run the j3 template dry run and write its normalised outputs")
    rec.add_argument("--out", default=str(GOLDEN))
    args = p.parse_args(argv)
    found = record(Path(args.out))
    print(json.dumps({name: len(data) for name, data in found.items()}, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
