"""Compose research/mycelic_e2e/VERIFIED_RESULTS.md from results_table JSON (no hand-transcribed numbers).

Usage: python gen_verified.py OUT.md rt_holdout.json rt_dev_final.json rt_abl_final.json rt_c6.json rt_prior.json
Any JSON may be missing (the section then says so)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ORDER = ["cross_domain", "fault", "contradiction", "temporal", "common_origin_pos", "common_origin_copies", "coincidence",
         "single_domain", "denied", "cross_tenant"]
SHORT = {"cross_domain": "x-dept", "fault": "fault", "contradiction": "contra", "temporal": "temporal", "common_origin_pos": "origin+",
         "common_origin_copies": "copies", "coincidence": "coinc", "single_domain": "single", "denied": "denied", "cross_tenant": "x-tenant"}
REV = {"C9abl-A4": "658a093", "C8abl-A4fix": "3c18b1a", "C8-S1": "195e9ad", "C1-S1": "1b74545", "C2a-M2": "56f9832", "C3-L3": "36a9951", "C4-S1": "54fec3f", "C6-S1": "4c27744", "C7-S1": "7d2e63b",
       "H-M101": "7f37551"}
ABL = {"A1": "routing ranker off (first N authorized holders)", "A2": "root-aware support off (count references, not source roots)",
       "A3": "verification questions off", "A4": "holder entity/term index publication off", "A5": "authorized routing off (any holder of the tenant)",
       "A6": "ingestion dedupe off"}


def load(p: str) -> list[dict]:
    try:
        return json.loads(Path(p).read_text())
    except (OSError, ValueError):
        return []


def rev(run: str) -> str:
    for k, v in REV.items():
        if run.startswith(k):
            return v
    if run.startswith("C7abl-A5fix"):
        return "7d2e63b + A5 fix"
    if run.startswith("C7abl"):
        return "7d2e63b"
    if run.startswith("C6abl"):
        return "4c27744"
    return "?"


def gate(r: dict) -> str:
    if r["mode"] == "baseline":
        return "n/a (no coordinator)"
    if r["gate_valid"] is True:
        return "valid"
    if r["gate_valid"] is False:
        return "fails " + ", ".join(r["gate_failed"] or [])
    return "not run"


def table(rows: list[dict], *, per_class: bool = True) -> str:
    head = "| run | code | size/seed | mode | n | correct | accuracy | 95 % Wilson | disclosures | gate |"
    sep = "|---|---|---|---|---:|---:|---:|---|---:|---|"
    out = [head, sep]
    for r in rows:
        mode = r["mode"] + (f" {r['ablation']}" if r.get("ablation") else "")
        if r["mode"] == "baseline":
            mode = "central baseline " + ("source" if "source" in r["run"] else "single" if "single" in r["run"] else "")
        out.append(f"| {r['run']} | `{rev(r['run'])}` | {r['size']}/{r['seed']} | {mode} | {r['n']} | {r['correct']} | {r['accuracy']:.3f} | "
                   f"[{r['ci'][0]:.3f}, {r['ci'][1]:.3f}] | {r['disclosures']} | {gate(r)} |")
    if per_class and rows:
        out += ["", "Per class (correct/n):", "", "| run | " + " | ".join(SHORT[c] for c in ORDER) + " |", "|---|" + "---:|" * len(ORDER)]
        for r in rows:
            pc = r["per_class"]
            out.append(f"| {r['run']} | " + " | ".join(f"{pc[c][0]}/{pc[c][1]}" if c in pc else "-" for c in ORDER) + " |")
    return "\n".join(out)


def main() -> None:
    out, hold, dev, abl, c6, prior = sys.argv[1:7]
    post = sys.argv[7] if len(sys.argv) > 7 else ""
    H, D, A, C6, P, PO = load(hold), load(dev), load(abl), load(c6), load(prior), load(post) if post else []
    parts = [open(Path(__file__).with_name("verified_head.md")).read().rstrip(), ""]
    parts += ["## 1. Holdout: the frozen candidate, run once", ""]
    parts += [table(H) if H else "_The holdout run had not finished when this file was generated._", ""]
    parts += [open(Path(__file__).with_name("verified_holdout_notes.md")).read().rstrip() if Path(__file__).with_name("verified_holdout_notes.md").exists() else "", ""]
    parts += ["## 2. Dev: the candidate code on the dev world S/1", "", table(D) if D else "_missing_", "",
              open(Path(__file__).with_name("verified_dev_notes.md")).read().rstrip(), ""]
    parts += ["## 3. Ablations of the candidate code (dev S/1, paired with C7-S1)", "",
              "\n".join(f"- **{k}**: {v}" for k, v in ABL.items()), "", table(A) if A else "_The ablation runs had not finished._", "",
              open(Path(__file__).with_name("verified_abl_notes.md")).read().rstrip(), ""]
    parts += ["## 4. Earlier candidates (history; every row a complete, scored run)", "", table(C6 + P), "",
              open(Path(__file__).with_name("verified_history_notes.md")).read().rstrip(), ""]
    if PO:
        parts += ["## 5. After the holdout: follow-up fixes, measured on dev only (no holdout evaluation)", "", table(PO), "",
                  open(Path(__file__).with_name("verified_post_notes.md")).read().rstrip(), ""]
    parts += [open(Path(__file__).with_name("verified_tail.md")).read().rstrip().replace("## 5. Live", "## 6. Live" if PO else "## 5. Live").replace("## 6. Reproduce", "## 7. Reproduce" if PO else "## 6. Reproduce"), ""]
    Path(out).write_text("\n".join(parts))
    print("wrote", out)


if __name__ == "__main__":
    main()
