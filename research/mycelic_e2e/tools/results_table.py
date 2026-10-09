"""Recompute the reported numbers from raw run directories (no hand transcription).

Usage::

    python -m research.mycelic_e2e.tools.results_table RUN_DIR [RUN_DIR ...] [--json OUT.json]

For each run directory it re-runs the isolated scorer (``bench/score.py:score_run``) on the asker views and reads the
architecture-gate report the run wrote (``arch_gate.json``; recomputed when ``--regate`` is given). It prints one
markdown row per run: split, size, seed, mode/variant, ablation, provider label, code revision, n, correct, accuracy,
95 % Wilson interval, disclosures, gate validity and failed gates, and the per-class accuracies.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from research.mycelic_e2e.bench import score as S


def _gate(run: Path, regate: bool) -> dict[str, Any]:
    if regate:
        from research.mycelic_e2e.bench import arch_gate as G
        rep = G.check(run)
        return rep if isinstance(rep, dict) else json.loads(json.dumps(rep, default=lambda o: getattr(o, "__dict__", str(o))))
    p = run / "arch_gate.json"
    return json.loads(p.read_text()) if p.exists() else {}


def row(run: Path, *, regate: bool = False) -> dict[str, Any]:
    rs = S.score_run(run)
    if hasattr(rs, "to_dict"):
        rs = rs.to_dict()
    d = rs if isinstance(rs, dict) else json.loads(json.dumps(rs, default=lambda o: getattr(o, "__dict__", str(o))))
    man = json.loads((run / "run_manifest.json").read_text()) if (run / "run_manifest.json").exists() else {}
    gate = _gate(run, regate)
    return {
        "run": run.name, "split": d.get("split"), "size": d.get("size") or man.get("size"), "seed": d.get("seed") or man.get("seed"),
        "mode": d.get("mode"), "ablation": d.get("ablation"), "provider": d.get("provider_label"),
        "revision": man.get("git_sha") or man.get("revision") or man.get("code_revision"),
        "n": d.get("n"), "correct": d.get("correct"), "accuracy": d.get("accuracy"), "ci": [d.get("ci_low"), d.get("ci_high")],
        "disclosures": (d.get("disclosures") or {}).get("total") if isinstance(d.get("disclosures"), dict) else d.get("disclosures"),
        "gate_valid": gate.get("valid") if gate else None, "gate_failed": gate.get("failed") if gate else None,
        "per_class": {c["cls"]: (c.get("correct"), c.get("n")) for c in (d.get("per_class") or []) if isinstance(c, dict)},
        "fake_py_sha256": man.get("fake_py_sha256"), "tasks_sha256": man.get("tasks_sha256"), "sources_sha256": man.get("sources_sha256"),
        "peak_rss_mb": man.get("peak_rss_mb"), "max_open_holders": man.get("max_open_holders"), "started_utc": man.get("started_utc"),
        "finished_utc": man.get("finished_utc"),
    }


def markdown(rows: list[dict[str, Any]]) -> str:
    head = "| run | split | size/seed | mode | ablation | n | correct | accuracy | 95% Wilson | disclosures | gate |\n|---|---|---|---|---|---:|---:|---:|---|---:|---|"
    out = [head]
    for r in rows:
        ci = r["ci"]
        gate = "valid" if r["gate_valid"] else (f"INVALID {r['gate_failed']}" if r["gate_valid"] is False else "not run")
        out.append(f"| {r['run']} | {r['split']} | {r['size']}/{r['seed']} | {r['mode']} | {r['ablation'] or '-'} | {r['n']} | {r['correct']} | "
                   f"{r['accuracy']:.3f} | [{ci[0]:.3f}, {ci[1]:.3f}] | {r['disclosures']} | {gate} |")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("runs", nargs="+")
    ap.add_argument("--json")
    ap.add_argument("--regate", action="store_true")
    ns = ap.parse_args(argv)
    rows = [row(Path(r), regate=ns.regate) for r in ns.runs]
    print(markdown(rows))
    for r in rows:
        print(f"\n{r['run']} per class: " + ", ".join(f"{k} {c}/{n}" for k, (c, n) in sorted(r["per_class"].items())))
    if ns.json:
        Path(ns.json).write_text(json.dumps(rows, indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
