"""Aggregate raw rows into tables, figures and a machine-readable summary.

Reads one or more results/<experiment_id>/rows.csv files (never modifies them)
and writes tables/, figures/ and results/summary.json.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from collections import defaultdict
from dataclasses import asdict
from pathlib import Path

import numpy as np

from .stats import Comparison, holm, loglog_fit, mean_ci, paired_compare

ROOT = Path(__file__).resolve().parents[1]

def _coerce(k: str, v: str):
    if v in ("", "nan", "None"):
        return float("nan")
    if v in ("True", "False"):
        return v == "True"
    try:
        f = float(v)
        return int(f) if f.is_integer() and any(h in k for h in ("seed", "n_agents", "n_claims", "partition_parts")) else f
    except ValueError:
        return v


def _open_rows(d: Path):
    """Raw rows may be stored plain or gzipped (they are gzipped for version control)."""
    if d.is_file():
        cands = [d]
    else:
        cands = [d / "rows.csv", d / "rows.csv.gz"]
    for c in cands:
        if c.exists():
            if c.suffix == ".gz":
                import gzip
                return gzip.open(c, "rt", newline="")
            return open(c, newline="")
    raise FileNotFoundError(f"no rows.csv or rows.csv.gz in {d}")


def load_rows(paths: list[Path]) -> list[dict]:
    rows = []
    for p in paths:
        with _open_rows(p) as fh:
            for r in csv.DictReader(fh):
                rows.append({k: _coerce(k, v) for k, v in r.items()})
    return rows


def cond_key(r: dict) -> tuple:
    return (r["n_agents"], r["regime"], r["severity"], r["corruption_type"],
            r["corruption_level"], r["partition_parts"], r["phase"])


_ROW_INDEX: dict[int, dict] = {}


def _index(rows) -> dict:
    """(condition, system) -> {seed: row}, built once per row list.

    The comparison code asks for thousands of (condition, system, metric) slices;
    scanning the row list for each one is what makes the analysis quadratic.
    """
    cached = _ROW_INDEX.get(id(rows))
    if cached is not None and cached["n"] == len(rows):
        return cached["idx"]
    idx = defaultdict(dict)
    for r in rows:
        idx[(cond_key(r), r["system"])][r["seed"]] = r
    _ROW_INDEX[id(rows)] = {"n": len(rows), "idx": idx}
    return idx


def series(rows, key, system, metric) -> np.ndarray:
    """Metric values for one (condition, system), ordered by seed."""
    vals = _index(rows)[(key, system)]
    return np.array([vals[s][metric] for s in sorted(vals)], float)


def paired_series(rows, key, sys_a, sys_b, metric):
    idx = _index(rows)
    va, vb = idx.get((key, sys_a), {}), idx.get((key, sys_b), {})
    seeds = sorted(set(va) & set(vb))
    return (np.array([va[s][metric] for s in seeds], float),
            np.array([vb[s][metric] for s in seeds], float))


def summarise(rows, metrics) -> dict:
    """mean / sd / 95% CI per (condition, system, metric)."""
    out = defaultdict(dict)
    by = defaultdict(list)
    for r in rows:
        by[(cond_key(r), r["system"])].append(r)
    for (key, system), rs in by.items():
        entry = {"n_seeds": len(rs)}
        for m in metrics:
            x = np.array([r.get(m, float("nan")) for r in rs], float)
            mu, sd, lo, hi = mean_ci(x)
            entry[m] = {"mean": mu, "sd": sd, "ci_low": lo, "ci_high": hi}
        out[key][system] = entry
    return out


# -------------------------------------------------------------------------
# tables
# -------------------------------------------------------------------------

MAIN_METRICS = ["knowledge_survival", "accuracy_macro", "availability", "iss",
                "contradiction_f1", "contradiction_precision", "contradiction_recall",
                "ece", "ece_recalibrated", "conf_auroc", "evidence_verification_acc",
                "strategic_spearman", "storage_per_claim", "messages_per_claim",
                "redundancy_efficiency", "communication_efficiency", "mean_probe_rounds",
                "acc_single_hop", "acc_multi_hop", "acc_temporal", "acc_revision_sensitive",
                "acc_reconstruction", "acc_given_answered", "contradiction_resolved_acc",
                "acc_partition_updated", "conflict_visible_rate",
                "acc_class_correlated", "acc_class_independent",
                "acc_single_root", "acc_multi_root", "iss_class_correlated",
                "iss_class_independent", "ece_class_correlated", "ece_class_independent",
                "eval_seconds"]


def write_csv(path: Path, rows: list[dict]):
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    keys = list(rows[0].keys())
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=keys)
        w.writeheader()
        w.writerows(rows)




# -------------------------------------------------------------------------
# run index
# -------------------------------------------------------------------------

def read_index() -> dict[str, list[dict]]:
    path = ROOT / "results" / "index.jsonl"
    entries = defaultdict(dict)
    if not path.exists():
        return {}
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        e = json.loads(line)
        entries[e["index_name"]][json.dumps(e.get("overrides", []), sort_keys=True)] = e
    return {k: list(v.values()) for k, v in entries.items()}


CONFIG_KEYS = ["budget_k", "question_budget", "question_strategy", "question_mix",
               "question_equalize_storage", "full_replicas", "max_probe"]


def load_entry(entry: dict) -> list[dict]:
    d = ROOT / entry["dir"]
    manifest = json.loads((d / "manifest.json").read_text())
    cfg = manifest["config"]
    rows = load_rows([d])
    for r in rows:
        r["index_name"] = entry["index_name"]
        r["run_dir"] = entry["dir"]
        for k in CONFIG_KEYS:
            r[k] = cfg.get(k)
        if isinstance(r.get("question_mix"), list):
            r["question_mix"] = ",".join(str(x) for x in r["question_mix"])
    return rows


HEADLINE = [
    ("random", 0.5, "none", 0.0, 0, "post_failure", "50% random churn"),
    ("random", 0.9, "none", 0.0, 0, "post_failure", "90% random churn"),
    ("correlated_org", 0.5, "none", 0.0, 0, "post_failure", "50% loss, whole organisations"),
    ("correlated_domain", 0.7, "none", 0.0, 0, "post_failure", "70% loss, whole failure domains"),
    ("targeted_whitebox", 0.5, "none", 0.0, 0, "post_failure", "50% targeted lineage attack"),
    ("random", 0.3, "root", 0.2, 0, "post_failure", "20% origin-source corruption"),
    ("random", 0.3, "node_coordinated", 0.2, 0, "post_failure", "20% coordinated node corruption"),
    ("partition", 0.0, "none", 0.0, 4, "after_reconnect", "4-way partition, after reconnect"),
]

PRIMARY_PAIRS = [("B6", "B3"), ("B6", "B5"), ("B6", "B4"), ("B6", "B2"), ("B6", "B1"),
                 ("B7", "B6"), ("B7", "B3Q"), ("B7", "B2"), ("B3Q", "B3")]
PRIMARY_METRICS = ["knowledge_survival", "accuracy_macro", "iss", "contradiction_f1",
                   "contradiction_resolved_acc", "acc_revision_sensitive", "ece_recalibrated"]


def build_main_tables(rows_by_n: dict[int, list[dict]], out_tables: Path,
                      out_figs: Path, summary: dict):
    from . import figures as F

    systems_order = ["B0", "B1", "B2", "B3", "B4", "B5", "B6", "B7", "B3Q"]

    # ---- appendix: every condition x system, mean and 95% CI ----------------
    appendix = []
    for n, rows in sorted(rows_by_n.items()):
        s = summarise(rows, MAIN_METRICS)
        for key, per_sys in sorted(s.items(), key=lambda kv: str(kv[0])):
            for sysid, entry in sorted(per_sys.items()):
                rec = {"n_agents": key[0], "regime": key[1], "severity": key[2],
                       "corruption_type": key[3], "corruption_level": key[4],
                       "partition_parts": key[5], "phase": key[6], "system": sysid,
                       "n_seeds": entry["n_seeds"]}
                for m in MAIN_METRICS:
                    rec[f"{m}_mean"] = entry[m]["mean"]
                    rec[f"{m}_sd"] = entry[m]["sd"]
                    rec[f"{m}_ci_low"] = entry[m]["ci_low"]
                    rec[f"{m}_ci_high"] = entry[m]["ci_high"]
                appendix.append(rec)
    write_csv(out_tables / "appendix_full_grid.csv", appendix)
    summary["n_appendix_rows"] = len(appendix)

    # ---- Table 1: headline conditions at the largest fully-gridded scale ----
    main_n = max(n for n in rows_by_n if n <= 10000)
    rows = rows_by_n[main_n]
    s = summarise(rows, MAIN_METRICS)
    n_seeds = max((e["n_seeds"] for per in s.values() for e in per.values()), default=0)
    table1 = []
    for reg, sev, ct, cl, pp, phase, label in HEADLINE:
        key = (main_n, reg, sev, ct, cl, pp, phase)
        if key not in s:
            continue
        for sysid in systems_order:
            e = s[key].get(sysid)
            if not e:
                continue
            table1.append({
                "condition": label, "system": sysid,
                "KS": e["knowledge_survival"]["mean"],
                "KS_ci_low": e["knowledge_survival"]["ci_low"],
                "KS_ci_high": e["knowledge_survival"]["ci_high"],
                "accuracy": e["accuracy_macro"]["mean"],
                "accuracy_ci_low": e["accuracy_macro"]["ci_low"],
                "accuracy_ci_high": e["accuracy_macro"]["ci_high"],
                "ISS": e["iss"]["mean"],
                "contradiction_F1": e["contradiction_f1"]["mean"],
                "contradiction_resolved": e["contradiction_resolved_acc"]["mean"],
                "acc_partition_updated": e["acc_partition_updated"]["mean"],
                "conflict_visible_rate": e["conflict_visible_rate"]["mean"],
                "acc_given_answered": e["acc_given_answered"]["mean"],
                "ECE_recal": e["ece_recalibrated"]["mean"],
                "storage_per_claim": e["storage_per_claim"]["mean"],
                "messages_per_claim": e["messages_per_claim"]["mean"],
                "RE": e["redundancy_efficiency"]["mean"],
                "CE": e["communication_efficiency"]["mean"],
                "n_seeds": e["n_seeds"]})
    write_csv(out_tables / "table1_main_results.csv", table1)
    summary["table1"] = table1
    summary["main_n"] = main_n
    summary["main_n_seeds"] = n_seeds

    # ---- paired statistical tests -------------------------------------------
    tests: list[Comparison] = []
    for n, rws in sorted(rows_by_n.items()):
        for reg, sev, ct, cl, pp, phase, label in HEADLINE:
            key = (n, reg, sev, ct, cl, pp, phase)
            for metric in PRIMARY_METRICS:
                fam = []
                for a, b in PRIMARY_PAIRS:
                    xa, xb = paired_series(rws, key, a, b, metric)
                    if xa.size < 3:
                        continue
                    c = paired_compare(xa, xb, metric, a, b)
                    c.__dict__["n_agents"] = n
                    c.__dict__["condition"] = label
                    fam.append(c)
                holm(fam)
                tests.extend(fam)
    test_rows = [{**{k: v for k, v in asdict(c).items()},
                  "n_agents": c.__dict__["n_agents"], "condition": c.__dict__["condition"]}
                 for c in tests]
    write_csv(out_tables / "table2_statistical_tests.csv", test_rows)
    summary["n_statistical_tests"] = len(test_rows)
    summary["tests"] = test_rows

    # ---- money figure 1 ------------------------------------------------------
    regimes = ["random", "correlated_org", "targeted_whitebox"]
    titles = ["random churn", "correlated failure (whole organisations)",
              "targeted lineage attack (white box)"]
    data = defaultdict(list)
    for key, per_sys in s.items():
        _, reg, sev, ct, cl, pp, phase = key
        if ct != "none" or reg not in regimes:
            continue
        for sysid, e in per_sys.items():
            data[(reg, sysid)].append({"severity": sev, **e})
    F.money_figure(data, systems_order, regimes, titles,
                   ["knowledge_survival", "accuracy_macro"],
                   ["knowledge survival", "task accuracy (macro)"],
                   main_n, out_figs / "fig1_money_survival_vs_failure.png", n_seeds)

    # ---- corruption figure ---------------------------------------------------
    cdata = defaultdict(list)
    for key, per_sys in s.items():
        _, reg, sev, ct, cl, pp, phase = key
        if ct == "none":
            continue
        for sysid, e in per_sys.items():
            cdata[(ct, sysid)].append({"corruption_level": cl, **e})
    # include the zero-corruption point from the matching churn condition
    base_key = (main_n, "random", 0.3, "none", 0.0, 0, "post_failure")
    if base_key in s:
        for ct in ("node_coordinated", "root", "node_uncoordinated"):
            for sysid, e in s[base_key].items():
                cdata[(ct, sysid)].append({"corruption_level": 0.0, **e})
    F.corruption_figure(cdata, systems_order, out_figs / "fig4_corruption.png", main_n, n_seeds)

    # ---- correlated-replication figure (section 7) ---------------------------
    key = (main_n, "targeted_whitebox", 0.5, "none", 0.0, 0, "post_failure")
    if key in s:
        subset = [x for x in ["B1", "B2", "B3", "B4", "B5", "B6", "B7"] if x in s[key]]
        F.class_figure(s[key], subset, out_figs / "fig5_correlated_vs_independent.png",
                       main_n, n_seeds)
        summary["class_table"] = [
            {"system": x,
             "acc_correlated": s[key][x]["acc_class_correlated"]["mean"],
             "acc_independent": s[key][x]["acc_class_independent"]["mean"],
             "iss_correlated": s[key][x]["iss_class_correlated"]["mean"],
             "iss_independent": s[key][x]["iss_class_independent"]["mean"],
             "ece_correlated": s[key][x]["ece_class_correlated"]["mean"],
             "ece_independent": s[key][x]["ece_class_independent"]["mean"]}
            for x in subset]
        write_csv(out_tables / "table6_correlated_replication.csv", summary["class_table"])

    return s, main_n, n_seeds


def build_scaling(rows_by_n: dict[int, list[dict]], out_tables: Path, out_figs: Path,
                  summary: dict):
    from . import figures as F
    systems = ["B1", "B2", "B3", "B4", "B5", "B6", "B7"]
    metrics = ["storage_per_claim", "messages_per_claim", "mean_probe_rounds",
               "knowledge_survival", "eval_seconds"]
    key_tail = ("random", 0.5, "none", 0.0, 0, "post_failure")
    scaling = defaultdict(list)
    for n, rows in sorted(rows_by_n.items()):
        s = summarise(rows, metrics)
        key = (n, *key_tail)
        if key not in s:
            continue
        for sysid in systems:
            if sysid in s[key]:
                scaling[sysid].append({"n_agents": n, **s[key][sysid]})
    F.scaling_figure(scaling, out_figs / "fig3_scaling.png")

    fits = []
    for sysid, pts in scaling.items():
        n = np.array([p["n_agents"] for p in pts], float)
        for m in ["storage_per_claim", "messages_per_claim", "mean_probe_rounds", "eval_seconds"]:
            y = np.array([p[m]["mean"] for p in pts], float)
            fit = loglog_fit(n, y)
            fits.append({"system": sysid, "quantity": m, "n_points": fit["n_points"],
                         "loglog_slope": fit["slope"], "r2": fit["r2"],
                         "stderr": fit["stderr"], "classification": fit["classification"],
                         "values": ";".join(f"{int(a)}:{b:.4g}" for a, b in zip(n, y))})
        ks = np.array([p["knowledge_survival"]["mean"] for p in pts], float)
        fits.append({"system": sysid, "quantity": "knowledge_survival",
                     "n_points": len(ks), "loglog_slope": loglog_fit(n, ks)["slope"],
                     "r2": loglog_fit(n, ks)["r2"], "stderr": loglog_fit(n, ks)["stderr"],
                     "classification": "flat in N" if ks.std() < 0.02 else "varies with N",
                     "values": ";".join(f"{int(a)}:{b:.4g}" for a, b in zip(n, ks))})
    write_csv(out_tables / "table3_scaling.csv", fits)
    summary["scaling"] = fits
    summary["scaling_points"] = {s: [{"n_agents": p["n_agents"],
                                      **{m: p[m]["mean"] for m in metrics}} for p in pts]
                                 for s, pts in scaling.items()}
    return scaling


def build_frontier(index, main_rows_by_n, out_tables: Path, out_figs: Path, summary: dict):
    from . import figures as F
    entries = []
    for name in ("budget_n10000", "budget_n1000"):
        cand = index.get(name, [])
        if cand and cand[0]["n_agents"] in main_rows_by_n:
            entries = cand
            break
    if not entries:
        entries = index.get("budget_n10000", []) or index.get("budget_n1000", [])
    rows = []
    for e in entries:
        rows.extend(load_entry(e))
    if not rows:
        return
    n_agents = rows[0]["n_agents"]
    key_tail = ("targeted_whitebox", 0.5, "none", 0.0, 0, "post_failure")
    sweep = defaultdict(list)
    table = []
    by_k = defaultdict(list)
    for r in rows:
        by_k[(r["budget_k"], r["system"])].append(r)
    for (k, sysid), rs in sorted(by_k.items(), key=lambda kv: (kv[0][1], kv[0][0])):
        sel = [r for r in rs if cond_key(r)[1:] == key_tail]
        if not sel:
            continue
        e = summarise(sel, MAIN_METRICS)[(n_agents, *key_tail)][sysid]
        pt = {"budget_k": k, "system": sysid, **e,
              "storage_per_claim": e["storage_per_claim"]["mean"],
              "total_cost_per_claim": e["storage_per_claim"]["mean"] + e["messages_per_claim"]["mean"]}
        sweep[sysid].append(pt)
        table.append({"system": sysid, "budget_k": k,
                      "storage_per_claim": pt["storage_per_claim"],
                      "messages_per_claim": e["messages_per_claim"]["mean"],
                      "knowledge_survival": e["knowledge_survival"]["mean"],
                      "ks_ci_low": e["knowledge_survival"]["ci_low"],
                      "ks_ci_high": e["knowledge_survival"]["ci_high"],
                      "accuracy_macro": e["accuracy_macro"]["mean"],
                      "iss": e["iss"]["mean"], "n_seeds": e["n_seeds"]})
    write_csv(out_tables / "table5_budget_frontier.csv", table)
    summary["frontier"] = table

    # fixed-architecture reference points from the main run
    main = main_rows_by_n.get(n_agents, [])
    s = summarise(main, MAIN_METRICS)
    key = (n_agents, *key_tail)
    points = []
    n_seeds = 0
    if key in s:
        for sysid in ["B0", "B1", "B2", "B3", "B4", "B5", "B6", "B7"]:
            if sysid not in s[key]:
                continue
            e = s[key][sysid]
            n_seeds = e["n_seeds"]
            points.append({"system": sysid, **e,
                           "storage_per_claim": e["storage_per_claim"]["mean"],
                           "total_cost_per_claim": e["storage_per_claim"]["mean"]
                           + e["messages_per_claim"]["mean"]})
    F.frontier_figure(points, sweep, out_figs / "fig2_money_frontier.png", n_agents,
                      "50% targeted lineage attack", n_seeds)
    summary["frontier_points"] = [{"system": p["system"],
                                   "storage_per_claim": p["storage_per_claim"],
                                   "total_cost_per_claim": p["total_cost_per_claim"],
                                   "knowledge_survival": p["knowledge_survival"]["mean"]}
                                  for p in points]


def build_questioning(index, out_tables: Path, out_figs: Path, summary: dict):
    """G_Q: what continual questioning adds, at what cost, and where it fails."""
    from . import figures as F
    out = {}

    def collect(name):
        rows = []
        for e in index.get(name, []):
            rows.extend(load_entry(e))
        return rows

    budget_rows = collect("questioning_budget")
    curves = defaultdict(list)
    table = []
    if budget_rows:
        n_agents = budget_rows[0]["n_agents"]
        conds = sorted({cond_key(r)[1:] for r in budget_rows})
        by = defaultdict(list)
        for r in budget_rows:
            by[(r["question_budget"], cond_key(r)[1:], r["system"])].append(r)
        for (b, ct, sysid), rs in sorted(by.items(), key=lambda kv: (kv[0][2], str(kv[0][1]), kv[0][0])):
            e = summarise(rs, MAIN_METRICS)[(n_agents, *ct)][sysid]
            rec = {"question_budget": b, "condition": "|".join(str(x) for x in ct),
                   "system": sysid, **e}
            if ct == ("correlated_org", 0.5, "none", 0.0, 0, "post_failure"):
                curves[sysid].append(rec)
            table.append({"question_budget": b, "condition": rec["condition"], "system": sysid,
                          "accuracy_macro": e["accuracy_macro"]["mean"],
                          "acc_ci_low": e["accuracy_macro"]["ci_low"],
                          "acc_ci_high": e["accuracy_macro"]["ci_high"],
                          "knowledge_survival": e["knowledge_survival"]["mean"],
                          "acc_revision_sensitive": e["acc_revision_sensitive"]["mean"],
                          "contradiction_f1": e["contradiction_f1"]["mean"],
                          "ece_recalibrated": e["ece_recalibrated"]["mean"],
                          "storage_per_claim": e["storage_per_claim"]["mean"],
                          "messages_per_claim": e["messages_per_claim"]["mean"],
                          "n_seeds": e["n_seeds"]})
        F.questioning_figure({F.SHORT.get(k, k): v for k, v in curves.items()},
                             out_figs / "fig6_questioning_budget.png", n_agents,
                             max(t["n_seeds"] for t in table))
        write_csv(out_tables / "table4_questioning_budget.csv", table)
        out["budget"] = table

        # G_Q with paired tests, on the lineage base and on the count base
        gq = []
        for (b, ct) in sorted({(k[0], k[1]) for k in by}):
            for a, base in (("B7", "B6"), ("B3Q", "B3")):
                rs = [r for r in budget_rows
                      if r["question_budget"] == b and cond_key(r)[1:] == ct]
                key = (n_agents, *ct)
                for metric in ["accuracy_macro", "acc_revision_sensitive",
                               "knowledge_survival", "contradiction_f1", "ece_recalibrated"]:
                    xa, xb = paired_series(rs, key, a, base, metric)
                    if xa.size < 3:
                        continue
                    c = paired_compare(xa, xb, metric, a, base)
                    d = asdict(c)
                    d.update({"question_budget": b,
                              "condition": "|".join(str(x) for x in ct)})
                    gq.append(d)
        holm_groups = defaultdict(list)
        for d in gq:
            holm_groups[d["metric"]].append(d)
        for _, group in holm_groups.items():
            ps = sorted(range(len(group)), key=lambda i: group[i]["p_paired_t"])
            m = len(group); prev = 0.0
            for rank, i in enumerate(ps):
                adj = min(1.0, max(prev, (m - rank) * group[i]["p_paired_t"]))
                prev = adj
                group[i]["p_holm"] = adj
                group[i]["significant_holm_05"] = adj < 0.05
        write_csv(out_tables / "table4b_questioning_gq.csv", gq)
        out["gq"] = gq

    for name, fname in (("questioning_strategy", "table4c_questioning_strategy.csv"),
                        ("questioning_mix", "table4d_questioning_mix.csv"),
                        ("questioning_equalize", "table4e_questioning_equalize.csv")):
        rows = collect(name)
        if not rows:
            continue
        n_agents = rows[0]["n_agents"]
        by = defaultdict(list)
        for r in rows:
            tag = (r.get("question_strategy"), r.get("question_mix"),
                   r.get("question_equalize_storage"))
            by[(tag, cond_key(r)[1:], r["system"])].append(r)
        tab = []
        for (tag, ct, sysid), rs in sorted(by.items(), key=lambda kv: str(kv[0])):
            e = summarise(rs, MAIN_METRICS)[(n_agents, *ct)][sysid]
            tab.append({"strategy": tag[0], "mix": tag[1], "equalize_storage": tag[2],
                        "condition": "|".join(str(x) for x in ct), "system": sysid,
                        "accuracy_macro": e["accuracy_macro"]["mean"],
                        "acc_ci_low": e["accuracy_macro"]["ci_low"],
                        "acc_ci_high": e["accuracy_macro"]["ci_high"],
                        "acc_revision_sensitive": e["acc_revision_sensitive"]["mean"],
                        "knowledge_survival": e["knowledge_survival"]["mean"],
                        "iss": e["iss"]["mean"],
                        "contradiction_f1": e["contradiction_f1"]["mean"],
                        "storage_per_claim": e["storage_per_claim"]["mean"],
                        "messages_per_claim": e["messages_per_claim"]["mean"],
                        "n_seeds": e["n_seeds"]})
        write_csv(out_tables / fname, tab)
        out[name] = tab
    summary["questioning"] = out


def build_factorial(index, out_tables: Path, summary: dict, index_name: str = "factorial",
                    out_name: str = "table7_placement_x_aggregation.csv",
                    summary_key: str = "factorial"):
    rows = []
    for e in index.get(index_name, []):
        rows.extend(load_entry(e))
    if not rows:
        return
    n_agents = rows[0]["n_agents"]
    s = summarise(rows, MAIN_METRICS)
    tab = []
    for key, per_sys in sorted(s.items(), key=lambda kv: str(kv[0])):
        for sysid, e in sorted(per_sys.items()):
            tab.append({"condition": "|".join(str(x) for x in key[1:]), "system": sysid,
                        "placement": {"B3": "random", "B3L": "random", "B5": "support-count",
                                      "B6": "lineage", "B6C": "lineage", "B2": "broad",
                                      "B2L": "broad"}.get(sysid, "?"),
                        "aggregation": {"B3": "count", "B3L": "lineage", "B5": "count",
                                        "B6": "lineage", "B6C": "count", "B2": "count",
                                        "B2L": "lineage"}.get(sysid, "?"),
                        "knowledge_survival": e["knowledge_survival"]["mean"],
                        "ks_ci_low": e["knowledge_survival"]["ci_low"],
                        "ks_ci_high": e["knowledge_survival"]["ci_high"],
                        "accuracy_macro": e["accuracy_macro"]["mean"],
                        "iss": e["iss"]["mean"],
                        "contradiction_f1": e["contradiction_f1"]["mean"],
                        "ece_recalibrated": e["ece_recalibrated"]["mean"],
                        "storage_per_claim": e["storage_per_claim"]["mean"],
                        "n_seeds": e["n_seeds"]})
    write_csv(out_tables / out_name, tab)
    summary[summary_key] = tab


def build_negative_regimes(index, out_tables: Path, summary: dict):
    """Runs that deliberately alter the world to find where lineage stops helping."""
    rows = []
    for e in index.get("negative", []):
        rs = load_entry(e)
        tag = e["dir"].split("/")[-1].split("_20")[0].replace("negative_n10000_", "")
        for r in rs:
            r["regime_tag"] = tag
        rows.extend(rs)
    if not rows:
        return
    n_agents = rows[0]["n_agents"]
    by = defaultdict(list)
    for r in rows:
        by[(r["regime_tag"], cond_key(r)[1:], r["system"])].append(r)
    tab = []
    for (tag, ct, sysid), rs in sorted(by.items(), key=lambda kv: (kv[0][0], str(kv[0][1]), kv[0][2])):
        e = summarise(rs, MAIN_METRICS)[(n_agents, *ct)][sysid]
        tab.append({"world_variant": tag, "condition": "|".join(str(x) for x in ct),
                    "system": sysid,
                    "knowledge_survival": e["knowledge_survival"]["mean"],
                    "ks_ci_low": e["knowledge_survival"]["ci_low"],
                    "ks_ci_high": e["knowledge_survival"]["ci_high"],
                    "accuracy_macro": e["accuracy_macro"]["mean"],
                    "acc_ci_low": e["accuracy_macro"]["ci_low"],
                    "acc_ci_high": e["accuracy_macro"]["ci_high"],
                    "iss": e["iss"]["mean"],
                    "contradiction_f1": e["contradiction_f1"]["mean"],
                    "contradiction_resolved_acc": e["contradiction_resolved_acc"]["mean"],
                    "messages_per_claim": e["messages_per_claim"]["mean"],
                    "n_seeds": e["n_seeds"]})
    write_csv(out_tables / "table9_negative_regimes.csv", tab)
    # paired B6-B3 and B7-B6 differences inside each altered world
    diffs = []
    for tag in sorted({r["regime_tag"] for r in rows}):
        rs = [r for r in rows if r["regime_tag"] == tag]
        for ct in sorted({cond_key(r)[1:] for r in rs}, key=str):
            key = (n_agents, *ct)
            for a, b in (("B6", "B3"), ("B7", "B6"), ("B3Q", "B3"), ("B6", "B5")):
                for metric in ("accuracy_macro", "knowledge_survival", "iss"):
                    xa, xb = paired_series(rs, key, a, b, metric)
                    if xa.size < 3:
                        continue
                    c = paired_compare(xa, xb, metric, a, b)
                    d = asdict(c)
                    d.update({"world_variant": tag, "condition": "|".join(str(x) for x in ct)})
                    diffs.append(d)
    write_csv(out_tables / "table9b_negative_regime_tests.csv", diffs)
    summary["negative_regimes"] = tab
    summary["negative_regime_tests"] = diffs


def build_negative_results(rows_by_n, summary: dict, out_tables: Path):
    """Every condition where the proposed architecture fails to beat a baseline."""
    neg = []
    for n, rows in sorted(rows_by_n.items()):
        keys = sorted({cond_key(r) for r in rows}, key=str)
        for key in keys:
            for a, b in [("B6", "B3"), ("B6", "B5"), ("B6", "B2"), ("B6", "B4"),
                         ("B7", "B6"), ("B7", "B2"), ("B7", "B3Q")]:
                for metric in ["knowledge_survival", "accuracy_macro", "iss"]:
                    xa, xb = paired_series(rows, key, a, b, metric)
                    if xa.size < 3:
                        continue
                    c = paired_compare(xa, xb, metric, a, b)
                    if c.mean_diff < 0 and c.p_paired_t < 0.05:
                        neg.append({"n_agents": n, "regime": key[1], "severity": key[2],
                                    "corruption_type": key[3], "corruption_level": key[4],
                                    "partition_parts": key[5], "phase": key[6],
                                    "metric": metric, "system": a, "baseline": b,
                                    "mean_system": c.mean_a, "mean_baseline": c.mean_b,
                                    "mean_diff": c.mean_diff, "ci_low": c.ci_low,
                                    "ci_high": c.ci_high, "cohens_dz": c.cohens_dz,
                                    "p_paired_t": c.p_paired_t, "n_pairs": c.n_pairs})
    write_csv(out_tables / "table8_negative_results.csv", neg)
    summary["negative_results"] = neg
    summary["n_negative_results"] = len(neg)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Analyse stress-test results")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--index", default=None, help="analyse a single index name")
    ap.add_argument("--out-tag", default="")
    args = ap.parse_args(argv)

    index = read_index()
    if not index:
        raise SystemExit("no results/index.jsonl -- run run_all.sh first")

    out_tables = ROOT / "tables"
    out_figs = ROOT / "figures"
    out_tables.mkdir(exist_ok=True)
    out_figs.mkdir(exist_ok=True)
    summary: dict = {"generated_utc": __import__("datetime").datetime.now(
        __import__("datetime").timezone.utc).isoformat()}

    names = [args.index] if args.index else [k for k in index if k.startswith("main_")]
    rows_by_n: dict[int, list[dict]] = {}
    runtime = 0.0
    total_rows = 0
    manifests = {}
    for name in names:
        for e in index[name]:
            rws = load_entry(e)
            rows_by_n.setdefault(e["n_agents"], []).extend(rws)
            runtime += e.get("runtime_seconds", 0.0)
            total_rows += e.get("n_rows", len(rws))
            manifests[name] = json.loads((ROOT / e["dir"] / "manifest.json").read_text())
    summary["main_runs"] = {k: {"dir": v["dir"], "n_agents": v["n_agents"],
                                "n_seeds": v["n_seeds"], "n_rows": v["n_rows"],
                                "runtime_seconds": v["runtime_seconds"]}
                            for k, vs in index.items() if k.startswith("main_")
                            for v in [vs[-1]]}
    summary["scales"] = sorted(rows_by_n)
    summary["environment"] = next(iter(manifests.values()))["environment"] if manifests else {}
    summary["git_commit"] = next(iter(manifests.values()))["git_commit"] if manifests else "unknown"
    summary["questions_per_seed"] = {str(m["config"]["n_agents"]): m.get("questions_per_seed")
                                     for m in manifests.values()}
    summary["conditions_per_seed"] = {str(m["config"]["n_agents"]): m.get("n_conditions")
                                      for m in manifests.values()}
    grids = {}
    for m in manifests.values():
        c = m["config"]
        grids[str(c["n_agents"])] = {
            "failure_regimes": {k: v for k, v in c["failure_grid"].items()},
            "corruption_types": c.get("corruption_types", []),
            "corruption_levels": c.get("corruption_levels", []),
            "partition_parts": c.get("partition_parts", []),
            "n_conditions": m.get("n_conditions"),
            "systems": m.get("systems"),
        }
    summary["grids"] = grids

    s, main_n, n_seeds = build_main_tables(rows_by_n, out_tables, out_figs, summary)
    build_scaling(rows_by_n, out_tables, out_figs, summary)
    build_frontier(index, rows_by_n, out_tables, out_figs, summary)
    build_questioning(index, out_tables, out_figs, summary)
    build_factorial(index, out_tables, summary)
    build_factorial(index, out_tables, summary, index_name="factorial_heavy",
                    out_name="table7b_factorial_heavy_correlation.csv",
                    summary_key="factorial_heavy")
    build_negative_regimes(index, out_tables, summary)
    build_negative_results(rows_by_n, summary, out_tables)

    # total experimental accounting across every run in the index
    total_runtime = sum(e.get("runtime_seconds", 0.0) for v in index.values() for e in v)
    total_rows_all = sum(e.get("n_rows", 0) for v in index.values() for e in v)
    summary["totals"] = {
        "n_runs": sum(len(v) for v in index.values()),
        "total_runtime_seconds": total_runtime,
        "total_rows": total_rows_all,
        "index_names": sorted(index),
    }

    out = ROOT / "results" / f"summary{('_' + args.out_tag) if args.out_tag else ''}.json"
    out.write_text(json.dumps(summary, indent=2, default=float))
    print(f"wrote {out}")
    print(f"tables -> {out_tables}, figures -> {out_figs}")
    return summary


if __name__ == "__main__":
    main()
