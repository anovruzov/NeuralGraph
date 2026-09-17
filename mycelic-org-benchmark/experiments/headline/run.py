#!/usr/bin/env python3
"""Headline comparison: the executive table (DESIGN.md §12 / task executive table).

    python experiments/headline/run.py [--tier tier2] [--malicious 0.10] [--quick]
    python experiments/headline/run.py --tier tier1 --quick        # dry run

Default tier2 (10k workers, 100k interactions), malicious_fraction 0.10 with the standard attack mix
from configs/attacks.yaml, systems B2_central_rag, B3_central_llm_summary, B5_flat_agents,
B6_hier_no_lineage, B7_mycelic, B8_mycelic_security, B9_mycelic_questioning plus ORACLE_central_stats.
Writes `results/processed/headline.csv` (one row per run) and `results/tables/executive_comparison.md`
(System x Global Discovery, Cross-org synthesis, Poison Propagation, Private Data Exposed, Compression,
Cost, Bytes; mean ± sd over the seeds of this batch).  `--table-only` rebuilds the table from the
latest batch in headline.csv without running anything.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from experiments.common import (Condition, fmt_mean_sd, load_processed, log, make_cfg, parse_args, resolve_systems,  # noqa: E402
                                run_grid, runner_banner, seeds_from_args)

SYSTEMS = ["B2_central_rag", "B3_central_llm_summary", "B5_flat_agents", "B6_hier_no_lineage", "B7_mycelic",
           "B8_mycelic_security", "B9_mycelic_questioning", "ORACLE_central_stats"]
COLUMNS = [
    ("Global discovery (recall_global)", "metrics.recall_global", 3, 1.0, ""),
    ("Cross-org synthesis (recall_cross_team)", "metrics.recall_cross_team", 3, 1.0, ""),
    ("Precision (strict)", "metrics.precision_strict", 3, 1.0, ""),
    ("Poison propagation (poison_promotion_rate)", "metrics.poison.poison_promotion_rate", 3, 1.0, ""),
    ("Private data exposed (raw_sensitive_leakage)", "metrics.raw_sensitive_leakage", 3, 1.0, ""),
    ("Raw bytes off device (fraction)", "metrics.fraction_raw_exposed", 3, 1.0, ""),
    ("Compression (raw / promoted)", "metrics.compression_ratio", 1, 1.0, "x"),
    ("Cost (USD est.)", "metrics.cost_usd_est", 3, 1.0, ""),
    ("Bytes transmitted (MB)", "metrics.bytes_transmitted", 2, 1 / 2**20, ""),
    ("Tokens to cloud (k)", "metrics.tokens_to_cloud", 1, 1e-3, ""),
    ("Runtime (s)", "runtime_s", 1, 1.0, ""),
]


def write_executive_table(df, out_path: Path, systems: list[str], batch_id: str | None, tier: str, malicious: float) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["# Executive comparison", ""]
    if df is None or len(df) == 0:
        lines.append("_No headline rows available._")
        out_path.write_text("\n".join(lines) + "\n")
        return out_path
    if batch_id is not None and "batch_id" in df.columns:
        df = df[df["batch_id"] == batch_id]
    if "status" in df.columns:
        ok = df[df["status"] == "ok"]
    else:
        ok = df
    n_seeds = ok["seed"].nunique() if "seed" in ok.columns and len(ok) else 0
    lines.append(f"Tier: {tier}; malicious fraction: {malicious}; batch: {batch_id or 'all'}; seeds: {n_seeds}; "
                 f"values are mean ± sd over seeds; produced by `experiments/headline/run.py` from `results/processed/headline.csv` "
                 f"(backend: simulated-slm, see DESIGN.md §9).")
    lines.append("")
    header = ["System"] + [c[0] for c in COLUMNS]
    lines.append("| " + " | ".join(header) + " |")
    lines.append("|" + "|".join(["---"] * len(header)) + "|")
    present = [s for s in systems if len(ok) and (ok["system"] == s).any()] if len(ok) else []
    for s in present:
        sub = ok[ok["system"] == s]
        cells = [s]
        for _, col, digits, scale, unit in COLUMNS:
            cells.append(fmt_mean_sd(sub[col].tolist(), digits, scale, unit) if col in sub.columns else "n/a")
        lines.append("| " + " | ".join(cells) + " |")
    failed = df[df["status"] != "ok"] if "status" in df.columns else df.iloc[0:0]
    if len(failed):
        lines.append("")
        lines.append(f"Runs not ok in this batch: {len(failed)} (" + ", ".join(sorted(set(failed['system'].astype(str)))) + "); see results/raw/headline/*/manifest.json.")
    lines.append("")
    lines.append("Columns: recall_global / recall_cross_team = fraction of hidden global / cross-team effects accepted at the root "
                 "(exact signature match); poison_promotion_rate = accepted root claims classified as poison / accepted; "
                 "raw_sensitive_leakage = canary tokens that crossed a boundary / all canaries; compression = raw record bytes / "
                 "bytes promoted above the team layer; cost = simulated model cost (configs/models.yaml).")
    out_path.write_text("\n".join(lines) + "\n")
    return out_path


def main(argv: list[str] | None = None) -> int:
    def extra(ap):
        ap.add_argument("--malicious", type=float, default=0.10, help="malicious worker fraction")
        ap.add_argument("--table-only", action="store_true", help="only rebuild the executive table from headline.csv")
    args = parse_args(__doc__, argv, extra)
    if "--tier" not in (argv if argv is not None else sys.argv[1:]):
        args.tier = "tier2"
    cfg = make_cfg(args.tier, args.overrides)
    seeds = seeds_from_args(args, cfg)
    systems = resolve_systems(args, SYSTEMS, cfg, "headline")
    table_path = args.results / "tables" / "executive_comparison.md"
    if args.table_only:
        df = load_processed(args.results, "headline")
        batch = None
        if len(df) and "batch_id" in df.columns:
            batch = sorted(df["batch_id"].astype(str).unique())[-1]
        write_executive_table(df, table_path, systems, batch, args.tier, args.malicious)
        log(f"table -> {table_path}", family="headline")
        return 0
    f = float(args.malicious)
    conds = [Condition(name=f"headline-malicious-{f:.2f}", columns={"malicious_fraction": f},
                       attack={"fraction": f, "type_mix": cfg["attacks"].get("type_mix")} if f > 0 else None)]
    runner_banner("headline", args, cfg, seeds, systems, conds)
    t0 = time.perf_counter()
    rows = run_grid("headline", systems, seeds, cfg, conditions=conds, results_root=args.results, jobs=args.jobs, tag=args.tag,
                    tier=args.tier, quick=args.quick)
    batch = rows[0]["batch_id"] if rows else None
    df = load_processed(args.results, "headline")
    write_executive_table(df, table_path, systems, batch, args.tier, f)
    log(f"executive table -> {table_path} ({time.perf_counter() - t0:.0f}s total)", family="headline")
    return 0


if __name__ == "__main__":
    sys.exit(main())
