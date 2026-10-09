"""Markdown report: system vs centralized baseline vs ablations (PLAN_v1 §D WP3).

``render(run_dir)`` summarizes the run in ``run_dir`` and every sibling run directory with the same seed, size and split (the
baseline and the ablations are separate runs, normally ``<run>-baseline``, ``<run>-A1`` ...), scoring each with
:func:`bench.score.score_run` and reading its gate from ``arch_gate.json`` when present. The table always shows the provider
label, the gate status (an INVALID gate is printed, never hidden), n, correct, accuracy and the 95 % Wilson interval. The report
is written to ``<run_dir>/report.md`` and returned.

``finalize(run_dir, ...)`` is the one-call wrap-up for a finished run: score it, run the gate, write ``score.json`` and
``arch_gate.json`` into the directory and (optionally) append the ledger row.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable, Sequence

from . import ledger as ledger_mod
from .score import RunScore, ScoreError, format_table, score_run


def _load_json(p: Path) -> dict[str, Any] | None:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def arch_gate_paths(d: Path) -> tuple[Path, Path]:
    from . import arch_gate
    return arch_gate.resolve_run_paths(d)


def finalize(run_dir: str | Path, *, coord_db: str | Path | None = None, holders_dir: str | Path | None = None, expect_hypergraph: bool | None = None,
             expect_ranker: bool | None = None, run_id: str | None = None, ledger: str | Path | None = None, extra: dict[str, Any] | None = None,
             run_gate: bool = True) -> dict[str, Any]:
    """Score ``run_dir`` and run the architecture gate. Writes ``score.json`` and ``arch_gate.json``; appends the ledger row when
    ``run_id`` is given. A baseline run (``mode == 'baseline'``) has no coordinator, so its gate is recorded as ``not_applicable``."""
    from . import arch_gate
    d = Path(run_dir)
    if run_gate and not ((d / "raw_authority.json").exists() and (d / "reach_authority.json").exists()):
        # the asker's raw-access entitlement and reachable holders come from the organization (not gold); a system run does not write them itself
        try:
            from .baseline_central import write_raw_authority
            d_coord, _ = arch_gate_paths(d)
            if d_coord.exists():
                write_raw_authority(d, org_db=d_coord)
        except Exception:      # noqa: BLE001 - without the files the scorer stays strict and says so (supporting.reach_authority is None)
            pass
    rs = score_run(d)
    (d / "score.json").write_text(json.dumps(rs.to_dict(with_tasks=True), indent=2, default=str), encoding="utf-8")
    gate = None
    manifest = rs.manifest
    if run_gate and rs.mode != "baseline":
        d_coord, d_holders = arch_gate.resolve_run_paths(d)
        coord = Path(coord_db) if coord_db else d_coord
        holders = Path(holders_dir) if holders_dir else d_holders
        hg = expect_hypergraph if expect_hypergraph is not None else bool(manifest.get("hypergraph", True))
        gate = arch_gate.check(d, coord_db=coord, holders_dir=holders, expect_hypergraph=hg,
                               expect_ranker=expect_ranker if expect_ranker is not None else (hg and rs.ablation != "A1"))
        (d / "arch_gate.json").write_text(json.dumps(gate.to_dict(), indent=2, default=str), encoding="utf-8")
    row = None
    if run_id:
        row = ledger_mod.finish_run(run_id, rs, gate, extra=extra, ledger=ledger)
    return {"score": rs, "gate": gate, "ledger_row": row}


def _summary(d: Path) -> dict[str, Any]:
    try:
        rs: RunScore = score_run(d)
    except (ScoreError, OSError, ValueError, KeyError) as exc:
        return {"dir": d, "error": f"{type(exc).__name__}: {exc}"}
    gate = _load_json(d / "arch_gate.json")
    if rs.mode == "baseline":
        gstat = "n/a (baseline has no coordinator)"
    elif gate is None:
        gstat = "NOT RUN"
    elif gate.get("valid"):
        gstat = "valid"
    else:
        exp = set(ledger_mod.EXPECTED_GATE_FAILURES.get(rs.ablation or "", []))
        failed = gate.get("failed") or []
        gstat = ("ablation: " if rs.ablation and not set(failed) - exp else "INVALID: ") + ",".join(failed)
    variant = (rs.manifest or {}).get("variant")
    if rs.mode == "baseline":
        tag = {"source": "primary", "single": "secondary"}.get(str(variant), "")
        label = f"baseline ({variant}{', ' + tag if tag else ''})" if variant else "baseline"
    else:
        label = f"ablation {rs.ablation}" if rs.ablation else "system"
    return {"dir": d, "rs": rs, "gate": gate, "gate_status": gstat, "label": label}


def _siblings(d: Path) -> list[Path]:
    m = _load_json(d / "run_manifest.json") or {}
    key = (m.get("seed"), m.get("size"), m.get("split"))
    out = []
    for p in sorted(d.parent.iterdir()) if d.parent.exists() else []:
        if p.is_dir() and p != d and (p / "run_manifest.json").exists():
            pm = _load_json(p / "run_manifest.json") or {}
            if (pm.get("seed"), pm.get("size"), pm.get("split")) == key:
                out.append(p)
    return out


def _pct(x: float | None) -> str:
    return "-" if x is None else f"{100 * x:.1f}%"


def render(run_dir: str | Path, extra_dirs: Sequence[str | Path] = (), out: str | Path | None = None, *, siblings: bool = True) -> Path:
    d = Path(run_dir)
    dirs = [d, *[Path(p) for p in extra_dirs], *(_siblings(d) if siblings else [])]
    seen, rows = set(), []
    for p in dirs:
        if p.resolve() in seen:
            continue
        seen.add(p.resolve())
        rows.append(_summary(p))
    # system first, then the baseline with the primary variant (source) before the secondary one (single), then the ablations
    rank = lambda lbl: 0 if lbl == "system" else 1 if "primary" in lbl else 2 if lbl.startswith("baseline") else 3      # noqa: E731
    rows.sort(key=lambda r: (rank(r.get("label", "")), r.get("label", ""), str(r["dir"])))
    ok = [r for r in rows if "rs" in r]
    head = ok[0]["rs"] if ok else None
    lines = ["# Mycelic-E2E run report", ""]
    if head is not None:
        lines += [f"split **{head.split}** - size **{head.size}** - seed **{head.seed}** - contract v1. Every row carries its provider label; "
                  "accuracy is correct / ALL tasks of the frozen set with a 95 % Wilson interval.", ""]
    lines += ["| run | provider | gate | n | correct | accuracy | 95% CI | disclosures | decoy accepted | latency p50 / p95 (s) | model calls |",
              "|---|---|---|---:|---:|---:|---|---:|---:|---|---:|"]
    for r in rows:
        if "error" in r:
            lines.append(f"| {r['dir'].name} | - | - | - | - | - | unscorable: {r['error']} | - | - | - | - |")
            continue
        rs, sup = r["rs"], r["rs"].supporting
        lat = sup.get("latency_s") or {}
        lines.append(f"| {r['label']} ({r['dir'].name}) | {rs.provider_label or '?'} | {r['gate_status']} | {rs.n} | {rs.correct} | {_pct(rs.accuracy)} | "
                     f"[{_pct(rs.ci_low)}, {_pct(rs.ci_high)}] | {rs.disclosures} | {_pct((sup.get('decoy_acceptance') or {}).get('rate'))} | "
                     f"{lat.get('p50') if lat.get('p50') is None else round(lat['p50'], 2)} / {lat.get('p95') if lat.get('p95') is None else round(lat['p95'], 2)} | "
                     f"{sup.get('model_calls') if sup.get('model_calls') is not None else '-'} |")
    classes = sorted({c.cls for r in ok for c in r["rs"].per_class})
    if classes:
        lines += ["", "## Accuracy per task class (correct / n)", "", "| class | " + " | ".join(r["label"] for r in ok) + " |", "|---|" + "---:|" * len(ok)]
        for cls in classes:
            cells = []
            for r in ok:
                row = next((c for c in r["rs"].per_class if c.cls == cls), None)
                cells.append("-" if row is None else f"{row.correct}/{row.n}")
            lines.append(f"| {cls} | " + " | ".join(cells) + " |")
    lines += ["", "## Architecture gate and counts", ""]
    for r in ok:
        g = r.get("gate")
        lines.append(f"### {r['label']} ({r['dir'].name}) - gate: {r['gate_status']}")
        if g:
            for gid, res in g["gates"].items():
                lines.append(f"- {gid} **{res['status']}** - {res['title']}" + (f" ({res['detail']})" if res.get("detail") else ""))
                for p in res.get("problems", [])[:2]:
                    lines.append(f"    - {p}")
            c = g.get("counts") or {}
            keys = ("holders_created", "holders_with_records", "holders_activated", "holders_routed", "routes", "records_ingested", "applied_events_outcomes",
                    "claims_by_status", "hyperedges_by_kind")
            lines.append("- counts: " + "; ".join(f"{k}={c.get(k)}" for k in keys))
        else:
            lines.append("- no gate report for this run")
        lines.append("")
    lines += ["## Per-class detail (primary run)", "", "```", format_table(head) if head is not None else "no scorable run", "```", ""]
    path = Path(out) if out else d / "report.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def main(argv: Iterable[str] | None = None) -> int:      # pragma: no cover
    import argparse
    ap = argparse.ArgumentParser(description="Render the run report")
    ap.add_argument("run_dir")
    ap.add_argument("--extra", nargs="*", default=[])
    ap.add_argument("--finalize", action="store_true", help="score + gate the directory first")
    ns = ap.parse_args(list(argv) if argv is not None else None)
    if ns.finalize:
        finalize(ns.run_dir)
    print(render(ns.run_dir, ns.extra))
    return 0


if __name__ == "__main__":      # pragma: no cover
    raise SystemExit(main())
