"""Per-pattern loss accounting: where does each hidden pattern die?

The headline metrics say *how many* hidden patterns a system reports.  This
module says *where the rest went*.  Every discoverable gold pattern is traced
through the stages of the hierarchy's pipeline and the first stage at which
it is lost is recorded, together with enough per-pattern metadata (rare or
common, regions spanned, facets, witnesses) to slice the losses afterwards.

Stages, in pipeline order, for the hierarchy:

  extracted        >= 2 facets produced a correct (pred, anchor) claim at the
                   edge.  Below this nothing downstream can work.
  sketch_visible   the anchor passes the kernel's relational triage test on
                   the site sketches: >= 2 foreign sites, >= 2 regions, a
                   causal span >= 2.  Sub-reasons are recorded for the ones
                   that fail: no site bit set (support threshold), too few
                   foreign sites, too few regions, span too short.
  in_triage        the anchor made the triage candidate list (same test, as
                   the code actually ran it, before the question budget).
  questioned       the anchor survived the question budget cut.
  descent_reached  a descent for this anchor queried at least one user who
                   holds a facet of the pattern.
  in_pool          >= 2 of the pattern's gold links (pred, anchor) are in the
                   kernel's final working pool.  This is what the report's
                   "evidence coverage" measures.
  candidate        the last synthesis produced a hypothesis on this anchor and
                   chain sharing >= 1 gold link (before any register cut).
  matched_any      a hypothesis matched under the primary rule (>= 2 gold
                   links and >= half), at ANY confidence.
  matched_tau      ...and with confidence >= 0.5, which is what the metrics
                   count.  The gap between these two is the calibration loss.
  in_register      the matching hypothesis survived the register cut.

The centralised systems have no sketch, triage, question or descent stage;
their funnel is extracted -> in_pool -> candidate -> matched -> register.

Output: artifacts/loss_funnel.jsonl, one row per (arch, scale, seed, pid),
plus a summary row per (arch, scale, seed) with the survival counts.

    python3 -m research.mycelic.loss_accounting            # 10k x 5, 50k x 3
    python3 -m research.mycelic.loss_accounting 10000 0,1  # custom
"""
from __future__ import annotations

import json
import os
import sys
import time
from typing import Dict, List, Optional, Sequence, Set, Tuple

import numpy as np

from .corpus import CAUSAL_CHAINS, PRED_ID, Pattern
from .evalm import _match_sets
from .models import allocation
from .ops import _chain_span, stem_rep_map
from .org import ENT, REGION, SITE, USER
from .runner import ART, World, build_world, hier_cfg, run_arch
from .systems import HierRunner, RunResult

FUNNEL = os.path.join(ART, "loss_funnel.jsonl")

HIER_STAGES = ["extracted", "sketch_visible", "in_triage", "questioned",
               "descent_reached", "in_pool", "candidate", "matched_any",
               "matched_tau", "in_register"]
FLAT_STAGES = ["extracted", "in_pool", "candidate", "matched_any",
               "matched_tau", "in_register"]

TAU = 0.5


def _facets_extracted(world: World, ul, p: Pattern) -> Tuple[int, int]:
    """(facets with >= 1 correct claim, facets with >= 1 claim of any kind).

    A facet counts as extracted only if some record of it produced a claim
    carrying BOTH the right anchor and the right predicate; an anchor slip or
    a predicate slip is a loss here, not later.
    """
    ex = ul.ex
    rid_to_i = {int(r): i for i, r in enumerate(ex.rid.tolist())}
    ok = anyk = 0
    for j, fr in enumerate(p.facet_records):
        want = p.preds[j]
        hit = False
        seen = False
        for r in fr:
            i = rid_to_i.get(int(r))
            if i is None:
                continue
            seen = True
            if int(ex.anchor[i]) == p.anchor and int(ex.pred[i]) == want:
                hit = True
                break
        ok += int(hit)
        anyk += int(seen)
    return ok, anyk


def _sketch_diagnosis(h, cfg, anchor: int) -> Dict[str, object]:
    """Re-run the kernel's triage predicate for ONE anchor, keeping the reason
    it fails.  Mirrors HierRunner._question_round exactly."""
    org = h.org
    rows = []
    tot = 0
    n_sites_any = 0
    for st, ent in h.site_sketch.items():
        e = ent.get(anchor)
        if e is None:
            continue
        n_sites_any += 1
        t, mask, t0, t1 = e
        tot += t
        if mask:
            rows.append((st, org.ancestor_at(st, REGION), mask, t))
    out = {"sk_sites_present": n_sites_any,
           "sk_sites_with_bit": len(rows),
           "sk_total_mentions": int(tot)}
    if not rows:
        out["sketch_fail"] = "no_site_bit"
        return out
    total = max(1, tot)
    foreign = [r for r in rows if r[3] / total <= cfg.foreign_share]
    out["sk_foreign_sites"] = len(foreign)
    if len(foreign) < 2:
        out["sketch_fail"] = "foreign_lt2"
        return out
    regs = {r[1] for r in foreign}
    out["sk_foreign_regions"] = len(regs)
    if len(regs) < 2:
        out["sketch_fail"] = "regions_lt2"
        return out
    um = 0
    for r in foreign:
        um |= r[2]
    span, _ = _chain_span(um)
    out["sk_span"] = int(span)
    if span < 2:
        out["sketch_fail"] = "span_lt2"
        return out
    out["sketch_fail"] = ""
    return out


def _best_match(hyps, p: Pattern, stem) -> Tuple[Optional[int], int]:
    """(index of best primary-matching hypothesis, gold links it shares)."""
    m = _match_sets(hyps, [p], stem, lenient=False)
    idx = m.get(p.pid)
    if not idx:
        return None, 0
    best = max(idx, key=lambda i: hyps[i].conf)
    return best, len(set(hyps[best].preds) & set(p.preds))


def _hier_rows(arch: str, world: World, res: RunResult, runner: HierRunner,
               ul, scale: int, seed: int) -> List[Dict]:
    c = world.corpus
    h = runner.h
    cfg = runner.cfg
    stem = stem_rep_map(c)
    pats = {p.pid: p for p in c.patterns}
    ranked = list(res.hypotheses)                    # final, sorted by conf
    ranked_tau = [x for x in ranked if x.conf >= TAU]
    rank_of = {id(x): i for i, x in enumerate(ranked)}
    q_anchors = {int(q.anchor) for q in h.questions}
    q_order = {a: i for i, a in enumerate(h.question_order)}
    triage_rank = {a: i for i, (g, a) in enumerate(
        sorted(((g, a) for a, g in h.triage_gain.items()), reverse=True))}
    rows = []
    for pid in world.gold.discoverable:
        p = pats[pid]
        a = int(p.anchor)
        n_fac = len(p.preds)
        need = max(2, (n_fac + 1) // 2)
        ok, anyk = _facets_extracted(world, ul, p)
        r: Dict[str, object] = {
            "arch": arch, "scale": scale, "seed": seed, "pid": pid,
            "anchor": a, "chain": int(p.chain), "n_facets": n_fac,
            "rare": bool(p.rare), "n_regions": int(p.n_regions),
            "n_witnesses": int(sum(len(fr) for fr in p.facet_records)),
            "contradicted": bool(getattr(p, "contradicted", False)),
            "facets_extracted": ok, "facets_seen": anyk,
        }
        r["extracted"] = ok >= 2
        r.update(_sketch_diagnosis(h, cfg, a))
        r["sketch_visible"] = r["sketch_fail"] == ""
        r["in_triage"] = a in h.triage_gain
        r["triage_rank"] = triage_rank.get(a, -1)
        r["question_rank"] = q_order.get(a, -1)
        r["questioned"] = a in q_anchors
        facet_users: Set[int] = {int(u) for fu in p.facet_users for u in fu}
        reached = h.reached_users.get(a, set())
        r["users_reached"] = len(reached)
        r["facet_users_reached"] = len(reached & facet_users)
        r["facets_reached"] = sum(1 for fu in p.facet_users
                                  if any(int(u) in reached for u in fu))
        r["descent_reached"] = r["facet_users_reached"] > 0
        links = {(int(pr), a) for pr in p.preds}
        r["links_in_pool"] = len(links & res.retained)
        r["in_pool"] = r["links_in_pool"] >= 2
        # candidate: the last synthesis, pre-register-cut
        full = h.full_hyps
        cand = [x for x in full if int(x.anchor) == a and x.chain == p.chain
                and set(x.preds) & set(p.preds)]
        r["candidate"] = bool(cand)
        r["candidate_links"] = max((len(set(x.preds) & set(p.preds))
                                    for x in cand), default=0)
        r["candidate_conf"] = max((float(x.conf) for x in cand), default=-1.0)
        bi, shared = _best_match(full, p, stem)
        r["matched_any"] = bi is not None
        r["match_links"] = shared
        r["match_conf"] = float(full[bi].conf) if bi is not None else -1.0
        r["matched_tau"] = bi is not None and full[bi].conf >= TAU
        # register: was the matching hypothesis kept after the cut?
        bi2, _ = _best_match(ranked_tau, p, stem)
        r["in_register"] = bi2 is not None
        r["rank"] = rank_of.get(id(ranked_tau[bi2]), -1) if bi2 is not None else -1
        r["first_loss"] = next((s for s in HIER_STAGES if not r[s]), "")
        rows.append(r)
    return rows


def _flat_rows(arch: str, world: World, res: RunResult, scale: int,
               seed: int) -> List[Dict]:
    c = world.corpus
    stem = stem_rep_map(c)
    pats = {p.pid: p for p in c.patterns}
    ranked = list(res.hypotheses)
    ranked_tau = [x for x in ranked if x.conf >= TAU]
    rank_of = {id(x): i for i, x in enumerate(ranked)}
    # the flat systems' pools carry evidence ids per KO, so "extracted" is
    # read off the pool: a facet whose record ids appear in a KO with the
    # right (pred, anchor) was extracted correctly
    ev_of: Dict[Tuple[int, int], Set[int]] = {}
    for k in res.kernel_kos:
        ev_of.setdefault((int(k.pred), int(k.anchor)), set()).update(
            int(e) for e in k.evidence)
    rows = []
    for pid in world.gold.discoverable:
        p = pats[pid]
        a = int(p.anchor)
        ok = 0
        for j, fr in enumerate(p.facet_records):
            ev = ev_of.get((int(p.preds[j]), a), set())
            if any(int(x) in ev for x in fr):
                ok += 1
        r: Dict[str, object] = {
            "arch": arch, "scale": scale, "seed": seed, "pid": pid,
            "anchor": a, "chain": int(p.chain), "n_facets": len(p.preds),
            "rare": bool(p.rare), "n_regions": int(p.n_regions),
            "n_witnesses": int(sum(len(fr) for fr in p.facet_records)),
            "contradicted": bool(getattr(p, "contradicted", False)),
            "facets_extracted": ok,
        }
        links = {(int(pr), a) for pr in p.preds}
        r["links_in_pool"] = len(links & res.retained)
        r["extracted"] = r["links_in_pool"] >= 2   # best available proxy
        r["in_pool"] = r["links_in_pool"] >= 2
        # no pre-cut list for the flat systems: candidate == matched at any
        # confidence in the returned list (the register cut is rarely binding
        # for them, and this is stated in the summary)
        bi, shared = _best_match(ranked, p, stem)
        cand = [x for x in ranked if int(x.anchor) == a and x.chain == p.chain
                and set(x.preds) & set(p.preds)]
        r["candidate"] = bool(cand)
        r["candidate_links"] = max((len(set(x.preds) & set(p.preds))
                                    for x in cand), default=0)
        r["candidate_conf"] = max((float(x.conf) for x in cand), default=-1.0)
        r["matched_any"] = bi is not None
        r["match_links"] = shared
        r["match_conf"] = float(ranked[bi].conf) if bi is not None else -1.0
        r["matched_tau"] = bi is not None and ranked[bi].conf >= TAU
        bi2, _ = _best_match(ranked_tau, p, stem)
        r["in_register"] = bi2 is not None
        r["rank"] = rank_of.get(id(ranked_tau[bi2]), -1) if bi2 is not None else -1
        r["first_loss"] = next((s for s in FLAT_STAGES if not r[s]), "")
        rows.append(r)
    return rows


def _summary(arch: str, rows: List[Dict], scale: int, seed: int,
             res: RunResult) -> Dict:
    stages = HIER_STAGES if arch.startswith(("H_", "G_", "F_", "E_", "J_",
                                            "I_", "V")) else FLAT_STAGES
    n = len(rows)
    out: Dict[str, object] = {"arch": arch, "scale": scale, "seed": seed,
                              "summary": True, "n_gold": n,
                              "n_rare": sum(1 for r in rows if r["rare"]),
                              "stages": stages}
    prev = None
    for s in stages:
        k = sum(1 for r in rows if r[s])
        out[f"surv_{s}"] = k
        out[f"frac_{s}"] = k / max(1, n)
        kr = sum(1 for r in rows if r[s] and r["rare"])
        out[f"rare_surv_{s}"] = kr
        if prev is not None:
            pk = sum(1 for r in rows if r[prev])
            out[f"cond_{s}"] = k / max(1, pk)
        prev = s
    fl: Dict[str, int] = {}
    for r in rows:
        fl[r["first_loss"] or "reported"] = fl.get(r["first_loss"] or "reported", 0) + 1
    out["first_loss_counts"] = fl
    if "sketch_fail" in rows[0]:
        sf: Dict[str, int] = {}
        for r in rows:
            if not r["sketch_visible"]:
                sf[r["sketch_fail"]] = sf.get(r["sketch_fail"], 0) + 1
        out["sketch_fail_counts"] = sf
    out["n_hypotheses"] = len(res.hypotheses)
    out["n_hypotheses_tau"] = sum(1 for x in res.hypotheses if x.conf >= TAU)
    out["kernel_pool"] = len(res.kernel_kos)
    out["n_questions"] = len(res.questions)
    out["compute_units"] = float(res.meter.cu)
    return out


def run(scales: Sequence[int], seeds: Sequence[int],
        archs: Sequence[str] = ("H_mycelic_full", "B4_central_triage",
                                "Y_oracle_retrieval", "A2_chunked_ctx"),
        alloc_name: str = "back-loaded", out: str = FUNNEL,
        cfg_over: Optional[Dict] = None, tag: str = "") -> List[Dict]:
    all_rows: List[Dict] = []
    with open(out, "a") as fh:
        for scale in scales:
            for seed in seeds:
                t0 = time.time()
                w = build_world(scale, seed)
                alloc = allocation(alloc_name)
                for arch in archs:
                    t1 = time.time()
                    name = arch + tag
                    if arch.startswith(("H_", "G_", "F_", "E_", "J_", "I_")) \
                            or arch == "V":
                        from .runner import ARCHS
                        over = dict(ARCHS.get(arch, {}).get("cfg", {}))
                        if cfg_over:
                            over.update(cfg_over)
                        cfg = hier_cfg(**over)
                        ul = w.user_layer(alloc[USER], seed)
                        runner = HierRunner(w.corpus, alloc, cfg, seed=seed,
                                            ul=ul, near_miss=w.near_miss)
                        res = runner.run()
                        rows = _hier_rows(name, w, res, runner, ul, scale, seed)
                    else:
                        res = run_arch(arch, w, alloc, seed)
                        rows = _flat_rows(name, w, res, scale, seed)
                    summ = _summary(name, rows, scale, seed, res)
                    for r in rows:
                        fh.write(json.dumps(r) + "\n")
                    fh.write(json.dumps(summ) + "\n")
                    fh.flush()
                    all_rows.extend(rows)
                    all_rows.append(summ)
                    st = summ["stages"]
                    print(f"  {name:22s} " + " ".join(
                        f"{s[:8]}={summ['frac_' + s]:.2f}" for s in st)
                        + f"  ({time.time() - t1:.0f}s)", flush=True)
                w.clear_cache()
                print(f"scale={scale} seed={seed} done in {time.time() - t0:.0f}s",
                      flush=True)
    return all_rows


if __name__ == "__main__":
    if len(sys.argv) > 1:
        scales = [int(x) for x in sys.argv[1].split(",")]
        seeds = [int(x) for x in sys.argv[2].split(",")] if len(sys.argv) > 2 \
            else [0]
        run(scales, seeds)
    else:
        run([10_000], [0, 1, 2, 3, 4])
        run([50_000], [0, 1, 2])
