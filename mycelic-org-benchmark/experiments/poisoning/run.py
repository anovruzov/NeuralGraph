#!/usr/bin/env python3
"""Poisoning family: malicious-fraction sweep and the edge-security detector sweep.

    python experiments/poisoning/run.py --tier tier1 [--quick] [--only poisoning|edge_security|both]

* `poisoning.csv`     - attacks.fraction_sweep (configs/attacks.yaml) x {B1, B3, B6, B7, B8, B9} with the
  standard attack type mix; fraction 0 is the clean reference on the same worlds.
* `edge_security.csv` - at 10% and 20% malicious, detector in {none, rules, central_classifier,
  cloud_classifier, local_slm, hybrid_local_rules, lineage_aware} on the B7 topology.  Detection
  recall / precision / F1 / false suppression are computed from the hierarchy's batch and quarantine
  traces (attack tags are evaluation-only ground truth) and merged with `security.summary()` when the
  pipeline provides one; tokens_to_cloud and bytes exposed come from the pipeline's counters.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from experiments.common import (Condition, RunContext, log, make_cfg, parse_args, resolve_systems, run_grid,  # noqa: E402
                                runner_banner, seeds_from_args)

POISON_SYSTEMS = ["B1_central_keyword", "B3_central_llm_summary", "B6_hier_no_lineage", "B7_mycelic", "B8_mycelic_security",
                  "B9_mycelic_questioning"]
DETECTORS = ["none", "rules", "central_classifier", "cloud_classifier", "local_slm", "hybrid_local_rules", "lineage_aware"]
QUICK_FRACTIONS = [0.0, 0.10, 0.30]
QUICK_DETECTORS = ["none", "rules", "lineage_aware"]
SECURITY_FRACTIONS = [0.10, 0.20]
CANARY_RE = re.compile(r"CANARY-[a-z_]+-[0-9a-f]{8}")


def poisoning_conditions(cfg: dict[str, Any], quick: bool) -> list[Condition]:
    fractions = QUICK_FRACTIONS if quick else [float(f) for f in cfg["attacks"].get("fraction_sweep", [0.0, 0.1, 0.2])]
    out = []
    for f in fractions:
        out.append(Condition(name=f"malicious-{f:.2f}", columns={"malicious_fraction": f},
                             attack={"fraction": f, "type_mix": cfg["attacks"].get("type_mix")} if f > 0 else None))
    return out


def security_conditions(cfg: dict[str, Any], quick: bool) -> list[Condition]:
    dets = QUICK_DETECTORS if quick else DETECTORS
    fracs = SECURITY_FRACTIONS[:1] if quick else SECURITY_FRACTIONS
    out = []
    for f in fracs:
        for d in dets:
            out.append(Condition(name=f"malicious-{f:.2f}-{d}", columns={"malicious_fraction": f, "detector": d},
                                 sys_cfg={"kind": "hierarchy", "lineage": True, "detector": d, "local_slm_classifier": False},
                                 attack={"fraction": f, "type_mix": cfg["attacks"].get("type_mix")}))
    return out


def security_columns(ctx: RunContext) -> dict[str, Any]:
    """Detection quality of the security pipeline (record level from batch traces, claim level from quarantine)."""
    hier = ctx.result.get("hier")
    out: dict[str, Any] = {}
    if hier is None:
        return out
    world = ctx.world
    attack_total = int((world.attack_tag > 0).sum())
    benign_total = int(world.n - attack_total)
    # denominators are the records the team nodes actually SAW: the duplicate-evidence hook replays attack-tagged
    # copies into the batches, so the number of attack records seen exceeds the world's count (recall would exceed 1)
    attack_seen = sum(b["attack_records"] for b in hier.batch_trace)   # every batch with an attack record is traced
    flagged_attack = sum(b["attack_records"] - b["attack_kept"] for b in hier.batch_trace)
    flagged_benign = sum(b["benign_dropped"] for b in hier.batch_trace)
    rec = flagged_attack / attack_seen if attack_seen else float("nan")
    out["security.attack_records_world"] = attack_total
    out["security.attack_records_seen"] = int(attack_seen)
    prec = flagged_attack / (flagged_attack + flagged_benign) if (flagged_attack + flagged_benign) else float("nan")
    f1 = 2 * prec * rec / (prec + rec) if (prec == prec and rec == rec and (prec + rec) > 0) else float("nan")
    out["security.record_detection_recall"] = rec
    out["security.record_detection_precision"] = prec
    out["security.record_detection_f1"] = f1
    out["security.record_false_suppression"] = flagged_benign / benign_total if benign_total else float("nan")
    out["security.records_flagged_attack"] = int(flagged_attack)
    out["security.records_flagged_benign"] = int(flagged_benign)
    q_attack = sum(1 for q in hier.quarantine_trace if q.get("attack"))
    q_benign = sum(1 for q in hier.quarantine_trace if not q.get("attack"))
    out["security.claims_quarantined_attack"] = q_attack
    out["security.claims_quarantined_benign"] = q_benign
    out["security.claim_quarantine_precision"] = q_attack / (q_attack + q_benign) if (q_attack + q_benign) else float("nan")
    poison = ctx.metrics.get("poison", {}) or {}
    root_total = int(ctx.metrics.get("n_accepted", 0) or 0)
    out["security.poison_claims_at_root"] = poison.get("poison_claims_at_root")
    out["security.poison_promotion_rate"] = poison.get("poison_promotion_rate")
    sec = getattr(hier, "security", None)
    if sec is not None:
        out["security.pipeline"] = getattr(sec, "name", type(sec).__name__)
        out["security.tokens_to_cloud"] = int(getattr(sec, "tokens_to_cloud", 0) or 0)
        out["security.bytes_exposed"] = int(getattr(sec, "bytes_exposed", 0) or 0)
        out["security.flagged_records"] = int(getattr(sec, "flagged_records", 0) or 0)
        out["security.flagged_claims"] = int(getattr(sec, "flagged_claims", 0) or 0)
        texts = list(getattr(sec, "text_exposed", []) or [])
        out["security.n_text_exposed"] = len(texts)
        exposed = set()
        for t in texts:
            exposed.update(CANARY_RE.findall(str(t)))
        all_canaries = {c for c in world.canary if c}
        out["security.canaries_exposed"] = len(exposed & all_canaries)
        out["security.canary_leakage"] = len(exposed & all_canaries) / max(len(all_canaries), 1)
        if hasattr(sec, "summary"):
            try:
                summ = sec.summary() or {}
                for k, v in (summ.items() if isinstance(summ, dict) else []):
                    out[f"security.summary.{k}"] = v
            except Exception as e:  # pragma: no cover
                out["security.summary_error"] = str(e)[:120]
    else:
        out["security.pipeline"] = "none"
        out["security.tokens_to_cloud"] = 0
        out["security.bytes_exposed"] = 0
    out["security.total_tokens_to_cloud"] = int(ctx.metrics.get("tokens_to_cloud", 0) or 0) + int(out.get("security.tokens_to_cloud", 0) or 0)
    out["security.n_root_accepted"] = root_total
    return out


def main(argv: list[str] | None = None) -> int:
    args = parse_args(__doc__, argv, lambda ap: ap.add_argument("--only", choices=["poisoning", "edge_security", "both"],
                                                                 default="both", help="which sub-family to run"))
    cfg = make_cfg(args.tier, args.overrides)
    seeds = seeds_from_args(args, cfg)
    if args.only in ("poisoning", "both"):
        systems = resolve_systems(args, POISON_SYSTEMS, cfg, "poisoning")
        conds = poisoning_conditions(cfg, args.quick)
        runner_banner("poisoning", args, cfg, seeds, systems, conds)
        run_grid("poisoning", systems, seeds, cfg, per_run=security_columns, conditions=conds, results_root=args.results,
                 jobs=args.jobs, tag=args.tag, tier=args.tier, quick=args.quick)
    if args.only in ("edge_security", "both"):
        systems = resolve_systems(args, ["B7_mycelic"], None, "edge_security")
        conds = security_conditions(cfg, args.quick)
        runner_banner("edge_security", args, cfg, seeds, systems, conds)
        run_grid("edge_security", systems, seeds, cfg, per_run=security_columns, conditions=conds, results_root=args.results,
                 jobs=args.jobs, tag=args.tag, tier=args.tier, quick=args.quick)
    log("done", family="poisoning")
    return 0


if __name__ == "__main__":
    sys.exit(main())
